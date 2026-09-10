"""TASK-S2-03 ACR 埋点与汇总视图 单元测试

覆盖范围（对齐任务书 §四 验收清单「ACR 计数口径精确」）：
- §6.1 七项口径取值（Approve=1 / 条件=0.5 / 自动放行=0 / 超时 Deny=2 / 重跑=1 /
  逃逸=1 / 查看=0）逐项断言
- 分母 = closed + failed；**排除 explore/consult**；abandoned 单列
- 探索满意度（P7.1-16）单列
- intent / difficulty / fatigue_bucket 启发式分类
- intervention 事件幂等（重放不重复计数）
- 按日 / 按 ISO 周视图
- reject 等扩展项不污染 §6.1 分子
"""

import os

import pytest

from agent.observability import acr as A
from agent.observability import events as ev
from agent.observability.events import EventStore


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv(ev.ENV_DIR, str(tmp_path / "events"))
    ev.reset_event_stores()
    yield
    ev.reset_event_stores()


@pytest.fixture
def store(tmp_path):
    return EventStore(str(tmp_path / "events" / "events.jsonl"))


# ════════════════════════════════════════════════════════════
#  1. §6.1 口径
# ════════════════════════════════════════════════════════════


class TestWeights:
    def test_seven_core_weights_exact(self):
        assert A.INTERVENTION_WEIGHTS["approve"] == 1.0
        assert A.INTERVENTION_WEIGHTS["conditional"] == 0.5
        assert A.INTERVENTION_WEIGHTS["auto_pass"] == 0.0
        assert A.INTERVENTION_WEIGHTS["timeout_deny"] == 2.0
        assert A.INTERVENTION_WEIGHTS["rerun"] == 1.0
        assert A.INTERVENTION_WEIGHTS["escape"] == 1.0
        assert A.INTERVENTION_WEIGHTS["view"] == 0.0
        assert set(A.CORE_INTERVENTION_WEIGHTS) == {
            "approve", "conditional", "auto_pass", "timeout_deny",
            "rerun", "escape", "view"}

    def test_weight_lookup(self):
        assert A.intervention_weight("approve") == 1.0
        assert A.intervention_weight("timeout_deny") == 2.0

    def test_unknown_kind_zero_and_strict_raises(self):
        assert A.intervention_weight("mystery") == 0.0
        with pytest.raises(A.ACRRuleError):
            A.intervention_weight("mystery", strict=True)

    def test_reject_is_extension_not_core(self):
        assert A.INTERVENTION_WEIGHTS["reject"] == 1.0
        assert "reject" not in A.CORE_INTERVENTION_WEIGHTS
        assert A.EXTENDED_INTERVENTION_WEIGHTS == {"reject": 1.0}


class TestClassifiers:
    @pytest.mark.parametrize("text,expected", [
        ("帮我修复这个报错", "fix"),
        ("解释一下 fail 的 traceback", "fix"),
        ("介绍一下这个原理", "explore"),
        ("看看这个仓库是怎么组织的", "explore"),
        ("你觉得该不该上线", "consult"),
        ("给点建议", "consult"),
        ("实现一个新的解析器", "build"),
        ("部署到生产", "operate"),
        ("", "other"),
    ])
    def test_classify_intent(self, text, expected):
        assert A.classify_intent(text) == expected

    def test_classify_intent_strips_control_chars(self):
        assert A.classify_intent("\x00\x01修复 bug") == "fix"

    def test_classify_difficulty_tiers(self):
        assert A.classify_difficulty("hi") == "easy"
        assert A.classify_difficulty("x" * 200) == "medium"
        assert A.classify_difficulty("x" * 700) == "hard"
        assert A.classify_difficulty("请重构这个模块") == "hard"

    def test_difficulty_explore_demoted(self):
        assert A.classify_difficulty("x" * 50, A.INTENT_EXPLORE) == "easy"

    @pytest.mark.parametrize("latency,expected", [
        (0, "instant"), (5000, "instant"), (59999, "quick"),
        (120000, "deliberate"), (700000, "slow"), (7200000, "stale"),
        (None, "unknown"), (-1, "unknown"),
    ])
    def test_fatigue_bucket(self, latency, expected):
        assert A.fatigue_bucket(latency) == expected


# ════════════════════════════════════════════════════════════
#  2. 埋点写入
# ════════════════════════════════════════════════════════════


