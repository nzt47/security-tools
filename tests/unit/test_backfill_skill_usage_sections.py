# -*- coding: utf-8 -*-
"""skill.md「何时使用」批量补写脚本（scripts/backfill_skill_usage_sections.py）。

用户 2026-10-29 的口径是「复读描述」：只从 front matter 的 description 拆出触发条件，
不新增描述里没有的能力。这里锁四件事：拆分口径、尾注清洗、插入位置、幂等。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load():
    path = ROOT / "scripts" / "backfill_skill_usage_sections.py"
    spec = importlib.util.spec_from_file_location("backfill_skill_usage_sections", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BF = _load()

GOOD = """---
id: demo
description: Use when testing a demo skill. 由 1 份素材蒸馏生成
---

# Demo

这是介绍段落。

## 步骤
- 一
"""


def test_derive_section_from_english_description():
    heading, bullets = BF.derive_section("Use when asked to rename identifiers, or update API calls")
    assert heading == "## When to Use"
    assert bullets == ["Asked to rename identifiers, or update API calls"]


def test_derive_section_chinese_keeps_sentence_whole():
    heading, bullets = BF.derive_section("在对话中表达情感色彩，让回应更生动")
    assert heading == "## 何时使用"
    assert bullets == ["在对话中表达情感色彩，让回应更生动"]


def test_derive_section_strips_distill_trailer_and_quotes():
    heading, bullets = BF.derive_section("'Use when debugging。由 1 份素材蒸馏生成'", "pd-x")
    assert heading == "## When to Use"
    assert bullets == ["Debugging"]
    assert all("蒸馏" not in b for b in bullets)


def test_derive_section_skips_empty_description():
    assert BF.derive_section("") == ("", [])


def test_insert_after_intro_paragraph():
    out = BF.insert_section(GOOD, "## When to Use", ["Testing a demo skill"])
    assert out is not None
    lines = out.splitlines()
    intro = lines.index("这是介绍段落。")
    heading = lines.index("## When to Use")
    steps = lines.index("## 步骤")
    assert intro < heading < steps
    assert "- Testing a demo skill" in lines


def test_insert_is_idempotent_when_section_exists():
    once = BF.insert_section(GOOD, "## When to Use", ["Testing a demo skill"])
    assert once is not None
    assert BF.insert_section(once, "## When to Use", ["Testing a demo skill"]) is None


def test_insert_without_h1_puts_section_first():
    text = "---\nid: x\ndescription: Use when x\n---\n\n正文第一段。\n"
    out = BF.insert_section(text, "## When to Use", ["X"])
    assert out is not None
    assert "## When to Use" in out.splitlines()[:8]


def test_front_matter_is_preserved_byte_for_byte():
    out = BF.insert_section(GOOD, "## When to Use", ["Testing a demo skill"])
    assert out.startswith("---\nid: demo\ndescription: Use when testing a demo skill. 由 1 份素材蒸馏生成\n---\n")
