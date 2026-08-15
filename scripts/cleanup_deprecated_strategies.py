#!/usr/bin/env python3
"""任务6 清理脚本：物理删除 deprecated 超过 N 天的策略（SQLite evolution.db）

背景: 策略库"只追加不删除"，旧策略以 deprecated 标记淘汰（【不易】约束）。
本脚本在运维窗口对已 deprecated 且存活超过 --days 天的策略执行**物理删除**，
防止库无限膨胀。默认 dry-run（只展示不删除），加 --execute 才真正 DELETE。

运行:
  python scripts/cleanup_deprecated_strategies.py                     # dry-run 预览
  python scripts/cleanup_deprecated_strategies.py --days 30 --execute # 真删（默认 30 天）
  python scripts/cleanup_deprecated_strategies.py --db <path> --days 30 --execute

注意: status 存放在 strategies 表的 data(JSON) 列内，created_at 为顶层 REAL 列。
"""

import argparse
import datetime
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agent.evolution.injector import _DB_FILENAME
from agent.evolution.selector import STATUS_DEPRECATED, Strategy


def _fmt_ts(ts: float) -> str:
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except (ValueError, OSError, TypeError):
        return "-"


def main() -> int:
    default_db = os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "data", "evolution", _DB_FILENAME))
    parser = argparse.ArgumentParser(description="物理删除 deprecated 超 N 天的策略")
    parser.add_argument("--db", default=default_db, help="evolution.db 路径")
    parser.add_argument("--days", type=int, default=30,
                        help="deprecated 存活超过 N 天才删（默认 30）")
    parser.add_argument("--execute", action="store_true",
                        help="真正执行 DELETE（默认 dry-run 仅预览）")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"[库不存在] {args.db}")
        print("  当前无 evolution.db —— 策略库尚未使用 SQLite 后端或尚无数据，无需清理。")
        return 0

    now = time.time()
    threshold = now - args.days * 86400
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        rows = conn.execute(
            "SELECT id, strategy_id, data, created_at FROM strategies ORDER BY id"
        ).fetchall()
    except sqlite3.Error as e:
        conn.close()
        print(f"[读取失败] {e}")
        return 1

    doomed: list = []   # (id, strategy_id, scope, created_at, age_days)
    parsed_errors = 0
    for r in rows:
        try:
            s = Strategy.from_dict(json.loads(r["data"]))
        except Exception:
            parsed_errors += 1
            continue
        created = s.created_at if isinstance(s.created_at, (int, float)) else r["created_at"]
        if (s.status == STATUS_DEPRECATED and created
                and isinstance(created, (int, float)) and created < threshold):
            age = (now - created) / 86400
            doomed.append((r["id"], s.strategy_id, s.scope, created, age))

    print("=" * 72)
    print(f"deprecated 清理预览: {args.db}")
    print(f"  阈值: deprecated 且创建早于 {_fmt_ts(threshold)}（存活 > {args.days} 天）")
    print(f"  策略总数: {len(rows)}" + (f"（{parsed_errors} 条解析失败跳过）" if parsed_errors else ""))
    print(f"  可清理  : {len(doomed)} 条 deprecated 超期策略")
    print("=" * 72)
    if not doomed:
        print("  无需清理（无 deprecated 超期策略）")
    for _id, sid, scope, created, age in doomed:
        print(f"    {sid}  scope={scope:<26} 创建={_fmt_ts(created)} 已存活 {age:.1f} 天")

    if args.execute and doomed:
        ids = [d[0] for d in doomed]
        placeholders = ",".join("?" * len(ids))
        try:
            conn.execute(f"DELETE FROM strategies WHERE id IN ({placeholders})", ids)
            conn.commit()
            print(f"\n  已物理删除 {len(ids)} 条策略（其余 active/未超期策略保留）")
        except sqlite3.Error as e:
            print(f"\n[删除失败] {e}")
            conn.close()
            return 1
    else:
        print("\n  （dry-run：未执行删除，确认后加 --execute 才物理删除）")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
