"""TASK-04 Step 1 · 知识卡片 → Skill DRAFT 连接器测试

覆盖（验收 §6 功能验收前三条 + 测试要求）:
    1. approved 卡片（status=current + metadata.distilled=True）→ Skill DRAFT（字段映射完整）
    2. 未 approved 卡片（draft/未蒸馏）→ 拒绝，不含 DRAFT 产出
    3. 重复卡片（与已有技能 Jaccard≥0.7/内容哈希一致）→ 跳过并记录
    4. 幂等：已转换卡片重复执行零新增（converted_to_skill 标记）
    5. dry-run：只产出预览，不落盘、不写幂等标记
    6. LLM 不可用 → 模板降级（内容非空且含模板骨架）
    7. CLI convert-cards 子命令退出码 0 + 汇总输出

守【不易】: 全部使用 tmp_path 隔离（CardStore/SkillStore），绝不触碰真实 knowledge/ 与 data/。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent.knowledge.card import CardStore
from agent.knowledge.schema import Card, slugify
from agent.knowledge.skill_bridge import KnowledgeSkillBridge
from agent.skills_mgmt.models import (
    ContentType,
    Skill,
    SkillCategory,
    SkillStatus,
)
from agent.skills_mgmt.store import SkillStore

_REPO_ROOT = Path(__file__).resolve().parents[2]


def make_card(
    title: str,
    *,
    status: str = "current",
    metadata=None,
    insight: str = "一句话核心洞见",
    content: str = "",
    tags=None,
) -> Card:
    """构造卡片（契约：slug = slugify(title)）。"""
    card = Card(
        title=title,
        slug=slugify(title),
        status=status,
        type="concepts",
        source="inbox/test.md",
        date="2026-08-02",
        tags=list(tags or []),
        links=[],
        contradictions=[],
        insight=insight,
        metadata=dict(metadata or {}),
    )
    card.content = content
    return card


@pytest.fixture()
def stores(tmp_path):
    """隔离的卡片库 + 技能库。"""
    card_store = CardStore(str(tmp_path / "wiki"))
    skill_store = SkillStore(str(tmp_path / "skills.json"))
    return card_store, skill_store


def _bridge(card_store, skill_store):
    return KnowledgeSkillBridge(card_store=card_store, skills_store=skill_store)


# ─── 连接器核心 ───


def test_eligible_card_produces_draft_skill(stores):
    """approved 卡片（current + distilled）→ Skill DRAFT，字段映射完整。"""
    card_store, skill_store = stores
    card = make_card("测试沉淀技能", metadata={"distilled": True},
                     content="核心要点\n- 要点1\n- 要点2")
    card_store.create(card)

    skill_id = _bridge(card_store, skill_store).card_to_skill_draft(card)

    assert skill_id
    skill = skill_store.get(skill_id)
    assert skill is not None
    assert skill.status == SkillStatus.DRAFT  # 只产草稿，绝不自动注册/发布
    assert skill.name == "测试沉淀技能"
    assert "一句话核心洞见" in skill.description
    assert skill.content  # LLM 不可用 → 模板降级，内容非空
    # 幂等标记已写回卡片 frontmatter（重读磁盘验证）
    persisted = card_store.get(card.slug)
    assert persisted.metadata.get("converted_to_skill") == skill_id


def test_not_eligible_card_rejected(stores):
    """未 approved 卡片（draft 状态 / 未蒸馏）→ 拒绝，无 DRAFT 产出。"""
    card_store, skill_store = stores
    card_store.create(make_card("未确认卡片", status="draft",
                                metadata={"distilled": True}))
    card_store.create(make_card("未蒸馏卡片", status="current",
                                metadata={}))

    bridge = _bridge(card_store, skill_store)
    for c in card_store.list():
        assert bridge.card_to_skill_draft(c) is None
        assert bridge.last_result["reason"] == "not_eligible"
    assert skill_store.count() == 0  # 不含 DRAFT 产出


def test_duplicate_card_skipped(stores, monkeypatch):
    """与已有技能内容哈希一致 → 判定重复，跳过并记录（零新增）。"""
    from agent.skills_mgmt.creator import AIAssistedGenerator as _RealGen

    class _FakeGen:
        """确定性生成器：无论输入输出固定内容 → 内容哈希必然一致。"""

        def __init__(self, llm_client=None):
            pass

        def generate(self, *, name, intent, category="custom", tags=None):
            import re

            sid = re.sub(r"[^a-zA-Z0-9]+", "-", name.lower()).strip("-")
            return Skill(
                id=sid or "skill", name=name, description=intent[:2000],
                category=SkillCategory.AI_GENERATED, tags=list(tags or []),
                status=SkillStatus.DRAFT, source="ai_assisted",
                author="ai_assistant", content="FIXED_BODY",
                content_type=ContentType.MARKDOWN, version="0.1.0",
            )

    monkeypatch.setattr("agent.skills_mgmt.creator.AIAssistedGenerator", _FakeGen)

    card_store, skill_store = stores
    card_a = make_card("技能甲", metadata={"distilled": True}, content="内容A")
    card_b = make_card("技能乙", metadata={"distilled": True}, content="内容A")
    card_store.create(card_a)
    card_store.create(card_b)

    bridge = _bridge(card_store, skill_store)
    assert bridge.card_to_skill_draft(card_a)  # 第一张成功
    assert bridge.card_to_skill_draft(card_b) is None  # 第二张重复跳过
    assert bridge.last_result["reason"] == "duplicate"
    assert skill_store.count() == 1  # 零新增


def test_idempotent_second_convert_skips(stores):
    """已转换卡片重复执行 → 幂等跳过，零新增。"""
    card_store, skill_store = stores
    card = make_card("幂等卡片", metadata={"distilled": True}, content="正文")
    card_store.create(card)

    bridge = _bridge(card_store, skill_store)
    first = bridge.card_to_skill_draft(card)
    assert first

    second = bridge.card_to_skill_draft(card)  # 同一对象，标记已在 metadata
    assert second == first
    assert bridge.last_result["reason"] == "already_converted"
    assert skill_store.count() == 1


def test_dry_run_no_side_effects(stores):
    """dry-run：只产出预览 skill_id，不落盘、不写幂等标记。"""
    card_store, skill_store = stores
    card = make_card("预览卡片", metadata={"distilled": True}, content="正文")
    card_store.create(card)

    results = _bridge(card_store, skill_store).convert_cards(dry_run=True)

    assert len(results) == 1
    assert results[0]["skill_id"]  # 预览 id 存在
    assert results[0]["skipped"] is False
    assert skill_store.count() == 0  # 不落盘
    assert card_store.get(card.slug).metadata.get("converted_to_skill") is None


def test_llm_unavailable_uses_template_fallback(stores):
    """LLM 客户端为 None → 模板降级，草稿内容含模板骨架（非空）。"""
    card_store, skill_store = stores
    card = make_card("模板降级卡片", metadata={"distilled": True},
                     insight="用于演示降级路径")
    card_store.create(card)

    skill_id = _bridge(card_store, skill_store).card_to_skill_draft(card)
    skill = skill_store.get(skill_id)
    assert skill is not None
    assert "触发条件" in skill.content or "适用场景" in skill.content


# ─── CLI 入口（subprocess，验证退出码契约）───


def test_cli_convert_cards_dry_run_exit_zero(tmp_path):
    """convert-cards --dry-run → exit 0，输出含汇总行与卡片 slug。"""
    wiki = tmp_path / "wiki"
    card_store = CardStore(str(wiki))
    card_store.create(make_card("CLI转换卡片", metadata={"distilled": True}))
    skills_store = tmp_path / "skills.json"

    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [sys.executable, "-m", "agent.knowledge", "convert-cards",
         "--wiki", str(wiki), "--skills-store", str(skills_store),
         "--dry-run"],
        capture_output=True, text=True, encoding="utf-8",
        cwd=str(_REPO_ROOT), env=env, timeout=90,
    )
    assert proc.returncode == 0, f"stderr={proc.stderr}"
    assert "cli转换卡片" in proc.stdout  # CLI 按 slug 输出
    assert "产出 1" in proc.stdout
    assert SkillStore(str(skills_store)).count() == 0  # dry-run 不落盘
