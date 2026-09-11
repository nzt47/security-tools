"""TASK-S3-01 §零 S2 遗留收口验证（L1–L4）

本文件是**四项遗留的收口证据**（验收清单最后四条）：
- 【L1】工具链落账 `capability_id` 与 `data/descriptors.json` 台账可 join；
- 【L2】orchestrator wire 规划成功分支产出任务级 Trace 与 `task.closed`，
  且与 LLM 段**无重复开闭**；
- 【L3】borrowed 能力 `trace_policy` 为真实 ledger 引用（非 `(S2-pending)` 占位）；
- 【L4】stage 入轨（首次入轨计划 + 执行 + 报告字段齐备）。
"""

from __future__ import annotations

import ast
import json
import pathlib
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.audit import facade as facade_mod
from agent.audit.chain import AuditChain
from agent.descriptors import bridge as bridge_mod
from agent.descriptors.bridge import (
    descriptor_from_mcp_tool,
    ledger_trace_policy,
    resolve_capability_id,
    skill_to_descriptor,
)
from agent.guardrails.input_guard import GuardAction, GuardResult
from agent.guardrails.output_guard import OutputResult
from agent.observability.trace_v2 import TraceContext, TraceFacade
from agent.orchestrator.orchestrator import Orchestrator

pytestmark = pytest.mark.usefixtures("isolated_audit", "events_dir")


# ════════════════════════════════════════════════════════════
#  fixtures
# ════════════════════════════════════════════════════════════


@pytest.fixture
def chain(tmp_path):
    c = AuditChain(str(tmp_path / "audit_chain.db"),
                   roots_path=str(tmp_path / "roots.jsonl"),
                   signing_key_path=str(tmp_path / "k.pem"), auto_seal=False)
    yield c
    c.close(timeout=2.0)


@pytest.fixture
def isolated_audit(chain):
    previous = facade_mod.audit.bind(chain)
    old_enabled = facade_mod.audit.enabled
    facade_mod.audit.enabled = True
    facade_mod.audit.reset_counters()
    yield facade_mod.audit
    facade_mod.audit.bind(previous)
    facade_mod.audit.enabled = old_enabled


@pytest.fixture
def events_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CP_EVENTS_DIR", str(tmp_path / "events"))
    import agent.observability.events as events_mod
    events_mod.reset_event_stores()
    yield str(tmp_path / "events")
    events_mod.reset_event_stores()


@pytest.fixture(autouse=True)
def clean_trace_context():
    from agent.observability.trace_v2 import _trace_context_var
    _trace_context_var.set(None)
    yield
    _trace_context_var.set(None)


@pytest.fixture
def facade(tmp_path):
    f = TraceFacade(str(tmp_path / "trace.db"))
    TraceFacade._instance = f
    yield f
    f.flush()
    f._store.stop(timeout=2.0)
    TraceFacade._instance = None


def _task_closed_events(directory: str):
    import agent.observability.events as events_mod
    return [e for e in events_mod.iter_events(directory=directory)
            if e.type == "task.closed"]


# ════════════════════════════════════════════════════════════
#  【L1】工具名 → capability_id 运行时改写
# ════════════════════════════════════════════════════════════


