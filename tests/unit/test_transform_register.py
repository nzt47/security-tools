# -*- coding: utf-8 -*-
"""S5 第二批：把隔离区技能草稿登记进文件轨（transform/register.py）。

锁四件事：
  1. 只登记不启用（front matter 里 enabled=false 原样保留）；
  2. 目标已存在 ⇒ 拒绝，不覆盖；
  3. intake 校验失败 ⇒ 回滚刚写的目录（不留半成品）；
  4. 台账推进到 registered，并记录 target。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.skills_mgmt.transform import jobs
from agent.skills_mgmt.transform.register import RegisterError, register_skill_job

SKILL_MD = """---
id: gamma-draft
name: 伽马草稿
description: 从外来材料抽出来的技能草稿
author: yunshu-ingestion
category: custom
source: ingestion
tags: []
content_type: markdown
enabled: false
version: 0.1.0
status: draft
---

# 伽马草稿

## 何时使用

- 需要落地测试时
"""


def _draft(tmp_path: Path) -> Path:
    tdir = tmp_path / "drafts" / "ing_x"
    tdir.mkdir(parents=True)
    (tdir / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    (tdir / "CARD.md").write_text("# 伽马草稿\n\n## 何时使用\n\n- 需要落地测试时\n", encoding="utf-8")
    (tdir / "manifest.json").write_text(json.dumps({
        "draft_id": "ing_x", "form": "skill", "status": "draft", "enabled": False,
        "skill_id": "gamma-draft", "skill_name": "伽马草稿",
    }, ensure_ascii=False), encoding="utf-8")
    return tdir


def _job(tmp_path: Path, tdir: Path, *, form: str = "skill") -> dict:
    return jobs.upsert_job(source="ingestion", source_ref="ing_x", form=form,
                           title="伽马草稿", artifacts=str(tdir), root=tmp_path)


def _patch_intake(monkeypatch, *, ok: bool = True):
    calls = []

    def fake_intake(skill_id, **kwargs):
        calls.append((skill_id, kwargs))
        return {"ok": ok, "skill_id": skill_id, "stage": "done" if ok else "validate",
                "error": "" if ok else "缺必填键"}

    import scripts.intake_skill as intake_mod
    monkeypatch.setattr(intake_mod, "intake_one", fake_intake, raising=False)
    monkeypatch.setattr(intake_mod, "open_pr", lambda sid: {"ok": True, "pr_url": "https://example/pr/1"},
                        raising=False)
    return calls


def test_登记写出文件轨并推进台账(tmp_path, monkeypatch):
    tdir = _draft(tmp_path)
    job = _job(tmp_path, tdir)
    calls = _patch_intake(monkeypatch)
    rep = register_skill_job(job["job_id"], root=tmp_path)
    assert rep["ok"] is True and rep["skill_id"] == "gamma-draft"
    target = tmp_path / "data" / "skills_repo" / "gamma-draft"
    assert (target / "skill.md").is_file() and (target / "CARD.md").is_file()
    body = (target / "skill.md").read_text(encoding="utf-8")
    assert "enabled: false" in body, "登记不等于启用"
    assert rep["enabled"] is False
    assert calls and calls[0][0] == "gamma-draft" and calls[0][1].get("fix_meta") is True
    stored = jobs.get_job(job["job_id"], root=tmp_path)
    assert stored["status"] == "registered"
    assert stored["target"] == {"kind": "skill", "id": "gamma-draft"}


def test_目标已存在则拒绝且不覆盖(tmp_path, monkeypatch):
    tdir = _draft(tmp_path)
    job = _job(tmp_path, tdir)
    _patch_intake(monkeypatch)
    target = tmp_path / "data" / "skills_repo" / "gamma-draft"
    target.mkdir(parents=True)
    (target / "skill.md").write_text("原有内容", encoding="utf-8")
    with pytest.raises(RegisterError) as exc:
        register_skill_job(job["job_id"], root=tmp_path)
    assert exc.value.code == "skill-exists"
    assert (target / "skill.md").read_text(encoding="utf-8") == "原有内容"


def test_intake_校验失败会回滚目录(tmp_path, monkeypatch):
    tdir = _draft(tmp_path)
    job = _job(tmp_path, tdir)
    _patch_intake(monkeypatch, ok=False)
    with pytest.raises(RegisterError) as exc:
        register_skill_job(job["job_id"], root=tmp_path)
    assert exc.value.code == "intake-rejected"
    assert not (tmp_path / "data" / "skills_repo" / "gamma-draft").exists(), "失败必须回滚"
    assert jobs.get_job(job["job_id"], root=tmp_path)["status"] == "candidate"


def test_非技能形态拒绝(tmp_path, monkeypatch):
    tdir = _draft(tmp_path)
    job = _job(tmp_path, tdir, form="tool")
    _patch_intake(monkeypatch)
    with pytest.raises(RegisterError) as exc:
        register_skill_job(job["job_id"], root=tmp_path)
    assert exc.value.code == "job-form"


def test_已登记作业不能再登记(tmp_path, monkeypatch):
    tdir = _draft(tmp_path)
    job = _job(tmp_path, tdir)
    _patch_intake(monkeypatch)
    register_skill_job(job["job_id"], root=tmp_path)
    with pytest.raises(RegisterError) as exc:
        register_skill_job(job["job_id"], root=tmp_path)
    assert exc.value.code == "job-status"


def test_缺_SKILL_md_拒绝(tmp_path, monkeypatch):
    tdir = tmp_path / "drafts" / "ing_empty"
    tdir.mkdir(parents=True)
    job = _job(tmp_path, tdir)
    _patch_intake(monkeypatch)
    with pytest.raises(RegisterError) as exc:
        register_skill_job(job["job_id"], root=tmp_path)
    assert exc.value.code == "draft-missing"
