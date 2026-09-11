"""TASK-S5-02 L2 基线与难度拟合单测（`agent/eval/baseline.py` / `calibration.py`）

覆盖：
- 三路数据源的**复用性**与缺位时的如实标注（不创建运行时目录、不以 0 冒充）；
- 样本充分性判定与缺口清单；
- Owner 裁定 C 的触发条件状态（L2 就绪 = unlocked；成本样本不足单独披露）；
- S2-03 遗留 #3：难度权重拟合的可复现性 + **四闸全绿**才允许切换（默认只披露不考核）。
"""

from __future__ import annotations

import json
import os
import time

import pytest

from agent.eval import baseline as B
from agent.eval import calibration as CAL
from agent.eval import cases as C
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


def _write_ledger(directory: str, *, rows: int = 3, sampled: int = 10,
                  passed: int = 9) -> str:
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "shadow_ledger.jsonl")
    now = time.time()
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for index in range(rows):
            fh.write(json.dumps({
                "kind": "shadow_run", "capability_id": "cp.builtin.read_file",
                "generated_at": now - index * 86400, "allowed": True, "budget": 5,
                "sampled": sampled, "passed": passed, "negative": 0,
                "judge_kind": "rule", "degradation": "ok",
                "p99_wall_candidate_ms": 12.5 + index,
                "p99_wall_upstream_ms": 20.0,
                "shadow_version": "s3-03.1"}, ensure_ascii=False) + "\n")
    return path


# ════════════════════════════════════════════════════════════
#  三路数据源
# ════════════════════════════════════════════════════════════


class TestShadowInputs:
    def test_without_dir_is_unavailable_and_creates_nothing(self, tmp_path):
        probe = tmp_path / "shadow_probe"
        result = B.shadow_inputs(shadow_dir="")
        assert result["available"] is False and result["rows"] == 0
        assert result["p99_wall_candidate_ms"] is None
        assert "不触碰运行时目录" in result["reason"]
        assert not probe.exists()

    def test_with_ledger_rows(self, tmp_path):
        directory = str(tmp_path / "shadow")
        _write_ledger(directory, rows=3, sampled=10, passed=9)
        result = B.shadow_inputs(shadow_dir=directory)
        assert result["available"] and result["rows"] == 3
        assert result["pass_rate"] == 0.9
        assert result["p99_wall_candidate_ms"] == 14.5     # 取最大（保守）
        assert result["p99_wall_upstream_ms"] == 20.0
        assert result["clock"].startswith("wall_clock")
        cap = result["by_capability"]["cp.builtin.read_file"]
        assert cap["runs"] == 3 and cap["daily_average_samples"] == 10.0
        assert cap["adequate_for_calibration"] is False

    def test_empty_ledger_reports_reason(self, tmp_path):
        directory = str(tmp_path / "shadow_empty")
        os.makedirs(directory, exist_ok=True)
        result = B.shadow_inputs(shadow_dir=directory)
        assert result["available"] is False and "为空" in result["reason"]


class TestTraceInputs:
    def test_explicit_db_path(self, tmp_path):
        result = B.trace_inputs(trace_db=str(tmp_path / "traces.db"))
        assert result["available"] is True and result["total"] == 0
        assert result["min_samples_per_capability"] == B.MIN_SAMPLES_PER_CAPABILITY

    def test_missing_default_db_is_not_created(self, tmp_path, monkeypatch):
        from agent.observability import trace_v2
        target = str(tmp_path / "missing" / "traces.db")
        monkeypatch.setattr(trace_v2, "_DEFAULT_DB_PATH", target, raising=True)
        result = B.trace_inputs()
        assert result["available"] is False and result["total"] == 0
        assert not os.path.exists(target)