class TestL1CapabilityRewrite:
    def test_canonical_form_is_deterministic(self):
        assert bridge_mod.canonical_capability_id("read_file") == \
            "cp.builtin.read_file"
        assert bridge_mod.canonical_capability_id("cp.x.y") == "cp.x.y"
        assert bridge_mod.canonical_capability_id("") == ""

    def test_mcp_source_id_used(self):
        assert bridge_mod.canonical_capability_id(
            "remote read", source_id="File System") == \
            "cp.file-system.remote-read"

    def test_resolve_matches_ledger_key(self, tmp_path):
        """改写结果必须与台账登记键**逐字相同**（join 的前提）"""
        from tests.unit.digestion_util import descriptor_registry
        reg = descriptor_registry(tmp_path, ["read_file"])
        out = resolve_capability_id("read_file", registry=reg)
        assert out["capability_id"] == "cp.builtin.read_file"
        assert out["joined"] is True
        assert out["rewritten"] is True
        assert out["resolved_by"] == "registry_id"
        assert reg.get(out["capability_id"]) is not None

    def test_unregistered_tool_gets_canonical_key_not_raw_name(self, tmp_path):
        from tests.unit.digestion_util import descriptor_registry
        reg = descriptor_registry(tmp_path, ["read_file"])
        out = resolve_capability_id("mystery_tool", registry=reg)
        assert out["capability_id"] == "cp.builtin.mystery_tool"
        assert out["joined"] is False        # 诚实：不伪造 provenance
        assert out["resolved_by"] == "derived"

    def test_already_canonical_is_passthrough(self, tmp_path):
        from tests.unit.digestion_util import descriptor_registry
        reg = descriptor_registry(tmp_path, ["read_file"])
        out = resolve_capability_id("cp.builtin.read_file", registry=reg)
        assert out["rewritten"] is False
        assert out["resolved_by"] == "already_canonical"

    def test_empty_name(self):
        out = resolve_capability_id("")
        assert out["capability_id"] == "" and out["resolved_by"] == "empty"

    def test_registry_name_fallback(self, tmp_path):
        """登记 id 与工具名不同段时，靠 `capability.name` 兜底命中"""
        from tests.unit.digestion_util import descriptor_registry
        reg = descriptor_registry(tmp_path, [])
        desc = bridge_mod.descriptor_from_builtin_tool(
            "read_file", "读文件", source_id="tools")
        reg.register(desc)
        out = resolve_capability_id("read_file", registry=reg)
        assert out["capability_id"] == "cp.tools.read_file"
        assert out["resolved_by"] in ("registry_id", "registry_name")

    def test_registry_unavailable_falls_back_to_raw(self):
        class _Boom:
            def resolve_alias(self, _n):
                raise RuntimeError("down")

            def get(self, _c):
                raise RuntimeError("down")

        out = resolve_capability_id("read_file", registry=_Boom())
        assert out["capability_id"] == "read_file"
        assert out["resolved_by"] == "advisory_fallback"

    def test_name_index_cache_invalidated_by_count(self, tmp_path):
        from tests.unit.digestion_util import descriptor_registry
        reg = descriptor_registry(tmp_path, [])
        assert resolve_capability_id("alpha", registry=reg)["joined"] is False
        reg.register(bridge_mod.descriptor_from_builtin_tool("alpha", "A",
                                                             source_id="tools"))
        out = resolve_capability_id("alpha", registry=reg)
        assert out["joined"] is True


