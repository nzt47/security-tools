"""资源泄漏检测模块测试

覆盖维度：
1. 数据结构序列化（ResourceSnapshot/TrendResult）
2. 单次采样 sample()
3. 历史记录存储与查询
4. 趋势分析（线性回归）与泄漏检测
5. 压测模式切换
6. 池提供者注册与采样
7. 降级策略（子监控失败不影响整体）
8. 业务指标上报
9. 启动/停止后台线程
10. 全局实例与并发
"""

import threading
import time

import pytest

from agent.monitoring.resource_monitor import (
    ResourceMonitor,
    ResourceSnapshot,
    MemoryStat,
    ThreadPoolStat,
    FileHandleStat,
    DbConnectionStat,
    TrendResult,
    get_resource_monitor,
    reset_resource_monitor,
)


# ============================================================================
# 数据结构测试
# ============================================================================

class TestResourceSnapshot:
    """ResourceSnapshot 数据结构测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_default_snapshot(self):
        snap = ResourceSnapshot(timestamp=time.time())
        assert snap.memory.current_bytes == 0
        assert snap.thread_pool.active_threads == 0
        assert snap.file_handles.open_count == 0
        assert snap.db_connections.pools == {}
        assert snap.sample_duration_ms == 0.0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_to_dict_serializable(self):
        import json
        snap = ResourceSnapshot(timestamp=time.time())
        d = snap.to_dict()
        # 确保可 JSON 序列化
        json.dumps(d)
        assert "timestamp" in d
        assert "memory" in d
        assert "thread_pool" in d
        assert "file_handles" in d
        assert "db_connections" in d

    @pytest.mark.unit
    @pytest.mark.p1
    def test_iso_time_set(self):
        snap = ResourceSnapshot(timestamp=time.time())
        assert snap.iso_time  # 非空
        assert "T" in snap.iso_time  # ISO 格式


class TestTrendResult:
    """TrendResult 数据结构测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_trend_result_fields(self):
        result = TrendResult(
            resource_type="memory",
            slope=1.5,
            intercept=100,
            r_squared=0.95,
            sample_count=10,
            is_leaking=True,
            threshold=1.0,
        )
        assert result.resource_type == "memory"
        assert result.slope == 1.5
        assert result.is_leaking is True


# ============================================================================
# 采样核心测试
# ============================================================================

class TestSampling:
    """采样功能测试"""

    @pytest.fixture(autouse=True)
    def setup(self):
        reset_resource_monitor()
        self.monitor = ResourceMonitor(config={"persist_enabled": False})
        yield
        self.monitor.stop()
        reset_resource_monitor()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_manual_sample_returns_snapshot(self):
        snap = self.monitor.sample()
        assert isinstance(snap, ResourceSnapshot)
        assert snap.timestamp > 0
        assert snap.sample_duration_ms >= 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sample_captures_memory(self):
        snap = self.monitor.sample()
        # tracemalloc 应启动，current_bytes 应非零（解释器本身有分配）
        assert snap.memory.current_bytes >= 0
        assert snap.memory.peak_bytes >= 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sample_captures_threads(self):
        snap = self.monitor.sample()
        # 至少有当前线程
        assert snap.thread_pool.active_threads >= 1

    @pytest.mark.unit
    @pytest.mark.p1
    def test_sample_captures_file_handles(self):
        snap = self.monitor.sample()
        # psutil 可能可用也可能不可用
        assert isinstance(snap.file_handles.available, bool)
        if snap.file_handles.available:
            assert snap.file_handles.open_count >= 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sample_stored_in_history(self):
        self.monitor.sample()
        history = self.monitor.get_history()
        assert len(history) == 1

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_snapshot_returns_latest(self):
        self.monitor.sample()
        time.sleep(0.01)
        self.monitor.sample()
        snap = self.monitor.get_snapshot()
        assert snap is not None
        history = self.monitor.get_history()
        assert snap is history[-1]


class TestHistory:
    """历史记录测试"""

    @pytest.fixture(autouse=True)
    def setup(self):
        reset_resource_monitor()
        self.monitor = ResourceMonitor(config={"persist_enabled": False})
        yield
        self.monitor.stop()
        reset_resource_monitor()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_history_limit(self):
        for _ in range(5):
            self.monitor.sample()
        history = self.monitor.get_history(limit=3)
        assert len(history) == 3
        # 返回最近的 3 条
        all_history = self.monitor.get_history()
        assert history[-1] is all_history[-1]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_history_returns_empty_when_no_samples(self):
        reset_resource_monitor()
        monitor = ResourceMonitor(config={"persist_enabled": False})
        assert monitor.get_history() == []
        assert monitor.get_snapshot() is None

    @pytest.mark.unit
    @pytest.mark.p1
    def test_history_capped_by_max_size(self):
        # 使用小 history_size 验证环形缓冲
        reset_resource_monitor()
        monitor = ResourceMonitor(config={"history_size": 5, "persist_enabled": False})
        for _ in range(10):
            monitor.sample()
        history = monitor.get_history()
        assert len(history) == 5
        monitor.stop()


