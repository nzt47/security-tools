"""TASK-S3-01 L4/L3 收口执行入口：存量 stage 首次入轨 + 占位 trace_policy 切换

用法::

    python scripts/run_s3_01_ingest.py --dry-run        # 干跑（只出计划）
    python scripts/run_s3_01_ingest.py --execute        # 实跑（写台账+审计+事件）
    python scripts/run_s3_01_ingest.py --execute --report data/digestion/s3_01_ingest_report.json

实跑前自动备份运行时台账到 ``data/digestion/descriptors_pre_ingest_<ts>.json``
（该目录已 gitignore；备份是**保守**取向：即使入轨结果需回滚，原台账可逐字还原）。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.descriptors.registry import DescriptorRegistry  # noqa: E402
from agent.digestion.stage import backfill_stages  # noqa: E402

DEFAULT_LEDGER = "data/descriptors.json"
BACKUP_DIR = os.path.join("data", "digestion")
DEFAULT_REPORT = os.path.join(BACKUP_DIR, "s3_01_ingest_report.json")


def _backup(ledger_path: str) -> str:
    if not os.path.exists(ledger_path):
        return ""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    target = os.path.join(BACKUP_DIR, f"descriptors_pre_ingest_{stamp}.json")
    shutil.copy2(ledger_path, target)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(
        description="TASK-S3-01 存量 stage 首次入轨 + trace_policy 占位串切换")
    parser.add_argument("--ledger", default=DEFAULT_LEDGER,
                        help="descriptor 台账路径（默认 data/descriptors.json）")
    parser.add_argument("--execute", action="store_true",
                        help="真正写入（缺省为干跑）")
    parser.add_argument("--dry-run", action="store_true",
                        help="显式声明干跑（等价于省略 --execute，便于脚本化调用）")
    parser.add_argument("--no-events", action="store_true",
                        help="不发 digest.stage 事件（仅写台账+审计）")
    parser.add_argument("--report", default=DEFAULT_REPORT,
                        help="入轨报告 JSON 落盘路径")
    parser.add_argument("--json", action="store_true", help="stdout 输出完整 JSON")
    args = parser.parse_args()
    execute = bool(args.execute and not args.dry_run)

    reg = DescriptorRegistry(path=args.ledger)
    backup = _backup(args.ledger) if execute else ""

    result = backfill_stages(reg, execute=execute,
                            emit_events=not args.no_events)
    result["ledger"] = args.ledger
    result["backup"] = backup

    os.makedirs(os.path.dirname(args.report) or ".", exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        mode = "实跑" if execute else "干跑"
        print(f"[{mode}] 台账={args.ledger}")
        if backup:
            print(f"  备份={backup}")
        print(f"  资产总数={result['total_assets']} "
              f"未入轨={result['empty_stage']} 按来源={result['by_source_type']}")
        print(f"  入轨成功={len(result['ingested'])} 失败={len(result['failed'])} "
              f"残留未入轨={result['warnings_after']}")
        policies = result.get("policies") or {}
        if policies:
            print(f"  占位 trace_policy={policies.get('placeholder', 0)} "
                  f"已切换={len(policies.get('refreshed') or [])}")
        print(f"  报告={args.report}")
    return 0 if not result["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
