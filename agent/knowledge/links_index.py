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

【变易】索引文件可删，读侧（card._has_incoming_links）缺失时回退全库扫描。
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
        return {}
    refs: dict[str, list[str]] = {}
    cur: Optional[str] = None
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


def rebuild_links_index(wiki_root: str | Path, index_path: str | Path) -> int:
    """全量重建入链索引：一次 list() 构建全部入链关系，返回被引用 slug 数。"""
    from agent.knowledge.card import CardStore  # importlib 惰性导入（无 AST 循环边）

    store = CardStore(wiki_root)
    _t0 = time.perf_counter()
    refs: dict[str, list[str]] = {}
    for card in store.list():
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
    elif target_slug in refs and ref_slug in refs[target_slug]:
        refs[target_slug].remove(ref_slug)
        changed = 1
        if not refs[target_slug]:
            del refs[target_slug]
    if changed:
        _atomic_write(path, _render(refs))
    logger.info(
        "update_links_delta: target=%s ref=%s add=%s 变更=%d 耗时=%.2fms",
        target_slug, ref_slug, add, changed,
        (time.perf_counter() - _t0) * 1000,
    )
    return changed
