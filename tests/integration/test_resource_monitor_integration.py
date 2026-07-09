"""ResourceMonitor 集成测试

覆盖 agent.monitoring.resource_monitor 模块：
- 数据结构（MemoryStat/ThreadPoolStat/FileHandleStat/DbConnectionStat/ResourceSnapshot/TrendResult）
- ResourceMonitor 核心 API（start/stop/sample/get_snapshot/get_history/get_trend）
- 子监控采样（memory/thread_pool/file_handles/db_connections）
- 趋势分析（线性回归、泄漏判定、回调）
- 持久化（写入/加载/重写/清理）
- 全局单例
"""

import json
import os
import time
import threading
import tracemalloc
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

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
# Fixtures
# ============================================================================

@pytest.fixture
def monitor():
    """默认 monitor（禁用持久化，避免污染磁盘）"""
    m = ResourceMonitor(config={"persist_enabled": False})
    yield m
    m.stop()


@pytest.fixture
def monitor_with_persist(tmp_path):
    """带持久化的 monitor（使用临时目录）"""
    persist_path = str(tmp_path / "history.jsonl")
    m = ResourceMonitor(config={
        "persist_enabled": True,
        "persist_path": persist_path,
        "persist_batch_size": 3,
    })
    yield m
    m.stop()


@pytest.fixture
def reset_global():
    """重置全局单例"""
    reset_resource_monitor()
    yield
    reset_resource_monitor()


# ============================================================================
# 数据结构测试
# ============================================================================

class TestMemoryStat:
    def test_defaults(self):
        stat = MemoryStat()
        assert stat.current_bytes == 0
        assert stat.peak_bytes == 0
        assert stat.top_allocations == []

    def test_custom_values(self):
        stat = MemoryStat(current_bytes=1024, peak_bytes=2048, top_allocations=[{"file": "x.py"}])
        assert stat.current_bytes == 1024
        assert stat.peak_bytes == 2048
        assert len(stat.top_allocations) == 1


class TestThreadPoolStat:
    def test_defaults(self):
        stat = ThreadPoolStat()
        assert stat.active_threads == 0
        assert stat.registered_pools == {}

    def test_custom(self):
        stat = ThreadPoolStat(active_threads=5, registered_pools={"pool1": {"active": 2}})
        assert stat.active_threads == 5
        assert "pool1" in stat.registered_pools


class TestFileHandleStat:
    def test_defaults(self):
        stat = FileHandleStat()
        assert stat.open_count == 0
        assert stat.available is True

    def test_unavailable(self):
        stat = FileHandleStat(available=False)
        assert stat.available is False


class TestDbConnectionStat:
    def test_defaults(self):
        stat = DbConnectionStat()
        assert stat.available is True
        assert stat.pools == {}

    def test_with_pools(self):
        stat = DbConnectionStat(pools={"db1": {"active": 3, "idle": 2, "size": 5}})
        assert stat.pools["db1"]["active"] == 3


class TestResourceSnapshot:
    def test_post_init_generates_iso_time(self):
        ts = time.time()
        snap = ResourceSnapshot(timestamp=ts)
        assert snap.iso_time != ""
        expected = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        assert snap.iso_time == expected

    def test_post_init_preserves_explicit_iso_time(self):
        snap = ResourceSnapshot(timestamp=time.time(), iso_time="custom-time")
        assert snap.iso_time == "custom-time"

    def test_to_dict(self):
        snap = ResourceSnapshot(timestamp=time.time())
        d = snap.to_dict()
        assert "timestamp" in d
        assert "memory" in d
        assert "thread_pool" in d
        assert "file_handles" in d
        assert "db_connections" in d
        assert "sample_duration_ms" in d
        assert "iso_time" in d

    def test_to_dict_contains_nested(self):
        snap = ResourceSnapshot(
            timestamp=time.time(),
            memory=MemoryStat(current_bytes=100),
        )
        d = snap.to_dict()
        assert d["memory"]["current_bytes"] == 100


class TestTrendResult:
    def test_defaults(self):
        result = TrendResult(
            resource_type="memory",
            slope=1.5,
            intercept=10.0,
            r_squared=0.95,
            sample_count=10,
            is_leaking=True,
            threshold=1.0,
        )
        assert result.resource_type == "memory"
        assert result.slope == 1.5
        assert result.is_leaking is True


# ============================================================================
# 初始化测试
# ============================================================================

class TestInitialization:
    def test_default_config(self):
        m = ResourceMonitor(config={"persist_enabled": False})
        assert m._history_size == 1440
        assert m._stress_mode is False
        assert m._sample_thread is None
        assert m._providers == {}
        assert m._leak_callbacks == []
        m.stop()

    def test_custom_history_size(self):
        m = ResourceMonitor(config={"history_size": 100, "persist_enabled": False})
        assert m._history_size == 100
        assert m._history.maxlen == 100
        m.stop()

    def test_tracemalloc_started(self):
        m = ResourceMonitor(config={"persist_enabled": False})
        assert m._tracemalloc_started is True
        m.stop()

    def test_monitor_trace_id_generated(self):
        m = ResourceMonitor(config={"persist_enabled": False})
        assert m._monitor_trace_id.startswith("resource-monitor-")
        m.stop()

    def test_persist_disabled_by_config(self):
        m = ResourceMonitor(config={"persist_enabled": False})
        assert m._persist_enabled is False
        m.stop()

    def test_persist_enabled_default_path(self):
        m = ResourceMonitor(config={"persist_enabled": True, "persist_path": ""})
        assert m._persist_enabled is True
        assert "resource_monitor_history.jsonl" in m._persist_path
        m.stop()

    def test_persist_custom_path(self):
        m = ResourceMonitor(config={"persist_enabled": True, "persist_path": "/tmp/test.jsonl"})
        assert m._persist_path == "/tmp/test.jsonl"
        m.stop()

    def test_persist_max_age_hours(self):
        m = ResourceMonitor(config={"persist_max_age_hours": 48, "persist_enabled": False})
        assert m._persist_max_age_hours == 48
        m.stop()

    def test_persist_batch_size(self):
        m = ResourceMonitor(config={"persist_batch_size": 50, "persist_enabled": False})
        assert m._persist_batch_size == 50
        m.stop()


