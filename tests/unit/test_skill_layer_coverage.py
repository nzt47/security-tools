# -*- coding: utf-8 -*-
"""S4 前置：分层覆盖率基线脚本的可测内核（只读）。

基线存在的意义：C-8 要求「接入分层装配前先有回归基线」。这条用例锁住脚本的**判据**，
不是锁住某个数字 —— 技能库变了，比例自然会变。
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


def test_可动手_需要触发与步骤两面(tmp_path):
    _mk(tmp_path, "with-both", "## 何时使用\n- 需要时\n\n## 步骤\n- 第一步\n")
    _mk(tmp_path, "only-trigger", "## 何时使用\n- 需要时\n")
    data = cov.measure(tmp_path)
    assert data["skills"] == 2
    assert data["with_card_md"] == 0 and data["derived"] == 2
    assert data["actionable_l1"] == 1
    assert 0 < data["actionable_pct"] < 100


def test_省多少与_L2_长度挂钩(tmp_path):
    _mk(tmp_path, "long", "## 何时使用\n- 需要时\n\n## 步骤\n- 第一步\n" + "\n详细内容\n" * 200)
    data = cov.measure(tmp_path)
    assert data["rows"][0]["l2_chars"] > data["rows"][0]["l1_chars"]
    assert data["avg_saving_pct"] > 0


def test_有卡片时计入_with_card_md(tmp_path):
    _mk(tmp_path, "carded", "## 何时使用\n- 需要时\n\n## 步骤\n- 第一步\n")
    (tmp_path / "carded" / "CARD.md").write_text("# 卡\n", encoding="utf-8")
    data = cov.measure(tmp_path)
    assert data["with_card_md"] == 1