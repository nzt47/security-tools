#!/usr/bin/env python
"""决策缓存性能实测（§5.6：决策缓存 p99 < 5ms）

【为什么要有这个脚本，而不是只在单测里断言】

    START S4-02 §七.2 已经点明：「性能断言（p99<5ms）在 CI 覆盖率插桩下会抖动」。
    因此本任务把口径拆成两半：

      - **单测**（``tests/unit/test_policy_engine.py::TestLatencyEvidence``）做
        **相对断言**：缓存命中路径的 p99 不慢于未命中路径的同一量级 —— 这是
        「缓存确实在起作用」的结构性证据，不受机器负载影响；
      - **本脚本**做**绝对实测**：输出 p50/p95/p99/max，作为验收报告引用的原始证据。

    两者都必要：单测保证不会退化成「缓存没生效却仍然绿」，脚本保证有可核对的口径。

【口径（必须写清楚，否则数字没有意义）】

    - 计时用 ``time.perf_counter()``，测量 ``PolicyEngine.check()`` **完整耗时**
      （含缓存查找、埋点与决策日志写入），单位毫秒。
    - **命中路径** = 重复同一组输入（缓存容量足够）；**未命中路径** = 每组输入只来一次。
    - 预热：先跑 ``--warmup`` 次丢弃样本（排除首次导入/分支预测/内存分配的开销）。
    - 默认**不落盘、不埋点**（``--with-io`` 打开），因为验收指标是**决策**延迟；
      打开后的数字会包含审计/事件/日志 IO，单独一行列出。
    - 判定：命中路径 p99 ≤ ``--threshold-ms``（默认 5.0）。用 ``--enforce`` 才让
      不达标变成非零退出（CI 分片高负载下不建议强制）。

【用法】

    python scripts/bench_policy_cache.py                       # 打印证据
    python scripts/bench_policy_cache.py --enforce              # 不达标即退出 1
    python scripts/bench_policy_cache.py --iterations 20000 --with-io
    python scripts/bench_policy_cache.py --json-out reports/policy_cache_bench.json
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.policy.engine import DecisionObserver, PolicyEngine  # noqa: E402
from agent.policy.models import PolicyContext  # noqa: E402
from agent.policy.store import PolicyStore  # noqa: E402

#: 默认策略库（仓库真实配置；不存在则只用内置不变量）
DEFAULT_POLICY_FILE = "data/policies/policies.json"

#: 判定阈值（毫秒）—— §5.6 的硬指标
DEFAULT_THRESHOLD_MS = 5.0

#: 输入多样本数（决定缓存未命中路径的样本来源）
DEFAULT_VARIANTS = 64


def _build_engine(*, cache_size: int, with_io: bool, tmp_dir: str) -> PolicyEngine:
    store = PolicyStore(path=DEFAULT_POLICY_FILE)
    if with_io:
        from agent.policy.decisions import DecisionLog
        log = DecisionLog(os.path.join(tmp_dir, "bench_decisions.jsonl"))
        observer = DecisionObserver()
    else:
        log = False
        observer = DecisionObserver(enabled=False)
    return PolicyEngine(store, cache_size=cache_size, decision_log=log,
                        observer=observer, inbox=False)


def _contexts(variants: int) -> List[PolicyContext]:
    """构造覆盖判定分支的输入集合（命中/未命中/拒绝各占一部分）"""
    out: List[PolicyContext] = []
    for index in range(variants):
        external = index % 3 != 0
        data_class = "secret" if index % 5 == 0 else "internal"
        out.append(PolicyContext.build(
            capability_id=f"cp.bench.demo.act{index % 7}",
            capability={"trust": {"data_class": data_class}},
            tenant_id=f"t-{index % 4}",
            actor=f"u-{index}",
            action=f"http.{'post' if external else 'get'}",
            target={"external": external, "host": "bench.example.com",
                    "scheme": "https"},
            attributes={"payload_bytes": 128 + index}))
    return out


def _percentiles(samples: Sequence[float]) -> Dict[str, Any]:
    if not samples:
        return {"count": 0, "p50": None, "p95": None, "p99": None, "max": None,
                "mean": None, "stdev": None}
    ordered = sorted(samples)

    def _pct(p: float) -> float:
        index = min(len(ordered) - 1,
                    max(0, int(round((p / 100.0) * (len(ordered) - 1)))))
        return ordered[index]

    return {
        "count": len(ordered),
        "p50": round(_pct(50), 4),
        "p95": round(_pct(95), 4),
        "p99": round(_pct(99), 4),
        "max": round(ordered[-1], 4),
        "mean": round(statistics.fmean(ordered), 4),
        "stdev": round(statistics.pstdev(ordered), 4) if len(ordered) > 1 else 0.0,
    }


def measure(*, iterations: int, variants: int, warmup: int, cache_size: int,
            with_io: bool, tmp_dir: str) -> Dict[str, Any]:
    """跑两轮：命中路径（重复同一批输入）与未命中路径（每次新输入）"""
    engine = _build_engine(cache_size=cache_size, with_io=with_io, tmp_dir=tmp_dir)
    contexts = _contexts(variants)

    # ── 预热（样本丢弃）──
    for index in range(warmup):
        engine.check(contexts[index % len(contexts)])

    # ── 命中路径 ──
    engine.reset_latency()
    hit_samples: List[float] = []
    for index in range(iterations):
        ctx = contexts[index % len(contexts)]
        engine.check(ctx)          # 第一次可能未命中
        started = time.perf_counter()
        decision = engine.check(ctx)   # 第二次必定命中（容量充足）
        hit_samples.append((time.perf_counter() - started) * 1000.0)
        if index == 0 and not decision.cache_hit:
            # 容量不足导致的"看似命中"会让指标失真 —— 显式失败而不是静默
            raise SystemExit(
                f"缓存未生效（capacity={cache_size}, variants={variants}）；"
                "请增大 --cache-size 或减少 --variants")
    # 必须**在重置样本之前**取走引擎内部统计：命中/未命中两轮的样本共用同一个
    # 有界窗口，晚一步取就只剩最后一轮的数据（本脚本实现期踩到的坑）。
    hit_engine_stats = engine.latency_percentiles(cache_hit=True)
    cache_stats = dict(engine.stats["cache"])

    # ── 未命中路径 ──
    engine.reset_latency()
    engine.invalidate()
    miss_samples: List[float] = []
    for index in range(iterations):
        ctx = contexts[index % len(contexts)]
        engine.invalidate()        # 每次强制未命中（同输入不同 epoch）
        started = time.perf_counter()
        engine.check(ctx)
        miss_samples.append((time.perf_counter() - started) * 1000.0)
    miss_engine_stats = engine.latency_percentiles(cache_hit=False)

    return {
        "engine_latency_hit": hit_engine_stats,
        "engine_latency_miss": miss_engine_stats,
        "wall_hit": _percentiles(hit_samples),
        "wall_miss": _percentiles(miss_samples),
        "cache_stats": cache_stats,
    }


def _print_table(title: str, stats: Dict[str, Any]) -> None:
    print(f"\n{title}")
    print(f"  samples={stats['count']:<8} p50={stats['p50']} ms   "
          f"p95={stats['p95']} ms   p99={stats['p99']} ms   "
          f"max={stats['max']} ms   mean={stats['mean']} ms")
    if stats.get("stdev") is not None:
        print(f"  stdev={stats['stdev']} ms")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bench_policy_cache",
        description="决策缓存 p99 实测（§5.6）")
    parser.add_argument("--iterations", type=int, default=5000,
                        help="每个路径的样本数（默认 5000）")
    parser.add_argument("--variants", type=int, default=DEFAULT_VARIANTS,
                        help="输入多样本数（默认 64）")
    parser.add_argument("--warmup", type=int, default=200, help="预热次数（默认 200）")
    parser.add_argument("--cache-size", type=int, default=2048, help="缓存容量")
    parser.add_argument("--with-io", action="store_true",
                        help="打开审计/事件/决策日志（数字含 IO；会明显变慢但远低于阈值）")
    parser.add_argument("--threshold-ms", type=float, default=DEFAULT_THRESHOLD_MS,
                        help="命中路径 p99 阈值（默认 5.0 ms）")
    parser.add_argument("--enforce", action="store_true",
                        help="不达标时以非零退出码结束（CI 高负载下慎用）")
    parser.add_argument("--json-out", default=None, help="把结果写成 JSON")
    args = parser.parse_args(list(argv) if argv is not None else None)

    import tempfile
    tmp_dir = tempfile.mkdtemp(prefix="policy_bench_")

    print("=" * 74)
    print("云枢策略决策缓存实测（§5.6 决策缓存 p99 < 5ms）")
    print("=" * 74)
    print(f"python      : {sys.version.split()[0]}")
    print(f"platform    : {platform.platform()}")
    print(f"cpu_count   : {os.cpu_count()}")
    print(f"口径        : perf_counter 包夹 PolicyEngine.check() 全量耗时（毫秒）")
    print(f"参数        : iterations={args.iterations} variants={args.variants} "
          f"warmup={args.warmup} cache_size={args.cache_size} with_io={args.with_io}")
    print(f"策略库      : {DEFAULT_POLICY_FILE}")

    result = measure(iterations=args.iterations, variants=args.variants,
                     warmup=args.warmup, cache_size=args.cache_size,
                     with_io=args.with_io, tmp_dir=tmp_dir)
    engine = _build_engine(cache_size=args.cache_size, with_io=args.with_io,
                           tmp_dir=tmp_dir)
    print(f"生效策略数  : {len(engine.store.active())}"
          f"（指纹 {engine.store.fingerprint()}）")

    _print_table("【命中路径】引擎内部统计", result["engine_latency_hit"])
    _print_table("【命中路径】外部计时（bench 侧）", result["wall_hit"])
    _print_table("【未命中路径】引擎内部统计", result["engine_latency_miss"])
    _print_table("【未命中路径】外部计时（bench 侧）", result["wall_miss"])
    print(f"\n缓存统计    : {result['cache_stats']}")

    hit_p99 = result["wall_hit"]["p99"] or 0.0
    budget = args.threshold_ms
    print(f"\n判定        : 命中路径 p99 = {hit_p99} ms  "
          f"{'≤' if hit_p99 <= budget else '>'} 阈值 {budget} ms  "
          f"=> {'PASS' if hit_p99 <= budget else 'FAIL'}")

    payload = {
        "schema": "policy.cache_bench.v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "params": {"iterations": args.iterations, "variants": args.variants,
                   "warmup": args.warmup, "cache_size": args.cache_size,
                   "with_io": args.with_io, "threshold_ms": budget},
        "policy_file": DEFAULT_POLICY_FILE,
        "result": result,
        "verdict": "pass" if hit_p99 <= budget else "fail",
    }
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        print(f"结果已写出: {args.json_out}")

    if args.enforce and hit_p99 > budget:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI 入口
    raise SystemExit(main())