class TestL1ToolChainLedgerPoint:
    def test_tool_chain_writes_canonical_key(self, facade):
        """`ToolCallingService._execute_safe` 的统一轨落账键已是 capability_id"""
        from agent.tool_calling import ToolCallingService

        facade.start(task_id="task-l1", workspace_id="ws_l1")
        svc = ToolCallingService.__new__(ToolCallingService)
        svc._execute_safe_core = MagicMock(return_value={"ok": True})
        svc._execute_safe("read_file", {"path": "x.py"})
        facade.finish()
        assert facade.flush()
        rows = facade.list_by_capability("cp.builtin.read_file")
        assert len(rows) == 1
        assert rows[0].capability_id == "cp.builtin.read_file"

    def test_legacy_raw_name_no_longer_written(self, facade):
        from agent.tool_calling import ToolCallingService

        facade.start(task_id="task-l1b", workspace_id="ws_l1")
        svc = ToolCallingService.__new__(ToolCallingService)
        svc._execute_safe_core = MagicMock(return_value={"ok": True})
        svc._execute_safe("shell_execute", {"cmd": "pytest"})
        facade.finish()
        assert facade.flush()
        assert facade.list_by_capability("shell_execute") == []

    def test_resolver_helper_is_best_effort(self):
        from agent.tool_calling import resolve_tool_capability_id

        with patch("agent.descriptors.bridge.resolve_capability_id",
                   side_effect=RuntimeError("boom")):
            assert resolve_tool_capability_id("read_file") == "read_file"

    def test_helper_returns_capability_id(self):
        from agent.tool_calling import resolve_tool_capability_id
        assert resolve_tool_capability_id("read_file").startswith("cp.")

    def test_trace_joins_registered_ledger(self, facade, tmp_path):
        """【L1】落账键 ↔ descriptor 台账可 join（**隔离台账**，不依赖运行时数据）

        为何不用默认 `DescriptorRegistry()`：默认台账 `data/descriptors.json` 是
        **运行时产物且已 gitignore** ⇒ CI 冷启动时不存在，registry 为空，
        join 必然失败。本用例改为在隔离台账上按**真实桥接路径**登记后断言 join，
        既证明 L1 的契约（改写键 == 登记键），又不依赖运行时状态。
        （该缺陷是本地绿、CI 红的典型 —— 交付前用"移除运行时台账"复现确认。）
        """
        from agent.descriptors.bridge import register_bridge_view
        from agent.descriptors.registry import DescriptorRegistry
        from agent.tool_calling import ToolCallingService

        ledger = DescriptorRegistry(path=str(tmp_path / "l1_ledger.json"),
                                    autosave=False)
        register_bridge_view(ledger, "builtin",
                             [{"name": "read_file", "description": "读文件"}])
        facade.start(task_id="task-l1c", workspace_id="ws_l1")
        svc = ToolCallingService.__new__(ToolCallingService)
        svc._execute_safe_core = MagicMock(return_value={"ok": True})
        svc._execute_safe("read_file", {"path": "x.py"})
        facade.finish()
        assert facade.flush()

        [row] = facade.list_by_capability("cp.builtin.read_file")
        assert row.capability_id == "cp.builtin.read_file"   # 改写后的落账键
        assert ledger.get(row.capability_id) is not None     # 同键可 join

    def test_trace_joins_default_ledger_when_present(self, facade):
        """【L1】运行时台账**存在时**同样可 join（CI 冷启动则跳过，不算失败）"""
        import pathlib

        if not pathlib.Path("data/descriptors.json").exists():
            pytest.skip("运行时台账不存在（CI 冷启动）—— 契约由上一用例覆盖")
        from agent.descriptors.registry import DescriptorRegistry
        from agent.tool_calling import ToolCallingService

        ledger = DescriptorRegistry()
        facade.start(task_id="task-l1d", workspace_id="ws_l1")
        svc = ToolCallingService.__new__(ToolCallingService)
        svc._execute_safe_core = MagicMock(return_value={"ok": True})
        svc._execute_safe("read_file", {"path": "x.py"})
        facade.finish()
        assert facade.flush()
        [row] = facade.list_by_capability("cp.builtin.read_file")
        assert ledger.get(row.capability_id) is not None


# ════════════════════════════════════════════════════════════
#  【L2】orchestrator wire 规划成功分支的任务级 Trace
# ════════════════════════════════════════════════════════════

_COMPLEX_INPUT = "帮我构建一个分布式系统架构"
_SIMPLE_INPUT = "帮我完成一个多步骤任务"


def _wire_orch(**overrides):
    """直落 wire→LLM 段的 orchestrator（复用 `test_planning_wire` 的桩模式）"""
    behavior = MagicMock()
    behavior.can_execute.return_value = (True, "")
    behavior.profile.enable_reflection = False

    orch = Orchestrator.__new__(Orchestrator)
    defaults = {
        "_running": True,
        "_interaction_count": 1,
        "_interaction_lock": threading.Lock(),
        "_last_context_warning": None,
        "_last_was_template": False,
        "_session_id": "test-s3-01-l2",
        "_guardrails_input_guard": MagicMock(
            check=lambda x: GuardResult(GuardAction.ALLOW)),
        "_guardrails_output_guard": MagicMock(
            check=lambda x: OutputResult(filtered=x)),
        "_workflow_engine": MagicMock(try_match=lambda x: None),
        "_load_workflow_learning_layer_config":
            lambda: {"enabled": False, "min_score": 0.25},
        "_memory": MagicMock(),
        "_behavior": behavior,
        "_build_body_status": MagicMock(return_value="Body status"),
        "_build_reject_response": MagicMock(return_value="Request rejected"),
        "_call_llm": MagicMock(return_value="LLM 直答响应"),
        "_call_llm_v2": MagicMock(return_value="LLM 直答响应"),
        "_set_thinking_mode": MagicMock(),
        "_check_context_usage": MagicMock(return_value=None),
        "_v2_lifetrace": False,
        "_v2_distillation": False,
        "_v2_persona": False,
        "_vector_memory": None,
        "_trace_recorder": None,
        "_error_reporter": None,
        "_current_mode": MagicMock(value="test_mode"),
        "_persona_injector": None,
        "_persona_extractor": None,
        "_is_skill_enabled": lambda x: False,
        "_planner": None,
        "_planning_enabled": False,
        "_needs_planning": lambda x: False,
        "_config": {"planning": {"timeout_seconds": 30}},
        "_update_dst_after_route": MagicMock(),
        "_semantic_layer_match": MagicMock(return_value=None),
        "_should_reject": MagicMock(return_value=(False, "")),
        "_load_reject_config": MagicMock(return_value={"threshold": 0.3}),
        "check_health": MagicMock(return_value=[]),
        "_learn_workflow_from_interaction": MagicMock(),
        "_load_planning_wire_config": lambda: {
            "enabled": False, "min_complexity": "COMPLEX",
            "timeout_seconds": 30},
    }
    for key, value in defaults.items():
        setattr(orch, key, value)
    for key, value in overrides.items():
        setattr(orch, key, value)
    return orch


