"""技能清理服务单元测试（技能中心自动清除无用技能）。

覆盖：
    - 多轨删除（service.delete → legacy/文件轨/分类全清，根治孤儿）
    - 孤儿扫描/清理（dry_run 不删；真实清理清除 pd ghost、保留有效技能）
    - 无用技能物理淘汰（归档超期零使用 → 删；活跃 → 保留）
    - 幂等与安全（删不存在 id 不报错；内置文件轨技能不当孤儿）
"""

import json
import pathlib

import pytest

from agent.skills_mgmt import SkillsMgmtService
from agent.skills_mgmt.cleanup import (
    cleanup_orphans,
    cleanup_unused,
    remove_skill_everywhere,
    report,
    scan_orphans,
    scan_unused,
)
from agent.skills_mgmt.models import SkillStatus


@pytest.fixture
def iso_svc(tmp_path):
    """隔离服务：主轨/文件轨/legacy 全在 tmp 下，不碰生产。"""
    svc = SkillsMgmtService(
        store_path=str(tmp_path / "skills_mgmt.json"),
        repo_path=str(tmp_path / "skills_repo"),
    )
    return svc


def _seed(iso_svc, tmp_path, skills=("skill-a",)):
    for sid in skills:
        iso_svc.create_manual({"id": sid, "name": sid, "content": "# body",
                               "content_type": "markdown"})
    return iso_svc


class TestMultiTrackDelete:
    """service.delete 应同步清除全部存储轨（根治孤儿源头）。"""

    def test_delete_clears_all_tracks(self, iso_svc, tmp_path):
        _seed(iso_svc, tmp_path, skills=("skill-a", "skill-b"))
        # 文件轨也建上（模拟完整技能）
        for sid in ("skill-a", "skill-b"):
            iso_svc.file_store.create(
                sid, meta={"id": sid, "name": sid, "enabled": True,
                           "status": "approved"}, instruction="# body")
        # 把两个都写进 legacy（模拟历史同步）
        legacy = tmp_path / "skills.json"
        legacy.write_text(json.dumps({"skills": [
            {"id": "skill-a", "name": "a", "enabled": True},
            {"id": "skill-b", "name": "b", "enabled": True},
        ]}), encoding="utf-8")

        # 删 skill-b → 主轨/文件轨/legacy 都应消失
        iso_svc.delete("skill-b")
        assert iso_svc.store.get("skill-b") is None
        assert not (tmp_path / "skills_repo" / "skill-b").exists()
        legacy_data = json.loads(legacy.read_text(encoding="utf-8"))
        assert "skill-b" not in [s["id"] for s in legacy_data["skills"]]
        # skill-a 保留
        assert iso_svc.store.get("skill-a") is not None

    def test_delete_missing_raises(self, iso_svc):
        with pytest.raises(Exception):
            iso_svc.delete("no-such-skill")

    def test_remove_everywhere_idempotent(self, iso_svc):
        res = remove_skill_everywhere(iso_svc, "ghost-not-exist")
        assert "removed" in res  # 不抛异常