class TestCostInputs:
    def test_window_passthrough_and_adequacy(self):
        from agent.observability import acr as ACR
        UTC.record_cost(model="gpt-4o-mini", tokens_in=1000, tokens_out=500,
                        interaction_id="i1", ts=TS)
        ACR.record_task_closed(task_id="t1", status="closed", intent="fix", ts=TS)
        result = B.cost_inputs(start=DAY, end=DAY)
        assert result["llm_calls"] == 1
        assert result["cost_samples"] == 1
        assert result["cost_samples_adequate"] is False
        assert result["utc_cents_per_task"] == pytest.approx(
            result["cost_normalized_cents"])
        assert "utc.utc_window" in result["source"]

    def test_cost_without_tasks_has_no_utc(self):
        """只有成本事件、没有任务收尾 → UTC 分母为 0 → None（不以 0 冒充）"""
        UTC.record_cost(model="gpt-4o-mini", tokens_in=1000, interaction_id="i1", ts=TS)
        result = B.cost_inputs(start=DAY, end=DAY)
        assert result["llm_calls"] == 1 and result["utc_cents_per_task"] is None

    def test_no_events(self):
        result = B.cost_inputs(start=DAY, end=DAY)
        assert result["cost_samples"] == 0
        assert result["utc_cents_per_task"] is None


# ════════════════════════════════════════════════════════════
#  基线组装
# ════════════════════════════════════════════════════════════


class TestBuildBaseline:
    def test_l2_dataset_ready_but_sources_missing(self, tmp_path):
        snapshot = B.build_l2_baseline(days=1, start=DAY, end=DAY,
                                       shadow_dir=str(tmp_path / "none"),
                                       trace_db=str(tmp_path / "t.db"))
        assert snapshot["schema"] == B.BASELINE_SCHEMA
        assert snapshot["caseset"]["l2_dataset_cases"] == 50
        assert snapshot["caseset"]["caseset_sha256"]
        assert set(snapshot["caseset"]["by_scenario"]) >= set(C.SEED_SCENARIOS)
        trigger = snapshot["calibration_trigger"]
        assert trigger["l2_dataset_ready"] is True
        assert trigger["status"] == "unlocked"
        assert trigger["cost_samples_adequate"] is False
        assert "不声称" in trigger["what_is_not_claimed"]
        assert snapshot["sample_adequacy"]["adequate"] is False
        assert snapshot["sample_adequacy"]["shortfall"]
        assert snapshot["clock"]["case_run"].startswith("wall_clock")

    def test_blocked_without_l2_dataset(self, tmp_path):
        snapshot = B.build_l2_baseline(days=1, start=DAY, end=DAY,
                                       case_set=C.EvalCaseSet(layer="L2", cases=()),
                                       shadow_dir=str(tmp_path / "none"),
                                       trace_db=str(tmp_path / "t.db"))
        trigger = snapshot["calibration_trigger"]
        assert trigger["l2_dataset_ready"] is False and trigger["status"] == "blocked"

    def test_metrics_and_sources_embedded(self, tmp_path):
        snapshot = B.build_l2_baseline(days=1, start=DAY, end=DAY,
                                       shadow_dir=str(tmp_path / "none"),
                                       trace_db=str(tmp_path / "t.db"))
        assert set(snapshot["metrics"]) >= {"digest_throughput", "abandoned_rate"}
        assert "acr" in snapshot["metric_sources"]

    def test_write_load_roundtrip_and_markdown(self, tmp_path):
        snapshot = B.build_l2_baseline(days=1, start=DAY, end=DAY,
                                       shadow_dir=str(tmp_path / "none"),
                                       trace_db=str(tmp_path / "t.db"))
        path = B.write_l2_baseline(snapshot, str(tmp_path / "l2.json"))
        loaded = B.load_l2_baseline(path)
        assert loaded["caseset"]["caseset_sha256"] == snapshot["caseset"]["caseset_sha256"]
        text = B.baseline_markdown(loaded)
        assert "L2 Core-50 基线快照" in text
        assert "校准触发条件" in text

    def test_load_missing_or_corrupt(self, tmp_path):
        assert B.load_l2_baseline(str(tmp_path / "nope.json")) == {}
        bad = tmp_path / "bad.json"
        bad.write_text("{bad", encoding="utf-8")
        assert B.load_l2_baseline(str(bad)) == {}

    def test_write_refuses_anchor_dir(self, tmp_path):
        from agent.eval import anchor as A
        snapshot = B.build_l2_baseline(days=1, start=DAY, end=DAY,
                                       shadow_dir=str(tmp_path / "none"),
                                       trace_db=str(tmp_path / "t.db"))
        with pytest.raises(A.AnchorReadOnlyError):
            B.write_l2_baseline(snapshot, os.path.join(A.DEFAULT_ANCHOR_DIR, "x.json"))

    def test_default_path_is_runtime_artifact_dir(self):
        assert B.default_baseline_path().endswith(
            os.path.join("data", "eval", "l2_baseline.json"))


