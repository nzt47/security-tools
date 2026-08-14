"""TASK-05 Step 4 · offline_evolver 周期调度测试

覆盖（验收 §6 功能验收第五条 + 测试要求）:
    1. 调度注册存在（默认关闭，开启后经 task_scheduler 注册）
    2. dry_run=true 只预演候选：零提交/零 KPI/零审计
    3. 正式运行后 "进化采纳率" KPI 有值 + 审计摘要落盘
    4. 注销任务存在

守【不易】: 不触碰 offline_evolver.py 算法；fake 只替换服务边界
（_new_evolver / evolve_batch 返回值），提交门槛等算法语义不变。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.learning_metrics import get_learning_metrics, reset_learning_metrics
from agent.skills_mgmt.evolution_scheduler import (
    EvolutionScheduler,
    TASK_NAME,
    _enabled,
)
from agent.skills_mgmt.models import Skill, SkillMetrics

ENV_PREFIX = "LEARNING_EVOLVER"


class _FakeEvolver:
    """只实现候选筛选预演所需的最小接口（不动算法本体）。"""

    def __init__(self, candidates):
        self._candidates = candidates

    def _select_candidates(self):
        return self._candidates


class _FakeService:
    def __init__(self, *, evolver=None, batch=None):
        self._evolver = evolver or _FakeEvolver([])
        self._batch = batch or {}
        self.evolve_calls = 0
        self.preview_calls = 0

    def _new_evolver(self):
        self.preview_calls += 1
        return self._evolver

    def evolve_batch(self, skill_ids=None, *, max_rounds=1, trigger="scheduler"):
        self.evolve_calls += 1
        return self._batch


def _candidate_skill(skill_id: str) -> Skill:
    return Skill(
        id=skill_id,
        name=skill_id,
        metrics=SkillMetrics(usage_count=50, success_rate=0.7),
    )


@pytest.fixture(autouse=True)
def _cleanup(monkeypatch):
    for key in ("ENABLED", "INTERVAL_DAYS", "DRY_RUN", "AUDIT_FILE"):
        monkeypatch.delenv(f"{ENV_PREFIX}_{key}", raising=False)
    reset_learning_metrics()
    EvolutionScheduler().unschedule()
    yield
    EvolutionScheduler().unschedule()
    reset_learning_metrics()


def _audit_lines(audit_path: Path) -> list:
    text = audit_path.read_text(encoding="utf-8") if audit_path.exists() else ""
    return [json.loads(x) for x in text.splitlines() if x.strip()]


# ─── 调度注册 ───


def test_schedule_disabled_by_default(monkeypatch):
    """未开启总开关 → schedule 返回 disabled，任务未注册。"""
    monkeypatch.setattr("agent.skills_mgmt.evolution_scheduler._enabled",
                        lambda: False)
    result = EvolutionScheduler().schedule()
    assert result["status"] == "disabled"

    from agent.task_scheduler import get_scheduler
    names = [t["name"] for t in get_scheduler().list_tasks()]
    assert TASK_NAME not in names


def test_schedule_registered_when_enabled(monkeypatch):
    """开启总开关 → 周级任务注册（interval_days 默认 7）。"""
    monkeypatch.setattr("agent.skills_mgmt.evolution_scheduler._enabled",
                        lambda: True)
    monkeypatch.setattr("agent.skills_mgmt.evolution_scheduler._interval_days",
                        lambda: 7)
    try:
        result = EvolutionScheduler().schedule()
        assert result["status"] == "scheduled"
        assert result["interval_days"] == 7

        from agent.task_scheduler import get_scheduler
        names = [t["name"] for t in get_scheduler().list_tasks()]
        assert TASK_NAME in names
    finally:
        EvolutionScheduler().unschedule()


def test_unschedule_removes_task(monkeypatch):
    """注销后任务不存在。"""
    monkeypatch.setattr("agent.skills_mgmt.evolution_scheduler._enabled",
                        lambda: True)
    EvolutionScheduler().schedule()

    from agent.task_scheduler import get_scheduler
    assert TASK_NAME in [t["name"] for t in get_scheduler().list_tasks()]

    assert EvolutionScheduler().unschedule() is True
    names = [t["name"] for t in get_scheduler().list_tasks()]
    assert TASK_NAME not in names


# ─── dry-run 零提交 ───


def test_dry_run_preview_zero_commit(tmp_path):
    """dry_run=true：只预演候选，evolve_batch 零调用/零 KPI/零审计。"""
    fake_svc = _FakeService(
        evolver=_FakeEvolver([_candidate_skill("cand-1")]))
    audit_path = tmp_path / "evo_audit.jsonl"
    sched = EvolutionScheduler(service=fake_svc, audit_path=str(audit_path))

    report = sched.run(dry_run=True, trigger="scheduler")

    assert report["dry_run"] is True
    assert len(report["planned_candidates"]) == 1
    assert report["planned_candidates"][0]["skill_id"] == "cand-1"
    assert fake_svc.evolve_calls == 0
    assert fake_svc.preview_calls == 1
    # KPI 零递增
    metrics = get_learning_metrics()
    assert metrics._evolution_candidates == 0
    assert metrics._evolution_adopted == 0
    # 零审计写入
    assert not audit_path.exists()


# ─── 正式运行：KPI + 审计 ───


def test_real_run_records_kpi_and_audit(tmp_path):
    """正式运行：报告摘要正确 + 进化采纳率 KPI 有值 + 审计落盘。"""
    batch = {
        "total_skills": 2, "evolved_count": 1, "skipped_count": 1,
        "failed_count": 0, "avg_improvement": 0.07, "cost_tokens": 120,
        "budget_breached": False,
    }
    fake_svc = _FakeService(batch=batch)
    audit_path = tmp_path / "evo_audit.jsonl"
    sched = EvolutionScheduler(service=fake_svc, audit_path=str(audit_path))

    report = sched.run(dry_run=False, trigger="scheduler")

    assert fake_svc.evolve_calls == 1
    assert report["evolved_count"] == 1
    assert report["total_candidates"] == 2
    assert report["adopted_candidates"] == 1
    # KPI：候选 2 次（1 adopted + 1 rejected），采纳 1
    metrics = get_learning_metrics()
    assert metrics._evolution_candidates == 2
    assert metrics._evolution_adopted == 1
    # 审计摘要落盘
    lines = _audit_lines(audit_path)
    assert len(lines) == 1
    assert lines[0]["event"] == "evolution_schedule_run"
    assert lines[0]["adopted_candidates"] == 1
    assert lines[0]["cost_tokens"] == 120


def test_real_run_error_reports_no_crash(tmp_path):
    """evolve_batch 抛错 → 报告 error，不中断/不崩溃。"""
    class _BrokenService(_FakeService):
        def evolve_batch(self, skill_ids=None, *, max_rounds=1,
                         trigger="scheduler"):
            self.evolve_calls += 1
            raise RuntimeError("评估服务不可用")

    fake_svc = _BrokenService()
    audit_path = tmp_path / "evo_audit.jsonl"
    sched = EvolutionScheduler(service=fake_svc, audit_path=str(audit_path))

    report = sched.run(dry_run=False, trigger="scheduler")

    assert "error" in report
    assert "评估服务不可用" in report["error"]
    # 失败路径不写审计/KPI
    assert not audit_path.exists()
    assert get_learning_metrics()._evolution_candidates == 0


def test_dry_run_uses_config_default_true():
    """_dry_run 未配置时默认 true（不可变约束）。"""
    assert _enabled() is False  # 总开关默认关闭
    from agent.skills_mgmt.evolution_scheduler import _dry_run
    assert _dry_run() is True