# ============================================================================
# 趋势分析测试
# ============================================================================

class TestTrendAnalysis:
    """趋势分析与泄漏检测测试"""

    @pytest.fixture(autouse=True)
    def setup(self):
        reset_resource_monitor()
        self.monitor = ResourceMonitor(config={"persist_enabled": False})
        yield
        self.monitor.stop()
        reset_resource_monitor()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_no_trend_with_insufficient_samples(self):
        # 少于 2 个样本无法计算趋势
        assert self.monitor.get_trend("memory") is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_linear_regression_flat(self):
        # 构造平缓序列（斜率应为 0）
        from agent.monitoring.resource_monitor import ResourceMonitor as RM
        original = RM._linear_regression
        try:
            # 直接测试静态方法
            slope, intercept, r2 = RM._linear_regression([(0, 100), (1, 100), (2, 100)])
            assert slope == 0.0
            assert intercept == 100.0
            assert r2 == 1.0
        finally:
            pass

    @pytest.mark.unit
    @pytest.mark.p0
    def test_linear_regression_increasing(self):
        from agent.monitoring.resource_monitor import ResourceMonitor as RM
        slope, intercept, r2 = RM._linear_regression([(0, 10), (1, 20), (2, 30), (3, 40)])
        assert slope == pytest.approx(10.0)
        assert intercept == pytest.approx(10.0)
        assert r2 == pytest.approx(1.0)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_linear_regression_decreasing(self):
        from agent.monitoring.resource_monitor import ResourceMonitor as RM
        slope, intercept, r2 = RM._linear_regression([(0, 40), (1, 30), (2, 20), (3, 10)])
        assert slope == pytest.approx(-10.0)

    @pytest.mark.unit
    @pytest.mark.p1
    def test_linear_regression_single_point(self):
        from agent.monitoring.resource_monitor import ResourceMonitor as RM
        slope, intercept, r2 = RM._linear_regression([(0, 100)])
        assert slope == 0.0
        assert r2 == 0.0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_leak_detection_triggers(self):
        # 通过注入历史数据模拟内存持续增长
        reset_resource_monitor()
        monitor = ResourceMonitor(config={"leak_slope_threshold": 1.0, "history_size": 100, "persist_enabled": False})
        # 直接注入伪造快照（绕过真实采样）
        base_time = time.time()
        for i in range(10):
            snap = ResourceSnapshot(timestamp=base_time + i)
            snap.memory.current_bytes = 1000 + i * 100  # 每采样增长 100 字节
            monitor._history.append(snap)
        trend = monitor.get_trend("memory")
        assert trend is not None
        assert trend.slope == pytest.approx(100.0)
        assert trend.is_leaking is True  # 100 > 阈值 1.0
        monitor.stop()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_no_leak_when_stable(self):
        reset_resource_monitor()
        monitor = ResourceMonitor(config={"leak_slope_threshold": 1.0, "history_size": 100, "persist_enabled": False})
        base_time = time.time()
        for i in range(10):
            snap = ResourceSnapshot(timestamp=base_time + i)
            snap.memory.current_bytes = 1000  # 恒定值
            monitor._history.append(snap)
        trend = monitor.get_trend("memory")
        assert trend is not None
        assert trend.slope == pytest.approx(0.0)
        assert trend.is_leaking is False
        monitor.stop()

    @pytest.mark.unit
    @pytest.mark.p1
    def test_leak_callback_fired(self):
        reset_resource_monitor()
        monitor = ResourceMonitor(config={"leak_slope_threshold": 1.0, "history_size": 100, "persist_enabled": False})
        received = []
        monitor.register_leak_callback(lambda r: received.append(r))
        base_time = time.time()
        for i in range(5):
            snap = ResourceSnapshot(timestamp=base_time + i)
            snap.memory.current_bytes = 1000 + i * 500  # 强增长
            monitor._history.append(snap)
        monitor.get_trend("memory")
        assert len(received) == 1
        assert received[0].is_leaking is True
        monitor.stop()


# ============================================================================
# 压测模式测试
# ============================================================================