class TestOrphanScanCleanup:
    """孤儿扫描/清理。"""

    def _make_ghost_legacy(self, iso_svc, tmp_path, ghost_ids):
        """主轨只有 skill-a；legacy 额外含 pd ghost。"""
        _seed(iso_svc, tmp_path, skills=("skill-a",))
        legacy = tmp_path / "skills.json"
        rows = [{"id": "skill-a", "name": "a", "enabled": True}]
        for g in ghost_ids:
            rows.append({"id": g, "name": g, "enabled": False})
        legacy.write_text(json.dumps({"skills": rows}), encoding="utf-8")

    def test_scan_finds_pd_ghost_only(self, iso_svc, tmp_path):
        self._make_ghost_legacy(iso_svc, tmp_path,
                                ghost_ids=["pd-skill-ghost1",
                                           "pd-skill-ghost2"])
        orphans = scan_orphans(iso_svc)
        ids = [o["id"] for o in orphans]
        assert "pd-skill-ghost1" in ids
        assert "pd-skill-ghost2" in ids
        assert "skill-a" not in ids  # 有效技能不当孤儿

    def test_builtin_file_track_not_orphan(self, iso_svc, tmp_path):
        """文件轨存在的内置技能(非 pd)即使主轨无也不当孤儿。"""
        _seed(iso_svc, tmp_path, skills=("skill-a",))
        # 文件轨建内置技能 context_aware(主轨不注册)
        iso_svc.file_store.create(
            "context_aware", meta={"id": "context_aware",
                                   "source": "legacy_migration"},
            instruction="# 上下文感知")
        legacy = tmp_path / "skills.json"
        legacy.write_text(json.dumps({"skills": [
            {"id": "context_aware", "enabled": True},
        ]}), encoding="utf-8")
        orphans = scan_orphans(iso_svc)
        assert "context_aware" not in [o["id"] for o in orphans]

    def test_cleanup_dry_run_no_delete(self, iso_svc, tmp_path):
        self._make_ghost_legacy(iso_svc, tmp_path, ghost_ids=["pd-skill-g1"])
        r = cleanup_orphans(iso_svc, dry_run=True)
        assert r["cleaned"] == []
        legacy = json.loads((tmp_path / "skills.json")
                            .read_text(encoding="utf-8"))
        assert "pd-skill-g1" in [s["id"] for s in legacy["skills"]]

    def test_cleanup_real_removes_ghost(self, iso_svc, tmp_path):
        self._make_ghost_legacy(iso_svc, tmp_path,
                                ghost_ids=["pd-skill-g1", "pd-skill-g2"])
        cleanup_orphans(iso_svc, dry_run=False)
        orphans = scan_orphans(iso_svc)
        assert orphans == []
        legacy = json.loads((tmp_path / "skills.json")
                            .read_text(encoding="utf-8"))
        legacy_ids = [s["id"] for s in legacy["skills"]]
        assert "pd-skill-g1" not in legacy_ids
        assert "skill-a" in legacy_ids  # 有效技能保留


class TestUnusedCleanup:
    """无用技能物理淘汰。"""

    def _make_archived_old(self, iso_svc, tmp_path):
        _seed(iso_svc, tmp_path, skills=("old-archived", "active-skill"))
        s = iso_svc.get("old-archived")
        s.status = SkillStatus.ARCHIVED.value
        s.metrics.usage_count = 0
        s.metrics.last_used_at = "2020-01-01T00:00:00"
        iso_svc.store.upsert(s)
        a = iso_svc.get("active-skill")
        a.metrics.usage_count = 5
        a.metrics.last_used_at = "2026-09-01T00:00:00"
        iso_svc.store.upsert(a)

    def test_scan_unused_finds_archived_only(self, iso_svc, tmp_path):
        self._make_archived_old(iso_svc, tmp_path)
        cand = scan_unused(iso_svc, archived_days=30)
        ids = [c["id"] for c in cand]
        assert "old-archived" in ids
        assert "active-skill" not in ids

    def test_cleanup_unused_dry_run(self, iso_svc, tmp_path):
        self._make_archived_old(iso_svc, tmp_path)
        r = cleanup_unused(iso_svc, dry_run=True, archived_days=30)
        assert r["removed"] == []
        assert iso_svc.store.get("old-archived") is not None

    def test_cleanup_unused_removes_archived(self, iso_svc, tmp_path):
        self._make_archived_old(iso_svc, tmp_path)
        r = cleanup_unused(iso_svc, dry_run=False, archived_days=30)
        assert "old-archived" in [x["id"] for x in r["removed"]]
        assert iso_svc.store.get("old-archived") is None
        assert iso_svc.store.get("active-skill") is not None

    def test_report_shape(self, iso_svc, tmp_path):
        _seed(iso_svc, tmp_path, skills=("skill-a",))
        rep = report(iso_svc)
        assert rep["ok"] is True
        assert rep["total_skills"] == 1
        assert "orphans" in rep and "unused" in rep


class TestServiceFacade:
    """service 门面代理。"""

    def test_cleanup_report_proxy(self, iso_svc, tmp_path):
        _seed(iso_svc, tmp_path, skills=("skill-a",))
        rep = iso_svc.cleanup_report()
        assert rep["ok"] is True

    def test_cleanup_orphans_proxy(self, iso_svc, tmp_path):
        _seed(iso_svc, tmp_path, skills=("skill-a",))
        r = iso_svc.cleanup_orphans(dry_run=True)
        assert "found" in r

    def test_cleanup_unused_proxy(self, iso_svc, tmp_path):
        _seed(iso_svc, tmp_path, skills=("skill-a",))
        r = iso_svc.cleanup_unused(dry_run=True)
        assert "found" in r
