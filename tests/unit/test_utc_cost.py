"""TASK-S2-03 UTC 成本埋点与归一 单元测试

覆盖范围（对齐任务书 §四 验收清单「cost 埋点 / 归一公式可复现 / 缓存命中不计 token」）：
- 归一公式可复现（主力模型锚价 + 系数表）；默认系数下与既有 cost_tracker 口径逐分一致
- 缓存命中不计 token（P7.1-18）
- 埋点字段完备（task_id/workspace_id/subject_id/model/tokens_in/out/retries/
  shadow_overhead/cents）
- 锚模型解析优先级（env > config.yaml > 默认）与系数覆盖（CP_UTC_COEFFICIENTS）
- 日 / 周 / 快照聚合与 UTC = 成本/任务数；§7 断食阈值输入量（baseline/ratio）
- 与既有成本监控数据源对账（reconcile_pricing / reconcile_cost_log）
"""

import json
import os

import pytest

from agent.observability import events as ev
from agent.observability import utc as U
from agent.observability.events import EventStore


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv(ev.ENV_DIR, str(tmp_path / "events"))
    monkeypatch.setenv(U.ENV_ANCHOR_MODEL, "gpt-4")
    for name in (U.ENV_COEFFICIENTS, U.ENV_PRICE_OVERRIDES):
        monkeypatch.delenv(name, raising=False)
    U.reset_config_cache()
    ev.reset_event_stores()
    yield
    U.reset_config_cache()
    ev.reset_event_stores()


@pytest.fixture
def store(tmp_path):
    return EventStore(str(tmp_path / "events" / "events.jsonl"))


# ════════════════════════════════════════════════════════════
#  1. 价格 / 锚 / 系数表
# ════════════════════════════════════════════════════════════


class TestPricesAndAnchor:
    def test_prices_come_from_existing_cost_monitor(self):
        from agent.model_router.cost_tracker import MODEL_COSTS
        assert U.model_costs() == MODEL_COSTS

    def test_unknown_model_uses_same_fallback_as_legacy(self):
        assert U.price_usd_per_1k("no-such-model") == U.DEFAULT_MODEL_PRICE_USD_PER_1K

    def test_anchor_from_env(self, monkeypatch):
        monkeypatch.setenv(U.ENV_ANCHOR_MODEL, "gpt-4o-mini")
        U.reset_config_cache()
        assert U.resolve_anchor_model() == ("gpt-4o-mini", "env")

    def test_anchor_from_config_yaml(self, monkeypatch):
        monkeypatch.delenv(U.ENV_ANCHOR_MODEL, raising=False)
        U.reset_config_cache()
        model, source = U.resolve_anchor_model()
        assert model and source in ("config.yaml", "default")

    def test_anchor_prices_in_cents(self):
        prices = U.anchor_prices_cents()
        assert prices["input"] == pytest.approx(3.0)      # gpt-4: 0.03 USD/1k
        assert prices["output"] == pytest.approx(6.0)

    def test_price_ratio_coefficient(self):
        coef = U.coefficient("gpt-4o-mini")               # 相对 gpt-4 锚
        assert coef["source"] == "price_ratio"
        assert coef["in"] == pytest.approx(0.00015 / 0.03)
        assert coef["out"] == pytest.approx(0.0006 / 0.06)
        assert U.coefficient("gpt-4")["in"] == pytest.approx(1.0)

    def test_coefficient_override(self, monkeypatch):
        monkeypatch.setenv(U.ENV_COEFFICIENTS, json.dumps(
            {"gpt-4o-mini": {"in": 0.5, "out": 0.25}}))
        coef = U.coefficient("gpt-4o-mini")
        assert coef == {"in": 0.5, "out": 0.25, "source": "override"}

    def test_bad_coefficient_json_ignored(self, monkeypatch):
        monkeypatch.setenv(U.ENV_COEFFICIENTS, "{not json}")
        assert U.coefficient("gpt-4o-mini")["source"] == "price_ratio"

    def test_coefficient_table_for_metric_dictionary(self):
        table = U.coefficient_table(["gpt-4", "gpt-4o-mini"])
        assert table["anchor_model"] == "gpt-4"
        assert set(table["models"]) == {"gpt-4", "gpt-4o-mini"}
        assert "price_usd_per_1k" in table["models"]["gpt-4"]


# ════════════════════════════════════════════════════════════
#  2. 归一公式
# ════════════════════════════════════════════════════════════