# ════════════════════════════════════════════════════════════
#  S2-03 #3 难度权重拟合与切换
# ════════════════════════════════════════════════════════════


def _observations(per_stratum: int = 25) -> list:
    rows = []
    for difficulty, weight in (("easy", 0.2), ("medium", 1.0), ("hard", 2.0)):
        for index in range(per_stratum):
            rows.append({"task_id": f"{difficulty}-{index}", "difficulty": difficulty,
                         "intervened": weight > 0, "intervention_weight": weight,
                         "duration_ms": 100.0, "cost_cents": 1.0})
    return rows


class TestDifficultyFit:
    def test_no_observations_refuses_weights(self):
        fit = CAL.fit_difficulty_weights([])
        assert fit.status == "no_observations" and fit.weights == {}
        assert fit.usable is False

    def test_zero_signal_refuses_weights(self):
        rows = [{"difficulty": d, "intervention_weight": 0.0}
                for d in ("easy", "medium", "hard")] * 25
        fit = CAL.fit_difficulty_weights(rows)
        assert fit.status == "no_signal" and fit.weights == {}
        assert "等权" in fit.notes

    def test_insufficient_samples_not_usable(self):
        fit = CAL.fit_difficulty_weights(_observations(per_stratum=3))
        assert fit.status == "insufficient_samples" and fit.usable is False
        assert fit.weights  # 仍给出权重供披露
        assert "不得" in fit.notes

    def test_fitted_weights_are_relative_and_reproducible(self):
        rows = _observations(per_stratum=25)
        first = CAL.fit_difficulty_weights(rows, now="2026-09-11T00:00:00+0800")
        second = CAL.fit_difficulty_weights(rows, now="2026-09-11T00:00:00+0800")
        assert first.usable is True and first.status == "fitted"
        assert first.to_dict() == second.to_dict()
        assert first.weights["easy"] < first.weights["medium"] < first.weights["hard"]
        assert first.samples_by_difficulty == {"easy": 25, "hard": 25, "medium": 25}

    def test_fit_carries_l2_baseline_hash(self):
        baseline = {"caseset": {"caseset_sha256": "abc"}, "window": {"start": "a"}}
        fit = CAL.fit_difficulty_weights(_observations(), l2_baseline=baseline)
        assert fit.l2_caseset_sha256 == "abc"

    def test_write_load_roundtrip(self, tmp_path):
        fit = CAL.fit_difficulty_weights(_observations())
        path = CAL.write_fit(fit, str(tmp_path / "fit.json"))
        loaded = CAL.load_fit(path)
        assert loaded is not None and loaded.to_dict() == fit.to_dict()

    def test_load_missing_corrupt_or_wrong_version(self, tmp_path):
        assert CAL.load_fit(str(tmp_path / "nope.json")) is None
        bad = tmp_path / "bad.json"
        bad.write_text("{bad", encoding="utf-8")
        assert CAL.load_fit(str(bad)) is None
        wrong = tmp_path / "wrong.json"
        wrong.write_text(json.dumps({"version": "v0"}), encoding="utf-8")
        assert CAL.load_fit(str(wrong)) is None

    def test_write_refuses_anchor_dir(self, tmp_path):
        from agent.eval import anchor as A
        fit = CAL.fit_difficulty_weights(_observations())
        with pytest.raises(A.AnchorReadOnlyError):
            CAL.write_fit(fit, os.path.join(A.DEFAULT_ANCHOR_DIR, "fit.json"))

    def test_observations_from_events(self):
        from agent.observability import acr as ACR
        ev.emit(ev.EV_INTERVENTION, {"task_id": "t1", "kind": "approve",
                                     "weight": 1.0}, ts=TS)
        ACR.record_task_closed(task_id="t1", status="closed", intent="fix",
                               difficulty="hard", ts=TS)
        ACR.record_task_closed(task_id="t2", status="closed", intent="fix",
                               difficulty="easy", ts=TS)
        rows = CAL.difficulty_observations(ev.iter_events(day=DAY))
        by_id = {row["task_id"]: row for row in rows}
        assert by_id["t1"]["intervention_weight"] == 1.0
        assert by_id["t2"]["intervention_weight"] == 0.0
        assert by_id["t1"]["difficulty"] == "hard"


