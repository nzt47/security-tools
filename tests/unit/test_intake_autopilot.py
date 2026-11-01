# -*- coding: utf-8 -*-
"""入轨助手「自动修 + 一条龙 PR」的单元测试（2026-11-01 追加）。

覆盖两件事：
1. fix_front_matter 只补**可确定性推导**的字段（不翻译、不编造），干跑不落盘；
2. open_pr 的前置守卫：非 master、有无关改动、非法 id 一律拒绝（不做真 git 操作）。
"""
from __future__ import annotations

import importlib

intake = importlib.import_module("scripts.intake_skill")

GOOD = """---
id: demo-skill
name: demo-skill
description: Use when testing the intake script.
author: unknown
category: custom
tags:
- external
content_type: markdown
enabled: true
version: 0.1.0
status: approved
description_zh: 测试用技能。
source: test
---

# Demo Skill

## 何时使用

- 测试时使用。
"""

THIN = """---
id: thin-skill
name: 精简技能（自动整理）
description: 这是一个中文描述。
---

# 精简技能

正文。
"""


def _write(root, sid, text):
    d = root / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "skill.md").write_text(text, encoding="utf-8")
    return d


class TestFixFrontMatter:

    def test_补齐机械字段且不碰正文(self, tmp_path):
        d = _write(tmp_path, "thin-skill", THIN)
        rep = intake.fix_front_matter(d, execute=True)
        assert rep["ok"] is True
        assert {"version", "status", "category", "author", "tags", "content_type", "enabled"} <= set(rep["fixed"])
        text = (d / "skill.md").read_text(encoding="utf-8")
        assert text.startswith("---") and "# 精简技能" in text and "正文。" in text
        assert "description_zh: 这是一个中文描述。" in text
        assert "name: 精简技能" in text and "自动整理" not in text

    def test_干跑不落盘(self, tmp_path):
        d = _write(tmp_path, "thin-skill", THIN)
        before = (d / "skill.md").read_text(encoding="utf-8")
        rep = intake.fix_front_matter(d, execute=False)
        assert rep["changed"] is True
        assert (d / "skill.md").read_text(encoding="utf-8") == before

    def test_英文描述不自动翻译(self, tmp_path):
        text = "---\nid: en-skill\nname: En\ndescription: Use when testing.\n---\n\n# En\n\nbody\n"
        d = _write(tmp_path, "en-skill", text)
        rep = intake.fix_front_matter(d, execute=True)
        assert "description_zh" not in (d / "skill.md").read_text(encoding="utf-8")
        assert any("description_zh" in h for h in rep["needs_human"])

    def test_缺何时使用只提示不生成(self, tmp_path):
        d = _write(tmp_path, "thin-skill", THIN)
        rep = intake.fix_front_matter(d, execute=True)
        assert any("何时使用" in h for h in rep["needs_human"])
        assert "## 何时使用" not in (d / "skill.md").read_text(encoding="utf-8")

    def test_完整技能无需改动(self, tmp_path):
        d = _write(tmp_path, "demo-skill", GOOD)
        rep = intake.fix_front_matter(d, execute=False)
        assert rep["ok"] is True and rep["changed"] is False and rep["fixed"] == []

    def test_缺_front_matter_报错(self, tmp_path):
        d = _write(tmp_path, "no-fm", "# 只有正文\n")
        rep = intake.fix_front_matter(d, execute=True)
        assert rep["ok"] is False and "front matter" in rep["error"]


class TestOpenPrGuards:

    def test_拒绝非法_id(self):
        out = intake.open_pr("..")
        assert out["ok"] is False and "非法" in out["error"]

    def test_不在_master_上直接拒绝(self, monkeypatch):
        class _P:
            returncode = 0
            stdout = "security/workflow-permissions\n"
            stderr = ""
        monkeypatch.setattr(intake, "_run", lambda *a, **k: _P())
        out = intake.open_pr("demo-skill")
        assert out["ok"] is False and "master" in out["error"]

    def test_有无关改动就拒绝(self, monkeypatch):
        class _P:
            returncode = 0
            stdout = "master\n"
            stderr = ""
        monkeypatch.setattr(intake, "_run", lambda *a, **k: _P())
        monkeypatch.setattr(intake, "changed_paths",
                            lambda root=None: ["data/_draft.py", "data/skills_repo/demo-skill/skill.md"])
        out = intake.open_pr("demo-skill")
        assert out["ok"] is False and "无关的改动" in out["error"]

    def test_白名单只认该技能目录与派生物(self):
        paths = intake.allowed_commit_paths("demo-skill")
        assert any(p.startswith("data/skills_repo/demo-skill") for p in paths)
        assert intake._is_allowed("data/skills_repo/demo-skill/skill.md", "demo-skill") is True
        assert intake._is_allowed("data/skills_repo/other-skill/skill.md", "demo-skill") is False
        assert intake._is_allowed("data/_draft.py", "demo-skill") is False