# ============================================================================
# 配置读取测试
# ============================================================================

class TestGetConfig:
    def test_config_takes_priority(self):
        m = ResourceMonitor(config={"sample_interval_sec": 30, "persist_enabled": False})
        assert m._get_config("sample_interval_sec", 60) == 30
        m.stop()

    def test_default_when_not_in_config(self):
        m = ResourceMonitor(config={"persist_enabled": False})
        assert m._get_config("nonexistent_key", "default_val") == "default_val"
        m.stop()

    def test_empty_config_uses_default(self):
        m = ResourceMonitor(config={})
        assert m._get_config("any_key", 42) == 42
        m.stop()


class TestGetSampleInterval:
    def test_normal_mode(self):
        m = ResourceMonitor(config={"sample_interval_sec": 45, "persist_enabled": False})
        assert m._get_sample_interval() == 45.0
        m.stop()

    def test_stress_mode(self):
        m = ResourceMonitor(config={
            "stress_test_interval_sec": 2.0,
            "persist_enabled": False,
        })
        m.enable_stress_mode()
        assert m._get_sample_interval() == 2.0
        m.stop()

    def test_stress_mode_default(self):
        m = ResourceMonitor(config={"persist_enabled": False})
        m.enable_stress_mode()
        assert m._get_sample_interval() == 1.0
        m.stop()

    def test_normal_mode_default(self):
        m = ResourceMonitor(config={"persist_enabled": False})
        assert m._get_sample_interval() == 60.0
        m.stop()


# ============================================================================
# 压测模式测试
# ============================================================================

class TestStressMode:
    def test_enable_stress_mode(self):
        m = ResourceMonitor(config={"persist_enabled": False})
        m.enable_stress_mode()
        assert m._stress_mode is True
        m.stop()

    def test_disable_stress_mode(self):
        m = ResourceMonitor(config={"persist_enabled": False})
        m.enable_stress_mode()
        m.disable_stress_mode()
        assert m._stress_mode is False
        m.stop()

    def test_toggle_multiple_times(self):
        m = ResourceMonitor(config={"persist_enabled": False})
        m.enable_stress_mode()
        m.enable_stress_mode()
        assert m._stress_mode is True
        m.disable_stress_mode()
        m.disable_stress_mode()
        assert m._stress_mode is False
        m.stop()


# ============================================================================
# 采样测试
# ============================================================================

class TestSample:
    def test_sample_returns_snapshot(self, monitor):
        snap = monitor.sample()
        assert isinstance(snap, ResourceSnapshot)
        assert snap.timestamp > 0
        assert snap.iso_time != ""

    def test_sample_populates_memory(self, monitor):
        snap = monitor.sample()
        assert isinstance(snap.memory, MemoryStat)
        assert snap.memory.current_bytes >= 0

    def test_sample_populates_thread_pool(self, monitor):
        snap = monitor.sample()
        assert isinstance(snap.thread_pool, ThreadPoolStat)
        assert snap.thread_pool.active_threads >= 1

    def test_sample_populates_file_handles(self, monitor):
        snap = monitor.sample()
        assert isinstance(snap.file_handles, FileHandleStat)

    def test_sample_populates_db_connections(self, monitor):
        snap = monitor.sample()
        assert isinstance(snap.db_connections, DbConnectionStat)

    def test_sample_duration_positive(self, monitor):
        snap = monitor.sample()
        assert snap.sample_duration_ms >= 0

    def test_sample_appends_to_history(self, monitor):
        assert len(monitor._history) == 0
        monitor.sample()
        assert len(monitor._history) == 1

    def test_sample_multiple_times(self, monitor):
        for _ in range(5):
            monitor.sample()
        assert len(monitor._history) == 5


class TestSampleMemory:
    def test_tracemalloc_not_started_returns_empty(self):
        m = ResourceMonitor(config={"persist_enabled": False})
        m._tracemalloc_started = False
        stat = m._sample_memory()
        assert stat.current_bytes == 0
        assert stat.peak_bytes == 0
        assert stat.top_allocations == []
        m.stop()

    def test_tracemalloc_started_returns_stats(self, monitor):
        stat = monitor._sample_memory()
        assert stat.current_bytes >= 0
        assert stat.peak_bytes >= 0

    def test_top_allocations_format(self, monitor):
        stat = monitor._sample_memory()
        if stat.top_allocations:
            alloc = stat.top_allocations[0]
            assert "file" in alloc
            assert "line" in alloc
            assert "size_bytes" in alloc
            assert "count" in alloc


