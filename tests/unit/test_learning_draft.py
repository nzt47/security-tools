# -*- coding: utf-8 -*-
"""学习草稿落地（`transform/learning.write_learning_draft`）。

锁三件事：
1. 产物与摄取草稿同形（SKILL.md 9 键 + CARD.md + manifest.skill_id）⇒ 队列的
   「登记为技能」对两种来源一视同仁；
2. 台账记 `source=learning` / `status=candidate`（学习不再绕过队列）；
3. 非法 id 直接拒，不写半成品。
"""
from __future__ import annotations

import json

import pytest

from agent.skills_mgmt.transform import learning


def test_写草稿_同形且进台账(monkeypatch, tmp_path):
    captured: dict = {}
    from agent.skills_mgmt.transform import jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "upsert_job", lambda **kw: captured.update(kw))

    info = learning.write_learning_draft(
        skill_id="mem-summary",
        name="记忆摘要",
        description="把对话压成摘要",
        body="## 步骤\n1. 读对话\n2. 提炼要点\n",
        tags=["memory"],
        source_ref="memory:c1",
        draft_dir=tmp_path,
    )

    root = tmp_path / info["draft_id"]
    skill_md = (root / "SKILL.md").read_text(encoding="utf-8")
    for key in ("id:", "name:", "description:", "author:", "category:",
                "source:", "tags:", "content_type:", "enabled:", "version:",
                "status:"):
        assert key in skill_md, f"front matter 缺 {key}"
    assert "enabled: false" in skill_md and "status: draft" in skill_md
    assert (root / "CARD.md").is_file()

    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["skill_id"] == "mem-summary"
    assert manifest["form"] == "skill" and manifest["status"] == "draft"
    assert manifest["adapter"] == "learning"

    assert captured.get("source") == "learning"
    assert captured.get("status") == "candidate"
    assert captured.get("form") == "skill"


def test_非法_id_拒绝(tmp_path):
    with pytest.raises(ValueError):
        learning.write_learning_draft(
            skill_id="Bad ID!", name="x", description="y", body="z",
            draft_dir=tmp_path,
        )
    assert not any(tmp_path.iterdir())
