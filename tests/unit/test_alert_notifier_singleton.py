"""alert_notifier 单例迁移单元测试（中优先级首批）

覆盖：
- 单例行为：唯一性、注册、config 首次传入生效、已初始化后忽略
- 重置：新实例、GC 回收、幂等
- 并发首次初始化、fallback 行为
"""
import gc
import threading
import weakref

import pytest

import agent.monitoring.alert_notifier as module
from agent.monitoring.alert_notifier import get_alert_notifier
from agent.utils.singleton_manager import is_initialized


@pytest.fixture(autouse=True)
def _cleanup_singleton():
    """每个用例前后重置单例，保证测试隔离"""
    module.reset_alert_notifier()
    yield
    module.reset_alert_notifier()


class TestAlertNotifierSingleton:
    """单例行为测试"""

    def test_get_alert_notifier_returns_same_instance(self):
        a = get_alert_notifier()
        b = get_alert_notifier()
        assert a is b

    def test_registers_in_singleton_manager(self):
        get_alert_notifier()
        assert is_initialized("alert_notifier")

    def test_config_passed_on_first_create(self):
        """首次传入 config 生效（通过 SingletonManager dict 通道）"""
        first = get_alert_notifier({"custom": True})
        assert first.config.get("custom") is True

    def test_config_ignored_after_initialized(self):
        """已初始化后再次传 config 不触发重建"""
        first = get_alert_notifier()
        assert get_alert_notifier({"custom": True}) is first

    def test_reset_returns_new_instance(self):
        first = get_alert_notifier()
        module.reset_alert_notifier()
        second = get_alert_notifier()
        assert first is not second

    def test_reset_releases_instance_for_gc(self):
        ref = weakref.ref(get_alert_notifier())
        module.reset_alert_notifier()
        gc.collect()
        assert ref() is None

    def test_reset_idempotent_when_not_initialized(self):
        module.reset_alert_notifier()
        module.reset_alert_notifier()


class TestAlertNotifierConcurrency:
    """并发场景测试"""

    def test_concurrent_first_get_initializes_once(self):
        """多线程并发首次 get_alert_notifier 只构造一个实例（双重检查锁）"""
        orig_cls = module.AlertNotifier
        created = []

        class CountingNotifier(orig_cls):
            def __init__(self, *args, **kwargs):
                created.append(1)
                super().__init__(*args, **kwargs)

        module.AlertNotifier = CountingNotifier
        try:
            results = []
            errors = []
            barrier = threading.Barrier(8)

            def worker():
                barrier.wait()
                try:
                    results.append(get_alert_notifier())
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
            module.AlertNotifier = orig_cls

    def test_concurrent_get_after_init_returns_same_instance(self):
        get_alert_notifier()
        instances = []

        def worker():
            instances.append(get_alert_notifier())

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(i is instances[0] for i in instances)


class TestAlertNotifierFallback:
    """SingletonManager 不可用时的 fallback 行为"""

    def test_fallback_still_singleton(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        a = get_alert_notifier()
        b = get_alert_notifier()
        assert a is b

    def test_fallback_config_passed_directly(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        first = get_alert_notifier({"custom": True})
        assert first.config.get("custom") is True

    def test_fallback_reset_works(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        first = get_alert_notifier()
        module.reset_alert_notifier()
        second = get_alert_notifier()
        assert first is not second
