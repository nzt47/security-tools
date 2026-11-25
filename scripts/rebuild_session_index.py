"""全量重建会话全文索引（E1-4b）

索引是**可丢弃的派生数据**（`data/session_index.db`）；换机器/库损坏/改了分词口径时，
用本脚本从 `data/sessions/*/messages.jsonl` 重建，不丢任何事实。

用法：
    python -X utf8 scripts/rebuild_session_index.py            # 重建并打印统计
    python -X utf8 scripts/rebuild_session_index.py --json     # 机器可读
    python -X utf8 scripts/rebuild_session_index.py --dry-run  # 只读会话目录，不写索引
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.context import session_index as SI  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="全量重建会话消息全文索引（FTS5 trigram）")
    ap.add_argument("--sessions-dir", default="", help="会话目录（默认 <repo>/data/sessions）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    ap.add_argument("--dry-run", action="store_true", help="只统计会话/消息条数，不写索引")
    args = ap.parse_args(argv)

    base = Path(args.sessions_dir) if args.sessions_dir else (ROOT / "data" / "sessions")
    if not base.is_dir():
        print("[重建] 会话目录不存在: %s" % base)
        return 2

    if args.dry_run:
        sessions = 0
        messages = 0
        for d in sorted(base.iterdir()):
            f = d / "messages.jsonl"
            if not d.is_dir() or not f.is_file():
                continue
            sessions += 1
            with open(f, encoding="utf-8", errors="replace") as fh:
                messages += sum(1 for line in fh if line.strip())
        out = {"ok": True, "dry_run": True, "sessions": sessions, "messages": messages,
               "db": str(SI.db_path())}
    else:
        out = SI.rebuild(base)
        out["stats"] = SI.stats()

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print("[重建] ok=%s 会话 %s / 消息 %s / 失败 %s / 用时 %sms"
              % (out.get("ok"), out.get("sessions"), out.get("messages"),
                 out.get("failed", 0), out.get("took_ms", "-")))
        print("[重建] 索引库: %s" % out.get("db", SI.db_path()))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
