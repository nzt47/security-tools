"""知识库健康巡检：产出《知识库健康报告》（任务5 · 治理层）。

检测五类问题（数据来源均为任务2 既有能力）：
    孤儿卡片    — find_orphans（无任何 wiki 入链）
    断链        — find_broken_links（links 指向不存在卡片）
    index 漂移  — 卡片集合与 index.md 条目集合 diff
    过期声明    — status=current 且 date 距今天数超过 stale_days
    未裁决矛盾  — contradictions 中 status != resolved

健康分算法参考 `agent/memory/reviewer.py::_calculate_health_score`
扣分模式（base=100，各扣分项封顶，最低 0）。
【不易】约束：不修改 agent/memory/reviewer.py，仅复用其扣分模式。
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

from agent.knowledge.index import read_index_slugs
from agent.knowledge.links import find_broken_links, find_orphans

logger = logging.getLogger(__name__)


@dataclass
class HealthReport:
    """知识库健康巡检结果（《知识库健康报告》数据模型）。"""

    checked_at: str
    total_cards: int
    orphans: list[str] = field(default_factory=list)  # 孤儿卡片（无入链）
    broken_links: list[dict] = field(default_factory=list)  # [{from_slug, to_slug}]
    index_drift: list[str] = field(default_factory=list)  # index.md 与实际不同步的卡片
    stale_cards: list[dict] = field(default_factory=list)  # [{slug, days_unaccessed}] Current 超期未访问
    unresolved_conflicts: list[dict] = field(default_factory=list)  # [{source_slug, target_slug, summary}] 矛盾未裁决
    health_score: float = 100.0
    suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """序列化为 API 响应 dict（asdict 展开嵌套结构）。

        【不易】字段名即 API 契约：路由层 report.to_dict() 与测试断言
        （total_cards/health_score/orphans/suggestions）一一对应，不得改名。
        """
        return asdict(self)


# 扣分规则：{检测项: (每单位扣分, 封顶)}
# 各封顶合计 20+20+10+20+30=100，天然保证 score >= 0。
_PENALTIES = {
    "orphans": (2, 20),
    "broken_links": (2, 20),
    "index_drift": (2, 10),
    "stale": (3, 20),
    "conflicts": (5, 30),
}


def _days_since(date_str: str, today: Optional[date] = None) -> int:
    """`YYYY-MM-DD` 距 today 的天数；解析失败返回 -1（不视为过期）。"""
    today = today or date.today()
    try:
        d = date.fromisoformat(date_str)
    except (ValueError, TypeError):
        return -1
    return (today - d).days


def compute_health_score(report: HealthReport) -> float:
    """按扣分规则计算健康分（base=100，各项封顶，最低 0）。"""
    score = 100.0
    score -= min(len(report.orphans) * _PENALTIES["orphans"][0],
                 _PENALTIES["orphans"][1])
    score -= min(len(report.broken_links) * _PENALTIES["broken_links"][0],
                 _PENALTIES["broken_links"][1])
    score -= min(len(report.index_drift) * _PENALTIES["index_drift"][0],
                 _PENALTIES["index_drift"][1])
    score -= min(len(report.stale_cards) * _PENALTIES["stale"][0],
                 _PENALTIES["stale"][1])
    score -= min(len(report.unresolved_conflicts) * _PENALTIES["conflicts"][0],
                 _PENALTIES["conflicts"][1])
    return max(0.0, round(score, 1))


def _find_stale_cards(cards, stale_days: int) -> list[dict]:
    """找出 status=current 且 date 超期未访问的卡片。"""
    stale: list[dict] = []
    for card in cards:
        if card.status != "current":
            continue
        days = _days_since(card.date)
        if days > stale_days:
            stale.append({"slug": card.slug, "days_unaccessed": days})
    return stale


def _find_unresolved_conflicts(cards) -> list[dict]:
    """找出 contradictions 中 status != resolved 的矛盾条目。"""
    out: list[dict] = []
    for card in cards:
        for item in card.contradictions or []:
            if item.get("status") != "resolved":
                out.append({
                    "source_slug": card.slug,
                    "target_slug": item.get("target_slug", ""),
                    "summary": item.get("summary", ""),
                })
    return out


def _make_suggestions(report: HealthReport, stale_days: int) -> list[str]:
    """根据巡检结果生成可执行建议。"""
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
) -> HealthReport:
    """全量巡检：孤儿/断链/index 漂移/过期声明/未裁决矛盾。

    Args:
        card_store: 任务2 CardStore 实例（鸭子类型：仅调用 `.list()`）。
        index_path: index.md 路径（默认 knowledge/index.md，与布局一致）。
        stale_days: 过期阈值（天），status=current 且 date 距今天数超阈值即过期。
    """
    checked_at = date.today().isoformat()
    cards = card_store.list()
    report = HealthReport(checked_at=checked_at, total_cards=len(cards))

    report.orphans = find_orphans(cards)
    report.broken_links = find_broken_links(cards, card_store)
    report.index_drift = sorted(
        {c.slug for c in cards} ^ set(read_index_slugs(index_path))
    )
    report.stale_cards = _find_stale_cards(cards, stale_days)
    report.unresolved_conflicts = _find_unresolved_conflicts(cards)
    report.health_score = compute_health_score(report)
    report.suggestions = _make_suggestions(report, stale_days)

    logger.info(
        "lint_all: total=%d score=%.1f orphans=%d broken=%d drift=%d stale=%d conflicts=%d",
        report.total_cards, report.health_score,
        len(report.orphans), len(report.broken_links),
        len(report.index_drift), len(report.stale_cards),
        len(report.unresolved_conflicts),
    )
    return report


def render_report(report: HealthReport, stale_days: int = 90) -> str:
    """渲染《知识库健康报告》Markdown 文本（定时审计落盘用）。"""
    lines = [
        "# 知识库健康报告",
        "",
        f"- 巡检时间: {report.checked_at}",
        f"- 卡片总数: {report.total_cards}",
        f"- 健康分: {report.health_score:.1f} / 100",
        f"- 过期阈值: {stale_days} 天",
        "",
    ]

    def _section(title: str, rows: list) -> None:
        lines.append(f"## {title}")
        lines.append("")
        if not rows:
            lines.append("- 无")
            lines.append("")
            return
        for row in rows:
            if isinstance(row, dict):
                detail = "，".join(f"{k}={v}" for k, v in row.items())
                lines.append(f"- {detail}")
            else:
                lines.append(f"- {row}")
        lines.append("")

    _section("一、孤儿卡片", report.orphans)
    _section("二、断链", report.broken_links)
    _section("三、index 漂移", report.index_drift)
    _section("四、过期声明", report.stale_cards)
    _section("五、未裁决矛盾", report.unresolved_conflicts)

    lines.append("## 六、建议")
    lines.append("")
    for s in report.suggestions:
        lines.append(f"- {s}")
    lines.append("")
    return "\n".join(lines)
