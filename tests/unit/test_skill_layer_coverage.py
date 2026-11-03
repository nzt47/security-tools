# -*- coding: utf-8 -*-
"""S4 前置：分层保留率基线脚本的可测内核（只读）。

锁的是**判据**，不是数字（技能库变了比例自然会变）：
  · L2 里已有的面（步骤/触发），L1 必须保住；
  · 保留率达标（≥GATE_PCT）才算「可以切 L1 装配」。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "skill_layer_coverage", ROOT / "scripts" / "audit" / "skill_layer_coverage.py")
cov = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cov)


def _mk(repo: Path, name: str, body: str) -> None:
    d = repo / name
    d.mkdir(parents=True)
    (d / "skill.md").write_text(
        "---\nid: %s\nname: %s\ndescription: 演示\n---\n\n" % (name, name) + body,
        encoding="utf-8")


def test_步骤保留率_按_L2_是否真有步骤来算(tmp_path):
    _mk(tmp_path, "with-steps", "## 何时使用\n- 需要时\n\n## 步骤\n- 第一步\n")
    _mk(tmp_path, "no-steps", "## 何时使用\n- 需要时\n")
    data = cov.measure(tmp_path)
    assert data["skills"] == 2
    assert data["procedural"] == 1        # 只有一条技能"本来就有步骤"
    assert data["steps_kept"] == 1
    assert data["steps_kept_pct"] == 100.0


def test_触发保留率_英文标题也算(tmp_path):
    _mk(tmp_path, "en-skill", "## When to Use This Skill\n- 需要时\n")
    data = cov.measure(tmp_path)
    assert data["triggered"] == 1
    assert data["trigger_kept_pct"] == 100.0


def test_英文_Step_标题也能抽成步骤(tmp_path):
    """实测漏点：find-skills 的步骤写在 ### Step 1..6（没有 how to 字样）。"""
    _mk(tmp_path, "en-steps",
        "## When to Use\n- 需要时\n\n"
        "### Step 1: 先看需求\n- 读一遍需求\n\n"
        "### Step 2: 再查库\n- 查能力索引\n")
    data = cov.measure(tmp_path)
    assert data["procedural"] == 1
    assert data["steps_kept"] == 1


def test_有卡片时计入_with_card_md(tmp_path):
    _mk(tmp_path, "carded", "## 何时使用\n- 需要时\n\n## 步骤\n- 第一步\n")
    (tmp_path / "carded" / "CARD.md").write_text("# 卡\n", encoding="utf-8")
    data = cov.measure(tmp_path)
    assert data["with_card_md"] == 1
    assert data["gate_pct"] == cov.GATE_PCT