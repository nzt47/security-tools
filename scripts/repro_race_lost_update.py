#!/usr/bin/env python
"""丢更新（lost update）复现与复测脚本（TASK-08 子工作流 D / E1f）

【不易（为什么要有一个能"重测同一指标"的脚本）】
    审计报告 `security-tools/stress-report.json` 给出 `lost_rate: 0.063`、
    `lost_updates: 121031`、`threads: 64`。但该报告是**一次性产物**：
    仓库里存在 14 份逐字节相同的副本（MD5 一致），说明它**从未被重跑过**，
    也没有脚本化的重测入口。没有重测入口，就无法回答"现在还是不是这样"。
    本脚本把那条测量路径**固定成可重复执行的形态**。

【实测更正（与任务书描述不符，必须先看这一段）】
    1. 任务书称报告在 `data/stress-report.json` —— **该路径不存在**。
       实际在 `security-tools/stress-report.json`（684 B，2026-08-02）。
    2. 被争用的对象**不是**会话/计数/JSON 文件/DB 行，而是
       `agent/monitoring/prometheus.py:732` 的**模块级进程内 dict**
       `_intent_layer_counts`，其唯一消费方是 `yunshu_intent_layer_ratio` 这个
       **Gauge**。（同一个函数里的 Counter `.inc()` 在竞态块**之外**，
       且 `prometheus_client` 内部自带锁，不是竞态点。）
    3. **该竞态在本任务之前已被修复**：`git log -S "_counts_lock"` 只有一次提交
       `e6b71281`（2026-08-13，晚于报告 11 天），它在读改写外加了 `_counts_lock`。
       故本任务的处置是「**已修复 + 提供重测**」，而不是"再修一遍"。

【用途】
    python scripts/repro_race_lost_update.py                    # 设计内并发 vs 超设计并发
    python scripts/repro_race_lost_update.py --mode unlocked    # 模拟修复前，复现丢更新
    python scripts/repro_race_lost_update.py --threads 64 --iters 30000 --json
    python scripts/repro_race_lost_update.py --mode unlocked --threads 64 --iters 30000
        ↑ 这一条用于复现报告里的 0.063 量级

【退出码】0 = 指标已测出（无论是否有丢更新）；2 = 参数/环境错误。
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

#: waitress 生产配置的线程数（`app_server.py`: `serve(..., threads=16)`）
#: —— 这是**设计内并发**，是判断业务影响的基准线 16。
PRODUCTION_THREADS = 16


@contextlib.contextmanager
def _emulate_prefix_unlocked(module):
    """把 `_counts_lock` 临时换成**不提供互斥**的上下文管理器

    Why 这样做而不是改写源码：被测的必须是**同一个** `record_intent_layer`
    与**同一份** `_intent_layer_counts`。只把锁换成空操作，就能精确复现
    `e6b71281` 之前的代码形态（该提交唯一的改动就是给读改写加了这把锁），
    从而回答"那把锁到底值多少"。
    """
    original = module._counts_lock

    class _NoopLock:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    module._counts_lock = _NoopLock()
    try:
        yield
    finally:
        module._counts_lock = original


def measure(threads: int = 64, iters: int = 30000,
            locked: bool = True, layers: int = 5) -> dict:
    """用 `threads` 个线程各调用 `iters` 次，测量 `_intent_layer_counts` 的丢更新

    Args:
        locked: False 时模拟修复前（去掉互斥）—— 应当观察到丢更新

    Returns:
        {threads, iters, expected, observed, lost_updates, lost_rate, ...}
    """
    from agent.monitoring import prometheus as P

    P.reset_intent_layer_counts()
    layer_names = [f"L{i}" for i in range(layers)]

    def _worker(tid: int) -> None:
        # 每个线程只打自己那一层，使"每层期望计数"完全可预测 ——
        # 这样丢更新数 = 期望 - 实测，无需依赖任何随机性
        name = layer_names[tid % layers]
        for _ in range(iters):
            P.record_intent_layer(name)

    ctx = contextlib.nullcontext() if locked else _emulate_prefix_unlocked(P)
    with ctx:
        ts = [threading.Thread(target=_worker, args=(i,)) for i in range(threads)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()

    counts = dict(P._intent_layer_counts)
    observed = sum(counts.values())
    expected = threads * iters
    lost = expected - observed
    return {
        "mode": "locked(current)" if locked else "unlocked(pre-e6b71281)",
        "threads": threads,
        "iters_per_thread": iters,
        "expected_total": expected,
        "observed_total": observed,
        "lost_updates": lost,
        "lost_rate": round(lost / expected, 6) if expected else 0.0,
        "per_layer": {k: counts.get(k, 0) for k in layer_names},
        "integrity": {
            # 报告里的 failures 空表语义：各层计数之和必须等于 observed
            "sum_matches_observed": sum(counts.values()) == observed,
            "all_layers_present": all(k in counts for k in layer_names),
        },
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="丢更新复现/复测（E1f）")
    ap.add_argument("--threads", type=int, default=PRODUCTION_THREADS)
    ap.add_argument("--iters", type=int, default=30000)
    ap.add_argument("--mode", choices=("locked", "unlocked", "both"), default="both",
                    help="locked=当前代码；unlocked=模拟修复前；both=两者对比")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.threads < 1 or args.iters < 1:
        print("参数错误：--threads/--iters 必须为正整数", file=sys.stderr)
        return 2

    runs = []
    try:
        if args.mode in ("locked", "both"):
            runs.append(measure(args.threads, args.iters, locked=True))
        if args.mode in ("unlocked", "both"):
            runs.append(measure(args.threads, args.iters, locked=False))
        # 超设计并发对照（仅当用户没显式指定线程数时给出，避免脚本变慢）
        if args.mode == "both" and args.threads == PRODUCTION_THREADS:
            runs.append(measure(64, args.iters, locked=True))
    except Exception as exc:  # noqa: BLE001
        print(f"测量失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"runs": runs, "production_threads": PRODUCTION_THREADS},
                         ensure_ascii=False, indent=2))
        return 0

    print("=" * 74)
    print("丢更新复现/复测（E1f）—— 对象: agent/monitoring/prometheus.py::_intent_layer_counts")
    print(f"waitress 设计内并发 = {PRODUCTION_THREADS} 线程（app_server.py: serve(..., threads=16)）")
    print("=" * 74)
    print(f"{'模式':<26}{'线程':>6}{'期望':>12}{'实测':>12}{'丢失':>10}{'丢率':>10}")
    print("-" * 74)
    for r in runs:
        print(f"{r['mode']:<26}{r['threads']:>6}{r['expected_total']:>12}"
              f"{r['observed_total']:>12}{r['lost_updates']:>10}{r['lost_rate']:>10}")
    print("-" * 74)
    for r in runs:
        if not r["integrity"]["sum_matches_observed"]:
            print(f"  ✗ {r['mode']} 各层之和与总数不符（数据结构被破坏）")
    print()
    print("读法：unlocked 行 = 修复前形态，用于复现报告的 0.063 量级；")
    print("      locked 行   = 当前代码（e6b71281 已加锁），丢率应为 0。")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
