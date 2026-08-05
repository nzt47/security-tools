#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ci_thread_monitor.py — CI 线程/进程资源监控（采样 + 峰值报告）

背景（2026-08-05）: 云枢测试 Shard 3 (py3.12) 在 pytest-xdist(-n 2) + pytest-timeout
thread 方法下触发 "RuntimeError: can't start new thread" INTERNALERROR。
根因: xdist 下 --timeout-method=signal 自动降级为 thread，每测试一个 Timer 线程，
叠加 error_handler 重试测试自身线程，瞬时线程/进程数逼近容器 pids 限制。

本脚本在 CI 后台采样线程/进程数，pytest 结束后用 --report 输出峰值，
用于确认峰值是否逼近 pids.max（/sys/fs/cgroup/pids.max），为后续 -n 调优提供数据。

【简易】单文件零第三方依赖，仅标准库（json/threading/subprocess/time）。
【不易】采样只读不改（不注入代码、不动测试逻辑）；采集失败自动降级为 0 不抛异常。

用法（CI 集成，bash）:
    python scripts/ci_thread_monitor.py --output monitor.log --interval 2 &
    MONITOR_PID=$!
    pytest ... || true
    kill $MONITOR_PID 2>/dev/null || true
    wait $MONITOR_PID 2>/dev/null || true
    python scripts/ci_thread_monitor.py --report monitor.log || true

用法（手动）:
    python scripts/ci_thread_monitor.py --duration 60 --interval 2 --output m.log
    python scripts/ci_thread_monitor.py --report m.log
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

MAX_INT = 2**31 - 1


def _cmd_stdout(args: list[str]) -> str | None:
    """执行命令取 stdout 首行；失败/超时返回 None（采样降级，不中断监控）。"""
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=5, check=False)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def count_threads() -> int:
    """全局线程数：Linux 用 ps -eLf 计数；其他平台用当前进程 threading.active_count()。"""
    out = _cmd_stdout(["ps", "-eLf", "--no-headers"])
    if out is not None:
        return len(out.splitlines())
    # Windows/降级：统计当前进程线程数（仅参考）
    return threading.active_count()


def count_procs() -> int:
    """全局进程数：Linux 用 ps -e 计数；其他平台返回 0。"""
    out = _cmd_stdout(["ps", "-e", "--no-headers"])
    return len(out.splitlines()) if out is not None else 0


def read_pids_max() -> str:
    """容器 pids 限制（Linux cgroup v2），不可用时返回 'N/A'。"""
    for path in ("/sys/fs/cgroup/pids.max", "/sys/fs/cgroup/pids/pids.max"):
        try:
            with open(path, encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            continue
    return "N/A"


def sample() -> dict:
    """单次采样。采样失败字段降级为 0（【不易】不因监控中断 pytest）。"""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "ts": ts,
        "threads": count_threads(),
        "procs": count_procs(),
        "cpu": os.cpu_count() or 0,
        "pids_max": read_pids_max(),
    }


def run_monitor(output: str, interval: float, duration: float) -> int:
    """循环采样写入 output（JSON Lines）。duration<=0 表示无限（配合 CI 后台 kill）。"""
    start = time.monotonic()
    with open(output, "a", encoding="utf-8") as f:
        while True:
            try:
                f.write(json.dumps(sample(), ensure_ascii=False) + "\n")
                f.flush()
            except OSError:
                return 1
            if duration > 0 and time.monotonic() - start >= duration:
                break
            time.sleep(max(interval, 0.1))
    return 0


def report(log_path: str) -> int:
    """读取 JSON Lines 采样，输出线程/进程峰值摘要。"""
    rows: list[dict] = []
    try:
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except (OSError, json.JSONDecodeError) as e:
        print(f"[ERROR] 读取采样日志失败: {e}", file=sys.stderr)
        return 1
    if not rows:
        print("[WARN] 采样日志为空（监控可能未启动或被立即 kill）")
        return 0

    threads = [r["threads"] for r in rows]
    procs = [r["procs"] for r in rows]
    peak_t = max(threads)
    peak_p = max(procs)
    pids_max = rows[-1].get("pids_max", "N/A")

    print("=== CI 线程/进程资源监控报告 ===")
    print(f"采样点数: {len(rows)}  区间: {rows[0]['ts']} → {rows[-1]['ts']}")
    print(f"线程数: min={min(threads)} avg={sum(threads)/len(threads):.0f} max={peak_t}")
    print(f"进程数: min={min(procs)} avg={sum(procs)/len(procs):.0f} max={peak_p}")
    print(f"容器 pids.max: {pids_max}  CPU核数: {rows[-1].get('cpu', 0)}")
    peak_row = max(rows, key=lambda r: r["threads"])
    print(f"线程峰值时间点: {peak_row['ts']}（threads={peak_row['threads']} procs={peak_row['procs']}）")
    if pids_max.isdigit():
        ratio = peak_t / int(pids_max)
        print(f"峰值占用率: {ratio:.1%}（{'⚠️ 逼近限制' if ratio > 0.8 else '✅ 余量充足'}）")
    print("==================================")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="CI 线程/进程资源监控（采样 + 峰值报告）")
    ap.add_argument("--output", default="thread-monitor.log", help="采样输出文件（JSON Lines）")
    ap.add_argument("--interval", type=float, default=2.0, help="采样间隔秒（默认 2）")
    ap.add_argument("--duration", type=float, default=0, help="采样时长秒，<=0 无限（默认 0）")
    ap.add_argument("--report", metavar="LOG", help="报告模式：读取采样日志输出峰值摘要")
    args = ap.parse_args()

    if args.report:
        return report(args.report)
    if args.duration > 0:
        print(f"监控启动: 采样 {args.duration}s / 间隔 {args.interval}s → {args.output}", flush=True)
    else:
        print(f"监控启动: 无限采样 / 间隔 {args.interval}s → {args.output}（CI 用 kill 停止）", flush=True)
    return run_monitor(args.output, args.interval, args.duration)


if __name__ == "__main__":
    sys.exit(main())