class TestNormalizeCost:
    def test_formula_is_reproducible_by_hand(self):
        calc = U.normalize_cost(1000, 500, "gpt-4")
        # gpt-4 锚价 3.0/6.0 cents per 1k，系数 1.0
        expected = 1000 / 1000 * 3.0 + 500 / 1000 * 6.0
        assert calc["cost_normalized_cents"] == pytest.approx(expected)

    def test_anchor_model_cost_equals_raw(self):
        calc = U.normalize_cost(1234, 567, "gpt-4")
        assert calc["cost_normalized_cents"] == pytest.approx(calc["cost_raw_cents"])

    def test_cheaper_model_scales_by_coefficient(self):
        calc = U.normalize_cost(1000, 1000, "gpt-4o-mini")
        assert calc["coefficient_source"] == "price_ratio"
        assert calc["cost_normalized_cents"] < calc["cost_raw_cents"] + 1e-9
        assert calc["cost_normalized_cents"] == pytest.approx(
            1000 / 1000 * 3.0 * (0.00015 / 0.03) + 1000 / 1000 * 6.0 * (0.0006 / 0.06))

    def test_cache_hit_bills_no_tokens(self):
        calc = U.normalize_cost(5000, 2000, "gpt-4", cache_hit=True)
        assert calc["billable_tokens_in"] == 0
        assert calc["billable_tokens_out"] == 0
        assert calc["cost_normalized_cents"] == 0.0
        assert calc["cost_raw_cents"] == 0.0
        # 原始 token 仍如实记录（只是不计费）
        assert calc["tokens_in"] == 5000 and calc["tokens_out"] == 2000

    def test_cache_hit_still_counts_shadow_overhead(self):
        calc = U.normalize_cost(5000, 2000, "gpt-4", cache_hit=True,
                                shadow_overhead_cents=1.5)
        assert calc["cost_normalized_cents"] == pytest.approx(1.5)

    def test_retry_tokens_added_only_when_explicit(self):
        base = U.normalize_cost(1000, 0, "gpt-4")
        retried = U.normalize_cost(1000, 0, "gpt-4", retry_tokens_in=1000)
        assert retried["cost_normalized_cents"] == pytest.approx(
            base["cost_normalized_cents"] * 2)

    def test_shadow_overhead_ms_recorded(self):
        calc = U.normalize_cost(0, 0, "gpt-4", shadow_overhead_ms=250.5,
                                shadow_overhead_cents=0.5)
        assert calc["shadow_overhead_ms"] == 250.5
        assert calc["cost_normalized_cents"] == pytest.approx(0.5)

    def test_negative_tokens_clamped(self):
        calc = U.normalize_cost(-10, -10, "gpt-4")
        assert calc["billable_tokens_in"] == 0 and calc["billable_tokens_out"] == 0

    def test_calibrated_coefficient_changes_result(self, monkeypatch):
        monkeypatch.setenv(U.ENV_COEFFICIENTS,
                           json.dumps({"gpt-4o-mini": {"in": 1.0, "out": 1.0}}))
        calc = U.normalize_cost(1000, 1000, "gpt-4o-mini")
        assert calc["coefficient_source"] == "override"
        assert calc["cost_normalized_cents"] == pytest.approx(3.0 + 6.0)


class TestReconcile:
    def test_pricing_consistent_with_existing_monitor(self):
        report = U.reconcile_pricing(["gpt-4", "gpt-3.5-turbo", "gpt-4o-mini",
                                      "unknown-probe"])
        assert report["consistent"] is True
        assert all(row["delta_cents"] == 0 for row in report["rows"])

    def test_pricing_diverges_after_calibration(self, monkeypatch):
        monkeypatch.setenv(U.ENV_COEFFICIENTS,
                           json.dumps({"gpt-4o-mini": {"in": 2.0, "out": 2.0}}))
        report = U.reconcile_pricing(["gpt-4o-mini"])
        assert report["consistent"] is False
        assert report["rows"][0]["coefficient_source"] == "override"

    def test_cost_log_missing_is_honest(self, tmp_path):
        report = U.reconcile_cost_log(str(tmp_path / "nope.jsonl"))
        assert report["rows"] == 0 and report["consistent"] is None

    def test_cost_log_reconciliation(self, tmp_path):
        path = tmp_path / "cost_log.jsonl"
        path.write_text(json.dumps({
            "model": "gpt-4", "input_tokens": 1000, "output_tokens": 500,
            "cost_usd": 0.06, "timestamp": "2026-09-09T10:00:00"}) + "\n",
            encoding="utf-8")
        report = U.reconcile_cost_log(str(path))
        assert report["rows"] == 1
        assert report["legacy_cents"] == pytest.approx(6.0)
        assert report["consistent"] is True


