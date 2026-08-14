"""TASK-05 Step 4 · Skill 生命周期自动淘汰测试

覆盖（验收 §6 功能验收第四条 + 测试要求）:
    1. PUBLISHED 闲置 > unused_days → DEPRECATED
    2. DEPRECATED 闲置 > archive_days → ARCHIVED
    3. DEPRECATED→ARCHIVED 时序（同一技能两轮判定）
    4. last_used_at 缺失: usage_count==0 以创建时间判定；usage>0 保守不迁移
    5. dry_run=true 零迁移/零审计
    6. 容量超 upgrade_threshold → 检索升级建议（不自动改检索配置）

守【不易】: 状态迁移只改 models.py 状态机，全程不物理删除文件。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from agent.skills_mgmt.lifecycle import (
    DEFAULT_ARCHIVE_DAYS,
    DEFAULT_UNUSED_DAYS,
    LifecycleManager,
    _idle_days,
)
from agent.skills_mgmt.models import SkillStatus
from agent.skills_mgmt.service import SkillsMgmtService

ENV_PREFIX = "LEARNING_LIFECYCLE"
AUDIT_ENV = f"{ENV_PREFIX}_AUDIT_FILE"

FIXED_NOW = datetime(2026, 8, 14, 12, 0, 0)


@pytest.fixture()
def svc(tmp_path):
    return SkillsMgmtService(store_path=str(tmp_path / "skills.json"))


@pytest.fixture(autouse=True)
def _env_cleanup(monkeypatch):
    for key in ("ENABLED", "INTERVAL_HOURS", "UNUSED_DAYS", "ARCHIVE_DAYS",
                "DRY_RUN", "AUDIT_FILE", "UPGRADE_THRESHOLD"):
        monkeypatch.delenv(f"{ENV_PREFIX}_{key}", raising=False)


def _add_skill(svc, skill_id: str, *, status: SkillStatus,
               last_used: str | None = None, usage: int = 0,
               created: str | None = None) -> None:
    skill = svc.create_manual({"id": skill_id, "name": skill_id})
    skill.status = status
    skill.metrics.usage_count = usage
    skill.metrics.last_used_at = last_used
    if created:
        skill.created_at = created
    svc.store.upsert(skill)


def _audit_lines(audit_path: Path) -> list:
    text = audit_path.read_text(encoding="utf-8") if audit_path.exists() else ""
    return [json.loads(x) for x in text.splitlines() if x.strip()]


def _days_ago(days: int) -> str:
    return (FIXED_NOW - timedelta(days=days)).isoformat()


# ─── 闲置天数判定 ───


def test_idle_days_uses_last_used():
    """last_used_at 存在 → 以闲置天数返回。"""
    class _M:
        usage_count = 0
        last_used_at = _days_ago(100)

    class _S:
        metrics = _M()
        created_at = _days_ago(300)

    assert _idle_days(_S, FIXED_NOW) == 100


def test_idle_days_missing_last_used_usage_zero_uses_created():
    """last_used_at 缺失且 usage_count==0 → 以创建时间近似闲置天数。"""
    class _M:
        usage_count = 0
        last_used_at = None

    class _S:
        metrics = _M()
        created_at = _days_ago(120)

    assert _idle_days(_S, FIXED_NOW) == 120


def test_idle_days_usage_without_last_used_not_migrated():
    """usage_count>0 但 last_used_at 缺失（异常数据）→ 保守返回 None 不迁移。"""
    class _M:
        usage_count = 5
        last_used_at = None

    class _S:
        metrics = _M()
        created_at = _days_ago(400)

    assert _idle_days(_S, FIXED_NOW) is None


# ─── 状态迁移（正式执行） ───


def test_published_unused_deprecated(svc, tmp_path):
    """PUBLISHED 闲置 > unused_days → DEPRECATED + 审计，文件不删除。"""
    _add_skill(svc, "s-old", status=SkillStatus.PUBLISHED,
               last_used=_days_ago(100))
    audit_path = tmp_path / "lc_audit.jsonl"
    mgr = LifecycleManager(service=svc, audit_path=str(audit_path), now=FIXED_NOW)
    monkey = pytest.MonkeyPatch()
    monkey.setattr("agent.skills_mgmt.lifecycle._unused_days",
                   lambda: DEFAULT_UNUSED_DAYS)
    monkey.setattr("agent.skills_mgmt.lifecycle._archive_days",
                   lambda: DEFAULT_ARCHIVE_DAYS)

    report = mgr.run_lifecycle_check(dry_run=False)
    monkey.undo()

    assert len(report["deprecated"]) == 1
    assert report["deprecated"][0]["skill_id"] == "s-old"
    assert svc.get("s-old").status == SkillStatus.DEPRECATED
    # 全程不物理删除
    assert svc.store.get("s-old") is not None
    lines = _audit_lines(audit_path)
    assert lines[0]["action"] == "deprecate"
    assert lines[0]["to_status"] == "deprecated"


def test_deprecated_unused_archived(svc, tmp_path):
    """DEPRECATED 闲置 > archive_days → ARCHIVED + 审计。"""
    _add_skill(svc, "s-arch", status=SkillStatus.DEPRECATED,
               last_used=_days_ago(200))
    audit_path = tmp_path / "lc_audit.jsonl"
    mgr = LifecycleManager(service=svc, audit_path=str(audit_path), now=FIXED_NOW)
    monkey = pytest.MonkeyPatch()
    monkey.setattr("agent.skills_mgmt.lifecycle._archive_days",
                   lambda: DEFAULT_ARCHIVE_DAYS)

    report = mgr.run_lifecycle_check(dry_run=False)
    monkey.undo()

    assert len(report["archived"]) == 1
    assert svc.get("s-arch").status == SkillStatus.ARCHIVED
    lines = _audit_lines(audit_path)
    assert lines[0]["action"] == "archive"
    assert lines[0]["to_status"] == "archived"


def test_deprecated_to_archived_sequence(svc, tmp_path):
    """时序：同一零使用 Skill 先 DEPRECATED（unused_days）再 ARCHIVED（archive_days）。"""
    _add_skill(svc, "s-seq", status=SkillStatus.PUBLISHED,
               last_used=_days_ago(100))
    audit_path = tmp_path / "lc_audit.jsonl"
    mgr = LifecycleManager(service=svc, audit_path=str(audit_path), now=FIXED_NOW)
    monkey = pytest.MonkeyPatch()
    monkey.setattr("agent.skills_mgmt.lifecycle._unused_days",
                   lambda: DEFAULT_UNUSED_DAYS)
    monkey.setattr("agent.skills_mgmt.lifecycle._archive_days",
                   lambda: DEFAULT_ARCHIVE_DAYS)

    # 第一轮：100 天 > 90 → DEPRECATED
    mgr.run_lifecycle_check(dry_run=False)
    assert svc.get("s-seq").status == SkillStatus.DEPRECATED

    # 更新闲置基准（仍 > archive_days=180，且已 DEPRECATED）
    skill = svc.get("s-seq")
    skill.metrics.last_used_at = _days_ago(200)
    svc.store.upsert(skill)
    # 第二轮：200 天 > 180 → ARCHIVED
    mgr.run_lifecycle_check(dry_run=False)
    monkey.undo()

    assert svc.get("s-seq").status == SkillStatus.ARCHIVED
    assert svc.store.get("s-seq") is not None  # 全程不物理删除
    lines = _audit_lines(audit_path)
    assert [l["action"] for l in lines] == ["deprecate", "archive"]


def test_last_used_missing_usage_zero_deprecated(svc, tmp_path):
    """last_used_at 缺失 + usage_count==0 + 创建时间超阈值 → DEPRECATED。"""
    _add_skill(svc, "s-never", status=SkillStatus.PUBLISHED,
               usage=0, created=_days_ago(100))
    audit_path = tmp_path / "lc_audit.jsonl"
    mgr = LifecycleManager(service=svc, audit_path=str(audit_path), now=FIXED_NOW)

    report = mgr.run_lifecycle_check(dry_run=False)

    assert len(report["deprecated"]) == 1
    assert report["deprecated"][0]["idle_days"] == 100
    assert svc.get("s-never").status == SkillStatus.DEPRECATED


def test_recent_use_not_migrated(svc, tmp_path):
    """近期仍有使用（闲置 < 阈值）→ 不迁移。"""
    _add_skill(svc, "s-recent", status=SkillStatus.PUBLISHED,
               last_used=_days_ago(10))
    audit_path = tmp_path / "lc_audit.jsonl"
    mgr = LifecycleManager(service=svc, audit_path=str(audit_path), now=FIXED_NOW)

    report = mgr.run_lifecycle_check(dry_run=False)

    assert report["deprecated"] == []
    assert report["archived"] == []
    assert svc.get("s-recent").status == SkillStatus.PUBLISHED
    assert not audit_path.exists()


# ─── dry-run 零副作用 ───


def test_dry_run_no_migration_no_audit(svc, tmp_path):
    """dry_run=true：报告列出计划迁移但零状态变更/零审计。"""
    _add_skill(svc, "s-plan", status=SkillStatus.PUBLISHED,
               last_used=_days_ago(100))
    audit_path = tmp_path / "lc_audit.jsonl"
    mgr = LifecycleManager(service=svc, audit_path=str(audit_path), now=FIXED_NOW)

    report = mgr.run_lifecycle_check(dry_run=True)

    assert report["dry_run"] is True
    assert len(report["deprecated"]) == 1
    assert report["deprecated"][0]["to_status"] == "deprecated"
    assert svc.get("s-plan").status == SkillStatus.PUBLISHED
    assert not audit_path.exists()


# ─── 容量超限建议 ───


def test_capacity_over_threshold_suggestion(svc, tmp_path):
    """技能总数超 upgrade_threshold → 输出检索升级建议（不改检索配置）。"""
    for i in range(3):
        _add_skill(svc, f"s-{i}", status=SkillStatus.PUBLISHED,
                   last_used=_days_ago(1))
    mgr = LifecycleManager(service=svc, audit_path=str(tmp_path / "lc.jsonl"),
                           now=FIXED_NOW)
    monkey = pytest.MonkeyPatch()
    monkey.setattr("agent.skills_mgmt.lifecycle._upgrade_threshold", lambda: 2)

    report = mgr.run_lifecycle_check(dry_run=True)
    monkey.undo()

    assert len(report["suggestions"]) == 1
    suggestion = report["suggestions"][0]
    assert suggestion["type"] == "retrieval_upgrade"
    assert suggestion["skill_count"] == 3
    assert suggestion["threshold"] == 2
