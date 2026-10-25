#!/usr/bin/env python
"""把存量「外部记忆导入」（消息流）迁到向量记忆 —— §10 第 3 步。

用法：
    python scripts/migrate_memory_import_to_vector.py                  # 只读盘点（默认 dry-run）
    python scripts/migrate_memory_import_to_vector.py --commit --confirm

判据：
  · 默认 dry-run，不写向量、不删消息；
  · 真迁必须同时给 --commit 与 --confirm（与导入器 commit 同一口径：判据在路上，不在心里）；
  · 迁移顺序是"写向量 → 清单登记 vector_ids → 删原消息 → 标 migrated → 写审计"，
    删原消息失败会**补偿删除刚写的向量**，不留双写。

退出码：0 正常；2 参数错误；3 需要确认（给了 --commit 但没给 --confirm）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.memory_import import migrate as migrate_mod  # noqa: E402
from agent.memory_import.models import MemoryImportError  # noqa: E402


def _print(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _cmd(args) -> int:
    root = Path(args.root) if args.root else None
    messages = Path(args.messages) if args.messages else None
    result = migrate_mod.migrate(
        commit=bool(args.commit),
        confirm=bool(args.confirm),
        root=root,
        messages_path=messages,
    )
    if args.json:
        _print(result)
        return 0 if result.get("ok") else 1
    if result.get("dry_run"):
        pending = result.get("would") or []
        print(f"待迁移批次：{len(pending)} 个（dry-run，未写向量、未删任何消息）")
        for item in pending:
            print(f"  {item['batch_id']}  {item.get('origin') or '?'}/"
                  f"{item.get('period') or '?'}  {item['chars']:>6} 字符  "
                  f"msg_id={item['msg_id']}  {item['title']}")
        for batch_id in result.get("missing_in_messages") or []:
            print(f"  [跳过] {batch_id}：清单有、消息流里已找不到（可能已被按 id 删除）")
        if result.get("messages_without_manifest"):
            print(f"  [提示] 消息流里有导入前缀但无清单的批次："
                  f"{result['messages_without_manifest']}（不归本脚本处理）")
        print("确认后执行：--commit --confirm（会物理删除 messages.jsonl 里的原行）")
        return 0
    migrated = result.get("migrated") or []
    failed = result.get("failed") or []
    print(f"迁移完成：成功 {len(migrated)} 个批次，失败 {len(failed)} 个")
    for item in migrated:
        seq = (item.get("audit") or {}).get("seq")
        print(f"  {item['batch_id']} -> {item['vector_ids']}  "
              f"原消息已删={item['deleted']}  审计 seq={seq}")
    for item in failed:
        print(f"  [失败] {item['batch_id']}：{item['error']}")
    return 0 if not failed else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="把存量外部记忆导入从消息流迁到向量记忆（默认 dry-run）")
    parser.add_argument("--root", help="memory_import 数据根（默认 data/ingestion/memory_import）")
    parser.add_argument("--messages", help="消息文件路径（默认 data/messages.jsonl）")
    parser.add_argument("--commit", action="store_true", help="真迁移（需同时 --confirm）")
    parser.add_argument("--confirm", action="store_true", help="确认物理删除原消息")
    parser.add_argument("--json", action="store_true", help="输出 JSON（脚本/审计用）")
    args = parser.parse_args(argv)
    try:
        return int(_cmd(args) or 0)
    except MemoryImportError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "title": exc.title,
                          "message": exc.message, "detail": exc.detail},
                         ensure_ascii=False, indent=2))
        return 3 if exc.code == "confirmation-required" else 2
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