# ════════════════════════════════════════════════════════════
#  3. 埋点字段（§6.6 cost）
# ════════════════════════════════════════════════════════════


class TestCostEvent:
    def test_payload_has_all_spec_fields(self, store):
        env = U.record_cost(model="gpt-4", provider="openai", source="chat",
                            tokens_in=1000, tokens_out=500, retries=2,
                            shadow_overhead_ms=10.0, shadow_overhead_cents=0.25,
                            task_id="t1", interaction_id="llm-1",
                            duration_ms=123.4, store=store)
        payload = env.payload
        for field in ("task_id", "workspace_id", "subject_id", "model",
                      "tokens_in", "tokens_out", "retries", "shadow_overhead_ms",
                      "shadow_overhead_cents", "cost_raw_cents",
                      "cost_normalized_cents", "cache_hit", "anchor_model"):
            assert field in payload, field
        assert payload["tokens_in"] == 1000 and payload["tokens_out"] == 500
        assert payload["retries"] == 2

    def test_cache_hit_flag_kept_in_payload(self, store):
        env = U.record_cost(model="gpt-4", tokens_in=10, tokens_out=10,
                            cache_hit=True, interaction_id="llm-2", store=store)
        assert env.payload["cache_hit"] is True
        assert env.payload["billable_tokens_in"] == 0

    def test_interaction_idempotent(self, store):
        first = U.record_cost(model="gpt-4", tokens_in=1, interaction_id="x1",
                              store=store)
        second = U.record_cost(model="gpt-4", tokens_in=99, interaction_id="x1",
                               store=store)
        assert first is not None and second is None

    def test_distinct_interactions_both_counted(self, store):
        assert U.record_cost(model="gpt-4", interaction_id="a", store=store)
        assert U.record_cost(model="gpt-4", interaction_id="b", store=store)
        assert len(ev.read_events(types=["cost"],
                                  directory=os.path.dirname(store.path))) == 2

    def test_task_id_injected_from_trace_context(self, store, monkeypatch):
        from agent.observability.trace_v2 import TraceContext
        ctx = TraceContext(trace_id="tr-1", task_id="task-1",
                           workspace_id="ws_abc", subject_id="sess-1")
        token = ctx.enter()
        try:
            env = U.record_cost(model="gpt-4", tokens_in=1, store=store)
        finally:
            TraceContext.exit(token)
        assert env.payload["task_id"] == "task-1"
        assert env.payload["workspace_id"] == "ws_abc"
        assert env.payload["subject_id"] == "sess-1"
        assert env.correlation_id == "tr-1"


# ════════════════════════════════════════════════════════════
#  4. 聚合（日 / 周 / 快照）
# ════════════════════════════════════════════════════════════


def _seed(store, day="2026-09-09"):
    ts = f"{day}T10:00:00.000+08:00"
    U.record_cost(model="gpt-4", tokens_in=1000, tokens_out=500,
                  interaction_id=f"a@{day}", ts=ts, store=store)
    U.record_cost(model="gpt-4o-mini", tokens_in=10000, tokens_out=10000,
                  cache_hit=True, interaction_id=f"b@{day}", ts=ts, store=store)
    from agent.observability import acr
    acr.record_task_closed(task_id=f"t1@{day}", status=acr.STATUS_CLOSED,
                           intent="fix", ts=ts, store=store)
    acr.record_task_closed(task_id=f"t2@{day}", status=acr.STATUS_FAILED,
                           intent="fix", ts=ts, store=store)
    acr.record_task_closed(task_id=f"t3@{day}", status=acr.STATUS_CLOSED,
                           intent="explore", ts=ts, store=store)