class TestSwitchGate:
    def _baseline(self, sha: str = "abc") -> dict:
        return {"caseset": {"caseset_sha256": sha},
                "calibration_trigger": {"l2_dataset_ready": True}}

    def test_default_blocked_and_disclosed(self):
        status = CAL.switch_status(fit=CAL.fit_difficulty_weights(_observations()),
                                   l2_baseline=self._baseline(), env={})
        assert status["allowed"] is False
        assert status["mode"].startswith("disclose_only")
        assert "只披露不考核" in status["disclosure"]
        assert any("开关" in reason for reason in status["reasons"])

    def test_all_gates_green_allows(self):
        fit = CAL.fit_difficulty_weights(_observations(), l2_baseline=self._baseline())
        status = CAL.switch_status(fit=fit, l2_baseline=self._baseline(),
                                   env={CAL.FIT_ENV: "1"})
        assert status["allowed"] is True and status["mode"].startswith("assess")
        assert "考核" in status["disclosure"]
        assert all(status["gates"].values())

    def test_missing_baseline_blocks(self):
        fit = CAL.fit_difficulty_weights(_observations())
        status = CAL.switch_status(fit=fit, l2_baseline=None, env={CAL.FIT_ENV: "1"})
        assert status["allowed"] is False
        assert status["gates"]["l2_baseline_ready"] is False
        assert any("基线未就绪" in reason for reason in status["reasons"])

    def test_baseline_version_change_invalidates_fit(self):
        fit = CAL.fit_difficulty_weights(_observations(), l2_baseline=self._baseline("old"))
        status = CAL.switch_status(fit=fit, l2_baseline=self._baseline("new"),
                                   env={CAL.FIT_ENV: "1"})
        assert status["allowed"] is False
        assert status["gates"]["fit_matches_baseline"] is False

    def test_unusable_fit_blocks(self):
        fit = CAL.fit_difficulty_weights(_observations(per_stratum=2),
                                         l2_baseline=self._baseline())
        status = CAL.switch_status(fit=fit, l2_baseline=self._baseline(),
                                   env={CAL.FIT_ENV: "1"})
        assert status["allowed"] is False and status["gates"]["fit_usable"] is False

    def test_difficulty_weight_is_one_when_blocked(self):
        fit = CAL.fit_difficulty_weights(_observations())
        assert CAL.difficulty_weight("hard", fit=fit, env={}) == 1.0
        assert CAL.difficulty_weight("hard", fit=fit, l2_baseline=None, env={}) == 1.0

    def test_difficulty_weight_uses_fit_when_allowed(self):
        fit = CAL.fit_difficulty_weights(_observations(), l2_baseline=self._baseline())
        weight = CAL.difficulty_weight("hard", fit=fit, l2_baseline=self._baseline(),
                                       env={CAL.FIT_ENV: "1"})
        assert weight == pytest.approx(fit.weights["hard"])
        assert CAL.difficulty_weight("unknown", fit=fit, l2_baseline=self._baseline(),
                                     env={CAL.FIT_ENV: "1"}) == 1.0

    def test_checklist_has_action_verify_rollback(self):
        checklist = CAL.switch_checklist()
        assert len(checklist) >= 4
        assert all(item["action"] and item["verify"] and item["rollback"]
                   for item in checklist)
        assert any("CP_ACR_DIFFICULTY_FIT" in item["action"] for item in checklist)

    def test_calibration_report_shape(self):
        report = CAL.calibration_report(observations=_observations(),
                                        l2_baseline=self._baseline())
        assert report["s2_03_item"].startswith("#3")
        assert report["fit"]["status"] == "fitted"
        assert report["gate"]["allowed"] is False
        assert report["disclosure"]
