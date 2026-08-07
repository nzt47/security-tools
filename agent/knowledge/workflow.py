"""人机协同闭环工作流编排（任务7 · Step 1/3）。

编排 captured → distilled → discussed → carded → audited 五步闭环：
    Step1 收集     → ingest_file（任务1）
    Step2 提炼     → distill（任务3）
    Step3 深度讨论 → discuss / extract_card_insights（任务7 Step 2）
    Step4 产卡聚合 → promote_to_card（任务3→2） / card_from_discussion（任务7 Step 3）
    Step5 审计健康 → find_broken_links / find_orphans（轻量治理，任务5 未做时兜底）

【不易】人机边界：所有产卡结果状态均为 draft，必须人工确认
（transition → current）才转当前有效——AI 只簿记不裁决。
【不易】降级铁律：任一 LLM 环节（提炼/讨论/提炼讨论字段）失败时降级为
骨架产物，不抛异常；仅"使用错误"（素材/笔记/讨论不存在）抛异常。
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import date
from enum import Enum
from pathlib import Path

from agent.knowledge.card import CardStore
from agent.knowledge.discuss import (
    discuss,
    extract_card_insights,
    load_discussion,
)
from agent.knowledge.distill import Note, distill, promote_to_card
from agent.knowledge.ingest import get_knowledge_root, ingest_file
from agent.knowledge.links import find_broken_links, find_orphans
from agent.knowledge.logbook import append_log
from agent.knowledge.schema import Card, slugify, validate_card

logger = logging.getLogger(__name__)


class WorkflowState(str, Enum):
    CAPTURED = "captured"      # 已收集
    DISTILLED = "distilled"    # 已提炼
    DISCUSSED = "discussed"    # 已讨论
    CARDED = "carded"          # 已产卡
    AUDITED = "audited"        # 已审计


class WorkflowRunner:
    """编排 captured → distilled → discussed → carded → audited"""

    def __init__(self, knowledge_root: str | None = None, llm=None):
        self.root = get_knowledge_root(knowledge_root)
        self.llm = llm

    # ── Step1: 收集 ────────────────────────────────────────────

    def run_ingest(self, src_path: str | Path, dest_layer: str = "inbox",
                   source_type: str | None = None) -> str:
        """调用任务1 ingest_file，返回入库 slug。"""
        result = ingest_file(str(src_path), dest_layer=dest_layer,
                             source_type=source_type,
                             knowledge_root=str(self.root))
        logger.info("[workflow] Step1 收集成功 slug=%s layer=%s",
                    result.slug, result.layer)
        return result.slug

    # ── Step2: 提炼 ────────────────────────────────────────────

    def run_distill(self, src_path: str | Path) -> Note:
        """调用任务3 distill，返回结构化笔记（LLM 不可用自动降级骨架）。"""
        note = distill(src_path, llm=self.llm, knowledge_root=str(self.root))
        logger.info("[workflow] Step2 提炼完成 source=%s slug=%s distilled=%s reason=%s",
                    src_path, note.slug, note.distilled, note.reason or "none")
        return note

    # ── Step3: 深度讨论 ────────────────────────────────────────

    def run_discuss(self, note_slug: str, question: str) -> str:
        """深度讨论，返回讨论记录文件路径（LLM 不可用降级骨架记录）。"""
        path = discuss(note_slug, question, llm=self.llm,
                       knowledge_root=str(self.root))
        logger.info("[workflow] Step3 讨论完成 note_slug=%s → %s", note_slug, path)
        return path

    # ── Step4: 产卡 ────────────────────────────────────────────

    def run_card(self, note_slug: str, card_type: str = "concepts") -> str:
        """从已确认（approved）笔记产卡，返回卡片 slug。

        前置：笔记须人工 approve（promote_to_card 强制校验）。
        """
        card = promote_to_card(note_slug, card_type=card_type,
                               knowledge_root=str(self.root),
                               wiki_root=str(self.root / "wiki"))
        logger.info("[workflow] Step4 产卡成功 slug=%s（状态 draft，待人工转 current）",
                    card.slug)
        return card.slug

    def card_from_discussion(self, discussion_path: str | Path,
                             card_type: str = "concepts") -> str:
        """从讨论记录提炼「一句话核心洞见 + 适用边界」生成知识卡片（任务2 CardStore）。

        映射（任务7 规格 Step 3）：
            one_line_insight → insight
            适用边界         → scope
            建议交叉引用     → links
            讨论标记的矛盾   → contradictions（status=conflict，不自动裁决）
        产卡后状态 draft，必须人工 transition → current（人机边界）。
        返回卡片 slug。
        """
        path = Path(discussion_path)
        logger.info("[workflow] 讨论产卡请求 discussion=%s card_type=%s",
                    path.name, card_type)
        if not path.is_file():
            logger.warning("[workflow] 讨论记录不存在，终止产卡: %s", path)
            raise FileNotFoundError(f"讨论记录不存在: {discussion_path}")
        disc = load_discussion(path)

        # 提炼讨论字段（LLM 可用时自动补充；不可用时用已回填值）
        extracted = extract_card_insights(path, llm=self.llm)
        links = list(disc.links or extracted["links"])
        conflicts = list(disc.conflicts or extracted["conflicts"])
        card = Card(
            title=disc.title or disc.topic,
            slug=slugify(disc.title or disc.topic),
            status="draft",  # 必须人工确认才转 current
            type=card_type,
            source=disc.source or f"discussion:{path.name}",
            date=disc.distill_date or date.today().isoformat(),
            links=links,
            contradictions=[{"target_slug": s, "status": "conflict"}
                            for s in conflicts],
            insight=disc.insight or extracted["one_line_insight"],
            scope=disc.scope or extracted["scope"],
            content=_discussion_to_card_body(disc, extracted),
            metadata={
                "source_card": f"processed/{path.name}",
                "distilled": disc.distilled,
                "llm_model": disc.llm_model,
            },
        )
        if card.slug != slugify(card.title):
            # 讨论主题标题被消歧（同题不同来源）：置显式 slug 豁免
            logger.info("[workflow] 讨论产卡 slug 消歧: %s → explicit_slug 豁免",
                        card.slug)
            card.explicit_slug = True
        errors = validate_card(asdict(card))
        if errors:
            logger.warning("[workflow] 讨论产卡校验失败 slug=%s 违规=%s",
                           card.slug, errors)
            raise ValueError("讨论产卡校验失败: " + "; ".join(errors))
        logger.info("[workflow] 讨论产卡校验通过 slug=%s insight=%r scope=%r links=%s conflicts=%s",
                    card.slug, card.insight, card.scope, card.links, conflicts)
        store = CardStore(self.root / "wiki")
        store.create(card)
        append_log("card_from_discussion", card.slug,
                   f"source={path.name}",
                   log_path=str(self.root / "log.md"))
        logger.info("[workflow] 讨论产卡成功 slug=%s ← discussion=%s（draft，待人工转 current）",
                    card.slug, path.name)
        return card.slug

    # ── Step5: 审计 ────────────────────────────────────────────

    def run_audit(self) -> dict:
        """轻量治理：断链 + 孤儿检测，返回健康报告。

        任务5 完整治理（mark_conflict/resolve）未实现前，作为兜底巡检；
        已复用任务2 links 模块（find_broken_links/find_orphans）。
        """
        store = CardStore(self.root / "wiki")
        cards = store.list()
        broken = find_broken_links(cards, store)
        orphans = find_orphans(cards)
        report = {
            "total_cards": len(cards),
            "broken_links": broken,
            "orphans": orphans,
            "ok": not broken and not orphans,
            "audited_at": date.today().isoformat(),
        }
        logger.info("[workflow] Step5 审计完成 卡片=%s 断链=%s 孤儿=%s ok=%s",
                    report["total_cards"], len(broken), len(orphans), report["ok"])
        return report


def _discussion_to_card_body(disc, extracted: dict) -> str:
    """讨论记录 → 卡片正文：结论摘要 + 问答要点 + 冲突标记 + 相关概念双链。"""
    lines: list[str] = []
    insight = disc.insight or extracted.get("one_line_insight") or ""
    scope = disc.scope or extracted.get("scope") or ""
    if insight:
        lines += ["## 一句话洞见", insight, ""]
    if scope:
        lines += ["## 适用边界", scope, ""]
    if disc.content:
        lines += ["## 讨论记录", "", disc.content.strip(), ""]
    links = list(disc.links or extracted.get("links") or [])
    if links:
        lines.append("## 相关概念")
        lines.extend(f"- [[{s}]]" for s in links)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
