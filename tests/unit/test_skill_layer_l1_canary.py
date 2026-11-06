# -*- coding: utf-8 -*-
"""C-8 L1 装配灰度的守卫（默认 full；灰名单内 L1 必须覆盖 L2 的步骤/触发面）。

锁的是**契约**，不是数字：
  · 默认（不设 env）必须逐字走旧路径 L2 全文 —— 灰度不能悄悄成为默认；
  · MODE=l1 且技能在名单内 ⇒ 只注入 L1；名单外 / 名单空 / 开关拼错 / L1 缺卡 一律回落 L2；
  · 两处注入（inject_instruction 与 inject_sensitive_skill）共用同一判定，不许只改一处；
  · 灰名单本身是真实技能（有目录 + CARD.md），且 L1 覆盖 L2 里已有的步骤/触发面。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from agent.skills_mgmt import layered
from agent.skills_mgmt.context_injector import ContextInjector

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "skill_layer_coverage", ROOT / "scripts" / "audit" / "skill_layer_coverage.py")
cov = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cov)

#: 与 layered.DEFAULT_L1_IDS 同源；这里显式写死一份，改名单时两边一起改（防"改了没人发现"）
CANARY = ("pdf", "gh-cli", "code-refactor", "engineering-test-delivery", "handoff")
REPO = ROOT / "data" / "skills_repo"


class _StubLoader:
    """只记录有没有被调用：走了 L1 就不该再读 L2 全文。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def load_instruction(self, skill_id: str) -> dict:
        self.calls.append(skill_id)
        return {"skill_id": skill_id, "instruction": "## 何时使用\n- L2 全文占位",
                "estimated_tokens": 12, "instruction_chars": 20, "layer": 2}

    def list_all_metadata(self, *, enabled_only: bool = False) -> list:
        return []


def _inj() -> tuple[ContextInjector, _StubLoader]:
    loader = _StubLoader()
    return ContextInjector(loader=loader), loader


def test_默认_full_逐字走_L2_全文(monkeypatch):
    monkeypatch.delenv("CP_SKILL_LAYER_MODE", raising=False)
    monkeypatch.delenv("CP_SKILL_LAYER_L1_IDS", raising=False)
    inj, loader = _inj()
    got = inj.inject_instruction("pdf")
    assert got["layer"] == 2
    assert loader.calls == ["pdf"]
    assert "L2 全文占位" in got["prompt"]


def test_名单内切到_L1_且不再读_L2(monkeypatch):
    monkeypatch.setenv("CP_SKILL_LAYER_MODE", "l1")
    monkeypatch.setenv("CP_SKILL_LAYER_L1_IDS", "pdf")
    inj, loader = _inj()
    got = inj.inject_instruction("pdf")
    assert got["layer"] == 1
    assert loader.calls == []
    l1_text = layered.load_layer("pdf", "L1")["text"]
    assert l1_text.splitlines()[0] in got["prompt"]
    assert got["estimated_tokens"] > 0


def test_名单外仍是_L2(monkeypatch):
    monkeypatch.setenv("CP_SKILL_LAYER_MODE", "l1")
    monkeypatch.setenv("CP_SKILL_LAYER_L1_IDS", "pdf")
    inj, loader = _inj()
    got = inj.inject_instruction("gstack")
    assert got["layer"] == 2
    assert loader.calls == ["gstack"]


@pytest.mark.parametrize("mode", ["", "full", "L2", "l 1", "layered", "1"])
def test_开关不是_l1_就回退_full(monkeypatch, mode):
    monkeypatch.setenv("CP_SKILL_LAYER_MODE", mode)
    monkeypatch.setenv("CP_SKILL_LAYER_L1_IDS", "pdf")
    inj, loader = _inj()
    assert inj.inject_instruction("pdf")["layer"] == 2
    assert loader.calls == ["pdf"]


def test_白名单空串等于回滚(monkeypatch):
    monkeypatch.setenv("CP_SKILL_LAYER_MODE", "l1")
    monkeypatch.setenv("CP_SKILL_LAYER_L1_IDS", "  ,  ")
    inj, loader = _inj()
    assert inj.inject_instruction("pdf")["layer"] == 2
    assert loader.calls == ["pdf"]


def test_L1_缺卡时回落_L2(monkeypatch):
    """灰名单写错/技能只有主轨没有目录：宁可多花 token，也不能让技能用不了。"""
    monkeypatch.setenv("CP_SKILL_LAYER_MODE", "l1")
    monkeypatch.setenv("CP_SKILL_LAYER_L1_IDS", "definitely-not-a-skill")
    inj, loader = _inj()
    got = inj.inject_instruction("definitely-not-a-skill")
    assert got["layer"] == 2
    assert loader.calls == ["definitely-not-a-skill"]


def test_敏感技能注入也走同一开关(monkeypatch):
    """两处 load_instruction 必须同源：只改 inject_instruction 会让隔离窗口仍读 L2。"""
    monkeypatch.setenv("CP_SKILL_LAYER_MODE", "l1")
    monkeypatch.setenv("CP_SKILL_LAYER_L1_IDS", "pdf")
    inj, loader = _inj()
    got = inj.inject_sensitive_skill("pdf")
    assert loader.calls == []
    l1_text = layered.load_layer("pdf", "L1")["text"]
    assert l1_text.splitlines()[0] in got["prompt"]


def test_灰度名单是真实技能且有_CARD_md():
    """交接文档里那份名单把工具名（git/run_lint/notify/read_pdf）当成了技能，这里钉住。"""
    assert tuple(layered.DEFAULT_L1_IDS) == CANARY
    assert len(set(CANARY)) == len(CANARY) == 5
    if not REPO.is_dir():
        pytest.skip("技能仓库不在（CI 精简检出）")
    missing = [s for s in CANARY if not (REPO / s / "CARD.md").is_file()]
    assert missing == [], "灰名单里的技能没有 CARD.md（L1 会降级，灰度就不成立）: %s" % missing


def test_灰名单内_L1_覆盖_L2_的全部步骤与触发面():
    """与 scripts/audit/skill_layer_coverage.py 同口径：L2 里有的一面，L1 不许丢。"""
    if not REPO.is_dir():
        pytest.skip("技能仓库不在（CI 精简检出）")
    data = cov.measure(REPO)
    rows = {r["skill_id"]: r for r in data["rows"]}
    checked = 0
    for sid in CANARY:
        r = rows.get(sid)
        assert r is not None, "灰名单技能不在基线里: %s" % sid
        if r["has_steps"]:
            assert r["steps_kept"] is True, "%s 的 L1 丢了步骤面" % sid
            checked += 1
        if r["has_trigger"]:
            assert r["trigger_kept"] is True, "%s 的 L1 丢了触发面" % sid
            checked += 1
    assert checked > 0, "5 条试点一条步骤/触发面都没有，灰度的判据就是空转"
    # 整体基线（全部技能）也不许被这 5 条拉低
    assert data["steps_kept_pct"] == 100.0
    assert data["trigger_kept_pct"] == 100.0