def _wire_enabled_cfg(min_complexity: str = "COMPLEX",
                      timeout_seconds: float = 30):
    return lambda: {"enabled": True, "min_complexity": min_complexity,
                    "timeout_seconds": timeout_seconds}


@patch("agent.orchestrator.orchestrator._MONITORING_AVAILABLE", False)
class TestL2WirePathTaskTrace:
    def test_wire_success_emits_task_trace_and_task_closed(self, facade,
                                                          events_dir):
        """【L2】wire 规划成功 ⇒ 任务级 Trace + `task.closed`（进 ACR 分母）"""
        from planning.core import ChatResult

        planner = AsyncMock()
        planner.chat.return_value = ChatResult(response="规划引擎响应")
        orch = _wire_orch(_planner=planner,
                          _load_planning_wire_config=_wire_enabled_cfg())
        result = orch.process(_COMPLEX_INPUT)
        assert result["data"] == "规划引擎响应"
        assert facade.flush()

        task_rows = [t for t in facade.query() if not t.capability_id]
        assert len(task_rows) == 1, "wire 路径应产出唯一任务级 Trace"
        assert task_rows[0].response.status == "success"
        assert task_rows[0].tenancy.workspace_id

        closed = _task_closed_events(events_dir)
        assert len(closed) == 1, "wire 路径应产出唯一 task.closed"
        assert closed[0].payload["workspace_id"]

    def test_wire_success_no_context_leak(self, facade, events_dir):
        from planning.core import ChatResult

        planner = AsyncMock()
        planner.chat.return_value = ChatResult(response="规划响应")
        orch = _wire_orch(_planner=planner,
                          _load_planning_wire_config=_wire_enabled_cfg())
        orch.process(_COMPLEX_INPUT)
        assert TraceContext.current() is None

    def test_llm_path_still_single_open_close(self, facade, events_dir):
        """LLM 直答路径：仍然**恰好一次**开闭（上提起点未引入重复）"""
        planner = AsyncMock()
        orch = _wire_orch(_planner=planner)   # wire 关闭
        result = orch.process(_COMPLEX_INPUT)
        assert result["data"] == "LLM 直答响应"
        assert facade.flush()
        task_rows = [t for t in facade.query() if not t.capability_id]
        assert len(task_rows) == 1
        assert len(_task_closed_events(events_dir)) == 1

    def test_wire_fallback_path_single_open_close(self, facade, events_dir):
        """wire 规划失败回退 LLM：仍恰好一次开闭（二值分流互斥）"""
        planner = AsyncMock()
        planner.chat.side_effect = RuntimeError("planning crash")
        orch = _wire_orch(_planner=planner,
                          _load_planning_wire_config=_wire_enabled_cfg())
        result = orch.process(_COMPLEX_INPUT)
        assert result["data"] == "LLM 直答响应"
        assert facade.flush()
        task_rows = [t for t in facade.query() if not t.capability_id]
        assert len(task_rows) == 1
        assert len(_task_closed_events(events_dir)) == 1

    def test_simple_task_single_open_close(self, facade, events_dir):
        planner = AsyncMock()
        orch = _wire_orch(_planner=planner,
                          _load_planning_wire_config=_wire_enabled_cfg())
        orch.process(_SIMPLE_INPUT)
        planner.chat.assert_not_called()
        assert facade.flush()
        assert len([t for t in facade.query() if not t.capability_id]) == 1
        assert len(_task_closed_events(events_dir)) == 1

    def test_llm_error_path_still_closes_once(self, facade, events_dir):
        planner = AsyncMock()
        orch = _wire_orch(_planner=planner)
        orch._call_llm = MagicMock(side_effect=RuntimeError("llm boom"))
        with patch("agent.orchestrator.orchestrator._MONITORING_AVAILABLE", False):
            result = orch.process(_COMPLEX_INPUT)
        assert result["success"] is False
        assert facade.flush()
        task_rows = [t for t in facade.query() if not t.capability_id]
        assert len(task_rows) == 1
        assert task_rows[0].response.status == "error"
        assert len(_task_closed_events(events_dir)) == 1
        assert TraceContext.current() is None

    def test_wire_task_included_in_acr_denominator(self, facade, events_dir):
        """`task.closed` 即 ACR 分母来源 ⇒ wire 路径任务自此计入"""
        from planning.core import ChatResult

        planner = AsyncMock()
        planner.chat.return_value = ChatResult(response="规划响应")
        orch = _wire_orch(_planner=planner,
                          _load_planning_wire_config=_wire_enabled_cfg())
        orch.process(_COMPLEX_INPUT)
        [closed] = _task_closed_events(events_dir)
        assert closed.payload.get("status") in ("closed", "failed")
        assert closed.payload.get("task_id")
        assert closed.payload.get("workspace_id")
        assert closed.correlation_id

    def test_branch_structural_exclusivity(self):
        """结构断言：**单一**起点 + 两处收口分属 if/else 互斥分支"""
        from agent.orchestrator import orchestrator as orch_mod

        source = pathlib.Path(orch_mod.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        begins, ends = [], []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "_begin_unified_task_trace":
                    begins.append(node.lineno)
                elif node.func.id == "_end_unified_task_trace":
                    ends.append(node.lineno)
        assert len(begins) == 1, f"应只有唯一 Trace 起点，实际 {begins}"
        assert len(ends) == 2, f"应为两处互斥收口，实际 {ends}"
        assert begins[0] < min(ends), "起点必须上提至两处收口之前"

        # 定位 `if not _wire_planning_used:` 节点，断言两处收口**分属 body / orelse**
        target = None
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            test = node.test
            if (isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)
                    and isinstance(test.operand, ast.Name)
                    and test.operand.id == "_wire_planning_used"):
                target = node
                break
        assert target is not None, "未找到 wire/LLM 分流分支"

        def _calls_in(nodes):
            found = []
            for stmt in nodes:
                for sub in ast.walk(stmt):
                    if (isinstance(sub, ast.Call)
                            and isinstance(sub.func, ast.Name)
                            and sub.func.id == "_end_unified_task_trace"):
                        found.append(sub.lineno)
            return found

        in_body = _calls_in(target.body)
        in_else = _calls_in(target.orelse)
        assert len(in_body) == 1, f"LLM 段应有且仅有一个收口，实际 {in_body}"
        assert len(in_else) == 1, f"wire 段应有且仅有一个收口，实际 {in_else}"
        assert target.lineno > begins[0], "起点必须在分流之前（覆盖两条路径）"


