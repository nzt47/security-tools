"""TASK-S5-02 §6.7 指标字典单测（`agent/eval/metrics.py`）

覆盖：
- 指标字典完整性（≥5 项可计算、每项都必须有 source/formula/target）；
- 每个指标的计算与**分子/分母/样本量**；
- 数据源缺位一律 ``None``（**不以 0 冒充**）与"样本不足只披露不考核"；
- S2-03 遗留 #4 的探索满意度**双列正式口径**与替代关系披露；
- 事件目录隔离到 `tmp_path`（不触碰运行时目录）。
"""

from __future__ import annotations

import os

import pytest

from agent.eval import metrics as M
from agent.observability import acr as ACR
from agent.observability import events as ev
from agent.observability import utc as UTC

DAY = "2026-09-10"
TS = f"{DAY}T10:00:00+08:00"


@pytest.fixture(autouse=True)
def isolated_events(tmp_path, monkeypatch):
    monkeypatch.setenv(ev.ENV_DIR, str(tmp_path / "events"))
    ev.reset_event_stores()
    UTC.reset_config_cache()
    yield str(tmp_path / "events")
    ev.reset_event_stores()
    UTC.reset_config_cache()


# ════════════════════════════════════════════════════════════
#  字典
# ════════════════════════════════════════════════════════════


class TestDictionary:
    def test_at_least_five_computable(self):
        computable = M.computable_metrics()
        assert len(computable) >= 5

    def test_every_spec_has_traceable_fields(self):
        for spec in M.METRIC_SPECS:
            assert spec.key and spec.name and spec.definition
            assert spec.formula and spec.source and spec.target
            assert spec.unit is not None

    def test_keys_unique_and_indexed(self):
        keys = [spec.key for spec in M.METRIC_SPECS]
        assert len(keys) == len(set(keys))
        assert set(M.METRIC_BY_KEY) == set(keys)

    def test_dictionary_serializable(self):
        payload = M.metric_dictionary()
        assert len(payload) == len(M.METRIC_SPECS)
        assert all(isinstance(row, dict) for row in payload)

    def test_framework_only_metrics_declare_disclosure(self):
        for spec in M.METRIC_SPECS:
            if not spec.computable:
                assert spec.disclosure, spec.key

    def test_contracts_are_declared(self):
        assert M.delegation_recovery_contract()["triple_fields"] == [
            "artifact", "trace", "reflection"]
        assert "hindsight_best" in M.routing_annotation_contract()["required"]


# ════════════════════════════════════════════════════════════
#  单指标
# ════════════════════════════════════════════════════════════


