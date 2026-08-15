"""TASK-05 Step 4 · 反馈建议自动执行体测试

覆盖（验收 §6 功能验收第一~三条 + 测试要求）:
    1. dry_run=true: 4 类建议报告正确但零状态变更/零审计/零写操作
    2. dry_run=false: promote/merge/deprecate/improve 动作执行正确，
       每个动作有版本快照 + 审计日志完整
    3. promote 未过审核链 → 被拒（与 TASK-04 强制链联动）
    4. 任一技能失败不影响其他（逐技能 try/except）

守【不易】: tmp_path 隔离（store + 审计文件）；只 mock
get_skill_feedback_summary（不碰 feedback.py 生成逻辑与 schema）。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from agent.skills_mgmt.feedback_agent import (
    ACTION_DEPRECATE_MERGE,
    ACTION_IMPROVE,
    ACTION_KEEP,
    ACTION_PROMOTE,
    FeedbackAgent,
)
from agent.skills_mgmt.models import ReviewResult, ReviewStatus, SkillStatus
from agent.skills_mgmt.service import SkillsMgmtService

ENV_PREFIX = "LEARNING_FEEDBACK_AGENT"
AUDIT_ENV = f"{ENV_PREFIX}_AUDIT_FILE"


@pytest.fixture()
def svc(tmp_path):
    return SkillsMgmtService(store_path=str(tmp_path / "skills.json"))


@pytest.fixture(autouse=True)
def _env_cleanup(monkeypatch):
    for key in ("ENABLED", "INTERVAL_HOURS", "DRY_RUN", "AUDIT_FILE"):
        monkeypatch.delenv(f"{ENV_PREFIX}_{key}", raising=False)


def _passed_review() -> ReviewResult:
    return ReviewResult(
        status=ReviewStatus.PASSED,
        score=80.0,
        duplicate_score=0.0,
        security_score=90.0,
        quality_score=80.0,
        reviewed_at="2026-08-14T00:00:00",
        reviewed_by="tester",
    )


def _add_skill(svc, skill_id: str, *, content: str = "default content body x",
               usage: int = 0) -> None:
    skill = svc.create_manual({"id": skill_id, "name": skill_id})
    skill.content = content
    skill.metrics.usage_count = usage
    svc.store.upsert(skill)


def _summary(action: str, *, satisfaction: float = 95.0,
             total: int = 8, avg: float = 4.5) -> dict:
    return {
        "skill_id": "x", "time_range_days": 30, "total_feedback": total,
        "total_rated": total, "like_count": 0, "dislike_count": 0,
        "satisfaction_rate_percent": satisfaction, "avg_rating": avg,
        "by_type": {}, "by_category": {}, "quality_cases_count": 0,
        "recent_dislike_comments": [], "recommended_action": action,
    }


def _audit_lines(audit_path: Path) -> list:
    text = audit_path.read_text(encoding="utf-8") if audit_path.exists() else ""
    return [json.loads(x) for x in text.splitlines() if x.strip()]


# ─── dry-run 零副作用 ───


def test_dry_run_report_zero_side_effects(svc, tmp_path):
    """4 类建议 dry_run=true：报告正确但零状态变更/零审计写入。"""
    actions = {
        "s-promote": _summary(ACTION_PROMOTE),
        "s-merge": _summary(ACTION_DEPRECATE_MERGE, satisfaction=30.0),
        "s-improve": _summary(ACTION_IMPROVE, avg=2.0),
        "s-keep": _summary(ACTION_KEEP),
    }
    for skill_id in actions:
        _add_skill(svc, skill_id)
    monkey = pytest.MonkeyPatch()
    monkey.setattr(svc, "get_skill_feedback_summary",
                   lambda sid, days=30: actions[sid])
    audit_path = tmp_path / "fa_audit.jsonl"
    agent = FeedbackAgent(service=svc, audit_path=str(audit_path))

    report = agent.execute_recommendations(dry_run=True)
    monkey.undo()

    assert report["dry_run"] is True
    assert report["total_skills"] == 4
    assert report["processed"] == 4
    assert len(report["planned"]) == 4
    assert report["executed"] == []
    assert report["rejected"] == []
    # 零状态变更
    assert svc.get("s-promote").status == SkillStatus.DRAFT
    assert svc.get("s-merge").status == SkillStatus.DRAFT
    # 零审计写入
    assert not audit_path.exists()


# ─── promote：与 TASK-04 强制审核链联动 ───


def test_promote_after_review_published(svc, tmp_path):
    """已过审核链 → promote 成功发布 + 版本快照 + 审计完整。"""
    skill = svc.create_manual({"id": "s-promote", "name": "s-promote"})
    skill.review = _passed_review()
    svc.store.upsert(skill)
    audit_path = tmp_path / "fa_audit.jsonl"
    agent = FeedbackAgent(service=svc, audit_path=str(audit_path))
    monkey = pytest.MonkeyPatch()
    monkey.setattr(svc, "get_skill_feedback_summary",
                   lambda sid, days=30: _summary(ACTION_PROMOTE))

    report = agent.execute_recommendations(dry_run=False)
    monkey.undo()

    assert report["executed"][0]["result"] == "published"
    assert svc.get("s-promote").status == SkillStatus.PUBLISHED
    # 动作前 bump_version 快照可回滚
    assert len(svc.list_versions("s-promote")) >= 1
    lines = _audit_lines(audit_path)
    assert len(lines) == 1
    assert lines[0]["skill_id"] == "s-promote"
    assert lines[0]["action"] == ACTION_PROMOTE
    assert lines[0]["snapshot_version"]


def test_promote_without_review_rejected(svc, tmp_path):
    """未过审核链 → promote 被拒（TASK-04 强制链联动），状态不变。"""
    _add_skill(svc, "s-noreview")
    audit_path = tmp_path / "fa_audit.jsonl"
    agent = FeedbackAgent(service=svc, audit_path=str(audit_path))
    monkey = pytest.MonkeyPatch()
    monkey.setattr(svc, "get_skill_feedback_summary",
                   lambda sid, days=30: _summary(ACTION_PROMOTE))

    report = agent.execute_recommendations(dry_run=False)
    monkey.undo()

    assert len(report["rejected"]) == 1
    assert report["rejected"][0]["result"] == "rejected"
    assert report["executed"] == []
    assert svc.get("s-noreview").status == SkillStatus.DRAFT
    assert not audit_path.exists()


# ─── consider_deprecate_or_merge ───


def test_consider_merge_merges_duplicate(svc, tmp_path):
    """有高相似技能 → merge（src 移除，dst 保留），审计 result=merged。"""
    _add_skill(svc, "s-merge-src", content="同一内容 abc",
               usage=1)
    _add_skill(svc, "s-merge-dst", content="同一内容 abc",
               usage=100)
    audit_path = tmp_path / "fa_audit.jsonl"
    agent = FeedbackAgent(service=svc, audit_path=str(audit_path))
    monkey = pytest.MonkeyPatch()
    monkey.setattr(svc, "get_skill_feedback_summary",
                   lambda sid, days=30: _summary(
                       ACTION_DEPRECATE_MERGE, satisfaction=30.0))

    report = agent.execute_recommendations(dry_run=False)
    monkey.undo()

    assert report["executed"][0]["result"] == "merged"
    assert report["executed"][0]["merged_into"] == "s-merge-dst"
    # src 被合并移除，dst 保留
    assert svc.store.get("s-merge-src") is None
    assert svc.store.get("s-merge-dst") is not None
    lines = _audit_lines(audit_path)
    assert lines[0]["result"] == "merged"
    assert lines[0]["snapshot_version"]


def test_consider_deprecate_when_no_duplicate(svc, tmp_path):
    """无高相似技能 → 状态迁移 DEPRECATED（仅状态，不删文件）。"""
    _add_skill(svc, "s-solo", content="独一无二的内容 zzz")
    audit_path = tmp_path / "fa_audit.jsonl"
    agent = FeedbackAgent(service=svc, audit_path=str(audit_path))
    monkey = pytest.MonkeyPatch()
    monkey.setattr(svc, "get_skill_feedback_summary",
                   lambda sid, days=30: _summary(
                       ACTION_DEPRECATE_MERGE, satisfaction=30.0))

    report = agent.execute_recommendations(dry_run=False)
    monkey.undo()

    assert report["executed"][0]["result"] == "deprecated"
    assert svc.get("s-solo").status == SkillStatus.DEPRECATED
    # 文件仍存在（绝不物理删除）
    assert svc.store.get("s-solo") is not None


# ─── improve_params ───


def test_improve_params_optimized(svc, tmp_path):
    """improve_params：调 optimize_params + 版本快照 + 审计。"""
    _add_skill(svc, "s-params")
    audit_path = tmp_path / "fa_audit.jsonl"
    agent = FeedbackAgent(service=svc, audit_path=str(audit_path))
    monkey = pytest.MonkeyPatch()
    monkey.setattr(svc, "get_skill_feedback_summary",
                   lambda sid, days=30: _summary(ACTION_IMPROVE, avg=2.0))
    calls = {"n": 0}
    monkey.setattr(
        svc, "optimize_params",
        lambda sid, feedback_summary=None: calls.update(n=calls["n"] + 1)
        or {"optimized": True})

    report = agent.execute_recommendations(dry_run=False)
    monkey.undo()

    assert report["executed"][0]["result"] == "params_optimized"
    assert calls["n"] == 1
    assert len(svc.list_versions("s-params")) >= 1
    lines = _audit_lines(audit_path)
    assert lines[0]["action"] == ACTION_IMPROVE


# ─── keep / 异常隔离 ───


def test_keep_skipped(svc, tmp_path):
    """keep 建议 → 跳过，无动作/无审计。"""
    _add_skill(svc, "s-keep")
    audit_path = tmp_path / "fa_audit.jsonl"
    agent = FeedbackAgent(service=svc, audit_path=str(audit_path))
    monkey = pytest.MonkeyPatch()
    monkey.setattr(svc, "get_skill_feedback_summary",
                   lambda sid, days=30: _summary(ACTION_KEEP))

    report = agent.execute_recommendations(dry_run=False)
    monkey.undo()

    assert report["actions"][ACTION_KEEP] == 1
    assert report["executed"] == []
    assert report["rejected"] == []
    assert not audit_path.exists()


def test_one_skill_error_not_blocking(svc, tmp_path):
    """任一技能处理失败不影响其他技能（逐技能 try/except）。"""
    good = svc.create_manual({"id": "s-good", "name": "s-good"})
    good.review = _passed_review()
    svc.store.upsert(good)
    _add_skill(svc, "s-bad")
    audit_path = tmp_path / "fa_audit.jsonl"
    agent = FeedbackAgent(service=svc, audit_path=str(audit_path))
    monkey = pytest.MonkeyPatch()

    def _fake(sid, days=30):
        if sid == "s-bad":
            raise RuntimeError("反馈库暂不可用")
        return _summary(ACTION_PROMOTE)

    monkey.setattr(svc, "get_skill_feedback_summary", _fake)

    report = agent.execute_recommendations(dry_run=False)
    monkey.undo()

    assert len(report["errors"]) == 1
    assert report["errors"][0]["skill_id"] == "s-bad"
    assert report["processed"] == 1
    assert svc.get("s-good").status == SkillStatus.PUBLISHED


# ─── 调度注册 ───


def test_schedule_disabled_by_default(svc):
    """未开启总开关 → schedule 返回 disabled，任务未注册。"""
    from agent.skills_mgmt import feedback_agent as fa_mod
    monkey = pytest.MonkeyPatch()
    monkey.setattr(fa_mod, "_enabled", lambda: False)

    agent = FeedbackAgent(service=svc)
    result = agent.schedule()
    monkey.undo()

    assert result["status"] == "disabled"
    from agent.task_scheduler import get_scheduler
    names = [t["name"] for t in get_scheduler().list_tasks()]
    assert fa_mod.TASK_NAME not in names


def test_schedule_registered_when_enabled(svc):
    """开启总开关 → 任务注册，且与 TASK-04 同一调度收口。"""
    from agent.skills_mgmt import feedback_agent as fa_mod
    from agent.task_scheduler import get_scheduler
    monkey = pytest.MonkeyPatch()
    monkey.setattr(fa_mod, "_enabled", lambda: True)
    monkey.setattr(fa_mod, "_interval_hours", lambda: 24)

    try:
        agent = FeedbackAgent(service=svc)
        result = agent.schedule()
        assert result["status"] == "scheduled"
        names = [t["name"] for t in get_scheduler().list_tasks()]
        assert fa_mod.TASK_NAME in names
    finally:
        FeedbackAgent(service=svc).unschedule()
        monkey.undo()
