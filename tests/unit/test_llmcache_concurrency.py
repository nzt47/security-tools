"""monitoring/performance.LLMCache 并发安全测试。

修复前：get/put 的「检查 → 结构变更（move_to_end/popitem/删除）→ 计数」为
TOCTOU + 读-改-写序列，多线程并发会损坏 OrderedDict 内部链表（RuntimeError）
或丢计数/超容量。修复后：get/put/clear/get_top_patterns 统一 threading.Lock，
锁内仅内存操作（遵守持锁纪律），容量检查与驱逐原子化。
"""

import threading

from agent.monitoring.performance import LLMCache


class TestLLMCacheConcurrency:
    """LLMCache 并发读写（threading.Lock 原子化）。"""

    def test_concurrent_put_capacity_no_oversell(self):
        """100 线程 × 10 次 put 唯一 key（max=50）：容量不超限、无结构损坏"""
        cache = LLMCache(max_size=50, ttl_seconds=3600)
        n_threads, per = 100, 10
        total = n_threads * per
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker(tid):
            try:
                barrier.wait()
                for i in range(per):
                    cache.put(f"prompt_{tid}_{i}", f"response_{tid}_{i}")
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"并发 put 不应抛异常（无结构损坏）: {errors}"
        assert len(cache.cache) <= cache.max_size  # 容量检查不超限
        # 写入总数 = 容量 + 逐出数（key 全部唯一）
        assert cache.stats.evictions == total - len(cache.cache)

    def test_concurrent_put_same_key_no_bloat(self):
        """100 线程 × 50 次 put 相同 key：容量恒为 1、无结构损坏"""
        cache = LLMCache(max_size=10, ttl_seconds=3600)
        n_threads, per = 100, 50
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker():
            try:
                barrier.wait()
                for _ in range(per):
                    cache.put("same_prompt", "response")
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(cache.cache) == 1
        # cache key 为 prompt 的 sha256 哈希（_hash_prompt），非原文
        key = cache._hash_prompt("same_prompt")
        assert cache.cache[key].response == "response"

    def test_concurrent_get_hit_count_precise(self):
        """预置 key 后 100 线程 × 50 次 get：命中计数精确无丢失"""
        cache = LLMCache(max_size=100, ttl_seconds=3600)
        cache.put("hello world", "greeting_response")
        n_threads, per = 100, 50
        total = n_threads * per
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker():
            try:
                barrier.wait()
                for _ in range(per):
                    assert cache.get("hello world") == "greeting_response"
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert cache.stats.hits == total                 # 命中计数无丢失
        assert cache.stats.misses == 0
        top = cache.get_top_patterns()
        assert sum(count for _, count in top) == total   # pattern 分布精确

    def test_concurrent_readers_and_writers_no_crash(self):
        """get/put/clear/get_top_patterns 混合并发：不抛异常、计数守恒"""
        cache = LLMCache(max_size=100, ttl_seconds=3600)
        for i in range(20):
            cache.put(f"seed_{i}", f"resp_{i}")
        stop = threading.Event()
        errors = []

        def writer(tid):
            try:
                for i in range(50):
                    cache.put(f"w_{tid}_{i}", f"r_{tid}_{i}")
                    if i % 10 == 0:
                        cache.get(f"w_{tid}_{i - 5 if i >= 5 else 0}")
            except Exception as e:  # pragma: no cover
                errors.append(e)

        def reader(_):
            try:
                while not stop.is_set():
                    cache.get("seed_0")
                    cache.get_top_patterns(3)
                    cache.get_stats()
            except Exception as e:  # pragma: no cover
                errors.append(e)

        writers = [threading.Thread(target=writer, args=(t,)) for t in range(4)]
        readers = [threading.Thread(target=reader, args=(t,)) for t in range(4)]
        for t in writers + readers:
            t.start()
        for t in writers:
            t.join()
        stop.set()
        for t in readers:
            t.join()

        assert not errors, f"读写并发不应抛异常: {errors}"
        assert len(cache.cache) <= cache.max_size
        # put 守恒：预置 20 + 写入 200，留存 = 总写入 - 逐出（无丢失写入）
        assert len(cache.cache) == 20 + 200 - cache.stats.evictions