# ════════════════════════════════════════════════════════════
#  【L3】trace_policy 真实 ledger 引用
# ════════════════════════════════════════════════════════════


class TestL3RealLedgerTracePolicy:
    def test_mcp_default_policy_is_real_reference(self):
        desc = descriptor_from_mcp_tool(
            {"name": "remote_read", "description": "x"}, server="filesystem")
        policy = desc.evolution.trace_policy
        assert "pending" not in policy.lower()
        assert "ledger=unified_traces@agent/data/tool_trace.db" in policy
        assert "#capability_id=cp.filesystem.remote_read" in policy
        assert "#read=UnifiedTraceStore.list_by_capability" in policy

    def test_skill_import_default_policy_is_real_reference(self):
        desc, _ = skill_to_descriptor({
            "id": "code-observability", "name": "n", "category": "community",
            "status": "published"})
        policy = desc.evolution.trace_policy
        assert "pending" not in policy.lower()
        assert "#capability_id=cp.skill.code-observability" in policy
        assert "#read=UnifiedTraceStore.list_by_capability" in policy

    def test_caller_supplied_policy_wins(self):
        desc = descriptor_from_mcp_tool(
            {"name": "t"}, server="s", trace_policy="trace:custom")
        assert desc.evolution.trace_policy == "trace:custom"

    def test_borrowed_validator_still_requires_policy(self):
        from agent.descriptors.validator import assert_valid
        from agent.descriptors.models import DescriptorValidationError
        desc = descriptor_from_mcp_tool({"name": "t"}, server="s",
                                        trace_policy=" ")
        with pytest.raises(DescriptorValidationError):
            assert_valid(desc)

    def test_no_placeholder_literals_left_in_bridge(self):
        """`bridge.py` 中**不再有**把占位串当值使用的代码（文档引用除外）

        用 AST 取全部字符串常量并剔除 docstring —— 文档里如实记载"S2 期曾用
        `…(S2-pending)` 占位"是必要的历史披露，不属于"仍在用占位串"。
        """
        source = pathlib.Path(bridge_mod.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)

        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                first = node.body[0] if node.body else None
                if (isinstance(first, ast.Expr)
                        and isinstance(first.value, ast.Constant)
                        and isinstance(first.value.value, str)):
                    docstrings.add(id(first.value))

        offenders = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in docstrings):
                if "S2-pending" in node.value or "S2-ledger-pending" in node.value:
                    offenders.append(node.value)
        assert offenders == [], f"仍有占位串字面量: {offenders}"

    def test_ledger_trace_policy_shape(self):
        policy = ledger_trace_policy(source="srv", capability_id="cp.a.b",
                                     route="call-side")
        assert policy == (
            "trace:srv:call-side:ledger=unified_traces@agent/data/tool_trace.db"
            "#capability_id=cp.a.b#read=UnifiedTraceStore.list_by_capability")

    def test_ledger_trace_policy_without_route(self):
        policy = ledger_trace_policy(source="srv", capability_id="cp.a.b")
        assert policy.startswith("trace:srv:ledger=")
        assert ":call-side" not in policy

    def test_ledger_trace_policy_sanitizes_source(self):
        policy = ledger_trace_policy(source="My Server!", capability_id="c")
        assert policy.startswith("trace:my-server:")

    def test_placeholder_detector(self):
        from agent.digestion.stage import is_placeholder_trace_policy
        assert is_placeholder_trace_policy("trace:x:call-side(S2-ledger-pending)")
        assert is_placeholder_trace_policy("trace:skill:a:import-ledger(S2-pending)")
        assert not is_placeholder_trace_policy(
            ledger_trace_policy(source="s", capability_id="c"))
        assert not is_placeholder_trace_policy("")

    def test_real_ledger_policies_have_no_placeholder(self):
        """【L3】真实 `data/descriptors.json`：borrowed 资产无占位串残留"""
        from agent.descriptors.registry import DescriptorRegistry

        ledger = pathlib.Path("data/descriptors.json")
        if not ledger.exists():
            pytest.skip("运行时台账不存在（CI 冷启动）")
        reg = DescriptorRegistry()
        for desc in reg.list():
            if desc.evolution.stage and desc.evolution.stage.value == "borrowed":
                policy = desc.evolution.trace_policy or ""
                assert "pending" not in policy.lower(), \
                    f"{desc.capability_id} 仍为占位串: {policy}"
                assert "ledger=unified_traces" in policy, \
                    f"{desc.capability_id} 非真实台账引用: {policy}"

    def test_policy_refresh_uses_patch_audit_not_stage(self, tmp_path, chain):
        """占位串刷新走 `descriptor.patch`（字段级），不与 stage 留痕混淆"""
        from agent.digestion.stage import refresh_trace_policies
        from tests.unit.digestion_util import descriptor_registry

        reg = descriptor_registry(tmp_path, [])
        desc = bridge_mod.descriptor_from_builtin_tool("read_file", "读文件")
        desc.evolution.stage = bridge_mod.EvolutionStage.BORROWED
        desc.evolution.trace_policy = "trace:builtin:call-side(S2-ledger-pending)"
        reg.register(desc)
        assert reg.get("cp.builtin.read_file").evolution.stage.value == "borrowed"

        result = refresh_trace_policies(reg, execute=True)
        assert result["placeholder"] == 1
        assert len(result["refreshed"]) == 1
        policy = reg.get("cp.builtin.read_file").evolution.trace_policy
        assert "pending" not in policy and "ledger=unified_traces" in policy
        chain.flush()
        actions = [e.action for e in chain.entries()]
        assert "descriptor.patch" in actions
        assert "descriptor.stage" not in actions

    def test_policy_refresh_dry_run(self, tmp_path):
        from agent.digestion.stage import refresh_trace_policies
        from tests.unit.digestion_util import descriptor_registry

        reg = descriptor_registry(tmp_path, [])
        desc = bridge_mod.descriptor_from_builtin_tool("read_file", "读文件")
        desc.evolution.trace_policy = "trace:x(S2-pending)"
        reg.register(desc)
        result = refresh_trace_policies(reg, execute=False)
        assert result["placeholder"] == 1 and result["refreshed"] == []
        assert "pending" in reg.get("cp.builtin.read_file").evolution.trace_policy


