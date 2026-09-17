"""「定时任务来源」上报链路测试（``session_source == "scheduled"``）

背景（本文件要钉死的事实）：
    ``data/permission_policies.json`` 的 ``scheduled-no-write``（``write_file``）/
    ``scheduled-no-edit``（``edit``）两条 ABAC 规则靠 ``ABACContext.session_source ==
    "scheduled"`` 触发。但改动前**没有任何生产链路**报这个来源——严格模式里的来源只来自
    **进程级**环境变量 ``CP_PERMISSION_SESSION_SOURCE``（缺省 ``cli``），无法区分"这次调用
    来自定时任务"还是"来自人机对话" ⇒ 两条规则**永远触发不了**，是纯摆设。

本次接通的两段链路：
    ① ``agent/tool_gate.py`` 新增按**调用上下文**的会话来源（``contextvars``）：
       ``set_session_source()`` / ``current_session_source()`` / ``reset_session_source()``；
       取值优先级 = 调用方显式传入 → contextvar → 环境变量 ``CP_PERMISSION_SESSION_SOURCE``
       → 缺省 ``"cli"``。**不新增任何环境变量**。
    ② ``agent/task_scheduler.py::run_task`` 在**执行线程内**把来源标为 ``scheduled``，
       并用 ``try/finally`` 保证任何退出路径都还原（否则会污染后续人机对话的工具调用）。

被守护的不变量（顺序即重要性）：
    1. **不设任何环境变量 ⇒ 行为与改动前一字不变**：严格模式仍默认关闭，``write_file`` 放行；
    2. 严格模式 + ``owner`` + 来源 ``cli``（缺省）⇒ ``write_file`` / ``edit`` 放行
       —— 规则不误伤普通会话；
    3. 严格模式 + ``owner`` + 来源 ``scheduled`` ⇒ 两条规则**第一次真正拦下**这两个工具，
       且拒绝结构含 ``blocked=True`` / ``error_code="PERMISSION_DENIED"``；
    4. 还原正确性：``set`` 之后必须能还原（含 ``with`` 的异常路径、重复 reset），还原后回到
       环境变量/缺省判定；
    5. ``contextvars`` 的**线程隔离**：新线程未设置时读到空（**不**继承父线程——这正是
       "必须在执行线程内设置"的原因，也是调度线程里设置会失效的原因）。

口径纪律：与 ``tests/unit/test_tool_gate_strict.py`` 一致，本文件严格模式部分使用**真实**
    ``data/permission_policies.json``（不造假夹具）——"这两条真实规则到底会不会触发"只能
    在真实策略上验证。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

import agent.task_scheduler as ts_module
import agent.tool_gate as G
from agent.task_scheduler import TaskScheduler

pytestmark = pytest.mark.unit

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_POLICY_PATH = _PROJECT_ROOT / "data" / "permission_policies.json"

#: 两条规则的共同目标工具（都只允许"非定时任务来源"调用）
WRITE_TOOLS = ("write_file", "edit")

#: 两条规则的规则名（命中判定不依赖它们，仅用于"规则确实存在"的静态断言）
SCHEDULED_RULES = ("scheduled-no-write", "scheduled-no-edit")

_WRITE_ARGS: Dict[str, Any] = {"path": "x.txt", "content": "c"}


# ════════════════════════════════════════════════════════════
#  fixtures / 工具函数
# ════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """每个用例都从"未设置任何相关环境变量 + 上下文未声明来源"起步

    四个相关环境变量（严格模式开关/角色/来源、闸门总开关）全部清掉；并清掉严格模式的
    网关单例与派生缓存，避免用例间互相污染。
    """
    for name in (G.STRICT_ENABLED_ENV, G.STRICT_ROLE_ENV, G.STRICT_SOURCE_ENV,
                 G.GATE_ENABLED_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(G, "_STRICT_GATEWAY", None)
    G._reset_cache()
    assert G.current_session_source() == "", "用例起点必须是「上下文未声明来源」"
    yield
    monkeypatch.setattr(G, "_STRICT_GATEWAY", None)
    G._reset_cache()


@pytest.fixture
def patch_history(monkeypatch, tmp_path):
    """把调度器的历史落盘路径重定向到 tmp_path（**绝不碰 data/**）"""
    history = tmp_path / "task_history.jsonl"
    monkeypatch.setattr(ts_module, "TASK_HISTORY_FILE", history)
    monkeypatch.setattr(ts_module, "HEARTBEAT_HISTORY_FILE", tmp_path / "heartbeat.json")
    monkeypatch.setattr(ts_module, "SCHEDULED_TASKS_FILE", tmp_path / "tasks.json")
    return history


def _enable_strict(monkeypatch, role: str = "owner",
                   source: Optional[str] = None) -> None:
    """打开严格模式；``source=None`` ⇒ **不设**来源环境变量（走缺省链路）"""
    monkeypatch.setenv(G.STRICT_ENABLED_ENV, "1")
    monkeypatch.setenv(G.STRICT_ROLE_ENV, role)
    if source is not None:
        monkeypatch.setenv(G.STRICT_SOURCE_ENV, source)


def _py_task(func, interval: int = 1, name: str = "来源探针任务") -> Dict[str, Any]:
    """``python_func`` 类型任务（**不用** system_command：那条路径会真的起子进程）"""
    return {
        "task_id": "probe-py-task",
        "name": name,
        "type": "python_func",
        "func": func,
        "interval": interval,
        "last_run": None,
        "enabled": True,
    }


# ════════════════════════════════════════════════════════════
#  零、前置事实：两条规则真实存在，且目标工具未错位
# ════════════════════════════════════════════════════════════


class TestPreconditions:

    def test_两条_scheduled_规则存在于真实策略且来源为_scheduled(self):
        policy = json.loads(_POLICY_PATH.read_text(encoding="utf-8"))
        rules = {r["name"]: r for r in policy["abac_rules"]}
        for name in SCHEDULED_RULES:
            assert name in rules, f"真实策略里缺少 ABAC 规则 {name}"
            assert rules[name]["deny_if"] == {"session_source_in": ["scheduled"]}

    def test_规则目标工具分别是_write_file_与_edit(self):
        policy = json.loads(_POLICY_PATH.read_text(encoding="utf-8"))
        targets = {r["name"]: r["tool"] for r in policy["abac_rules"]
                   if r["name"] in SCHEDULED_RULES}
        assert targets == {"scheduled-no-write": "write_file",
                           "scheduled-no-edit": "edit"}

    def test_严格模式默认关闭(self):
        assert G._strict_enabled() is False

    def test_上下文来源默认读到空(self):
        assert G.current_session_source() == ""
        assert G._strict_source() == "cli", "空 ⇒ 落到缺省 cli"


# ════════════════════════════════════════════════════════════
#  一、默认口径：不设任何环境变量 ⇒ 行为与改动前一致
# ════════════════════════════════════════════════════════════


class TestDefaultUnchanged:
    """本任务是"接通链路"，**不是**"收紧默认权限"——这里钉死它没有收紧默认行为"""

    @pytest.mark.parametrize("tool", WRITE_TOOLS)
    def test_不设任何环境变量时写类工具放行(self, tool):
        assert G.check_tool_call(tool, dict(_WRITE_ARGS)) is None, (
            "严格模式默认关闭 ⇒ 闸门仍是 fail-open，write_file/edit 必须放行"
        )

    @pytest.mark.parametrize("tool", WRITE_TOOLS)
    def test_即使上下文报了_scheduled_未开严格模式也放行(self, tool):
        """上报本身**不**改变默认行为：来源只有被严格模式的 ABAC 消费时才有意义"""
        with G.set_session_source("scheduled"):
            assert G.check_tool_call(tool, dict(_WRITE_ARGS)) is None

    def test_环境变量来源在未开严格模式时同样不生效(self, monkeypatch):
        monkeypatch.setenv(G.STRICT_SOURCE_ENV, "scheduled")
        assert G.check_tool_call("write_file", dict(_WRITE_ARGS)) is None


# ════════════════════════════════════════════════════════════
#  二、严格模式 + owner + 来源 cli（缺省）⇒ 放行（不误伤普通会话）
# ════════════════════════════════════════════════════════════


class TestStrictOwnerWithCliSource:

    @pytest.mark.parametrize("tool", WRITE_TOOLS)
    def test_owner_且来源缺省时写类工具放行(self, monkeypatch, tool):
        _enable_strict(monkeypatch, role="owner")          # 不设来源 ⇒ 缺省 cli
        assert G._strict_source() == "cli"
        assert G.check_tool_call(tool, dict(_WRITE_ARGS)) is None

    @pytest.mark.parametrize("tool", WRITE_TOOLS)
    def test_owner_且来源显式_cli_时写类工具放行(self, monkeypatch, tool):
        _enable_strict(monkeypatch, role="owner", source="cli")
        assert G.check_tool_call(tool, dict(_WRITE_ARGS)) is None

    def test_读取类工具在任何来源下都不受影响(self, monkeypatch):
        """两条规则只管写路径；read_file 不该被来源牵连"""
        _enable_strict(monkeypatch, role="owner")
        with G.set_session_source("scheduled"):
            assert G.check_tool_call("read_file", {"path": "x.txt"}) is None


# ════════════════════════════════════════════════════════════
#  三、严格模式 + owner + 来源 scheduled ⇒ 两条规则第一次真正生效
# ════════════════════════════════════════════════════════════


class TestStrictOwnerWithScheduledSource:
    """这是本任务的核心：owner 的 RBAC 白名单是 ``["*"]``（什么都没拦），
    唯一能拒的就是这两条 ABAC 规则 ⇒ 被拒即证明规则真的触发了。"""

    @pytest.mark.parametrize("tool", WRITE_TOOLS)
    def test_来源_scheduled_时写类工具被拒(self, monkeypatch, tool):
        _enable_strict(monkeypatch, role="owner")

        with G.set_session_source("scheduled"):
            result = G.check_tool_call(tool, dict(_WRITE_ARGS))

        assert result is not None, f"{tool} 在定时任务来源下必须被 ABAC 规则拒绝"
        assert set(result) == {"ok", "blocked", "error_code", "error"}
        assert result["ok"] is False
        assert result["blocked"] is True
        assert result["error_code"] == "PERMISSION_DENIED"
        assert "'scheduled'" in result["error"], "拒绝文案必须带上被判定的会话来源"

    def test_显式参数比上下文变量优先级更高(self, monkeypatch):
        _enable_strict(monkeypatch, role="owner")
        with G.set_session_source("scheduled"):
            # 显式声明 cli ⇒ 覆盖上下文里的 scheduled ⇒ 放行
            assert G.check_tool_call("write_file", dict(_WRITE_ARGS),
                                     session_source="cli") is None
            # 反之，上下文是 cli 时显式声明 scheduled 也必须被拒
        assert G.check_tool_call("write_file", dict(_WRITE_ARGS),
                                 session_source="scheduled") is not None

    def test_上下文变量比环境变量优先级更高(self, monkeypatch):
        """环境变量是进程级的（这里是 cli）；上下文声明 scheduled 必须赢"""
        _enable_strict(monkeypatch, role="owner", source="cli")
        with G.set_session_source("scheduled"):
            assert G._strict_source() == "scheduled"
            assert G.check_tool_call("write_file", dict(_WRITE_ARGS)) is not None

    def test_拒绝确实来自这两条_ABAC_规则而非_RBAC(self, monkeypatch, tmp_path):
        """把两条规则从策略副本里摘掉 ⇒ 同样条件（owner + scheduled）立刻放行

        这条断言把"拒绝归因"钉死：owner 的 ``allowed_tools=["*"]`` 本来什么都不会拦，
        去掉这两条规则后不再有任何拒绝理由 ⇒ 拒绝只能来自它们。
        """
        from agent.permission_system import ABACContext, PermissionGateway, Role

        policy = json.loads(_POLICY_PATH.read_text(encoding="utf-8"))
        stripped = dict(policy)
        stripped["abac_rules"] = [
            r for r in policy["abac_rules"] if r["name"] not in SCHEDULED_RULES
        ]
        path = tmp_path / "policy_without_scheduled_rules.json"
        path.write_text(json.dumps(stripped, ensure_ascii=False), encoding="utf-8")

        gateway = PermissionGateway(policy_path=str(path))
        assert gateway.is_degraded is False, "策略副本必须能正常加载，否则本断言空转"

        ctx = ABACContext(role=Role.OWNER, session_source="scheduled")
        assert gateway.check("write_file", {}, ctx).allowed is True
        assert gateway.check("edit", {}, ctx).allowed is True

        # 对照：真实策略下同一上下文必须被拒
        real = PermissionGateway(policy_path=str(_POLICY_PATH))
        assert real.check("write_file", {}, ctx).allowed is False
        assert real.check("edit", {}, ctx).allowed is False


# ════════════════════════════════════════════════════════════
#  四、还原正确性
# ════════════════════════════════════════════════════════════


class TestRestoreSemantics:

    def test_句柄_reset_后回到空(self):
        handle = G.set_session_source("scheduled")
        assert G.current_session_source() == "scheduled"
        handle.reset()
        assert G.current_session_source() == ""
        assert G._strict_source() == "cli"

    def test_重复_reset_是幂等的(self):
        handle = G.set_session_source("scheduled")
        handle.reset()
        handle.reset()                      # 不得抛异常
        assert G.current_session_source() == ""

    def test_with_退出即还原(self):
        with G.set_session_source("scheduled"):
            assert G.current_session_source() == "scheduled"
        assert G.current_session_source() == ""
        assert G._strict_source() == "cli"

    def test_with_异常路径同样还原(self):
        with pytest.raises(RuntimeError):
            with G.set_session_source("scheduled"):
                raise RuntimeError("boom")
        assert G.current_session_source() == ""

    def test_reset_session_source_也接受裸_token(self):
        token = G._SESSION_SOURCE_VAR.set("scheduled")
        assert G.current_session_source() == "scheduled"
        G.reset_session_source(token)
        assert G.current_session_source() == ""

    def test_还原后按缺省_cli_判定(self, monkeypatch):
        _enable_strict(monkeypatch, role="owner")
        with G.set_session_source("scheduled"):
            assert G.check_tool_call("write_file", dict(_WRITE_ARGS)) is not None
        # 还原后：人机对话链路缺省 cli ⇒ 立刻恢复放行
        assert G.current_session_source() == ""
        assert G.check_tool_call("write_file", dict(_WRITE_ARGS)) is None

    def test_还原后按环境变量判定(self, monkeypatch):
        """还原是"回到下一级优先级"，不是"清空一切"：环境变量设了 scheduled 就仍按它判"""
        _enable_strict(monkeypatch, role="owner", source="scheduled")
        with G.set_session_source("cli"):
            assert G.check_tool_call("write_file", dict(_WRITE_ARGS)) is None
        assert G._strict_source() == "scheduled"
        assert G.check_tool_call("write_file", dict(_WRITE_ARGS)) is not None

    def test_空白来源等价于未声明(self):
        handle = G.set_session_source("   ")
        try:
            assert G.current_session_source() == ""
            assert G._strict_source() == "cli"
        finally:
            handle.reset()


# ════════════════════════════════════════════════════════════
#  五、contextvar 的线程隔离（"必须在执行线程内设置"的根据）
# ════════════════════════════════════════════════════════════


class TestContextVarThreadIsolation:

    def test_新线程不继承父线程的来源(self):
        """**不**断言"继承"——那正是要避免的：调度线程里设置对执行线程无效"""
        seen: Dict[str, Any] = {}

        def _worker():
            seen["src"] = G.current_session_source()
            seen["strict"] = G._strict_source()

        with G.set_session_source("scheduled"):
            assert G.current_session_source() == "scheduled"
            thread = threading.Thread(target=_worker)
            thread.start()
            thread.join(timeout=10)
            assert not thread.is_alive()
            # 父线程不受子线程影响：子线程读空，父线程仍是 scheduled
            assert G.current_session_source() == "scheduled"

        assert seen["src"] == "", "新线程读到的必须是空（未继承父线程的 contextvar）"
        assert seen["strict"] == "cli"
        # 退出 with 即还原（父线程的上下文也没被带出去）
        assert G.current_session_source() == ""


# ════════════════════════════════════════════════════════════
#  六、调度器集成：run_task 执行期真的报 scheduled，且退出即还原
# ════════════════════════════════════════════════════════════


class TestSchedulerReportsScheduled:

    def test_run_task_内读到_scheduled_任务外还原为空(self, patch_history):
        scheduler = TaskScheduler()
        seen: Dict[str, Any] = {}

        def _body():
            seen["src"] = G.current_session_source()

        task = _py_task(_body)
        scheduler.tasks = [task]

        result = scheduler.run_task(task)

        assert result["status"] == "success"
        assert seen["src"] == "scheduled", "任务执行期必须报定时任务来源"
        assert G.current_session_source() == "", "任务外必须已还原（否则污染人机对话）"

    def test_任务体抛异常后仍然还原(self, patch_history):
        """异常路径：任务体抛错被内层 except 吃掉，但还原由 finally 保证"""
        scheduler = TaskScheduler()
        seen: Dict[str, Any] = {}

        def _body():
            seen["src"] = G.current_session_source()
            raise RuntimeError("任务体故意失败")

        task = _py_task(_body)
        result = scheduler.run_task(task)

        assert result["status"] == "failed"
        assert seen["src"] == "scheduled"
        assert G.current_session_source() == ""

    def test_tick_在独立线程内执行时也报_scheduled_且主线程不受影响(self, patch_history):
        """tick() 是 daemon 线程里的真实调用形态（_run_loop → tick → run_task）

        断言两件事：①设置在**执行线程**内（故 worker 线程内读得到）；
        ②主线程（模拟人机对话链路）完全不受影响。
        """
        scheduler = TaskScheduler()
        seen: Dict[str, Any] = {}

        def _body():
            seen["src"] = G.current_session_source()

        task = _py_task(_body)
        scheduler.tasks = [task]

        thread = threading.Thread(target=scheduler.tick)
        thread.start()
        thread.join(timeout=30)
        assert not thread.is_alive(), "tick 未在超时内结束"

        assert seen["src"] == "scheduled"
        assert G.current_session_source() == ""

    def test_严格模式下_任务内写文件被拦_任务外恢复放行(self, monkeypatch, patch_history):
        """端到端：这两条 ABAC 规则在**真实的定时任务链路**上第一次真正生效"""
        _enable_strict(monkeypatch, role="owner")           # 来源走缺省 cli

        scheduler = TaskScheduler()
        seen: Dict[str, Any] = {}

        def _body():
            for tool in WRITE_TOOLS:
                seen[tool] = G.check_tool_call(tool, dict(_WRITE_ARGS))

        task = _py_task(_body)
        result = scheduler.run_task(task)

        assert result["status"] == "success"
        for tool in WRITE_TOOLS:
            blocked = seen[tool]
            assert blocked is not None, f"定时任务内的 {tool} 必须被两条 ABAC 规则拦下"
            assert blocked["blocked"] is True
            assert blocked["error_code"] == "PERMISSION_DENIED"

        # 任务结束 ⇒ 还原 ⇒ 同进程内的人机对话工具调用不受影响
        assert G.current_session_source() == ""
        assert G.check_tool_call("write_file", dict(_WRITE_ARGS)) is None
        assert G.check_tool_call("edit", dict(_WRITE_ARGS)) is None

    def test_执行期内上报不改变任务结果结构(self, patch_history):
        """上报是旁路：任务结果 dict 的既有结构一字未变"""
        scheduler = TaskScheduler()
        task = _py_task(lambda: None)
        result = scheduler.run_task(task)

        assert set(result) == {"task_id", "name", "type", "start_time", "end_time",
                               "status", "output", "error", "duration_ms"}
        assert result["status"] == "success"
        assert patch_history.exists(), "历史仍照常落盘（重定向到 tmp_path）"

    def test_上报失败不影响任务执行(self, monkeypatch, patch_history):
        """tool_gate 不可用时（``set_session_source`` 一调就炸）任务必须照常跑完"""
        def _boom(*_a, **_k):
            raise RuntimeError("tool_gate unavailable")

        monkeypatch.setattr(G, "set_session_source", _boom)
        scheduler = TaskScheduler()
        ran: Dict[str, Any] = {}

        def _body():
            ran["ok"] = True

        result = scheduler.run_task(_py_task(_body))

        assert ran.get("ok") is True, "上报失败不得阻断任务体"
        assert result["status"] == "success"
        assert G.current_session_source() == ""


# ════════════════════════════════════════════════════════════
#  五、人工重跑（execute_now）不得被当成"无人值守"来源
# ════════════════════════════════════════════════════════════


class TestManualTriggerNotReported:
    """``scheduled-no-write`` / ``scheduled-no-edit`` 防的是**无人值守**任务乱改东西。

    人工在 UI 上点"立即执行"（``execute_now``）时**有人在场**，若也按 ``scheduled`` 处理，
    手动重跑一个生成报告的任务反而写不出文件——属误伤。故 ``execute_now`` 传
    ``trigger="manual"``，不上报该来源（落到 ``cli``）。
    """

    def test_execute_now_不上报_scheduled(self, patch_history):
        scheduler = TaskScheduler()
        seen: Dict[str, Any] = {}

        def _body():
            seen["src"] = G.current_session_source()

        task = _py_task(_body)
        scheduler.tasks = [task]

        result = scheduler.execute_now(task["task_id"])

        assert result is not None and result["status"] == "success"
        assert seen["src"] == "", "人工重跑不得上报 scheduled（否则被 ABAC 误当成无人值守）"
        assert G.current_session_source() == "", "任务外必须已还原"

    def test_run_task_显式_manual_不上报(self, patch_history):
        """``run_task(..., trigger="manual")`` 直调也应不上报"""
        scheduler = TaskScheduler()
        seen: Dict[str, Any] = {}

        def _body():
            seen["src"] = G.current_session_source()

        task = _py_task(_body)
        scheduler.tasks = [task]

        result = scheduler.run_task(task, trigger="manual")

        assert result["status"] == "success"
        assert seen["src"] == "", "manual 触发不得上报 scheduled"

    def test_默认仍上报_调度路径行为不变(self, patch_history):
        """不传 ``trigger`` 的既有调用点（``tick``）保持原行为：仍上报 ``scheduled``"""
        scheduler = TaskScheduler()
        seen: Dict[str, Any] = {}

        def _body():
            seen["src"] = G.current_session_source()

        task = _py_task(_body)
        scheduler.tasks = [task]

        result = scheduler.run_task(task)

        assert result["status"] == "success"
        assert seen["src"] == "scheduled", "调度路径必须继续上报（默认值不能改变既有语义）"
