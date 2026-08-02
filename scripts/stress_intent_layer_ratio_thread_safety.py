#!/usr/bin/env python3
"""意图层 ratio 不变量高并发压力测试（独立脚本）

【不易】核心不变量（与 verify_intent_layer_ratio_ci.py 同一数学恒等）：
  - INV-RATIO: sum(count/total) == 1.0（count/total 求和 = total/total，容差 1e-9）
  - INV-GAUGE: yunshu_intent_layer_ratio 实际 gauge 值 == count/total
  - INV-CONCUR: 高并发写入下 ratio 总和仍 = 1.0
    TD-3 场景：_intent_layer_counts 无显式锁，GIL 守护下 read-modify-write 可能
    丢失更新（绝对计数偏低），但 ratio 数学恒等不受影响 —— 本脚本同时测量丢失率。

【变易】参数化压测规模：--threads / --iters / --rounds / --seed
【简易】独立可运行，仅依赖 prometheus-client 与项目自身 prometheus 模块。

用法:
  python scripts/stress_intent_layer_ratio_thread_safety.py
  python scripts/stress_intent_layer_ratio_thread_safety.py \\
      --threads 64 --iters 100000 --rounds 5 --json stress-report.json
"""
import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone

# 确保项目根目录在 sys.path（独立运行脚本时 agent 包可导入）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.monitoring.prometheus import (
    record_intent_layer,
    reset_intent_layer_counts,
    _intent_layer_counts as _ilc,
)

# 容忍误差：浮点除法求和的理论上界（IEEE754 double）
_EPS = 1e-9

# 压测层池：贴近真实流量分布（rule/template/semantic/llm 为主，reject/子指标低频）
_LAYER_POOL = (
    ["rule"] * 4
    + ["template"] * 3
    + ["semantic"] * 3
    + ["llm"] * 4
    + ["reject"] * 1
    + ["llm_low_confidence_fallback"] * 1
    + ["llm_error"] * 1
)


def _ratio_sum() -> float:
    """计算当前 ratio 总和（分母同步不变量，应恒 = 1.0）"""
    total = sum(_ilc.values())
    if total == 0:
        return 0.0
    return sum(c / total for c in _ilc.values())


def _gauge_value(layer):
    """读取指定 layer 的 ratio Gauge 当前值（经 prometheus REGISTRY）"""
    try:
        from prometheus_client import REGISTRY as _REG
        sample = _REG.get_sample_value(
            "yunshu_intent_layer_ratio", {"layer": layer}
        )
        return sample if sample is not None else 0.0
    except Exception:
        # prometheus_client 不可用时 gauge 检查降级跳过（对应 _NoopGauge 场景）
        return None


def _check_invariant(round_no: int, failures: list) -> None:
    """校验 ratio 不变量：总和 = 1.0、每层 ratio ∈ [0,1]、gauge 同步"""
    total = sum(_ilc.values())
    if total == 0:
        return
    rs = _ratio_sum()
    if abs(rs - 1.0) > _EPS:
        failures.append(
            "[round %d] ratio 总和=%.12f (期望 1.0), counts=%r"
            % (round_no, rs, dict(_ilc))
        )
    for layer, count in _ilc.items():
        r = count / total
        if r < 0.0 or r > 1.0:
            failures.append(
                "[round %d] layer=%s ratio=%.6f 越界" % (round_no, layer, r)
            )
        gv = _gauge_value(layer)
        if gv is not None and abs(gv - r) > 1e-6:
            failures.append(
                "[round %d] layer=%s gauge=%.10f != count/total=%.10f"
                % (round_no, layer, gv, r)
            )


def _worker(iterations: int, seed: int, barrier) -> None:
    """单个压测线程：随机层重复写入 record_intent_layer"""
    import random
    rng = random.Random(seed)
    barrier.wait()  # 同步起跑，最大化争用窗口
    for _ in range(iterations):
        record_intent_layer(rng.choice(_LAYER_POOL))


def run_concurrency_round(threads: int, iters: int, seed: int,
                          failures: list, round_no: int) -> dict:
    """执行一轮并发压测，返回本轮统计信息"""
    reset_intent_layer_counts()
    barrier = threading.Barrier(threads)
    workers = [
        threading.Thread(target=_worker, args=(iters, seed + i, barrier))
        for i in range(threads)
    ]
    t0 = time.perf_counter()
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    elapsed = time.perf_counter() - t0

    expected = threads * iters
    actual = sum(_ilc.values())
    lost = expected - actual
    lost_rate = lost / expected if expected else 0.0
    _check_invariant(round_no, failures)

    return {
        "threads": threads,
        "iters_per_thread": iters,
        "expected_total": expected,
        "actual_total": actual,
        "lost_updates": lost,
        "lost_rate": round(lost_rate, 6),
        "elapsed_sec": round(elapsed, 3),
        "ratio_sum": round(_ratio_sum(), 12),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="意图层 ratio 不变量并发压力测试")
    parser.add_argument("--threads", type=int, default=32, help="并发线程数（默认 32）")
    parser.add_argument("--iters", type=int, default=50000, help="每线程写入次数（默认 50000）")
    parser.add_argument("--rounds", type=int, default=3, help="重复压测轮数（默认 3）")
    parser.add_argument("--seed", type=int, default=20260802, help="伪随机种子")
    parser.add_argument("--json", default="", help="输出 JSON 报告路径（可选）")
    args = parser.parse_args()

    if args.threads < 1 or args.iters < 1 or args.rounds < 1:
        print("❌ --threads/--iters/--rounds 必须为正整数")
        return 2

    print("=" * 70)
    print("  意图层 ratio 不变量高并发压力测试")
    print("  线程数=%d × 每线程写入=%d × 轮数=%d" % (args.threads, args.iters, args.rounds))
    print("  总写入量=%d（每轮）" % (args.threads * args.iters))
    print("=" * 70)

    failures = []
    rounds_report = []
    for r in range(1, args.rounds + 1):
        stats = run_concurrency_round(
            args.threads, args.iters, args.seed + r * 7919, failures, r
        )
        rounds_report.append(stats)
        flag = "✓" if abs(stats["ratio_sum"] - 1.0) < _EPS else "✗"
        print(
            "[round %d/%d] %s ratio 总和=%.12f 实际计数=%d/%d 丢失率=%.4f%% 耗时=%.2fs"
            % (
                r, args.rounds, flag, stats["ratio_sum"],
                stats["actual_total"], stats["expected_total"],
                stats["lost_rate"] * 100, stats["elapsed_sec"],
            )
        )
        # 汇总报告：单轮立即输出不变量详情
        for layer, count in _ilc.items():
            print("    layer=%-26s count=%-9d ratio=%.6f" % (layer, count, count / max(sum(_ilc.values()), 1)))
        reset_intent_layer_counts()

    ok = not failures
    print("=" * 70)
    if ok:
        print("✓ 全部 %d 轮并发写入后 ratio 总和恒 = 1.0，gauge 同步，无越界" % args.rounds)
    else:
        print("✗ 发现 %d 处不变量违规：" % len(failures))
        for f in failures:
            print("   - %s" % f)
    print("=" * 70)

    report = {
        "invariants": {
            "ratio_sum_equals_1": ok,
            "tolerance": _EPS,
            "failures": failures,
        },
        "rounds": rounds_report,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print("JSON 报告已写入: %s" % args.json)

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
