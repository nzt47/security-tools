"""TASK-06 行为漂移跨会话检测 — 基线持久化/漂移事件/调度测试

覆盖（任务书 §6 功能验收第三条 + 测试要求）:
    1. 基线保存/滚动清理正确（retention_weeks 生效）
    2. 漂移超阈值产 behavior_drift 事件；低于阈值不产
    3. BehaviorDriftScheduler 默认关闭（enabled=false → disabled，不注册任务）
    4. 开启后 run(): 保存基线 → 对比 → 产事件（记忆+草稿）；基线不足跳过

守【不易】: 全部使用 tmp_path 隔离基线目录，绝不触碰真实 ~/.Yunshu/baselines；
任务注册后必须清理。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import agent.learning.behavior_drift as bd
from sensor.behavior_sensor import ActivityBehaviorSensor
from sensor.novelty import compute_drift_score, detect_behavior_drift, week_key


def _write_week_baseline(tmp_path, week: str, metrics: dict) -> None:
    """直接写入一份周基线文件（模拟历史周份）。"""
    (tmp_path / f"behavior_{week}.json").write_text(
        json.dumps({"week": week, "metrics": metrics}), encoding="utf-8")


def _cleanup_drift_scheduler():
    """清理可能残留的漂移检测任务（跨测试安全）。"""
    try:
        bd.BehaviorDriftScheduler().unschedule()
    except Exception:  # noqa: BLE001 清理失败不阻断
        pass


@pytest.fixture(autouse=True)
def _drift_env_cleanup(monkeypatch):
    """每个用例前清掉漂移相关 env，避免外部泄漏影响断言。"""
    for var in ("SENSOR_LEARNING_ENABLED", "SENSOR_LEARNING_DRIFT_THRESHOLD",
                "SENSOR_LEARNING_BASELINE_RETENTION_WEEKS"):
        monkeypatch.delenv(var, raising=False)
    yield
    _cleanup_drift_scheduler()


# ═══════════════════════════════════════════════════════════════
#  1. 漂移量化与事件化
# ═══════════════════════════════════════════════════════════════

def test_week_key_monday():
    """周基线键 = 本周周一日期（2026-08-14 是周五，周一为 2026-08-10）。"""
    import datetime as dt

    assert week_key(dt.date(2026, 8, 14)) == "2026-08-10"
    assert week_key(dt.date(2026, 8, 10)) == "2026-08-10"  # 周一本身


def test_compute_drift_score_no_overlap():
    """无重叠指标 → 漂移度 0。"""
    assert compute_drift_score({"a": 1}, {"b": 2}) == 0.0
    assert compute_drift_score({}, {}) == 0.0


def test_compute_drift_score_relative_deviation():
    """相对偏差均值: 40→80 = 1.0；40→42 = 0.05。"""
    assert compute_drift_score(
        {"behavior_mem_percent": 40.0}, {"behavior_mem_percent": 80.0}) == pytest.approx(1.0)
    assert compute_drift_score(
        {"behavior_mem_percent": 40.0}, {"behavior_mem_percent": 42.0}) == pytest.approx(0.05)


def test_detect_behavior_drift_above_threshold():
    """漂移超阈值 → behavior_drift 事件（中置信 0.5）。"""
    old = {"week": "w1", "metrics": {"m": 40.0}}
    new = {"week": "w2", "metrics": {"m": 80.0}}
    ev = detect_behavior_drift(old, new, 0.3)
    assert ev is not None
    assert ev.event_type == "behavior_drift"
    assert ev.confidence == 0.50
    assert ev.level == "medium"
    assert ev.detail["drift_score"] == pytest.approx(1.0)


def test_detect_behavior_drift_below_threshold_none():
    """低于阈值 / 无基线 → 不产事件。"""
    old = {"metrics": {"m": 40.0}}
    new = {"metrics": {"m": 42.0}}
    assert detect_behavior_drift(old, new, 0.3) is None
    assert detect_behavior_drift(None, None, 0.3) is None


# ═══════════════════════════════════════════════════════════════
#  2. 基线持久化（跨会话）
# ═══════════════════════════════════════════════════════════════

def test_baseline_save_and_list(tmp_path):
    """保存当前周基线 → 文件含 week/metrics；list_baselines 可读。"""
    sensor = ActivityBehaviorSensor(baseline_dir=str(tmp_path))
    sensor.capture_baseline = lambda: {"captured_at": "t",
                                       "metrics": {"behavior_mem_percent": 42.0}}
    res = sensor.save_baseline()
    assert res["saved"] is True
    assert res["week"] == week_key()
    data = json.loads(Path(res["path"]).read_text(encoding="utf-8"))
    assert data["week"] == res["week"]
    assert data["metrics"]["behavior_mem_percent"] == 42.0
    entries = ActivityBehaviorSensor.list_baselines(str(tmp_path))
    assert [e["week"] for e in entries] == [res["week"]]


def test_baseline_retention_prunes_old(tmp_path):
    """超保留周数 → 滚动删除最旧基线文件。"""
    for wk in ("2026-01-05", "2026-01-12", "2026-01-19", "2026-01-26"):
        _write_week_baseline(tmp_path, wk, {"m": 1.0})
    ActivityBehaviorSensor._prune_baselines(str(tmp_path), retention_weeks=2)
    remaining = ActivityBehaviorSensor.list_baselines(str(tmp_path))
    assert [e["week"] for e in remaining] == ["2026-01-19", "2026-01-26"]


def test_baseline_load_missing_returns_none(tmp_path):
    """加载不存在的周基线 → None。"""
    assert ActivityBehaviorSensor.load_baseline("2026-01-05", str(tmp_path)) is None


# ═══════════════════════════════════════════════════════════════
#  3. 调度器（默认关闭 / 注册 / run）
# ═══════════════════════════════════════════════════════════════

def test_drift_scheduler_default_disabled(monkeypatch):
    """未开启 → status=disabled，不注册任务。"""
    monkeypatch.setattr(bd, "_sensor_learning_enabled", lambda: False)
    res = bd.BehaviorDriftScheduler().schedule()
    assert res["status"] == "disabled"
    from agent.task_scheduler import get_scheduler
    names = [t.get("name") for t in get_scheduler().list_tasks()]
    assert bd.TASK_NAME not in names


def test_drift_scheduler_enabled_registers(monkeypatch):
    """开启 → 注册周级任务（interval_hours 生效）。"""
    monkeypatch.setattr(bd, "_sensor_learning_enabled", lambda: True)
    res = bd.BehaviorDriftScheduler().schedule(interval_hours=168)
    assert res["status"] == "scheduled"
    assert res["interval_hours"] == 168
    from agent.task_scheduler import get_scheduler
    names = [t.get("name") for t in get_scheduler().list_tasks()]
    assert bd.TASK_NAME in names


def test_drift_run_insufficient_baselines(tmp_path, monkeypatch):
    """基线不足两份 → skipped（不产事件）。"""
    monkeypatch.setattr(bd, "_sensor_learning_enabled", lambda: True)
    monkeypatch.setattr(bd, "_drift_threshold", lambda: 0.3)
    monkeypatch.setattr(bd, "_baseline_retention_weeks", lambda: 8)
    sensor = ActivityBehaviorSensor(baseline_dir=str(tmp_path))
    sensor.capture_baseline = lambda: {"captured_at": "t",
                                       "metrics": {"behavior_mem_percent": 40.0}}
    sched = bd.BehaviorDriftScheduler(
        sensor=sensor, baseline_dir=str(tmp_path),
        draft_dir=str(tmp_path / "drafts"), memory_dir=str(tmp_path / "mem"))
    res = sched.run()
    assert res["status"] == "skipped"
    assert res["reason"] == "insufficient_baselines"


def test_drift_run_produces_event_with_draft(tmp_path, monkeypatch):
    """漂移超阈值 → drift_detected；记忆（记录）+ 草稿出现，不注册技能。"""
    monkeypatch.setattr(bd, "_sensor_learning_enabled", lambda: True)
    monkeypatch.setattr(bd, "_drift_threshold", lambda: 0.3)
    monkeypatch.setattr(bd, "_baseline_retention_weeks", lambda: 8)
    _write_week_baseline(tmp_path, "2026-08-03", {"behavior_mem_percent": 40.0})
    sensor = ActivityBehaviorSensor(baseline_dir=str(tmp_path))
    sensor.capture_baseline = lambda: {"captured_at": "t",
                                       "metrics": {"behavior_mem_percent": 80.0}}
    sched = bd.BehaviorDriftScheduler(
        sensor=sensor, baseline_dir=str(tmp_path),
        draft_dir=str(tmp_path / "drafts"), memory_dir=str(tmp_path / "mem"),
        audit_path=str(tmp_path / "audit.jsonl"))
    res = sched.run()
    assert res["status"] == "drift_detected"
    assert res["drift_score"] == pytest.approx(1.0)
    # 记录（记忆）出现该事件
    mem_file = tmp_path / "mem" / "novelty_memory.jsonl"
    assert mem_file.exists()
    recs = [json.loads(l) for l in mem_file.read_text(encoding="utf-8").strip().splitlines()]
    assert any(r["event_type"] == "behavior_drift" for r in recs)
    # 草稿（仅 DRAFT，不注册技能）
    drafts = list((tmp_path / "drafts").glob("*.json"))
    assert len(drafts) == 1
    assert json.loads(drafts[0].read_text(encoding="utf-8"))["draft_status"] == "DRAFT"


def test_drift_run_no_drift(tmp_path, monkeypatch):
    """漂移低于阈值 → no_drift，不产事件（无记忆/草稿）。"""
    monkeypatch.setattr(bd, "_sensor_learning_enabled", lambda: True)
    monkeypatch.setattr(bd, "_drift_threshold", lambda: 0.3)
    monkeypatch.setattr(bd, "_baseline_retention_weeks", lambda: 8)
    _write_week_baseline(tmp_path, "2026-08-03", {"behavior_mem_percent": 40.0})
    sensor = ActivityBehaviorSensor(baseline_dir=str(tmp_path))
    sensor.capture_baseline = lambda: {"captured_at": "t",
                                       "metrics": {"behavior_mem_percent": 40.0}}
    sched = bd.BehaviorDriftScheduler(
        sensor=sensor, baseline_dir=str(tmp_path),
        draft_dir=str(tmp_path / "drafts"), memory_dir=str(tmp_path / "mem"))
    res = sched.run()
    assert res["status"] == "no_drift"
    assert not (tmp_path / "mem").exists()
    assert not (tmp_path / "drafts").exists()
