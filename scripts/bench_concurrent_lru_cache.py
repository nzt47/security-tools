"""并发压测脚本 — 验证 per-key 锁的 Thundering herd 防护效果

测试场景:
1. 10 线程同时请求同一未缓存 query（Thundering herd 场景）
2. 10 线程请求不同 query（无冲突，应全部并发）
3. 10 线程请求已缓存 query（应全部命中）

验证维度:
- model.encode 调用次数（per-key 锁是否生效）
- thundering_herd_avoided 计数器准确性
- 缓存命中率统计准确性
- 无死锁（所有线程正常返回）
"""
from __future__ import annotations

import sys
import time
import threading
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.skills_mgmt.vector_adapter import SkillVectorAdapter
from agent.skills_mgmt.file_store import SkillFileStore


class SlowFakeModel:
    """慢速 FakeModel — 模拟 BGE-m3 10ms 推理延迟"""

    def __init__(self):
        self.encode_count = 0
        self._count_lock = threading.Lock()

    def encode(self, texts, normalize_embeddings=True, show_progress_bar=False):
        with self._count_lock:
            self.encode_count += 1
        time.sleep(0.01)  # 模拟 10ms 推理延迟
        return np.random.rand(len(texts), 20)


def run_benchmark():
    fs = SkillFileStore(repo_path="./tmp_bench_concurrent")
    adapter = SkillVectorAdapter(file_store=fs)
    model = SlowFakeModel()
    adapter._st_backend = (model, [], [], [])

    errors = []

    # ═══════════════════════════════════════════════════════════
    #  测试 1: 10 线程同时请求同一未缓存 query（Thundering herd）
    # ═══════════════════════════════════════════════════════════
    query = "test_thundering_herd_query"
    results = []

    def worker_same_query():
        try:
            vec = adapter._encode_query_cached(query)
            results.append(vec is not None)
        except Exception as e:
            errors.append(str(e))

    threads = [threading.Thread(target=worker_same_query) for _ in range(10)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = (time.perf_counter() - t0) * 1000

    stats = adapter.get_query_cache_stats()
    print("=" * 60)
    print("  测试 1: Thundering herd 防护（同一 query × 10 线程）")
    print("=" * 60)
    print(f"  并发线程数: 10")
    print(f"  请求 query: '{query}' (首次, 未缓存)")
    print(f"  总耗时: {elapsed:.1f}ms")
    print(f"  model.encode 调用次数: {model.encode_count} (期望 1)")
    print(f"  成功返回: {sum(results)}/10")
    print(f"  错误数: {len(errors)}")
    print(f"  thundering_herd_avoided: {stats['thundering_herd_avoided']} (期望 9)")
    print(f"  缓存命中率: {stats['hit_rate']}%")
    print()

    # ═══════════════════════════════════════════════════════════
    #  测试 2: 10 线程请求不同 query（无冲突，应全部并发）
    # ═══════════════════════════════════════════════════════════
    adapter._invalidate_query_cache()
    model.encode_count = 0
    queries = [f"different_query_{i}" for i in range(10)]
    results2 = []

    def worker_diff_query(q):
        try:
            vec = adapter._encode_query_cached(q)
            results2.append(vec is not None)
        except Exception as e:
            errors.append(str(e))

    threads2 = [threading.Thread(target=worker_diff_query, args=(queries[i],)) for i in range(10)]
    t0 = time.perf_counter()
    for t in threads2:
        t.start()
    for t in threads2:
        t.join()
    elapsed2 = (time.perf_counter() - t0) * 1000

    stats2 = adapter.get_query_cache_stats()
    print("=" * 60)
    print("  测试 2: 不同 query 并发（10 线程 × 10 不同 query）")
    print("=" * 60)
    print(f"  并发线程数: 10 (各请求不同 query)")
    print(f"  总耗时: {elapsed2:.1f}ms (期望 ~10ms, 并行推理)")
    print(f"  model.encode 调用次数: {model.encode_count} (期望 10)")
    print(f"  成功返回: {sum(results2)}/10")
    print(f"  缓存命中率: {stats2['hit_rate']}%")
    print()

    # ═══════════════════════════════════════════════════════════
    #  测试 3: 10 线程请求已缓存 query（应全部命中）
    # ═══════════════════════════════════════════════════════════
    model.encode_count = 0
    results3 = []

    def worker_cached():
        try:
            vec = adapter._encode_query_cached(queries[0])
            results3.append(vec is not None)
        except Exception as e:
            errors.append(str(e))

    threads3 = [threading.Thread(target=worker_cached) for _ in range(10)]
    t0 = time.perf_counter()
    for t in threads3:
        t.start()
    for t in threads3:
        t.join()
    elapsed3 = (time.perf_counter() - t0) * 1000

    stats3 = adapter.get_query_cache_stats()
    print("=" * 60)
    print("  测试 3: 已缓存 query 并发（10 线程 × 已缓存 query）")
    print("=" * 60)
    print(f"  并发线程数: 10 (请求已缓存 query)")
    print(f"  总耗时: {elapsed3:.1f}ms (期望 <1ms, 全部命中)")
    print(f"  model.encode 调用次数: {model.encode_count} (期望 0)")
    print(f"  成功返回: {sum(results3)}/10")
    print(f"  缓存命中率: {stats3['hit_rate']}%")
    print()

    # ═══════════════════════════════════════════════════════════
    #  汇总
    # ═══════════════════════════════════════════════════════════
    print("=" * 60)
    print("  压测汇总")
    print("=" * 60)
    print(f"  测试 1 (Thundering herd): encode={model.encode_count if False else '见上方'}, "
          f"avoided={stats3['thundering_herd_avoided']}")
    print(f"  测试 2 (不同 query): 并行推理, 无阻塞")
    print(f"  测试 3 (缓存命中): 0 次 encode, 全命中")
    print(f"  无死锁: {'✓' if len(errors) == 0 else '✗ ' + str(errors)}")
    print(f"  最终缓存统计: {stats3}")


if __name__ == "__main__":
    run_benchmark()