class TestSampleThreadPool:
    def test_no_providers(self, monitor):
        stat = monitor._sample_thread_pool()
        assert stat.active_threads >= 1
        assert stat.registered_pools == {}

    def test_with_thread_provider(self, monitor):
        monitor.register_pool_provider(
            "pool1",
            lambda: {"active": 3, "queued": 1, "size": 5},
            "thread",
        )
        stat = monitor._sample_thread_pool()
        assert "pool1" in stat.registered_pools
        assert stat.registered_pools["pool1"]["active"] == 3
        assert stat.registered_pools["pool1"]["queued"] == 1
        assert stat.registered_pools["pool1"]["size"] == 5

    def test_provider_exception_isolated(self, monitor):
        def bad_provider():
            raise RuntimeError("boom")

        monitor.register_pool_provider("bad", bad_provider, "thread")
        stat = monitor._sample_thread_pool()
        assert stat.active_threads >= 1
        assert "bad" not in stat.registered_pools

    def test_provider_returns_none(self, monitor):
        monitor.register_pool_provider("none", lambda: None, "thread")
        stat = monitor._sample_thread_pool()
        assert "none" in stat.registered_pools
        assert stat.registered_pools["none"]["active"] == 0

    def test_db_provider_not_in_thread_pool(self, monitor):
        monitor.register_pool_provider(
            "db1", lambda: {"active": 1, "idle": 2, "size": 3}, "db"
        )
        stat = monitor._sample_thread_pool()
        assert "db1" not in stat.registered_pools


class TestSampleFileHandles:
    def test_returns_stat(self, monitor):
        stat = monitor._sample_file_handles()
        assert isinstance(stat, FileHandleStat)
        assert stat.available in (True, False)

    def test_psutil_unavailable(self, monitor):
        m = ResourceMonitor(config={"persist_enabled": False})
        with patch("agent.monitoring.resource_monitor._PSUTIL_AVAILABLE", False):
            stat = m._sample_file_handles()
            assert stat.available is False
            assert stat.open_count == 0
        m.stop()

    def test_psutil_no_such_process(self, monitor):
        import psutil
        with patch("agent.monitoring.resource_monitor._PSUTIL_AVAILABLE", True), \
             patch("psutil.Process") as mock_proc:
            mock_proc.return_value.open_files.side_effect = psutil.NoSuchProcess(123)
            stat = monitor._sample_file_handles()
            assert stat.available is False

    def test_psutil_access_denied(self, monitor):
        import psutil
        with patch("agent.monitoring.resource_monitor._PSUTIL_AVAILABLE", True), \
             patch("psutil.Process") as mock_proc:
            mock_proc.return_value.open_files.side_effect = psutil.AccessDenied()
            stat = monitor._sample_file_handles()
            assert stat.available is False


class TestSampleDbConnections:
    def test_no_providers(self, monitor):
        stat = monitor._sample_db_connections()
        assert stat.pools == {}

    def test_with_db_provider(self, monitor):
        monitor.register_pool_provider(
            "db1", lambda: {"active": 2, "idle": 3, "size": 5}, "db"
        )
        stat = monitor._sample_db_connections()
        assert "db1" in stat.pools
        assert stat.pools["db1"]["active"] == 2
        assert stat.pools["db1"]["idle"] == 3
        assert stat.pools["db1"]["size"] == 5

    def test_thread_provider_not_in_db(self, monitor):
        monitor.register_pool_provider(
            "thread1", lambda: {"active": 1}, "thread"
        )
        stat = monitor._sample_db_connections()
        assert "thread1" not in stat.pools

    def test_provider_exception_isolated(self, monitor):
        def bad_provider():
            raise RuntimeError("db error")

        monitor.register_pool_provider("bad_db", bad_provider, "db")
        stat = monitor._sample_db_connections()
        assert "bad_db" not in stat.pools

    def test_provider_returns_none(self, monitor):
        monitor.register_pool_provider("none_db", lambda: None, "db")
        stat = monitor._sample_db_connections()
        assert "none_db" in stat.pools


# ============================================================================
# 历史与快照查询测试
# ============================================================================

class TestGetSnapshot:
    def test_empty_history_returns_none(self, monitor):
        assert monitor.get_snapshot() is None

    def test_returns_latest(self, monitor):
        monitor.sample()
        monitor.sample()
        snap = monitor.get_snapshot()
        assert snap is not None
        assert isinstance(snap, ResourceSnapshot)


class TestGetHistory:
    def test_empty_history(self, monitor):
        assert monitor.get_history() == []

    def test_all_history(self, monitor):
        for _ in range(3):
            monitor.sample()
        history = monitor.get_history()
        assert len(history) == 3

    def test_limited_history(self, monitor):
        for _ in range(5):
            monitor.sample()
        history = monitor.get_history(limit=2)
        assert len(history) == 2

    def test_limit_larger_than_history(self, monitor):
        monitor.sample()
        history = monitor.get_history(limit=10)
        assert len(history) == 1

    def test_history_ring_buffer(self):
        m = ResourceMonitor(config={"history_size": 3, "persist_enabled": False})
        for _ in range(5):
            m.sample()
        history = m.get_history()
        assert len(history) == 3
        m.stop()


# ============================================================================
# 趋势分析测试
# ============================================================================

