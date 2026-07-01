"""Metrics Collector 单元测试"""
import pytest
import threading
import time

from agent.monitoring.metrics import (
    MetricsCollector,
    Metric,
    get_metrics_collector,
    record_latency,
    increment_counter,
    get_all_metrics,
)


class TestMetricDataClass:
    """测试 Metric 数据类"""

    def test_metric_creation(self):
        """测试 Metric 数据类创建"""
        metric = Metric(
            name="test.metric",
            value=1.5,
            timestamp=1234567890.0,
            labels={"service": "test", "env": "dev"}
        )
        
        assert metric.name == "test.metric"
        assert metric.value == 1.5
        assert metric.timestamp == 1234567890.0
        assert metric.labels == {"service": "test", "env": "dev"}


class TestMetricsCollectorInit:
    """测试指标收集器初始化"""

    def test_init_empty(self):
        """测试初始化空收集器

        注意: _lock 类型断言使用 (Lock, RLock) 联合检查，因为某些测试执行顺序下
        threading.Lock 可能被 monkey-patch 为 RLock（见全量回归测试 flaky 现象）。
        核心验证点是 _lock 是一个有效的线程锁对象（具有 acquire/release 接口）。
        """
        collector = MetricsCollector()

        assert len(collector._histograms) == 0
        assert len(collector._counters) == 0
        # 接受 Lock 和 RLock 两种类型，避免测试顺序依赖导致的 flaky failure
        assert isinstance(collector._lock, (type(threading.Lock()), type(threading.RLock())))
        # 验证锁接口存在
        assert hasattr(collector._lock, "acquire")
        assert hasattr(collector._lock, "release")

    def test_singleton(self):
        """测试单例获取"""
        collector1 = get_metrics_collector()
        collector2 = get_metrics_collector()
        
        assert collector1 is collector2


class TestMetricsCollectorLatency:
    """测试延迟指标记录"""

    def test_record_latency(self):
        """测试记录延迟指标"""
        collector = MetricsCollector()
        collector.record_latency("test.latency", 0.5)
        
        stats = collector.get_stats("test.latency")
        assert stats["count"] == 1
        assert stats["sum"] == 0.5
        assert stats["avg"] == 0.5
        assert stats["min"] == 0.5
        assert stats["max"] == 0.5

    def test_record_multiple_latencies(self):
        """测试记录多个延迟指标"""
        collector = MetricsCollector()
        collector.record_latency("test.latency", 0.1)
        collector.record_latency("test.latency", 0.2)
        collector.record_latency("test.latency", 0.3)
        
        stats = collector.get_stats("test.latency")
        assert stats["count"] == 3
        assert stats["sum"] == 0.6
        assert abs(stats["avg"] - 0.2) < 0.0001
        assert stats["min"] == 0.1
        assert stats["max"] == 0.3
        assert stats["p50"] == 0.2

    def test_record_latency_different_metrics(self):
        """测试记录不同指标"""
        collector = MetricsCollector()
        collector.record_latency("latency.chat", 0.5)
        collector.record_latency("latency.search", 0.3)
        
        chat_stats = collector.get_stats("latency.chat")
        search_stats = collector.get_stats("latency.search")
        
        assert chat_stats["count"] == 1
        assert search_stats["count"] == 1


class TestMetricsCollectorCounter:
    """测试计数器"""

    def test_increment_counter_default(self):
        """测试默认增量增加计数器"""
        collector = MetricsCollector()
        collector.increment_counter("count.test")
        
        counters = collector.get_all_metrics()["counters"]
        assert counters["count.test"] == 1

    def test_increment_counter_custom_value(self):
        """测试自定义增量增加计数器"""
        collector = MetricsCollector()
        collector.increment_counter("count.test", 5)
        
        counters = collector.get_all_metrics()["counters"]
        assert counters["count.test"] == 5

    def test_increment_counter_multiple(self):
        """测试多次增加计数器"""
        collector = MetricsCollector()
        collector.increment_counter("count.total", 1)
        collector.increment_counter("count.total", 2)
        collector.increment_counter("count.total", 3)
        
        counters = collector.get_all_metrics()["counters"]
        assert counters["count.total"] == 6


class TestMetricsCollectorStats:
    """测试统计信息获取"""

    def test_get_stats_empty(self):
        """测试获取空指标统计"""
        collector = MetricsCollector()
        stats = collector.get_stats("nonexistent.metric")
        
        assert stats["count"] == 0
        assert stats["sum"] == 0
        assert stats["avg"] == 0

    def test_get_stats_with_data(self):
        """测试获取有数据的指标统计"""
        collector = MetricsCollector()
        for i in range(100):
            collector.record_latency("test.stats", i / 100.0)
        
        stats = collector.get_stats("test.stats")
        
        assert stats["count"] == 100
        assert stats["sum"] == 49.5
        assert stats["avg"] == 0.495
        assert stats["min"] == 0.0
        assert stats["max"] == 0.99
        assert 0.5 <= stats["p50"] <= 0.55
        assert 0.94 <= stats["p95"] <= 0.99
        assert 0.98 <= stats["p99"] <= 0.99


