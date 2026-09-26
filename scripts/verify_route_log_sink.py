#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B2 · 路由决策事件落盘 sink 只读校验脚本

用途：检查 app_server 启动后，路由打点是否真的落进了持久化 sink
（data/logs/<date>.jsonl），并给出退出码，供 CI / 运维巡检直接判定。

【只读承诺】本脚本只以 'r' 打开文件（且只读目标 jsonl 与目录清单），
不创建、不修改、不删除任何文件；不改环境、不起服务。可用文件 mtime
前后对比复核（见 docs/audit_skill_governance/B2.md 的验收证据）。

退出码：
  0 = 通过（目标文件存在，且规定的事件类型都出现）
  1 = 不通过（文件存在但缺少规定事件 → sink 没生效 / 服务没跑过真实请求）
  2 = 目标文件不存在或不可读（今天还没有任何事件落盘）

判定规则（规定的事件类型）：
  A. 最终路由决策事件 orchestrator.process.route_decision >= N（默认 1）
     —— 来自 agent/orchestrator/routing_observability.py:269-299 emit_route_decision
  B. 路由层/埋点事件（action 前缀 orchestrator.layer. 或 orchestrator.intent_layer.）>= 1
     —— 来自 routing_observability.py:222-266 log_layer_result /
        orchestrator.py:368 _record_intent_layer
  C.（非必需，仅报告）工具漏斗检索事件 tool_retrieval、流量汇总 orchestrator.traffic.summary

用法：
    python scripts/verify_route_log_sink.py
    python scripts/verify_route_log_sink.py --date 2026-09-25 --min-route-decisions 2 --show 3
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_DIR = os.path.join(_REPO_ROOT, "data", "logs")

_EV_ROUTE_DECISION = "orchestrator.process.route_decision"
_EV_TRAFFIC_SUMMARY = "orchestrator.traffic.summary"
_EV_TOOL_RETRIEVAL = "tool_retrieval"
_PREFIX_LAYER = "orchestrator.layer."
_PREFIX_INTENT = "orchestrator.intent_layer."


def _target_path(args) -> str:
    if args.path:
        return os.path.abspath(args.path)
    date_str = args.date or datetime.datetime.now().strftime("%Y-%m-%d")
    return os.path.join(os.path.abspath(args.dir), date_str + ".jsonl")


def _iter_events(path: str):
    """逐行读取目标 JSONL（只读）；产出 (行号, 原始行, 解析后的 dict 或 None)"""
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception:
                yield lineno, raw.rstrip("\n"), None
                continue
            if not isinstance(entry, dict):
                yield lineno, raw.rstrip("\n"), None
                continue
            yield lineno, raw.rstrip("\n"), entry


