"""知识卡片引擎 CLI — python -m agent.knowledge 统一入口

与 agent/preflight 同款 `python -m <pkg>` 模式（CI 与本地一致），
集成任务2 验证通过的引擎核心逻辑（card / links / index 三大模块）：

    index-rebuild      全量重建 index.md（rebuild_index）
    card-list          列出卡片（可按 status / type 过滤）
    card-transition    状态迁移（draft→current→archive，非法迁移报错）
    check-links        断链检测（find_broken_links，检出断链 exit 1）
    orphans            孤儿检测（find_orphans）

退出码约定（不易）：0 = 成功；1 = 运行出错（卡片不存在 / 非法状态迁移 / 检出断链）。
--verbose 打开 logging INFO，可观测各模块耗时统计与断链调试日志。
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

from agent.knowledge.card import (
    CardConflictError,
    CardNotFoundError,
    CardStore,
    InvalidTransitionError,
)
from agent.knowledge.distill import promote_to_card
from agent.knowledge.index import rebuild_index
from agent.knowledge.links import find_broken_links, find_orphans

# 默认布局（AGENTS.md）：knowledge/wiki + knowledge/index.md
_DEFAULT_WIKI = "knowledge/wiki"
_DEFAULT_INDEX = "knowledge/index.md"

logger = logging.getLogger(__name__)


def cmd_index_rebuild(args: argparse.Namespace) -> int:
    """全量重建 index.md（rebuild_index 与增量叠加一致性已由测试保障）。"""
    logger.info("CLI index-rebuild: 开始全量重建 wiki=%s index=%s", args.wiki, args.index)
    count = rebuild_index(args.wiki, args.index)
    print(f"index.md 全量重建完成，索引卡片数={count}")
    logger.info("CLI index-rebuild: 完成，索引卡片数=%d", count)
    return 0


def cmd_card_list(args: argparse.Namespace) -> int:
    """列出卡片（按 slug 字典序，可过滤）。"""
    store = CardStore(args.wiki)
    cards = store.list(status=args.status, type=args.type)
    logger.info(
        "CLI card-list: wiki=%s 过滤 status=%s type=%s 命中卡片数=%d",
        args.wiki, args.status, args.type, len(cards),
    )
    for c in cards:
        print(f"{c.slug}\t{c.status}\t{c.type}\t{c.insight}")
    print(f"共 {len(cards)} 张卡片")
    return 0


def cmd_card_transition(args: argparse.Namespace) -> int:
    """状态迁移；非法迁移/卡片不存在 → exit 1。"""
    store = CardStore(args.wiki)
    logger.info(
        "CLI card-transition: 尝试迁移 slug=%s → %s wiki=%s",
        args.slug, args.to_status, args.wiki,
    )
    try:
        store.transition(args.slug, args.to_status)
    except (CardNotFoundError, InvalidTransitionError) as exc:
        logger.warning("CLI card-transition: 迁移失败 slug=%s → %s 原因=%s",
                       args.slug, args.to_status, exc)
        print(f"迁移失败: {exc}", file=sys.stderr)
        return 1
    print(f"{args.slug} → {args.to_status} ✓")
    logger.info("CLI card-transition: 迁移成功 slug=%s → %s", args.slug, args.to_status)
    return 0


def cmd_check_links(args: argparse.Namespace) -> int:
    """断链检测；检出任何断链 → exit 1（可作 CI 健康门禁）。"""
    store = CardStore(args.wiki)
    broken = find_broken_links(store.list(), store)
    if not broken:
        print("无断链 ✓")
        logger.info("CLI check-links: 无断链 wiki=%s", args.wiki)
        return 0
    for b in broken:
        print(f"断链: {b['from_slug']} → {b['to_slug']}")
    print(f"检出 {len(broken)} 条断链 ✗", file=sys.stderr)
    logger.warning("CLI check-links: 检出 %d 条断链 wiki=%s", len(broken), args.wiki)
    return 1


def cmd_orphans(args: argparse.Namespace) -> int:
    """孤儿检测（无入链卡片）；报告型命令，恒 exit 0。"""
    store = CardStore(args.wiki)
    orphans = find_orphans(store.list())
    logger.info("CLI orphans: 检出孤儿数=%d wiki=%s", len(orphans), args.wiki)
    if not orphans:
        print("无孤儿卡片 ✓")
        return 0
    for slug in orphans:
        print(f"孤儿: {slug}")
    print(f"共 {len(orphans)} 张孤儿卡片")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    """批量导入目录下 *.md 卡片；有失败 → exit 1（CI 门禁）。"""
    store = CardStore(args.wiki)
    logger.info("CLI import: 批量导入 dir=%s force=%s wiki=%s",
                args.dir, args.force, args.wiki)
    try:
        result = store.import_from_dir(args.dir, force=args.force)
    except ValueError as exc:
        logger.warning("CLI import: 导入失败 dir=%s 原因=%s", args.dir, exc)
        print(f"导入失败: {exc}", file=sys.stderr)
        return 1
    print(f"导入完成: 成功 {result.imported} / 跳过冲突 {result.skipped} / 失败 {result.failed}")
    for name, reason in result.failures:
        print(f"  失败 {name}: {reason}", file=sys.stderr)
        logger.warning("CLI import: 文件失败 name=%s 原因=%s", name, reason)
    return 1 if result.failed else 0


def cmd_export(args: argparse.Namespace) -> int:
    """导出卡片为 frontmatter md（可再 import 回读）；纯报告命令恒 exit 0。"""
    store = CardStore(args.wiki)
    logger.info("CLI export: 导出到 dir=%s 过滤 status=%s type=%s wiki=%s",
                args.dir, args.status, args.type, args.wiki)
    try:
        n = store.export_dir(args.dir, status=args.status, type=args.type)
    except (OSError, ValueError) as exc:
        logger.warning("CLI export: 导出失败 dir=%s 原因=%s", args.dir, exc)
        print(f"导出失败: {exc}", file=sys.stderr)
        return 1
    print(f"导出 {n} 张卡片 → {args.dir}")
    logger.info("CLI export: 完成，导出卡片数=%d", n)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """列出卡片（按 type 分组 + 状态统计摘要）。"""
    store = CardStore(args.wiki)
    cards = store.list(status=args.status, type=args.type)
    by_type: dict[str, list] = {}
    for c in cards:
        by_type.setdefault(c.type, []).append(c)
    for t, group in sorted(by_type.items()):
        for c in group:
            print(f"  [{t}] {c.slug} ({c.status})")
    stats = Counter(c.status for c in cards)
    summary = " / ".join(f"{k} {v}" for k, v in sorted(stats.items()))
    print(f"共 {len(cards)} 张卡片（{summary}）")
    logger.info("CLI list: wiki=%s 过滤 status=%s type=%s 命中卡片数=%d",
                args.wiki, args.status, args.type, len(cards))
    return 0


def cmd_card_from_note(args: argparse.Namespace) -> int:
    """从 processed/ 已确认笔记产卡（任务3 → 任务2 对接）；失败 → exit 1。"""
    wiki_root = Path(args.wiki)
    knowledge_root = Path(args.knowledge) if args.knowledge else wiki_root.parent
    logger.info("CLI card-from-note: slug=%s card_type=%s wiki=%s knowledge=%s",
                args.slug, args.card_type, args.wiki, knowledge_root)
    try:
        card = promote_to_card(
            args.slug,
            card_type=args.card_type,
            knowledge_root=knowledge_root,
            wiki_root=wiki_root,
        )
    except (FileNotFoundError, ValueError, CardConflictError) as exc:
        logger.warning("CLI card-from-note: 产卡失败 slug=%s 原因=%s", args.slug, exc)
        print(f"产卡失败: {exc}", file=sys.stderr)
        return 1
    print(f"产卡成功: {card.slug}（type={card.type} status={card.status}）")
    logger.info("CLI card-from-note: 产卡成功 slug=%s type=%s", card.slug, card.type)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agent.knowledge",
        description="知识卡片引擎 CLI（任务2 核心逻辑：card/links/index）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("index-rebuild", help="全量重建 index.md")
    p.add_argument("--wiki", default=_DEFAULT_WIKI)
    p.add_argument("--index", default=_DEFAULT_INDEX)
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_index_rebuild)

    p = sub.add_parser("card-list", help="列出卡片")
    p.add_argument("--status", default=None, help="按状态过滤（draft/current/unknown）")
    p.add_argument("--type", default=None, help="按类型过滤（concepts/entities/insights）")
    p.add_argument("--wiki", default=_DEFAULT_WIKI)
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_card_list)

    p = sub.add_parser("card-transition", help="状态迁移")
    p.add_argument("slug")
    p.add_argument("to_status")
    p.add_argument("--wiki", default=_DEFAULT_WIKI)
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_card_transition)

    p = sub.add_parser("check-links", help="断链检测（有断链 exit 1）")
    p.add_argument("--wiki", default=_DEFAULT_WIKI)
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_check_links)

    p = sub.add_parser("orphans", help="孤儿检测")
    p.add_argument("--wiki", default=_DEFAULT_WIKI)
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_orphans)

    p = sub.add_parser("import", help="批量导入目录下 *.md 卡片")
    p.add_argument("dir")
    p.add_argument("--force", action="store_true",
                   help="同 slug 冲突时改走 update（默认跳过不覆盖）")
    p.add_argument("--wiki", default=_DEFAULT_WIKI)
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("export", help="导出卡片为 frontmatter md（可再 import）")
    p.add_argument("dir")
    p.add_argument("--status", default=None, help="按状态过滤（draft/current/unknown）")
    p.add_argument("--type", default=None, help="按类型过滤（concepts/entities/insights）")
    p.add_argument("--wiki", default=_DEFAULT_WIKI)
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("list", help="列出卡片（按 type 分组 + 状态统计）")
    p.add_argument("--status", default=None, help="按状态过滤（draft/current/unknown）")
    p.add_argument("--type", default=None, help="按类型过滤（concepts/entities/insights）")
    p.add_argument("--wiki", default=_DEFAULT_WIKI)
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser(
        "card-from-note",
        help="从 processed/ 已确认笔记产卡（任务3 → 任务2 对接）",
    )
    p.add_argument("slug", help="processed/ 笔记 slug（须已 approve）")
    p.add_argument("--card-type", default="concepts",
                   help="卡片类型（concepts/entities/insights）")
    p.add_argument("--wiki", default=_DEFAULT_WIKI)
    p.add_argument("--knowledge", default=None,
                   help="knowledge 根（含 processed/），默认取 --wiki 的父目录")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_card_from_note)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.verbose:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