class TestEmission:
    def test_record_intervention_uses_table_weight(self, store):
        env = A.record_intervention("approve", task_id="t1", store=store)
        assert env.payload["weight"] == 1.0
        assert env.payload["kind"] == "approve"
        assert env.payload["task_id"] == "t1"

    def test_record_intervention_explicit_weight_override(self, store):
        env = A.record_intervention("approve", weight=0.25, store=store)
        assert env.payload["weight"] == 0.25

    def test_record_intervention_idempotent(self, store):
        first = A.record_intervention("escape", source_ref="f1", store=store)
        second = A.record_intervention("escape", source_ref="f1", store=store)
        assert first is not None and second is None
        assert len(ev.read_events(directory=os.path.dirname(store.path))) == 1

    def test_record_approval_emits_both_events(self, store):
        envs = A.record_approval(kind="approve", record_id="a1", state="approved",
                                 latency_ms=5000, store=store)
        types = [e.type for e in envs if e is not None]
        assert types == ["approval", "intervention"]
        approval = envs[0]
        assert approval.payload["fatigue_bucket"] == "instant"
        assert approval.payload["latency_ms"] == 5000.0
        assert approval.payload["record_id"] == "a1"

    def test_record_approval_without_intervention(self, store):
        envs = A.record_approval(kind="merged", record_id="a1",
                                 count_intervention=False, store=store)
        assert envs[0] is not None and envs[1] is None

    def test_record_escape_emits_both_events(self, store):
        envs = A.record_escape(task_id="evil", reason="untracked_content_change",
                               capability_id="cp.governed.x.write", path="p",
                               digest_after="abc", store=store)
        assert [e.type for e in envs if e is not None] == ["escape", "intervention"]
        assert envs[0].payload["capability_id"] == "cp.governed.x.write"
        assert envs[1].payload["weight"] == 1.0

    def test_task_closed_derives_intervened_from_tally(self, store):
        A.record_intervention("rerun", correlation_id="task-1", store=store)
        env = A.record_task_closed(task_id="task-1", status=A.STATUS_CLOSED,
                                   intent=A.INTENT_FIX, correlation_id="task-1",
                                   store=store)
        assert env.payload["intervened"] is True
        assert env.payload["intervention_kind"] == "rerun"
        assert env.payload["intervention_kinds"] == ["rerun"]

    def test_task_closed_without_intervention(self, store):
        env = A.record_task_closed(task_id="t2", store=store)
        assert env.payload["intervened"] is False
        assert env.payload["intervention_kind"] == ""

    def test_task_closed_rejects_illegal_status_or_intent(self, store):
        with pytest.raises(A.ACRRuleError):
            A.record_task_closed(task_id="t", status="weird", store=store)
        with pytest.raises(A.ACRRuleError):
            A.record_task_closed(task_id="t", intent="weird", store=store)
        with pytest.raises(A.ACRRuleError):
            A.record_task_closed(task_id="t", difficulty="weird", store=store)

    def test_task_abandoned_uses_own_event_type(self, store):
        env = A.record_task_abandoned(task_id="t9", reason="用户放弃", store=store)
        assert env.type == ev.EV_TASK_ABANDONED
        assert env.payload["status"] == A.STATUS_ABANDONED
        assert env.payload["abandon_reason"] == "用户放弃"

    def test_task_closed_idempotent_per_task(self, store):
        assert A.record_task_closed(task_id="t1", store=store) is not None
        assert A.record_task_closed(task_id="t1", store=store) is None


# ════════════════════════════════════════════════════════════
#  3. 汇总视图口径
# ════════════════════════════════════════════════════════════


