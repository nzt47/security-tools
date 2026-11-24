#!/usr/bin/env python
"""E0-3 检查点 CLI（只读为主；`snapshot` 会写一份新检查点）。

用法：
  python scripts/checkpoint_cli.py snapshot [--note 文本]
  python scripts/checkpoint_cli.py list [--limit 20]
  python scripts/checkpoint_cli.py show <id>
  python scripts/checkpoint_cli.py diff <id> [--against <id2>]     # 不给 against = 与当前工作区比
  python scripts/checkpoint_cli.py status
  ... 均可加 --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.context import checkpoint  # noqa: E402


def _print(payload, as_json: bool) -> None:
    if as_json or not isinstance(payload, (dict, list)):
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return
    if isinstance(payload, list):
        for row in payload:
            print(f"{row.get('id')}  {row.get('created_at')}  {row.get('trigger_tool')}"
                  f"  改动 {row.get('changed')} / 捕获 {row.get('captured')}"
                  f"{'  (截断)' if row.get('truncated') else ''}")
        return
    print(json.dumps(payload, ensure_ascii=False, indent=1))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="云枢 E0-3 检查点：list/show/diff/snapshot/status")
    ap.add_argument("command", choices=["list", "show", "diff", "snapshot", "status"])
    ap.add_argument("checkpoint_id", nargs="?", default="", help="检查点 id（show/diff 用）")
    ap.add_argument("--against", default="", help="diff 时与另一个检查点比（默认与当前工作区比）")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--note", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.command == "status":
        _print(checkpoint.status(), args.json)
        return 0
    if args.command == "list":
        _print(checkpoint.list_checkpoints(limit=args.limit), args.json)
        return 0
    if args.command == "snapshot":
        manifest = checkpoint.snapshot(trigger_tool="manual", note=args.note)
        if manifest is None:
            print("无改动（或不是 git 工作区 / 快照已关闭）")
            return 0
        _print({"id": manifest["id"], "changed": manifest["changed_total"],
                "captured": manifest["files_captured"], "diff_bytes": manifest["diff_bytes"]},
               args.json)
        return 0
    if not args.checkpoint_id:
        print("show/diff 需要 checkpoint id", file=sys.stderr)
        return 2
    if args.command == "show":
        manifest = checkpoint.load_manifest(args.checkpoint_id)
        if manifest is None:
            print("检查点不存在或 id 非法", file=sys.stderr)
            return 1
        _print(manifest, args.json)
        return 0
    result = checkpoint.compare(args.checkpoint_id, args.against or None)
    if result is None:
        print("检查点不存在或 against 非法", file=sys.stderr)
        return 1
    _print(result, args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
