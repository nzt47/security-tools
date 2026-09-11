"""TASK-S2-01 TraceContext 透传接线 集成测试

覆盖（任务书 §二 步骤 2 的三层接线）：
- 会话层：SessionManager.workspace_id_for（P7.1-19 workspace-hash）
- 工具执行链：ToolCallingService._execute_safe 写统一子 Trace（parent 串联）
- 任务编排：orchestrator 任务级统一 Trace 起点/收尾（无 ContextVar 泄漏）
- 端到端「修复失败测试」任务：task 主 Trace + tool 子 Trace 可串联且全程含 workspace_id
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from agent.observability import trace_v2
from agent.observability.tool_trace import ToolTraceRecorder
from agent.observability.trace_v2 import (
    STATUS_ERROR,
    STATUS_SUCCESS,
    TraceContext,
    TraceFacade,
    derive_workspace_id,
    load_runtime_descriptors,
)


# ════════════════════════════════════════════════════════════
#  fixture
# ════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _clean_state():
    trace_v2._trace_context_var.set(None)
    TraceFacade.reset()
    ToolTraceRecorder.reset()
    yield
    trace_v2._trace_context_var.set(None)
    TraceFacade.reset()
    ToolTraceRecorder.reset()


@pytest.fixture
def unified(tmp_path):
    """把 TraceFacade 单例指向临时台账（避免污染真实 tool_trace.db）"""
    f = TraceFacade(str(tmp_path / "trace_v2_it.db"))
    TraceFacade._instance = f
    yield f
    f.flush()
    f._store.stop(timeout=2.0)
    TraceFacade._instance = None


@pytest.fixture
def tool_recorder(tmp_path):
    r = ToolTraceRecorder(db_path=str(tmp_path / "tool_trace_it.db"))
    ToolTraceRecorder._instance = r
    yield r
    r.stop(timeout=2.0)
    ToolTraceRecorder._instance = None


def _service(core_result):
    from agent.tool_calling import ToolCallingService
    svc = ToolCallingService.__new__(ToolCallingService)
    svc._execute_safe_core = MagicMock(return_value=core_result)
    return svc


# ════════════════════════════════════════════════════════════
#  1. 会话层：workspace_id 来源（P7.1-19）
# ════════════════════════════════════════════════════════════


class TestSessionWorkspaceId:
    def test_workspace_id_for_default_workspace(self, tmp_path):
        from agent.session_manager import SessionManager
        mgr = SessionManager(sessions_dir=str(tmp_path / "sessions"))
        info = mgr.create_session(title="t")
        wid = mgr.workspace_id_for(info["id"])
        assert wid.startswith("ws_") and len(wid) == 19
        # 与直接派生该会话默认工作空间路径一致
        assert wid == derive_workspace_id(str(mgr.get_session_workspace_dir(info["id"])))

    def test_workspace_id_for_deterministic(self, tmp_path):
        from agent.session_manager import SessionManager
        mgr = SessionManager(sessions_dir=str(tmp_path / "sessions"))
        info = mgr.create_session(title="t")
        assert mgr.workspace_id_for(info["id"]) == mgr.workspace_id_for(info["id"])

    def test_workspace_id_for_custom_bound_root(self, tmp_path):
        from agent.session_manager import SessionManager
        mgr = SessionManager(sessions_dir=str(tmp_path / "sessions"))
        info = mgr.create_session(title="t")
        default_wid = mgr.workspace_id_for(info["id"])
        repo = tmp_path / "myrepo"
        repo.mkdir()
        ok, _ = mgr.bind_workspace_root(info["id"], str(repo), create=True)
        assert ok
        bound_wid = mgr.workspace_id_for(info["id"])
        assert bound_wid != default_wid
        assert bound_wid == derive_workspace_id(str(repo))

    def test_workspace_id_for_unknown_session_is_path_hash(self, tmp_path):
        from agent.session_manager import SessionManager
        mgr = SessionManager(sessions_dir=str(tmp_path / "sessions"))
        wid = mgr.workspace_id_for("sess_does_not_exist")
        assert wid.startswith("ws_")


# ════════════════════════════════════════════════════════════
#  2. 工具执行链：统一子 Trace 透传
# ════════════════════════════════════════════════════════════


class TestToolCallingPropagation:
    def test_execute_safe_records_child_trace_when_context_active(
            self, unified, tool_recorder):
        root = unified.start(task_id="tk", tenant_id="ten", workspace_id="ws_repo")
        svc = _service({"ok": True, "result": "done"})
        result = svc._execute_safe("read_file", {"path": "x.py"})
        assert result["ok"] is True
        assert unified.flush()

        [child] = unified.list_by_capability("cp.builtin.read_file")
        assert child.parent_trace_id == root
        assert child.task_id == "tk"
        assert child.tenancy.workspace_id == "ws_repo"
        assert child.tenancy.tenant_id == "ten"
        assert child.response.status == STATUS_SUCCESS
        assert child.request.args_hash
        assert child.timing.duration_ms >= 0

    def test_execute_safe_skips_unified_trace_without_context(
            self, unified, tool_recorder):
        """无任务级 TraceContext → 不写统一 Trace（不污染共享台账）"""
        svc = _service({"ok": True})
        svc._execute_safe("read_file", {"path": "x.py"})
        assert unified.flush()
        assert unified.query() == []

    def test_execute_safe_failure_records_error_status(self, unified, tool_recorder):
        unified.start(task_id="tk", workspace_id="ws")
        svc = _service({"ok": False, "error": "boom"})
        svc._execute_safe("shell_execute", {"cmd": "pytest"})
        assert unified.flush()
        [child] = unified.list_by_capability("cp.builtin.shell_execute")
        assert child.response.status == STATUS_ERROR
        assert child.response.error_code == "boom"

    def test_execute_safe_records_tool_exception_path(self, unified, tool_recorder):
        unified.start(task_id="tk", workspace_id="ws")
        from agent import tools
        svc = _service({"ok": True})
        svc._execute_safe_core = MagicMock(side_effect=tools.ToolError("tool boom"))
        svc._execute_safe("read_file", {"path": "x"})
        assert unified.flush()
        [child] = unified.list_by_capability("cp.builtin.read_file")
        assert child.response.status == STATUS_ERROR
        assert "boom" in child.response.error_code

    def test_unified_trace_failure_does_not_break_tool(self, unified, tool_recorder):
        """守【不易】：统一 Trace 异常绝不影响工具执行主路径"""
        unified.start(task_id="tk", workspace_id="ws")
        svc = _service({"ok": True, "result": "ok"})
        with patch.object(TraceFacade, "record", side_effect=RuntimeError("trace down")):
            result = svc._execute_safe("read_file", {"path": "x"})
        assert result == {"ok": True, "result": "ok"}

    def test_existing_tool_trace_still_written_alongside_unified(
            self, unified, tool_recorder):
        """既有 tool_trace 写入方保留（零删除，双写过渡）"""
        unified.start(task_id="tk", workspace_id="ws")
        svc = _service({"ok": True})
        # read_file 属高频工具（10% 采样）→ 固定 random 保证落账（确定性）
        with patch("agent.observability.tool_trace.random.random", return_value=0.0):
            svc._execute_safe("read_file", {"path": "x"})
        assert tool_recorder.flush()
        rows = tool_recorder.get_recent_traces("read_file")
        assert len(rows) == 1
        assert rows[0].success is True
        assert unified.flush()
        # TASK-S3-01【L1】：统一轨落账键已由工具名改写为 canonical capability_id
        # （既有 tool_trace 轨仍按工具名落账——两轨职责不同，未做同步改写）
        assert len(unified.list_by_capability("cp.builtin.read_file")) == 1


# ════════════════════════════════════════════════════════════
#  3. 任务编排层：orchestrator 任务级 Trace 起点/收尾
# ════════════════════════════════════════════════════════════


class TestOrchestratorWiring:
    def test_begin_sets_context_with_cwd_workspace(self, unified):
        from agent.orchestrator import orchestrator as o
        tid = o._begin_unified_task_trace(None, "task-x")
        assert tid
        ctx = TraceContext.current()
        assert ctx is not None and ctx.trace_id == tid
        assert ctx.workspace_id.startswith("ws_")
        assert ctx.task_id == "task-x"
        assert ctx.tenant_id == ctx.workspace_id  # P7.2-08 tenant=workspace-hash
        o._end_unified_task_trace(tid)

    def test_begin_uses_session_workspace_id(self, unified):
        from agent.orchestrator import orchestrator as o

        class _StubMgr:
            def workspace_id_for(self, session_id):
                return "ws_from_session"

        with patch("agent.session_manager.SessionManager", _StubMgr):
            tid = o._begin_unified_task_trace("sess_1", "task-y")
        ctx = TraceContext.current()
        assert ctx.workspace_id == "ws_from_session"
        assert ctx.subject_id == "sess_1"
        o._end_unified_task_trace(tid)

    def test_end_writes_task_trace_and_clears_context(self, unified):
        from agent.orchestrator import orchestrator as o
        tid = o._begin_unified_task_trace(None, "task-z")
        assert unified.flush()
        o._end_unified_task_trace(tid, STATUS_SUCCESS)
        assert unified.flush()
        assert TraceContext.current() is None
        [task] = [t for t in unified.query() if t.trace_id == tid]
        assert task.capability_id == ""
        assert task.task_id == "task-z"
        assert task.response.status == STATUS_SUCCESS

    def test_end_with_mismatched_trace_id_is_noop(self, unified):
        from agent.orchestrator import orchestrator as o
        tid = o._begin_unified_task_trace(None, "task-w")
        o._end_unified_task_trace("other_trace_id_xx")
        assert TraceContext.current() is not None  # 未被误清
        o._end_unified_task_trace(tid)

    def test_end_with_empty_id_is_noop(self, unified):
        from agent.orchestrator import orchestrator as o
        o._end_unified_task_trace("")  # 不抛异常

    def test_begin_failure_returns_empty(self, unified):
        from agent.orchestrator import orchestrator as o
        with patch.object(TraceFacade, "start", side_effect=RuntimeError("nope")):
            assert o._begin_unified_task_trace(None, "t") == ""

    def test_end_failure_is_swallowed(self, unified):
        from agent.orchestrator import orchestrator as o
        tid = o._begin_unified_task_trace(None, "task-v")
        with patch.object(TraceFacade, "finish", side_effect=RuntimeError("down")):
            o._end_unified_task_trace(tid)  # 不抛异常


# ════════════════════════════════════════════════════════════
#  4. 端到端：「修复失败测试」任务（task 主 + tool 子 串联）
# ════════════════════════════════════════════════════════════


class TestEndToEndFixFailedTestTask:
    def test_task_and_tool_traces_chain_with_workspace(self, unified, tool_recorder):
        """端到端一个「修复失败测试」任务：主 Trace（task 级）+ 子 Trace（tool 级）
        经 parent_trace_id 串联，全部含 workspace_id。"""
        workspace_id = derive_workspace_id("C:/repo/demo")
        root = unified.start(task_id="fix-failed-test", tenant_id=workspace_id,
                             workspace_id=workspace_id, subject_id="dev1",
                             policy_version="policy-v1")

        svc = _service({"ok": True, "result": "def test_x(): ..."})
        svc._execute_safe("read_file", {"path": "tests/test_x.py"})
        svc2 = _service({"ok": False, "error": "1 failed"})
        svc2._execute_safe("shell_execute", {"cmd": "pytest tests/test_x.py"})
        svc3 = _service({"ok": True, "result": "patched"})
        svc3._execute_safe("write_file", {"path": "src/x.py", "content": "fix"})

        unified.finish(status=STATUS_SUCCESS)
        assert unified.flush()

        chain = unified.chain(root)
        # 主 Trace 在链首，3 个子 Trace 经 parent 挂到主 Trace
        assert chain[0].trace_id == root
        assert sum(1 for t in chain if t.parent_trace_id == root) == 3
        assert len(chain) == 4
        # 全程含 workspace_id（主 + 子）
        assert all(t.tenancy.workspace_id == workspace_id for t in chain)
        assert all(t.task_id == "fix-failed-test" for t in chain)

        summary = unified.task_summary("fix-failed-test")
        assert summary["step_count"] == 3
        assert summary["success_count"] == 2
        assert summary["failed_count"] == 1
        assert abs(summary["success_rate"] - 2 / 3) < 1e-4
        assert summary["workspace_id"] == workspace_id
        assert summary["capabilities"] == [
            "cp.builtin.read_file", "cp.builtin.shell_execute",
            "cp.builtin.write_file"]

    def test_chain_supports_s3_pattern_mining_threshold(self, unified):
        """S3 模式挖掘：同一 capability 累积 ≥20 条同类轨迹可计量"""
        unified.start(task_id="tk", workspace_id="ws")
        for i in range(22):
            unified.record("cp.builtin.read_file", args={"path": f"f{i}.py"},
                           output={"ok": True})
        unified.finish()
        assert unified.flush()
        rows = unified.list_by_capability("cp.builtin.read_file", limit=100)
        assert len(rows) == 22
        # S5 评测可直接消费（每行含 hash + status + timing）
        for r in rows:
            assert r.request.args_hash and r.response.status == STATUS_SUCCESS
            assert r.timing.duration_ms is not None

    def test_trace_capability_id_joins_registered_descriptor(self, unified, tmp_path):
        from agent.descriptors.registry import DescriptorRegistry
        reg = DescriptorRegistry(path=str(tmp_path / "d.json"), autosave=False)
        reg, summary = load_runtime_descriptors(
            reg, builtin_entries=[{"name": "read_file", "description": "读文件"}])
        assert summary["builtin"]["registered"] == 1

        unified.start(task_id="tk", workspace_id="ws")
        unified.record("cp.builtin.read_file", args={"path": "x"},
                       output={"ok": True})
        unified.finish()
        assert unified.flush()

        [trace] = unified.list_by_capability("cp.builtin.read_file")
        descriptor = reg.get(trace.capability_id)
        assert descriptor is not None
        assert descriptor.capability.name == "read_file"

    def test_trace_stats_artifact_consumable(self, unified, tmp_path):
        """读取接口产出可被 S3/S5 直接消费的摘要（trace_stats.json）"""
        unified.start(task_id="tk", workspace_id="ws")
        unified.record("cp.builtin.read_file", args={"path": "a"}, output={"ok": True})
        unified.record("cp.builtin.read_file", args={"path": "b"},
                       output={"ok": False, "error": "E"})
        unified.finish()
        assert unified.flush()
        target = tmp_path / "trace_stats.json"
        stats = unified.write_stats(str(target))
        payload = json.loads(target.read_text(encoding="utf-8"))
        assert payload["by_capability"]["cp.builtin.read_file"] == 2
        assert payload["schema_version"] == trace_v2.SCHEMA_VERSION
        assert stats["workspace_degraded"] == 0
