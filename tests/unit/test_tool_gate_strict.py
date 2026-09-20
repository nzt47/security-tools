"""``agent/tool_gate.py`` 的 **RBAC 严格模式**（``CP_TOOL_GATE_STRICT``）单元测试

背景（为什么要有"默认关闭的开关"）：
    闸门默认口径是 **fail-open + 显式拒绝**（只拦 ``roles[*].denied_tools`` 与
    ``trust.requires_approval``）。把 ``PermissionGateway`` 的 **RBAC 严格白名单**
    （``_check_rbac``：不在 ``allowed_tools`` 即拒；角色无策略也拒）裸接进来会当场
    封杀绝大多数工具——``default_role`` 是 ``guest``，其白名单只有
    ``["web_search", "read_file"]`` ⇒ 实测 **74 个真实工具只放行 2 个（封杀 97.3%）**。
    因此严格层做成**可选开关，默认关闭**，并由 ``owner`` 角色（``allowed_tools=["*"]``）
    提供"开了不会当场全封"的落点。

被守护的不变量：
    1. **未设** ``CP_TOOL_GATE_STRICT`` ⇒ 行为与开关加入前完全一致：
       一个会被 ``guest`` 拒的真实工具**仍然放行**；
    2. 设 ``=1`` 且角色 ``owner`` ⇒ 真实策略下 ``read_file``/``write_file``/``grep``/``edit``
       **放行**（证明 ``["*"]`` 生效、开关真的可用，而不是"一开就全封"）；
    3. 设 ``=1`` 且角色 ``guest`` ⇒ 不在 guest 白名单里的**真实**工具被**拒绝**，
       且拒绝结构含 ``blocked=True`` / ``error_code="PERMISSION_DENIED"``，文案注明严格模式；
    4. 设 ``=1`` 且网关抛异常（monkeypatch）⇒ **放行**（fail-open）且不抛异常；
    5. 非法 ``CP_PERMISSION_DEFAULT_ROLE`` ⇒ 回退**缺省角色**（现为 ``guest``，最小权限）
       且不崩；【2026-09-20 TASK-06 E15 契约更新：原为回退 ``owner``】
    6. 与 ``agent/tools/__init__.py::call()`` 集成：严格模式下被拒工具 **handler 零执行**。

口径纪律：本文件用**真实** ``data/permission_policies.json``（不造假夹具）——严格模式的
    全部意义就是"真实策略下会发生什么"，用假策略测等于没测。故**不 monkeypatch**
    ``POLICY_POLICIES_PATH`` / ``DESCRIPTORS_PATH``。
    （对照组：``tests/unit/test_tool_gate.py`` 测的是默认 fail-open 口径，那边用
    临时假文件是对的。）
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import agent.tool_gate as G

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFS_DIR = _PROJECT_ROOT / "data" / "tool_definitions"
_POLICY_PATH = _PROJECT_ROOT / "data" / "permission_policies.json"
_NAME_RE = re.compile(r"^name:\s*(\S+)\s*$")

#: 探针工具名（注册进真实注册表，用后精确还原）
PROBE_TOOL = "probe_strict_gate_tool"

#: guest 白名单**不含**它们的真实工具（用于证明严格模式真的在拦）
DENIED_UNDER_GUEST = ("edit", "grep", "write_file", "shell_execute")

#: owner（``["*"]``）下必须放行的真实工具（ABAC 未覆盖它们）
ALLOWED_UNDER_OWNER = ("read_file", "write_file", "grep", "edit", "todo_write")


# ════════════════════════════════════════════════════════════
#  fixtures
# ════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _clean_strict_env(monkeypatch):
    """每个用例都从"未设置任何严格模式环境变量"起步，并清掉网关单例缓存

    【2026-09-17 补】同时**关掉审批边界**（``CP_TOOL_GATE_APPROVAL_ENFORCE=0``）：
    本文件的被测对象是**严格模式（RBAC/ABAC）这一层**，而审批边界是另一层、且它默认开启、
    还排在严格模式之前 ⇒ 不过滤掉它，用例拿到的拒绝可能来自审批边界而不是 RBAC，
    断言就失去意义（``DENIED_UNDER_GUEST`` 里的 ``shell_execute`` 正落在审批边界上）。
    审批边界自身的行为由 ``tests/unit/test_tool_gate.py`` 与
    ``tests/unit/test_tool_approval*.py`` 负责；两者互不代偿。
    """
    for name in (G.STRICT_ENABLED_ENV, G.STRICT_ROLE_ENV, G.STRICT_SOURCE_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(G.APPROVAL_ENFORCE_ENV, "0")
    monkeypatch.setattr(G, "_STRICT_GATEWAY", None)
    G._reset_cache()
    yield
    monkeypatch.setattr(G, "_STRICT_GATEWAY", None)
    G._reset_cache()


@pytest.fixture(scope="module")
def real_tool_names() -> set:
    """注册表口径：``data/tool_definitions/*.yaml`` 的 ``name``"""
    names = set()
    for path in _DEFS_DIR.glob("*.yaml"):
        for line in path.read_text(encoding="utf-8").splitlines():
            matched = _NAME_RE.match(line)
            if matched:
                names.add(matched.group(1))
                break
    assert names, f"未能从 {_DEFS_DIR} 读到任何工具定义（口径失效）"
    return names


def _enable_strict(monkeypatch, role: str = "owner", source: str = "cli") -> None:
    monkeypatch.setenv(G.STRICT_ENABLED_ENV, "1")
    monkeypatch.setenv(G.STRICT_ROLE_ENV, role)
    monkeypatch.setenv(G.STRICT_SOURCE_ENV, source)


# ════════════════════════════════════════════════════════════
#  零、前置事实
# ════════════════════════════════════════════════════════════


class TestPreconditions:
    """先证明用例落在真实策略上，否则后面的断言会变成空转"""

    def test_开关默认关闭(self):
        assert G._strict_enabled() is False

    def test_真实策略文件存在且未降级(self):
        from agent.permission_system import ABACContext, PermissionGateway, Role
        gateway = PermissionGateway(policy_path=str(_POLICY_PATH))
        assert gateway.is_degraded is False
        assert gateway.default_role == Role.GUEST, "default_role 必须仍是 guest（本任务不改它）"
        # guest 严格白名单只放行 2 个真实工具 ⇒ 裸接必然全封（这正是开关默认关闭的理由）
        assert gateway.check("edit", {}, ABACContext(role=Role.GUEST)).allowed is False
        assert gateway.check("read_file", {}, ABACContext(role=Role.GUEST)).allowed is True

    def test_owner_角色已定义且白名单为通配(self):
        from agent.permission_system import Role
        policy = json.loads(_POLICY_PATH.read_text(encoding="utf-8"))
        assert Role.OWNER.value == "owner"
        owner = policy["roles"]["owner"]
        assert owner["allowed_tools"] == ["*"]
        assert owner["denied_tools"] == []
        assert policy["default_role"] == "guest"      # 不变

    def test_既有枚举值未被改动(self):
        from agent.permission_system import Role
        assert (Role.ADMIN.value, Role.DEVELOPER.value, Role.GUEST.value) == (
            "admin", "developer", "guest")

    def test_时段规则已从活规则移除并留档于_policy_notes(self, real_tool_names):
        """任务 C：`off-hours-shell-restriction` 已停用（它会把工作时段从 18:00 开始的人全禁掉）

        停用**不是删除**：规则名与恢复方法留档在顶层 `_policy_notes`（加载器不读该键，
        故停用后不影响任何判定）。此前的断言是"该规则存在"，本次改为"已停用 + 留档"，
        与 `data/permission_policies.json` 的现实一致。
        """
        policy = json.loads(_POLICY_PATH.read_text(encoding="utf-8"))
        assert "off-hours-shell-restriction" not in {
            r["name"] for r in policy["abac_rules"]
        }, "该规则不应再出现在 abac_rules 里（否则 18:00 上班的人工作时段 shell 全禁）"
        notes = {n["rule"]: n for n in policy.get("_policy_notes", [])}
        assert "off-hours-shell-restriction" in notes, "停用必须留档在 _policy_notes"
        note = notes["off-hours-shell-restriction"]
        assert "18:00" in note["disabled_reason"]          # 写明与主人作息的冲突
        assert "time_outside" in note["reenable_hint"]     # 给出可照抄的恢复片段
        assert "18:00" in note["reenable_hint"]
        assert "shell_execute" in real_tool_names          # 目标工具是已注册工具
        # 该规则的目标工具是已注册工具 ⇒ 按 _inactive_rules 的不变量（目标必须未注册）
        # 它**不能**放进 _inactive_rules
        assert "off-hours-shell-restriction" not in {
            r.get("name") for r in policy.get("_inactive_rules", [])
        }

    def test_其它_ABAC_规则的目标工具都存在(self, real_tool_names):
        policy = json.loads(_POLICY_PATH.read_text(encoding="utf-8"))
        for rule in policy["abac_rules"]:
            assert rule["tool"] in real_tool_names, (
                f"ABAC 规则 {rule['name']} 的目标工具 {rule['tool']!r} 不存在于注册表 ⇒ 永不触发"
            )

    def test_所有用例用的工具名都真实存在(self, real_tool_names):
        for name in DENIED_UNDER_GUEST + ALLOWED_UNDER_OWNER:
            assert name in real_tool_names, f"用例引用了不存在的工具名: {name}"


# ════════════════════════════════════════════════════════════
#  一、默认关闭：零行为变化
# ════════════════════════════════════════════════════════════


class TestStrictOffByDefault:

    @pytest.mark.parametrize("tool", DENIED_UNDER_GUEST)
    def test_未设开关时_guest_会拒的工具仍放行(self, tool):
        """这是"不设环境变量即与改动前一致"的直接证据：guest 白名单管不着这里"""
        assert G.check_tool_call(tool, {}) is None

    @pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off", " off ", "", "2", "on!", "enabled"])
    def test_非启用取值一律保持_fail_open(self, monkeypatch, value):
        monkeypatch.setenv(G.STRICT_ENABLED_ENV, value)
        monkeypatch.setenv(G.STRICT_ROLE_ENV, "guest")     # 若误启用，edit 必被拒
        assert G.check_tool_call("edit", {}) is None, f"取值 {value!r} 不该启用严格模式"

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "Yes", " on "])
    def test_启用取值大小写与空白不敏感(self, monkeypatch, value):
        monkeypatch.setenv(G.STRICT_ENABLED_ENV, value)
        monkeypatch.setenv(G.STRICT_ROLE_ENV, "guest")
        result = G.check_tool_call("edit", {})
        assert result is not None and result["blocked"] is True

    def test_总开关关闭时严格模式也不生效(self, monkeypatch):
        """``CP_TOOL_GATE_ENABLED=0`` 是更外层的总开关，严格模式不得越过它"""
        _enable_strict(monkeypatch, role="guest")
        monkeypatch.setenv(G.GATE_ENABLED_ENV, "0")
        assert G.check_tool_call("edit", {}) is None


# ════════════════════════════════════════════════════════════
#  二、owner：开关打开后"可用"
# ════════════════════════════════════════════════════════════


class TestStrictWithOwnerRole:

    @pytest.mark.parametrize("tool", ALLOWED_UNDER_OWNER)
    def test_owner_下常用工具仍放行(self, monkeypatch, tool):
        _enable_strict(monkeypatch, role="owner")
        assert G.check_tool_call(tool, {"path": "x.txt"}) is None, (
            f"owner 的 allowed_tools=[\"*\"] 必须让 {tool} 通过"
        )

    def test_缺省角色是最小权限_guest(self, monkeypatch):
        """只设开关、不设角色 ⇒ 走缺省 **guest（最小权限）**

        【2026-09-20 契约更新：因为 TASK-06 E15 落地】
          本用例原名 `test_缺省角色就是owner`，断言"缺省 = owner，所以 edit 放行"。
          TASK-06 §3 第 6 步第 6 项明确要求把严格模式的缺省角色从 `owner`（**特权**：
          `allowed_tools=["*"]` ⇒ 开了等于没开）改为**最小权限**（拒绝或只读）。
          故断言反转为：缺省走 `guest`，且一个不在 guest 白名单里的真实工具
          （`edit`）**被拒** —— 这正是"缺省不留特权"的可判定形式。
          需要宽松口径的场景（例如只想验证"白名单为通配时能放行"）请**显式**传
          `role="owner"`，见 `TestStrictWithOwnerRole` 的其它用例。
        """
        from agent.permission_system import Role
        monkeypatch.setenv(G.STRICT_ENABLED_ENV, "1")
        assert G._strict_role() == Role.GUEST
        blocked = G.check_tool_call("edit", {})
        assert blocked is not None and blocked["blocked"] is True, \
            "缺省角色必须是**最小权限**：edit 不在 guest 白名单里，必须被拒"
        assert "'guest'" in blocked["error"]

    def test_缺省会话来源是cli(self, monkeypatch):
        monkeypatch.setenv(G.STRICT_ENABLED_ENV, "1")
        assert G._strict_source() == "cli"

    def test_scheduled_来源会命中写文件禁令(self, monkeypatch):
        """session_source 不是装饰：报 scheduled 会命中 scheduled-no-write/edit"""
        _enable_strict(monkeypatch, role="owner", source="scheduled")
        blocked = G.check_tool_call("write_file", {"path": "x.txt", "content": "c"})
        assert blocked is not None and blocked["blocked"] is True
        assert "严格模式" in blocked["error"]


# ════════════════════════════════════════════════════════════
#  三、guest：严格模式真的会拒（且结构正确）
# ════════════════════════════════════════════════════════════


class TestStrictWithGuestRole:

    @pytest.mark.parametrize("tool", DENIED_UNDER_GUEST)
    def test_guest_下白名单外工具被拒(self, monkeypatch, tool):
        _enable_strict(monkeypatch, role="guest")
        result = G.check_tool_call(tool, {})
        assert result is not None, f"{tool} 不在 guest 白名单里，必须被严格模式拒绝"
        assert set(result) == {"ok", "blocked", "error_code", "error"}
        assert result["ok"] is False
        assert result["blocked"] is True
        assert result["error_code"] == "PERMISSION_DENIED"

    def test_拒绝文案注明严格模式并带上网关_reason(self, monkeypatch):
        _enable_strict(monkeypatch, role="guest")
        error = G.check_tool_call("edit", {})["error"]
        assert "严格模式" in error
        assert G.STRICT_ENABLED_ENV in error
        assert "'guest'" in error
        assert "权限不足" in error          # 网关给出的 reason 原样带出

    def test_guest_白名单内的工具仍放行(self, monkeypatch):
        """严格 ≠ 全封：guest 白名单里的 read_file 必须过"""
        _enable_strict(monkeypatch, role="guest")
        assert G.check_tool_call("read_file", {"path": "x.txt"}) is None


# ════════════════════════════════════════════════════════════
#  四、fail-open 边界：严格层自身出错 ⇒ 放行
# ════════════════════════════════════════════════════════════


class TestStrictFailOpen:

    def test_网关抛异常时放行且不抛(self, monkeypatch):
        """monkeypatch 网关为"一调就炸"：闸门必须放行，而不是把异常抛给调用方"""
        _enable_strict(monkeypatch, role="guest")     # 若网关正常，edit 必被拒

        boom = MagicMock()
        boom.check.side_effect = RuntimeError("gateway down")
        monkeypatch.setattr(G, "_strict_gateway", lambda: boom)

        assert G.check_tool_call("edit", {}) is None

    def test_网关构造失败时放行(self, monkeypatch):
        _enable_strict(monkeypatch, role="guest")

        def _boom():
            raise RuntimeError("cannot build gateway")

        monkeypatch.setattr(G, "_strict_gateway", _boom)
        assert G.check_tool_call("edit", {}) is None

    def test_网关返回结构异常时放行(self, monkeypatch):
        """返回值缺 allowed 字段 ⇒ 按放行处理（不认识就不拦）"""
        _enable_strict(monkeypatch, role="guest")

        gateway = MagicMock()
        gateway.check.return_value = object()          # 无 allowed 属性
        monkeypatch.setattr(G, "_strict_gateway", lambda: gateway)

        assert G.check_tool_call("edit", {}) is None

    def test_角色枚举导入失败时放行(self, monkeypatch):
        _enable_strict(monkeypatch, role="guest")

        def _boom(*_a, **_k):
            raise ImportError("permission_system unavailable")

        import agent.permission_system as ps
        monkeypatch.setattr(ps, "ABACContext", _boom)
        assert G.check_tool_call("edit", {}) is None

    def test_网关明确拒绝时不得被_fail_open_吞掉(self, monkeypatch):
        """反向钉死边界：fail-open 只针对"闸门出错"，不针对"网关说不行\""""
        _enable_strict(monkeypatch, role="guest")
        from agent.permission_system import PermissionResult

        gateway = MagicMock()
        gateway.check.return_value = PermissionResult(allowed=False, reason="权限不足")
        monkeypatch.setattr(G, "_strict_gateway", lambda: gateway)

        result = G.check_tool_call("edit", {})
        assert result is not None and result["blocked"] is True


# ════════════════════════════════════════════════════════════
#  五、非法角色值 ⇒ 回退 owner 且不崩
# ════════════════════════════════════════════════════════════


class TestInvalidRoleFallback:
    """非法角色值 ⇒ 回退**缺省角色**（现为 `guest` = 最小权限）且不崩

    【2026-09-20 契约更新：因为 TASK-06 E15 落地】
      本类原名 `TestInvalidRoleFallback::test_非法角色回退_owner`，docstring 的理由是
      "回退比因为拼错一个环境变量就变成全量拒绝安全得多"。TASK-06 把这条取舍**反过来**
      了：安全开关的误配置必须落在**更严**的一侧（否则"把角色名拼错"就是一条把严格
      模式静默降级为"全放行"的路径 —— `owner` 的 `allowed_tools=["*"]`）。
      故回退值改为 `_DEFAULT_STRICT_ROLE`（`guest`），且**未设置**与**非法**共用同一条
      规则（不为两者各留一套判据，D1）。
    """

    @pytest.mark.parametrize("bad", ["no_such_role", "GUEST2", "root", "管理员"])
    def test_非法角色回退缺省角色(self, monkeypatch, bad):
        from agent.permission_system import Role
        monkeypatch.setenv(G.STRICT_ENABLED_ENV, "1")
        monkeypatch.setenv(G.STRICT_ROLE_ENV, bad)
        assert G._strict_role() == Role(G._DEFAULT_STRICT_ROLE)
        assert G._strict_role() == Role.GUEST, "缺省角色必须是**最小权限**"

    def test_带空白的合法值不算非法(self, monkeypatch):
        """`"owner "` **不是**非法值：`_env_str()` 先去空白 ⇒ 它是合法的 `owner`

        【2026-09-20 实测更正】原参数化列表里含 `"owner "`，而它在旧断言
        （`== Role.OWNER`）下"通过"的原因是**它是合法值**，不是回退 —— 即那条参数
        一直在测另一件事（空白容忍），却挂在"回退"用例名下。现拆分为本用例。
        """
        from agent.permission_system import Role
        monkeypatch.setenv(G.STRICT_ENABLED_ENV, "1")
        monkeypatch.setenv(G.STRICT_ROLE_ENV, "owner ")
        assert G._strict_role() == Role.OWNER

    def test_非法角色下行为与缺省角色一致(self, monkeypatch):
        """回退后按 `guest` 判定 ⇒ edit 被拒（与"只设开关不设角色"逐字同款）

        【为什么不再断言"edit 放行"】那是 `owner` 时代的契约。现在"拼错角色名"的后果
        是**拒绝**（更严），这与 TASK-06 E15 的方向一致；要验证"回退没崩"的正面证据是
        拒绝结果的结构完整（`blocked=True` + `PERMISSION_DENIED` + 文案注明严格模式），
        以及 `_strict_role()` 不抛异常。
        """
        monkeypatch.setenv(G.STRICT_ENABLED_ENV, "1")
        monkeypatch.setenv(G.STRICT_ROLE_ENV, "no_such_role")
        blocked = G.check_tool_call("edit", {})
        assert blocked is not None and blocked["blocked"] is True
        assert blocked["error_code"] == "PERMISSION_DENIED"
        assert "严格模式" in blocked["error"]
        # 与"不设角色"逐字一致（同一条回退规则，不是两套）
        monkeypatch.delenv(G.STRICT_ROLE_ENV, raising=False)
        default_blocked = G.check_tool_call("edit", {})
        assert default_blocked is not None and default_blocked["blocked"] is True
        assert "'guest'" in default_blocked["error"]

    def test_大小写与空白被容忍(self, monkeypatch):
        from agent.permission_system import Role
        monkeypatch.setenv(G.STRICT_ENABLED_ENV, "1")
        monkeypatch.setenv(G.STRICT_ROLE_ENV, "  OWNER ")
        assert G._strict_role() == Role.OWNER


# ════════════════════════════════════════════════════════════
#  六、接线：tools.call() 的 handler 零执行
# ════════════════════════════════════════════════════════════


@pytest.fixture
def probe_tool():
    """注册一个计数探针工具到真实注册表；用完精确还原（与 test_tool_gate.py 同款）"""
    from agent import tools as registry

    calls = {"n": 0}

    def _handler(**kwargs):
        calls["n"] += 1
        return {"ok": True, "result": "handler-ran", "echo": kwargs}

    saved_entry = registry._registry.get(PROBE_TOOL)
    saved_health = registry._tool_health.get(PROBE_TOOL)
    registry.register(PROBE_TOOL, "严格模式接线探针工具", handler=_handler)
    try:
        yield calls
    finally:
        if saved_entry is None:
            registry.unregister(PROBE_TOOL)
        else:
            registry._registry[PROBE_TOOL] = saved_entry
            registry._registry_version += 1
        if saved_health is None:
            registry._tool_health.pop(PROBE_TOOL, None)
        else:
            registry._tool_health[PROBE_TOOL] = saved_health


class TestToolsCallIntegration:

    def test_严格模式下被拒工具_handler_零执行(self, monkeypatch, probe_tool):
        from agent import tools as registry

        _enable_strict(monkeypatch, role="guest")     # 探针工具不在 guest 白名单

        result = registry.call(PROBE_TOOL, a=1)

        assert probe_tool["n"] == 0, "被严格模式拒绝的工具，handler 绝不能执行"
        assert result["ok"] is False
        assert result["blocked"] is True
        assert result["error_code"] == "PERMISSION_DENIED"
        assert "严格模式" in result["error"]

    def test_严格模式下放行工具正常执行一次(self, monkeypatch, probe_tool):
        from agent import tools as registry

        _enable_strict(monkeypatch, role="owner")

        result = registry.call(PROBE_TOOL, a=1)

        assert result["ok"] is True and result["result"] == "handler-ran"
        assert probe_tool["n"] == 1

    def test_未设开关时直连路径行为不变(self, probe_tool):
        from agent import tools as registry

        result = registry.call(PROBE_TOOL, a=1)

        assert result["ok"] is True
        assert probe_tool["n"] == 1


# ════════════════════════════════════════════════════════════
#  七、任务 C1：时间窗口支持跨午夜
# ════════════════════════════════════════════════════════════


class TestTimeInWindowCrossMidnight:
    """``PermissionGateway._time_in_window`` 的两种语义

    传 ``now`` 是为了可测（**不打全局 datetime / 系统时钟**）；既有调用点
    （``_check_abac``）都不传该参数 ⇒ 行为与改动前一致。
    """

    @pytest.mark.parametrize("start,end,now,expected", [
        # ── 同日窗口（历史语义，必须一字不变）──
        ("09:00", "18:00", "12:00", True),
        ("09:00", "18:00", "20:00", False),
        ("09:00", "18:00", "09:00", True),      # 含起点端点
        ("09:00", "18:00", "18:00", True),      # 含终点端点
        ("09:00", "18:00", "08:59", False),
        ("09:00", "18:00", "18:01", False),
        ("00:00", "23:59", "00:00", True),
        ("00:00", "23:59", "23:59", True),
        ("12:00", "12:00", "12:00", True),      # start == end ⇒ 退化为单点
        ("12:00", "12:00", "12:01", False),
    ])
    def test_同日窗口语义不变(self, start, end, now, expected):
        from agent.permission_system import PermissionGateway

        assert PermissionGateway._time_in_window(start, end, now) is expected

    @pytest.mark.parametrize("start,end,now,expected", [
        # ── 跨午夜窗口（本次新增；改动前恒为 False）──
        ("18:00", "06:00", "20:00", True),
        ("18:00", "06:00", "02:00", True),
        ("18:00", "06:00", "12:00", False),
        ("18:00", "06:00", "18:00", True),      # 含起点端点
        ("18:00", "06:00", "06:00", True),      # 含终点端点
        ("18:00", "06:00", "17:59", False),
        ("18:00", "06:00", "06:01", False),
        ("18:00", "06:00", "23:59", True),
        ("18:00", "06:00", "00:00", True),
        ("23:00", "01:00", "23:30", True),
        ("23:00", "01:00", "00:30", True),
        ("23:00", "01:00", "12:00", False),
    ])
    def test_跨午夜窗口生效(self, start, end, now, expected):
        from agent.permission_system import PermissionGateway

        assert PermissionGateway._time_in_window(start, end, now) is expected

    def test_改动前跨午夜窗口恒为假(self):
        """钉住被修掉的缺陷本身：旧的 ``start <= now <= end`` 链式比较在 start > end 时恒假"""
        for now in ("00:00", "06:00", "12:00", "18:00", "20:00", "23:59"):
            assert not ("18:00" <= now <= "06:00"), (
                "这正是原实现表达不出跨午夜窗口的原因：任何 now 都不可能同时 >=18:00 且 <=06:00"
            )

    def test_不传_now_时走真实时钟(self, monkeypatch):
        """缺省参数走 datetime.now()；用 00:00-23:59 全天窗口做时间无关断言"""
        from agent.permission_system import PermissionGateway

        assert PermissionGateway._time_in_window("00:00", "23:59") is True

    def test_经_check_abac_链路跨午夜窗口可被正确判定(self, tmp_path):
        """把 C1 的修复放到**真实 ABAC 规则链**上验证（不是只测 helper）

        构造两个窗口，两者的端点都距当前时刻 ≥3 小时 ⇒ 结果与"跑测试时刚好跨分钟"无关：

        - ``[now-3h, now+3h]``：无论是否跨午夜都**包含** now ⇒ 规则不拒绝；
        - ``[now+3h, now-3h]``：无论是否跨午夜都**不包含** now ⇒ 规则拒绝。

        旧的字符串比较实现下，第二种（跨午夜）永远拒绝、第一种（跨午夜的半边）也永远拒绝，
        即"按 18:00 上班的作息配窗口"根本无从表达。
        """
        from agent.permission_system import (
            ABACContext, PermissionGateway, Role,
        )

        def _gateway_with_window(start: str, end: str):
            policy = {
                "version": 1,
                "default_role": "guest",
                "roles": {"admin": {"allowed_tools": ["*"], "denied_tools": []}},
                "abac_rules": [{
                    "name": "probe-window",
                    "tool": "shell_execute",
                    "deny_if": {"time_outside": [start, end]},
                }],
            }
            path = tmp_path / f"policy_{start.replace(':', '')}_{end.replace(':', '')}.json"
            path.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")
            return PermissionGateway(policy_path=str(path))

        now = datetime.now()
        before = (now - timedelta(hours=3)).strftime("%H:%M")
        after = (now + timedelta(hours=3)).strftime("%H:%M")

        inside = _gateway_with_window(before, after)
        assert inside.check("shell_execute", {}, ABACContext(role=Role.ADMIN)).allowed is True

        outside = _gateway_with_window(after, before)
        assert outside.check("shell_execute", {}, ABACContext(role=Role.ADMIN)).allowed is False


# ════════════════════════════════════════════════════════════
#  八、任务 C2/C3：shell_execute 不再被时间窗口拒绝
# ════════════════════════════════════════════════════════════


class TestShellExecuteNoLongerTimeBlocked:

    def test_严格模式加_owner_下_shell_execute_不被拒(self, monkeypatch):
        """真实策略下（ABAC 已无 time_outside 规则）⇒ 时间不再是拒绝理由

        这条用例与运行时刻无关：规则已从 ``abac_rules`` 移除，唯一的时段来源消失，
        故无论白天还是 18:00 之后都必须放行（改动前：00:07 实测会被 ABAC 拒）。

        【2026-09-17 补注】本用例钉的是**严格模式（RBAC/ABAC）这一层**的契约，与
        "该工具要不要人工审批"是两件事。``shell_execute``（risk=critical）正落在审批
        边界上，故本文件用 autouse 夹具关掉那条独立开关（见 ``_clean_strict_env``），
        使断言只反映 RBAC/ABAC 的结果。
        """
        _enable_strict(monkeypatch, role="owner")
        assert G.check_tool_call("shell_execute", {"command": "echo hi"}) is None

    def test_真实策略下_ABAC_已无时段规则(self):
        """直接核对数据：``time_outside`` 不再出现在活规则里（时间无关的静态断言）"""
        policy = json.loads(_POLICY_PATH.read_text(encoding="utf-8"))
        offenders = [
            rule["name"] for rule in policy["abac_rules"]
            if "time_outside" in (rule.get("deny_if") or {})
        ]
        assert not offenders, f"这些活规则仍带时段限制，会在工作时间外误杀: {offenders}"

    def test_网关对_shell_execute_已无时段性结果(self):
        """真策略 + owner：连测多次结果必须一致（时段规则若在，00:07 与白天会不同）"""
        from agent.permission_system import ABACContext, PermissionGateway, Role
        gateway = PermissionGateway(policy_path=str(_POLICY_PATH))
        results = {
            gateway.check("shell_execute", {}, ABACContext(role=Role.OWNER)).allowed
            for _ in range(3)
        }
        assert results == {True}