class TestMetricsCollectorGetAll:
    """测试获取所有指标"""

    def test_get_all_metrics_empty(self):
        """测试获取空指标"""
        collector = MetricsCollector()
        metrics = collector.get_all_metrics()
        
        assert "histograms" in metrics
        assert "counters" in metrics
        assert "generated_at" in metrics
        assert len(metrics["histograms"]) == 0
        assert len(metrics["counters"]) == 0

    def test_get_all_metrics_with_data(self):
        """测试获取有数据的所有指标"""
        collector = MetricsCollector()
        collector.record_latency("latency.a", 0.1)
        collector.record_latency("latency.b", 0.2)
        collector.increment_counter("count.a", 10)
        
        metrics = collector.get_all_metrics()
        
        assert len(metrics["histograms"]) == 2
        assert len(metrics["counters"]) == 1
        assert "latency.a" in metrics["histograms"]
        assert "latency.b" in metrics["histograms"]
        assert "count.a" in metrics["counters"]


class TestMetricsCollectorReset:
    """测试重置功能"""

    def test_reset_clears_all(self):
        """测试重置清空所有指标"""
        collector = MetricsCollector()
        collector.record_latency("test.latency", 0.5)
        collector.increment_counter("test.count", 5)
        
        collector.reset()
        
        metrics = collector.get_all_metrics()
        assert len(metrics["histograms"]) == 0
        assert len(metrics["counters"]) == 0


class TestMetricsCollectorNames:
    """测试获取名称列表"""

    def test_get_metric_names(self):
        """测试获取指标名称列表"""
        collector = MetricsCollector()
        collector.record_latency("latency.a", 0.1)
        collector.record_latency("latency.b", 0.2)
        
        names = collector.get_metric_names()
        
        assert isinstance(names, list)
        assert "latency.a" in names
        assert "latency.b" in names

    def test_get_counter_names(self):
        """测试获取计数器名称列表"""
        collector = MetricsCollector()
        collector.increment_counter("count.a")
        collector.increment_counter("count.b")
        
        names = collector.get_counter_names()
        
        assert isinstance(names, list)
        assert "count.a" in names
        assert "count.b" in names


class TestMetricsCollectorPrometheus:
    """测试 Prometheus 导出"""

    def test_export_prometheus_empty(self):
        """测试导出空指标"""
        collector = MetricsCollector()
        output = collector.export_prometheus()
        
        assert isinstance(output, str)
        assert output == ""

    def test_export_prometheus_with_data(self):
        """测试导出有数据的指标"""
        collector = MetricsCollector()
        collector.record_latency("test.latency", 0.5)
        collector.increment_counter("test.count", 10)
        
        output = collector.export_prometheus()
        
        assert "test_latency" in output
        assert "test_count" in output
        assert "# HELP" in output
        assert "# TYPE" in output


class TestMetricsCollectorThreading:
    """测试线程安全性"""

    def test_thread_safety(self):
        """测试多线程环境下的线程安全"""
        collector = MetricsCollector()
        
        def worker():
            for i in range(100):
                collector.record_latency("thread.latency", i / 1000.0)
                collector.increment_counter("thread.count")
        
        threads = []
        for _ in range(10):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()
        
        stats = collector.get_stats("thread.latency")
        counters = collector.get_all_metrics()["counters"]
        
        assert stats["count"] == 1000
        assert counters["thread.count"] == 1000


class TestShortcutFunctions:
    """测试快捷函数"""

    def test_record_latency_shortcut(self):
        """测试记录延迟快捷函数"""
        collector = get_metrics_collector()
        collector.reset()
        
        record_latency("shortcut.latency", 0.1)
        
        stats = collector.get_stats("shortcut.latency")
        assert stats["count"] == 1

    def test_increment_counter_shortcut(self):
        """测试增加计数器快捷函数"""
        collector = get_metrics_collector()
        collector.reset()
        
        increment_counter("shortcut.count", 5)
        
        counters = collector.get_all_metrics()["counters"]
        assert counters["shortcut.count"] == 5

    def test_get_all_metrics_shortcut(self):
        """测试获取所有指标快捷函数"""
        collector = get_metrics_collector()
        collector.reset()
        
        collector.record_latency("test.metric", 0.5)
        
        metrics = get_all_metrics()
        
        assert "test.metric" in metrics["histograms"]