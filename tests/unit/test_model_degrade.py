"""TASK-S2-03 model.degraded 事件（P7.1-18 第 9 事件 / §11.6.0）单元测试

覆盖范围：
- `model.degraded {from, to, reason}` 字段完备 + `E_MODEL_DEGRADED` 错误码
- 降级链解析优先级（explicit > env > config.yaml > 文档化默认链）
- 候选模型解析（next_fallback_model）
- 主模型失败收口：默认只发事件不切模型（fallback_attempted=False，如实标注）
- 开启 CP_MODEL_FALLBACK_ENABLED 后真实降级重试（成功 → 返回结果）
- 降级候选同样失败 → 如实记录 fallback_error，不吞原始错误
- call_with_model_fallback 逐个候选降级；全失败抛最后一个异常
- 幂等（同一 from→to→reason 折叠）
- 汇总视图（按边/按原因）
"""

import json
import os

import pytest

from agent.observability import events as ev
from agent.observability import model_degrade as D
from agent.observability.events import EventStore


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv(ev.ENV_DIR, str(tmp_path / "events"))
    for name in (D.ENV_FALLBACK_CHAIN, D.ENV_FALLBACK_ENABLED):
        monkeypatch.delenv(name, raising=False)
    D.reset_chain_cache()
    ev.reset_event_stores()
    yield
    D.reset_chain_cache()
    ev.reset_event_stores()


@pytest.fixture
def store(tmp_path):
    return EventStore(str(tmp_path / "events" / "events.jsonl"))


class TestChainResolution:
    def test_default_chain_when_unconfigured(self):
        models, source = D.resolve_fallback_chain()
        assert source in ("default", "config.yaml")
        assert models

    def test_env_chain_wins(self, monkeypatch):
        monkeypatch.setenv(D.ENV_FALLBACK_CHAIN, "m-a, m-b ;m-c")
        D.reset_chain_cache()
        models, source = D.resolve_fallback_chain()
        assert models == ["m-a", "m-b", "m-c"] and source == "env"

    def test_explicit_chain(self):
        models, source = D.resolve_fallback_chain(["x", "", "y"])
        assert models == ["x", "y"] and source == "explicit"

    def test_next_fallback_skips_current(self):
        chain = ["gpt-4", "gpt-4o-mini"]
        assert D.next_fallback_model("gpt-4", chain) == "gpt-4o-mini"
        assert D.next_fallback_model("unknown", chain) == "gpt-4"

    def test_next_fallback_none_when_exhausted(self):
        assert D.next_fallback_model("only", ["only"]) == D.FALLBACK_NONE

    def test_fallback_disabled_by_default(self):
        assert D.fallback_enabled() is False

    def test_fallback_enabled_by_env(self, monkeypatch):
        monkeypatch.setenv(D.ENV_FALLBACK_ENABLED, "1")
        assert D.fallback_enabled() is True


class TestReportDegraded:
    def test_payload_has_from_to_reason_and_error_code(self, store):
        env = D.report_model_degraded(from_model="gpt-4", reason="timeout",
                                      store=store)
        assert env.type == "model.degraded"
        assert env.payload["from"] == "gpt-4"
        assert env.payload["to"]  # 解析出真实候选模型，非占位
        assert env.payload["reason"] == "timeout"
        assert env.payload["error_code"] == D.E_MODEL_DEGRADED == "E_MODEL_DEGRADED"

    def test_explicit_target_kept(self, store):
        env = D.report_model_degraded(from_model="a", to_model="b", reason="r",
                                      store=store)
        assert env.payload["to"] == "b"

    def test_marks_whether_fallback_attempted(self, store):
        env = D.report_model_degraded(from_model="a", reason="r", store=store)
        assert env.payload["fallback_attempted"] is False
        assert env.payload["fallback_available"] is True

    def test_idempotent_on_same_edge_and_reason(self, store):
        first = D.report_model_degraded(from_model="a", reason="boom", store=store)
        second = D.report_model_degraded(from_model="a", reason="boom", store=store)
        assert first is not None and second is None

    def test_no_candidate_marks_unavailable(self, store):
        env = D.report_model_degraded(from_model="a", reason="r", chain=["a"],
                                      store=store)
        assert env.payload["to"] == ""
        assert env.payload["fallback_available"] is False

    def test_trace_context_injected(self, store):
        from agent.observability.trace_v2 import TraceContext
        ctx = TraceContext(trace_id="tr", task_id="task-9", workspace_id="ws_z")
        token = ctx.enter()
        try:
            env = D.report_model_degraded(from_model="a", reason="r", store=store)
        finally:
            TraceContext.exit(token)
        assert env.payload["task_id"] == "task-9"
        assert env.payload["workspace_id"] == "ws_z"
        assert env.correlation_id == "tr"


