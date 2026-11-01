# -*- coding: utf-8 -*-
"""技能转化器 P2（--apply 落地）的测试。

守四件事：
1. 干跑不写盘；execute 才落 transforms/ 与 CARD.md；
2. 幂等：同一份内容 → 同一个 transform_id（不会每次生成新目录）；
3. `--new` 会写 front matter（9 个必填键齐）+ 正文 + CARD.md；
4. 审核 block 的产物标 blocked（调用方据此不启动转化）。
"""
from __future__ import annotations

from pathlib import Path

from agent.skills_mgmt.transform import apply_plan, render_card

SKILL = """---
id: gamma-skill
name: 伽马技能
description: 用于测试落地流程的一段描述。
---

# 伽马技能

## 何时使用

- 需要落地测试时

## 步骤

1. 先做什么
2. 再做什么

## 示例

```bash
echo hi
```
"""


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "data" / "skills_repo").mkdir(parents=True)
    (root / "data" / "capability_manifest.json").write_text(
        '{"entries": []}', encoding="utf-8")
    return root


def _artifact(tmp_path: Path, text: str = SKILL) -> Path:
    p = tmp_path / "incoming.md"
    p.write_text(text, encoding="utf-8")
    return p


def test_干跑不写盘(tmp_path):
    root = _repo(tmp_path)
    art = _artifact(tmp_path)
    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    rep = apply_plan(art, new="gamma-skill", execute=False, root=root)
    after = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    assert rep["ok"] is True and rep["written"] == []
    assert before == after


def test_execute_落归档与_CARD(tmp_path):
    root = _repo(tmp_path)
    art = _artifact(tmp_path)
    rep = apply_plan(art, new="gamma-skill", execute=True, root=root)
    tdir = Path(rep["transform_dir"])
    for name in ("raw.md", "card.md", "compare.json", "review.json", "manifest.json"):
        assert (tdir / name).is_file(), name
    skill_dir = root / "data" / "skills_repo" / "gamma-skill"
    card = (skill_dir / "CARD.md").read_text(encoding="utf-8")
    assert "## 何时使用" in card and "需要落地测试时" in card
    body = (skill_dir / "skill.md").read_text(encoding="utf-8")
    for key in ("id: gamma-skill", "author:", "category:", "source:", "tags:", "content_type:", "enabled:", "version:", "status:"):
        assert key in body, key


def test_幂等_同一内容同_id(tmp_path):
    root = _repo(tmp_path)
    art = _artifact(tmp_path)
    a = apply_plan(art, execute=False, root=root)
    b = apply_plan(art, execute=False, root=root)
    assert a["transform_id"] == b["transform_id"]
    assert len(a["transform_id"]) == 12


def test_危险产物标_blocked(tmp_path):
    root = _repo(tmp_path)
    art = _artifact(tmp_path, SKILL + "\nrm -rf /\ntoken: ghp_" + "A" * 30 + "\n")
    rep = apply_plan(art, execute=False, root=root)
    assert rep["review_verdict"] == "block" and rep["blocked"] is True


def test_非法_id_被拒(tmp_path):
    root = _repo(tmp_path)
    art = _artifact(tmp_path)
    assert apply_plan(art, new="../evil", execute=False, root=root)["ok"] is False
    assert apply_plan(art, into="Bad Id", execute=False, root=root)["ok"] is False


def test_render_card_空面给占位(tmp_path):
    md = render_card({"name": "x", "summary": "s", "trigger": [], "steps": [],
                      "params": [], "guard": [], "example": ""})
    assert "原文没有这一节" in md and "原文没有示例" in md
