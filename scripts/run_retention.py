#!/usr/bin/env python
"""保留策略归档入口（TASK-S8-01 步骤 2/3/4 的手动与验收入口）。

【三种模式（默认最保守）】
    --dry-run（默认）     只列出将归档/删除的清单与体积，**不落盘、不入审计**
    --execute             真正归档（仍受 `CP_RETENTION_DELETE_SOURCE` 约束：默认不删）
    --verify <归档件>      校验并还原抽样（临时目录，不改线上）
    --metrics              归档前后各采一次既有指标并断言一致（口径不变证据）

【为什么 `--execute` 也默认不删】
    默认策略 = 只归档不删除（批次总表 §二②）。删除需同时满足：
    ① `--execute`；② `CP_RETENTION_DELETE_SOURCE=true`；③ `PurgeGuard` 放行
    （红线 / 记忆类 / 未标可删 / 有指标依赖 / 越界 / 未归档 —— 六条任一即拒）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, Optional, Sequence

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from agent.retention.archiver import Archiver  # noqa: E402
from agent.retention.guard import PurgeGuard  # noqa: E402
from agent.retention.metrics import (  # noqa: E402
    METRIC_NAMES,
    snapshot,
    compare_metrics,
)
from agent.retention.policy import load_policy  # noqa: E402
from agent.retention.restorer import Restorer, cleanup_restore_dir  # noqa: E402


def _print_report(report: Any) -> None:
    for line in report.summary_lines():
        print(line)
    if report.notes:
        for note in report.notes:
            print(f"  · {note}")


def cmd_plan(args: argparse.Namespace) -> int:
    policy = load_policy()
    box = Archiver(policy, root=args.root or "", archive_dir=args.archive_dir or "")
    report = box.plan()
    _print_report(report)
    print("\n本模式不落盘。要执行请加 --execute（仍默认不删除）。")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report.to_dict(), fh, ensure_ascii=False, indent=2)
        print(f"[run_retention] JSON 报告已写入 {args.json}")
    return 0


def cmd_execute(args: argparse.Namespace) -> int:
    policy = load_policy()
    box = Archiver(policy, root=args.root or "", archive_dir=args.archive_dir or "")
    # 首跑纪律：首次执行先出 dry-run 清单（与调度器同一条底线）
    preview = box.plan()
    print("── 执行前预览（首跑纪律）────────────────────────────")
    _print_report(preview)
    print("────────────────────────────────────────────────────")
    report = box.run(confirm=True)
    _print_report(report)
    print(f"\n链式审计：{report.audit.get('status')}"
          f"（action=retention.run" 
          + (f"，seq={report.audit.get('seq')}" if report.audit.get("seq") else "")
          + "）")
    print(f"事件：{report.event.get('status')}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report.to_dict(), fh, ensure_ascii=False, indent=2)
        print(f"[run_retention] JSON 报告已写入 {args.json}")
    return 1 if report.totals.get("classes_error") else 0


def cmd_verify(args: argparse.Namespace) -> int:
    policy = load_policy()
    tool = Restorer(policy, root=args.root or "", archive_dir=args.archive_dir or "")
    targets = [args.verify] if args.verify != "*" else [
        a["manifest_file"] for a in tool.list_archives(args.class_id)
        if a.get("manifest_file") and not a.get("error")]
    if not targets:
        print("[run_retention] 没有可校验的归档件")
        return 0
    rc = 0
    for target in targets:
        try:
            report = tool.sample_roundtrip(target, sample=args.sample)
        except Exception as e:  # noqa: BLE001 校验失败即如实报告
            print(f"[run_retention] {target} 校验失败：{type(e).__name__}: {e}")
            rc = 1
            continue
        print(report.summary())
        for item in report.files:
            flag = "✅" if item.match else "❌"
            print(f"  {flag} {item.verdict:<10} {item.path}"
                  f"（{item.bytes} 字节）")
        print(f"  还原目录：{report.target_dir}"
              f"{'（临时，已清理）' if args.cleanup else ''}")
        if not report.ok:
            rc = 1
        if args.cleanup:
            cleanup_restore_dir(report)
    return rc


def cmd_metrics(args: argparse.Namespace) -> int:
    policy = load_policy()
    box = Archiver(policy, root=args.root or "", archive_dir=args.archive_dir or "")
    kw: Dict[str, Any] = {
        "names": tuple(args.metric or METRIC_NAMES),
        "events_dir": args.events_dir or "",
        "shadow_dir": args.shadow_dir or "",
        "audit_db": args.audit_db or "",
        "anchor_day": args.anchor_day or "",
    }
    before = snapshot(**kw)
    print("── 归档前 ──")
    for name, data in before.items():
        print(f"  {name}: available={data.get('available')} "
              f"{'' if data.get('available') else data.get('reason', '')}")
    report = box.run(confirm=bool(args.execute))
    _print_report(report)
    after = snapshot(**kw)
    print("── 归档后 ──")
    for name, data in after.items():
        print(f"  {name}: available={data.get('available')} "
              f"{'' if data.get('available') else data.get('reason', '')}")
    comparison = compare_metrics(before, after)
    print("\n── 口径复算对照 ──")
    for item in comparison.metrics:
        print("  " + item.summary())
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"before": before, "after": after,
                       "comparison": comparison.to_dict(),
                       "archiver": report.to_dict()},
                      fh, ensure_ascii=False, indent=2)
        print(f"[run_retention] JSON 报告已写入 {args.json}")
    return 0 if comparison.consistent else 1


def cmd_guard(args: argparse.Namespace) -> int:
    """打印护栏对每一类的判定（验收用：红线类必须全部被拒）。"""
    policy = load_policy()
    guard = PurgeGuard(policy, root=args.root or "")
    for row in guard.redline_scan():
        flag = "允许" if row["allowed"] else f"拒绝[{row['code']}]"
        print(f"  {row['class_id']:<22} {flag}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="数据生命周期保留策略归档（TASK-S8-01）")
    parser.add_argument("--root", default="", help="仓库根（默认项目根）")
    parser.add_argument("--archive-dir", default="", help="归档目录（默认 data/archive）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只列清单与体积，不落盘（默认行为）")
    parser.add_argument("--execute", action="store_true", help="真正执行归档")
    parser.add_argument("--verify", default="",
                        help="校验并抽样还原指定归档件（`*` = 全部）")
    parser.add_argument("--class-id", default="", help="限定数据类（--verify * 时）")
    parser.add_argument("--sample", type=int, default=0,
                        help="抽样还原前 N 个文件（0 = 全部）")
    parser.add_argument("--cleanup", action="store_true", help="还原后清理临时目录")
    parser.add_argument("--metrics", action="store_true",
                        help="归档前后复算既有指标并断言一致")
    parser.add_argument("--metric", action="append", default=None,
                        help="限定指标（可重复；默认全部受守护指标）")
    parser.add_argument("--guard", action="store_true", help="打印护栏对每一类的判定")
    parser.add_argument("--events-dir", default="", help="事件目录（指标用）")
    parser.add_argument("--shadow-dir", default="", help="灰度台账目录（指标用）")
    parser.add_argument("--audit-db", default="", help="审计链库（指标用）")
    parser.add_argument("--anchor-day", default="", help="UTC 周锚定日（指标用）")
    parser.add_argument("--json", default="", help="把报告写入该 JSON 文件")
    args = parser.parse_args(argv)

    if args.guard:
        return cmd_guard(args)
    if args.verify:
        return cmd_verify(args)
    if args.metrics:
        return cmd_metrics(args)
    if args.execute:
        return cmd_execute(args)
    return cmd_plan(args)


if __name__ == "__main__":                      # pragma: no cover
    sys.exit(main())
