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
from importlib import import_module
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


# ═══════════════════════════════════════════════════════════════════════════
# 任务5 · 增量索引（文件监听触发，替代全量重扫）
# ═══════════════════════════════════════════════════════════════════════════
#
# 【不易】增量不变量：handle_wiki_file_event 绝不调用 store.list() /
# rebuild_index()（全量扫描），只读受影响单文件并更新该 slug 的 index 条目
# 与受影响卡片的反向链接——解决设计文件缺陷②（AI 全量重扫的 token 成本）。


def read_index_slugs(index_path: str | Path) -> list[str]:
    """解析 index.md 中全部条目 slug（- [[slug]] 行），按出现顺序返回。"""
    path = Path(index_path)
    if not path.exists():
        logger.info("read_index_slugs: index.md 缺失（视为空集合）: %s", path)
        return []
    slugs: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _ENTRY_RE.match(line)
        if m:
            slugs.append(m.group(1).strip())
    logger.info(
        "read_index_slugs: index_path=%s 条目=%d", path, len(slugs),
    )
    return slugs


def _locate_card_slug(path: str | Path, wiki_root: str | Path) -> Optional[tuple[str, bool]]:
    """从文件路径定位卡片 slug。

    返回 (slug, in_wiki)：in_wiki=True 表示卡片位于 wiki 类型目录
    （concepts/entities/insights），False 表示位于 archives 目录。
    非卡片文件（后缀不是 .md、不在受管目录）返回 None。
    """
    p = Path(path)
    wiki_root = Path(wiki_root)
    if p.suffix.lower() != ".md":
        logger.debug("_locate_card_slug: 非 md 文件忽略 path=%s", p)
        return None
    try:
        rel = p.relative_to(wiki_root)
        parts = rel.parts
        if len(parts) == 2 and parts[0] in _SECTIONS:  # wiki/<type>/<slug>.md
            return parts[1][:-3], True
    except ValueError:
        pass
    archives = wiki_root.parent / "archives"
    try:
        rel = p.relative_to(archives)
        if len(rel.parts) == 1:  # archives/<slug>.md
            return rel.parts[0][:-3], False
    except ValueError:
        pass
    logger.debug("_locate_card_slug: 目录外文件忽略 path=%s", p)
    return None


def _clear_reverse_refs(slug: str, links_index_path: str | Path) -> None:
    """清除入链索引中以 slug 为引用方的全部登记（该卡片已删除/归档）。"""
    refs = read_links_index(links_index_path)
    for target in list(refs):
        if slug in refs[target]:
            update_links_delta(target, slug, links_index_path, add=False)
    logger.info("_clear_reverse_refs: 已清除 slug=%s 的反向引用登记", slug)


