#!/usr/bin/env python
"""外部记忆导入 CLI（默认全程 dry-run，写入必须显式 --confirm）。

用法：
    python scripts/memory_import.py preview --sources claude,trae
    python scripts/memory_import.py preview --sources trae --no-transcripts
    python scripts/memory_import.py show    --plan mp_xxx
    python scripts/memory_import.py commit  --plan mp_xxx            # dry-run
    python scripts/memory_import.py commit  --plan mp_xxx --batch mb_a,mb_b --confirm
    python scripts/memory_import.py rollback --batch mb_a            # dry-run
    python scripts/memory_import.py rollback --batch mb_a --confirm

退出码：0 正常；2 参数/计划错误；3 需要确认（未确认就要求写入）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.memory_import import service  # noqa: E402
from agent.memory_import.models import MemoryImportError  # noqa: E402


def _print(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _cmd_preview(args) -> int:
    sources = [s.strip() for s in str(args.sources or "claude,trae").split(",") if s.strip()]
    plan = service.preview(
        sources=sources,
        claude_dir=Path(args.claude_dir) if args.claude_dir else None,
        appdata=Path(args.appdata) if args.appdata else None,
        include_transcripts=not args.no_transcripts,
        root=Path(args.root) if args.root else None,
        save=not args.no_save,
    )
    stats = plan.get("stats") or {}
    print(f"plan_id: {plan['plan_id']}")
    print(f"扫描 {stats.get('scanned')} 条 -> 去噪 {stats.get('denoised')} / "
          f"去重 {stats.get('duplicates')} -> 保留 {stats.get('kept')} 条"
          f"（{stats.get('chars_kept')} 字符）")
    redaction = plan.get("redaction") or {}
    if redaction.get("hits"):
        print(f"脱敏：{redaction['hits']}（拒绝 {redaction.get('blocked', 0)} 条）")
    print(f"批次：{len(plan.get('batches') or [])}")
    for batch in plan.get("batches") or []:
        print(f"  {batch['batch_id']}  {batch['carrier_label']:<6} "
              f"{batch['records']:>5} 条 {batch['chars']:>6} 字符  {batch['title']}")
    if plan.get("plan_path"):
        print(f"计划快照：{plan['plan_path']}")
    print("提示：以上为 dry-run，未写入任何记忆；确认后按批次 commit。")
    return 0


def _cmd_show(args) -> int:
    plan = service.load_plan(args.plan, root=Path(args.root) if args.root else None)
    _print({k: v for k, v in plan.items() if k != "blocked_samples"})
    return 0


def _cmd_commit(args) -> int:
    batch_ids = [b.strip() for b in str(args.batch or "").split(",") if b.strip()]
    result = service.commit(
        args.plan, batch_ids or None,
        confirm=bool(args.confirm), dry_run=not args.commit,
        root=Path(args.root) if args.root else None,
    )
    _print(result)
    if result.get("dry_run"):
        print("提示：dry-run 未写入；加 --commit 并把 --confirm 一起给才会真写。")
    return 0


def _cmd_rollback(args) -> int:
    result = service.rollback(
        args.batch, confirm=bool(args.confirm), dry_run=not args.commit,
        root=Path(args.root) if args.root else None,
    )
    _print(result)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="云枢外部记忆导入（默认 dry-run）")
    parser.add_argument("--root", help="memory_import 数据根（默认 data/ingestion/memory_import）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_preview = sub.add_parser("preview", help="只读预览（默认不写任何记忆）")
    p_preview.add_argument("--sources", default="claude,trae")
    p_preview.add_argument("--claude-dir", default="")
    p_preview.add_argument("--appdata", default="")
    p_preview.add_argument("--no-transcripts", action="store_true")
    p_preview.add_argument("--no-save", action="store_true", help="不落计划快照")
    p_preview.set_defaults(func=_cmd_preview)

    p_show = sub.add_parser("show", help="查看计划快照")
    p_show.add_argument("--plan", required=True)
    p_show.set_defaults(func=_cmd_show)

    p_commit = sub.add_parser("commit", help="按批次写入（默认 dry-run）")
    p_commit.add_argument("--plan", required=True)
    p_commit.add_argument("--batch", default="", help="逗号分隔的 batch_id；缺省=全部")
    p_commit.add_argument("--commit", action="store_true", help="真写入（需同时 --confirm）")
    p_commit.add_argument("--confirm", action="store_true")
    p_commit.set_defaults(func=_cmd_commit)

    p_rollback = sub.add_parser("rollback", help="按批次回滚（默认 dry-run）")
    p_rollback.add_argument("--batch", required=True)
    p_rollback.add_argument("--commit", action="store_true")
    p_rollback.add_argument("--confirm", action="store_true")
    p_rollback.set_defaults(func=_cmd_rollback)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
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
