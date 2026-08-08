"""知识检索 QPS / P99 延迟基准（优化前后对比：内存链接缓存）。

对比两种模式（同一数据集 / 同一查询负载 / 同一进程硬件基线）：
- cache 模式（优化后）：双链扩展走预计算内存缓存（_link_cache），零文件 I/O；
- legacy 模式（优化前模拟）：双链扩展逐条 resolve_link→CardStore.get（文件 I/O
  + 读锁），与上一轮耗时日志实测（link avg≈75.7ms）同路径。

度量：单线程 + 多线程 并发下的 QPS 与 p50/p95/p99/max 延迟。

用法（Windows PowerShell）：
    $env:PYTHONIOENCODING="utf-8"
    python scripts/dev/benchmark_knowledge_search.py [--n 100] [--threads 8]
    # --json 输出到 test_reports/benchmark_knowledge_search.json
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import statistics
import sys
import tempfile
import time
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import scripts.dev.verify_knowledge_search as vks  # noqa: E402  复杂数据集 + fake 向量路

from agent.knowledge import KnowledgeSearch, resolve_link  # noqa: E402

logger = logging.getLogger(__name__)

# 查询负载（模拟真实混合检索：命中/部分命中/未命中）
_QUERIES = ["机器学习", "烘焙", "调参技巧", "深度学习", "机器学习", "生成对抗", "机器学习"]


def _legacy_link_recall(self, seeds, trace_id=None):
    """优化前实现（模拟基线）：逐条 resolve_link→CardStore.get（文件 I/O + 读锁）。"""
    expanded: list[str] = []
    seen = set(seeds)
    for seed in seeds:
        card = self._cards.get(seed)
        if card is None:
            continue
        for target in card.links:
            resolved = resolve_link(target, self._card_store)
            if resolved is None:
                continue
            if resolved.slug in seen:
                continue
            seen.add(resolved.slug)
            expanded.append(resolved.slug)
    return expanded


def _percentile(sorted_ms: list[float], pct: float) -> float:
    if not sorted_ms:
        return 0.0
    return sorted_ms[min(len(sorted_ms) - 1, int(pct * len(sorted_ms)))]


def _run_bench(searcher, queries, n, threads, rng_seed=20260807) -> dict:
    """执行基准：n 次 search（threads=1 串行，>1 并发均分），返回统计。"""
    rng = random.Random(rng_seed)
    seq = [rng.choice(queries) for _ in range(n)]
    for q in seq[:3]:  # 预热（BM25 索引/文件缓存热身后再计时）
        searcher.search(q)

    latencies: list[float] = []
    t0 = time.perf_counter()
    if threads == 1:
        for q in seq:
            s = time.perf_counter()
            searcher.search(q, 5)
            latencies.append((time.perf_counter() - s) * 1000)
    else:
        per_thread = max(1, n // threads)
        def _worker(tid: int) -> None:
            start = tid * per_thread
            for q in seq[start:start + per_thread]:
                s = time.perf_counter()
                searcher.search(q, 5)
                latencies.append((time.perf_counter() - s) * 1000)
        with ThreadPoolExecutor(max_workers=threads) as pool:
            list(pool.map(_worker, range(threads)))
    total_s = time.perf_counter() - t0

    actual = len(latencies)
    sorted_ms = sorted(latencies)
    return {
        "calls": actual,
        "qps": round(actual / total_s, 1),
        "mean_ms": round(statistics.mean(sorted_ms), 3),
        "p50_ms": round(_percentile(sorted_ms, 0.50), 3),
        "p95_ms": round(_percentile(sorted_ms, 0.95), 3),
        "p99_ms": round(_percentile(sorted_ms, 0.99), 3),
        "max_ms": round(sorted_ms[-1], 3),
    }


def _make_searcher(cache_mode: bool):
    tmp = tempfile.mkdtemp(prefix="kb-bench-")
    wiki = Path(tmp) / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    store = vks.build_complex_wiki(wiki)
    searcher = KnowledgeSearch(
        store, vector_store=vks._KeywordVectorStore(store.list()),
        min_score=0.3, timing_sample_rate=1.0,
    )
    if not cache_mode:
        searcher._link_recall = types.MethodType(_legacy_link_recall, searcher)
    return searcher


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="知识检索 QPS/P99 基准（缓存前后对比）")
    parser.add_argument("--n", type=int, default=100, help="每次基准调用次数（默认 100）")
    parser.add_argument("--threads", type=int, default=8, help="并发线程数（默认 8）")
    parser.add_argument("--json", type=str, default="", help="JSON 输出路径（默认不写文件）")
    args = parser.parse_args(argv)

    # 抑制 resolve_link / 写路径 INFO 日志（legacy 模式会刷屏）
    for _name in ("agent.knowledge.links", "agent.knowledge.card",
                  "agent.knowledge.index", "agent.knowledge.logbook",
                  "agent.knowledge.search"):
        logging.getLogger(_name).setLevel(logging.WARNING)
    logging.basicConfig(level=logging.WARNING)

    results: dict[str, dict] = {}
    print(f"════ 知识检索基准（n={args.n} 次 × threads={args.threads}）════")
    for mode, cache_mode in (("legacy（优化前）", False), ("cache（优化后）", True)):
        searcher = _make_searcher(cache_mode)
        r = _run_bench(searcher, _QUERIES, args.n, args.threads)
        results["cache" if cache_mode else "legacy"] = r
        print(f"\n── {mode} ──")
        print(f"  QPS        : {r['qps']:>10.1f}")
        print(f"  p50 延迟    : {r['p50_ms']:>10.3f} ms")
        print(f"  p95 延迟    : {r['p95_ms']:>10.3f} ms")
        print(f"  p99 延迟    : {r['p99_ms']:>10.3f} ms")
        print(f"  max 延迟    : {r['max_ms']:>10.3f} ms")

    a, b = results["legacy"], results["cache"]
    print("\n════ 前后对比 ════")
    print(f"  QPS : {a['qps']} → {b['qps']}（{b['qps'] / a['qps']:.1f}x）")
    print(f"  p99 : {a['p99_ms']} ms → {b['p99_ms']} ms（{a['p99_ms'] / max(b['p99_ms'], 1e-6):.0f}x 提速）")
    print(f"  p95 : {a['p95_ms']} ms → {b['p95_ms']} ms")
    print(f"  mean: {a['mean_ms']} ms → {b['mean_ms']} ms")

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[OK] JSON 已写入: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
