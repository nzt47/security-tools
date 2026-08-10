"""人机协同闭环工作流编排（任务7 · Step 1/3）。

编排 captured → distilled → discussed → carded → audited 五步闭环：
    Step1 收集     → ingest_file（任务1）
    Step2 提炼     → distill（任务3）
    Step3 深度讨论 → discuss / extract_card_insights（任务7 Step 2）
    Step4 产卡聚合 → promote_to_card（任务3→2） / card_from_discussion（任务7 Step 3）
    Step5 审计健康 → lint_all 五类检测（孤儿/断链/index 漂移/过期/未裁决矛盾）+ 健康分

【不易】人机边界：所有产卡结果状态均为 draft，必须人工确认
（transition → current）才转当前有效——AI 只簿记不裁决。
【不易】降级铁律：任一 LLM 环节（提炼/讨论/提炼讨论字段）失败时降级为
骨架产物，不抛异常；仅"使用错误"（素材/笔记/讨论不存在）抛异常。
"""
from __future__ import annotations

import logging
import time
from dataclasses import asdict
from datetime import date
from enum import Enum
from pathlib import Path

from agent.knowledge.card import CardConflictError, CardStore
from agent.knowledge.discuss import (
    discuss,
    extract_card_insights,
    load_discussion,
)
from agent.knowledge.distill import Note, distill, promote_to_card
from agent.knowledge.ingest import get_knowledge_root, ingest_file
from agent.knowledge.lint import lint_all, score_breakdown
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
        _t0 = time.perf_counter()
        try:
            result = ingest_file(str(src_path), dest_layer=dest_layer,
                                 source_type=source_type,
                                 knowledge_root=str(self.root))
        except Exception as exc:
            logger.error("[workflow] Step1 收集失败 src=%s layer=%s: %s",
                         src_path, dest_layer, exc, exc_info=True)
            raise
        logger.info("[workflow] Step1 收集成功 slug=%s layer=%s 敏感=%s 耗时=%.1fms",
                    result.slug, result.layer, result.sensitive,
                    (time.perf_counter() - _t0) * 1000)
        return result.slug

    # ── Step2: 提炼 ────────────────────────────────────────────

    def run_distill(self, src_path: str | Path) -> Note:
        """调用任务3 distill，返回结构化笔记（LLM 不可用自动降级骨架）。"""
        _t0 = time.perf_counter()
        try:
            note = distill(src_path, llm=self.llm, knowledge_root=str(self.root))
        except Exception as exc:
            logger.error("[workflow] Step2 提炼失败 src=%s: %s", src_path, exc,
                         exc_info=True)
            raise
        logger.info("[workflow] Step2 提炼完成 source=%s slug=%s distilled=%s "
                    "reason=%s 耗时=%.1fms",
                    src_path, note.slug, note.distilled,
                    note.reason or "none", (time.perf_counter() - _t0) * 1000)
        return note

    # ── Step3: 深度讨论 ────────────────────────────────────────

    def run_discuss(self, note_slug: str, question: str) -> str:
        """深度讨论，返回讨论记录文件路径（LLM 不可用降级骨架记录）。"""
        _t0 = time.perf_counter()
        try:
            path = discuss(note_slug, question, llm=self.llm,
                           knowledge_root=str(self.root))
        except FileNotFoundError as exc:
            logger.error("[workflow] Step3 讨论失败（笔记不存在）note_slug=%s: %s",
                         note_slug, exc)
            raise
        logger.info("[workflow] Step3 讨论完成 note_slug=%s → %s 耗时=%.1fms",
                    note_slug, path, (time.perf_counter() - _t0) * 1000)
        return path

    # ── Step4: 产卡 ────────────────────────────────────────────

    def run_card(self, note_slug: str, card_type: str = "concepts") -> str:
        """从已确认（approved）笔记产卡，返回卡片 slug。

        前置：笔记须人工 approve（promote_to_card 强制校验）。
        """
        _t0 = time.perf_counter()
        try:
            card = promote_to_card(note_slug, card_type=card_type,
                                   knowledge_root=str(self.root),
                                   wiki_root=str(self.root / "wiki"))
        except (ValueError, CardConflictError) as exc:
            logger.warning("[workflow] Step4 产卡被拒 note_slug=%s card_type=%s: %s",
                           note_slug, card_type, exc)
            raise
        logger.info("[workflow] Step4 产卡成功 slug=%s（状态 draft，待人工转 current）"
                    "耗时=%.1fms", card.slug, (time.perf_counter() - _t0) * 1000)
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
        """Step5 审计健康：对接任务5 lint_all 五类检测 + 健康分推导。

        核心三步（检测 → 计算 → 报告）分别计时并输出 logger.info，
        便于运行时性能分析定位瓶颈；检测明细与扣分推导逐项打印。
        """
        _t0 = time.perf_counter()
        store = CardStore(self.root / "wiki")
        logger.info(
            "[workflow] Step5 审计启动 wiki=%s index=%s 卡片=%d",
            self.root / "wiki", self.root / "index.md", len(store.list()),
        )

        # ── 步骤1 检测：lint_all 五类检测（孤儿/断链/index漂移/过期/矛盾）──
        _t_detect = time.perf_counter()
        hr = lint_all(store, index_path=str(self.root / "index.md"))
        t_detect = (time.perf_counter() - _t_detect) * 1000

        # ── 步骤2 计算：健康分 + 扣分明细推导 ──
        _t_score = time.perf_counter()
        breakdown = score_breakdown(hr)
        deducted = round(100.0 - hr.health_score, 1)
        t_score = (time.perf_counter() - _t_score) * 1000

        # 检测明细逐项列出（便于运行时排查五类问题）
        logger.info(
            "[workflow] Step5 检测明细 孤儿=%s 断链=%s index漂移=%s 过期=%s "
            "未裁决矛盾=%s 耗时=%.2fms",
            hr.orphans, hr.broken_links, hr.index_drift,
            hr.stale_cards, hr.unresolved_conflicts, t_detect,
        )
        # 健康分推导（与 score_breakdown 同源 _PENALTIES：2/2/2/3/5 分，各类封顶）
        logger.info(
            "[workflow] Step5 健康分计算完成 base=100 扣分合计=%.1f 明细=%s（"
            "孤儿=%d×2(封顶20) 断链=%d×2(封顶20) index漂移=%d×2(封顶10) "
            "过期=%d×3(封顶20) 矛盾=%d×5(封顶30)）耗时=%.2fms",
            deducted, breakdown or "无扣分",
            len(hr.orphans), len(hr.broken_links), len(hr.index_drift),
            len(hr.stale_cards), len(hr.unresolved_conflicts), t_score,
        )

        # ── 步骤3 报告：组装审计结果 dict ──
        _t_report = time.perf_counter()
        report = {
            "total_cards": hr.total_cards,
            "broken_links": hr.broken_links,
            "orphans": hr.orphans,
            "index_drift": hr.index_drift,
            "stale_cards": hr.stale_cards,
            "unresolved_conflicts": hr.unresolved_conflicts,
            "health_score": hr.health_score,
            "score_breakdown": breakdown,
            "ok": not (hr.orphans or hr.broken_links or hr.index_drift
                       or hr.stale_cards or hr.unresolved_conflicts),
            "audited_at": hr.checked_at,
            "suggestions": hr.suggestions,
        }
        t_report = (time.perf_counter() - _t_report) * 1000
        t_total = (time.perf_counter() - _t0) * 1000

        logger.info(
            "[workflow] Step5 审计完成 卡片=%s 健康分=%s 孤儿=%s 断链=%s index漂移=%s "
            "过期=%s 未裁决矛盾=%s ok=%s 总耗时=%.1fms",
            report["total_cards"], report["health_score"],
            len(report["orphans"]), len(report["broken_links"]),
            len(report["index_drift"]), len(report["stale_cards"]),
            len(report["unresolved_conflicts"]), report["ok"], t_total,
        )
        # 三阶段耗时汇总（检测/计算/报告），便于性能分析
        logger.info(
            "[workflow] Step5 耗时明细 检测=%.2fms 计算=%.2fms 报告=%.2fms 总=%.1fms",
            t_detect, t_score, t_report, t_total,
        )
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
