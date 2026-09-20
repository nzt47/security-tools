"""``fan_out`` 工具（并行多线委派）单元测试

覆盖任务书要求的六条：
  1. 八要素缺一项 ⇒ **整体拒绝**，错误里点名是第几个任务缺什么（且零副作用）
  2. 正常路径 ⇒ 每个子任务拿到的是**其主线装配出的工具集**（断言 ``authorized_capabilities``）
  3. 主线不存在/已停用 ⇒ 该任务失败，且**没有**退化成全量授权
  4. ``govern`` 平面工具默认不出现在任何子任务的授权集里（只有显式 allow_govern 才放行）
  5. 单个子任务失败 ⇒ ``fan_out`` 整体仍返回逐任务信封，``failed=1``
  6. ``max_concurrency`` 越界被 clamp
外加：注册与 schema 口径、YAML 与 Python 字面量一致、预算/超时透传与汇总、前置能力报错。

【不易】不启动任何子进程、不调用任何真实 LLM：
        执行器经 ``fan_out_tools._build_executor`` 注入点替换为假执行器；
        ``dl`` 是 MagicMock（只暴露 ``_subagent_mgr`` / ``_llm``）。
【简易】注册后即注销，全局工具注册表不留残留。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest
import yaml

from agent import tool_router as _router
from agent import tools as _tools
from agent.lines import assemble, get_line_registry, load_tool_meta
from agent.subagent.collection import CostRecord
from agent.subagent.delegation import (
    E_DELEGATION_INCOMPLETE,
    EIGHT_ELEMENTS,
    ELEMENT_ARTIFACT_FORMAT,
    ELEMENT_BUDGET_TOKENS,
    ELEMENT_CALLBACK_URL,
    ELEMENT_CONSTRAINTS,
    ELEMENT_GOAL,
    ELEMENT_LABELS,
    ELEMENT_PRIOR_ARTIFACTS,
    ELEMENT_PROHIBITIONS,
    ELEMENT_TIMEOUT_SECONDS,
    DelegationContext,
    element_problems,
)
from agent.subagent.executor import DEFAULT_MAX_CONCURRENCY, ExecutionOutcome
from agent.subagent.lifecycle import SubagentLifecycleManager
from agent.subagent.toolset import SubAgentToolset
from agent.tools import fan_out_tools, subagent_tools

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_FAN_OUT_YAML = _PROJECT_ROOT / "data" / "tool_definitions" / "fan_out.yaml"

#: 一份合法任务（用例在此基础上做覆盖）
VALID_TASK = {
    "line": "engineering",
    "goal": "把 docs/zh 下的 12 篇设计稿抽取为可复现步骤序列",
    "constraints": ["只读仓库，不得修改任何文件"],
    "prior_artifacts": [],
    "prohibitions": [],
    "artifact_format": "JSON Lines：每行 {name, steps[]}",
    "budget_tokens": 20000,
    "timeout_seconds": 60,
    "callback_url": "internal://fan_out",
}

_UNSET = object()


def _task(**overrides) -> dict:
    """合法任务 + 覆盖项（深拷贝，避免用例之间互相污染）"""
    data = copy.deepcopy(VALID_TASK)
    data.update(overrides)
    return data


def _outcome(delegation_id: str = "", ok: bool = True,
             tokens: int = 0, **overrides) -> ExecutionOutcome:
    """假 ``ExecutionOutcome``（真实 dataclass：字段映射才是真校验）"""
    # 显式标注 Dict[str, Any]：混合取值会被推成 dict[str, object]，
    # 展开给 dataclass 时会报 10 处 arg-type（mypy 实测踩过）
    data: Dict[str, Any] = {
        "delegation_id": delegation_id,
        "ok": ok,
        "tier": "jsonl",
        "payload": {"summary": f"完成 {delegation_id}"},
        "artifacts": ({"path": "out/a.jsonl", "size": 12},),
        "duration_ms": 12.5,
    }
    if tokens:
        data["cost"] = CostRecord(delegation_id=delegation_id, counted=True, wasted=False,
                                  input_tokens=tokens - 10, output_tokens=10)
    data.update(overrides)
    return ExecutionOutcome(**data)


class _FakeExecutor:
    """假执行器：记录 ``execute_many`` 入参，按契约返回逐任务结果

    ``tools_for`` / ``authorized_for`` 是**逐任务**工厂（``execute_many`` 的 ``tools`` /
    ``authorized_capabilities`` 是整批共用的单值）——本假件就地求值并记进调用记录，
    断言因此看到的是"那个子代理真正被授权的集合"。

    ``fail_indexes`` 复刻真实执行器的**线程级兜底**契约
    （``agent/subagent/executor.py`` 的 ``_run``：单任务异常收敛为 ``ok=False``
    的 ``ExecutionOutcome``，不拖垮整批），用来验证 fan_out 侧不会把它升级成整体失败。
    """

    def __init__(self, *, fail_indexes=(), drop_indexes=(), raise_on_call: bool = False,
                 tokens_per_task: int = 0) -> None:
        self.calls: list = []
        self._fail = set(fail_indexes)
        self._drop = set(drop_indexes)
        self._raise = bool(raise_on_call)
        self._tokens = int(tokens_per_task)

    def execute_many(self, delegations, *, max_concurrency=None, tools=(),
                     authorized_capabilities=None, tools_for=None, authorized_for=None,
                     credentials_for=None, parent_trace=None):
        items = list(delegations)
        self.calls.append({
            "delegations": items,
            "max_concurrency": max_concurrency,
            "tools": list(tools),
            "authorized_capabilities": (None if authorized_capabilities is None
                                        else list(authorized_capabilities)),
            "authorized": {
                ctx.delegation_id: list(authorized_for(ctx) if authorized_for is not None
                                        else (authorized_capabilities or ()))
                for ctx in items
            },
            "requested": {
                ctx.delegation_id: list(tools_for(ctx) if tools_for is not None else tools)
                for ctx in items
            },
            "credentials_for": credentials_for,
            "parent_trace": parent_trace,
        })
        if self._raise:
            raise RuntimeError("批量入口炸了")
        out = []
        for idx, ctx in enumerate(items):
            if idx in self._drop:
                continue
            if idx in self._fail:
                out.append(ExecutionOutcome(
                    delegation_id=ctx.delegation_id, ok=False,
                    error_code="E_DELEGATION_FAILED",
                    error="RuntimeError: 子代理执行异常",
                    sub_reason="executor_exception"))
            else:
                out.append(_outcome(ctx.delegation_id, tokens=self._tokens))
        return out


def _all_yaml_tool_names() -> list:
    return sorted(load_tool_meta().keys())


def _expected_tools_for_line(line_id: str) -> list:
    """该主线的期望授权集

    = 装配器结果 − 未开启 allow_govern 时的 govern 项 − §5.7 机制 3 硬禁项
      （子代理工具集不含记忆读写：矩阵 ``view.memory`` / ``memory.write``
       对 sub_agent 是 ❌，故 ``search_memory`` / ``remember`` 等不进授权集）
    """
    from agent.subagent.toolset import SubAgentToolset

    meta = load_tool_meta()
    profile = get_line_registry().load(line_id)
    assert profile is not None, f"主线不存在: {line_id}"
    result = assemble(profile, sorted(meta.keys()), meta=meta)
    tools = list(result.tools)
    if not profile.allow_govern:
        tools = [t for t in tools if meta[t].plane != "govern"]
    hard = set(SubAgentToolset.hard_denied(tools))
    return [t for t in tools if t not in hard]


def _govern_tools() -> set:
    meta = load_tool_meta()
    return {n for n, m in meta.items() if m.plane == "govern"}


def _granted(call: dict, ctx) -> set:
    """取某 ctx 在这批里被授权的工具集，并钉住三条同源不变量

    1. 执行器侧工厂（``authorized_for``）求值结果；
    2. ``ctx.metadata["authorized_capabilities"]``（进 task_file 的那份）；
    3. ``tools_for`` 的申请清单；
    三者必须一致，否则「申请 ∩ 授权」会把子代理可见集裁掉一部分。
    """
    authorized = set(call["authorized"][ctx.delegation_id])
    assert authorized == set(ctx.metadata["authorized_capabilities"]), (
        "authorized_for 工厂与 ctx.metadata 的授权集不一致")
    assert authorized == set(call["requested"][ctx.delegation_id]), (
        "申请清单与授权清单不同源（子代理可见集会因此被裁剪）")
    return authorized


# ════════════════════════════════════════════════════════════
#  fixtures
# ════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _isolate_registration():
    """每个用例结束后注销 fan_out，避免污染全局工具注册表"""
    yield
    _tools.unregister("fan_out")


@pytest.fixture(autouse=True)
def _no_real_channel(monkeypatch):
    """默认关掉外部 CLI 通道（走 LLM 通道）——避免宿主环境变量影响用例"""
    monkeypatch.delenv("CP_SUBAGENT_AGENT_CLI", raising=False)


@pytest.fixture()
def full_pool(monkeypatch):
    """模拟「全量工具已注册」的宿主：候选池 = data/tool_definitions/*.yaml 的工具名"""
    names = _all_yaml_tool_names()
    monkeypatch.setattr(fan_out_tools, "_available_tool_names", lambda: list(names))
    return names


@pytest.fixture()
def make_tool(monkeypatch):
    """返回 ``(handler, executor, manager, llm)`` 的工厂（执行器是假件，管理器是真件）

    管理器刻意用**真** ``SubagentLifecycleManager``：``fan_out`` 走
    ``manager.delegate_many``（分身纪律：容量上限/TTL/回收），用真件才能断言
    「分身建了又回收、容量上限生效」；LLM 与执行器仍是假件（不起子进程、不触网）。
    """
    def _make(llm=_UNSET, mgr=_UNSET, executor=None):
        manager = SubagentLifecycleManager() if mgr is _UNSET else mgr
        llm_obj = MagicMock() if llm is _UNSET else llm
        dl = MagicMock()
        dl._subagent_mgr = manager
        dl._llm = llm_obj
        ex = _FakeExecutor() if executor is None else executor
        monkeypatch.setattr(fan_out_tools, "_build_executor", lambda _llm: ex)
        fan_out_tools.register_all(dl)
        return _tools._registry["fan_out"]["handler"], ex, manager, llm_obj
    return _make


# ════════════════════════════════════════════════════════════
#  1. 注册与治理声明
# ════════════════════════════════════════════════════════════


class TestRegistrationAndGovernance:
    def test_工具已注册且_schema_是对象(self, make_tool):
        handler, _, _, _ = make_tool()
        assert callable(handler)
        assert _tools._registry["fan_out"]["schema"]["type"] == "object"

    def test_描述含检索关键词(self, make_tool):
        make_tool()
        desc = _tools._registry["fan_out"]["description"]
        for kw in ("fan_out", "parallel delegate", "multi-agent", "并行"):
            assert kw in desc, f"工具 description 缺少检索关键词 {kw}"

    def test_schema_任务项必填八要素(self, make_tool):
        make_tool()
        items = _tools._registry["fan_out"]["schema"]["properties"]["tasks"]["items"]
        assert set(items["required"]) == set(EIGHT_ELEMENTS)
        for name in EIGHT_ELEMENTS:
            assert ELEMENT_LABELS[name] in items["properties"][name]["description"]

    def test_schema_只要求_tasks_必填(self, make_tool):
        make_tool()
        schema = _tools._registry["fan_out"]["schema"]
        assert schema["required"] == ["tasks"]
        assert set(schema["properties"]) == {"tasks", "max_concurrency"}

    def test_治理声明_act_execute_high(self):
        """plane=act / effect=execute / risk=high（起 N 个并发子代理，成本与副作用放大）"""
        doc = yaml.safe_load(_FAN_OUT_YAML.read_text(encoding="utf-8"))
        assert doc["name"] == "fan_out"
        assert doc["category"] == "async"
        assert doc["plane"] == "act"
        assert doc["effect"] == "execute"
        assert doc["risk"] == "high"
        assert doc["deprecated"] is False
        assert doc["version"] == "1.0.0"
        assert doc["examples"] == []
        # 字段顺序照 grep.yaml；`tool_type`/`llm_callable`/`callable_mode`/
        # `permission_level`/`sandbox_allowed` 是「可被 LLM 调用」统一标注字段
        # （见 docs/工具与技能可调用性标注规范.md），插在治理三元组与 tags 之间
        #
        # 【2026-09-20 契约更新：TASK-04 新增两处字段（本用例因此变红，非 TASK-06 引起）】
        #   `location` / `location_reason` 由 TASK-04（CapabilitySpec + location，提交
        #   `807401ba`）加进 91 个 YAML，位置在 `version` 之后、治理三元组之前。
        #   本用例断言的是**字段集合与顺序**这一契约（防"数据被悄悄改写"），
        #   故随契约更新补上这两个键；顺序取自 `data/tool_definitions/fan_out.yaml` 实况。
        #   注：`permission_level` 在本文件里的取值由 `internal` 变为 `restricted`
        #   （TASK-06：`risk: high` 进确认流 ⇒ 派生权限等级为 restricted）——
        #   本用例只钉**键名**，不钉取值；取值由
        #   `tests/unit/test_tool_callability.py` 与 `test_confirm_level.py` 负责。
        assert list(doc.keys()) == [
            "name", "category", "description", "deprecated", "version",
            "location", "location_reason",
            "plane", "effect", "risk", "tool_type", "llm_callable",
            "callable_mode", "permission_level", "sandbox_allowed",
            "tags", "schema", "examples",
        ]

    def test_yaml_与_python_字面量逐字段一致(self, make_tool):
        """YAML 的 description/schema 必须与 @_tools.register 的静态字面量完全相同"""
        make_tool()
        entry = _tools._registry["fan_out"]
        doc = yaml.safe_load(_FAN_OUT_YAML.read_text(encoding="utf-8"))
        assert doc["name"] == entry["name"]
        assert doc["description"] == entry["description"]
        assert doc["schema"] == entry["schema"]

    def test_已接入关键词路由的_async_分类(self):
        """漏了这步它在关键词路由下不可达（文档 §7 第 3 步）"""
        assert "fan_out" in _router._DEFAULT_TOOL_CATEGORIES["async"]["tools"]
        assert "fan_out" in _router.TOOL_CATEGORIES["async"]["tools"]
        assert "fan_out" in _router.ALL_TOOLS_SET

    def test_候选池取自真实注册表(self):
        """``_available_tool_names`` 反映宿主注册的工具（fail-closed 的前提）"""
        _tools.register("fan_out_probe_a", "探针A", handler=lambda **kw: {"ok": True})
        _tools.register("fan_out_probe_b", "探针B", handler=lambda **kw: {"ok": True})
        try:
            names = fan_out_tools._available_tool_names()
            assert "fan_out_probe_a" in names and "fan_out_probe_b" in names
            assert len(names) == len(set(names)), "候选池出现重复项"
        finally:
            _tools.unregister("fan_out_probe_a")
            _tools.unregister("fan_out_probe_b")

    def test_候选池为空时不退回YAML全量(self, monkeypatch):
        monkeypatch.setattr(_tools, "list_tools", lambda: [])
        assert fan_out_tools._available_tool_names() == []


# ════════════════════════════════════════════════════════════
#  2. 八要素：先全校验、后副作用
# ════════════════════════════════════════════════════════════


class TestBatchValidation:
    def test_第2个任务约束为空整体拒绝并点名(self, make_tool, full_pool):
        handler, ex, _, _ = make_tool()
        result = handler(tasks=[_task(line="dev"), _task(constraints=[])])

        assert result["ok"] is False
        assert result["error_code"] == E_DELEGATION_INCOMPLETE
        assert "第 2 个任务" in result["error"]
        assert "②约束" in result["error"]
        assert ex.calls == [], "八要素不合格却已经启动了子代理（违反「先全校验后副作用」）"
        assert result["tasks_problems"] == [{
            "index": 1,
            "problems": {"constraints": element_problems(
                {**VALID_TASK, "constraints": []})["constraints"]},
            "missing_labels": ["②约束"],
        }]

    def test_未提供的字段按未声明处理(self, make_tool, full_pool):
        """③④「未声明」与「声明为空」是两件事：省略该字段即拒绝"""
        handler, ex, _, _ = make_tool()
        broken = _task()
        broken.pop(ELEMENT_PRIOR_ARTIFACTS)
        broken.pop(ELEMENT_PROHIBITIONS)
        result = handler(tasks=[broken])

        assert result["ok"] is False
        assert set(result["tasks_problems"][0]["problems"]) == {
            ELEMENT_PRIOR_ARTIFACTS, ELEMENT_PROHIBITIONS}
        assert "③已有成果" in result["error"] and "④禁止事项" in result["error"]
        assert ex.calls == []

    def test_多个任务不合格时逐个点名(self, make_tool, full_pool):
        handler, _, _, _ = make_tool()
        result = handler(tasks=[
            _task(goal="太短"),
            _task(line="dev"),
            _task(budget_tokens=0),
        ])
        assert [p["index"] for p in result["tasks_problems"]] == [0, 2]
        assert "第 1 个任务" in result["error"] and "第 3 个任务" in result["error"]
        assert "①目标" in result["error"] and "⑥预算令牌" in result["error"]

    @pytest.mark.parametrize("overrides,label", [
        ({ELEMENT_GOAL: "   "}, "①目标"),
        ({ELEMENT_ARTIFACT_FORMAT: ""}, "⑤产物格式"),
        ({ELEMENT_BUDGET_TOKENS: -1}, "⑥预算令牌"),
        ({ELEMENT_TIMEOUT_SECONDS: "60"}, "⑦超时"),
        ({ELEMENT_CALLBACK_URL: "  "}, "⑧回调地址"),
    ])
    def test_单个要素不合格即整体拒绝(self, make_tool, full_pool, overrides, label):
        handler, ex, _, _ = make_tool()
        result = handler(tasks=[_task(line="dev"), _task(**overrides)])
        assert result["ok"] is False
        assert label in result["error"]
        assert result["tasks_problems"][0]["index"] == 1
        assert ex.calls == []

    def test_目标过短被拒(self, make_tool, full_pool):
        handler, ex, _, _ = make_tool()
        result = handler(tasks=[_task(goal="改一下")])
        assert result["ok"] is False
        assert "①目标" in result["error"]
        assert ex.calls == []

    def test_任务不是对象被点名(self, make_tool, full_pool):
        handler, ex, _, _ = make_tool()
        result = handler(tasks=["不是对象"])
        assert result["ok"] is False
        assert result["tasks_problems"][0]["index"] == 0
        assert "必须是对象" in result["error"]
        assert ex.calls == []

    def test_tasks_缺失或为空被拒(self, make_tool, full_pool):
        handler, ex, _, _ = make_tool()
        for bad in (None, [], "x", {}):
            result = handler(tasks=bad)
            assert result["ok"] is False
            assert result["error_code"] == fan_out_tools.E_FAN_OUT_NO_TASKS
        assert ex.calls == []


# ════════════════════════════════════════════════════════════
#  3. 正常路径：按主线装配 + 并发派发
# ════════════════════════════════════════════════════════════


class TestHappyPath:
    def test_每个子任务拿到其主线装配的工具集(self, make_tool, full_pool):
        handler, ex, _, _ = make_tool()
        task_specs = [("engineering", 30000, 600), ("assistant", 20000, 120),
                      ("dev", 10000, 60)]
        result = handler(tasks=[
            _task(line=line, budget_tokens=budget, timeout_seconds=timeout)
            for line, budget, timeout in task_specs
        ])

        assert len(ex.calls) == 1, "execute_many 必须只被调用一次（并发生效）"
        call = ex.calls[0]
        assert len(call["delegations"]) == 3

        for ctx, (line, budget, timeout) in zip(call["delegations"], task_specs):
            expected = _expected_tools_for_line(line)
            assert _granted(call, ctx) == set(expected), f"{line} 的授权集与装配结果不符"
            assert ctx.metadata["line"] == line
            # 预算/超时逐任务透传给该子代理的委派契约
            assert ctx.budget_tokens == budget
            assert ctx.timeout_seconds == timeout
            assert isinstance(ctx, DelegationContext)
            assert ctx.validate() == ()

        # 三条线的工具集确实不同（证明是「按主线」而不是「一套通用清单」）
        sets = [_granted(call, c) for c in call["delegations"]]
        assert sets[0] != sets[1] and sets[0] != sets[2]
        assert "read_file" in sets[0] and "shell_execute" in sets[0]

        # 整批单值是空的（逐任务工具集走工厂），且工厂求值结果就是「申请 ∩ 授权」的原样
        assert call["tools"] == [] and call["authorized_capabilities"] is None
        for ctx in call["delegations"]:
            subset = ctx.metadata["authorized_capabilities"]
            assert set(SubAgentToolset.build(
                call["requested"][ctx.delegation_id], subset).visible_tools()) == set(subset), (
                "申请 ∩ 该任务授权 ≠ 该任务授权：逐任务授权机制失效")

    def test_结果信封逐任务如实汇报(self, make_tool, full_pool):
        handler, ex, _, _ = make_tool(executor=_FakeExecutor(tokens_per_task=120))
        result = handler(tasks=[_task(line="engineering"), _task(line="dev")])

        assert result["ok"] is True
        assert result["status"] == "all_succeeded"
        assert result["partial"] is False
        assert result["total"] == 2 and result["succeeded"] == 2 and result["failed"] == 0
        assert result["max_concurrency"] == DEFAULT_MAX_CONCURRENCY

        first = result["results"][0]
        assert first["index"] == 0 and first["line"] == "engineering"
        assert first["status"] == "ok"
        assert first["executed"] is True
        assert first["error_code"] == ""
        assert "完成" in first["summary"]
        assert first["outcome"]["delegation_id"].startswith("fan-")
        assert first["outcome"]["ok"] is True
        assert set(first["tools_granted"]) == set(_expected_tools_for_line("engineering"))
        assert first["tokens_used"] == 120

        # 汇总预算：总额度 = 各任务之和；实际消耗来自成本记账
        assert result["budget"]["allotted_tokens"] == 20000 * 2
        assert result["budget"]["allotted_timeout_seconds"] == 120.0
        assert result["budget"]["consumed_tokens"] == 240

    def test_同一主线被多个任务复用(self, make_tool, full_pool):
        handler, ex, _, _ = make_tool()
        result = handler(tasks=[_task(), _task(goal="把 README 的三段架构说明改写成表格")])
        assert result["succeeded"] == 2
        call = ex.calls[0]
        grants = [_granted(call, c) for c in call["delegations"]]
        assert grants[0] == grants[1] == set(_expected_tools_for_line("engineering"))

    def test_缺省用全局激活主线(self, make_tool, full_pool, monkeypatch):
        monkeypatch.setattr(get_line_registry(), "get_active", lambda: "dev")
        handler, ex, _, _ = make_tool()
        task = _task()
        task.pop("line")
        result = handler(tasks=[task])

        ctx = ex.calls[0]["delegations"][0]
        assert result["results"][0]["line"] == "dev"
        assert _granted(ex.calls[0], ctx) == set(_expected_tools_for_line("dev"))

    def test_无主线时用只读默认集(self, make_tool, full_pool, monkeypatch):
        monkeypatch.setattr(get_line_registry(), "get_active", lambda: None)
        handler, ex, _, _ = make_tool()
        task = _task()
        task.pop("line")
        result = handler(tasks=[task])

        granted = _granted(ex.calls[0], ex.calls[0]["delegations"][0])
        assert granted == set(subagent_tools._DEFAULT_SUBAGENT_TOOLS)
        assert not granted & {"write_file", "edit", "shell_execute"}
        assert result["results"][0]["line"] == ""

    def test_并发热点_只调用一次_execute_many(self, make_tool, full_pool):
        handler, ex, _, _ = make_tool()
        handler(tasks=[_task(line="dev") for _ in range(5)])
        assert len(ex.calls) == 1
        assert len(ex.calls[0]["delegations"]) == 5
        assert len({c.delegation_id for c in ex.calls[0]["delegations"]}) == 5, (
            "并发派发的 delegation_id 必须互不相同")

    def test_分身纪律_逐任务建分身且执行后全部回收(self, make_tool, full_pool):
        """fan_out 走 ``manager.delegate_many``：分身可见、TTL 取契约⑦、批次结束即回收"""
        handler, _, manager, _ = make_tool()
        result = handler(tasks=[_task(line="dev", timeout_seconds=42),
                                _task(line="assistant", timeout_seconds=7)])

        assert result["succeeded"] == 2
        stats = manager.get_stats()
        assert stats["total_created"] == 2, "逐任务没有建分身（绕过了生命周期管理器）"
        assert stats["total_destroyed"] == 2, "分身未回收"
        assert manager.count() == 0, "批次结束后仍有存活分身"

    def test_分身容量上限生效_超限任务就地失败(self, make_tool, full_pool):
        """``subagent.max_subagents`` 是硬上限：建不出分身的任务失败，其余照跑"""
        handler, ex, _, _ = make_tool(mgr=SubagentLifecycleManager(max_subagents=1))
        result = handler(tasks=[_task(line="dev"), _task(line="assistant")])

        assert result["succeeded"] == 1 and result["failed"] == 1
        failed = result["results"][1]
        assert failed["status"] == "failed"
        assert failed["error_code"] == "E_SUBAGENT_UNAVAILABLE"
        assert "上限" in failed["error"]
        assert failed["executed"] is False, "没进执行器却标成 executed=True"
        assert failed["tools_granted"], "该任务仍应如实带回它被授权过的工具集"
        assert len(ex.calls[0]["delegations"]) == 1, "超限任务不得被派发"


# ════════════════════════════════════════════════════════════
#  4. 主线不可用：该任务失败，绝不退化成全量授权
# ════════════════════════════════════════════════════════════


class TestLineUnavailable:
    def test_主线不存在该任务失败(self, make_tool, full_pool):
        handler, ex, _, _ = make_tool()
        result = handler(tasks=[_task(line="engineering"), _task(line="no_such_line")])

        assert result["total"] == 2 and result["succeeded"] == 1 and result["failed"] == 1
        assert result["status"] == "partial" and result["partial"] is True
        # ok 取「至少有一个成功」：单个任务失败不让整次调用被当成"没干活"；
        # 部分失败由 partial/failed/error_summary + 逐任务 error 如实表达
        assert result["ok"] is True
        assert "1/2 个子任务失败" in result["error_summary"]
        assert "第 2 个" in result["error_summary"]

        failed = result["results"][1]
        assert failed["status"] == "failed"
        assert failed["error_code"] == fan_out_tools.E_FAN_OUT_LINE_UNAVAILABLE
        assert "主线不存在" in failed["error"]
        assert failed["tools_granted"] == []
        assert failed["executed"] is False
        assert failed["outcome"] is None

        assert len(ex.calls[0]["delegations"]) == 1, "主线不可用的任务不得被派发"
        granted = _granted(ex.calls[0], ex.calls[0]["delegations"][0])
        assert granted == set(_expected_tools_for_line("engineering"))
        assert granted < set(full_pool), "授权集等于全量工具 ⇒ 退化成全量授权"

    def test_主线已停用该任务失败(self, make_tool, full_pool, monkeypatch):
        profile = get_line_registry().load("engineering")
        profile.enabled = False
        stub = MagicMock()
        stub.get_active.return_value = None
        stub.load.side_effect = lambda lid: profile if lid == "engineering" else None
        monkeypatch.setattr("agent.lines.get_line_registry", lambda: stub)

        handler, ex, _, _ = make_tool()
        result = handler(tasks=[_task(line="engineering")])

        assert result["failed"] == 1
        assert "已停用" in result["results"][0]["error"]
        assert result["results"][0]["tools_granted"] == []
        assert ex.calls == []

    def test_主线档案损坏该任务失败(self, make_tool, full_pool, monkeypatch):
        from agent.lines import LineRegistryError

        stub = MagicMock()
        stub.get_active.return_value = None
        stub.load.side_effect = LineRegistryError("坏的档案")
        monkeypatch.setattr("agent.lines.get_line_registry", lambda: stub)

        handler, ex, _, _ = make_tool()
        result = handler(tasks=[_task(line="engineering")])
        assert result["failed"] == 1
        assert "主线档案不可用" in result["results"][0]["error"]
        assert ex.calls == []

    def test_全部任务主线不可用时不调用执行器(self, make_tool, full_pool):
        handler, ex, _, _ = make_tool()
        result = handler(tasks=[_task(line="nope_a"), _task(line="nope_b")])
        assert result["failed"] == 2 and result["succeeded"] == 0
        assert result["status"] == "all_failed"
        assert ex.calls == []


# ════════════════════════════════════════════════════════════
#  5. govern 平面：默认不授予
# ════════════════════════════════════════════════════════════


class TestGovernPlaneGuard:
    def test_池子里确实有治理工具(self, full_pool):
        govern = _govern_tools()
        assert {"ext_install", "generate_tool", "connect_mcp"} <= govern
        assert govern <= set(full_pool), "候选池缺治理工具 ⇒ 下面的用例会变成空转"

    def test_默认不授予任何治理工具(self, make_tool, full_pool):
        govern = _govern_tools()
        handler, ex, _, _ = make_tool()
        result = handler(tasks=[
            _task(line=line) for line in
            ("engineering", "dev", "assistant", "recon", "knowledge")
        ])
        assert len(ex.calls[0]["delegations"]) == 5
        for ctx in ex.calls[0]["delegations"]:
            granted = _granted(ex.calls[0], ctx)
            assert granted, "授权集为空 ⇒ 用例失去意义"
            assert not granted & govern, (
                f"{ctx.metadata['line']} 拿到了治理工具: {sorted(granted & govern)}")
        assert result["govern_tools_granted"] == []

    def test_无主线时也不授予治理工具(self, make_tool, full_pool, monkeypatch):
        monkeypatch.setattr(get_line_registry(), "get_active", lambda: None)
        handler, ex, _, _ = make_tool()
        task = _task()
        task.pop("line")
        handler(tasks=[task])
        granted = _granted(ex.calls[0], ex.calls[0]["delegations"][0])
        assert not granted & _govern_tools()

    def test_显式允许治理的主线才放行且标注审批(self, make_tool, full_pool):
        """``digital_life`` 档案里 allow_govern: true —— 放行但必须进 needs_approval"""
        profile = get_line_registry().load("digital_life")
        assert profile.allow_govern is True, "口径自检：该主线必须显式开启治理"

        govern = _govern_tools()
        handler, ex, _, _ = make_tool()
        result = handler(tasks=[_task(line="digital_life")])

        granted = _granted(ex.calls[0], ex.calls[0]["delegations"][0])
        assert granted & govern, "显式 allow_govern=true 却没拿到治理工具"
        assert set(result["govern_tools_granted"]) == granted & govern
        item = result["results"][0]
        assert set(result["govern_tools_granted"]) <= set(item["needs_approval"])
        assert "allow_govern" in item["toolset_note"]

    def test_记忆读写不派给子代理(self, make_tool, full_pool):
        """§5.7 机制 3：主线给**主智能体**装配的记忆工具，派子代理时必须剔除

        ``engineering`` 主线装配集里含 ``search_memory`` / ``remember``（记忆读/写），
        而矩阵对 sub_agent 的 ``view.memory`` / ``memory.write`` 一律 ❌。
        若不剔除，子代理会拿到一份"授予了却永远调不动"的空头授权，
        且一旦真调用会被 ``DelegationExecutor`` 判整次委派失败。
        """
        from agent.subagent.toolset import SubAgentToolset

        expected = set(_expected_tools_for_line("engineering"))

        handler, ex, _, _ = make_tool()
        result = handler(tasks=[_task(line="engineering")])
        granted = _granted(ex.calls[0], ex.calls[0]["delegations"][0])

        hard = set(SubAgentToolset.hard_denied(sorted(_all_yaml_tool_names())))
        assert hard, "口径自检：硬禁集不应为空（否则本用例失去意义）"
        assert not granted & hard, f"子代理拿到了 §5.7 硬禁工具: {sorted(granted & hard)}"
        item = result["results"][0]
        assert "§5.7" in item["toolset_note"], "剔除硬禁工具后未如实上报"
        assert granted == expected


# ════════════════════════════════════════════════════════════
#  6. 部分失败：单个子任务失败不让整体失败
# ════════════════════════════════════════════════════════════


class TestPartialFailure:
    def test_单个子任务异常仍返回逐任务信封(self, make_tool, full_pool):
        handler, ex, _, _ = make_tool(executor=_FakeExecutor(fail_indexes={1}))
        result = handler(tasks=[_task(line="engineering"), _task(line="dev"),
                                _task(line="assistant")])

        # 工具返回的是**信封**（不是异常、不是整体拒绝）
        assert isinstance(result, dict) and "results" in result
        assert result["total"] == 3 and result["succeeded"] == 2 and result["failed"] == 1
        assert result["status"] == "partial" and result["partial"] is True
        # ok 取「至少有一个成功」（部分失败不让整次调用白跑，如实另报）
        assert result["ok"] is True
        assert "1/3 个子任务失败" in result["error_summary"]

        bad = result["results"][1]
        assert bad["status"] == "failed"
        assert bad["error_code"] == "E_DELEGATION_FAILED"
        assert "子代理执行异常" in bad["error"]
        assert bad["executed"] is True
        assert bad["outcome"]["sub_reason"] == "executor_exception"

        # 成功项照常带回结果与授权集
        for idx in (0, 2):
            assert result["results"][idx]["status"] == "ok"
            assert result["results"][idx]["tools_granted"]

    def test_全部失败时给顶层错误口径(self, make_tool, full_pool):
        """全部失败 ⇒ ok=False，并给出与 delegate 一致的顶层 error_code/error"""
        handler, ex, _, _ = make_tool(executor=_FakeExecutor(fail_indexes={0, 1}))
        result = handler(tasks=[_task(line="dev"), _task(line="assistant")])

        assert result["ok"] is False
        assert result["status"] == "all_failed" and result["partial"] is False
        assert result["error_code"] == "E_DELEGATION_FAILED"
        assert result["error"].startswith("全部子任务失败：")
        assert "2/2 个子任务失败" in result["error_summary"]

    def test_批量入口异常也收口为逐任务失败(self, make_tool, full_pool):
        handler, ex, _, _ = make_tool(executor=_FakeExecutor(raise_on_call=True))
        result = handler(tasks=[_task(line="dev"), _task(line="assistant")])

        assert isinstance(result, dict)
        assert result["failed"] == 2 and result["status"] == "all_failed"
        assert result["ok"] is False
        for item in result["results"]:
            assert item["error_code"] == fan_out_tools.E_FAN_OUT_BATCH_FAILED
            assert "批量执行异常" in item["error"]
            assert item["tools_granted"], "失败也必须如实带回该任务的授权集"

    def test_批量入口异常后分身同样被回收(self, make_tool, full_pool):
        """异常路径不得留下孤儿分身（回收在 finally 里）"""
        handler, _, manager, _ = make_tool(executor=_FakeExecutor(raise_on_call=True))
        handler(tasks=[_task(line="dev"), _task(line="assistant")])
        assert manager.count() == 0
        assert manager.get_stats()["total_destroyed"] == 2

    def test_执行器漏返结果时由管理器回填失败(self, make_tool, full_pool):
        """执行器少返一条 ⇒ delegate_many 回填失败结果（批次不留空洞、不误报成功）"""
        handler, ex, _, _ = make_tool(executor=_FakeExecutor(drop_indexes={1}))
        result = handler(tasks=[_task(line="dev"), _task(line="assistant")])
        assert result["failed"] == 1
        item = result["results"][1]
        assert item["error_code"] == "E_SUBAGENT_UNAVAILABLE"
        assert "未返回" in item["error"]
        assert item["executed"] is False
        assert item["tools_granted"], "该任务仍应如实带回它被授权过的工具集"

    def test_管理器漏返结果时_fan_out_自行兜底(self, make_tool, full_pool):
        """管理器契约是等长同序；万一它返回空列表，fan_out 仍不得留空洞"""
        class _SilentManager:
            def delegate(self, *a, **k):        # 前置校验只看它存在
                raise AssertionError("不应走单发入口")

            def delegate_many(self, *a, **k):
                return []

        handler, _, _, _ = make_tool(mgr=_SilentManager())
        result = handler(tasks=[_task(line="dev"), _task(line="assistant")])
        assert result["failed"] == 2
        for item in result["results"]:
            assert item["error_code"] == fan_out_tools.E_FAN_OUT_NO_RESULT
            assert item["tools_granted"] == []

    def test_handler_内异常收口不外抛(self, make_tool, full_pool, monkeypatch):
        handler, _, _, _ = make_tool()
        monkeypatch.setattr(fan_out_tools, "_elements_of",
                            lambda task: (_ for _ in ()).throw(ValueError("bad")))
        result = handler(tasks=[_task()])
        assert result["ok"] is False
        assert "并行委派执行异常" in result["error"]


# ════════════════════════════════════════════════════════════
#  7. 真实执行器整链（不经 mock：证明逐任务授权在真执行器里生效）
# ════════════════════════════════════════════════════════════


class _StubLlm:
    """最小 LLM 桩：每轮返回一个合法的最终 JSON 对象（不触网、不起子进程）"""

    model = "stub-llm"

    def chat(self, messages, system_prompt=None):  # noqa: ARG002
        return json.dumps({
            "status": "done",
            "summary": "已完成（桩）",
            "artifacts": [],
            "self_eval": {"verdict": "pass", "score": 1.0, "summary": "桩", "issues": []},
            "tool_calls": [],
        }, ensure_ascii=False)


class _CapturingExecutor:
    """真执行器的薄包装：保留 ``execute_many`` 的全部语义，另存每次的 ExecutionOutcome"""

    def __init__(self, llm):
        from agent.subagent.executor import DelegationExecutor

        self._inner = DelegationExecutor(llm=llm)
        self.outcomes: list = []
        self.max_concurrency_seen = None

    def execute_many(self, delegations, *, max_concurrency=None, tools=(),
                     authorized_capabilities=None, tools_for=None, authorized_for=None,
                     credentials_for=None, parent_trace=None):
        """签名与真执行器逐一对应（不透传 **kwargs：与实参同名的键会撞成 TypeError）"""
        self.max_concurrency_seen = max_concurrency
        out = self._inner.execute_many(
            delegations, max_concurrency=max_concurrency, tools=tools,
            authorized_capabilities=authorized_capabilities, tools_for=tools_for,
            authorized_for=authorized_for, credentials_for=credentials_for,
            parent_trace=parent_trace)
        self.outcomes.extend(out)
        return out


class TestRealExecutorIntegration:
    """走**真** ``DelegationExecutor`` + **真** ``SubagentLifecycleManager``

    这条用例专门验证"逐任务工具集"这件事在真实链路上成立：``execute_many`` 的
    ``tools`` / ``authorized_capabilities`` 是整批共用的单值，逐任务靠
    ``tools_for`` / ``authorized_for`` 工厂生效（``executor._resolve_subset`` 之外的新路径）。
    断言取自执行器自己产出的 ``outcome.toolset["tools"]``（子代理**真正可见**的清单）。

    【超时口径】这两例会真正物化 task_file、走凭据/隔离/回收三件套与成本记账（本地约 3s）；
    CI 上与其余 11 个矩阵 job 共享 runner 时会明显变慢 —— 2026-09-17 实测 Shard 1 因
    ``--timeout=60`` 判超时（本地 2.7s 通过，属 runner 争用而非死锁）。故显式放宽到 180s
    （与 ``test_subagent_executor.py`` 的 e2e 级用例同口径），**不放宽任何断言**。
    """

    pytestmark = pytest.mark.timeout(180)

    def _handler(self, monkeypatch, holder):
        def _factory(_llm):
            holder["executor"] = _CapturingExecutor(_llm)
            return holder["executor"]

        monkeypatch.setattr(fan_out_tools, "_build_executor", _factory)
        dl = MagicMock()
        dl._subagent_mgr = SubagentLifecycleManager()
        dl._llm = _StubLlm()
        fan_out_tools.register_all(dl)
        return _tools._registry["fan_out"]["handler"], dl._subagent_mgr

    def test_逐任务可见集等于其主线装配集(self, monkeypatch, full_pool):
        holder: dict = {}
        handler, manager = self._handler(monkeypatch, holder)

        lines = ("assistant", "dev")
        result = handler(tasks=[_task(line=line) for line in lines], max_concurrency=2)

        assert result["succeeded"] == 2, result
        executor = holder["executor"]
        assert executor.max_concurrency_seen == 2
        assert len(executor.outcomes) == 2
        assert manager.count() == 0, "真实链路也必须回收分身"

        for outcome, line in zip(executor.outcomes, lines):
            expected = set(_expected_tools_for_line(line))
            assert outcome.ok is True, outcome.error
            assert set(outcome.toolset["tools"]) == expected, (
                f"{line}：子代理真正可见的工具集与装配结果不符")
            assert set(outcome.toolset["authorized_subset"]) == expected
            # 逐任务结果里回报的授权集与执行器口径一致
        for item, line in zip(result["results"], lines):
            assert set(item["tools_granted"]) == set(_expected_tools_for_line(line))
            assert item["status"] == "ok"

    def test_可见集不含治理工具_真执行器同口径(self, monkeypatch, full_pool):
        holder: dict = {}
        handler, manager = self._handler(monkeypatch, holder)

        result = handler(tasks=[_task(line="engineering"), _task(line="assistant")])
        assert result["succeeded"] == 2, result
        govern = _govern_tools()
        for item in result["results"]:
            assert not set(item["tools_granted"]) & govern
        for outcome in holder["executor"].outcomes:
            assert not set(outcome.toolset["tools"]) & govern
        assert manager.count() == 0


class TestSharedConcurrencyBarrier:
    """并发上限是**进程级**的：跨多次 fan_out 调用共用一个屏障

    为什么必须锁住：每次调用都新建执行器，若屏障随执行器新建，§4.2 的"并发上限 N"
    就退化成"每次调用 N"（全局 = 调用数 × N，无上界）。本类钉住"共享的是同一个屏障对象"。
    """

    def test_屏障是进程级单例且上限等于_DEFAULT_MAX_CONCURRENCY(self):
        b1 = fan_out_tools._shared_barrier()
        b2 = fan_out_tools._shared_barrier()
        assert b1 is b2, "每次调用都新建屏障 ⇒ 并发上限只是每调用的，不是全局的"
        assert b1.max_concurrency == DEFAULT_MAX_CONCURRENCY
        assert b1.name == "fan_out"

    def test_真构建路径把共享屏障注入执行器(self):
        """`_build_executor` 走真 `build_executor` ⇒ executor.barrier 就是共享屏障"""
        executor = fan_out_tools._build_executor(_StubLlm())
        assert executor.barrier is fan_out_tools._shared_barrier()

    def test_真实批量确实经过共享屏障计数(self, full_pool):
        """跑一批（真执行器 + 桩 LLM），共享屏障的 admitted 计数必须增长

        本用例**不**替换 `_build_executor`：必须走真 `build_executor`，才能证明
        生产路径确实注入了共享屏障（替换成假执行器会绕过屏障，等于没测到）。
        """
        barrier = fan_out_tools._shared_barrier()
        before = barrier.stats().total_admitted
        dl = MagicMock()
        dl._subagent_mgr = SubagentLifecycleManager()
        dl._llm = _StubLlm()
        fan_out_tools.register_all(dl)
        handler = _tools._registry["fan_out"]["handler"]

        result = handler(tasks=[_task(line="dev"), _task(line="assistant")],
                         max_concurrency=2)
        assert result["succeeded"] == 2, result
        after = barrier.stats()
        assert after.total_admitted - before == 2, (
            "真实批量没有经过共享屏障 ⇒ 跨调用并发上限不成立")
        assert after.in_flight == 0, "批次结束后必须归还全部槽位"


# ════════════════════════════════════════════════════════════
#  8. 并发上限 clamp
# ════════════════════════════════════════════════════════════

class TestConcurrencyClamp:
    @pytest.mark.parametrize("requested,expected", [
        (None, DEFAULT_MAX_CONCURRENCY),
        (1, 1),
        (2, 2),
        (DEFAULT_MAX_CONCURRENCY, DEFAULT_MAX_CONCURRENCY),
        (DEFAULT_MAX_CONCURRENCY + 1, DEFAULT_MAX_CONCURRENCY),
        (999, DEFAULT_MAX_CONCURRENCY),
        (0, 1),
        (-3, 1),
        (True, DEFAULT_MAX_CONCURRENCY),
        (2.5, DEFAULT_MAX_CONCURRENCY),
        ("3", DEFAULT_MAX_CONCURRENCY),
    ])
    def test_越界被_clamp_到合法区间(self, requested, expected):
        value, _note = fan_out_tools._clamp_concurrency(requested)
        assert value == expected

    def test_越界会在返回里说明(self, make_tool, full_pool):
        handler, ex, _, _ = make_tool()
        result = handler(tasks=[_task()], max_concurrency=64)
        assert ex.calls[0]["max_concurrency"] == DEFAULT_MAX_CONCURRENCY
        assert result["max_concurrency"] == DEFAULT_MAX_CONCURRENCY
        assert any("越界" in note for note in result["notes"])

    def test_区间内不写额外说明(self, make_tool, full_pool):
        handler, ex, _, _ = make_tool()
        result = handler(tasks=[_task()], max_concurrency=2)
        assert ex.calls[0]["max_concurrency"] == 2
        assert not any("越界" in note for note in result["notes"])


# ════════════════════════════════════════════════════════════
#  9. 前置能力与成本防线
# ════════════════════════════════════════════════════════════


class TestPreconditions:
    def test_管理器不可用返回清晰错误(self, full_pool, monkeypatch):
        ex = _FakeExecutor()
        dl = MagicMock()
        dl._subagent_mgr = None
        dl._llm = MagicMock()
        monkeypatch.setattr(fan_out_tools, "_build_executor", lambda _llm: ex)
        fan_out_tools.register_all(dl)
        handler = _tools._registry["fan_out"]["handler"]

        result = handler(tasks=[_task()])
        assert result["ok"] is False
        assert "子代理生命周期管理器不可用" in result["error"]
        assert "dl._subagent_mgr" in result["error"]
        assert ex.calls == []

    def test_管理器缺_delegate_返回清晰错误(self, make_tool, full_pool):
        class _NoDelegate:
            pass

        handler, ex, _, _ = make_tool(mgr=_NoDelegate())
        result = handler(tasks=[_task()])
        assert result["ok"] is False
        assert "未提供 delegate()" in result["error"]
        assert ex.calls == []

    def test_无_LLM_无_CLI_返回清晰错误(self, make_tool, full_pool):
        handler, ex, _, _ = make_tool(llm=None)
        result = handler(tasks=[_task()])
        assert result["ok"] is False
        assert result["error_code"] == subagent_tools.E_DELEGATION_NO_CHANNEL
        assert "未配置执行通道" in result["error"]
        assert "CP_SUBAGENT_AGENT_CLI" in result["error"]
        assert ex.calls == [], "不跑注定失败的空执行"

    def test_仅有外部_CLI_也可派发(self, make_tool, full_pool, monkeypatch):
        monkeypatch.setenv("CP_SUBAGENT_AGENT_CLI", "my-agent-cli")
        handler, ex, _, _ = make_tool(llm=None)
        result = handler(tasks=[_task(line="dev")])
        assert result["succeeded"] == 1
        assert len(ex.calls) == 1

    def test_任务数超过单次上限被拒(self, make_tool, full_pool, monkeypatch):
        monkeypatch.setenv("CP_FAN_OUT_MAX_TASKS", "2")
        handler, ex, _, _ = make_tool()
        result = handler(tasks=[_task(line="dev") for _ in range(3)])
        assert result["ok"] is False
        assert result["error_code"] == fan_out_tools.E_FAN_OUT_TOO_MANY_TASKS
        assert "超过单次上限 2" in result["error"]
        assert ex.calls == []

    def test_上限可经环境变量放宽(self, make_tool, full_pool, monkeypatch):
        monkeypatch.setenv("CP_FAN_OUT_MAX_TASKS", "3")
        handler, ex, _, _ = make_tool()
        result = handler(tasks=[_task(line="dev") for _ in range(3)])
        assert result["succeeded"] == 3

    @pytest.mark.parametrize("raw", ["abc", "0", "-2", ""])
    def test_非法上限值退回默认(self, monkeypatch, raw):
        monkeypatch.setenv("CP_FAN_OUT_MAX_TASKS", raw)
        if raw == "":
            assert fan_out_tools._max_tasks() == fan_out_tools._DEFAULT_MAX_TASKS
        else:
            assert fan_out_tools._max_tasks() == fan_out_tools._DEFAULT_MAX_TASKS
