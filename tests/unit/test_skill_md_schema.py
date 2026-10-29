# -*- coding: utf-8 -*-
"""skill.md 形状门禁（agent/skills_mgmt/skill_md_schema.py）。

守三件事：① 键缺失/分隔符粘连/id 不一致必须算 error（这些会让索引或双轨静默错位）；
② 「值为空」只算 warning（不逼着编造作者/标签）；③ 真实文件轨 0 error —— 这条就是 CI 门禁。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent.skills_mgmt import skill_md_schema as schema  # noqa: E402

GOOD = """---
id: demo-skill
name: demo-skill
description: demo
description_zh: 演示
author: someone
category: custom
source: test
tags:
- demo
content_type: markdown
enabled: true
status: current
version: 1.0.0
---

# Demo

## 何时使用

- 需要演示时。

## 示例

    demo()
"""


def test_valid_skill_passes_with_no_warnings():
    r = schema.validate_text(GOOD, dir_name="demo-skill")
    assert r["ok"] is True
    assert r["errors"] == []
    assert r["warnings"] == []


def test_missing_required_key_is_error():
    text = GOOD.replace("author: someone\n", "")
    r = schema.validate_text(text, dir_name="demo-skill")
    assert r["ok"] is False
    assert any("author" in e for e in r["errors"])


def test_glued_front_matter_separator_is_error():
    # 陷阱 17 的真实形态：`---# 标题` 不是独占一行的分隔符，整段元数据会被静默跳过。
    text = GOOD.replace("---\nid:", "---# demo\nid:", 1)
    r = schema.validate_text(text, dir_name="demo-skill")
    assert r["ok"] is False
    assert any("front matter" in e for e in r["errors"])


def test_id_mismatch_with_directory_is_error():
    r = schema.validate_text(GOOD, dir_name="other-dir")
    assert r["ok"] is False
    assert any("不一致" in e for e in r["errors"])


def test_empty_author_is_warning_not_error():
    text = GOOD.replace("author: someone", "author: ''")
    r = schema.validate_text(text, dir_name="demo-skill")
    assert r["ok"] is True
    assert any("author" in w and "为空" in w for w in r["warnings"])


def test_missing_structure_sections_are_only_warnings():
    text = GOOD.split("## 何时使用")[0]
    r = schema.validate_text(text, dir_name="demo-skill")
    assert r["ok"] is True
    joined = "；".join(r["warnings"])
    assert "何时使用" in joined and "示例" in joined


def test_missing_file_is_error_not_exception(tmp_path):
    r = schema.validate_file(tmp_path / "nope" / "skill.md")
    assert r["ok"] is False and r["errors"]


def test_trigger_section_counts_as_usage():
    """`## 触发条件 / 适用场景` 也算「何时使用」（2026-10-29 实测 pd-* 用它们写触发面）。"""
    text = GOOD.replace("## 何时使用", "## 触发条件")
    r = schema.validate_text(text, dir_name="demo-skill")
    assert r["ok"] is True
    assert not any("何时使用" in w for w in r["warnings"])


@pytest.mark.skipif(not (ROOT / "data" / "skills_repo").is_dir(), reason="本仓库无文件轨")
def test_real_repo_has_no_schema_errors():
    report = schema.scan_root(ROOT / "data" / "skills_repo")
    assert report["checked"] >= 30, report["checked"]
    bad = [(i["skill_id"], i["errors"]) for i in report["items"] if not i["ok"]]
    assert bad == [], bad