# ════════════════════════════════════════════════════════════
#  【L4】stage 入轨
# ════════════════════════════════════════════════════════════


class TestL4StageIngestion:
    def test_ingestion_report_shape(self, chain):
        from agent.digestion.stage import backfill_stages
        from tests.unit.digestion_util import descriptor_registry

        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            reg = descriptor_registry(pathlib.Path(tmp), ["a", "b"])
            report = backfill_stages(reg, execute=False)
            for key in ("total_assets", "empty_stage", "warnings_before",
                        "warnings_after", "by_source_type", "plan", "ingested",
                        "failed", "residual", "executed"):
                assert key in report
            assert report["empty_stage"] == 2
            assert report["warnings_before"] == 2

    def test_real_ledger_has_no_unstaged_asset(self):
        """【L4】真实台账：stage 为空资产数 = 0（warning 清零）"""
        ledger = pathlib.Path("data/descriptors.json")
        if not ledger.exists():
            pytest.skip("运行时台账不存在（CI 冷启动）")
        from agent.descriptors.registry import DescriptorRegistry
        reg = DescriptorRegistry()
        empty = [d.capability_id for d in reg.list() if not d.evolution.stage]
        assert empty == [], f"仍有未入轨资产: {empty}"

    def test_real_ledger_borrowed_assets_have_policy(self):
        ledger = pathlib.Path("data/descriptors.json")
        if not ledger.exists():
            pytest.skip("运行时台账不存在（CI 冷启动）")
        from agent.descriptors.registry import DescriptorRegistry
        reg = DescriptorRegistry()
        for desc in reg.list():
            if desc.evolution.stage and desc.evolution.stage.value == "borrowed":
                assert (desc.evolution.trace_policy or "").strip()

    def test_ingestion_is_audited_and_evented(self, tmp_path, chain, events_dir):
        from agent.digestion.stage import backfill_stages
        from tests.unit.digestion_util import descriptor_registry
        import agent.observability.events as events_mod

        reg = descriptor_registry(tmp_path, ["a", "b"])
        report = backfill_stages(reg, execute=True)
        assert len(report["ingested"]) == 2
        chain.flush()
        assert sum(1 for e in chain.entries()
                   if e.action == "descriptor.stage") == 2
        digests = [e for e in events_mod.iter_events(directory=events_dir)
                   if e.type == "digest.stage"]
        assert len(digests) == 2
        assert all(e.payload["scope"] == "first_entry_backfill" for e in digests)

    def test_ingestion_policy_is_real_ledger_reference(self, tmp_path):
        from agent.digestion.stage import backfill_stages
        from tests.unit.digestion_util import descriptor_registry

        reg = descriptor_registry(tmp_path, ["a"])
        backfill_stages(reg, execute=True)
        desc = reg.get("cp.builtin.a")
        assert desc.evolution.stage.value == "borrowed"
        assert "ledger=unified_traces" in desc.evolution.trace_policy
        assert "pending" not in desc.evolution.trace_policy.lower()