class TestSingleMetrics:
    def test_digest_throughput_insufficient_without_events(self):
        row = M.compute_digest_throughput([], window_days=7)
        assert row["value"] is None and row["status"] == M.STATUS_INSUFFICIENT
        assert row["samples"] == 0

    def test_digest_throughput_counts_and_normalizes(self, isolated_events):
        for capability in ("cp.a", "cp.b", "cp.a"):
            ev.emit(ev.EV_DIGEST_STAGE, {"capability_id": capability, "applied": True,
                                         "to_stage": "mirrored"}, ts=TS)
        ev.emit(ev.EV_DIGEST_STAGE, {"capability_id": "cp.c", "applied": False,
                                     "to_stage": "mirrored"}, ts=TS)
        rows = ev.iter_events(day=DAY)
        row = M.compute_digest_throughput(rows, window_days=7)
        assert row["value"] == 2.0 and row["numerator"] == 2
        doubled = M.compute_digest_throughput(rows, window_days=14)
        assert doubled["value"] == 1.0

    def test_internalization_rate_from_registry_stub(self):
        class _Stage:
            def __init__(self, value):
                self.value = value

        class _Evo:
            def __init__(self, stage):
                self.stage = _Stage(stage)

        class _Desc:
            def __init__(self, stage):
                self.evolution = _Evo(stage)

        class _Registry:
            def list(self):
                return [_Desc(s) for s in
                        ["borrowed", "internalized", "native", "deprecated"]]

        row = M.compute_internalization_rate(_Registry())
        assert row["value"] == 0.5 and row["numerator"] == 2 and row["denominator"] == 4
        assert row["status"] == M.STATUS_INSUFFICIENT  # 样本 4 < 20 → 只披露

    def test_internalization_rate_empty_registry(self):
        class _Empty:
            def list(self):
                return []

        row = M.compute_internalization_rate(_Empty())
        assert row["value"] is None and row["status"] == M.STATUS_UNAVAILABLE

    def test_internalization_rate_broken_registry(self):
        class _Boom:
            def list(self):
                raise RuntimeError("db locked")

        row = M.compute_internalization_rate(_Boom())
        assert row["value"] is None and "台账读取失败" in row["reason"]

    def test_delegation_recovery_framework_without_source(self):
        row = M.compute_delegation_recovery(None)
        assert row["value"] is None and row["status"] == M.STATUS_FRAMEWORK
        assert row["contract"]["source_owner"].startswith("S4-04")

    def test_delegation_disclosure_no_longer_blames_missing_link(self):
        """【2026-09-13 修正】披露不得再写成"委派链路未交付"（S4-04 已交付）

        链路缺位与样本缺位是两件事：前者是"还没造出来"，后者是"造好了但生产
        还没有调用点"。把后者写成前者会误导读者以为工程没做完。
        """
        disclosure = M.METRIC_BY_KEY["delegation_recovery"].disclosure
        assert "已交付" in disclosure
        assert "未交付" not in disclosure
        assert "样本缺位" in disclosure

    def test_delegation_recovery_with_rows(self):
        rows = [
            {"delegation_id": "d1", "capability_id": "cp.a", "artifact": True,
             "trace": "t1", "reflection": {"score": 1}},
            {"delegation_id": "d2", "capability_id": "cp.b", "artifact": True,
             "trace": "t2", "reflection": None},
        ]
        row = M.compute_delegation_recovery(rows)
        assert row["value"] == 0.5 and row["incomplete"] == ["d2"]
        assert row["target_met"] is False

    def test_skill_success_rate(self):
        rows = [{"passed": 9, "sampled": 10}, {"passed": 8, "sampled": 10}]
        row = M.compute_skill_success_rate(rows, upstream_rate=0.95)
        assert row["value"] == 0.85 and row["samples"] == 20
        assert row["required"] == pytest.approx(0.931)
        assert row["target_met"] is False

    def test_skill_success_rate_without_upstream(self):
        row = M.compute_skill_success_rate([{"passed": 1, "sampled": 2}])
        assert row["upstream_rate"] is None and row["target_met"] is None
        assert "暂不可判定" in row["disclosure"]

    def test_skill_success_rate_empty_ledger(self):
        row = M.compute_skill_success_rate([])
        assert row["value"] is None and row["status"] == M.STATUS_UNAVAILABLE

    def test_healing_latency_framework_without_events(self):
        row = M.compute_healing_latency([])
        assert row["value"] is None and row["status"] == M.STATUS_FRAMEWORK
        # 【2026-09-13 修正】披露文案**不得**再把 framework_only 归因于"无发射方"：
        # 发射方已交付（发射点见 self_healing/levels.py::emit_healing_triggered），
        # 事件缺位只说明"尚未发生被分级记录的自愈事件"。此处锁定新口径，防止回退。
        assert "emit_healing_triggered" in row["disclosure"]
        assert "不是缺发射方" in row["disclosure"]
        assert "无发射方" not in row["disclosure"]
        assert "emit_healing_triggered" in row["reason"]

    def test_healing_latency_with_events(self, isolated_events):
        ev.emit(ev.EV_HEALING_TRIGGERED, {"mttd_ms": 1000, "mttr_ms": 20000}, ts=TS)
        ev.emit(ev.EV_HEALING_TRIGGERED, {"mttd_ms": 3000, "mttr_ms": 40000}, ts=TS)
        row = M.compute_healing_latency(ev.iter_events(day=DAY))
        assert row["mttd_ms"] == 2000.0 and row["mttr_ms"] == 30000.0
        assert row["mttd_target_met"] is True and row["mttr_target_met"] is False

    def test_approval_decay_rate(self, isolated_events):
        # 注意：events.v1 按 event_id 幂等去重 → 同载荷同关联 id 只计一次，
        # 故这里用不同的 record_id 表达"三次真实审批"（与真实埋点一致）
        ev.emit(ev.EV_APPROVAL, {"kind": "auto_pass", "record_id": "r1"}, ts=TS)
        ev.emit(ev.EV_APPROVAL, {"kind": "auto_pass", "record_id": "r2"}, ts=TS)
        ev.emit(ev.EV_APPROVAL, {"kind": "approve", "record_id": "r3"}, ts=TS)
        row = M.compute_approval_decay_rate(ev.iter_events(day=DAY))
        assert row["value"] == pytest.approx(0.666667) and row["samples"] == 3
        assert row["by_kind"] == {"approve": 1, "auto_pass": 2}

    def test_approval_event_idempotency_dedupes(self, isolated_events):
        ev.emit(ev.EV_APPROVAL, {"kind": "auto_pass", "record_id": "same"}, ts=TS)
        ev.emit(ev.EV_APPROVAL, {"kind": "auto_pass", "record_id": "same"}, ts=TS)
        row = M.compute_approval_decay_rate(ev.iter_events(day=DAY))
        assert row["samples"] == 1

    def test_approval_decay_without_events(self):
        row = M.compute_approval_decay_rate([])
        assert row["value"] is None and row["status"] == M.STATUS_UNAVAILABLE

    def test_delegation_cycle_median(self, isolated_events):
        ev.emit(ev.EV_DIGEST_STAGE, {"capability_id": "cp.a", "applied": True,
                                     "to_stage": "borrowed"},
                ts="2026-09-01T10:00:00+08:00")
        ev.emit(ev.EV_DIGEST_STAGE, {"capability_id": "cp.a", "applied": True,
                                     "to_stage": "internalized"}, ts=TS)
        ev.emit(ev.EV_DIGEST_STAGE, {"capability_id": "cp.b", "applied": True,
                                     "to_stage": "internalized"}, ts=TS)
        row = M.compute_delegation_cycle_days(ev.iter_events())
        assert row["value"] == 9.0 and row["samples"] == 1
        assert row["pairs"][0]["capability_id"] == "cp.a"

    def test_delegation_cycle_without_pairs(self):
        row = M.compute_delegation_cycle_days([])
        assert row["value"] is None and row["status"] == M.STATUS_UNAVAILABLE

    def test_abandoned_rate(self, isolated_events):
        ACR.record_task_closed(task_id="t1", status="closed", intent="fix", ts=TS)
        ACR.record_task_closed(task_id="t2", status="failed", intent="fix", ts=TS)
        ACR.record_task_abandoned(task_id="t3", reason="user left", ts=TS)
        row = M.compute_abandoned_rate(ev.iter_events(day=DAY))
        assert row["value"] == pytest.approx(1 / 3)
        assert row["closed"] == 1 and row["failed"] == 1 and row["abandoned"] == 1

    def test_abandoned_rate_without_events(self):
        assert M.compute_abandoned_rate([])["value"] is None

    def test_abandoned_rate_dedupes_by_task(self, isolated_events):
        ACR.record_task_closed(task_id="t1", status="closed", ts=TS)
        ACR.record_task_closed(task_id="t1", status="closed", ts=TS)
        row = M.compute_abandoned_rate(ev.iter_events(day=DAY))
        assert row["samples"] == 1

    def test_routing_accuracy_framework_without_annotations(self):
        row = M.compute_routing_accuracy(None)
        assert row["value"] is None and row["status"] == M.STATUS_FRAMEWORK
        assert row["contract"]["annotators"] == ["human", "replay"]

    def test_routing_accuracy_with_annotations(self):
        rows = [{"task_id": "1", "chosen": "a", "hindsight_best": "a", "annotator": "human"},
                {"task_id": "2", "chosen": "a", "hindsight_best": "b", "annotator": "replay"},
                {"task_id": "3", "chosen": None, "hindsight_best": "b"}]
        row = M.compute_routing_accuracy(rows)
        assert row["value"] == 0.5 and row["samples"] == 2
        assert row["by_annotator"] == {"human": 1, "replay": 1}

    def test_utc_passthrough(self):
        row = M.compute_utc({"utc_cents_per_task": 12.5, "cost_normalized_cents": 25.0,
                             "tasks": {"closed_and_failed": 2}, "anchor_model": "m",
                             "utc_formula": "f", "cache_hit_rate": 0.5})
        assert row["value"] == 12.5 and row["denominator"] == 2
        assert M.compute_utc(None)["value"] is None


