"""知识库健康巡检：产出《知识库健康报告》（任务5 · 治理层）。

五类检测项（AGENTS.md §6 质量守卫分层）：
| 检测项 | 规则 | 数据来源 |
|---|---|---|
| 孤儿页面 | 无任何入链 | links.find_orphans |
| 断链 | links 指向不存在卡片 | links.find_broken_links |
| index 漂移 | 卡片集合与 index.md 条目集合 diff | index.read_index_slugs |
| 过期声明 | status=current 且 date 距今 > stale_days | Card frontmatter |
| 未裁决矛盾 | contradictions 中 status != resolved | Card schema |

健康分算法（参考 agent/memory/reviewer.py 扣分模式，未修改该模块）：
    base = 100；孤儿 2/张（封顶 20）、断链 2/条（封顶 20）、
    index 漂移 2/张（封顶 10）、过期声明 3/条（封顶 20）、
    未裁决矛盾 5/条（封顶 30）；health_score = max(0, round(base - 扣分, 1))。

【不易】
- 只读巡检，不修改任何卡片/索引/日志文件（可安全定时执行）。
- 复用任务2 links/index 能力，不重复实现解析逻辑。
- 检测分支均输出详细 logger 日志（含明细与耗时），便于运行时排查。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Optional

from agent.knowledge.index import read_index_slugs
from agent.knowledge.links import find_broken_links, find_orphans

logger = logging.getLogger(__name__)

# 扣分规则：{类别: (每项扣分, 该类封顶)}
_PENALTIES = {
    "orphans": (2, 20),
    "broken_links": (2, 20),
    "index_drift": (2, 10),
    "stale": (3, 20),
    "conflicts": (5, 30),
}


@dataclass
class HealthReport:
    """全量巡检结果（健康分 + 五类问题明细 + 建议）。"""

    checked_at: str
    total_cards: int
    orphans: list[str] = field(default_factory=list)              # 孤儿卡片（无入链）
    broken_links: list[dict] = field(default_factory=list)        # [{from_slug, to_slug}]
    index_drift: list[str] = field(default_factory=list)          # index.md 与实际不同步的卡片
    stale_cards: list[dict] = field(default_factory=list)         # [{slug, days_unaccessed}] current 超期未访问
    unresolved_conflicts: list[dict] = field(default_factory=list)  # [{slug, target_slug}] 矛盾未裁决
    health_score: float = 100.0
    suggestions: list[str] = field(default_factory=list)


def compute_health_score(report: HealthReport) -> float:
    """健康分 = 100 - Σ(各类 每项扣分 × 数量，按类封顶)；封底 0.0。

    参考 agent/memory/reviewer.py 扣分模式（未修改该模块）。
    """
    score = 100.0
    counts = {
        "orphans": len(report.orphans),
        "broken_links": len(report.broken_links),
        "index_drift": len(report.index_drift),
        "stale": len(report.stale_cards),
        "conflicts": len(report.unresolved_conflicts),
    }
    deductions: list[str] = []
    for kind, (per, cap) in _PENALTIES.items():
        n = counts[kind]
        if n == 0:
            continue
        raw = per * n
        applied = min(raw, cap)
        score -= applied
        deductions.append(f"{kind}={n}项(扣{applied}{'(封顶)' if raw > cap else ''})")
    if deductions:
        logger.info(
            "compute_health_score: base=100 扣分明细=[%s] 得分=%.1f",
            ", ".join(deductions), max(0.0, score),
        )
    else:
        logger.info("compute_health_score: 无扣分 健康分=100.0")
    return round(max(0.0, score), 1)


def _find_stale_cards(cards, stale_days: int, today: date) -> list[dict]:
    """过期声明：status=current 且 date 距今天数 > stale_days。"""
    stale: list[dict] = []
    for card in cards:
        if card.status != "current":
            continue
        try:
            card_date = date.fromisoformat(card.date)
        except (ValueError, TypeError):
            logger.debug("过期检测跳过: slug=%s date=%r 非 ISO 日期", card.slug, card.date)
            continue
        days = (today - card_date).days
        if days > stale_days:
            stale.append({"slug": card.slug, "days_unaccessed": days})
    if stale:
        logger.info(
            "过期声明: 命中 %d 条（status=current 且 date 距今 > %d 天）明细=%s",
            len(stale), stale_days, stale,
        )
    else:
        logger.info("过期声明: 无命中（阈值=%d 天）", stale_days)
    return stale


def _find_unresolved_conflicts(cards) -> list[dict]:
    """未裁决矛盾：contradictions 中 status != 'resolved'（含 conflict/reviewed）。

    输出键与 conflict.list_unresolved 对齐：{source_slug, target_slug, summary}。
    """
    unresolved: list[dict] = []
    for card in cards:
        for item in card.contradictions or []:
            if item.get("status") != "resolved":
                unresolved.append({
                    "source_slug": card.slug,
                    "target_slug": item.get("target_slug", ""),
                    "summary": item.get("summary", ""),
                })
    if unresolved:
        logger.info(
            "未裁决矛盾: 命中 %d 条（status != resolved）明细=%s", len(unresolved), unresolved,
        )
    else:
        logger.info("未裁决矛盾: 无命中")
    return unresolved


def _collect_suggestions(report: HealthReport, stale_days: int) -> list[str]:
    """按问题生成处置建议（人类/AI 后续行动指引）。"""
    suggestions: list[str] = []
    if report.orphans:
        suggestions.append(f"发现 {len(report.orphans)} 张孤儿卡片（无入链），建议补充引用或归档")
    if report.broken_links:
        suggestions.append(f"发现 {len(report.broken_links)} 条断链，建议修复引用或归档目标卡片")
    if report.index_drift:
        suggestions.append(f"index.md 与卡片不同步 {len(report.index_drift)} 张，建议执行 rebuild_index 全量重建")
    if report.stale_cards:
        suggestions.append(f"发现 {len(report.stale_cards)} 条过期声明（超过 {stale_days} 天未访问），建议人工审查降级")
    if report.unresolved_conflicts:
        suggestions.append(f"存在 {len(report.unresolved_conflicts)} 条未裁决矛盾，请人工调用 resolve_conflict 裁决")
    if not suggestions:
        suggestions.append("知识库状态良好，无需处理")
    return suggestions


def lint_all(
    card_store,
    index_path: str | Path = "knowledge/index.md",
    stale_days: int = 90,
    parallel_read: bool = False,
    light_read: bool = True,
) -> HealthReport:
    """全量巡检：孤儿/断链/index 漂移/过期声明/未裁决矛盾。

    只读巡检：不修改任何卡片、索引或日志文件。
    parallel_read=True 时卡片读盘走线程池并发（IO 密集提速；检测结果与
    串行完全一致，仅读盘加速）。
    light_read=True（默认）时卡片加载走 CardStore.list_light() 轻量检测
    视图（P0 内存优化：只解析检测六字段，不驻留正文/insight）；store 无
    list_light 时自动回退 list()（第三方/测试替身兼容，检测语义不变）。
    """
    _t0 = time.perf_counter()
    if light_read:
        # 优先轻量检测视图（P0 内存优化）；store 无 list_light（第三方/替身）时回退 list()
        getter = getattr(card_store, "list_light", None)
        if getter is not None:
            cards = list(getter(parallel=parallel_read))
        else:
            cards = list(card_store.list(parallel=parallel_read))
    else:
        cards = list(card_store.list(parallel=parallel_read))
    report = HealthReport(
        checked_at=date.today().isoformat(),
        total_cards=len(cards),
    )
    logger.info(
        "lint_all: 巡检开始 total_cards=%d index_path=%s stale_days=%d",
        len(cards), index_path, stale_days,
    )

    # 1. 孤儿页面（无任何入链）
    _t1 = time.perf_counter()
    report.orphans = find_orphans(cards)
    t_orphans = (time.perf_counter() - _t1) * 1000
    if report.orphans:
        logger.warning(
            "lint_all[孤儿]: 发现 %d 张无入链卡片 %s（建议补充引用或归档）耗时=%.2fms",
            len(report.orphans), report.orphans, t_orphans,
        )
    else:
        logger.info(
            "lint_all[孤儿]: 无命中（所有卡片均有入链）耗时=%.2fms", t_orphans,
        )

    # 2. 断链（links 指向不存在卡片）
    _t2 = time.perf_counter()
    report.broken_links = find_broken_links(cards, card_store)
    t_broken = (time.perf_counter() - _t2) * 1000
    if report.broken_links:
        logger.warning(
            "lint_all[断链]: 发现 %d 条指向不存在卡片的链接 %s 耗时=%.2fms",
            len(report.broken_links), report.broken_links, t_broken,
        )
    else:
        logger.info(
            "lint_all[断链]: 无命中（全部链接均可达）耗时=%.2fms", t_broken,
        )

    # 3. index 漂移（卡片集合与 index.md 条目集合 diff）
    _t3 = time.perf_counter()
    index_slugs = read_index_slugs(index_path)
    card_slugs = {c.slug for c in cards}
    report.index_drift = sorted(card_slugs ^ set(index_slugs))
    t_drift = (time.perf_counter() - _t3) * 1000
    if report.index_drift:
        logger.warning(
            "lint_all[index 漂移]: 卡片集合=%d 条目集合=%d 差异=%s（对称差；缺条目/幽灵条目均计入）耗时=%.2fms",
            len(card_slugs), len(index_slugs), report.index_drift, t_drift,
        )
    else:
        logger.info(
            "lint_all[index 漂移]: 无命中（index.md 与卡片集合一致）耗时=%.2fms", t_drift,
        )

    # 4. 过期声明（status=current 且 date 超期）
    _t4 = time.perf_counter()
    report.stale_cards = _find_stale_cards(cards, stale_days, date.today())
    t_stale = (time.perf_counter() - _t4) * 1000
    if report.stale_cards:
        logger.warning(
            "lint_all[过期声明]: 发现 %d 条 current 状态超期未访问（>%d 天）%s 耗时=%.2fms",
            len(report.stale_cards), stale_days, report.stale_cards, t_stale,
        )
    else:
        logger.info(
            "lint_all[过期声明]: 无命中（阈值=%d 天）耗时=%.2fms", stale_days, t_stale,
        )

    # 5. 未裁决矛盾（contradictions status != resolved）
    _t5 = time.perf_counter()
    report.unresolved_conflicts = _find_unresolved_conflicts(cards)
    t_conflicts = (time.perf_counter() - _t5) * 1000
    if report.unresolved_conflicts:
        logger.warning(
            "lint_all[矛盾]: 发现 %d 条未裁决矛盾 %s 耗时=%.2fms",
            len(report.unresolved_conflicts), report.unresolved_conflicts, t_conflicts,
        )
    else:
        logger.info("lint_all[矛盾]: 无命中 耗时=%.2fms", t_conflicts)

    t_total = (time.perf_counter() - _t0) * 1000
    report.health_score = compute_health_score(report)
    report.suggestions = _collect_suggestions(report, stale_days)
    logger.info("lint_all[建议]: 生成 %d 条处置建议", len(report.suggestions))
    # 统一耗时汇总：五步明细 + 总耗时，便于 grep 定位性能瓶颈
    logger.info(
        "lint_all[耗时汇总]: orphans=%.2fms broken=%.2fms drift=%.2fms stale=%.2fms "
        "conflicts=%.2fms total=%.2fms",
        t_orphans, t_broken, t_drift, t_stale, t_conflicts, t_total,
    )
    logger.info(
        "lint_all: 巡检完成 total=%d score=%.1f orphans=%d broken=%d drift=%d stale=%d conflicts=%d 总耗时=%.2fms",
        report.total_cards, report.health_score,
        len(report.orphans), len(report.broken_links), len(report.index_drift),
        len(report.stale_cards), len(report.unresolved_conflicts),
        t_total,
    )
    return report


def render_report(report: HealthReport, stale_days: int = 90) -> str:
    """渲染《知识库健康报告》Markdown（六节 + 健康分 + 建议）。"""
    lines = [
        "# 知识库健康报告",
        "",
        f"- 巡检时间: {report.checked_at}",
        f"- 卡片总数: {report.total_cards}",
        f"- 健康分: {report.health_score} / 100",
        f"- 过期阈值: {stale_days} 天",
        "",
        "## 一、孤儿卡片",
        "",
    ]
    if report.orphans:
        lines.extend(f"- {slug}" for slug in report.orphans)
    else:
        lines.append("- 无")
    lines += ["", "## 二、断链", ""]
    if report.broken_links:
        lines.extend(
            f"- from_slug={b['from_slug']}，to_slug={b['to_slug']}" for b in report.broken_links
        )
    else:
        lines.append("- 无")
    lines += ["", "## 三、index 漂移", ""]
    if report.index_drift:
        lines.extend(f"- {slug}" for slug in report.index_drift)
    else:
        lines.append("- 无")
    lines += ["", "## 四、过期声明", ""]
    if report.stale_cards:
        lines.extend(
            f"- slug={s['slug']}，days_unaccessed={s['days_unaccessed']}" for s in report.stale_cards
        )
    else:
        lines.append("- 无")
    lines += ["", "## 五、未裁决矛盾", ""]
    if report.unresolved_conflicts:
        lines.extend(
            f"- source_slug={u['source_slug']}，target_slug={u['target_slug']}，summary={u.get('summary', '')}"
            for u in report.unresolved_conflicts
        )
    else:
        lines.append("- 无")
    lines += ["", "## 六、建议", ""]
    lines.extend(f"- {s}" for s in report.suggestions)
    return "\n".join(lines) + "\n"


def _breakdown_from_counts(counts: dict[str, int]) -> dict[str, int]:
    """按 _PENALTIES 推导扣分明细：{类别: 实际扣分}，只含命中项。

    与 compute_health_score 同源，保证「日志推导」与「JSON 导出」扣分一致。
    """
    breakdown: dict[str, int] = {}
    for kind, (per, cap) in _PENALTIES.items():
        n = counts.get(kind, 0)
        if n:
            breakdown[kind] = min(per * n, cap)
    return breakdown


def score_breakdown(report: HealthReport) -> dict[str, int]:
    """从 HealthReport 五类数量推导扣分明细（供 workflow.run_audit 日志/报告）。"""
    counts = {
        "orphans": len(report.orphans),
        "broken_links": len(report.broken_links),
        "index_drift": len(report.index_drift),
        "stale": len(report.stale_cards),
        "conflicts": len(report.unresolved_conflicts),
    }
    return _breakdown_from_counts(counts)


def report_to_json(report, store=None) -> dict:
    """结构化健康报告 JSON（兼容 HealthReport / dict 双输入）。

    输入：
        report  HealthReport（lint_all / run_knowledge_audit 返回）
                | dict（workflow.run_audit 返回，字段已含 score_breakdown）
        store   CardStore 可选；提供时附加 cards[]（slug/status/type/contradictions）。
    输出键（自动化审计契约）：
        audited_at / total_cards / health_score / ok / score_breakdown
        broken_links / orphans / index_drift / stale_cards
        unresolved_conflicts / suggestions / cards[]（store 存在时）
    """
    if isinstance(report, HealthReport):
        data: dict = {
            "audited_at": report.checked_at,
            "total_cards": report.total_cards,
            "health_score": report.health_score,
            "ok": not (report.orphans or report.broken_links
                       or report.index_drift or report.stale_cards
                       or report.unresolved_conflicts),
            "score_breakdown": score_breakdown(report),
            "broken_links": report.broken_links,
            "orphans": report.orphans,
            "index_drift": report.index_drift,
            "stale_cards": report.stale_cards,
            "unresolved_conflicts": report.unresolved_conflicts,
            "suggestions": report.suggestions,
        }
    else:  # workflow.run_audit 返回的 dict（字段已齐）
        data = dict(report)
    if store is not None:
        data["cards"] = [
            {
                "slug": c.slug,
                "status": c.status,
                "type": c.type,
                "contradictions": c.contradictions or [],
            }
            for c in store.list()
        ]
    return data
