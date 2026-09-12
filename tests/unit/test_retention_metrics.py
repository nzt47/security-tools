"""TASK-S8-01 指标口径复算单测（验收#5：归档前后 ≥2 个既有指标一致）。

两组用例，缺一不可：

1. **一致**：归档前后逐字段相同 ⇒ 归档没有搬走读端看得见的数据。
2. **有牙齿**：人为把明细搬走后再复算 ⇒ **必须检测到不一致**。
   只有第 1 组会退化成"永远通过的摆设"，第 2 组保证判据真的在判定。
"""

from __future__ import annotations

import os
import shutil

import pytest

from agent.retention.archiver import Archiver
from agent.retention.metrics import (
    METRIC_NAMES,
    audit_chain_metric,
    check_roundtrip_metrics,
    compare_metrics,
    digestion_throughput_metric,
    snapshot,
    utc_weekly_metric,
)

from retention_testkit import (       # noqa: E402
    cleanup_event_stores,
    events_class,
    fixed_clock,
    make_policy,
    make_root,
    write_events,
    write_shadow_ledger,
)

ANCHOR = "2026-09-10"          # 2026-09-10 是周四 ⇒ ISO 周 2026-09-07 ~ 09-13


@pytest.fixture(autouse=True)
def _isolate():
    yield
    cleanup_event_stores()


# ── 1. 采集器本身 ────────────────────────────────────────────


def test_metric_names_are_registered():
    assert set(METRIC_NAMES) == {"utc.weekly", "digestion.throughput", "audit.chain"}
    assert len(METRIC_NAMES) >= 2, "验收要求至少 2 个指标复算对照"


def test_utc_weekly_metric_reports_unavailable_without_events_dir(tmp_path):
    data = utc_weekly_metric(events_dir=str(tmp_path / "nope"), anchor_day=ANCHOR)
    assert data["available"] is False
    assert "不存在" in data["reason"]


def test_utc_weekly_metric_reads_real_events(tmp_path):
    root = make_root(tmp_path)
    write_events(root, "2026-09-10", count=3, cost_cents=7.0)
    data = utc_weekly_metric(events_dir=os.path.join(root, "data", "events"),
                             anchor_day=ANCHOR)
    assert data["available"] is True
    assert data["llm_calls"] == 3
    assert data["cost_normalized_cents"] == pytest.approx(21.0)
    assert data["iso_week"] == "2026-W37"


def test_digestion_throughput_metric_reports_unavailable_without_ledger(tmp_path):
    data = digestion_throughput_metric(shadow_dir=str(tmp_path / "nope"))
    assert data["available"] is False


def test_digestion_throughput_metric_reads_real_ledger(tmp_path):
    root = make_root(tmp_path)
    write_shadow_ledger(root, runs=2, sampled=5)
    data = digestion_throughput_metric(
        shadow_dir=os.path.join(root, "data", "digestion", "shadow"))
    assert data["available"] is True
    assert data["ledger_runs"] == 2
    assert data["sampled_total"] == 10
    assert data["reviews_sampled"] == 1
    assert data["reviews_pending"] == 1


def test_audit_chain_metric_reports_unavailable_without_db(tmp_path):
    data = audit_chain_metric(db_path=str(tmp_path / "nope.db"))
    assert data["available"] is False
    # 关键：不可用状态下**不得**创建库文件（否则 dry-run 就不再是"不落盘"）
    assert not os.path.exists(str(tmp_path / "nope.db"))


def test_audit_chain_metric_verifies_chain(tmp_path):
    from agent.audit.chain import AuditChain

    db = str(tmp_path / "chain.db")
    chain = AuditChain(db_path=db, roots_path=str(tmp_path / "roots.jsonl"),
                       signing_key_path=str(tmp_path / "k.pem"))
    try:
        chain.append("a.one", "tester")
        chain.append("a.two", "tester")
        chain.flush()
    finally:
        chain.close()

    data = audit_chain_metric(db_path=db)
    assert data["available"] is True
    assert data["ok"] is True and data["checked"] == 2
    assert data["entries"] == 2 and data["head_seq"] == 2


# ── 2. 归档前后一致（≥2 个指标）──────────────────────────────


def test_utc_weekly_is_identical_before_and_after_archiving(tmp_path):
    root = make_root(tmp_path)
    write_events(root, "2026-09-10", count=3, cost_cents=5.0)
    write_events(root, "2026-09-11", count=2, cost_cents=2.5)
    events_dir = os.path.join(root, "data", "events")

    before = utc_weekly_metric(events_dir=events_dir, anchor_day=ANCHOR)
    assert before["available"] is True and before["llm_calls"] == 5

    policy = make_policy(root, [events_class()], delete_source=True)
    Archiver(policy, root=root, clock=fixed_clock(), audit=False,
             emit_events=False).run(confirm=True)

    after = utc_weekly_metric(events_dir=events_dir, anchor_day=ANCHOR)
    assert after == before, "事件流归档后 UTC 周成本口径发生变化"


