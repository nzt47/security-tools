"""知识卡片 → Skill DRAFT 连接器（TASK-04 Step 1）

背景（Why）:
    knowledge（素材→Note→卡片）与 skills_mgmt（Skill 三层检索/审核/版本）
    长期相互独立：knowledge 对 skills_mgmt 仅引用 RRF 常量，从不创建 Skill。
    本模块补齐"经验蒸馏 → Skill 沉淀"断点，只消费已确认卡片产出 DRAFT 草稿。

【不易】约束（禁止触碰）:
    - 不改 knowledge 卡片状态机 / distill.py 人工审批门控（approved+distilled 判定不改）
    - 不改 skills_mgmt 模型 / 状态机 / create_manual / reviewer 接口签名（仅追加调用链）
    - 只产 DRAFT，绝不自动注册/发布（复用 creator 既有落盘路径：防连点锁+版本快照+legacy 同步）
    - "approved 卡片"语义适配（变更说明裁决 R1）：Card 状态机无 approved 态
      （approved 仅存在于 distill.py 的 Note 层，promote_to_card 已强制），
      故判定 = status==current（人工确认有效态）+ metadata.distilled==True（已蒸馏产卡）

幂等 / 去重:
    - 幂等: 转换成功写回卡片 metadata.converted_to_skill = skill_id，重复转换跳过
    - 去重: 复用 skills_mgmt.reviewer.DuplicateDetector（Jaccard≥0.7 + 内容哈希），重复跳过并记录
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# knowledge 默认布局（与 agent/knowledge/__main__.py 的 _DEFAULT_WIKI 一致）
DEFAULT_WIKI_ROOT = "knowledge/wiki"
# 去重阈值（与 merge_skills Jaccard≥0.7 语义一致，见 task 4 不变式）
DEDUP_JACCARD_THRESHOLD = 0.7
# Skill.name 模型上限（models.Skill.name max_length=200）
SKILL_NAME_MAX_LEN = 200


class KnowledgeSkillBridge:
    """知识卡片 → Skill DRAFT 连接器

    Args:
        card_store: CardStore 实例（None 时按 wiki_root 懒加载）
        skills_store: SkillStore 实例（None 时用默认 data/skills_mgmt.json）
        creator: SkillCreator 实例（None 时基于 skills_store 懒加载）
        llm_client: LLM 客户端（None 时 AIAssistedGenerator 走模板降级）
        wiki_root: CardStore 默认根目录（仅当 card_store 未注入时使用）
    """

    # 可转换卡片判定（裁决 R1）：人工确认有效态 + 已蒸馏
    ELIGIBLE_STATUS = "current"

    def __init__(self, *, card_store: Optional[Any] = None,
                 skills_store: Optional[Any] = None,
                 creator: Optional[Any] = None,
                 llm_client: Optional[Any] = None,
                 wiki_root: str = DEFAULT_WIKI_ROOT):
        self._card_store = card_store
        self._skills_store = skills_store
        self._creator = creator
        self._llm_client = llm_client
        self._wiki_root = wiki_root
        # 最近一次转换结果明细（CLI/调用方展示跳过原因用）
        self.last_result: Dict[str, Any] = {}

    # ─── 懒加载（保持 knowledge 包轻量，依赖面随用随入） ───

    def _store(self) -> Any:
        if self._skills_store is None:
            from agent.skills_mgmt.store import SkillStore
            self._skills_store = SkillStore()
        return self._skills_store

    def _get_creator(self) -> Any:
        if self._creator is None:
            from agent.skills_mgmt.creator import SkillCreator
            self._creator = SkillCreator(self._store())
        return self._creator

    def _cards(self) -> Any:
        if self._card_store is None:
            from agent.knowledge.card import CardStore
            self._card_store = CardStore(self._wiki_root)
        return self._card_store

    # ─── 判定与字段映射 ───

    @classmethod
    def is_eligible(cls, card) -> bool:
        """可转换卡片：人工确认有效态(current) + 已蒸馏(distilled=True)。"""
        return (card.status == cls.ELIGIBLE_STATUS
                and bool(card.metadata.get("distilled")))

    @staticmethod
    def _skill_name(card) -> str:
        """Skill 名称：卡片标题截断至模型上限(200)。"""
        name = (card.title or card.slug or "knowledge-card").strip()
        return name[:SKILL_NAME_MAX_LEN]

    @staticmethod
    def _skill_intent(card) -> str:
        """Skill 意图：one_line_insight + 正文要点预览（映射 insight/core_points）。"""
        parts = [card.insight or ""]
        if card.content:
            parts.append(card.content[:500])
        return "\n".join(p for p in parts if p).strip() or card.slug

    # ─── 核心 ───

    def card_to_skill_draft(self, card, *, dry_run: bool = False) -> Optional[str]:
        """单卡 → Skill DRAFT；返回 skill_id，跳过/失败返回 None（原因在 last_result）。

        顺序（Why）: 先判定 → 幂等跳过 → 骨架生成 → 去重 → 落盘 → 写回幂等标记。
        """
        self.last_result = {"slug": card.slug, "skill_id": None,
                            "skipped": True, "reason": ""}

        if not self.is_eligible(card):
            self.last_result["reason"] = "not_eligible"
            logger.info(
                "[SkillBridge] 跳过 %s: 非可转换卡片(status=%s distilled=%s)",
                card.slug, card.status, card.metadata.get("distilled"),
            )
            return None

        existing = card.metadata.get("converted_to_skill")
        if existing:
            self.last_result["reason"] = "already_converted"
            self.last_result["skill_id"] = existing
            logger.info("[SkillBridge] 跳过 %s: 已转换 skill=%s（幂等）",
                        card.slug, existing)
            return existing

        from agent.skills_mgmt.creator import AIAssistedGenerator
        from agent.skills_mgmt.exceptions import SkillAlreadyExistsError
        from agent.skills_mgmt.reviewer import DuplicateDetector

        # 1) 骨架生成（LLM 不可用自动降级模板）；先不落盘以便去重前置
        draft = AIAssistedGenerator(llm_client=self._llm_client).generate(
            name=self._skill_name(card),
            intent=self._skill_intent(card),
            category="custom",
            tags=([t for t in (card.tags or []) if isinstance(t, str)][:10]
                  or ["knowledge_card"]),
        )

        # 2) 去重：与已有技能 Jaccard≥0.7 或内容哈希一致 → 跳过并记录
        dup_score, dup_with = DuplicateDetector(
            threshold=DEDUP_JACCARD_THRESHOLD).detect(draft, self._store().list_all())
        if dup_with:
            self.last_result["reason"] = "duplicate"
            self.last_result["duplicate_with"] = dup_with
            logger.info("[SkillBridge] 跳过 %s: 与已有技能重复 %s (jaccard=%.2f)",
                        card.slug, dup_with, dup_score / 100.0)
            return None

        if dry_run:
            self.last_result["skill_id"] = draft.id
            self.last_result["skipped"] = False
            logger.info("[SkillBridge] dry-run 卡片=%s 将产出 DRAFT skill=%s",
                        card.slug, draft.id)
            return draft.id

        # 3) 落盘：复用 creator 既有链路（防连点锁 + 版本快照 + legacy 同步）
        try:
            committed = self._get_creator()._commit_new_skill(draft)
        except SkillAlreadyExistsError:
            self.last_result["reason"] = "duplicate"
            logger.info("[SkillBridge] 跳过 %s: 同 id 技能已存在（视为重复）", card.slug)
            return None
        except Exception as e:  # noqa: BLE001 落盘失败不阻断批量
            self.last_result["reason"] = "commit_failed"
            self.last_result["error"] = str(e)
            logger.warning("[SkillBridge] 落盘失败 %s: %s", card.slug, e)
            return None

        # 4) 幂等标记写回卡片 frontmatter（失败不阻断，skill 已落盘）
        try:
            card.metadata["converted_to_skill"] = committed.id
            self._cards().update(card)
        except Exception as e:  # noqa: BLE001
            logger.warning("[SkillBridge] 幂等标记写回失败 %s: %s", card.slug, e)

        self.last_result.update({"skill_id": committed.id, "skipped": False})
        _kpi_record("skill")
        logger.info("[SkillBridge] 转换成功 %s → skill=%s(DRAFT)", card.slug, committed.id)
        return committed.id

    def convert_cards(self, *, dry_run: bool = False) -> List[Dict[str, Any]]:
        """批量转换所有可转换卡片；dry_run=True 只产出预览，不落盘不写标记。"""
        cards = [c for c in self._cards().list() if self.is_eligible(c)]
        logger.info("[SkillBridge] convert-cards 开始: 可转换卡片=%d dry_run=%s",
                    len(cards), dry_run)
        results: List[Dict[str, Any]] = []
        for card in cards:
            self.card_to_skill_draft(card, dry_run=dry_run)
            results.append(dict(self.last_result))
        created = sum(1 for r in results
                      if r.get("skill_id") and not r.get("skipped"))
        logger.info("[SkillBridge] convert-cards 完成: 共=%d 产出=%d 跳过=%d",
                    len(results), created, len(results) - created)
        return results


def _kpi_record(artifact_type: str) -> None:
    """沉淀增量 KPI（TASK-03 learning.artifacts.*）；埋点不可用静默降级。"""
    try:
        from agent.learning_metrics import get_learning_metrics
        get_learning_metrics().record_artifact(artifact_type)
    except Exception as e:  # noqa: BLE001 KPI 埋点失败不影响主链路
        logger.debug("[SkillBridge] KPI record_artifact 失败: %s", e)