def _seed_window(store, day="2026-09-09"):
    """一个可复算的固定场景（全部落在同一天）

    任务：3 个 fix（2 成功 1 失败）+ 1 个 explore（成功）+ 1 个 consult（失败）
          + 1 个 abandoned
    介入：fix 任务上 approve(1) + timeout_deny(2)；explore 任务上 approve(1)；
          无法归属的 view(0)
    期待：分母 = 3（closed 2 + failed 1）；分子 = 1 + 2 = 3（explore 的 approve 被排除）
          ACR = 1.0；探索满意度 = 1/(1+1) = 0.5

    任务 id 带日期后缀 → 不同日期的种子互不幂等折叠（同一日重放才折叠）。
    """
    ts = f"{day}T10:00:00.000+08:00"
    tag = day
    A.record_task_closed(task_id=f"fix-1@{tag}", status=A.STATUS_CLOSED,
                         intent=A.INTENT_FIX, correlation_id=f"fix-1@{tag}",
                         ts=ts, store=store)
    A.record_task_closed(task_id=f"fix-2@{tag}", status=A.STATUS_CLOSED,
                         intent=A.INTENT_FIX, correlation_id=f"fix-2@{tag}",
                         ts=ts, store=store)
    A.record_task_closed(task_id=f"fix-3@{tag}", status=A.STATUS_FAILED,
                         intent=A.INTENT_FIX, correlation_id=f"fix-3@{tag}",
                         ts=ts, store=store)
    A.record_task_closed(task_id=f"exp-1@{tag}", status=A.STATUS_CLOSED,
                         intent=A.INTENT_EXPLORE, correlation_id=f"exp-1@{tag}",
                         ts=ts, store=store)
    A.record_task_closed(task_id=f"con-1@{tag}", status=A.STATUS_FAILED,
                         intent=A.INTENT_CONSULT, correlation_id=f"con-1@{tag}",
                         ts=ts, store=store)
    A.record_task_abandoned(task_id=f"ab-1@{tag}", correlation_id=f"ab-1@{tag}",
                            ts=ts, store=store)
    A.record_intervention("approve", task_id=f"fix-1@{tag}",
                          correlation_id=f"fix-1@{tag}", source_ref=f"a1@{tag}",
                          ts=ts, store=store)
    A.record_intervention("timeout_deny", task_id=f"fix-2@{tag}",
                          correlation_id=f"fix-2@{tag}", source_ref=f"a2@{tag}",
                          ts=ts, store=store)
    A.record_intervention("approve", task_id=f"exp-1@{tag}",
                          correlation_id=f"exp-1@{tag}", intent=A.INTENT_EXPLORE,
                          source_ref=f"a3@{tag}", ts=ts, store=store)
    A.record_intervention("view", source_ref=f"v1@{tag}", ts=ts, store=store)


