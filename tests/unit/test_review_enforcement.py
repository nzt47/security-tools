"""TASK-04 Step 3 · 发布强制审核链测试

覆盖（验收 §6 功能验收第四条 + 测试要求）:
    1. enforce_before_publish=true（默认）: 无 ReviewResult → publish 被拒
    2. review 通过（PASSED）后 → publish 成功（status=published）
    3. review FAILED → publish 被拒
    4. 终态/驳回态不可发布（published/archived/rejected）
    5. force=True 显式豁免 → 发布成功 + 审计日志（review_waiver_publish）
    6. 配置豁免（enforce_before_publish=false）→ 发布成功 + 审计日志

守【不易】: 全部 tmp_path 隔离（SkillStore + 审计文件）；env 用 monkeypatch 隔离。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.skills_mgmt.exceptions import SkillReviewError
from agent.skills_mgmt.models import (
    ReviewResult,
    ReviewStatus,
    SkillStatus,
)
from agent.skills_mgmt.service import SkillsMgmtService

ENFORCE_ENV = "SKILLS_REVIEW_ENFORCE_PUBLISH"
AUDIT_ENV = "SKILLS_REVIEW_AUDIT_FILE"


@pytest.fixture()
def svc(tmp_path):
    return SkillsMgmtService(store_path=str(tmp_path / "skills.json"))


@pytest.fixture(autouse=True)
def _env_cleanup(monkeypatch):
    monkeypatch.delenv(ENFORCE_ENV, raising=False)
    monkeypatch.delenv(AUDIT_ENV, raising=False)


def _passed_review() -> ReviewResult:
    """构造三维评分达标的 PASSED ReviewResult（reviewer 置 PASSED 时即此形态）。"""
    return ReviewResult(
        status=ReviewStatus.PASSED,
        score=80.0,
        duplicate_score=0.0,
        security_score=90.0,
        quality_score=80.0,
        reviewed_at="2026-08-14T00:00:00",
        reviewed_by="tester",
    )


def _add_skill(svc, skill_id="test-skill"):
    return svc.create_manual({"id": skill_id, "name": skill_id})


def _audit_lines(audit_path: Path) -> list:
    text = audit_path.read_text(encoding="utf-8") if audit_path.exists() else ""
    return [json.loads(x) for x in text.splitlines() if x.strip()]


# ─── 强制审核链 ───


def test_publish_without_review_rejected(svc):
    """无 ReviewResult → publish 被拒（默认 enforce=true），状态不变。"""
    _add_skill(svc)

    with pytest.raises(SkillReviewError, match="未通过审核"):
        svc.publish("test-skill")

    assert svc.get("test-skill").status == SkillStatus.DRAFT


def test_publish_after_passed_review_succeeds(svc):
    """review 通过（PASSED）→ publish 成功。"""
    skill = _add_skill(svc)
    skill.review = _passed_review()
    svc.store.upsert(skill)

    published = svc.publish("test-skill")

    assert published.status == SkillStatus.PUBLISHED
    assert svc.get("test-skill").status == SkillStatus.PUBLISHED


def test_publish_after_failed_review_rejected(svc):
    """review FAILED → publish 被拒。"""
    skill = _add_skill(svc)
    skill.review = ReviewResult(status=ReviewStatus.FAILED, score=20.0)
    svc.store.upsert(skill)

    with pytest.raises(SkillReviewError, match="未通过审核"):
        svc.publish("test-skill")


def test_publish_terminal_status_rejected(svc):
    """终态/驳回态不可发布。"""
    for status in (SkillStatus.PUBLISHED, SkillStatus.ARCHIVED,
                   SkillStatus.REJECTED):
        skill_id = f"skill-{status.value}"
        skill = _add_skill(svc, skill_id)
        skill.status = status
        svc.store.upsert(skill)
        with pytest.raises(SkillReviewError, match="当前状态不可发布"):
            svc.publish(skill_id)


# ─── 豁免路径（必须写审计日志）───


def test_publish_force_bypass_writes_audit(svc, tmp_path, monkeypatch):
    """force=True 显式豁免 → 发布成功 + 审计日志留痕。"""
    monkeypatch.setenv(AUDIT_ENV, str(tmp_path / "audit.jsonl"))
    _add_skill(svc)

    published = svc.publish("test-skill", force=True, actor="tester",
                            reason="manual_approval")

    assert published.status == SkillStatus.PUBLISHED
    lines = _audit_lines(tmp_path / "audit.jsonl")
    assert len(lines) == 1
    rec = lines[0]
    assert rec["event"] == "review_waiver_publish"
    assert rec["skill_id"] == "test-skill"
    assert rec["actor"] == "tester"
    assert rec["reason"] == "manual_approval"


def test_publish_config_waiver_writes_audit(svc, tmp_path, monkeypatch):
    """enforce_before_publish=false（配置豁免）→ 发布成功 + 审计日志。"""
    monkeypatch.setenv(ENFORCE_ENV, "false")
    monkeypatch.setenv(AUDIT_ENV, str(tmp_path / "audit.jsonl"))
    _add_skill(svc)

    published = svc.publish("test-skill", actor="ops")

    assert published.status == SkillStatus.PUBLISHED
    lines = _audit_lines(tmp_path / "audit.jsonl")
    assert len(lines) == 1
    assert lines[0]["event"] == "review_waiver_publish"
    assert lines[0]["actor"] == "ops"
