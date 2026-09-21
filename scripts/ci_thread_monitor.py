#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ci_thread_monitor.py — CI 线程/进程资源监控（采样 + 峰值报告）

背景（2026-08-05）: 云枢测试 Shard 3 (py3.12) 在 pytest-xdist(-n 2) + pytest-timeout
thread 方法下触发 "RuntimeError: can't start new thread" INTERNALERROR。
根因: xdist 下 --timeout-method=signal 自动降级为 thread，每测试一个 Timer 线程，
叠加 error_handler 重试测试自身线程，瞬时线程/进程数逼近容器 pids 限制。

本脚本在 CI 后台采样线程/进程数，pytest 结束后用 --report 输出峰值，
用于确认峰值是否逼近 pids.max（/sys/fs/cgroup/pids.max），为后续 -n 调优提供数据。

【增量·2026-09-21 可观测性补强（只增不改，把"内存/线程压力"从推断变为观测）】
  1) --snapshot LABEL：一次性打印 nproc / cgroup memory.max / memory.current /
     pids.max（文件不存在或非 Linux 一律降级 N/A），用于跑测前后各打一次；
  2) 采样新增 mem_max 与 proc_samples：逐个进程读 /proc/<pid>/status 的
     VmHWM（内核维护的 RSS 峰值 ≡ "峰值 RSS"）与 Threads（线程数）；
     按 ppid 祖先链把 xdist worker 与主 pytest 进程区分开；
  3) 监控循环检测到被监控进程消失（= 该 worker 结束）时，立即打印其
     峰值 RSS / 峰值线程数 ⇒ 满足"每个 worker 结束前打印峰值 RSS 与线程数"；
  4) --report 追加"容器 memory.max"与"每 worker 峰值 RSS / 线程数"汇总表。
  风险控制：全部为标准库只读采样；任何采集失败退化为空列表/N/A，不抛异常，
  不改变既有退出码语义（--report 仍仅在"采样日志读取失败"时返回 1）。
  本脚本不注入代码、不改测试选择/超时/分片/失败判定。

【简易】单文件零第三方依赖，仅标准库（json/os/subprocess/sys/threading/time）。
【不易】采样只读不改（不注入代码、不动测试逻辑）；采集失败自动降级为 0 不抛异常。

用法（CI 集成，bash）:
    python scripts/ci_thread_monitor.py --snapshot "shard before"
    python scripts/ci_thread_monitor.py --output monitor.log --interval 2 &
    MONITOR_PID=$!
    pytest ... || true
    kill $MONITOR_PID 2>/dev/null || true
    wait $MONITOR_PID 2>/dev/null || true
    python scripts/ci_thread_monitor.py --report monitor.log || true
    python scripts/ci_thread_monitor.py --snapshot "shard after"

用法（手动）:
    python scripts/ci_thread_monitor.py --duration 60 --interval 2 --output m.log
    python scripts/ci_thread_monitor.py --report m.log
    python scripts/ci_thread_monitor.py --snapshot manual
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

# 祖先链回溯深度（xdist: 主进程 → worker 一般 <= 2 跳，留足余量）
_MAX_ANCESTOR_HOPS = 8


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


def read_mem_max() -> str:
    """容器内存上限（cgroup v2 memory.max / v1 memory.limit_in_bytes），不可用返回 'N/A'。"""
    for path in (
        "/sys/fs/cgroup/memory.max",
        "/sys/fs/cgroup/memory/memory.limit_in_bytes",
    ):
        try:
            with open(path, encoding="utf-8") as f:
                val = f.read().strip()
            if val:
                return val
        except OSError:
            continue
    return "N/A"


def read_mem_current() -> str:
    """容器当前内存占用（cgroup v2 memory.current / v1 usage_in_bytes），不可用返回 'N/A'。"""
    for path in (
        "/sys/fs/cgroup/memory.current",
        "/sys/fs/cgroup/memory/memory.usage_in_bytes",
    ):
        try:
            with open(path, encoding="utf-8") as f:
                val = f.read().strip()
            if val:
                return val
        except OSError:
            continue
    return "N/A"


