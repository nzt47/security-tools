"""入链索引 index_links.md 维护（P0-2 入链索引优化）。

文件格式（与 index.md 同风格，AI 自动维护）：
    # 知识库入链索引
    > 此文件由 AI 自动维护，请勿手动修改。更新时间: YYYY-MM-DD

    ## <被引用slug>
    - [[<引用方slug>]]
    ...

【不易】一致性不变量（由 tests/unit/test_links_index.py 锁定）：
- 全量（rebuild_links_index）与增量（update_links_delta 叠加）结果一致；
- 只记录指向 wiki 的纯 slug 入链，archives/ 前缀不入表（对齐 find_orphans）；
- 引用清空后对应段移除（不残留空段）；同引用幂等（重复 add/remove 无变更）。

【变易】索引回退与容错降级（索引文件可删，无数据丢失，随时 rebuild 重建）：
- 读侧（card._has_incoming_links）：优先查表 O(1)；索引缺失/解析失败时
  回退全库扫描（降级铁律：行为不劣于优化前，与原 O(N) 全扫一致）。
- 写侧（card.delete_many）：优先一次解析本索引构建入链映射（O(M)），
  缺失/损坏时回退全库扫描；集合内互引放行判定语义不变。
- 扫描路径内存 O(N) 线性、测完即释放（3000 卡 ≈5.6 MB），无长驻缓存；
  10 万卡量级回退峰值约 180 MB，如需可改造为流式扫描（逐文件解析）。
- 损坏卡片在扫描时跳过（list() 语义）；被删/被改卡 links 读取容错为空。
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import date
from pathlib import Path
from typing import Optional

from agent.knowledge.links import ARCHIVES_PREFIX

logger = logging.getLogger(__name__)

_HEADER = "# 知识库入链索引"
_TIME_PREFIX = "> 此文件由 AI 自动维护，请勿手动修改。更新时间: "
_SECTION_RE = re.compile(r"^## (.+)$")
_ENTRY_RE = re.compile(r"^\- \[\[([^\]]+)\]\]$")


def _atomic_write(path: Path, text: str) -> None:
    """同目录临时文件 + os.replace，保证原子性。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def read_links_index(index_path: str | Path) -> dict[str, list[str]]:
    """解析入链索引 → {被引用 slug: [引用方 slug, ...]}（保序、去重）。"""
    path = Path(index_path)
    if not path.exists():
        logger.debug("入链索引缺失（调用方将回退全库扫描）: %s", path)
        return {}
    _t0 = time.perf_counter()
    refs: dict[str, list[str]] = {}
    cur: Optional[str] = None
    unknown = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _SECTION_RE.match(line)
        if m:
            cur = m.group(1).strip()
            refs.setdefault(cur, [])
            continue
        e = _ENTRY_RE.match(line)
        if e and cur is not None:
            ref = e.group(1).strip()
            if ref not in refs[cur]:
                refs[cur].append(ref)
        elif line.strip() and not line.startswith(("#", ">")):
            unknown += 1  # 非空且非注释/非条目行：疑似污染，计入排查日志
    if unknown:
        logger.warning(
            "入链索引存在 %d 行无法识别（建议 rebuild_links_index 重整）: %s",
            unknown, path,
        )
    logger.info(
        "read_links_index: 解析完成 index_path=%s 被引用段=%d 引用方=%d 耗时=%.2fms",
        path, len(refs), sum(len(v) for v in refs.values()),
        (time.perf_counter() - _t0) * 1000,
    )
    return refs


def _render(refs: dict[str, list[str]]) -> str:
    """渲染为文件文本：段与引用方均按 slug 字典序（结果可复现）。"""
    lines = [_HEADER, f"{_TIME_PREFIX}{date.today().isoformat()}", ""]
    for target in sorted(refs):
        lines.append(f"## {target}")
        for ref in sorted(set(refs[target])):
            lines.append(f"- [[{ref}]]")
        lines.append("")
    return "\n".join(lines)


def rebuild_links_index(cards, index_path: str | Path) -> int:
    """全量重建入链索引：遍历传入的卡片构建全部入链关系，返回被引用 slug 数。

    cards: 可迭代对象，每项含 .links（list[str]）与 .slug（str）——鸭子类型，
    由调用方构造（如 CardStore(wiki_root).list()）。本模块不依赖 card 模块，
    静态依赖单向（card → links_index），无循环边。
    """
    _t0 = time.perf_counter()
    refs: dict[str, list[str]] = {}
    for card in cards:
        for link in card.links:
            if link.startswith(ARCHIVES_PREFIX):
                continue
            refs.setdefault(link, []).append(card.slug)
    _atomic_write(Path(index_path), _render(refs))
    logger.info(
        "rebuild_links_index: 全量重建完成 index_path=%s 被引用=%d 耗时=%.2fms",
        index_path, len(refs), (time.perf_counter() - _t0) * 1000,
    )
    return len(refs)


def update_links_delta(
    target_slug: str,
    ref_slug: str,
    index_path: str | Path,
    *,
    add: bool,
) -> int:
    """增量更新单条入链关系（`ref_slug` 引用 `target_slug`），返回变更数。

    - add=True：添加引用；已存在 → 0（幂等，不产生重复行）。
    - add=False：移除引用；不存在 → 0（幂等，且不创建文件）。
    - 引用清空后对应段移除（不残留空段）。
    """
    path = Path(index_path)
    _t0 = time.perf_counter()
    refs = read_links_index(path)
    changed = 0
    if add:
        if target_slug not in refs:
            refs[target_slug] = []
        if ref_slug not in refs[target_slug]:
            refs[target_slug].append(ref_slug)
            changed = 1
        else:
            logger.debug(
                "update_links_delta: add 幂等无变更 target=%s ref=%s", target_slug, ref_slug,
            )
    elif target_slug in refs and ref_slug in refs[target_slug]:
        refs[target_slug].remove(ref_slug)
        changed = 1
        if not refs[target_slug]:
            del refs[target_slug]
            logger.debug(
                "update_links_delta: 引用清空移除空段 target=%s", target_slug,
            )
    else:
        logger.debug(
            "update_links_delta: remove 幂等无变更 target=%s ref=%s（不创建文件）",
            target_slug, ref_slug,
        )
    if changed:
        _atomic_write(path, _render(refs))
    logger.info(
        "update_links_delta: target=%s ref=%s add=%s 变更=%d 耗时=%.2fms",
        target_slug, ref_slug, add, changed,
        (time.perf_counter() - _t0) * 1000,
    )
    return changed
