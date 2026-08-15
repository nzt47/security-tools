"""TASK-04 回归测试 · skill_bridge 连接器（英文卡片 + 显式 slug 场景）

来源（Why）: 2026-08-14 手工 mock 验证脚本固化。该验证确认了 approved
卡片（status=current + metadata.distilled=True，裁决 R1）可成功产出 Skill
DRAFT，且草稿不进入 skills store 之外的任何发布链路。

与 test_knowledge_skill_bridge.py 的分工: 该文件覆盖中文场景与 CLI/dry-run；
本文件覆盖英文卡片 + 显式 slug（explicit_slug 豁免 slugify 一致性校验）
+ 真实落盘重读验证，避免重复。

守【不易】: 全部使用 tmp_path 隔离（CardStore/SkillStore），绝不触碰真实
knowledge/ 与 data/；断言草稿状态恒为 DRAFT，绝不自动注册/发布。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.knowledge.card import CardStore
from agent.knowledge.schema import Card
from agent.knowledge.skill_bridge import KnowledgeSkillBridge
from agent.skills_mgmt.models import SkillStatus
from agent.skills_mgmt.store import SkillStore

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def stores(tmp_path):
    """隔离的卡片库 + 技能库。"""
    return CardStore(str(tmp_path / "wiki")), SkillStore(str(tmp_path / "skills.json"))


def make_english_card(**overrides) -> Card:
    """构造英文卡片（模拟 2026-08-14 手工验证的 mock 数据）。"""
    params = dict(
        title="Deploy Nginx to production cluster",
        slug="deploy-nginx-prod",  # 与 slugify(title) 不一致 → 需 explicit_slug
        status="current",
        type="insights",
        source="distill",
        date="2026-08-14",
        tags=["deploy", "nginx"],
        insight="Nginx production deploy uses blue-green for smooth rollback",
        content=("Steps: 1) build image 2) push registry 3) switch traffic "
                 "to green 4) health check 5) rollback plan."),
        metadata={"distilled": True},
        explicit_slug=True,  # 显式 slug 豁免（仅内存标记，不写 frontmatter）
    )
    params.update(overrides)
    return Card(**params)


def test_approved_english_card_produces_draft(stores):
    """approved（current + distilled）英文卡片 → Skill DRAFT 落盘，幂等标记写回。"""
    card_store, skill_store = stores
    card = make_english_card()
    card_store.create(card)

    bridge = KnowledgeSkillBridge(card_store=card_store, skills_store=skill_store)
    skill_id = bridge.card_to_skill_draft(card)

    assert skill_id  # 转换成功
    assert bridge.last_result["skipped"] is False
    skill = skill_store.get(skill_id)
    assert skill is not None
    assert skill.status == SkillStatus.DRAFT  # 只产草稿，绝不自动注册/发布
    assert skill.name == "Deploy Nginx to production cluster"
    assert skill.content  # LLM 不可用 → 模板降级，内容非空
    # 幂等标记写回卡片 frontmatter（重读磁盘验证）
    persisted = card_store.get(card.slug)
    assert persisted.metadata.get("converted_to_skill") == skill_id


def test_second_convert_idempotent_returns_existing(stores):
    """二次转换幂等跳过，返回已存在 skill_id，store 零新增。"""
    card_store, skill_store = stores
    card = make_english_card()
    card_store.create(card)

    bridge = KnowledgeSkillBridge(card_store=card_store, skills_store=skill_store)
    first = bridge.card_to_skill_draft(card)
    assert first

    second = bridge.card_to_skill_draft(card)
    assert second == first
    assert bridge.last_result["reason"] == "already_converted"
    assert skill_store.count() == 1  # 零新增


def test_not_distilled_card_rejected(stores):
    """未蒸馏卡片（distilled 缺失）→ 判定不可转换，无产出。"""
    card_store, skill_store = stores
    card = make_english_card(metadata={})  # 未蒸馏
    card_store.create(card)

    bridge = KnowledgeSkillBridge(card_store=card_store, skills_store=skill_store)
    assert bridge.card_to_skill_draft(card) is None
    assert bridge.last_result["reason"] == "not_eligible"
    assert KnowledgeSkillBridge.is_eligible(card) is False
    assert skill_store.count() == 0
