"""TASK-S2-03 双轨接线集成测试（真实发生点 → events.v1 事件流）

覆盖任务书 §二「双轨接线」要求的四个真实发生点：
1. 会话任务完成 → `task.closed`（orchestrator 收口点，含失败早退）
2. 审批 submit / approve / reject / 超时 → `approval.required` + `approval` + `intervention`
3. 用户手工编辑任务文件 → `escape`（受控写台账比对）
4. LLM 调用记账 → `cost`（含缓存命中不计 token）+ `model.degraded`

以及 S2-02 既有能力不被破坏（链式审计留痕仍发生）。
"""

import json
import os
from datetime import datetime, timedelta

import pytest

from agent.observability import acr, events as ev, utc
from agent.observability.trace_v2 import TraceContext, TraceFacade


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv(ev.ENV_DIR, str(tmp_path / "events"))
    monkeypatch.setenv("CP_UTC_ANCHOR_MODEL", "gpt-4")
    ev.reset_event_stores()
    utc.reset_config_cache()
    # TraceContext 是 ContextVar：显式清空，避免跨用例残留上下文
    from agent.observability import trace_v2
    trace_v2._trace_context_var.set(None)
    TraceFacade.reset()
    TraceFacade.instance(db_path=str(tmp_path / "trace.db"))
    yield
    trace_v2._trace_context_var.set(None)
    TraceFacade.reset()
    ev.reset_event_stores()
    utc.reset_config_cache()


@pytest.fixture
def events_dir(tmp_path):
    return str(tmp_path / "events")


# ════════════════════════════════════════════════════════════
#  1. 会话任务完成 → task.closed
# ════════════════════════════════════════════════════════════


class TestTaskClosedWiring:
    def test_success_path_emits_task_closed(self, tmp_path, events_dir):
        from agent.orchestrator.orchestrator import _end_unified_task_trace

        tid = TraceFacade.instance().start(task_id="task-orch-1",
                                          workspace_id="ws_demo",
                                          subject_id="sess-9")
        _end_unified_task_trace(tid, "success", "", "帮我修复这个报错")

        rows = ev.read_events(types=["task.closed"], directory=events_dir)
        assert len(rows) == 1
        payload = rows[0].payload
        assert payload["status"] == "closed"
        assert payload["intent"] == "fix"
        assert payload["difficulty"] in acr.DIFFICULTIES
        assert payload["intervened"] is False
        assert payload["task_id"] == "task-orch-1"
        # TraceFacade.finish() 会清空 ContextVar → 叶子字段必须仍完整落到 task.closed
        assert payload["workspace_id"] == "ws_demo"
        assert payload["subject_id"] == "sess-9"
        assert rows[0].correlation_id == tid

    def test_failure_path_records_failed(self, events_dir):
        from agent.orchestrator.orchestrator import _end_unified_task_trace

        tid = TraceFacade.instance().start(task_id="task-orch-2")
        _end_unified_task_trace(tid, "error", "APITimeoutError", "介绍一下这个原理")

        payload = ev.read_events(types=["task.closed"], directory=events_dir)[0].payload
        assert payload["status"] == "failed"
        assert payload["intent"] == "explore"
        assert payload["error_code"] == "APITimeoutError"

    def test_explore_task_excluded_from_acr_denominator(self, events_dir):
        from agent.orchestrator.orchestrator import _end_unified_task_trace

        tid = TraceFacade.instance().start(task_id="explore-1")
        _end_unified_task_trace(tid, "success", "", "看看这个项目怎么组织的")

        summary = acr.acr_daily(directory=events_dir)
        assert summary["excluded"]["tasks"] == 1
        assert summary["denominator"]["total"] == 0
        assert summary["exploration"]["satisfaction"] == 1.0

    def test_mismatched_trace_id_is_noop(self, events_dir):
        from agent.orchestrator.orchestrator import _end_unified_task_trace

        TraceFacade.instance().start(task_id="t-keep")
        _end_unified_task_trace("not-the-current-trace", "success")
        assert ev.read_events(types=["task.closed"], directory=events_dir) == []

    def test_in_task_intervention_marks_intervened(self, events_dir):
        from agent.orchestrator.orchestrator import _end_unified_task_trace

        tid = TraceFacade.instance().start(task_id="task-int")
        acr.record_intervention("rerun", correlation_id=tid, task_id="task-int")
        _end_unified_task_trace(tid, "success", "", "执行一下这个脚本")

        payload = ev.read_events(types=["task.closed"], directory=events_dir)[0].payload
        assert payload["intervened"] is True
        assert payload["intervention_kind"] == "rerun"


