"""optimized_metrics 模块并发安全测试

验证锁化修复后的高并发场景（用户点名 _stats 计数）：
1. 并发 increment_counter 采样路径：_stats['sampled_records'] 精确
2. 并发 record_latency 非采样路径：_stats['direct_records'] 与直方图 count 精确
3. 并发读写混合 get_stats/get_all_metrics/get_internal_stats 无 RuntimeError
4. 并发 init_batch_writer 仅创建一个 writer
"""

import threading
from unittest.mock import Mock

from agent.monitoring.optimized_metrics import (
    OptimizedMetricsCollector,
    SampledMetricsCollector,
    BatchMetricsWriter,
    _global_optimized_collector,
)


class TestOptimizedMetricsConcurrency:
    """优化指标模块并发安全测试"""

    N_THREADS = 16

    @staticmethod
    def _run_threads(target, args_list):
        """Barrier 同步起跑，放大竞争窗口"""
        barrier = threading.Barrier(len(args_list))
        results = []
        errors = []

        def worker(arg):
            barrier.wait()
            try:
                results.append(target(arg))
            except Exception as e:  # noqa: BLE001 - 收集所有异常统一断言
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(a,)) for a in args_list]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results, errors

    def test_concurrent_increment_sampled_stats_exact(self):
        """并发采样路径 increment_counter：sampled_records 精确、计数器值精确"""
        collector = OptimizedMetricsCollector(sampling_enabled=True, sample_rate=1.0)
        results, errors = self._run_threads(
            lambda i: collector.increment_counter("http_requests"),
            list(range(self.N_THREADS)),
        )
        assert not errors, f"并发 increment_counter 抛异常: {errors}"
        stats = collector.get_internal_stats()
        assert stats["sampled_records"] == self.N_THREADS, (
            f"sampled_records 应为 {self.N_THREADS}，实际 {stats['sampled_records']}"
        )
        sampler_stats = collector.get_stats()
        assert sampler_stats["counters"]["http_requests"] == self.N_THREADS

    def test_concurrent_record_latency_direct_stats_exact(self):
        """并发非采样路径 record_latency：direct_records 与直方图 count 精确"""
        collector = OptimizedMetricsCollector(sampling_enabled=False)
        results, errors = self._run_threads(
            lambda i: collector.record_latency("llm_latency", 0.05 + i * 0.01),
            list(range(self.N_THREADS)),
        )
        assert not errors, f"并发 record_latency 抛异常: {errors}"
        stats = collector.get_internal_stats()
        assert stats["direct_records"] == self.N_THREADS, (
            f"direct_records 应为 {self.N_THREADS}，实际 {stats['direct_records']}"
        )
        hist = collector.get_stats("llm_latency")
        assert hist["count"] == self.N_THREADS, (
            f"直方图 count 应为 {self.N_THREADS}，实际 {hist['count']}"
        )

    def test_concurrent_read_write_mix_no_runtime_error(self):
        """并发读写混合（record + get_stats + get_all_metrics + get_internal_stats）"""
        collector = OptimizedMetricsCollector(sampling_enabled=False)

        def writer(i):
            collector.increment_counter("mix_counter", 1)
            collector.record_latency("mix_latency", 0.1 + i * 0.001)

        def reader(i):
            collector.get_stats()
            collector.get_stats("mix_latency")
            collector.get_all_metrics()
            collector.get_internal_stats()

        barrier = threading.Barrier(self.N_THREADS)
        errors = []

        def worker(fn, arg):
            barrier.wait()
            try:
                fn(arg)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = []
        for i in range(self.N_THREADS):
            fn = writer if i % 2 == 0 else reader
            threads.append(threading.Thread(target=worker, args=(fn, i)))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"并发读写混合抛异常: {errors}"
        assert collector.get_internal_stats()["direct_records"] == self.N_THREADS

    def test_concurrent_init_batch_writer_single(self):
        """并发 init_batch_writer 仅创建一个 writer（check-then-create 原子）"""
        collector = OptimizedMetricsCollector(sampling_enabled=True)
        write_func = Mock()
        results, errors = self._run_threads(
            lambda i: collector.init_batch_writer(write_func, batch_size=100),
            list(range(8)),
        )
        assert not errors, f"并发 init_batch_writer 抛异常: {errors}"
        writer = collector._batch_writer
        assert writer is not None
        assert isinstance(writer, BatchMetricsWriter)
        writer.stop(timeout=1.0)

    def test_concurrent_sampler_histogram_toctou(self):
        """并发 record_latency 同一 metric：直方图仅一个实例、计数精确"""
        sampler = SampledMetricsCollector(sample_rate=1.0)
        results, errors = self._run_threads(
            lambda i: sampler.record_latency("same_metric", 10 + i),
            list(range(self.N_THREADS)),
        )
        assert not errors, f"并发 sampler record_latency 抛异常: {errors}"
        stats = sampler.get_stats()
        hist = stats["histograms"]["same_metric"]
        assert hist["count"] == self.N_THREADS, (
            f"直方图 count 应为 {self.N_THREADS}，实际 {hist['count']}"
        )
