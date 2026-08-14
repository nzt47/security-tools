"""锁竞争热点分析 — P1·B1 采样执行与热点报告（配套 agent/monitoring/lock_profiler.py）

【任务定位】
    在 LOCK_PROFILE=1 下采集锁等待/持锁样本（JSONL），并汇总出 Top 锁热点，
    为读锁/分段锁/无锁化改造提供量化依据。

【用法】
    python scripts/analyze_lock_hotspots.py --self-check                # 内置压力场景自检采样（最快验证链路）
    python scripts/analyze_lock_hotspots.py --pytest tests/unit/xxx.py  # 指定 pytest 场景采集（真实热点）
    python scripts/analyze_lock_hotspots.py --report --top 10           # 汇总 JSONL 生成热点报告
    python scripts/analyze_lock_hotspots.py --clean                      # 清空采样文件（每次新采集前建议先 clean）

【环境变量】
    LOCK_PROFILE_LOG    采样 JSONL 路径（默认 %TEMP%/lock_profile.jsonl）
    LOCK_PROFILE_BATCH  批量落盘条数（默认 500）

【注意（不易）】
    - --pytest 模式会以子进程运行 pytest 并注入 LOCK_PROFILE=1，请勿与全量回归并发跑
      （会产生性能噪音，污染样本与回归数据）。
    - 采样仅诊断用途，不修改任何被测代码。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def _default_log() -> str:
    return os.getenv("LOCK_PROFILE_LOG", os.path.join(tempfile.gettempdir(), "lock_profile.jsonl"))


def cmd_self_check() -> None:
    """内置压力场景：8 线程竞争同一把锁 + 1 把无竞争锁，验证采样链路并产出样例"""
    from agent.monitoring.lock_profiler import SampledLock, _recorder

    log_path = _default_log()
    if os.path.exists(log_path):
        os.remove(log_path)
    os.environ["LOCK_PROFILE"] = "1"
    os.environ["LOCK_PROFILE_LOG"] = log_path

    hot = SampledLock(name="hot_contended_lock")
    cold = SampledLock(name="cold_uncontended_lock")

    def contender() -> None:
        for _ in range(1000):
            with hot:
                pass  # 竞争热点：短临界区高竞争

    threads = [threading.Thread(target=contender) for _ in range(8)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for _ in range(1000):
        with cold:
            pass
    _recorder().flush_all()
    elapsed = time.perf_counter() - t0

    n = sum(1 for _ in open(log_path, encoding="utf-8")) if os.path.exists(log_path) else 0
    print(f"[self-check] 完成: 样本 {n} 条, 耗时 {elapsed:.2f}s, log={log_path}")
    print(f"[self-check] 后续执行: python scripts/analyze_lock_hotspots.py --report")


def cmd_pytest(targets: list) -> None:
    """在 LOCK_PROFILE=1 下运行指定 pytest 场景，采集真实锁热点"""
    log_path = _default_log()
    env = {**os.environ, "LOCK_PROFILE": "1", "LOCK_PROFILE_LOG": log_path}
    argv = [sys.executable, "-m", "pytest", "-p", "no:randomly", "--no-header", *targets]
    print(f"[pytest] 采集开始: {' '.join(argv)}")
    print(f"[pytest] 采样 log: {log_path}")
    rc = subprocess.call(argv, env=env)
    print(f"[pytest] 退出码 {rc}（0=通过）；采样数据已写入 {log_path}")
    print(f"[pytest] 汇总: python scripts/analyze_lock_hotspots.py --report")


def cmd_report(top: int) -> None:
    """汇总 JSONL → 锁热点报告（按累计等待 + 持锁排序，识别读锁/分段锁候选）"""
    log_path = _default_log()
    if not os.path.exists(log_path):
        print(f"[report] 采样文件不存在: {log_path}（先运行 --self-check 或 --pytest）")
        sys.exit(1)

    stats: dict = {}  # lock_name -> {count, wait_sum, hold_sum, wait_max, hold_max}
    for line in open(log_path, encoding="utf-8"):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = rec.get("lock_name", "<unknown>")
        s = stats.setdefault(name, {"count": 0, "wait_sum": 0.0, "hold_sum": 0.0,
                                    "wait_max": 0.0, "hold_max": 0.0})
        s["count"] += 1
        s["wait_sum"] += rec.get("wait_us", 0.0)
        s["hold_sum"] += rec.get("hold_us", 0.0)
        s["wait_max"] = max(s["wait_max"], rec.get("wait_us", 0.0))
        s["hold_max"] = max(s["hold_max"], rec.get("hold_us", 0.0))

    if not stats:
        print(f"[report] 采样文件为空或无效: {log_path}")
        sys.exit(1)

    rows = []
    for name, s in stats.items():
        rows.append((name, s["count"], s["wait_sum"] / 1000.0, s["hold_sum"] / 1000.0,
                     s["wait_max"] / 1000.0, s["hold_max"] / 1000.0))
    # 热点排序: 累计等待 ms 降序（竞争最烈优先）
    rows.sort(key=lambda r: r[2], reverse=True)

    print(f"\n{'锁名':<32}{'次数':>8}{'累计等待ms':>12}{'累计持锁ms':>12}{'最大等待ms':>12}{'最大持锁ms':>12}")
    print("-" * 88)
    for name, count, ws, hs, wm, hm in rows[:top]:
        print(f"{name:<32}{count:>8}{ws:>12.2f}{hs:>12.2f}{wm:>12.2f}{hm:>12.2f}")
    print(f"\n[report] 共 {len(rows)} 把锁被采样, 展示 Top {min(top, len(rows))}")
    print("[report] 候选优化方向: 累计等待高的锁 → 考虑读锁/分段锁/无锁化（见 P1B1_C2C3_实施计划）")


def cmd_clean() -> None:
    log_path = _default_log()
    if os.path.exists(log_path):
        os.remove(log_path)
        print(f"[clean] 已删除 {log_path}")
    else:
        print(f"[clean] 无采样文件（{log_path}）")


def main() -> None:
    parser = argparse.ArgumentParser(description="B1 锁竞争热点分析（采样执行 + 报告）")
    parser.add_argument("--self-check", action="store_true", help="内置压力场景自检采样")
    parser.add_argument("--pytest", nargs="+", metavar="TARGET", help="在 LOCK_PROFILE=1 下运行指定 pytest 场景")
    parser.add_argument("--report", action="store_true", help="汇总采样 JSONL 生成热点报告")
    parser.add_argument("--clean", action="store_true", help="清空采样文件")
    parser.add_argument("--top", type=int, default=10, help="报告展示 Top N（默认 10）")
    args = parser.parse_args()

    if args.clean:
        cmd_clean()
    if args.self_check:
        cmd_self_check()
    if args.pytest:
        cmd_pytest(args.pytest)
    if args.report:
        cmd_report(args.top)
    if not (args.clean or args.self_check or args.pytest or args.report):
        parser.print_help()


if __name__ == "__main__":
    main()
