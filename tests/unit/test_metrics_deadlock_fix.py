"""MetricsCollector 死锁修复验证测试

验证将 threading.Lock 改为 threading.RLock 后，以下场景不再死锁：
1. get_all_metrics → get_stats 重入调用（同线程）
2. 多线程并发 record_latency + get_stats
3. 多线程并发 get_all_metrics + record_latency
4. 持锁时直接调用 get_stats（RLock 可重入）

历史背景：
- 原 Lock 不可重入，get_all_metrics 必须先释放锁再调用 get_stats
- 改用 RLock 后，即使持锁时调用 get_stats 也不会死锁
- 本测试同时验证「锁内快照 + 锁外计算」的优化模式仍正常工作
"""
import threading
import time
import pytest

from agent.monitoring.metrics import MetricsCollector, get_metrics_collector


class TestRLockType:
    """验证锁类型为 RLock"""

    def test_lock_is_rlock(self):
        """_lock 应为 threading.RLock 实例（可重入锁）"""
        collector = MetricsCollector()
        # RLock 实例可通过 _is_owned 方法识别（Lock 没有此方法）
        assert hasattr(collector._lock, "_is_owned"), \
            "_lock 应为 RLock（含 _is_owned 方法），当前可能仍为不可重入 Lock"
        assert callable(getattr(collector._lock, "acquire", None))
        assert callable(getattr(collector._lock, "release", None))

    def test_global_collector_uses_rlock(self):
        """全局单例也应使用 RLock"""
        collector = get_metrics_collector()
        assert hasattr(collector._lock, "_is_owned"), \
            "全局 _global_collector._lock 应为 RLock"


class TestReentrantAccess:
    """验证同线程重入不再死锁"""

    def test_get_stats_while_holding_lock(self):
        """持锁时直接调用 get_stats 不应死锁（RLock 可重入）

        旧 Lock 行为：永久阻塞（get_stats 内 with self._lock 再次获取锁失败）
        新 RLock 行为：同线程可重入，立即返回
        """
        collector = MetricsCollector()
        collector.record_latency("test.metric", 0.5)

        # 模拟「持锁时调用 get_stats」的重入场景
        result = {}

        def call_get_stats_under_lock():
            with collector._lock:
                # 此时已持有锁，调用 get_stats 会再次 acquire
                # RLock 允许同线程重入，不会死锁
                result["stats"] = collector.get_stats("test.metric")

        thread = threading.Thread(target=call_get_stats_under_lock)
        thread.daemon = True
        thread.start()
        thread.join(timeout=5.0)

        assert not thread.is_alive(), \
            "线程超时未完成 — RLock 重入死锁未修复"
        assert result["stats"]["count"] == 1
        assert result["stats"]["sum"] == 0.5

    def test_get_all_metrics_internal_get_stats_no_deadlock(self):
        """get_all_metrics 内部调用 get_stats 不应死锁

        验证：即使 get_all_metrics 在锁内直接调用 get_stats（而非锁外），
        RLock 也能正确处理重入。
        """
        collector = MetricsCollector()
        for i in range(10):
            collector.record_latency("latency.test", i * 0.1)
        collector.increment_counter("count.test", 5)

        # 模拟「持锁时调用 get_all_metrics」的极端重入场景
        result = {}

        def call_get_all_metrics_under_lock():
            with collector._lock:
                result["metrics"] = collector.get_all_metrics()

        thread = threading.Thread(target=call_get_all_metrics_under_lock)
        thread.daemon = True
        thread.start()
        thread.join(timeout=5.0)

        assert not thread.is_alive(), "get_all_metrics 重入死锁"
        assert "latency.test" in result["metrics"]["histograms"]
        assert result["metrics"]["counters"]["count.test"] == 5