# ════════════════════════════════════════════════════════════
#  探索满意度（S2-03 #4 正式口径）
# ════════════════════════════════════════════════════════════


class TestExplorationSatisfaction:
    def test_dual_column_with_both_sources(self):
        acr_summary = {"exploration": {"tasks": 4, "closed_success": 3, "failed": 1,
                                       "satisfaction": 0.75}}
        feedback = {"like_count": 8, "dislike_count": 2}
        row = M.compute_exploration_satisfaction(acr_summary=acr_summary,
                                                 feedback_summary=feedback)
        assert row["thumbs_up_rate"] == 0.8
        assert row["closure_rate"] == 0.75
        assert row["feedback_available"] is True
        assert "代理" in row["substitution"] and "不得单独" in row["substitution"]
        assert row["scope"].startswith("explore/consult")

    def test_thumbs_column_empty_when_feedback_source_missing(self):
        acr_summary = {"exploration": {"tasks": 2, "closed_success": 1, "failed": 1,
                                       "satisfaction": 0.5}}
        row = M.compute_exploration_satisfaction(acr_summary=acr_summary)
        assert row["thumbs_up_rate"] is None and row["closure_rate"] == 0.5
        assert "不填 0 冒充" in row["reason"]

    def test_unavailable_without_any_source(self):
        row = M.compute_exploration_satisfaction(acr_summary={"exploration": {}},
                                                 feedback_summary=None)
        assert row["status"] == M.STATUS_UNAVAILABLE and row["value"] is None


