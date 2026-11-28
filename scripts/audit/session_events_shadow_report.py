#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""P5 / E5-1 Phase C 放行判据报告：**连续 N 天 shadow 零差异？**

读 agent.session_events.shadow_daily()（数据来自 shadow 读链路每轮的累积：
data/session_events/_shadow_daily.json，只存会话 id 的 sha256 前 16 位，不存正文）。

用法::

    python scripts/audit/session_events_shadow_report.py            # 30 天窗口，给人看
    python scripts/audit/session_events_shadow_report.py --json     # 机器可读
    python scripts/audit/session_events_shadow_report.py --days 30 --soft   # 永远退出 0

退出码：0 = 已达放行条件（窗口内零差异且连续干净天数 >= 窗口天数）；1 = 未达；2 = 参数错误。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:  # 允许从任意目录直接运行
    sys.path.insert(0, ROOT)


def build_report(days: int) -> Dict[str, Any]:
    from agent import session_events

    summary = session_events.shadow_daily(days=days)
    ready = bool(summary["total_days"] >= days
                 and summary["diff_days"] == 0
                 and summary["consecutive_clean_days"] >= days)
    summary["ready"] = ready
    summary["reason"] = ("已达放行条件" if ready else
                        ("窗口内天数不足（%d/%d）" % (summary["total_days"], days)
                         if summary["total_days"] < days else
                         "窗口内有差异或连续干净天数不足（diff_days=%d, consecutive=%d）"
                         % (summary["diff_days"], summary["consecutive_clean_days"])))
    return summary


def _print_human(report: Dict[str, Any]) -> None:
    print("== 会话事件 shadow 零差异报告 ==")
    print("窗口            : %d 天（有数据的 %d 天）" % (report["window"], report["total_days"]))
    print("有差异的天数    : %d" % report["diff_days"])
    print("连续零差异天数  : %d" % report["consecutive_clean_days"])
    print("累计对拍次数    : %d" % sum(row["compared"] for row in report["days"]))
    if report["days"]:
        print("最近 7 天       :")
        for row in report["days"][-7:]:
            print("  %s  对拍 %-4d 差异 %-4d %s"
                  % (row["date"], row["compared"], row["diff"], "clean" if row["clean"] else "DIFF"))
    print("结论            : %s" % report["reason"])


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="会话事件 shadow 零差异报告（P5 Phase C 放行判据）")
    parser.add_argument("--days", type=int, default=30, help="窗口天数（1-60，默认 30）")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    parser.add_argument("--soft", action="store_true", help="未达条件也退出 0")
    args = parser.parse_args(argv)
    if args.days < 1 or args.days > 60:
        print("--days 只支持 1-60", file=sys.stderr)
        return 2
    try:
        report = build_report(args.days)
    except Exception as exc:  # noqa: BLE001 脚本失败只报一句，不搬 traceback
        print("读取报告失败：%s" % type(exc).__name__, file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human(report)
    if args.soft:
        return 0
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
