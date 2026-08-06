"""双向链接解析与健康检测（任务2 · Step 2）。

链接语法（AGENTS.md §3.1 双链约定）：
    [[目标]]            → 指向 wiki 内卡片（目标即卡片 slug）
    [[目标|别名]]        → 同上，显示别名
    [[archives/目标]]    → 指向已归档卡片（目标在 knowledge/archives/ 下）

【不易】
- `parse_links` 返回规范化目标列表（去重、保序），目标可为 `slug` 或
  `archives/<slug>`，与 Card.links 字段取值一致。
- `resolve_link` 对失效链接返回 None 而非抛异常（断链容错）。
- `find_orphans` 只统计指向 wiki 的纯 slug 入链；`archives/...` 指向归档，
  不算 wiki 卡片入链。
"""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING, Optional

from agent.knowledge.schema import Card

if TYPE_CHECKING:
    from agent.knowledge.card import CardStore

logger = logging.getLogger(__name__)

# 两个捕获组：目标 / 可选别名（目标不允许含 | [ ] 换行）
_LINK_RE = re.compile(r"\[\[([^\[\]|\n]+)(?:\|([^\[\]\n]*))?\]\]")

ARCHIVES_PREFIX = "archives/"


def parse_links(md_text: str) -> list[str]:
    """解析 [[双链]] 语法，返回目标列表（去重、保序）。

    支持 `[[目标]]` 与 `[[目标|别名]]` 两种写法，提取 `|` 前的目标文本；
    目标可为纯 slug（wiki 卡片）或 `archives/<slug>`（归档卡片）。
    """
    _t0 = time.perf_counter()
    targets: list[str] = []
    for m in _LINK_RE.finditer(md_text or ""):
        target = m.group(1).strip()
        if target and target not in targets:
            targets.append(target)
    logger.info(
        "parse_links: 文本长度=%d 解析出双链目标=%s 耗时=%.2fms",
        len(md_text or ""), targets, (time.perf_counter() - _t0) * 1000,
    )
    return targets


def find_orphans(cards: list[Card]) -> list[str]:
    """找出无任何入链的卡片 slug 列表（孤儿页面）。

    入链 = 其他卡片 `links` 字段中指向 wiki 的纯 slug；
    指向归档的 `archives/...` 链接不算入链。
    """
    incoming: set[str] = set()
    _t0 = time.perf_counter()
    for card in cards:
        for link in card.links:
            if not link.startswith(ARCHIVES_PREFIX):
                incoming.add(link)
    orphans = [c.slug for c in cards if c.slug not in incoming]
    logger.info(
        "find_orphans: 全库卡片=%d 入链目标=%s 孤儿=%s 耗时=%.2fms",
        len(cards), sorted(incoming), orphans,
        (time.perf_counter() - _t0) * 1000,
    )
    return orphans


def resolve_link(slug: str, store: "CardStore") -> Optional[Card]:
    """解析链接；目标不存在返回 None（断链），不抛异常。

    目标可为纯 slug（wiki 卡片）或 `archives/<slug>`（归档卡片），
    解析规则见 CardStore.get。
    """
    _is_archives = slug.startswith(ARCHIVES_PREFIX)
    logger.debug(
        "resolve_link: 解析入口 slug=%r 目标类型=%s",
        slug, "archives" if _is_archives else "wiki",
    )
    _t0 = time.perf_counter()
    try:
        card = store.get(slug)
    except Exception as exc:
        logger.exception(
            "resolve_link: 目标 slug=%r 解析异常（视为断链）: %r", slug, exc,
        )
        return None
    if card is None:
        # 断链详情（复杂引用场景排查）：
        # - archives/ 前缀：仅查 archives/<rest>.md，未命中即断链
        # - 纯 slug：wiki 的 concepts/entities/insights 三目录均未命中，
        #   或目标文件存在但 frontmatter 损坏（损坏卡同样视为不存在）
        hint = (
            f"archives 目录无归档卡 archives/{slug[len(ARCHIVES_PREFIX):]}.md"
            if _is_archives
            else "wiki 的 concepts/entities/insights 均未命中（或卡片文件损坏）"
        )
        logger.info(
            "resolve_link: 断链 → slug=%r 目标类型=%s 原因=%s 耗时=%.2fms",
            slug, "archives" if _is_archives else "wiki", hint,
            (time.perf_counter() - _t0) * 1000,
        )
    else:
        logger.info(
            "resolve_link: 命中 slug=%r status=%s type=%s（目标=%s）耗时=%.2fms",
            slug, card.status, card.type,
            "archives" if _is_archives else "wiki",
            (time.perf_counter() - _t0) * 1000,
        )
    return card


def find_broken_links(cards: list[Card], store: "CardStore") -> list[dict]:
    """找出指向不存在卡片的链接：[{from_slug, to_slug}]。"""
    broken: list[dict] = []
    _t0 = time.perf_counter()
    for card in cards:
        for target in card.links:
            if resolve_link(target, store) is None:
                broken.append({"from_slug": card.slug, "to_slug": target})
    logger.info(
        "find_broken_links: 扫描卡片=%d 断链=%d 明细=%s 耗时=%.2fms",
        len(cards), len(broken), broken,
        (time.perf_counter() - _t0) * 1000,
    )
    return broken


def rewrite_link_targets(
    md_text: str,
    old_target: str,
    new_target: str,
    default_alias: Optional[str] = None,
) -> str:
    """把正文中指向 `old_target` 的双链改写为 `new_target`（Archive 重链用）。

    - `[[old]]`       → `[[new|default_alias]]`（default_alias 缺省用 old）
    - `[[old|别名]]`  → `[[new|别名]]`（保留原别名）
    - 其余链接原样保留。
    """
    def _repl(m: re.Match) -> str:
        target, alias = m.group(1).strip(), m.group(2)
        if target != old_target:
            return m.group(0)
        if alias and alias.strip():
            return f"[[{new_target}|{alias.strip()}]]"
        display = default_alias if default_alias else old_target
        return f"[[{new_target}|{display}]]"

    return _LINK_RE.sub(_repl, md_text or "")
