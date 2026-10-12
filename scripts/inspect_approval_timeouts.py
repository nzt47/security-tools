#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""审批超时巡检 —— 把「审批把对话拖死」变成可观测（只读）

【它回答什么】agent/data/approval_records.jsonl 是一次次工具审批的账本。实测：
    状态分布 approved 26 / rejected 26 / pending_review 2，而 rejected 的理由**全是**
    「超时未审批（timeout deny）：待办 4854s ≥ 阈值 900s」这类。
    审批是**阻塞式**的：模型要跑 shell_execute（risk critical）→ 挂成待批 → 无人批 →
    15 分钟后被超时否决 → 那一轮会话就像「答到一半不动了，也不继续」。

【怎么用】
    python scripts/inspect_approval_timeouts.py                  # 人读报告
    python scripts/inspect_approval_timeouts.py --json           # 机器读
    python scripts/inspect_approval_timeouts.py --pending-threshold-s 300
退出码：0 = 当前没有超期待办；2 = 有超期待办（要处理）；3 = 账本读不了。
历史超时否决**不影响退出码**（既成事实），只在报告里统计。

【口径】只读文件、不改任何状态；不打印 payload 里的值（只打 tool/level/risk 标签）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

DEFAULT_PATH = os.path.join("agent", "data", "approval_records.jsonl")
#: 终态（不在这些里的都算「未决待办」）
TERMINAL_STATES = ("approved", "rejected", "auto_approved", "denied", "done")
TIMEOUT_MARK = "超时未审批"


def parse_ts(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1]
    for fmt in (None, "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%m/%d/%Y %H:%M:%S"):
        try:
            return (datetime.fromisoformat(text) if fmt is None
                    else datetime.strptime(text, fmt))
        except (TypeError, ValueError):
            continue
    return None


def wait_seconds(reason: str) -> Optional[int]:
    """从 decision_reason 里取「待办 Ns」的 N（不猜：取第一个带 s 的纯数字 token）"""
    text = str(reason or "")
    if TIMEOUT_MARK not in text:
        return None
    for token in text.replace("：", " ").replace("≥", " ").split():
        if token.endswith("s") and token[:-1].isdigit():
            return int(token[:-1])
    return None


def load_records(path: str) -> Tuple[List[Dict[str, Any]], int]:
    """读账本；坏行跳过并计数（坏行多说明写入被打断，本身也是信号）"""
    rows: List[Dict[str, Any]] = []
    bad = 0
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                bad += 1
                continue
            if isinstance(obj, dict):
                rows.append(obj)
            else:
                bad += 1
    return rows, bad


def summarize(records: Iterable[Dict[str, Any]], *, now: Optional[datetime] = None,
              pending_threshold_s: float = 900.0, top: int = 10) -> Dict[str, Any]:
    stamp = now or datetime.now()
    rows = list(records)
    by_state: Dict[str, int] = {}
    by_tool: Dict[str, int] = {}
    waits: List[int] = []
    pending: List[Dict[str, Any]] = []
    for row in rows:
        state = str(row.get("state") or "?")
        by_state[state] = by_state.get(state, 0) + 1
        tool = str(row.get("object_id") or "?")
        by_tool[tool] = by_tool.get(tool, 0) + 1
        waited = wait_seconds(str(row.get("decision_reason") or ""))
        if waited is not None:
            waits.append(waited)
        if state not in TERMINAL_STATES:
            created = parse_ts(row.get("created_at"))
            age = (stamp - created).total_seconds() if created else None
            pending.append({
                "record_id": str(row.get("record_id") or ""),
                "tool": tool,
                "level": str(row.get("level") or ""),
                "risk": str((row.get("payload") or {}).get("risk") or ""),
                "state": state,
                "created_at": str(row.get("created_at") or ""),
                "age_s": (round(age, 1) if age is not None else None),
            })
    pending.sort(key=lambda x: (x["age_s"] is None, -(x["age_s"] or 0)))
    overdue = [p for p in pending
               if p["age_s"] is not None and p["age_s"] > pending_threshold_s]
    return {
        "total": len(rows),
        "by_state": dict(sorted(by_state.items(), key=lambda kv: -kv[1])),
        "by_tool": dict(sorted(by_tool.items(), key=lambda kv: -kv[1])[:top]),
        "timeout_denies": len(waits),
        "timeout_max_wait_s": (max(waits) if waits else 0),
        "timeout_avg_wait_s": (round(sum(waits) / len(waits), 1) if waits else 0.0),
        "pending_count": len(pending),
        "pending": pending[:top],
        "pending_threshold_s": float(pending_threshold_s),
        "overdue_pending_count": len(overdue),
        "overdue_pending": overdue[:top],
    }


def render(summary: Dict[str, Any]) -> str:
    lines = [
        "审批账本巡检（只读）",
        "  总记录：%d　状态分布：%s" % (
            summary["total"],
            "，".join("%s=%d" % kv for kv in summary["by_state"].items()) or "（空）"),
        "  历史超时否决：%d 条；最长待办 %ds，平均 %.1fs" % (
            summary["timeout_denies"], summary["timeout_max_wait_s"],
            summary["timeout_avg_wait_s"]),
        "  当前未决待办：%d 条（阈值 %.0fs / 逾期 %d 条）" % (
            summary["pending_count"], summary["pending_threshold_s"],
            summary["overdue_pending_count"]),
    ]
    for item in summary["overdue_pending"]:
        lines.append("    [!] %s | %s | level=%s | risk=%s | 已待 %.0fs | %s"
                     % (item["record_id"], item["tool"], item["level"],
                        item["risk"], item["age_s"] or 0, item["created_at"]))
    if summary["overdue_pending_count"]:
        lines.append("  => 这些待办会**卡住**等它的那次会话（审批是阻塞式）。"
                     "处理：批准/驳回，或把该工具纳入 CP_TOOL_CONFIRM_LEVEL_EXEMPT。")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="审批账本超时巡检（只读）")
    ap.add_argument("--path", default=DEFAULT_PATH, help="审批账本 JSONL 路径")
    ap.add_argument("--pending-threshold-s", type=float, default=900.0,
                    help="待办超过该秒数算逾期（默认 900，与 CP_TOOL_APPROVAL_TTL_SEC 同口径）")
    ap.add_argument("--top", type=int, default=10, help="明细最多列出条数")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    if not os.path.exists(args.path):
        print("账本不存在：%s" % args.path, file=sys.stderr)
        return 3
    try:
        rows, bad = load_records(args.path)
    except OSError as exc:
        print("账本读取失败：%s" % exc, file=sys.stderr)
        return 3
    summary = summarize(rows, pending_threshold_s=args.pending_threshold_s, top=args.top)
    summary["bad_lines"] = bad
    print(json.dumps(summary, ensure_ascii=False, indent=2) if args.json
          else render(summary))
    return 2 if summary["overdue_pending_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