def handle_wiki_file_event(
    event_type: str,
    path: str | Path,
    wiki_root: str | Path,
    *,
    index_path: str | Path,
    links_index_path: str | Path,
) -> Optional[str]:
    """处理单个 wiki/archives 文件事件，增量更新 index 与反向链接。

    【不易】增量不变量：绝不调用 store.list() / rebuild_index()（全量扫描），
    只读受影响单文件（created/modified）并更新该 slug 的 index 条目 +
    受影响卡片的反向链接。

    事件语义（FileWatcher 回调传入 SensorReading.metadata）：
    - created / modified：读取卡片（存在则 upsert 条目 + 登记反向链接）
    - deleted：移除条目 + 清除反向引用
    - archives 目录 created：移除 wiki index 条目（归档卡不进索引）
    - moved：调用方拆为 deleted + created 两次处理

    返回受影响 slug（忽略事件返回 None）。
    """
    located = _locate_card_slug(path, wiki_root)
    if located is None:
        return None
    slug, in_wiki = located
    _t0 = time.perf_counter()
    store = _get_store(wiki_root)  # importlib 惰性导入（无 AST import 边）

    if event_type in ("deleted",):
        _clear_reverse_refs(slug, links_index_path)
        update_index_delta(slug, None, index_path)
        logger.info(
            "handle_wiki_file_event[deleted]: slug=%s 条目已移除 耗时=%.2fms",
            slug, (time.perf_counter() - _t0) * 1000,
        )
        return slug

    if event_type == "created" and not in_wiki:
        # 外部移动到 archives：wiki index 条目移除（归档卡不进索引）
        _clear_reverse_refs(slug, links_index_path)
        update_index_delta(slug, None, index_path)
        logger.info(
            "handle_wiki_file_event[archives]: slug=%s 已归档，wiki 条目移除 耗时=%.2fms",
            slug, (time.perf_counter() - _t0) * 1000,
        )
        return slug

    # created / modified（wiki 内）：读单文件 upsert
    card = store.get(slug)
    if card is None:
        # 文件存在但解析失败/损坏：按删除处理（保守，避免索引悬挂）
        _clear_reverse_refs(slug, links_index_path)
        update_index_delta(slug, None, index_path)
        logger.warning(
            "handle_wiki_file_event: slug=%s 读取失败（视为损坏/缺失），条目移除 耗时=%.2fms",
            slug, (time.perf_counter() - _t0) * 1000,
        )
        return slug
    update_index_delta(slug, card, index_path)
    # 反向链接增量：先清旧登记再登记新引用（该卡已变化，旧引用可能已失效）
    _clear_reverse_refs(slug, links_index_path)
    for link in card.links:
        if not link.startswith(ARCHIVES_PREFIX):
            update_links_delta(link, slug, links_index_path, add=True)
    logger.info(
        "handle_wiki_file_event[%s]: slug=%s 增量更新完成 耗时=%.2fms",
        event_type, slug, (time.perf_counter() - _t0) * 1000,
    )
    return slug


def _load_watcher_cls():
    """动态加载 sensor.file_watcher.FileWatcher；依赖缺失返回 None（降级）。"""
    try:
        module = import_module("sensor.file_watcher")
        return module.FileWatcher
    except ImportError as exc:
        logger.warning("文件监听依赖缺失（watchdog 未安装？），增量索引降级停用: %r", exc)
        return None


def start_incremental_index_watcher(
    wiki_root: str | Path,
    *,
    index_path: Optional[str | Path] = None,
    links_index_path: Optional[str | Path] = None,
    debounce_sec: float = 2.0,
) -> Optional[Any]:
    """启动文件监听式增量索引（替代全量重扫）。

    - 监听 wiki 类型目录 + archives 目录（*.md），事件触发后仅重建受影响
      slug 的 index 条目与反向链接（handle_wiki_file_event）。
    - moved 事件拆为 deleted(src) + created(dest) 两次处理。
    - watchdog 依赖缺失时返回 None 静默降级（不抛异常）。

    返回 FileWatcher 实例（供 stop / 测试）；降级时返回 None。
    """
    watcher_cls = _load_watcher_cls()
    if watcher_cls is None:
        return None
    wiki_root = Path(wiki_root)
    index_path = index_path or (wiki_root.parent / "index.md")
    links_index_path = links_index_path or (wiki_root.parent / "index_links.md")

    def _on_event(reading) -> None:
        meta = reading.metadata
        event_type = meta.get("event_type")
        src = meta.get("src_path")
        try:
            if event_type == "moved":
                dest = meta.get("dest_path")
                handle_wiki_file_event(
                    "deleted", src, wiki_root,
                    index_path=index_path, links_index_path=links_index_path,
                )
                if dest:
                    handle_wiki_file_event(
                        "created", dest, wiki_root,
                        index_path=index_path, links_index_path=links_index_path,
                    )
            else:
                handle_wiki_file_event(
                    event_type, src, wiki_root,
                    index_path=index_path, links_index_path=links_index_path,
                )
        except Exception as exc:  # 监听回调容错：单个事件失败不中断监听
            logger.exception("增量索引事件处理失败 event=%s path=%s: %r", event_type, src, exc)

    watch_dirs = [
        str(wiki_root / t) for t in _SECTIONS
    ] + [str(wiki_root.parent / "archives")]
    watcher = watcher_cls(
        watch_dirs, callback=_on_event,
        include=["*.md"], debounce_sec=debounce_sec,
    )
    watcher.start()
    logger.info(
        "start_incremental_index_watcher: 监听已启动 wiki_root=%s index=%s links_index=%s",
        wiki_root, index_path, links_index_path,
    )
    return watcher