class TestGetTrend:
    def test_insufficient_history_returns_none(self, monitor):
        assert monitor.get_trend() is None

    def test_single_sample_returns_none(self, monitor):
        monitor.sample()
        assert monitor.get_trend() is None

    def test_two_samples_returns_result(self, monitor):
        monitor.sample()
        time.sleep(0.01)
        monitor.sample()
        result = monitor.get_trend()
        assert result is not None
        assert isinstance(result, TrendResult)
        assert result.resource_type == "memory"
        assert result.sample_count == 2

    def test_memory_trend(self, monitor):
        for _ in range(5):
            monitor.sample()
        result = monitor.get_trend("memory")
        assert result is not None
        assert result.resource_type == "memory"

    def test_thread_trend(self, monitor):
        for _ in range(3):
            monitor.sample()
        result = monitor.get_trend("thread")
        assert result is not None
        assert result.resource_type == "thread"

    def test_file_handle_trend_available(self, monitor):
        for _ in range(3):
            monitor.sample()
        result = monitor.get_trend("file_handle")
        if monitor.get_snapshot().file_handles.available:
            assert result is not None
            assert result.resource_type == "file_handle"

    def test_db_connection_trend(self, monitor):
        monitor.register_pool_provider(
            "db1", lambda: {"active": 1, "idle": 1, "size": 2}, "db"
        )
        for _ in range(3):
            monitor.sample()
        result = monitor.get_trend("db_connection")
        assert result is not None
        assert result.resource_type == "db_connection"

    def test_leak_detected_when_slope_exceeds_threshold(self):
        m = ResourceMonitor(config={
            "leak_slope_threshold": 0.0,
            "persist_enabled": False,
        })
        for i in range(5):
            snap = ResourceSnapshot(
                timestamp=time.time(),
                memory=MemoryStat(current_bytes=(i + 1) * 1000),
            )
            m._history.append(snap)
        result = m.get_trend("memory")
        assert result is not None
        assert result.is_leaking is True
        m.stop()

    def test_no_leak_when_slope_below_threshold(self):
        m = ResourceMonitor(config={
            "leak_slope_threshold": 1000000,
            "persist_enabled": False,
        })
        for i in range(5):
            snap = ResourceSnapshot(
                timestamp=time.time(),
                memory=MemoryStat(current_bytes=(i + 1) * 10),
            )
            m._history.append(snap)
        result = m.get_trend("memory")
        assert result is not None
        assert result.is_leaking is False
        m.stop()

    def test_unknown_resource_type_returns_none(self, monitor):
        for _ in range(3):
            monitor.sample()
        result = monitor.get_trend("unknown_type")
        assert result is None

    def test_file_handle_unavailable_returns_none(self):
        m = ResourceMonitor(config={"persist_enabled": False})
        for _ in range(3):
            snap = ResourceSnapshot(
                timestamp=time.time(),
                file_handles=FileHandleStat(available=False),
            )
            m._history.append(snap)
        result = m.get_trend("file_handle")
        assert result is None
        m.stop()


class TestExtractValue:
    def test_memory(self):
        snap = ResourceSnapshot(timestamp=time.time(), memory=MemoryStat(current_bytes=1024))
        assert ResourceMonitor._extract_value(snap, "memory") == 1024.0

    def test_thread(self):
        snap = ResourceSnapshot(timestamp=time.time(), thread_pool=ThreadPoolStat(active_threads=5))
        assert ResourceMonitor._extract_value(snap, "thread") == 5.0

    def test_file_handle_available(self):
        snap = ResourceSnapshot(
            timestamp=time.time(),
            file_handles=FileHandleStat(open_count=10, available=True),
        )
        assert ResourceMonitor._extract_value(snap, "file_handle") == 10.0

    def test_file_handle_unavailable(self):
        snap = ResourceSnapshot(
            timestamp=time.time(),
            file_handles=FileHandleStat(available=False),
        )
        assert ResourceMonitor._extract_value(snap, "file_handle") is None

    def test_db_connection(self):
        snap = ResourceSnapshot(
            timestamp=time.time(),
            db_connections=DbConnectionStat(pools={
                "db1": {"active": 2, "idle": 1, "size": 3},
                "db2": {"active": 4, "idle": 2, "size": 6},
            }),
        )
        assert ResourceMonitor._extract_value(snap, "db_connection") == 6.0

    def test_db_connection_empty(self):
        snap = ResourceSnapshot(timestamp=time.time())
        assert ResourceMonitor._extract_value(snap, "db_connection") == 0.0

    def test_unknown_type(self):
        snap = ResourceSnapshot(timestamp=time.time())
        assert ResourceMonitor._extract_value(snap, "unknown") is None


