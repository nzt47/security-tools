"""index.md 全量/增量维护（任务2 · Step 4）。

index.md 格式（任务2 约定）：
    # 知识库全局索引
    > 此文件由 AI 自动维护，请勿手动修改。更新时间: YYYY-MM-DD

    ## 概念 (Concepts)
    - [[slug]] `status` — 一句话摘要

    ## 实体 (Entities)
    ...

    ## 洞察 (Insights)
    ...

【不易】一致性要求：`rebuild_index` 与逐卡片 `update_index_delta` 叠加执行后
结果一致（同一排序键 slug、同一 section 标题、同一时间戳行）。
【变易】`update_index_delta(slug, card=None, ...)`：card 为 None 表示移除该
slug 条目（delete / archive 场景）。
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import date
from pathlib import Path
from typing import Optional

from agent.knowledge.schema import Card

logger = logging.getLogger(__name__)


def _get_store(wiki_root: str | Path):
    """惰性构造 CardStore（importlib 动态导入，避免 card↔index 循环依赖）。

    arch_rules 按 AST Import/ImportFrom 节点统计依赖边（含函数内/TYPE_CHECKING），
    直接写 import 语句会形成 card↔index 循环边；importlib 动态导入无对应
    AST 节点，依赖图不产生 `index → card` 边。首次调用时才真正导入。
    """
    from importlib import import_module

    store_cls = import_module("agent.knowledge.card").CardStore
    return store_cls(wiki_root)


_HEADER = "# 知识库全局索引"
_TIME_PREFIX = "> 此文件由 AI 自动维护，请勿手动修改。更新时间: "

# 分类 → section 标题（顺序即输出顺序）
_SECTIONS = {
    "concepts": "## 概念 (Concepts)",
    "entities": "## 实体 (Entities)",
    "insights": "## 洞察 (Insights)",
}

_ENTRY_RE = re.compile(r"^\- \[\[([^\]]+)\]\]")


def _atomic_write(path: Path, text: str) -> None:
    """同目录临时文件 + os.replace，保证原子性。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _entry_line(card: Card) -> str:
    """条目行：`- [[slug]] `status` — insight`（insight 换行压平防破坏行结构）。"""
    insight = " ".join(card.insight.split())
    return f"- [[{card.slug}]] `{card.status}` — {insight}"


def _is_entry_for(line: str, slug: str) -> bool:
    """判断行是否为 `- [[slug]] ...` 条目（前缀精确匹配，防 slug 前缀误伤）。"""
    return line.startswith(f"- [[{slug}]] ")


def _fresh_lines() -> list[str]:
    """新建 index 的骨架（含时间戳行与三个空 section 头）。"""
    lines = [_HEADER, f"{_TIME_PREFIX}{date.today().isoformat()}", ""]
    for heading in _SECTIONS.values():
        lines.extend([heading, ""])
    return lines


def rebuild_index(wiki_root: str | Path, index_path: str | Path) -> int:
    """全量重建 index.md：按 Concepts/Entities/Insights 分类，含一句话摘要 +
    状态角标。返回索引卡片数（wiki 全部卡片）。"""
    store = _get_store(wiki_root)  # importlib 惰性导入（无 AST import 边）
    cards = store.list()
    _t0 = time.perf_counter()

    lines = [_HEADER, f"{_TIME_PREFIX}{date.today().isoformat()}", ""]
    for type_key, heading in _SECTIONS.items():
        lines.append(heading)
        entries = sorted(
            (c for c in cards if c.type == type_key), key=lambda c: c.slug
        )
        lines.extend(_entry_line(c) for c in entries)
        lines.append("")
    _atomic_write(Path(index_path), "\n".join(lines))
    logger.info(
        "rebuild_index: 全量重建完成 index_path=%s 索引卡片数=%d 耗时=%.2fms",
        index_path, len(cards), (time.perf_counter() - _t0) * 1000,
    )
    return len(cards)


def update_index_delta(
    slug: str, card: Optional[Card], index_path: str | Path
) -> bool:
    """增量更新单个卡片对应的 index 条目（不重扫全库）。

    - `card` 非 None：插入/更新该 slug 条目（按 slug 字典序定位插入点）。
    - `card` 为 None：移除该 slug 条目（删除 / Archive 归档）。
    - 返回是否发生了写盘变更。
    """
    path = Path(index_path)
    _t0 = time.perf_counter()
    logger.info(
        "update_index_delta: 操作开始 slug=%s 动作=%s index_path=%s",
        slug,
        "移除条目" if card is None else f"写入(type={card.type})",
        index_path,
    )
    lines = (
        path.read_text(encoding="utf-8").splitlines()
        if path.exists()
        else _fresh_lines()
    )

    # 1. 刷新时间戳行
    lines = [
        f"{_TIME_PREFIX}{date.today().isoformat()}"
        if line.startswith(_TIME_PREFIX)
        else line
        for line in lines
    ]

    if card is None:
        filtered = [line for line in lines if not _is_entry_for(line, slug)]
        if len(filtered) == len(lines):
            logger.info("update_index_delta: 移除条目 slug=%s 不存在，无变更", slug)
            return False  # 条目本就不存在，无变更
        _atomic_write(path, "\n".join(filtered))
        logger.info(
            "update_index_delta: 移除条目 slug=%s 成功 耗时=%.2fms",
            slug, (time.perf_counter() - _t0) * 1000,
        )
        return True

    # 2. 未知类型不进索引
    heading = _SECTIONS.get(card.type)
    if heading is None:
        logger.warning("update_index_delta: slug=%s 未知类型 %r 不进索引", slug, card.type)
        return False

    # 3. 已有条目 → 先移除旧条目、再按目标 section 重新定位插入。
    #    type 变更（concepts → entities 等）时条目必须迁移到新 section，
    #    否则残留旧 section 会造成「增量叠加 ≠ 全量重建」的格式漂移。
    entry = _entry_line(card)
    existed = any(_is_entry_for(line, card.slug) for line in lines)
    if existed:
        lines = [line for line in lines if not _is_entry_for(line, card.slug)]
        logger.info("update_index_delta: 移除旧条目 slug=%s 后重新定位", card.slug)
    if heading not in lines:  # 骨架缺失时补 section 头（防御手工删改）
        lines = _fresh_lines()
    idx = lines.index(heading) + 1
    # 条目紧贴 heading：遇到空行（section 结束标记）即停止，避免
    # 插到下一个 section 头之前造成「空行在条目前」的格式漂移
    insert_at = idx
    while idx < len(lines):
        line = lines[idx]
        if line == "":
            break
        m = _ENTRY_RE.match(line)
        if m and m.group(1) > card.slug:
            break
        if line.startswith("## "):
            break
        insert_at = idx + 1
        idx += 1
    lines.insert(insert_at, entry)
    logger.info(
        "update_index_delta: 插入条目 slug=%s 到 %s 第 %d 行",
        card.slug, heading, insert_at,
    )

    _atomic_write(path, "\n".join(lines))
    logger.info(
        "update_index_delta: 完成 slug=%s 耗时=%.2fms",
        slug, (time.perf_counter() - _t0) * 1000,
    )
    return True
