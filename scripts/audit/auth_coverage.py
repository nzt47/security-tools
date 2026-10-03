#!/usr/bin/env python3
"""鉴权覆盖率盘点 + 基线生成（安全·2026-10-03）。

与 tests/unit/test_auth_coverage_baseline.py 配对：本脚本写基线，测试守基线。

    python scripts/audit/auth_coverage.py            # 只打印
    python scripts/audit/auth_coverage.py --write    # 写 reports/auth_coverage_baseline.json

【两面合起来才是完整判定】
  装饰器面（本脚本）：有没有加鉴权；
  豁免清单面（agent.server_auth.find_allowed_write_endpoints）：有没有被放开。
两面的**交集**才是真正裸奔的写端点 —— 审计 H-5 正是「漏装饰器 且 被豁免」，
只扫一面都发现不了。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "reports" / "auth_coverage_baseline.json"


def _load_scanner():
    """复用测试里的扫描实现，避免两份口径漂移。"""
    path = ROOT / "tests" / "unit" / "test_auth_coverage_baseline.py"
    spec = importlib.util.spec_from_file_location("auth_cov_scan", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser(description="鉴权覆盖率盘点")
    ap.add_argument("--write", action="store_true", help="写入基线文件")
    args = ap.parse_args()

    scan = _load_scanner()
    current = sorted(scan.collect_unguarded_mutating())

    by_file = {}
    for row in current:
        key = row.split(":")[0]
        by_file[key] = by_file.get(key, 0) + 1

    print("=" * 70)
    print("  鉴权覆盖率盘点（无鉴权装饰器的变更型路由）")
    print("=" * 70)
    for f, n in sorted(by_file.items(), key=lambda kv: -kv[1]):
        print("  " + str(n).rjust(3) + "  " + f)
    print("-" * 70)
    print("  合计 " + str(len(current)) + " 条")
    for row in current:
        print("    " + row)

    if args.write:
        payload = {
            "note": (
                "无鉴权装饰器的变更型路由（只允许收缩）。它不等于当前可未授权调用 —— "
                "本部署 CP_API_AUTH_MODE=enforce_all，全局闸门已覆盖变更型方法。本基线的"
                "意义是：① 新写端点必须自带鉴权；② 档位若被下调，这里就是裸奔清单。"
            ),
            "cleanup_progress": "存量未清；每修一条请重跑 --write 使基线收缩",
            "total_unguarded_mutating": len(current),
            "by_file": by_file,
            "unguarded_mutating": current,
        }
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print("[auth_coverage] 基线已写入 " + str(OUT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())