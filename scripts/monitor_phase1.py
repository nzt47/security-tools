#!/usr/bin/env python3
"""Phase 1 分块回归监控脚本 — 实时抓取 pytest_chunks/*.log 汇总行并报警

背景（2026-08-14 实测）：run_full_pytest.py 分块回归中，D 类慢测试的 Timeout 曾导致
pytest-timeout 调用 os._exit(1) 强制杀进程，chunk 日志**无汇总行**（rc=1 无 "passed"）。
因此"看 rc"不可靠，必须逐 chunk 核验汇总行。

本脚本轮询 chunk 日志：
  - 实时抓取最终汇总行（"=+ N passed [, M failed/error] ..."）
  - failed/error 计数 > 0 → [ALERT]（报警）
  - 无汇总行 + 父进程已退出 → [ALERT]（崩溃，被强杀）
  - 文件 mtime 长期停滞 + 父进程存活 → [WARN]（疑似卡死，thread 超时无法中断）

用法：
    python scripts/monitor_phase1.py [--log-dir pytest_chunks] [--chunks 4]
        [--poll 15] [--timeout 7200] [--parent-pid <pid>] [--stall-warn 180]

退出码：
    0 = 全部 chunk 完整执行且无 failed/error
    1 = 存在 failed/error、崩溃（无汇总）、卡死或监控总超时
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

# 兼容 "== 3662 passed, 39 skipped, 7 xfailed, 4 xpassed in 595.77s ==" 等 pytest 汇总格式
SUMMARY_RE = re.compile(
    r"=\s*(?P<passed>\d+) passed(?:, (?P<rest>[\d, \w]+?))?(?: in (?P<secs>[\d.]+)s)?\s*="
)
NO_TESTS_RE = re.compile(r"no tests ran")


def _is_alive_windows(pid: int) -> bool:
    """Windows: tasklist 查询 PID 是否存活（os.kill(pid, 0) 在 Windows 不可靠）"""
    try:
        r = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=10,
        )
        return str(pid) in r.stdout
    except (OSError, subprocess.SubprocessError):
        return True  # 查询失败时保守视为存活


def _is_alive(pid: int) -> bool:
    if sys.platform == "win32":
        return _is_alive_windows(pid)
    try:
        import os
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def parse_summary(text: str) -> dict | None:
    """提取日志最后一行汇总信息；无汇总返回 None"""
    for line in reversed(text.splitlines()):
        s = line.strip()
        if NO_TESTS_RE.search(s):
            return {"passed": 0, "failed": 0, "error": 0, "secs": "", "line": s}
        if "passed" in s and " in " in s:
            m = SUMMARY_RE.search(s)
            if m:
                rest = m.group("rest") or ""
                failed = 1 if re.search(r"\bfailed\b", rest) else 0
                error = 1 if re.search(r"\berror\b", rest) else 0
                return {
                    "passed": int(m.group("passed")),
                    "failed": failed, "error": error,
                    "secs": m.group("secs") or "",
                    "line": s,
                }
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--log-dir", default="pytest_chunks", type=Path)
    ap.add_argument("--chunks", type=int, default=4)
    ap.add_argument("--poll", type=int, default=15, help="轮询间隔秒数")
    ap.add_argument("--timeout", type=int, default=7200, help="监控总超时秒数")
    ap.add_argument("--parent-pid", type=int, default=None,
                    help="run_full_pytest.py 主进程 PID；退出视为 run 结束")
    ap.add_argument("--stall-warn", type=int, default=180,
                    help="chunk 日志 mtime 停滞秒数阈值（疑似卡死警告）")
    args = ap.parse_args()

    logdir: Path = args.log_dir
    expected = [logdir / f"chunk_{i}.log" for i in range(args.chunks)]
    t0 = time.monotonic()
    last_report = {}      # chunk index -> {summary, mtime}
    alerted = set()       # 已报警的 chunk（避免重复）
    parent_pid = args.parent_pid

    print(f"[monitor] 监控 {logdir}/chunk_*.log（{args.chunks} 块）"
          f" 轮询 {args.poll}s 总超时 {args.timeout}s"
          + (f" 父进程 {parent_pid}" if parent_pid else ""), flush=True)

    while True:
        elapsed = time.monotonic() - t0
        if elapsed > args.timeout:
            print(f"[ALERT] 监控总超时（{args.timeout:.0f}s），存在未完成 chunk", flush=True)
            return 1

        completed = 0
        for i, path in enumerate(expected):
            info = last_report.get(i, {})
            if not path.exists():
                info["state"] = "absent"
                last_report[i] = info
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            text = ""
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                pass
            summary = parse_summary(text)
            prev = info.get("summary")
            if summary and (prev is None or summary["line"] != prev.get("line")):
                flag = "ALERT" if (summary["failed"] or summary["error"]) else "OK"
                print(f"[{flag}] chunk_{i} 汇总: {summary['line']}", flush=True)
            info["summary"] = summary
            info["mtime"] = mtime
            info["state"] = "has_summary" if summary else "running"
            last_report[i] = info

            if summary:
                completed += 1
                if (summary["failed"] or summary["error"]) and i not in alerted:
                    alerted.add(i)
                    print(f"[ALERT] chunk_{i} 存在失败/错误 → 人工介入", flush=True)
                    sys.stdout.write("\a")
                    sys.stdout.flush()
            else:
                # 无汇总：卡死检测
                stall = time.time() - mtime
                if parent_pid and not _is_alive(parent_pid):
                    if i not in alerted:
                        alerted.add(i)
                        print(f"[ALERT] chunk_{i} 无汇总行且父进程已退出 → 疑似被强杀（崩溃）",
                              flush=True)
                        sys.stdout.write("\a")
                        sys.stdout.flush()
                elif stall > args.stall_warn and i not in alerted:
                    # 仅警告一次，不重复刷屏
                    alerted.add(i)
                    print(f"[WARN] chunk_{i} 日志 mtime 停滞 {stall:.0f}s 无汇总"
                          f" → 疑似卡死（thread 超时无法中断）", flush=True)

        # 父进程退出判定：所有预期 chunk 都有汇总 或 父进程已结束且无新增
        parent_done = (parent_pid is not None) and (not _is_alive(parent_pid))
        # 全 chunk 已定性（有汇总 或 已报警卡死/崩溃）→ 立即收尾，避免空转等总超时
        all_settled = (
            len(last_report) == args.chunks
            and all(
                v.get("state") == "has_summary" or i in alerted
                for i, v in last_report.items()
            )
        )
        if completed == args.chunks or (parent_done and completed > 0) or all_settled:
            print(f"[monitor] 运行结束：{completed}/{args.chunks} chunk 有汇总"
                  f"（耗时 {time.monotonic() - t0:.0f}s）", flush=True)
            any_fail = any(
                (v.get("summary") and (v["summary"]["failed"] or v["summary"]["error"]))
                or v.get("state") == "absent"
                for v in last_report.values()
            )
            missing = [i for i, v in last_report.items() if not v.get("summary")]
            if any_fail or missing:
                print(f"[ALERT] 最终判定 FAIL（失败/错误/无汇总: {missing}）", flush=True)
                return 1
            print("[monitor] 最终判定 PASS：全部 chunk 完整且无失败", flush=True)
            return 0

        time.sleep(args.poll)


if __name__ == "__main__":
    sys.exit(main())