# ════════════════════════════════════════════════════════════
#  2. 审批流 → approval.required / approval / intervention
# ════════════════════════════════════════════════════════════


@pytest.fixture
def flow(tmp_path):
    from agent.skills_mgmt.approval import ApprovalFlow
    return ApprovalFlow(records_path=str(tmp_path / "approvals.jsonl"))


class TestApprovalWiring:
    def test_submit_emits_approval_required(self, flow, events_dir):
        rec = flow.submit("skill", "s1", action="params_submit", actor="agent")
        rows = ev.read_events(types=["approval.required"], directory=events_dir)
        assert len(rows) == 1
        assert rows[0].payload["record_id"] == rec.record_id
        assert rows[0].payload["level"] == "L1"

    def test_approve_emits_intervention_weight_one(self, flow, events_dir):
        rec = flow.submit("skill", "s1", action="params_submit")
        flow.approve(rec.record_id, actor="reviewer")

        kinds = {r.payload["kind"]: r.payload for r in
                 ev.read_events(types=["intervention"], directory=events_dir)}
        assert kinds["approve"]["weight"] == 1.0
        approval = ev.read_events(types=["approval"], directory=events_dir)[0]
        assert approval.payload["kind"] == "approve"
        assert "fatigue_bucket" in approval.payload
        assert approval.payload["counted_as_intervention"] is True

    def test_approve_with_note_is_conditional(self, flow, events_dir):
        rec = flow.submit("skill", "s1", action="params_submit")
        flow.approve(rec.record_id, actor="reviewer", note="仅限本周")
        kinds = {r.payload["kind"] for r in
                 ev.read_events(types=["intervention"], directory=events_dir)}
        assert kinds == {"conditional"}
        weights = {r.payload["kind"]: r.payload["weight"] for r in
                   ev.read_events(types=["intervention"], directory=events_dir)}
        assert weights["conditional"] == 0.5

    def test_reject_emits_extension_kind(self, flow, events_dir):
        rec = flow.submit("skill", "s1", action="params_submit")
        flow.reject(rec.record_id, actor="reviewer", reason="证据不足")
        kinds = {r.payload["kind"] for r in
                 ev.read_events(types=["intervention"], directory=events_dir)}
        assert kinds == {"reject"}

    def test_l0_auto_pass_weight_zero(self, flow, events_dir):
        flow.submit("prompt", "p1", action="prompt_apply", level="L0")
        rows = ev.read_events(types=["intervention"], directory=events_dir)
        assert rows[0].payload["kind"] == "auto_pass"
        assert rows[0].payload["weight"] == 0.0

    def test_approval_disabled_is_auto_pass(self, tmp_path, events_dir):
        from agent.skills_mgmt.approval import ApprovalFlow
        disabled = ApprovalFlow(records_path=str(tmp_path / "a2.jsonl"),
                                enabled=False)
        disabled.submit("skill", "s1", action="params_submit")
        rows = ev.read_events(types=["intervention"], directory=events_dir)
        assert rows[0].payload["kind"] == "auto_pass"

    def test_timeout_deny_weight_two(self, flow, events_dir):
        rec = flow.submit("skill", "s1", action="params_submit")
        rec.created_at = (datetime.now() - timedelta(days=3)).isoformat(
            timespec="seconds")
        flow._persist()

        expired = flow.expire_pending(older_than_seconds=3600)
        assert [r.record_id for r in expired] == [rec.record_id]
        assert flow.get(rec.record_id).state == "rejected"
        assert "超时未审批" in flow.get(rec.record_id).decision_reason

        rows = ev.read_events(types=["intervention"], directory=events_dir)
        assert [r.payload["kind"] for r in rows] == ["timeout_deny"]
        assert rows[0].payload["weight"] == 2.0

    def test_timeout_does_not_double_count_as_reject(self, flow, events_dir):
        rec = flow.submit("skill", "s1", action="params_submit")
        rec.created_at = (datetime.now() - timedelta(days=3)).isoformat(
            timespec="seconds")
        flow._persist()
        flow.expire_pending(older_than_seconds=3600)
        kinds = [r.payload["kind"] for r in
                 ev.read_events(types=["intervention"], directory=events_dir)]
        assert "reject" not in kinds

    def test_timeout_skips_fresh_records(self, flow, events_dir):
        flow.submit("skill", "s1", action="params_submit")
        assert flow.expire_pending(older_than_seconds=3600) == []
        assert ev.read_events(types=["intervention"], directory=events_dir) == []

    def test_merge_measures_without_counting(self, flow, events_dir):
        rec = flow.submit("skill", "s1", action="params_submit",
                          applier=lambda: None)
        flow.approve(rec.record_id)
        flow.merge(rec.record_id)
        kinds = [r.payload["kind"] for r in
                 ev.read_events(types=["approval"], directory=events_dir)]
        assert "merged" in kinds
        interventions = [r.payload["kind"] for r in
                         ev.read_events(types=["intervention"], directory=events_dir)]
        assert "merged" not in interventions