# ════════════════════════════════════════════════════════════
#  窗口与周报
# ════════════════════════════════════════════════════════════


class TestWindowAndReport:
    def test_resolve_window_defaults(self):
        start, end = M.resolve_window(days=7, end=DAY)
        assert end == DAY and start == "2026-09-04"

    def test_resolve_window_explicit(self):
        assert M.resolve_window(start="2026-01-01", end="2026-01-31") == (
            "2026-01-01", "2026-01-31")

    def test_compute_metrics_full_shape(self, isolated_events):
        ACR.record_task_closed(task_id="t1", status="closed", intent="fix", ts=TS)
        ev.emit(ev.EV_APPROVAL, {"kind": "auto_pass"}, ts=TS)
        ev.emit(ev.EV_DIGEST_STAGE, {"capability_id": "cp.a", "applied": True,
                                     "to_stage": "mirrored"}, ts=TS)
        report = M.compute_metrics(start=DAY, end=DAY)
        assert report["schema"] == "eval.slo_weekly.v1"
        assert set(report["metrics"]) == set(M.METRIC_BY_KEY)
        assert report["metrics"]["approval_decay_rate"]["value"] == 1.0
        assert report["metrics"]["abandoned_rate"]["value"] == 0.0
        assert report["metrics"]["utc_cents_per_task"]["status"] in (
            M.STATUS_OK, M.STATUS_INSUFFICIENT, M.STATUS_UNAVAILABLE)
        assert "iter_events" in report["sources"]["events"]
        assert "不以 0 冒充" in report["traceability"]

    def test_compute_metrics_does_not_touch_data_dir(self, tmp_path):
        """缺省参数下不得读写运行时数据目录（不创建 data/eval 等落点）"""
        report = M.compute_metrics(start=DAY, end=DAY,
                                   events_dir=str(tmp_path / "events_none"))
        assert report["metrics"]["digest_throughput"]["value"] is None
        assert not os.path.exists(str(tmp_path / "events_none"))
        assert report["sources"]["feedback"] == "未接入（👍率列置空）"

    def test_render_markdown_traceable(self, isolated_events):
        report = M.compute_metrics(start=DAY, end=DAY)
        text = M.render_weekly_markdown(report)
        assert "数据源" in text and "公式" in text
        assert "达标" in text
        assert "✅" not in text and "❌" not in text
        assert "探索满意度（双列口径）" in text

    def test_write_weekly_report(self, tmp_path, isolated_events):
        report = M.compute_metrics(start=DAY, end=DAY)
        written = M.write_weekly_report(report, str(tmp_path / "r.json"),
                                        markdown_path=str(tmp_path / "r.md"))
        assert os.path.exists(written["json"]) and os.path.exists(written["markdown"])
        assert "指标周报" in open(written["markdown"], encoding="utf-8").read()

    def test_feedback_summary_from_missing_path_returns_none(self, tmp_path):
        target = tmp_path / "no_feedback"
        assert M.feedback_summary_from_path(str(target)) is None
        assert not target.exists()  # 不得因读取而创建目录