class TestLinearRegression:
    def test_insufficient_data(self):
        result = ResourceMonitor._linear_regression([(1, 2.0)])
        assert result == (0.0, 0.0, 0.0)

    def test_empty_data(self):
        result = ResourceMonitor._linear_regression([])
        assert result == (0.0, 0.0, 0.0)

    def test_perfect_linear(self):
        series = [(i, float(i * 2)) for i in range(10)]
        slope, intercept, r_squared = ResourceMonitor._linear_regression(series)
        assert abs(slope - 2.0) < 0.001
        assert abs(intercept) < 0.001
        assert abs(r_squared - 1.0) < 0.001

    def test_horizontal_line(self):
        series = [(i, 5.0) for i in range(10)]
        slope, intercept, r_squared = ResourceMonitor._linear_regression(series)
        assert slope == 0.0
        assert intercept == 5.0
        assert r_squared == 1.0

    def test_negative_slope(self):
        series = [(i, float(10 - i)) for i in range(10)]
        slope, intercept, r_squared = ResourceMonitor._linear_regression(series)
        assert slope < 0
        assert abs(slope - (-1.0)) < 0.001

    def test_denom_zero(self):
        series = [(5, 1.0), (5, 2.0), (5, 3.0)]
        slope, intercept, r_squared = ResourceMonitor._linear_regression(series)
        assert slope == 0.0
        assert intercept == 2.0

    def test_r_squared_clamped(self):
        series = [(0, 0.0), (1, 10.0), (2, 0.0)]
        slope, intercept, r_squared = ResourceMonitor._linear_regression(series)
        assert 0.0 <= r_squared <= 1.0


# ============================================================================
# 泄漏回调测试
# ============================================================================

class TestLeakCallback:
    def test_register_callback(self, monitor):
        monitor.register_leak_callback(lambda r: None)
        assert len(monitor._leak_callbacks) == 1

    def test_callback_fired_on_leak(self):
        m = ResourceMonitor(config={
            "leak_slope_threshold": 0.0,
            "persist_enabled": False,
        })
        called = []
        m.register_leak_callback(lambda r: called.append(r))
        for i in range(5):
            snap = ResourceSnapshot(
                timestamp=time.time(),
                memory=MemoryStat(current_bytes=(i + 1) * 1000),
            )
            m._history.append(snap)
        m.get_trend("memory")
        assert len(called) == 1
        assert called[0].is_leaking is True
        m.stop()

    def test_callback_exception_isolated(self):
        m = ResourceMonitor(config={
            "leak_slope_threshold": 0.0,
            "persist_enabled": False,
        })

        def bad_callback(r):
            raise RuntimeError("callback error")

        m.register_leak_callback(bad_callback)
        for i in range(3):
            snap = ResourceSnapshot(
                timestamp=time.time(),
                memory=MemoryStat(current_bytes=(i + 1) * 1000),
            )
            m._history.append(snap)
        result = m.get_trend("memory")
        assert result is not None
        m.stop()

    def test_multiple_callbacks(self):
        m = ResourceMonitor(config={
            "leak_slope_threshold": 0.0,
            "persist_enabled": False,
        })
        called1 = []
        called2 = []
        m.register_leak_callback(lambda r: called1.append(r))
        m.register_leak_callback(lambda r: called2.append(r))
        for i in range(3):
            snap = ResourceSnapshot(
                timestamp=time.time(),
                memory=MemoryStat(current_bytes=(i + 1) * 1000),
            )
            m._history.append(snap)
        m.get_trend("memory")
        assert len(called1) == 1
        assert len(called2) == 1
        m.stop()


# ============================================================================
# Provider 注册测试
# ============================================================================

class TestRegisterPoolProvider:
    def test_register_thread_provider(self, monitor):
        monitor.register_pool_provider("t1", lambda: {"active": 1}, "thread")
        assert "t1" in monitor._providers
        assert monitor._providers["t1"][1] == "thread"

    def test_register_db_provider(self, monitor):
        monitor.register_pool_provider("d1", lambda: {"active": 1}, "db")
        assert "d1" in monitor._providers
        assert monitor._providers["d1"][1] == "db"

    def test_register_overwrites(self, monitor):
        monitor.register_pool_provider("p1", lambda: {"active": 1}, "thread")
        monitor.register_pool_provider("p1", lambda: {"active": 2}, "db")
        assert monitor._providers["p1"][1] == "db"

    def test_default_pool_type(self, monitor):
        monitor.register_pool_provider("p2", lambda: {"active": 1})
        assert monitor._providers["p2"][1] == "thread"


# ============================================================================
# 状态查询测试
# ============================================================================

class TestGetStatus:
    def test_initial_status(self, monitor):
        status = monitor.get_status()
        assert status["running"] is False
        assert status["stress_mode"] is False
        assert status["history_count"] == 0
        assert status["history_size"] == 1440
        assert status["providers"] == []
        assert status["psutil_available"] in (True, False)
        assert status["tracemalloc_started"] is True
        assert status["latest_snapshot"] is None
        assert "persist" in status

    def test_status_after_sample(self, monitor):
        monitor.sample()
        status = monitor.get_status()
        assert status["history_count"] == 1
        assert status["latest_snapshot"] is not None

    def test_status_with_providers(self, monitor):
        monitor.register_pool_provider("p1", lambda: {"active": 1})
        status = monitor.get_status()
        assert "p1" in status["providers"]

    def test_status_stress_mode(self, monitor):
        monitor.enable_stress_mode()
        status = monitor.get_status()
        assert status["stress_mode"] is True

    def test_status_running(self, monitor):
        monitor.start()
        status = monitor.get_status()
        assert status["running"] is True

    def test_status_sample_interval(self, monitor):
        status = monitor.get_status()
        assert status["sample_interval_sec"] == 60.0


# ============================================================================
# 启动/停止测试
# ============================================================================