def _event_of(entry: dict) -> str:
    labels = entry.get("labels")
    if isinstance(labels, dict) and isinstance(labels.get("event"), str):
        return labels["event"]
    msg = entry.get("message")
    if isinstance(msg, str):
        try:
            body = json.loads(msg)
        except Exception:
            return ""
        if isinstance(body, dict) and isinstance(body.get("action"), str):
            return body["action"]
    return ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B2 路由决策事件落盘 sink 只读校验")
    ap.add_argument("--path", default="", help="直接指定 jsonl 路径（优先于 --dir/--date）")
    ap.add_argument("--dir", default=_DEFAULT_DIR, help="落盘目录（默认 <repo>/data/logs）")
    ap.add_argument("--date", default="", help="日期 YYYY-MM-DD（默认今天，本地时区）")
    ap.add_argument("--min-route-decisions", type=int, default=1,
                    help="要求的 route_decision 事件条数下限（默认 1）")
    ap.add_argument("--require-tool-retrieval", action="store_true",
                    help="额外要求出现 tool_retrieval 事件（默认只报告不判定）")
    ap.add_argument("--show", type=int, default=0, help="额外打印前 N 条命中的原始行")
    args = ap.parse_args(argv)

    path = _target_path(args)
    print("[B2] 校验路由事件 sink（只读）")
    print("     目标文件: %s" % path)

    if not os.path.isfile(path):
        print("[FAIL] 目标文件不存在：今天还没有任何路由事件落盘。")
        print("       排查：① 服务是否以 python app_server.py 启动（sink 在启动期装配）")
        print("             ② CP_ROUTE_EVENT_SINK_ENABLED 是否为 0（关闭即回到只进 stderr）")
        print("             ③ 是否有真实对话请求产生过路由事件")
        return 2

    size = os.path.getsize(path)
    counts = {
        _EV_ROUTE_DECISION: 0,
        "layer_events": 0,
        "intent_layer_events": 0,
        _EV_TRAFFIC_SUMMARY: 0,
        _EV_TOOL_RETRIEVAL: 0,
    }
    other_events = {}
    malformed = 0
    total_lines = 0
    samples = []
    timestamps = []

    for lineno, raw, entry in _iter_events(path):
        total_lines += 1
        if entry is None:
            malformed += 1
            continue
        ts = entry.get("timestamp")
        if isinstance(ts, (int, float)):
            timestamps.append(ts)
        event = _event_of(entry)
        if not event:
            other_events["<无 event 字段>"] = other_events.get("<无 event 字段>", 0) + 1
            continue
        if event == _EV_ROUTE_DECISION:
            counts[_EV_ROUTE_DECISION] += 1
        elif event.startswith(_PREFIX_LAYER):
            counts["layer_events"] += 1
        elif event.startswith(_PREFIX_INTENT):
            counts["intent_layer_events"] += 1
        elif event == _EV_TRAFFIC_SUMMARY:
            counts[_EV_TRAFFIC_SUMMARY] += 1
        elif event == _EV_TOOL_RETRIEVAL:
            counts[_EV_TOOL_RETRIEVAL] += 1
        else:
            other_events[event] = other_events.get(event, 0) + 1
            continue
        if len(samples) < max(args.show, 0):
            samples.append((lineno, raw))

    layer_total = counts["layer_events"] + counts["intent_layer_events"]

    print("     文件大小: %d 字节 / 行数: %d（空行计 0，坏行 %d）"
          % (size, total_lines, malformed))
    if timestamps:
        fmt = lambda t: datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")
        print("     时间范围: %s .. %s" % (fmt(min(timestamps)), fmt(max(timestamps))))
    print("     事件计数:")
    print("       %-42s %d  (判定项 A，要求 >= %d)"
          % (_EV_ROUTE_DECISION, counts[_EV_ROUTE_DECISION], args.min_route_decisions))
    print("       %-42s %d  (判定项 B: layer=%d + intent_layer=%d)"
          % ("orchestrator.layer.* + intent_layer.*", layer_total,
             counts["layer_events"], counts["intent_layer_events"]))
    print("       %-42s %d  (仅报告)" % (_EV_TOOL_RETRIEVAL, counts[_EV_TOOL_RETRIEVAL]))
    print("       %-42s %d  (仅报告)" % (_EV_TRAFFIC_SUMMARY, counts[_EV_TRAFFIC_SUMMARY]))
    for name, cnt in sorted(other_events.items()):
        print("       %-42s %d  (非路由事件)" % (name, cnt))

    for lineno, raw in samples:
        print("     样例 L%d: %s" % (lineno, raw if len(raw) <= 600 else raw[:600] + "..."))

    failures = []
    if counts[_EV_ROUTE_DECISION] < args.min_route_decisions:
        failures.append("route_decision=%d < %d" % (counts[_EV_ROUTE_DECISION],
                                                    args.min_route_decisions))
    if layer_total < 1:
        failures.append("layer/intent_layer 事件为 0")
    if args.require_tool_retrieval and counts[_EV_TOOL_RETRIEVAL] < 1:
        failures.append("tool_retrieval 事件为 0（--require-tool-retrieval）")

    if failures:
        print("[FAIL] %s" % "；".join(failures))
        return 1
    print("[OK] 路由事件已落盘：%s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