class TestSummaryMath:
    def test_denominator_and_acr(self, tmp_path, store):
        _seed_window(store)
        summary = A.acr_daily("2026-09-09", directory=str(tmp_path / "events"))
        assert summary["denominator"] == {"closed": 2, "failed": 1, "total": 3}
        assert summary["excluded"]["tasks"] == 2
        assert summary["abandoned"] == 1
        assert summary["acr_numerator"] == 3.0
        assert summary["acr"] == 1.0

    def test_explore_consult_excluded_from_denominator(self, tmp_path, store):
        _seed_window(store)
        summary = A.acr_daily("2026-09-09", directory=str(tmp_path / "events"))
        assert summary["excluded"]["intents"] == ["explore", "consult"]
        # 分母只含 3 个 fix（2 成功 + 1 失败），不含 explore/consult/abandoned
        assert summary["denominator"]["closed"] == 2
        assert summary["denominator"]["failed"] == 1

    def test_exploration_satisfaction_listed_separately(self, tmp_path, store):
        _seed_window(store)
        summary = A.acr_daily("2026-09-09", directory=str(tmp_path / "events"))
        exploration = summary["exploration"]
        assert exploration["tasks"] == 2
        assert exploration["closed_success"] == 1
        assert exploration["failed"] == 1
        assert exploration["satisfaction"] == 0.5
        assert "closure_success_ratio" in exploration["satisfaction_basis"]

    def test_view_weight_zero_does_not_affect_acr(self, tmp_path, store):
        _seed_window(store)
        summary = A.acr_daily("2026-09-09", directory=str(tmp_path / "events"))
        assert summary["interventions"]["counts"]["view"] == 1
        assert summary["interventions"]["by_kind"]["view"] == 0.0
        assert summary["acr_numerator"] == 3.0

    def test_auto_pass_weight_zero_counted(self, tmp_path, store):
        A.record_task_closed(task_id="t1", intent=A.INTENT_FIX, store=store,
                             ts="2026-09-09T10:00:00.000+08:00")
        A.record_intervention("auto_pass", task_id="t1",
                              ts="2026-09-09T10:00:00.000+08:00", store=store)
        summary = A.acr_daily("2026-09-09", directory=str(tmp_path / "events"))
        assert summary["interventions"]["counts"]["auto_pass"] == 1
        assert summary["acr"] == 0.0

    def test_reject_extension_kept_out_of_numerator(self, tmp_path, store):
        ts = "2026-09-09T10:00:00.000+08:00"
        A.record_task_closed(task_id="t1", intent=A.INTENT_FIX, store=store, ts=ts)
        A.record_intervention("reject", task_id="t1", source_ref="r1", ts=ts, store=store)
        summary = A.acr_daily("2026-09-09", directory=str(tmp_path / "events"))
        assert summary["acr_numerator"] == 0.0
        assert summary["interventions"]["extension_weight"] == 1.0
        assert summary["interventions"]["counts"]["reject"] == 1

    def test_no_tasks_acr_is_none(self, tmp_path, store):
        A.record_intervention("approve", ts="2026-09-09T10:00:00.000+08:00", store=store)
        summary = A.acr_daily("2026-09-09", directory=str(tmp_path / "events"))
        assert summary["acr"] is None
        assert summary["interventions"]["unattributed_weight"] == 1.0

    def test_unknown_kind_disclosed(self, tmp_path, store):
        ts = "2026-09-09T10:00:00.000+08:00"
        A.record_task_closed(task_id="t1", intent=A.INTENT_FIX, store=store, ts=ts)
        A.record_intervention("mystery_kind", task_id="t1", ts=ts, store=store)
        summary = A.acr_daily("2026-09-09", directory=str(tmp_path / "events"))
        assert summary["unknown_kinds"] == {"mystery_kind": 1}
        assert summary["acr"] == 0.0        # 未知 kind 权重 0，不虚增分子

    def test_replay_not_double_counted(self, tmp_path, store):
        _seed_window(store)
        # 重放同一批事件（同 idempotency_key / 同内容）→ 不重复计数
        _seed_window(store)
        summary = A.acr_daily("2026-09-09", directory=str(tmp_path / "events"))
        assert summary["denominator"]["total"] == 3
        assert summary["acr_numerator"] == 3.0
        assert summary["tasks"]["total"] == 6

    def test_by_intent_and_difficulty(self, tmp_path, store):
        _seed_window(store)
        summary = A.acr_daily("2026-09-09", directory=str(tmp_path / "events"))
        assert summary["tasks"]["by_intent"]["fix"] == 3
        assert summary["tasks"]["by_intent"]["explore"] == 1
        assert sum(summary["tasks"]["by_difficulty"].values()) == 6

    def test_weights_echo_spec(self, tmp_path, store):
        summary = A.acr_daily("2026-09-09", directory=str(tmp_path / "events"))
        assert summary["weights"]["approve"] == 1.0
        assert summary["weights"]["timeout_deny"] == 2.0
        assert summary["weights_extension"] == {"reject": 1.0}
        assert "ACR =" in summary["acr_formula"]


class TestSummaryViews:
    def test_weekly_window_is_monday_to_sunday(self, tmp_path, store):
        # 2026-09-09 是周三 → 周一 2026-09-07，周日 2026-09-13
        _seed_window(store, "2026-09-09")
        weekly = A.acr_weekly("2026-09-09", directory=str(tmp_path / "events"))
        assert weekly["window"] == {"start": "2026-09-07", "end": "2026-09-13"}
        assert weekly["denominator"]["total"] == 3

    def test_day_outside_week_excluded(self, tmp_path, store):
        _seed_window(store, "2026-09-01")
        weekly = A.acr_weekly("2026-09-09", directory=str(tmp_path / "events"))
        assert weekly["denominator"]["total"] == 0

    def test_days_window_aggregates(self, tmp_path, store):
        _seed_window(store, "2026-09-08")
        _seed_window(store, "2026-09-09")
        window = A.acr_summary(days=2, until="2026-09-09",
                               directory=str(tmp_path / "events"))
        assert window["denominator"]["total"] == 6
        assert window["acr"] == 1.0

    def test_snapshot_shape_and_by_day(self, tmp_path, store):
        _seed_window(store)
        snapshot = A.acr_snapshot(days=3, directory=str(tmp_path / "events"))
        assert set(snapshot) == {"today", "daily", "week", "window"}
        assert len(snapshot["daily"]) == 3
        assert len(A.acr_by_day(days=2, directory=str(tmp_path / "events"))) == 2

    def test_write_snapshot(self, tmp_path, store):
        _seed_window(store)
        target = str(tmp_path / "acr.json")
        A.write_acr_snapshot(target, days=2, directory=str(tmp_path / "events"))
        assert "acr" in open(target, encoding="utf-8").read()