class TestStartStop:
    def test_start_creates_thread(self, monitor):
        assert monitor._sample_thread is None
        monitor.start()
        assert monitor._sample_thread is not None
        assert monitor._sample_thread.is_alive()

    def test_start_idempotent(self, monitor):
        monitor.start()
        thread1 = monitor._sample_thread
        monitor.start()
        assert monitor._sample_thread is thread1

    def test_stop_clears_thread(self, monitor):
        monitor.start()
        monitor.stop()
        assert monitor._sample_thread is None

    def test_stop_without_start(self, monitor):
        monitor.stop()
        assert monitor._sample_thread is None

    def test_start_returns_true(self, monitor):
        assert monitor.start() is True

    def test_sample_loop_runs(self, monitor):
        monitor.start()
        time.sleep(0.2)
        monitor.stop()
        assert len(monitor._history) > 0


# ============================================================================
# 指标上报测试
# ============================================================================

class TestReportMetrics:
    def test_collector_none_no_error(self, monitor):
        with patch("agent.monitoring.resource_monitor._get_business_collector", return_value=None):
            monitor._report_metrics(ResourceSnapshot(timestamp=time.time()))

    def test_collector_available_reports(self, monitor):
        mock_collector = MagicMock()
        with patch("agent.monitoring.resource_monitor._get_business_collector", return_value=mock_collector):
            snap = ResourceSnapshot(
                timestamp=time.time(),
                memory=MemoryStat(current_bytes=1024),
                thread_pool=ThreadPoolStat(active_threads=3),
                file_handles=FileHandleStat(open_count=5, available=True),
                db_connections=DbConnectionStat(pools={
                    "db1": {"active": 2, "idle": 1, "size": 3}
                }),
            )
            monitor._report_metrics(snap)
            assert mock_collector._set_gauge.call_count >= 4

    def test_collector_exception_isolated(self, monitor):
        mock_collector = MagicMock()
        mock_collector._set_gauge.side_effect = RuntimeError("metric error")
        with patch("agent.monitoring.resource_monitor._get_business_collector", return_value=mock_collector):
            monitor._report_metrics(ResourceSnapshot(timestamp=time.time()))

    def test_file_handle_not_reported_when_unavailable(self, monitor):
        mock_collector = MagicMock()
        with patch("agent.monitoring.resource_monitor._get_business_collector", return_value=mock_collector):
            snap = ResourceSnapshot(
                timestamp=time.time(),
                file_handles=FileHandleStat(available=False),
            )
            monitor._report_metrics(snap)
            for call in mock_collector._set_gauge.call_args_list:
                args = call[0]
                labels = args[1]
                assert labels.get("resource_type") != "file_handle"


# ============================================================================
# 持久化测试
# ============================================================================

class TestPersistResolvePath:
    def test_configured_path(self):
        m = ResourceMonitor(config={"persist_path": "/custom/path.jsonl"})
        assert m._resolve_persist_path() == "/custom/path.jsonl"
        m.stop()

    def test_default_path(self):
        m = ResourceMonitor(config={"persist_path": ""})
        path = m._resolve_persist_path()
        assert "resource_monitor_history.jsonl" in path
        m.stop()


class TestPersistSample:
    def test_disabled_does_nothing(self):
        m = ResourceMonitor(config={"persist_enabled": False})
        snap = ResourceSnapshot(timestamp=time.time())
        m._persist_sample(snap)
        assert len(m._persist_buffer) == 0
        m.stop()

    def test_buffered_until_batch_size(self, monitor_with_persist):
        m = monitor_with_persist
        assert m._persist_batch_size == 3
        m._persist_sample(ResourceSnapshot(timestamp=time.time()))
        m._persist_sample(ResourceSnapshot(timestamp=time.time()))
        assert len(m._persist_buffer) == 2

    def test_flush_triggered_at_batch_size(self, monitor_with_persist):
        m = monitor_with_persist
        m._persist_sample(ResourceSnapshot(timestamp=time.time()))
        m._persist_sample(ResourceSnapshot(timestamp=time.time()))
        m._persist_sample(ResourceSnapshot(timestamp=time.time()))
        assert len(m._persist_buffer) == 0
        assert os.path.exists(m._persist_path)

    def test_flush_persist_empty_buffer(self, monitor_with_persist):
        m = monitor_with_persist
        m._flush_persist()
        assert not os.path.exists(m._persist_path)


