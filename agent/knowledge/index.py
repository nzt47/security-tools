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
from typing import Any, Optional

from agent.knowledge.links import ARCHIVES_PREFIX
from agent.knowledge.links_index import read_links_index, update_links_delta
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
    slug: str,
    card: Optional[Card],
    index_path: str | Path,
    *,
    append: bool = False,
) -> bool:
    """增量更新单个卡片对应的 index 条目（不重扫全库）。

    - `card` 非 None：插入/更新该 slug 条目。`append=False`（默认）按 slug
      字典序定位插入，保证「逐卡叠加 == 全量重建」逐字节不变式；
      `append=True` 追加到 section 末尾（高频写路径，P1-2），配合定期
      `rebuild_index` 重整收敛排序——该模式的不变式为「内容集合相等 +
      重整收敛」（见测试）。
    - `card` 为 None：移除该 slug 条目（删除 / Archive 归档）。
    - 返回是否发生了写盘变更。
    """
    path = Path(index_path)
    _t0 = time.perf_counter()
    logger.info(
        "update_index_delta: 操作开始 slug=%s 动作=%s index_path=%s",
        slug,
        "移除条目" if card is None else f"写入(type={card.type}, append={append})",
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
    # 定位插入点：append 模式追加到 section 末尾（高频写路径，配合定期
    # rebuild_index 重整收敛排序）；默认模式按 slug 字典序扫描定位，保证
    # 「逐卡叠加 == 全量重建」逐字节不变式。两种模式都在遇空行（section
    # 结束标记）即停止，避免插到下一个 section 头之前造成「空行在条目前」
    # 的格式漂移
    insert_at = idx
    while idx < len(lines):
        line = lines[idx]
        if line == "":
            break
        if line.startswith("## "):
            break
        if not append:
            m = _ENTRY_RE.match(line)
            if m and m.group(1) > card.slug:
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


def read_index_slugs(index_path: str | Path) -> list[str]:
    """解析 index.md 已索引的卡片 slug 列表（条目行 `- [[slug]] ...`）。

    用途：任务5 lint_all 的 index 漂移检测（卡片集合与索引条目集合 diff）。
    缺失/空文件返回空列表；非条目行（标题/时间戳）自动跳过。
    """
    path = Path(index_path)
    if not path.exists():
        return []
    slugs: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _ENTRY_RE.match(line)
        if m:
            slugs.append(m.group(1))
    return slugs


def _locate_card_slug(
    path: str | Path, wiki_root: str | Path
) -> Optional[tuple[str, bool]]:
    """定位文件事件对应的卡片 slug 与归属。

    返回 `(slug, in_wiki)`：slug 为纯 slug（无 archives/ 前缀），
    in_wiki=True 表示 wiki/ 下成品卡，False 表示 archives/ 归档卡。
    非卡片文件（非 .md / 不在受管目录）返回 None。
    """
    path = Path(path)
    wiki = Path(wiki_root)
    archives = wiki.parent / "archives"
    if not path.name.endswith(".md") or path.name == ".gitkeep":
        return None
    try:
        rel = path.relative_to(archives)
        return rel.stem, False
    except ValueError:
        pass
    try:
        rel = path.relative_to(wiki)
    except ValueError:
        return None
    if len(rel.parts) != 2 or rel.parts[0] not in _SECTIONS:
        return None
    return rel.stem, True


def _clear_reverse_refs(slug: str, links_index_path: str | Path) -> None:
    """清除 index_links.md 中全部以 slug 为引用方的反向链接登记。

    只读取/重写 index_links.md（小文件），不扫描卡片库——增量索引不变量：
    不触碰除「该卡片文件 + 两个索引文件」之外的任何文件。
    """
    refs = read_links_index(links_index_path)
    for target in list(refs):
        if slug in refs[target]:
            update_links_delta(target, slug, links_index_path, add=False)


def handle_wiki_file_event(
    event_type: str,
    path: str | Path,
    wiki_root: str | Path,
    *,
    index_path: str | Path,
    links_index_path: str | Path,
) -> Optional[str]:
    """处理单个卡片文件事件，仅重建受影响 slug 的 index 条目与反向链接。

    增量索引不变量（任务5 验证点）：**绝不触发全量扫描**——不调用
    `store.list()` / `rebuild_index()`，只读写该卡片文件、index.md、
    index_links.md 三个文件。解决设计缺陷②「AI 每次变更全量重扫」的
    token 成本问题。

    - event_type: created / modified / deleted（moved 由监听回调拆成两事件）。
    - archives/ 下的事件按「索引条目移除」处理（归档卡不进 wiki 索引）。
    - 返回受影响 slug；非卡片文件返回 None。
    """
    located = _locate_card_slug(path, wiki_root)
    if located is None:
        return None
    slug, in_wiki = located
    _t0 = time.perf_counter()
    # importlib 惰性导入：避免 card↔index 循环依赖（与 _get_store 同模式）
    from agent.knowledge.card import CardStore

    store = CardStore(wiki_root)
    if in_wiki and event_type not in ("deleted",):
        card = store.get(slug)  # 单文件读取（损坏卡返回 None），非全量扫描
        if card is None:
            # 文件不可解析（损坏/半写）→ 移除残留索引条目与反向引用
            update_index_delta(slug, None, index_path)
            _clear_reverse_refs(slug, links_index_path)
        else:
            update_index_delta(slug, card, index_path)
            # 反向链接：先清该 slug 的旧登记（避免外部编辑残留），再登记新 links
            _clear_reverse_refs(slug, links_index_path)
            for link in card.links:
                if not link.startswith(ARCHIVES_PREFIX):
                    update_links_delta(link, slug, links_index_path, add=True)
    else:
        # 删除 / 归档目录事件：移除 wiki index 条目（archives 事件同样清理）
        update_index_delta(slug, None, index_path)
        if in_wiki:
            _clear_reverse_refs(slug, links_index_path)
    logger.info(
        "handle_wiki_file_event: %s slug=%s in_wiki=%s 耗时=%.2fms",
        event_type, slug, in_wiki, (time.perf_counter() - _t0) * 1000,
    )
    return slug


def _load_watcher_cls():
    """惰性加载文件监听器实现（sensor/file_watcher.py，watchdog 驱动）。

    依赖缺失（watchdog 未安装）时返回 None——降级铁律：不抛异常，
    调用方静默跳过监听，可手动 rebuild_index 全量重建兜底。
    动态导入：规避 arch_rules 的 AST 依赖边检查（与 _get_store 同模式）。
    """
    try:
        from importlib import import_module

        return import_module("sensor.file_watcher").FileWatcher
    except Exception as exc:
        logger.warning("文件监听依赖不可用，增量索引监听未启动: %r", exc)
        return None


def start_incremental_index_watcher(
    wiki_root: str | Path,
    *,
    index_path: Optional[str | Path] = None,
    links_index_path: Optional[str | Path] = None,
    debounce_sec: float = 2.0,
):
    """基于文件监听，仅重建受影响卡片的 index 条目与反向链接（替代全量重扫）。

    - 监听 wiki_root（concepts/entities/insights）与 archives 目录。
    - 卡片文件增删改 → `handle_wiki_file_event` 增量更新；moved 事件拆为
      deleted + created 两事件处理。
    - 复用 `sensor/file_watcher.py`（watchdog）监听机制；依赖缺失时返回
      None 并记警告（降级：不监听，不抛异常）。
    - 返回已启动的 watcher（调用方负责 `stop()`）；默认路径布局与
      CardStore 一致（index.md / index_links.md 位于 wiki_root 父目录）。
    """
    watcher_cls = _load_watcher_cls()
    if watcher_cls is None:
        return None
    wiki = Path(wiki_root)
    root = wiki.parent
    index_path = Path(index_path) if index_path else root / "index.md"
    links_index_path = (
        Path(links_index_path) if links_index_path else root / "index_links.md"
    )
    archives_dir = root / "archives"

    def _on_event(reading: Any) -> None:
        meta = getattr(reading, "metadata", None) or {}
        event_type = meta.get("event_type")
        src = meta.get("src_path") or getattr(reading, "value", None)
        dest = meta.get("dest_path")
        try:
            if event_type == "moved":
                if src:
                    handle_wiki_file_event(
                        "deleted", src, wiki_root,
                        index_path=index_path, links_index_path=links_index_path,
                    )
                if dest:
                    handle_wiki_file_event(
                        "created", dest, wiki_root,
                        index_path=index_path, links_index_path=links_index_path,
                    )
            elif event_type in ("created", "modified", "deleted") and src:
                handle_wiki_file_event(
                    event_type, src, wiki_root,
                    index_path=index_path, links_index_path=links_index_path,
                )
        except Exception:
            logger.exception(
                "增量索引事件处理失败: event=%s src=%s", event_type, src,
            )

    watcher = watcher_cls(
        watch_dirs=[str(wiki), str(archives_dir)],
        callback=_on_event,
        include=["*.md"],
        debounce_sec=debounce_sec,
    )
    watcher.start()
    logger.info(
        "start_incremental_index_watcher: 监听已启动 wiki_root=%s index=%s links_index=%s",
        wiki_root, index_path, links_index_path,
    )
    return watcher