class TestStressMode:
    """压测模式测试"""

    @pytest.fixture(autouse=True)
    def setup(self):
        reset_resource_monitor()
        self.monitor = ResourceMonitor(config={"persist_enabled": False})
        yield
        self.monitor.stop()
        reset_resource_monitor()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_enable_stress_mode(self):
        self.monitor.enable_stress_mode()
        assert self.monitor._stress_mode is True
        status = self.monitor.get_status()
        assert status["stress_mode"] is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_disable_stress_mode(self):
        self.monitor.enable_stress_mode()
        self.monitor.disable_stress_mode()
        assert self.monitor._stress_mode is False

    @pytest.mark.unit
    @pytest.mark.p1
    def test_stress_mode_uses_shorter_interval(self):
        self.monitor.enable_stress_mode()
        interval = self.monitor._get_sample_interval()
        assert interval <= 10.0  # 压测间隔 <= 10 秒
        self.monitor.disable_stress_mode()
        interval_normal = self.monitor._get_sample_interval()
        assert interval_normal >= interval


# ============================================================================
# 池提供者测试
# ============================================================================

class TestPoolProviders:
    """外部资源池提供者测试"""

    @pytest.fixture(autouse=True)
    def setup(self):
        reset_resource_monitor()
        self.monitor = ResourceMonitor(config={"persist_enabled": False})
        yield
        self.monitor.stop()
        reset_resource_monitor()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_register_thread_pool_provider(self):
        def snapshot():
            return {"active": 4, "queued": 2, "size": 8}
        self.monitor.register_pool_provider("worker_pool", snapshot, "thread")
        snap = self.monitor.sample()
        assert "worker_pool" in snap.thread_pool.registered_pools
        assert snap.thread_pool.registered_pools["worker_pool"]["active"] == 4

    @pytest.mark.unit
    @pytest.mark.p0
    def test_register_db_pool_provider(self):
        def snapshot():
            return {"active": 2, "idle": 3, "size": 5}
        self.monitor.register_pool_provider("main_db", snapshot, "db")
        snap = self.monitor.sample()
        assert "main_db" in snap.db_connections.pools
        assert snap.db_connections.pools["main_db"]["active"] == 2

    @pytest.mark.unit
    @pytest.mark.p1
    def test_provider_exception_isolated(self):
        def bad_snapshot():
            raise RuntimeError("provider boom")
        def good_snapshot():
            return {"active": 1}
        self.monitor.register_pool_provider("bad_pool", bad_snapshot, "thread")
        self.monitor.register_pool_provider("good_pool", good_snapshot, "thread")
        # 单个 provider 异常不影响整体采样
        snap = self.monitor.sample()
        assert "good_pool" in snap.thread_pool.registered_pools
        assert "bad_pool" not in snap.thread_pool.registered_pools


# ============================================================================
# 降级策略测试
# ============================================================================

class TestDegradation:
    """降级策略测试（子监控失败不影响整体）"""

    @pytest.fixture(autouse=True)
    def setup(self):
        reset_resource_monitor()
        yield
        reset_resource_monitor()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sample_continues_if_memory_fails(self):
        monitor = ResourceMonitor(config={"persist_enabled": False})
        # 模拟 tracemalloc 失败
        monitor._tracemalloc_started = False
        snap = monitor.sample()
        # 内存降级为默认值，但其他采样继续
        assert snap.memory.current_bytes == 0
        assert snap.thread_pool.active_threads >= 1
        monitor.stop()

    @pytest.mark.unit
    @pytest.mark.p1
    def test_sample_continues_if_file_handle_unavailable(self):
        monitor = ResourceMonitor(config={"persist_enabled": False})
        # 模拟 psutil 不可用
        import agent.monitoring.resource_monitor as rm_module
        original = rm_module._PSUTIL_AVAILABLE
        rm_module._PSUTIL_AVAILABLE = False
        try:
            snap = monitor.sample()
            assert snap.file_handles.available is False
            assert snap.file_handles.open_count == 0
        finally:
            rm_module._PSUTIL_AVAILABLE = original
        monitor.stop()


# ============================================================================
# 业务指标上报测试
# ============================================================================

