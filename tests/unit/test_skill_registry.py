"""legacy 迁移单测：统一技能注册表（SkillRegistry）。

覆盖：
    - is_enabled/set_enabled：主轨与文件轨双源
    - toggle 写对应轨
    - as_legacy_rows 兼容视图（主轨+文件轨合并、无重复）
    - persona 技能（仅文件轨）可被识别与切换
"""

import pathlib

import pytest

from agent.skills_mgmt import SkillsMgmtService
from agent.skills_mgmt.registry import SkillRegistry


@pytest.fixture
def iso_svc(tmp_path):
    svc = SkillsMgmtService(
        store_path=str(tmp_path / "skills_mgmt.json"),
        repo_path=str(tmp_path / "skills_repo"),
    )
    return svc


@pytest.fixture
def reg(iso_svc):
    return SkillRegistry(service=iso_svc)


class TestRegistryRead:
    def test_main_track_skill(self, reg, iso_svc):
        iso_svc.create_manual({"id": "main-1", "name": "m",
                               "content": "# x", "content_type": "markdown"})
        assert reg.is_enabled("main-1") is True
        assert "main-1" in reg.list_skill_ids()

    def test_persona_file_track_skill(self, reg, iso_svc):
        """仅文件轨的 persona 技能（主轨未注册）应被识别。"""
        iso_svc.file_store.create(
            "self_reflection",
            meta={"id": "self_reflection", "enabled": True,
                  "status": "approved", "source": "legacy_migration"},
            instruction="# 自省")
        assert reg.is_enabled("self_reflection") is True
        assert "self_reflection" in reg.list_skill_ids()

    def test_unknown_default_true(self, reg):
        assert reg.is_enabled("nope") is True  # 历史语义：缺失视为启用

    def test_disabled_persona(self, reg, iso_svc):
        iso_svc.file_store.create(
            "voice_interaction",
            meta={"id": "voice_interaction", "enabled": False,
                  "status": "approved"},
            instruction="# 语音")
        assert reg.is_enabled("voice_interaction") is False


class TestRegistryWrite:
    def test_set_enabled_main(self, reg, iso_svc):
        iso_svc.create_manual({"id": "main-1", "name": "m",
                               "content": "# x", "content_type": "markdown"})
        r = reg.set_enabled("main-1", False)
        assert r["track"] == "main"
        assert reg.is_enabled("main-1") is False
        # 主轨确实改了
        assert iso_svc.get("main-1").enabled is False

    def test_set_enabled_file_track(self, reg, iso_svc):
        iso_svc.file_store.create(
            "self_reflection",
            meta={"id": "self_reflection", "enabled": True,
                  "status": "approved"},
            instruction="# 自省")
        r = reg.set_enabled("self_reflection", False)
        assert r["track"] == "file_track"
        assert reg.is_enabled("self_reflection") is False
        # front matter 改了
        meta = iso_svc.file_store.get_metadata("self_reflection")
        assert meta["enabled"] is False

    def test_toggle(self, reg, iso_svc):
        iso_svc.file_store.create(
            "safety_guard", meta={"id": "safety_guard", "enabled": True,
                                  "status": "approved"}, instruction="# 安全")
        r = reg.toggle("safety_guard")
        assert r["enabled"] is False
        assert reg.is_enabled("safety_guard") is False

    def test_set_enabled_unknown(self, reg):
        r = reg.set_enabled("nope", False)
        assert r["ok"] is False


class TestLegacyView:
    def test_as_legacy_rows_merges_no_dup(self, reg, iso_svc):
        iso_svc.create_manual({"id": "main-1", "name": "m",
                               "content": "# x", "content_type": "markdown"})
        iso_svc.file_store.create(
            "context_aware", meta={"id": "context_aware", "enabled": True,
                                   "status": "approved"},
            instruction="# 上下文")
        rows = reg.as_legacy_rows()
        ids = [r["id"] for r in rows]
        assert "main-1" in ids and "context_aware" in ids
        assert len(ids) == len(set(ids))  # 无重复
        # 行结构与旧 data/skills.json 同构
        row = next(r for r in rows if r["id"] == "main-1")
        assert set(row.keys()) >= {"id", "name", "enabled", "description",
                                   "params"}

    def test_legacy_view_reflects_toggle(self, reg, iso_svc):
        iso_svc.file_store.create(
            "proactive_suggestion",
            meta={"id": "proactive_suggestion", "enabled": True,
                  "status": "approved"}, instruction="# 主动")
        reg.set_enabled("proactive_suggestion", False)
        rows = reg.as_legacy_rows()
        row = next(r for r in rows if r["id"] == "proactive_suggestion")
        assert row["enabled"] is False