class TestConcurrentRecordAndGet:
    """验证多线程并发读写不死锁"""

    def test_concurrent_record_and_get_stats(self):
        """多线程并发 record_latency + get_stats 不应死锁

        模拟生产环境：后台线程持续 record_latency，
        主线程同时调用 get_stats 读取统计。
        """
        collector = MetricsCollector()
        stop_event = threading.Event()
        errors = []

        def writer():
            try:
                i = 0
                while not stop_event.is_set():
                    collector.record_latency("concurrent.metric", i * 0.01)
                    i += 1
                    time.sleep(0.001)
            except Exception as e:
                errors.append(("writer", e))

        def reader():
            try:
                for _ in range(50):
                    collector.get_stats("concurrent.metric")
                    time.sleep(0.002)
            except Exception as e:
                errors.append(("reader", e))

        writer_thread = threading.Thread(target=writer, daemon=True)
        reader_thread = threading.Thread(target=reader, daemon=True)
        writer_thread.start()
        reader_thread.start()

        reader_thread.join(timeout=10.0)
        stop_event.set()
        writer_thread.join(timeout=5.0)

        assert not reader_thread.is_alive(), "reader 线程超时 — 可能死锁"
        assert not writer_thread.is_alive(), "writer 线程未正常退出"
        assert not errors, f"并发过程出现异常: {errors}"

    def test_concurrent_get_all_metrics_and_record(self):
        """多线程并发 get_all_metrics + record 不应死锁"""
        collector = MetricsCollector()
        for i in range(100):
            collector.record_latency("baseline.latency", i * 0.01)

        stop_event = threading.Event()
        errors = []

        def recorder():
            try:
                i = 0
                while not stop_event.is_set():
                    collector.record_latency("baseline.latency", i * 0.001)
                    collector.increment_counter("baseline.count")
                    i += 1
                    time.sleep(0.001)
            except Exception as e:
                errors.append(("recorder", e))

        def getter():
            try:
                for _ in range(20):
                    metrics = collector.get_all_metrics()
                    assert "histograms" in metrics
                    assert "counters" in metrics
                    time.sleep(0.005)
            except Exception as e:
                errors.append(("getter", e))

        recorder_thread = threading.Thread(target=recorder, daemon=True)
        getter_thread = threading.Thread(target=getter, daemon=True)
        recorder_thread.start()
        getter_thread.start()

        getter_thread.join(timeout=15.0)
        stop_event.set()
        recorder_thread.join(timeout=5.0)

        assert not getter_thread.is_alive(), "getter 线程超时 — get_all_metrics 可能死锁"
        assert not errors, f"并发过程出现异常: {errors}"


class TestStressConcurrency:
    """压力测试：高并发场景验证"""

    def test_high_concurrency_no_deadlock(self):
        """10 线程并发混合读写，5 秒内应全部完成"""
        collector = MetricsCollector()
        stop_event = threading.Event()
        errors = []

        def mixed_worker(worker_id):
            try:
                for i in range(100):
                    collector.record_latency(f"metric.{worker_id}", i * 0.01)
                    collector.increment_counter(f"counter.{worker_id}")
                    if i % 10 == 0:
                        collector.get_stats(f"metric.{worker_id}")
                    if i % 20 == 0:
                        collector.get_all_metrics()
            except Exception as e:
                errors.append((worker_id, e))

        threads = []
        for wid in range(10):
            t = threading.Thread(target=mixed_worker, args=(wid,), daemon=True)
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=10.0)

        for idx, t in enumerate(threads):
            assert not t.is_alive(), f"线程 {idx} 超时 — 死锁可能"

        assert not errors, f"压力测试出现异常: {errors}"

        # 验证最终数据一致性
        metrics = collector.get_all_metrics()
        total_histograms = len(metrics["histograms"])
        total_counters = len(metrics["counters"])
        assert total_histograms == 10, f"应有 10 个 histogram，实际 {total_histograms}"
        assert total_counters == 10, f"应有 10 个 counter，实际 {total_counters}"

    def test_get_all_metrics_with_many_histograms(self):
        """get_all_metrics 在大量 histogram 时不死锁

        场景：100 个 histogram，每个 1000 个样本，
        get_all_metrics 会调用 100 次 get_stats（每次都获取锁）。
        RLock 应确保此场景不阻塞过久。
        """
        collector = MetricsCollector()
        for h in range(100):
            for i in range(100):
                collector.record_latency(f"hist.{h}", i * 0.001)

        result = {}

        def call_get_all_metrics():
            result["metrics"] = collector.get_all_metrics()

        thread = threading.Thread(target=call_get_all_metrics, daemon=True)
        thread.start()
        thread.join(timeout=10.0)

        assert not thread.is_alive(), "get_all_metrics 在大量数据下超时"
        assert len(result["metrics"]["histograms"]) == 100


class TestBackwardCompatibility:
    """验证 RLock 改造不影响现有功能"""

    def test_record_and_get_stats(self):
        """基本 record + get_stats 流程正常"""
        collector = MetricsCollector()
        collector.record_latency("compat.metric", 1.5)
        stats = collector.get_stats("compat.metric")
        assert stats["count"] == 1
        assert stats["max"] == 1.5

    def test_increment_counter(self):
        """计数器累加正常"""
        collector = MetricsCollector()
        collector.increment_counter("compat.count", 10)
        collector.increment_counter("compat.count", 5)
        counters = collector.get_all_metrics()["counters"]
        assert counters["compat.count"] == 15

    def test_reset_clears_all(self):
        """reset 清空所有指标"""
        collector = MetricsCollector()
        collector.record_latency("reset.latency", 0.5)
        collector.increment_counter("reset.count")
        collector.reset()
        metrics = collector.get_all_metrics()
        assert len(metrics["histograms"]) == 0
        assert len(metrics["counters"]) == 0

    def test_export_prometheus(self):
        """Prometheus 导出格式正常"""
        collector = MetricsCollector()
        collector.record_latency("export.latency", 0.5)
        collector.increment_counter("export.count", 10)
        output = collector.export_prometheus()
        assert "export_latency" in output
        assert "# HELP" in output
        assert "# TYPE" in output

    def test_singleton_unchanged(self):
        """全局单例行为不变"""
        c1 = get_metrics_collector()
        c2 = get_metrics_collector()
        assert c1 is c2