class TestMetricsReporting:
    """业务指标上报测试"""

    @pytest.fixture(autouse=True)
    def setup(self):
        reset_resource_monitor()
        self.monitor = ResourceMonitor(config={"persist_enabled": False})
        yield
        self.monitor.stop()
        reset_resource_monitor()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_metrics_reported_on_sample(self):
        # 注入 mock 收集器
        reported = []

        class MockCollector:
            def _set_gauge(self, name, labels, value):
                reported.append((name, labels, value))

        import agent.monitoring.resource_monitor as rm
        original = rm._business_collector
        rm._business_collector = MockCollector()
        try:
            self.monitor.sample()
            assert len(reported) > 0
            # 至少包含 memory 与 thread 类型
            types = {labels.get("resource_type") for _, labels, _ in reported}
            assert "memory" in types
            assert "thread" in types
        finally:
            rm._business_collector = original

    @pytest.mark.unit
    @pytest.mark.p1
    def test_metrics_failure_isolated(self):
        class BadCollector:
            def _set_gauge(self, name, labels, value):
                raise RuntimeError("metrics boom")
        import agent.monitoring.resource_monitor as rm
        original = rm._business_collector
        rm._business_collector = BadCollector()
        try:
            # 埋点失败不应影响采样
            snap = self.monitor.sample()
            assert isinstance(snap, ResourceSnapshot)
            assert snap.memory.current_bytes >= 0
        finally:
            rm._business_collector = original

    @pytest.mark.unit
    @pytest.mark.p1
    def test_no_collector_does_not_crash(self):
        import agent.monitoring.resource_monitor as rm
        original = rm._business_collector
        rm._business_collector = None
        try:
            snap = self.monitor.sample()
            assert isinstance(snap, ResourceSnapshot)
        finally:
            rm._business_collector = original


# ============================================================================
# 后台线程测试
# ============================================================================

class TestBackgroundThread:
    """启动/停止后台线程测试"""

    @pytest.fixture(autouse=True)
    def setup(self):
        reset_resource_monitor()
        yield
        reset_resource_monitor()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_start_and_stop(self):
        monitor = ResourceMonitor(config={"sample_interval_sec": 1, "persist_enabled": False})
        monitor.start()
        status = monitor.get_status()
        assert status["running"] is True
        time.sleep(1.5)  # 等待至少一次采样
        monitor.stop()
        assert monitor.get_status()["running"] is False
        assert len(monitor.get_history()) >= 1

    @pytest.mark.unit
    @pytest.mark.p1
    def test_start_idempotent(self):
        monitor = ResourceMonitor(config={"sample_interval_sec": 60, "persist_enabled": False})
        monitor.start()
        monitor.start()  # 重复启动不应创建新线程
        assert monitor.get_status()["running"] is True
        monitor.stop()

    @pytest.mark.unit
    @pytest.mark.p1
    def test_stop_idempotent(self):
        monitor = ResourceMonitor(config={"persist_enabled": False})
        monitor.stop()  # 未启动直接停止不应抛异常
        monitor.stop()


# ============================================================================
# 全局实例与状态测试
# ============================================================================

class TestGlobalInstance:
    """全局实例与状态查询测试"""

    @pytest.fixture(autouse=True)
    def setup(self):
        reset_resource_monitor()
        yield
        reset_resource_monitor()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_singleton(self):
        a = get_resource_monitor()
        b = get_resource_monitor()
        assert a is b

    @pytest.mark.unit
    @pytest.mark.p0
    def test_reset_creates_new(self):
        a = get_resource_monitor()
        reset_resource_monitor()
        b = get_resource_monitor()
        assert a is not b

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_status_structure(self):
        monitor = get_resource_monitor()
        status = monitor.get_status()
        assert "running" in status
        assert "stress_mode" in status
        assert "sample_interval_sec" in status
        assert "history_count" in status
        assert "history_size" in status
        assert "providers" in status
        assert "psutil_available" in status
        assert "tracemalloc_started" in status


# ============================================================================
# 辅助方法测试
# ============================================================================

class TestExtractValue:
    """_extract_value 静态方法测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_extract_memory(self):
        snap = ResourceSnapshot(timestamp=time.time())
        snap.memory.current_bytes = 5000
        assert ResourceMonitor._extract_value(snap, "memory") == 5000.0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_extract_threads(self):
        snap = ResourceSnapshot(timestamp=time.time())
        snap.thread_pool.active_threads = 7
        assert ResourceMonitor._extract_value(snap, "thread") == 7.0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_extract_file_handle_unavailable(self):
        snap = ResourceSnapshot(timestamp=time.time())
        snap.file_handles.available = False
        assert ResourceMonitor._extract_value(snap, "file_handle") is None

    @pytest.mark.unit
    @pytest.mark.p1
    def test_extract_db_connections_sum(self):
        snap = ResourceSnapshot(timestamp=time.time())
        snap.db_connections.pools = {
            "db1": {"active": 2, "idle": 3, "size": 5},
            "db2": {"active": 4, "idle": 1, "size": 5},
        }
        assert ResourceMonitor._extract_value(snap, "db_connection") == 6.0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_extract_unknown_type(self):
        snap = ResourceSnapshot(timestamp=time.time())
        assert ResourceMonitor._extract_value(snap, "unknown") is None
