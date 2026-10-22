"""文件轨 → 资产主轨补登记（统一表「仅运行时」行的治理入口）。

锁四件事：
  1. dry_run 只列候选、不写主轨；
  2. apply 后主轨有行、status/来源如实镜像文件轨，非法 category 归一为 custom；
  3. 登记后跑咨询性评估（review 非空），但不发布、不改 front matter；
  4. 幂等：再跑不再有候选。
"""
from __future__ import annotations

import json

from agent.skills_mgmt.service import SkillsMgmtService

SKILL_MD = """---
id: {sid}
name: 演示技能
description: 补登记用例
version: 0.1.0
category: {category}
status: {status}
enabled: true
tags:
- demo
---

# 演示技能

这是一段用于补登记测试的技能正文。
"""


def _svc(tmp_path):
    repo = tmp_path / "repo"
    (repo / "demo-skill").mkdir(parents=True, exist_ok=True)
    (repo / "demo-skill" / "skill.md").write_text(
        SKILL_MD.format(sid="demo-skill", category="example", status="approved"),
        encoding="utf-8")
    svc = SkillsMgmtService(store_path=str(tmp_path / "skills.json"),
                            repo_path=str(repo))
    # 评估事件写全局日志会污染真实 data/；本用例只测登记语义
    svc._emit_assessment_event = lambda *a, **k: None  # type: ignore[method-assign]
    return svc


def test_backfill_dry_run_does_not_write_main_track(tmp_path):
    svc = _svc(tmp_path)
    res = svc.backfill_file_track(dry_run=True)
    assert res["dry_run"] is True
    assert [c["id"] for c in res["candidates"]] == ["demo-skill"]
    assert svc.store.get("demo-skill") is None


def test_backfill_apply_registers_asset_and_keeps_file_track(tmp_path):
    svc = _svc(tmp_path)
    md_before = (tmp_path / "repo" / "demo-skill" / "skill.md").read_text(encoding="utf-8")

    res = svc.backfill_file_track(dry_run=False)
    assert res["dry_run"] is False
    assert [c["id"] for c in res["created"]] == ["demo-skill"]
    assert res["skipped"] == []

    skill = svc.store.get("demo-skill")
    assert skill is not None
    assert skill.status == "approved"          # 镜像文件轨现状
    assert skill.source == "file_track_backfill"
    assert skill.category == "custom"           # 非法 category=example 归一
    assert skill.enabled is True
    assert "演示技能" in skill.content
    assert skill.review is not None             # 咨询性评估已写入
    # 文件轨原样（不写 skills_repo、不改 front matter）
    assert (tmp_path / "repo" / "demo-skill" / "skill.md").read_text(encoding="utf-8") == md_before


def test_backfill_is_idempotent(tmp_path):
    svc = _svc(tmp_path)
    svc.backfill_file_track(dry_run=False)
    again = svc.backfill_file_track(dry_run=True)
    assert again["candidates"] == []
    applied = svc.backfill_file_track(dry_run=False)
    assert applied["created"] == []
