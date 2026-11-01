# -*- coding: utf-8 -*-
"""能力卡（agent/skills_mgmt/capability_cards.py）的单元测试。

守三件事：
1. 有 CARD.md 就用卡（layer=card），没有就从 skill.md 派生（layer=derived）；
2. 五个面（触发/步骤/参数/示例/约束）按标题抽取，抽不到就是空数组（**不编造**）；
3. 卡片是 L1：摘要与各面都要截断，不能把全文塞进列表。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.skills_mgmt.capability_cards import build_card, build_cards, _MAX_CHARS

SKILL = """---
id: demo-skill
name: 演示技能
description_zh: 用来演示能力卡的抽取口径，描述本身很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长。
category: custom
tags: [demo, test]
---

# 演示技能

## 何时使用

- 需要对账时
- 需要导出台账时

## 步骤

1. 先拉取清单
2. 再比对差异

## 参数

- `--since`：起始时间

## 示例

```bash
python demo.py --since 2026-01-01
```

## 注意

- 别在生产库上跑
"""


def _mk(root: Path, text: str = SKILL, card: str = ""):
    d = root / "demo-skill"
    d.mkdir(parents=True, exist_ok=True)
    (d / "skill.md").write_text(text, encoding="utf-8")
    if card:
        (d / "CARD.md").write_text(card, encoding="utf-8")
    return d


class TestBuildCard:

    def test_从_skill_md_派生五个面(self, tmp_path):
        d = _mk(tmp_path)
        card = build_card(d)
        assert card["layer"] == "derived"
        assert card["skill_id"] == "demo-skill" and card["name"] == "演示技能"
        assert card["trigger"] == ["需要对账时", "需要导出台账时"]
        assert card["steps"] == ["先拉取清单", "再比对差异"]
        assert card["params"] and "--since" in card["params"][0]
        assert "python demo.py" in card["example"]
        assert card["guard"] == ["别在生产库上跑"]

    def test_有_CARD_md_就用卡(self, tmp_path):
        d = _mk(tmp_path, card="---\nid: demo-skill\nname: 卡片版\n---\n\n## 何时使用\n\n- 卡片里的触发\n")
        card = build_card(d)
        assert card["layer"] == "card"
        assert card["name"] == "卡片版" and card["trigger"] == ["卡片里的触发"]

    def test_摘要与各面都截断(self, tmp_path):
        big = SKILL.replace("## 步骤", "## 步骤\n\n" + "\n".join("- 步骤%d" % i for i in range(200)))
        d = _mk(tmp_path, text=big)
        card = build_card(d)
        assert len(card["summary"]) <= 61
        assert len(card["steps"]) <= 6

    def test_抽不到就是空数组不编造(self, tmp_path):
        d = _mk(tmp_path, text="---\nid: demo-skill\nname: 空\n---\n\n# 空\n\n正文。\n")
        card = build_card(d)
        assert card["trigger"] == [] and card["steps"] == [] and card["guard"] == []

    def test_没有_skill_md_返回_None(self, tmp_path):
        d = tmp_path / "empty"
        d.mkdir()
        assert build_card(d) is None


def test_真实文件轨能出卡():
    cards = build_cards()
    assert len(cards) >= 40, "真实文件轨应有 40+ 条"
    assert all(c["skill_id"] and c["layer"] in ("card", "derived") for c in cards)
    assert any(c["trigger"] for c in cards), "至少有一部分技能能抽出触发面"