class TestAggregation:
    def test_daily_totals_and_utc(self, tmp_path, store):
        _seed(store)
        row = U.utc_daily("2026-09-09", directory=str(tmp_path / "events"))
        # 1000*3/1000 + 500*6/1000 = 3 + 3 = 6 cents；缓存命中不计 token
        assert row["cost_normalized_cents"] == pytest.approx(6.0)
        assert row["cost_raw_cents"] == pytest.approx(6.0)
        assert row["llm_calls"] == 2 and row["cache_hits"] == 1
        assert row["billable_tokens_in"] == 1000
        assert row["tasks"]["closed_and_failed"] == 3
        assert row["tasks"]["acr_cohort"] == 2
        assert row["utc_cents_per_task"] == pytest.approx(2.0)
        assert row["utc_cents_per_task_acr_cohort"] == pytest.approx(3.0)

    def test_daily_by_model(self, tmp_path, store):
        _seed(store)
        row = U.utc_daily("2026-09-09", directory=str(tmp_path / "events"))
        assert set(row["by_model"]) == {"gpt-4", "gpt-4o-mini"}
        assert row["by_model"]["gpt-4o-mini"]["cache_hits"] == 1

    def test_empty_day_utc_is_none(self, tmp_path, store):
        row = U.utc_daily("2026-09-01", directory=str(tmp_path / "events"))
        assert row["utc_cents_per_task"] is None
        assert row["llm_calls"] == 0

    def test_weekly_window(self, tmp_path, store):
        _seed(store, "2026-09-08")
        _seed(store, "2026-09-09")
        weekly = U.utc_weekly("2026-09-09", directory=str(tmp_path / "events"))
        assert weekly["iso_week"] == "2026-W37"
        assert weekly["window"] == {"start": "2026-09-07", "end": "2026-09-13"}
        assert weekly["llm_calls"] == 4
        assert weekly["cost_normalized_cents"] == pytest.approx(12.0)

    def test_weekly_excludes_other_weeks(self, tmp_path, store):
        _seed(store, "2026-09-01")
        weekly = U.utc_weekly("2026-09-09", directory=str(tmp_path / "events"))
        assert weekly["llm_calls"] == 0

    def test_snapshot_baseline_and_ratio(self, tmp_path, store):
        from datetime import date, timedelta

        from agent.observability import acr
        today = date.today()
        # 今日成本 6 cents；前两日各 6 cents → 基线 6 → ratio 1.0
        U.record_cost(model="gpt-4", tokens_in=1000, tokens_out=500,
                      interaction_id="today", store=store)
        for offset in (1, 2):
            past = (today - timedelta(days=offset)).isoformat()
            acr.record_task_closed(task_id=f"b{offset}", status=acr.STATUS_CLOSED,
                                   ts=f"{past}T10:00:00+08:00", store=store)
            U.record_cost(model="gpt-4", tokens_in=1000, tokens_out=500,
                          interaction_id=f"prev{offset}", store=store,
                          ts=f"{past}T10:00:00+08:00")
        snapshot = U.utc_snapshot(days=3, directory=str(tmp_path / "events"))
        assert snapshot["today"]["cost_normalized_cents"] == pytest.approx(6.0)
        assert snapshot["baseline_cents_per_day"] == pytest.approx(6.0)
        assert snapshot["ratio"] == pytest.approx(1.0)
        assert snapshot["thresholds"]["fasting_in"] == 1.3
        assert snapshot["thresholds"]["fasting_out"] == 1.1
        assert snapshot["anchor_model"] == "gpt-4"

    def test_snapshot_writes_file(self, tmp_path, store):
        _seed(store)
        target = str(tmp_path / "utc.json")
        U.write_utc_snapshot(target, days=2, directory=str(tmp_path / "events"))
        assert "utc_cents_per_task" in open(target, encoding="utf-8").read()

    def test_window_bounds(self, tmp_path, store):
        _seed(store, "2026-09-08")
        _seed(store, "2026-09-10")
        row = U.utc_window(start="2026-09-08", end="2026-09-09",
                           directory=str(tmp_path / "events"))
        assert row["llm_calls"] == 2

    def test_replay_not_double_counted(self, tmp_path, store):
        _seed(store)
        _seed(store)
        row = U.utc_daily("2026-09-09", directory=str(tmp_path / "events"))
        assert row["llm_calls"] == 2
        assert row["cost_normalized_cents"] == pytest.approx(6.0)

    def test_abandoned_tasks_excluded_from_utc_denominator(self, tmp_path, store):
        from agent.observability import acr
        acr.record_task_closed(task_id="t1", status=acr.STATUS_CLOSED,
                               intent="fix", store=store)
        acr.record_task_abandoned(task_id="t2", store=store)
        U.record_cost(model="gpt-4", tokens_in=1000, interaction_id="c",
                      store=store)
        row = U.utc_daily(directory=str(tmp_path / "events"))
        assert row["tasks"]["closed_and_failed"] == 1
        assert row["tasks"]["abandoned"] == 1
