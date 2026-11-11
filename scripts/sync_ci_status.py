# -*- coding: utf-8 -*-
"""把 GitHub Actions 最近几次运行结果同步成 `data/ci/status.json`（报警器的事件源之一）。

【本脚本现在是薄 CLI 包装】真正的同步实现收口在
`agent/monitoring/ci_status.py`，因为同一个动作有两个调用方：
① 人工/运维跑本脚本（带 `--check` / 退出码）；② 调度器每 30 分钟跑一次
（`register_ci_sync_scheduler`）。两份实现会漂移（2026-11-12 收口），故只留一份。

【为什么是"同步脚本 + 读文件"，而不是报警器自己去跑 gh】
采集线程每 60s 一轮。若它直接起 `gh` 子进程：① 每轮都要等网络（几秒），报警器会变成新的
不稳定源；② 无 gh / 未登录的环境里每轮都要走一次失败路径。故把"取数"与"判读"分开：
同步侧用 gh 取最近 N 次运行写成状态文件，报警器（`agent/alarms/collector.read_ci_status`）
只读文件 —— 文件缺失或过期（默认 2h）就如实按"数据源不可用"处理（不报警、不判恢复）。

用法：
    python scripts/sync_ci_status.py              # 同步（默认最近 10 次运行）
    python scripts/sync_ci_status.py --limit 20
    python scripts/sync_ci_status.py --check      # 只打印将要写入的内容，不落盘

输出（data/ci/status.json，运行期状态，不入库）：
    {"synced_at": "...", "source": "gh run list", "repo": "owner/name", "runs": [
        {"workflow": "云枢系统测试流程", "name": "...", "status": "completed",
         "conclusion": "success", "branch": "master", "created_at": "...",
         "database_id": 123, "url": "...", "title": "..."}]}
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # 允许从任意 cwd 运行（调度器/手工都用同一个入口）
    sys.path.insert(0, str(ROOT))

from agent.monitoring.ci_status import (  # noqa: E402  必须在 sys.path 调整之后
    DEFAULT_LIMIT,
    STALE_MAX_AGE_SECONDS,  # noqa: F401  供下游/排查引用（口径同 collector）
    OUT_REL_PATH,
    build_payload,
    read_status,
    sync_once,
)
from agent.monitoring.ci_status import _run_gh, _repo_slug  # noqa: E402

OUT_PATH = ROOT / OUT_REL_PATH
FAILED_CONCLUSIONS = ("failure", "timed_out", "startup_failure")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="同步 GitHub Actions 最近运行结果（报警器事件源）")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help="取最近多少次运行（默认 %d）" % DEFAULT_LIMIT)
    parser.add_argument("--check", action="store_true", help="只打印，不落盘")
    args = parser.parse_args(argv)

    if args.check:
        runs, reason = _run_gh(max(1, int(args.limit)))
        if runs is None:
            print("[sync-ci] " + reason, file=sys.stderr)
            return 2
        payload: Dict[str, Any] = build_payload(runs, repo=_repo_slug())
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    result = sync_once(limit=max(1, int(args.limit)))
    if result.get("status") != "written":
        print("[sync-ci] " + str(result.get("reason") or "同步失败"), file=sys.stderr)
        return 2
    payload = read_status() or {}
    runs = payload.get("runs") or []
    failed = [r for r in runs if r.get("conclusion") in FAILED_CONCLUSIONS]
    print("[sync-ci] 已写入 %s（%d 次运行，%d 次失败结论）"
          % (OUT_PATH.relative_to(ROOT), len(runs), len(failed)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