def test_metric_check_has_teeth(tmp_path):
    """**判据必须真的在判定**：把明细搬走 ⇒ 复算必须不一致。

    这条对应任务书 §二.4「若某类归档后指标无法复算 → 该类的归档方式不合格」。
    """
    root = make_root(tmp_path)
    write_events(root, "2026-09-10", count=3, cost_cents=5.0)
    events_dir = os.path.join(root, "data", "events")
    before = utc_weekly_metric(events_dir=events_dir, anchor_day=ANCHOR)
    assert before["llm_calls"] == 3

    # 模拟"错误的归档方式"：把源文件整份搬出读端视野
    shutil.move(os.path.join(events_dir, "events.jsonl"),
                os.path.join(root, "moved_away.jsonl"))
    after = utc_weekly_metric(events_dir=events_dir, anchor_day=ANCHOR)
    comparison = compare_metrics({"utc.weekly": before}, {"utc.weekly": after})
    assert comparison.consistent is False
    assert comparison.metrics[0].changed_fields
    assert "llm_calls" in comparison.metrics[0].changed_fields
    assert comparison.notes, "不一致时必须给出'改为双轨'的处置提示"


def test_digestion_throughput_is_identical_before_and_after_archiving(tmp_path):
    root = make_root(tmp_path)
    write_shadow_ledger(root, runs=3, sampled=4)
    shadow_dir = os.path.join(root, "data", "digestion", "shadow")

    before = digestion_throughput_metric(shadow_dir=shadow_dir)
    assert before["available"] is True and before["ledger_runs"] == 3

    from agent.retention.policy import ARCHIVE_COLD_PACK, KIND_JSONL, RetentionClass

    cls = RetentionClass(
        class_id="shadow_ledger", title="灰度台账", kind=KIND_JSONL,
        globs=("data/digestion/shadow/*.jsonl",), cold_days=1,
        archive_mode=ARCHIVE_COLD_PACK, owner="test", basis="test")
    policy = make_policy(root, [cls])
    report = Archiver(policy, root=root, clock=fixed_clock(), audit=False,
                      emit_events=False).run(confirm=True)
    assert report.totals["archives"] == 1, "样本应先被冷归档"

    after = digestion_throughput_metric(shadow_dir=shadow_dir)
    assert after == before, "灰度台账归档后消化吞吐口径发生变化"


def test_two_metrics_verified_together_by_check_roundtrip_metrics(tmp_path):
    """`check_roundtrip_metrics` 自带"前采 → 执行 → 后采"顺序。"""
    root = make_root(tmp_path)
    write_events(root, "2026-09-10", count=4, cost_cents=1.0)
    write_shadow_ledger(root, runs=2, sampled=3)
    events_dir = os.path.join(root, "data", "events")
    shadow_dir = os.path.join(root, "data", "digestion", "shadow")

    from agent.retention.policy import ARCHIVE_COLD_PACK, KIND_JSONL, RetentionClass

    ev = events_class()
    shadow = RetentionClass(
        class_id="shadow_ledger", title="灰度台账", kind=KIND_JSONL,
        globs=("data/digestion/shadow/*.jsonl",), cold_days=1,
        archive_mode=ARCHIVE_COLD_PACK, owner="test", basis="test")
    policy = make_policy(root, [ev, shadow], delete_source=True)
    box = Archiver(policy, root=root, clock=fixed_clock(), audit=False,
                   emit_events=False)

    comparison = check_roundtrip_metrics(
        run=lambda: box.run(confirm=True),
        metrics=("utc.weekly", "digestion.throughput"),
        events_dir=events_dir, shadow_dir=shadow_dir, anchor_day=ANCHOR)
    assert comparison.consistent is True, comparison.markdown()
    assert len(comparison.metrics) == 2
    for item in comparison.metrics:
        assert item.equal is True and item.verdict == "consistent"


# ── 3. 比对器行为 ────────────────────────────────────────────


def test_compare_metrics_flags_changed_field():
    before = {"m": {"available": True, "a": 1, "b": 2}}
    after = {"m": {"available": True, "a": 1, "b": 3}}
    report = compare_metrics(before, after)
    assert report.consistent is False
    assert report.metrics[0].changed_fields == ["b"]
    assert report.metrics[0].verdict == "changed"
    assert "**不一致**" in report.metrics[0].summary()


def test_compare_metrics_treats_unavailable_both_sides_as_consistent():
    before = {"m": {"available": False, "reason": "x"}}
    after = {"m": {"available": False, "reason": "x"}}
    report = compare_metrics(before, after)
    assert report.metrics[0].verdict == "unavailable"
    assert report.consistent is True


def test_compare_metrics_flags_newly_available():
    before = {"m": {"available": False, "reason": "x"}}
    after = {"m": {"available": True, "a": 1}}
    report = compare_metrics(before, after)
    assert report.consistent is False
    assert report.metrics[0].verdict == "newly_available"


def test_snapshot_marks_unknown_metric_unavailable():
    data = snapshot(names=("no.such.metric",))
    assert data["no.such.metric"]["available"] is False


def test_snapshot_all_default_metrics_is_read_only(tmp_path):
    """采集不得产生任何文件（含不建库/不建目录）。"""
    root = make_root(tmp_path)
    before = sorted(os.listdir(root))
    data = snapshot(events_dir=os.path.join(root, "data", "events"),
                    shadow_dir=os.path.join(root, "data", "digestion", "shadow"),
                    audit_db=os.path.join(root, "data", "audit", "audit_chain.db"),
                    anchor_day=ANCHOR)
    assert sorted(os.listdir(root)) == before
    assert set(data) == set(METRIC_NAMES)


def test_markdown_table_renders():
    report = compare_metrics({"utc.weekly": {"available": True, "a": 1}},
                             {"utc.weekly": {"available": True, "a": 1}})
    assert "utc.weekly" in report.markdown()
    assert "一致" in report.markdown()
