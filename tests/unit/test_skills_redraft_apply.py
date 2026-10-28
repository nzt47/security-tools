# -*- coding: utf-8 -*-
"""「再定义」落地 + 「使用说明」回退 的守卫用例。

背景（2026-10-28 用户实测）：
1. 点「应用草稿并评审」后说明一字未变 —— 因为 apply 走的是
   PATCH /api/skills-mgmt/<id>，而主轨白名单自 G1-B/M0（2026-09-26）起
   **不含** description / description_zh，PATCH 会静默忽略它们（200 但没改）。
   正确写路径只有 skill.md front matter（file_store.update_meta）。
2. 8 个只有主轨的技能在「详情 · 使用说明」报 HTTP 404 —— 文件轨缺失被
   冒泡成 SkillNotFoundError（=技能不存在），而它们的正文其实在主轨 content 里。

本文件同时钉住：没有文件轨时必须**显式报错**（不能再静默成功）、
字段超长必须拒绝、说明回退要标明来源（source=main_track）。
"""
from __future__ import annotations

import pytest

from agent.skills_mgmt import SkillsMgmtService
from agent.skills_mgmt.exceptions import (
    SkillFileError,
    SkillNotFoundError,
    SkillValidationError,
)


@pytest.fixture
def svc(tmp_path):
    """隔离主轨与文件轨（默认 repo_path 会写进生产 data/skills_repo）。"""
    return SkillsMgmtService(
        store_path=str(tmp_path / "skills_mgmt.json"),
        repo_path=str(tmp_path / "skills_repo"),
    )


def _make(svc, skill_id="rd-file", content="# 正文\n这是主轨正文。\n",
          make_file=True, description="english description"):
    svc.create_manual({
        "id": skill_id, "name": "测试技能", "description": description,
        "content": content, "content_type": "markdown", "category": "custom",
        "tags": ["rd"], "author": "tester",
    })
    md = svc.file_store.repo_path / skill_id / "skill.md"
    if make_file and not md.exists():
        svc.file_store.create(skill_id, {
            "name": "测试技能", "description": description,
            "category": "custom", "tags": ["rd"],
        }, instruction=content)
    return skill_id


def _front_matter(svc, skill_id):
    return (svc.file_store.repo_path / skill_id / "skill.md").read_text(encoding="utf-8")


class TestApplyRedraft:
    def test_writes_description_zh_into_skill_md(self, svc):
        sid = _make(svc)
        out = svc.apply_redraft(sid, description_zh="把 PDF 拆成结构化文本，供检索用。",
                                source="llm")
        assert out["applied"] == ["description_zh"], out
        assert out["source"] == "llm"
        md = _front_matter(svc, sid)
        assert "description_zh:" in md
        assert "把 PDF 拆成结构化文本" in md
        # 落盘后元数据索引立即可见（UI 的 /api/skills 就吃这一份）
        meta = svc.file_store.load_metadata_index(refresh=True).get(sid, {})
        assert meta.get("description_zh") == "把 PDF 拆成结构化文本，供检索用。"

    def test_name_change_syncs_main_track(self, svc):
        sid = _make(svc)
        out = svc.apply_redraft(sid, name="PDF 解析器", description_zh="新的中文说明")
        assert sorted(out["applied"]) == ["description_zh", "name"], out
        assert svc.get(sid).name == "PDF 解析器"
        assert "name: PDF 解析器" in _front_matter(svc, sid)

    def test_same_values_are_rejected_not_silently_ok(self, svc):
        sid = _make(svc)
        svc.apply_redraft(sid, description_zh="同一句话")
        with pytest.raises(SkillValidationError) as ei:
            svc.apply_redraft(sid, description_zh="同一句话")
        assert "没有需要写入" in str(ei.value)

    def test_missing_file_track_raises_explicitly(self, svc):
        sid = _make(svc, make_file=False)
        with pytest.raises(SkillFileError) as ei:
            svc.apply_redraft(sid, description_zh="无处落地的说明")
        assert "没有文件轨" in str(ei.value)
        # 且不能把说明偷偷写进主轨 description（那是检索字段，不是中文展示文案）
        assert svc.get(sid).description == "english description"

    def test_too_long_description_is_rejected(self, svc):
        sid = _make(svc)
        with pytest.raises(SkillValidationError) as ei:
            svc.apply_redraft(sid, description_zh="长" * 401)
        assert "≤400" in str(ei.value)

    def test_unknown_skill_raises_not_found(self, svc):
        with pytest.raises(SkillNotFoundError):
            svc.apply_redraft("no-such-skill", description_zh="x")


class TestInstructionFallback:
    def test_file_track_is_preferred(self, svc):
        sid = _make(svc, content="# 主轨正文\n主轨里的说明\n")
        out = svc.load_skill_instruction(sid)
        assert out["source"] == "file"
        assert "主轨正文" in out["instruction"]

    def test_missing_file_falls_back_to_main_track(self, svc):
        sid = _make(svc, make_file=False, content="# 只有主轨\n这段正文必须能读到\n")
        out = svc.load_skill_instruction(sid)
        assert out["source"] == "main_track"
        assert "这段正文必须能读到" in out["instruction"]
        assert out["instruction_chars"] == len(out["instruction"])
        assert out["hint"], "回退必须在响应里标明，否则 UI 会以为这是文件轨正文"

    def test_unknown_skill_still_404(self, svc):
        with pytest.raises(SkillNotFoundError):
            svc.load_skill_instruction("no-such-skill")