class TestFlushPersist:
    def test_writes_jsonl(self, monitor_with_persist):
        m = monitor_with_persist
        m._persist_buffer.append(ResourceSnapshot(timestamp=time.time()))
        m._flush_persist()
        assert os.path.exists(m._persist_path)
        with open(m._persist_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert content.strip() != ""
        lines = content.strip().split("\n")
        assert len(lines) == 1

    def test_multiple_flushes_append(self, monitor_with_persist):
        m = monitor_with_persist
        m._persist_buffer.append(ResourceSnapshot(timestamp=time.time()))
        m._flush_persist()
        m._persist_buffer.append(ResourceSnapshot(timestamp=time.time()))
        m._flush_persist()
        with open(m._persist_path, "r", encoding="utf-8") as f:
            lines = f.read().strip().split("\n")
        assert len(lines) == 2

    def test_disabled_does_nothing(self):
        m = ResourceMonitor(config={"persist_enabled": False})
        m._persist_buffer.append(ResourceSnapshot(timestamp=time.time()))
        m._flush_persist()
        m.stop()

    def test_creates_parent_directory(self, tmp_path):
        nested = str(tmp_path / "nested" / "dir" / "history.jsonl")
        m = ResourceMonitor(config={
            "persist_enabled": True,
            "persist_path": nested,
            "persist_batch_size": 1,
        })
        m._persist_buffer.append(ResourceSnapshot(timestamp=time.time()))
        m._flush_persist()
        assert os.path.exists(nested)
        m.stop()

    def test_manual_flush(self, monitor_with_persist):
        m = monitor_with_persist
        m._persist_buffer.append(ResourceSnapshot(timestamp=time.time()))
        m.flush_persist()
        assert len(m._persist_buffer) == 0
        assert os.path.exists(m._persist_path)


class TestLoadPersistedHistory:
    def test_no_file_returns_zero(self, monitor_with_persist):
        m = monitor_with_persist
        assert m._load_persisted_history() == 0

    def test_load_existing_file(self, monitor_with_persist):
        m = monitor_with_persist
        m._persist_buffer.append(ResourceSnapshot(timestamp=time.time()))
        m._flush_persist()
        m._persist_loaded = False
        count = m._load_persisted_history()
        assert count == 1
        assert len(m._history) == 1

    def test_load_only_once(self, monitor_with_persist):
        m = monitor_with_persist
        m._persist_buffer.append(ResourceSnapshot(timestamp=time.time()))
        m._flush_persist()
        m._persist_loaded = False
        m._load_persisted_history()
        count = m._load_persisted_history()
        assert count == 0

    def test_disabled_returns_zero(self):
        m = ResourceMonitor(config={"persist_enabled": False})
        assert m._load_persisted_history() == 0
        m.stop()

    def test_filters_expired_data(self, tmp_path):
        path = str(tmp_path / "history.jsonl")
        old_ts = time.time() - 200 * 3600
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "timestamp": old_ts,
                "iso_time": "",
                "memory": {},
                "thread_pool": {},
                "file_handles": {},
                "db_connections": {},
                "sample_duration_ms": 0,
            }) + "\n")
        m = ResourceMonitor(config={
            "persist_enabled": True,
            "persist_path": path,
            "persist_max_age_hours": 1,
        })
        count = m._load_persisted_history()
        assert count == 0
        m.stop()

    def test_skips_corrupted_lines(self, tmp_path):
        path = str(tmp_path / "history.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write("not json\n")
            f.write(json.dumps({
                "timestamp": time.time(),
                "iso_time": "",
                "memory": {},
                "thread_pool": {},
                "file_handles": {},
                "db_connections": {},
                "sample_duration_ms": 0,
            }) + "\n")
        m = ResourceMonitor(config={
            "persist_enabled": True,
            "persist_path": path,
        })
        count = m._load_persisted_history()
        assert count == 1
        m.stop()


class TestRewritePersistedFile:
    def test_removes_expired(self, tmp_path):
        path = str(tmp_path / "history.jsonl")
        old_ts = time.time() - 200 * 3600
        new_ts = time.time()
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp": old_ts}) + "\n")
            f.write(json.dumps({"timestamp": new_ts}) + "\n")
        m = ResourceMonitor(config={
            "persist_enabled": True,
            "persist_path": path,
            "persist_max_age_hours": 1,
        })
        m._rewrite_persisted_file()
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().strip().split("\n")
        assert len(lines) == 1
        m.stop()

    def test_no_file_returns(self, monitor_with_persist):
        m = monitor_with_persist
        m._rewrite_persisted_file()

    def test_skips_corrupted(self, tmp_path):
        path = str(tmp_path / "history.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write("corrupted\n")
            f.write(json.dumps({"timestamp": time.time()}) + "\n")
        m = ResourceMonitor(config={
            "persist_enabled": True,
            "persist_path": path,
        })
        m._rewrite_persisted_file()
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().strip().split("\n")
        assert len(lines) == 1
        m.stop()


class TestDictToSnapshot:
    def test_full_dict(self):
        data = {
            "timestamp": time.time(),
            "iso_time": "test",
            "memory": {"current_bytes": 100, "peak_bytes": 200, "top_allocations": []},
            "thread_pool": {"active_threads": 3, "registered_pools": {}},
            "file_handles": {"open_count": 5, "available": True},
            "db_connections": {"available": True, "pools": {}},
            "sample_duration_ms": 1.5,
        }
        snap = ResourceMonitor._dict_to_snapshot(None, data)
        assert snap is not None
        assert snap.memory.current_bytes == 100
        assert snap.thread_pool.active_threads == 3

    def test_minimal_dict(self):
        data = {"timestamp": time.time()}
        snap = ResourceMonitor._dict_to_snapshot(None, data)
        assert snap is not None
        assert snap.memory.current_bytes == 0

    def test_invalid_data_returns_none(self):
        snap = ResourceMonitor._dict_to_snapshot(None, {"timestamp": "not a number"})
        assert snap is None


class TestCleanupPersistedHistory:
    def test_no_file_returns_zero(self, monitor_with_persist):
        m = monitor_with_persist
        assert m.cleanup_persisted_history() == 0

    def test_returns_kept_count(self, monitor_with_persist):
        m = monitor_with_persist
        m._persist_buffer.append(ResourceSnapshot(timestamp=time.time()))
        m._flush_persist()
        m._persist_buffer.append(ResourceSnapshot(timestamp=time.time()))
        m._flush_persist()
        count = m.cleanup_persisted_history()
        assert count == 2

    def test_disabled_returns_zero(self):
        m = ResourceMonitor(config={"persist_enabled": False})
        assert m.cleanup_persisted_history() == 0
        m.stop()


class TestGetPersistStatus:
    def test_disabled(self):
        m = ResourceMonitor(config={"persist_enabled": False})
        status = m.get_persist_status()
        assert status["enabled"] is False
        m.stop()

    def test_enabled_no_file(self, monitor_with_persist):
        m = monitor_with_persist
        status = m.get_persist_status()
        assert status["enabled"] is True
        assert status["file_exists"] is False
        assert status["file_size_bytes"] == 0

    def test_enabled_with_file(self, monitor_with_persist):
        m = monitor_with_persist
        m._persist_buffer.append(ResourceSnapshot(timestamp=time.time()))
        m._flush_persist()
        status = m.get_persist_status()
        assert status["file_exists"] is True
        assert status["file_size_bytes"] > 0

    def test_buffer_count(self, monitor_with_persist):
        m = monitor_with_persist
        m._persist_buffer.append(ResourceSnapshot(timestamp=time.time()))
        status = m.get_persist_status()
        assert status["buffer_count"] == 1

    def test_status_fields(self, monitor_with_persist):
        m = monitor_with_persist
        status = m.get_persist_status()
        assert "path" in status
        assert "batch_size" in status
        assert "max_age_hours" in status
        assert "history_loaded" in status


# ============================================================================
# 降级策略测试
# ============================================================================

class TestDegradation:
    def test_sample_continues_on_memory_error(self, monitor):
        with patch.object(monitor, "_sample_memory", side_effect=RuntimeError("mem error")):
            snap = monitor.sample()
            assert snap.memory.current_bytes == 0

    def test_sample_continues_on_thread_pool_error(self, monitor):
        with patch.object(monitor, "_sample_thread_pool", side_effect=RuntimeError("thread error")):
            snap = monitor.sample()
            assert snap.thread_pool.active_threads == 0

    def test_sample_continues_on_file_handle_error(self, monitor):
        with patch.object(monitor, "_sample_file_handles", side_effect=RuntimeError("fh error")):
            snap = monitor.sample()
            assert snap.file_handles.available is True

    def test_sample_continues_on_db_error(self, monitor):
        with patch.object(monitor, "_sample_db_connections", side_effect=RuntimeError("db error")):
            snap = monitor.sample()
            assert snap.db_connections.pools == {}


class TestSampleLoopError:
    def test_sample_loop_continues_on_exception(self, monitor):
        # 配置短采样间隔确保 0.3 秒内多次调用
        monitor._config["sample_interval_sec"] = 0.05
        call_count = [0]
        original_sample = monitor._do_sample

        def failing_sample():
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("transient error")
            return original_sample()

        monitor._do_sample = failing_sample
        monitor.start()
        time.sleep(0.3)
        monitor.stop()
        assert call_count[0] >= 2


# ============================================================================
# 全局单例测试
# ============================================================================

class TestGlobalSingleton:
    def test_get_resource_monitor_returns_instance(self, reset_global):
        m = get_resource_monitor()
        assert isinstance(m, ResourceMonitor)

    def test_singleton_returns_same_instance(self, reset_global):
        m1 = get_resource_monitor()
        m2 = get_resource_monitor()
        assert m1 is m2

    def test_reset_clears_instance(self, reset_global):
        m1 = get_resource_monitor()
        reset_resource_monitor()
        m2 = get_resource_monitor()
        assert m1 is not m2

    def test_reset_stops_running_monitor(self, reset_global):
        m = get_resource_monitor()
        m.start()
        reset_resource_monitor()
        assert not m._sample_thread or not m._sample_thread.is_alive()


# ============================================================================
# 集成场景测试
# ============================================================================

class TestIntegrationScenario:
    def test_full_lifecycle(self, monitor):
        monitor.start()
        time.sleep(0.2)
        monitor.enable_stress_mode()
        time.sleep(0.2)
        monitor.disable_stress_mode()
        monitor.stop()
        assert len(monitor._history) > 0

    def test_trend_with_real_samples(self, monitor):
        for _ in range(10):
            monitor.sample()
            time.sleep(0.01)
        result = monitor.get_trend("memory")
        assert result is not None
        assert result.sample_count == 10

    def test_persist_full_cycle(self, tmp_path):
        path = str(tmp_path / "cycle.jsonl")
        m1 = ResourceMonitor(config={
            "persist_enabled": True,
            "persist_path": path,
            "persist_batch_size": 1,
        })
        m1.sample()
        m1.stop()
        assert os.path.exists(path)

        m2 = ResourceMonitor(config={
            "persist_enabled": True,
            "persist_path": path,
        })
        m2._persist_loaded = False
        loaded = m2._load_persisted_history()
        assert loaded == 1
        assert len(m2._history) == 1
        m2.stop()

    def test_providers_in_status(self, monitor):
        monitor.register_pool_provider("thread1", lambda: {"active": 1}, "thread")
        monitor.register_pool_provider("db1", lambda: {"active": 2}, "db")
        status = monitor.get_status()
        assert "thread1" in status["providers"]
        assert "db1" in status["providers"]

    def test_history_limit_returns_latest(self, monitor):
        for i in range(10):
            monitor.sample()
        history = monitor.get_history(limit=3)
        assert len(history) == 3
        assert history[-1] is monitor.get_snapshot()