# ════════════════════════════════════════════════════════════
#  3. 任务文件手工编辑 → escape
# ════════════════════════════════════════════════════════════


class TestTaskFileEscapeWiring:
    @pytest.fixture
    def task_file(self, tmp_path, monkeypatch):
        path = tmp_path / "scheduled_tasks.json"
        path.write_text(json.dumps({"tasks": []}), encoding="utf-8")
        import agent.tools.task_tools as tt
        monkeypatch.setattr(tt, "SCHEDULED_TASKS_FILE", str(path))
        monkeypatch.setattr("agent.task_scheduler.get_scheduler",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no scheduler")))
        return str(path)

    def test_create_registers_governed_write(self, task_file, events_dir):
        from agent.tools.task_tools import create_scheduled_task, list_scheduled_tasks

        assert create_scheduled_task("demo", "echo hi", 60)["ok"] is True
        list_scheduled_tasks()                     # 受控写之后 → clean
        assert ev.read_events(types=["escape"], directory=events_dir) == []

    def test_manual_edit_then_list_detects_escape(self, task_file, events_dir):
        from agent.tools.task_tools import create_scheduled_task, list_scheduled_tasks

        create_scheduled_task("demo", "echo hi", 60)
        # 用户手工改文件（绕过受控写入能力）
        with open(task_file, "w", encoding="utf-8") as fh:
            json.dump({"tasks": [{"id": "evil", "command": "rm -rf /"}]}, fh)

        list_scheduled_tasks()
        rows = ev.read_events(types=["escape"], directory=events_dir)
        assert len(rows) == 1
        payload = rows[0].payload
        assert payload["reason"] == "untracked_content_change"
        assert payload["capability_id"] == "cp.governed.scheduled_task.write"
        # 受控写时已登记 task_ids 快照 → 新增的 "evil" 被定位到（同时披露被删的任务）
        assert "evil" in payload["task_id"]
        assert "evil" in payload["changed_task_ids"]
        # 逃逸同时计一次介入（weight=1）
        interventions = ev.read_events(types=["intervention"], directory=events_dir)
        assert interventions[-1].payload["kind"] == "escape"
        assert interventions[-1].payload["weight"] == 1.0

    def test_repeated_list_does_not_duplicate_escape(self, task_file, events_dir):
        from agent.tools.task_tools import create_scheduled_task, list_scheduled_tasks

        create_scheduled_task("demo", "echo hi", 60)
        with open(task_file, "w", encoding="utf-8") as fh:
            json.dump({"tasks": [{"id": "evil"}]}, fh)
        list_scheduled_tasks()
        list_scheduled_tasks()
        assert len(ev.read_events(types=["escape"], directory=events_dir)) == 1

    def test_governed_recovery_is_clean_again(self, task_file, events_dir):
        from agent.tools.task_tools import (delete_scheduled_task,
                                            list_scheduled_tasks)
        # 先建基线（首次见到 → baseline），再做一次受控写
        list_scheduled_tasks()
        with open(task_file, "w", encoding="utf-8") as fh:
            json.dump({"tasks": []}, fh)
        from agent.observability.escape import record_governed_write
        record_governed_write(task_file, writer="test")
        assert ev.read_events(types=["escape"], directory=events_dir) == []
        assert delete_scheduled_task("nope")["ok"] is True


# ════════════════════════════════════════════════════════════
#  4. LLM 调用记账 → cost / model.degraded
# ════════════════════════════════════════════════════════════


class TestLlmCostWiring:
    def test_record_emits_cost_with_task_context(self, events_dir):
        from agent.llm_monitor import LLMInteraction, LLMMonitor

        monitor = LLMMonitor(max_records=5)
        ctx = TraceContext(trace_id="tr-llm", task_id="task-llm",
                           workspace_id="ws_llm", subject_id="sess-llm")
        token = ctx.enter()
        try:
            monitor.record(LLMInteraction(
                source="chat", model="gpt-4", provider="openai",
                request_tokens=1000, response_tokens=500, duration_ms=120.0))
        finally:
            TraceContext.exit(token)

        rows = ev.read_events(types=["cost"], directory=events_dir)
        assert len(rows) == 1
        payload = rows[0].payload
        assert payload["task_id"] == "task-llm"
        assert payload["workspace_id"] == "ws_llm"
        assert payload["subject_id"] == "sess-llm"
        assert payload["cost_normalized_cents"] == pytest.approx(6.0)

    def test_cache_hit_bills_zero_tokens(self, events_dir):
        from agent.llm_monitor import LLMInteraction, LLMMonitor

        monitor = LLMMonitor(max_records=5)
        monitor.record(LLMInteraction(source="chat", model="gpt-4",
                                      request_tokens=1000, response_tokens=500,
                                      cache_hit=True))
        payload = ev.read_events(types=["cost"], directory=events_dir)[0].payload
        assert payload["cache_hit"] is True
        assert payload["billable_tokens_in"] == 0
        assert payload["cost_normalized_cents"] == 0.0

    def test_error_emits_model_degraded(self, events_dir):
        from agent.llm_monitor import LLMInteraction, LLMMonitor

        monitor = LLMMonitor(max_records=5)
        monitor.record(LLMInteraction(source="chat", model="gpt-4",
                                      error="APITimeoutError: boom"))
        rows = ev.read_events(types=["model.degraded"], directory=events_dir)
        assert len(rows) == 1
        assert rows[0].payload["from"] == "gpt-4"
        assert rows[0].payload["to"]
        assert "APITimeoutError" in rows[0].payload["reason"]
        assert rows[0].payload["error_code"] == "E_MODEL_DEGRADED"

    def test_utf_strings_from_trace_context(self, events_dir):
        from agent.llm_monitor import LLMInteraction, LLMMonitor

        monitor = LLMMonitor(max_records=5)
        interaction = LLMMonitor.create_from_api_call(
            system_prompt="sys", messages=[{"role": "user", "content": "hi"}],
            model="gpt-4", provider="openai", source="chat")
        monitor.record(interaction)
        assert interaction.task_id == ""
        assert interaction.cost_normalized_cents >= 0.0
        assert len(ev.read_events(types=["cost"], directory=events_dir)) == 1

    def test_utc_aggregates_wired_costs(self, events_dir):
        from agent.llm_monitor import LLMInteraction, LLMMonitor

        monitor = LLMMonitor(max_records=5)
        for i in range(3):
            monitor.record(LLMInteraction(source="chat", model="gpt-4",
                                          request_tokens=1000, response_tokens=0,
                                          duration_ms=10.0))
        row = utc.utc_daily(directory=events_dir)
        assert row["llm_calls"] == 3
        assert row["cost_normalized_cents"] == pytest.approx(9.0)


# ════════════════════════════════════════════════════════════
#  5. 其它真实发生点
# ════════════════════════════════════════════════════════════


class TestOtherHooks:
    def test_scheduler_execute_now_records_rerun(self, events_dir, monkeypatch):
        from agent.task_scheduler import TaskScheduler

        scheduler = TaskScheduler()
        scheduler.tasks.append({"name": "demo", "type": "python_func",
                                "func": lambda: None, "interval": 1,
                                "last_run": None, "enabled": True,
                                "task_id": "t-1"})
        result = scheduler.execute_now("t-1")
        assert result["status"] == "success"
        rows = ev.read_events(types=["intervention"], directory=events_dir)
        assert rows[0].payload["kind"] == "rerun"
        assert rows[0].payload["weight"] == 1.0

    def test_scheduler_execute_now_unknown_task_noop(self, events_dir):
        from agent.task_scheduler import TaskScheduler

        scheduler = TaskScheduler()
        assert scheduler.execute_now("missing") is None
        assert ev.read_events(types=["intervention"], directory=events_dir) == []

    def test_pending_approvals_view_records_zero_weight(self, flow, events_dir):
        from agent.skills_mgmt.service import SkillsMgmtService

        flow.submit("skill", "s1", action="params_submit")
        service = SkillsMgmtService()
        service._approval = flow
        rows = service.list_pending_approvals()
        assert len(rows) == 1
        interventions = ev.read_events(types=["intervention"], directory=events_dir)
        assert interventions[-1].payload["kind"] == "view"
        assert interventions[-1].payload["weight"] == 0.0

    def test_graceful_degrade_emits_model_degraded(self, events_dir):
        from agent.graceful_degrade import get_degrade_manager

        manager = get_degrade_manager()
        manager._trigger_degrade("llm_chat")
        rows = ev.read_events(types=["model.degraded"], directory=events_dir)
        assert len(rows) == 1
        assert rows[0].payload["from"] == "llm_chat"
        assert rows[0].payload["reason"].startswith("graceful_degrade:")
        assert rows[0].payload["degrade_component"] == "llm_chat"

    def test_graceful_degrade_ignores_non_model_component(self, events_dir):
        from agent.graceful_degrade import get_degrade_manager

        get_degrade_manager()._trigger_degrade("memory_query")
        assert ev.read_events(types=["model.degraded"], directory=events_dir) == []

    def test_llm_service_default_does_not_change_behavior(self, events_dir,
                                                          monkeypatch):
        from memory.llm_service import LLMService

        service = LLMService(provider="openai", api_key="sk-test-0123456789",
                             model="gpt-4")

        def _boom(*args, **kwargs):
            raise RuntimeError("primary down")

        monkeypatch.setattr(service, "_chat_with_retry", _boom)
        from memory.llm_service import LLMServiceError
        with pytest.raises(LLMServiceError):
            service.chat([{"role": "user", "content": "hi"}])
        rows = ev.read_events(types=["model.degraded"], directory=events_dir)
        assert len(rows) == 1
        assert rows[0].payload["fallback_attempted"] is False

    def test_llm_service_fallback_switch_when_enabled(self, events_dir,
                                                      monkeypatch):
        from memory.llm_service import LLMService

        monkeypatch.setenv("CP_MODEL_FALLBACK_ENABLED", "1")
        monkeypatch.setenv("CP_MODEL_FALLBACK_CHAIN", "fallback-model")
        from agent.observability import model_degrade
        model_degrade.reset_chain_cache()      # 配置变更后清解析缓存
        service = LLMService(provider="openai", api_key="sk-test-0123456789",
                             model="gpt-4")

        def _boom(*args, **kwargs):
            raise RuntimeError("primary down")

        def _shadow_ok(self, *args, **kwargs):
            return f"recovered-by:{self.model}"

        monkeypatch.setattr(service, "_chat_with_retry", _boom)
        monkeypatch.setattr(LLMService, "_do_chat", _shadow_ok)
        assert service.chat([{"role": "user", "content": "hi"}]) == \
            "recovered-by:fallback-model"
        row = ev.read_events(types=["model.degraded"], directory=events_dir)[0]
        assert row.payload["to"] == "fallback-model"
        assert row.payload["fallback_attempted"] is True

    def test_event_stream_feeds_acr_and_utc_together(self, flow, events_dir):
        """一条真实任务链同时产出 ACR 与 UTC 数据（验收报告快照的自动化版本）"""
        from agent.llm_monitor import LLMInteraction, LLMMonitor
        from agent.orchestrator.orchestrator import _end_unified_task_trace

        tid = TraceFacade.instance().start(task_id="chain-1", workspace_id="ws_x")
        LLMMonitor(max_records=5).record(LLMInteraction(
            source="chat", model="gpt-4", provider="openai",
            request_tokens=2000, response_tokens=1000, duration_ms=90.0))
        rec = flow.submit("skill", "s1", action="params_submit", actor="agent")
        flow.approve(rec.record_id, actor="reviewer")
        _end_unified_task_trace(tid, "success", "", "实现一个新的解析器")

        acr_view = acr.acr_daily(directory=events_dir)
        utc_view = utc.utc_daily(directory=events_dir)
        assert acr_view["denominator"]["total"] == 1
        assert acr_view["interventions"]["by_kind"]["approve"] == 1.0
        assert utc_view["llm_calls"] == 1
        assert utc_view["utc_cents_per_task"] == pytest.approx(12.0)
        assert set(r.type for r in ev.read_events(directory=events_dir)) >= {
            "task.closed", "cost", "approval.required", "approval", "intervention"}
