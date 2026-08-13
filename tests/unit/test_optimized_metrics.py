"""优化指标采集模块并发安全测试。

覆盖 LockFreeCounter / LockFreeHistogram 的并发正确性：
- 修复前：注释断言「Python 中 GIL 保证 += 原子性」系误判——`+=` 是
  读-改-写序列（三条字节码），GIL 只在字节码边界切换，并发下会丢更新。
- 修复后：threading.Lock 保护读-改-写，计数精确；min/max 走锁内 set，
  不绕过封装直接写 _value。
"""

import threading

import pytest

from agent.monitoring.optimized_metrics import LockFreeCounter, LockFreeHistogram


class TestLockFreeCounterConcurrency:
    """LockFreeCounter 并发计数精确性。"""

    def test_concurrent_increment_no_lost_update(self):
        """100 线程 × 100 次并发 increment：最终计数精确无丢失"""
        counter = LockFreeCounter()
        n_threads, per = 100, 100
        total = n_threads * per
        barrier = threading.Barrier(n_threads)  # 同步起跑，放大读-改-写竞争
        errors = []

        def worker():
            try:
                barrier.wait()
                for _ in range(per):
                    counter.increment()
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert counter.get() == total  # 读-改-写序列锁保护后无丢失更新

    def test_concurrent_increment_with_delta(self):
        """并发带增量 increment：总和精确"""
        counter = LockFreeCounter()
        n_threads, per = 50, 40
        barrier = threading.Barrier(n_threads)

        def worker():
            barrier.wait()
            for i in range(per):
                counter.increment(i + 1)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        expected = n_threads * sum(range(1, per + 1))
        assert counter.get() == expected

    def test_concurrent_reset_returns_zero(self):
        """并发 reset：不抛异常，最终值恒为 0"""
        counter = LockFreeCounter()
        counter.increment(10)
        n_threads = 50
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker():
            try:
                barrier.wait()
                counter.reset()
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert counter.get() == 0

    def test_concurrent_get_and_increment_no_error(self):
        """并发 get 与 increment 混合：不抛异常、计数仍精确"""
        counter = LockFreeCounter()
        n_threads, per = 20, 50
        total = n_threads * per
        stop = threading.Event()
        errors = []

        def incrementer():
            try:
                for _ in range(per):
                    counter.increment()
            except Exception as e:  # pragma: no cover
                errors.append(e)

        def reader():
            try:
                while not stop.is_set():
                    assert isinstance(counter.get(), int)
            except Exception as e:  # pragma: no cover
                errors.append(e)

        writers = [threading.Thread(target=incrementer) for _ in range(n_threads)]
        readers = [threading.Thread(target=reader) for _ in range(4)]
        for t in writers + readers:
            t.start()
        for t in writers:
            t.join()
        stop.set()
        for t in readers:
            t.join()

        assert not errors
        assert counter.get() == total


class TestLockFreeHistogramConcurrency:
    """LockFreeHistogram 并发 record 统计精确性。"""

    def test_concurrent_record_count_and_sum(self):
        """并发 record：count/sum/桶计数精确，min/max 正确"""
        hist = LockFreeHistogram(buckets=[100, 500, 1000])
        n_threads, per = 30, 50
        total = n_threads * per
        values = list(range(1, per + 1))  # 1..50µs，落在第 0 桶（<=100）
        expected_sum = n_threads * sum(values)
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker():
            try:
                barrier.wait()
                for v in values:
                    hist.record(v)
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        stats = hist.get_stats()
        assert stats["count"] == total                 # count 计数精确
        assert stats["sum"] == expected_sum             # sum 累加精确
        assert stats["min"] == min(values)              # min 正确
        assert stats["max"] == max(values)              # max 正确
        assert sum(b["count"] for b in stats["buckets"]) == total  # 桶计数无丢失

    def test_concurrent_mixed_values_bucket_distribution(self):
        """并发 record 混合桶边界值：各桶计数与总数一致"""
        hist = LockFreeHistogram(buckets=[100, 500, 1000])
        # 分布在 4 个桶：50(<=100)、300(<=500)、800(<=1000)、2000(>1000)
        values = [50, 300, 800, 2000]
        n_threads, rounds = 20, 30
        total = n_threads * rounds * len(values)
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker():
            try:
                barrier.wait()
                for _ in range(rounds):
                    for v in values:
                        hist.record(v)
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        stats = hist.get_stats()
        assert stats["count"] == total
        bucket_counts = [b["count"] for b in stats["buckets"]]
        assert bucket_counts == [
            n_threads * rounds,  # <=100 桶
            n_threads * rounds,  # <=500 桶
            n_threads * rounds,  # <=1000 桶
            n_threads * rounds,  # >1000 桶
        ]
        assert stats["min"] == 50
        assert stats["max"] == 2000
