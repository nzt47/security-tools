# -*- coding: utf-8 -*-
"""「生成文件轨」——主轨独有技能补 skill.md（2026-10-29）。

用户实测：技能「智能体身份模板库」（templates）点「应用草稿并评审」→ 409
「没有文件轨…请先用工具栏『登记仅运行时技能』」，而那个按钮做的是**反方向**（文件轨→主轨），
根本补不了文件轨。于是新增 service.materialize_file_track + 路由 + 菜单项。

这里锁四件事：
① dry-run 只预览、不落盘；
② 落盘走 file_store.create：front matter 来自主轨元数据、正文取主轨 content；
③ 已有 skill.md / 目录已存在 ⇒ 拒绝（不覆盖、不移动）；
④ 落盘后 apply_redraft 能正常写 description_zh（原 409 场景闭环）。
"""
from __future__ import annotations

import pytest

from agent.skills_mgmt.exceptions import SkillFileError
from agent.skills_mgmt.models import Skill
from agent.skills_mgmt.service import SkillsMgmtService


def _svc(tmp_path):
    return SkillsMgmtService(
        store_path=str(tmp_path / "skills.json"),
        repo_path=str(tmp_path / "repo"),
        class_registry_path=str(tmp_path / "classes.json"),
    )


def _seed(svc, skill_id="templates", content="# 模板库\n\n这里是正文。") -> Skill:
    skill = Skill(id=skill_id, name="智能体身份模板库", description="agent identity templates",
                  content=content, author="云枢", source="manual")
    svc.store.upsert(skill)
    return skill


def test_dry_run_does_not_write(tmp_path):
    svc = _svc(tmp_path)
    _seed(svc)
    out = svc.materialize_file_track("templates", dry_run=True)
    assert out["dry_run"] is True and out["action"] == "would_create"
    assert not (tmp_path / "repo" / "templates" / "skill.md").exists()


def test_create_writes_front_matter_and_body(tmp_path):
    svc = _svc(tmp_path)
    _seed(svc)
    out = svc.materialize_file_track("templates", dry_run=False)
    md = tmp_path / "repo" / "templates" / "skill.md"
    assert out["action"] == "created" and md.is_file()
    meta, body, _scripts, _temp = svc.file_store.read("templates")
    assert meta["id"] == "templates"
    assert meta["name"] == "智能体身份模板库"
    assert meta["description"] == "agent identity templates"
    assert "模板库" in body


def test_refuses_to_overwrite_existing_file_track(tmp_path):
    svc = _svc(tmp_path)
    _seed(svc)
    svc.materialize_file_track("templates", dry_run=False)
    with pytest.raises(SkillFileError):
        svc.materialize_file_track("templates", dry_run=True)


def test_refuses_when_dir_exists_without_skill_md(tmp_path):
    svc = _svc(tmp_path)
    _seed(svc)
    (tmp_path / "repo" / "templates").mkdir(parents=True)
    with pytest.raises(SkillFileError):
        svc.materialize_file_track("templates", dry_run=False)


def test_redraft_works_after_materialize(tmp_path):
    """原 409 场景闭环：补完文件轨后，再定义 apply 能写中文说明。"""
    svc = _svc(tmp_path)
    _seed(svc)
    svc.materialize_file_track("templates", dry_run=False)
    out = svc.apply_redraft("templates", description_zh="智能体身份模板：给分身选人格与身份底稿。")
    assert out["applied"] == ["description_zh"]
    meta, _body, _s, _t = svc.file_store.read("templates")
    assert meta["description_zh"].startswith("智能体身份模板")