def read_nproc() -> str:
    """CPU 核数：优先 nproc（与 CI 文档口径一致），命令缺失时回退 os.cpu_count()。"""
    out = _cmd_stdout(["nproc"])
    if out is not None:
        return out
    return str(os.cpu_count() or 0)


# ── 逐进程采样（新增：峰值 RSS / 线程数）────────────────────────────────


def _read_text(path: str) -> str | None:
    """读文本文件；任何 OSError（含非 Linux 无 /proc）返回 None。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _proc_status(pid: int) -> dict[str, str] | None:
    """读 /proc/<pid>/status 为 {字段: 值}；读不到（进程已退出/非 Linux）返回 None。"""
    txt = _read_text(f"/proc/{pid}/status")
    if not txt:
        return None
    info: dict[str, str] = {}
    for line in txt.splitlines():
        key, sep, val = line.partition(":")
        if sep:
            info[key.strip()] = val.strip()
    return info or None


def _proc_cmdline(pid: int) -> str:
    """读 /proc/<pid>/cmdline（NUL 分隔）为单行字符串；读不到返回 ''。"""
    raw = _read_text(f"/proc/{pid}/cmdline") or ""
    return raw.replace("\x00", " ").strip()


def _to_int_kb(value: str | None) -> int:
    """'/proc/<pid>/status' 里的 'VmHWM: 1234 kB' → 1234（KB）；异常返回 0。"""
    if not value:
        return 0
    try:
        return int(str(value).split()[0])
    except (IndexError, ValueError):
        return 0


def _to_int(value: str | None) -> int:
    try:
        return int(str(value).split()[0])
    except (IndexError, ValueError):
        return 0


def _classify(pid: int, cmd: str, name: str, ppid_map: dict[int, int], runner_pids: set[int]) -> str:
    """区分主 pytest 进程 / xdist worker / 其它 python 进程（只读推断，失败退化为 '其它'）。"""
    if "pytest" in cmd:
        return "pytest-main"
    cur = pid
    for _ in range(_MAX_ANCESTOR_HOPS):
        cur = ppid_map.get(cur, 0)
        if not cur or cur <= 1:
            break
        if cur in runner_pids:
            return "worker"
    if name.startswith(("python", "pypy", "pytest")) or "python" in cmd:
        return "python-other"
    return "other"


def sample_processes(exclude_pids: set[int] | None = None) -> list[dict]:
    """采样"相关进程"的峰值 RSS(VmHWM) 与线程数(Threads)。

    只读 /proc（Linux）；非 Linux / 无权限 / 进程中途退出一律跳过，返回 []，
    绝不抛异常、绝不改变被测进程状态。
    """
    exclude = set(exclude_pids or ())
    exclude.add(os.getpid())  # 排除监控自身
    try:
        names = [n for n in os.listdir("/proc") if n.isdigit()]
    except OSError:
        return []  # 非 Linux（Windows/macOS 无 /proc）→ 安全跳过

    snap: dict[int, dict] = {}
    ppid_map: dict[int, int] = {}
    for name in names:
        pid = int(name)
        if pid in exclude:
            continue
        st = _proc_status(pid)
        if not st:
            continue
        ppid_map[pid] = _to_int(st.get("PPid"))
        snap[pid] = {
            "name": st.get("Name", ""),
            "vmhwm_kb": _to_int_kb(st.get("VmHWM")),   # 峰值 RSS（内核维护，单调不减）
            "vmrss_kb": _to_int_kb(st.get("VmRSS")),   # 当前 RSS（用于并发合计）
            "threads": _to_int(st.get("Threads")),
        }

    cmds = {pid: _proc_cmdline(pid) for pid in snap}
    runner_pids = {pid for pid, cmd in cmds.items() if "pytest" in cmd}

    out: list[dict] = []
    for pid, info in snap.items():
        cmd = cmds.get(pid, "")
        name = info["name"]
        if not (name.startswith(("python", "pypy", "pytest")) or "pytest" in cmd):
            continue  # 只关心 python/pytest 相关进程，避免采样噪音
        out.append(
            {
                "pid": pid,
                "kind": _classify(pid, cmd, name, ppid_map, runner_pids),
                "rss_kb": info["vmhwm_kb"],
                "rss_now_kb": info["vmrss_kb"],
                "threads": info["threads"],
                "cmd": cmd[:120],
            }
        )
    return out


def sample() -> dict:
    """单次采样。采样失败字段降级为 0（【不易】不因监控中断 pytest）。"""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        proc_samples = sample_processes()
    except Exception:  # noqa: BLE001 - 观测失败绝不影响被测流程
        proc_samples = []
    return {
        "ts": ts,
        "threads": count_threads(),
        "procs": count_procs(),
        "cpu": os.cpu_count() or 0,
        "pids_max": read_pids_max(),
        "mem_max": read_mem_max(),          # 新增：容器内存上限
        "proc_samples": proc_samples,        # 新增：逐进程峰值 RSS / 线程数
    }


def _track_and_report_exits(row: dict, tracked: dict[int, dict]) -> None:
    """更新被监控进程峰值；对已消失（结束）的进程立即打印峰值 RSS / 线程数。

    仅打印，任何异常吞掉（观测不得影响被测流程）。
    """
    try:
        current = row.get("proc_samples") or []
        now_pids: set[int] = set()
        for p in current:
            pid = int(p.get("pid"))
            now_pids.add(pid)
            rec = tracked.setdefault(
                pid,
                {"kind": p.get("kind", "?"), "cmd": p.get("cmd", ""), "rss_kb": 0, "threads": 0},
            )
            rec["rss_kb"] = max(rec["rss_kb"], int(p.get("rss_kb") or 0))
            rec["threads"] = max(rec["threads"], int(p.get("threads") or 0))
        for pid in [p for p in tracked if p not in now_pids]:
            rec = tracked.pop(pid)  # 该进程已结束（worker 退出）→ 打印其峰值
            print(
                f"[进程结束] pid={pid} kind={rec['kind']} "
                f"峰值RSS={rec['rss_kb'] / 1024:.1f}MB 峰值线程={rec['threads']} "
                f"cmd={rec['cmd'][:80]}",
                flush=True,
            )
    except Exception:  # noqa: BLE001
        return


def run_monitor(output: str, interval: float, duration: float) -> int:
    """循环采样写入 output（JSON Lines）。duration<=0 表示无限（配合 CI 后台 kill）。"""
    start = time.monotonic()
    tracked: dict[int, dict] = {}
    with open(output, "a", encoding="utf-8") as f:
        while True:
            try:
                row = sample()
            except Exception:  # noqa: BLE001 - 采样异常降级为空行，不中断监控
                row = {"ts": "", "threads": 0, "procs": 0, "cpu": 0,
                       "pids_max": "N/A", "mem_max": "N/A", "proc_samples": []}
            try:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                f.flush()
            except OSError:
                return 1
            _track_and_report_exits(row, tracked)
            if duration > 0 and time.monotonic() - start >= duration:
                break
            time.sleep(max(interval, 0.1))
    return 0


def _peak_index(rows: list[dict]) -> dict[int, dict]:
    """汇总每 pid 的峰值 RSS(VmHWM) / 峰值线程数（旧日志无该字段时返回空表）。"""
    peaks: dict[int, dict] = {}
    for r in rows:
        for p in r.get("proc_samples") or []:
            try:
                pid = int(p.get("pid"))
            except (TypeError, ValueError):
                continue
            rec = peaks.setdefault(
                pid,
                {"kind": p.get("kind", "?"), "cmd": p.get("cmd", ""), "rss_kb": 0, "threads": 0},
            )
            try:
                rec["rss_kb"] = max(rec["rss_kb"], int(p.get("rss_kb") or 0))
                rec["threads"] = max(rec["threads"], int(p.get("threads") or 0))
            except (TypeError, ValueError):
                continue
    return peaks


def _peak_concurrent_rss_mb(rows: list[dict]) -> float:
    """采样点内"被监控进程 VmRSS 之和"的最大值（并发内存压力实测值）。"""
    best = 0.0
    for r in rows:
        total = 0
        for p in r.get("proc_samples") or []:
            try:
                total += int(p.get("rss_now_kb") or 0)
            except (TypeError, ValueError):
                continue
        best = max(best, total / 1024.0)
    return best


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
    print(f"容器 memory.max: {rows[-1].get('mem_max', 'N/A')}")  # 【增量】内存上限
    peak_row = max(rows, key=lambda r: r["threads"])
    print(f"线程峰值时间点: {peak_row['ts']}（threads={peak_row['threads']} procs={peak_row['procs']}）")
    if pids_max.isdigit():
        ratio = peak_t / int(pids_max)
        print(f"峰值占用率: {ratio:.1%}（{'⚠️ 逼近限制' if ratio > 0.8 else '✅ 余量充足'}）")

    # 【增量】每 worker / 每进程峰值 RSS 与线程数（来自 /proc/<pid>/status VmHWM 采样）
    peaks = _peak_index(rows)
    print("--- 每个 worker / 进程的峰值 RSS 与线程数（VmHWM 采样）---")
    if not peaks:
        print("（无 /proc 逐进程采样数据：非 Linux runner，或监控未启动/未生效）")
    else:
        for pid, rec in sorted(peaks.items(), key=lambda kv: kv[1]["rss_kb"], reverse=True)[:20]:
            print(
                f"pid={pid} kind={rec['kind']} 峰值RSS={rec['rss_kb'] / 1024:.1f}MB "
                f"峰值线程={rec['threads']} cmd={rec['cmd'][:80]}"
            )
        workers = [v for v in peaks.values() if v["kind"] == "worker"]
        print(f"被监控进程数: {len(peaks)}（其中 worker: {len(workers)}）")
        print(f"并发峰值合计 RSS: {_peak_concurrent_rss_mb(rows):.1f}MB（被监控进程 VmRSS 之和的最大值）")
    print("==================================")
    return 0


def snapshot(label: str) -> int:
    """一次性资源快照（跑测前后各打一次）。

    【不易】纯打印：非 Linux / cgroup 文件缺失 / 命令缺失一律降级 N/A，
    无论发生什么都返回 0 —— 观测本身绝不允许把作业变红。
    """
    try:
        nproc = read_nproc()
    except Exception:  # noqa: BLE001
        nproc = "N/A"
    try:
        mem_max = read_mem_max()
    except Exception:  # noqa: BLE001
        mem_max = "N/A"
    try:
        mem_current = read_mem_current()
    except Exception:  # noqa: BLE001
        mem_current = "N/A"
    try:
        pids_max = read_pids_max()
    except Exception:  # noqa: BLE001
        pids_max = "N/A"
    try:
        threads = count_threads()
    except Exception:  # noqa: BLE001
        threads = 0
    try:
        procs = count_procs()
    except Exception:  # noqa: BLE001
        procs = 0
    print(f"=== [资源快照] {label} ===", flush=True)
    print(f"nproc: {nproc}", flush=True)
    print(f"cgroup memory.max: {mem_max}", flush=True)
    print(f"cgroup memory.current: {mem_current}", flush=True)
    print(f"cgroup pids.max: {pids_max}", flush=True)
    print(f"threads: {threads}  procs: {procs}", flush=True)
    print("=== [资源快照结束] ===", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="CI 线程/进程资源监控（采样 + 峰值报告）")
    ap.add_argument("--output", default="thread-monitor.log", help="采样输出文件（JSON Lines）")
    ap.add_argument("--interval", type=float, default=2.0, help="采样间隔秒（默认 2）")
    ap.add_argument("--duration", type=float, default=0, help="采样时长秒，<=0 无限（默认 0）")
    ap.add_argument("--report", metavar="LOG", help="报告模式：读取采样日志输出峰值摘要")
    ap.add_argument("--snapshot", metavar="LABEL", help="快照模式：打印一次 nproc/内存/pids 上限后退出（恒 0）")
    args = ap.parse_args()

    if args.snapshot is not None:
        return snapshot(args.snapshot)
    if args.report:
        return report(args.report)
    if args.duration > 0:
        print(f"监控启动: 采样 {args.duration}s / 间隔 {args.interval}s → {args.output}", flush=True)
    else:
        print(f"监控启动: 无限采样 / 间隔 {args.interval}s → {args.output}（CI 用 kill 停止）", flush=True)
    return run_monitor(args.output, args.interval, args.duration)


if __name__ == "__main__":
    sys.exit(main())
