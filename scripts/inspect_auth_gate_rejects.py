#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""鉴权闸门 401 巡检 —— 把「UI 半瘫」变成可监控（只读）

【它回答什么】app_server 的 [AuthGate] 在 CP_API_AUTH_MODE=enforce_all 下把 GET 也纳入
校验；若前端轮询的**只读**端点不在 CP_API_AUTH_ALLOW 里，就会成片 401。实测一天内：
    GET /api/context/status 2064 次、GET /api/background/tasks 980 次、
    GET /api/skills-mgmt/assess/stream 187 次
表现为面板/状态不刷新（像卡死），而且**没有告警** —— 本脚本把它变成可读数据。

【怎么用】
    python scripts/inspect_auth_gate_rejects.py                     # 默认扫 logs/*.err.log
    python scripts/inspect_auth_gate_rejects.py --files "logs/*.log" --threshold 100 --json
退出码：0 = 没有超过阈值的端点；2 = 有（说明某轮询端点在成片被拒）；3 = 没扫到文件。

【口径】只读日志；只统计 method/path —— 闸门日志行里本就不含任何密钥。
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from typing import Any, Dict, Iterable, List, Optional, Tuple

DEFAULT_GLOB = "logs/*.err.log"
REJECT_MARK = "[AuthGate] 拒绝未授权请求"
SHADOW_MARK = "[AuthGate][shadow] 将拦截未授权请求"


def extract_target(line: str, mark: str) -> Optional[Tuple[str, str]]:
    """从日志行里取 (method, path)；只认 mark 之后用空格分隔的 method=/path= 令牌"""
    idx = line.find(mark)
    if idx < 0:
        return None
    method = ""
    path = ""
    for token in line[idx + len(mark):].split():
        if token.startswith("method="):
            method = token[len("method="):]
        elif token.startswith("path="):
            path = token[len("path="):]
    if not method and not path:
        return None
    return method, path


def scan_lines(lines: Iterable[str]) -> Dict[str, Any]:
    rejects: Dict[str, int] = {}
    shadow: Dict[str, int] = {}
    total = 0
    for line in lines:
        hit = extract_target(line, REJECT_MARK)
        if hit is not None:
            key = "%s %s" % hit
            rejects[key] = rejects.get(key, 0) + 1
            total += 1
            continue
        hit = extract_target(line, SHADOW_MARK)
        if hit is not None:
            key = "%s %s" % hit
            shadow[key] = shadow.get(key, 0) + 1
    return {"rejects": rejects, "shadow": shadow, "total": total}


def scan_files(paths: Iterable[str]) -> Dict[str, Any]:
    agg: Dict[str, Any] = {"rejects": {}, "shadow": {}, "total": 0,
                           "files": [], "read_errors": []}
    for path in paths:
        agg["files"].append(path)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                part = scan_lines(fh)
        except OSError as exc:
            agg["read_errors"].append("%s: %s" % (path, exc))
            continue
        agg["total"] += part["total"]
        for key, cnt in part["rejects"].items():
            agg["rejects"][key] = agg["rejects"].get(key, 0) + cnt
        for key, cnt in part["shadow"].items():
            agg["shadow"][key] = agg["shadow"].get(key, 0) + cnt
    return agg


def summarize(agg: Dict[str, Any], *, top: int = 10,
              threshold: int = 200) -> Dict[str, Any]:
    ranked = sorted(agg["rejects"].items(), key=lambda kv: -kv[1])
    over = [{"path": k, "count": v} for k, v in ranked if v >= threshold]
    # 用 .get：summarize 也要能吃 scan_lines() 的直接产物（它没有 files/read_errors 两键）
    return {
        "files": list(agg.get("files") or ()),
        "read_errors": list(agg.get("read_errors") or ()),
        "total_rejects": agg.get("total", 0),
        "top_rejects": [{"path": k, "count": v} for k, v in ranked[:top]],
        "shadow_by_path": [{"path": k, "count": v} for k, v in
                           sorted(agg["shadow"].items(), key=lambda kv: -kv[1])[:top]],
        "threshold": int(threshold),
        "over_threshold": over,
    }


def render(summary: Dict[str, Any]) -> str:
    lines = [
        "鉴权闸门 401 巡检（只读）",
        "  扫描文件 %d 个；[AuthGate] 拒绝合计 %d 次"
        % (len(summary["files"]), summary["total_rejects"]),
        "  被拒最多的端点：",
    ]
    for item in summary["top_rejects"]:
        lines.append("    %6d  %s" % (item["count"], item["path"]))
    if summary["shadow_by_path"]:
        lines.append("  （shadow 档预演：%d 类端点会被拦）"
                     % len(summary["shadow_by_path"]))
    if summary["over_threshold"]:
        lines.append("  => 有 %d 个端点超过阈值 %d；这些多半是**只读 UI 轮询**，"
                     % (len(summary["over_threshold"]), summary["threshold"]))
        lines.append("     建议加入 CP_API_AUTH_ALLOW（而不是把 enforce_all 降回 enforce）。")
    if summary["read_errors"]:
        lines.append("  读取失败 %d 个文件" % len(summary["read_errors"]))
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="鉴权闸门 401 巡检（只读）")
    ap.add_argument("--files", default=DEFAULT_GLOB,
                    help="日志 glob（默认 logs/*.err.log）")
    ap.add_argument("--threshold", type=int, default=200,
                    help="单端点被拒次数告警阈值")
    ap.add_argument("--top", type=int, default=10, help="最多列出条数")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    paths = sorted(glob.glob(args.files))
    if not paths:
        print("没有匹配到日志文件：%s" % args.files, file=sys.stderr)
        return 3
    summary = summarize(scan_files(paths), top=args.top, threshold=args.threshold)
    print(json.dumps(summary, ensure_ascii=False, indent=2) if args.json
          else render(summary))
    return 2 if summary["over_threshold"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