class TestHandlePrimaryFailure:
    def test_default_does_not_switch_model(self, store):
        calls = []
        outcome = D.handle_primary_failure(
            model="gpt-4", reason="boom", retry=lambda m: calls.append(m),
            chain=["gpt-4", "gpt-4o-mini"], store=store)
        assert outcome["degraded"] is True
        assert outcome["attempted"] is False and outcome["succeeded"] is False
        assert calls == []                       # 默认不改运行时行为
        assert outcome["to"] == "gpt-4o-mini"

    def test_allow_retry_switches_and_returns_result(self, store):
        outcome = D.handle_primary_failure(
            model="gpt-4", reason="boom", retry=lambda m: f"ok:{m}",
            chain=["gpt-4", "gpt-4o-mini"], allow_retry=True, store=store)
        assert outcome["attempted"] is True and outcome["succeeded"] is True
        assert outcome["result"] == "ok:gpt-4o-mini"
        assert outcome["event"].payload["fallback_attempted"] is True

    def test_fallback_failure_recorded(self, store):
        def _retry(model):
            raise RuntimeError("fallback down")

        outcome = D.handle_primary_failure(
            model="gpt-4", reason="boom", retry=_retry,
            chain=["gpt-4", "gpt-4o-mini"], allow_retry=True, store=store)
        assert outcome["attempted"] is True and outcome["succeeded"] is False
        assert "fallback down" in outcome["error"]
        assert "fallback down" in outcome["event"].payload["fallback_error"]

    def test_no_retry_callable_still_reports(self, store):
        outcome = D.handle_primary_failure(model="gpt-4", reason="boom",
                                           chain=["gpt-4", "x"], store=store)
        assert outcome["attempted"] is False
        assert outcome["event"] is not None


class TestCallWithModelFallback:
    def test_first_candidate_wins(self, store):
        result = D.call_with_model_fallback(lambda m: f"ok:{m}",
                                            ["m1", "m2"], store=store)
        assert result == "ok:m1"
        assert ev.read_events(types=["model.degraded"],
                              directory=os.path.dirname(store.path)) == []

    def test_degrades_on_failure(self, store):
        def _call(model):
            if model == "m1":
                raise RuntimeError("m1 down")
            return f"ok:{model}"

        assert D.call_with_model_fallback(_call, ["m1", "m2"], store=store) == "ok:m2"
        events = ev.read_events(types=["model.degraded"],
                                directory=os.path.dirname(store.path))
        assert len(events) == 1
        assert events[0].payload["from"] == "m1"
        assert events[0].payload["to"] == "m2"

    def test_all_failed_raises_last_error(self, store):
        def _call(model):
            raise RuntimeError(f"{model} down")

        with pytest.raises(RuntimeError, match="m2 down"):
            D.call_with_model_fallback(_call, ["m1", "m2"], store=store)
        events = ev.read_events(types=["model.degraded"],
                                directory=os.path.dirname(store.path))
        assert len(events) == 2
        edges = {(e.payload["from"], e.payload["to"]) for e in events}
        assert ("m1", "m2") in edges
        assert ("m2", "") in edges          # 链尾：无后续候选

    def test_empty_chain_rejected(self):
        with pytest.raises(ValueError):
            D.call_with_model_fallback(lambda m: m, [], store=None)


class TestSummary:
    def test_summary_edges_and_reasons(self, tmp_path, store):
        D.report_model_degraded(from_model="gpt-4", to_model="gpt-4o-mini",
                                reason="APITimeoutError: x", store=store)
        D.report_model_degraded(from_model="gpt-4o-mini", to_model="gpt-3.5-turbo",
                                reason="ExternalServiceError: y", store=store)
        summary = D.degrade_summary(directory=os.path.dirname(store.path))
        assert summary["total"] == 2
        assert summary["edges"] == {"gpt-4 -> gpt-4o-mini": 1,
                                    "gpt-4o-mini -> gpt-3.5-turbo": 1}
        assert summary["reasons"] == {"APITimeoutError": 1, "ExternalServiceError": 1}
        assert summary["error_code"] == "E_MODEL_DEGRADED"
        assert "models" in summary["chain"]

    def test_empty_summary(self, tmp_path, store):
        summary = D.degrade_summary(directory=str(tmp_path / "events"))
        assert summary["total"] == 0 and summary["edges"] == {}
