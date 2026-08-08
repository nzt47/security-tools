"""alert_manager 单例迁移单元测试

覆盖：
- 单例行为：唯一性、注册、config_path 首次传入生效、已初始化后忽略
- start/stop 生命周期：状态往返、模块级 start/stop 函数、重复 start 幂等
- cleanup 钩子：重置时停止运行中的实例、未启动实例重置安全
- 并发首次初始化、fallback 行为
"""
import gc
import threading
import weakref

import pytest

import agent.monitoring.alert_manager as module
from agent.monitoring.alert_manager import get_alert_manager
from agent.utils.singleton_manager import is_initialized


@pytest.fixture(autouse=True)
def _cleanup_singleton():
    """每个用例前后重置单例，保证测试隔离"""
    module.reset_alert_manager()
    yield
    module.reset_alert_manager()


class TestAlertManagerSingleton:
    """单例行为测试"""

    def test_get_alert_manager_returns_same_instance(self):
        a = get_alert_manager()
        b = get_alert_manager()
        assert a is b

    def test_registers_in_singleton_manager(self):
        get_alert_manager()
        assert is_initialized("alert_manager")

    def test_config_path_passed_on_first_create(self):
        """首次传入 config_path 生效（经 SingletonManager dict 通道）"""
        orig_cls = module.AlertManager
        received = []

        class RecordingManager(orig_cls):
            def __init__(self, config_path=None):
                received.append(config_path)
                super().__init__(config_path)

        module.AlertManager = RecordingManager
        try:
            get_alert_manager("path/to/alerts.yml")
            assert received == ["path/to/alerts.yml"]
        finally:
            module.AlertManager = orig_cls

    def test_config_path_ignored_after_initialized(self):
        """已初始化后再次传 config_path 不触发重建"""
        first = get_alert_manager()
        assert get_alert_manager("other.yml") is first

    def test_reset_returns_new_instance(self):
        first = get_alert_manager()
        module.reset_alert_manager()
        second = get_alert_manager()
        assert first is not second

    def test_reset_releases_instance_for_gc(self):
        ref = weakref.ref(get_alert_manager())
        module.reset_alert_manager()
        gc.collect()
        assert ref() is None

    def test_reset_idempotent_when_not_initialized(self):
        module.reset_alert_manager()
        module.reset_alert_manager()


class TestAlertManagerLifecycle:
    """start/stop 生命周期测试"""

    def test_start_then_stop_restores_state(self):
        """生命周期：start → running，stop → stopped"""
        manager = get_alert_manager()
        manager.start()
        assert manager._running is True
        manager.stop()
        assert manager._running is False

    def test_start_is_idempotent(self):
        """重复 start 不重复启动（running 时直接返回）"""
        manager = get_alert_manager()
        manager.start()
        manager.start()
        assert manager._running is True
        manager.stop()

    def test_module_level_start_then_instance_stop(self):
        """模块级 start_alert_manager 启动 + 实例 stop 状态往返"""
        manager = module.start_alert_manager()
        assert manager._running is True
        manager.stop()
        assert manager._running is False

    def test_stop_when_not_started_is_safe(self):
        """未启动直接 stop 安全（stop 幂等）"""
        manager = get_alert_manager()
        manager.stop()
        assert manager._running is False


class TestAlertManagerCleanupHook:
    """cleanup 钩子测试（重点）"""

    def test_reset_stops_running_instance(self):
        """cleanup 钩子：重置时停止运行中的实例"""
        manager = get_alert_manager()
        manager.start()
        assert manager._running is True
        module.reset_alert_manager()
        assert manager._running is False  # 旧实例已被 cleanup 钩子停止

    def test_reset_stops_evaluator_and_healer(self):
        """cleanup 钩子联动停止评估器与自愈器"""
        manager = get_alert_manager()
        manager.start()
        assert manager._evaluator._running is True
        module.reset_alert_manager()
        assert manager._evaluator._running is False

    def test_reset_without_start_is_safe(self):
        """未启动实例重置安全（cleanup 对 stopped 实例幂等）"""
        get_alert_manager()
        module.reset_alert_manager()  # 不应抛异常
        module.reset_alert_manager()


class TestAlertManagerConcurrency:
    """并发场景测试"""

    def test_concurrent_first_get_initializes_once(self):
        """多线程并发首次 get_alert_manager 只构造一个实例（双重检查锁）"""
        orig_cls = module.AlertManager
        created = []

        class CountingManager(orig_cls):
            def __init__(self, config_path=None):
                created.append(1)
                super().__init__(config_path)

        module.AlertManager = CountingManager
        try:
            results = []
            errors = []
            barrier = threading.Barrier(8)

            def worker():
                barrier.wait()
                try:
                    results.append(get_alert_manager())
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
            module.AlertManager = orig_cls

    def test_concurrent_get_after_init_returns_same_instance(self):
        get_alert_manager()
        instances = []

        def worker():
            instances.append(get_alert_manager())

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(i is instances[0] for i in instances)


class TestAlertManagerFallback:
    """SingletonManager 不可用时的 fallback 行为"""

    def test_fallback_still_singleton(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        a = get_alert_manager()
        b = get_alert_manager()
        assert a is b

    def test_fallback_config_path_passed_directly(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        first = get_alert_manager("path/to/alerts.yml")
        assert first.config_path == "path/to/alerts.yml"

    def test_fallback_reset_works(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        first = get_alert_manager()
        module.reset_alert_manager()
        second = get_alert_manager()
        assert first is not second
