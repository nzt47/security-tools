"""monitoring/search 单例迁移单元测试

覆盖：
- 单例行为：唯一性、注册、重置、GC 回收
- 搜索状态恢复：start/stop 状态往返、stop 后重启、重置后干净状态、cleanup 停止旧实例
- 并发首次初始化、fallback 行为
"""
import gc
import threading
import weakref

import pytest

import agent.monitoring.search as module
from agent.monitoring.search import get_performance_monitor
from agent.utils.singleton_manager import is_initialized


@pytest.fixture(autouse=True)
def _cleanup_singleton():
    """每个用例前后重置单例，保证测试隔离"""
    module.reset_performance_monitor()
    yield
    module.reset_performance_monitor()


def _quiet(monitor):
    """替换 _perform_check 为空操作，避免触发真实搜索检测"""
    monitor._perform_check = lambda: None


class TestSearchMonitorSingleton:
    """单例行为测试"""

    def test_get_performance_monitor_returns_same_instance(self):
        a = get_performance_monitor()
        b = get_performance_monitor()
        assert a is b

    def test_registers_in_singleton_manager(self):
        get_performance_monitor()
        assert is_initialized("search_performance_monitor")

    def test_reset_returns_new_instance(self):
        first = get_performance_monitor()
        module.reset_performance_monitor()
        second = get_performance_monitor()
        assert first is not second

    def test_reset_releases_instance_for_gc(self):
        ref = weakref.ref(get_performance_monitor())
        module.reset_performance_monitor()
        gc.collect()
        assert ref() is None

    def test_reset_idempotent_when_not_initialized(self):
        module.reset_performance_monitor()
        module.reset_performance_monitor()


class TestSearchStateRecovery:
    """搜索状态恢复：start/stop 往返 + 重置后干净状态"""

    def test_start_then_stop_restores_state(self):
        """状态恢复：start → running，stop → 回到未运行状态"""
        monitor = get_performance_monitor()
        _quiet(monitor)
        monitor.start()
        assert monitor.get_status()["running"] is True
        monitor.stop()
        assert monitor.get_status()["running"] is False

    def test_restart_after_stop_supported(self):
        """状态恢复：stop 后可再次 start（TLM-AUDIT-002 支持重启）"""
        monitor = get_performance_monitor()
        _quiet(monitor)
        monitor.start()
        monitor.stop()
        monitor.start()
        assert monitor.get_status()["running"] is True
        monitor.stop()

    def test_reset_provides_clean_running_state(self):
        """测试隔离：前序实例运行中，重置后新实例处于未运行状态"""
        first = get_performance_monitor()
        _quiet(first)
        first.start()
        assert first.get_status()["running"] is True

        module.reset_performance_monitor()
        second = get_performance_monitor()
        assert second is not first
        assert second.get_status()["running"] is False

    def test_reset_stops_old_running_instance(self):
        """cleanup 钩子：重置时停止旧实例的监控线程"""
        monitor = get_performance_monitor()
        _quiet(monitor)
        monitor.start()
        module.reset_performance_monitor()
        assert monitor._running is False  # 旧实例已被 cleanup 钩子停止

    def test_manual_check_records_history(self, monkeypatch):
        """手动检测写入历史（状态恢复的数据源）"""
        monitor = get_performance_monitor()
        before = monitor.get_status()["history_count"]

        def fake_check():
            monitor._performance_history.append({"status": "ok", "engine": "mock"})

        monkeypatch.setattr(monitor, "_perform_check", fake_check)
        result = monitor.run_manual_check()
        assert result["status"] == "ok"
        # 构造时会从数据文件加载历史，断言相对增量
        assert monitor.get_status()["history_count"] == before + 1

    def test_module_level_start_stop_roundtrip(self):
        """模块级 start/stop 函数状态往返"""
        monitor = get_performance_monitor()
        _quiet(monitor)
        status = module.start_performance_monitor(interval_sec=60)
        assert status["running"] is True
        status = module.stop_performance_monitor()
        assert status["running"] is False


class TestSearchMonitorConcurrency:
    """并发场景测试"""

    def test_concurrent_first_get_initializes_once(self):
        """多线程并发首次 get_performance_monitor 只构造一个实例（双重检查锁）"""
        orig_cls = module.SearchPerformanceMonitor
        created = []

        class CountingMonitor(orig_cls):
            def __init__(self, *args, **kwargs):
                created.append(1)
                super().__init__(*args, **kwargs)

        module.SearchPerformanceMonitor = CountingMonitor
        try:
            results = []
            errors = []
            barrier = threading.Barrier(8)

            def worker():
                barrier.wait()
                try:
                    results.append(get_performance_monitor())
                except Exception as e:  # pragma: no cover
                    errors.append(e)

            threads = [threading.Thread(target=worker) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert not errors
            assert len(created) == 1, f"应只构造一次，实际 {len(created)} 次"
            assert all(r is results[0] for r in results)
        finally:
            module.SearchPerformanceMonitor = orig_cls

    def test_concurrent_get_after_init_returns_same_instance(self):
        get_performance_monitor()
        instances = []

        def worker():
            instances.append(get_performance_monitor())

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(i is instances[0] for i in instances)


class TestSearchMonitorFallback:
    """SingletonManager 不可用时的 fallback 行为"""

    def test_fallback_still_singleton(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        a = get_performance_monitor()
        b = get_performance_monitor()
        assert a is b

    def test_fallback_reset_works(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        first = get_performance_monitor()
        module.reset_performance_monitor()
        second = get_performance_monitor()
        assert first is not second
