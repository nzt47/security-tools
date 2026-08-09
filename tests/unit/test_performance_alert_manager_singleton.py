"""performance._alert_manager 单例迁移单元测试

覆盖：
- 单例行为：唯一性、注册（单例名 performance_alert_manager）、config 首次传入生效、已初始化后忽略
- 生命周期：config 驱动的告警行为（CPU/内存触发、冷却、回调、持续告警）
- 清理钩子：该类无 start/stop（纯检查类）→ 无 cleanup 注册；验证 reset 无副作用 + GC 释放
- 并发首次初始化、fallback 行为
"""
import gc
import threading
import weakref

import pytest

import agent.monitoring.performance as module
from agent.monitoring.performance import (
    AlertConfig,
    PerformanceAlertManager,
    RuntimeSampler,
    get_alert_manager,
)
from agent.utils.singleton_manager import get_singleton, is_initialized


@pytest.fixture(autouse=True)
def _cleanup_singleton():
    """每个用例前后重置单例，保证测试隔离"""
    module.reset_performance_alert_manager()
    yield
    module.reset_performance_alert_manager()


class TestPerformanceAlertManagerSingleton:
    """单例行为测试"""

    def test_get_alert_manager_returns_same_instance(self):
        a = get_alert_manager()
        b = get_alert_manager()
        assert a is b

    def test_registers_in_singleton_manager(self):
        get_alert_manager()
        assert is_initialized("performance_alert_manager")

    def test_singleton_name_distinct_from_alert_manager(self):
        """单例名须与 alert_manager 区分（不冲突、不共享）"""
        get_alert_manager()
        assert is_initialized("performance_alert_manager")
        assert not is_initialized("alert_manager")

    def test_singleton_manager_channel_returns_same_instance(self):
        ev = get_alert_manager()
        assert get_singleton("performance_alert_manager") is ev

    def test_config_passed_on_first_create(self):
        """首次传入 config 生效（经 SingletonManager dict 通道）"""
        orig_cls = module.PerformanceAlertManager
        received = []

        class RecordingManager(orig_cls):
            def __init__(self, config=None):
                received.append(config)
                super().__init__(config)

        module.PerformanceAlertManager = RecordingManager
        try:
            config = AlertConfig(cpu_threshold=75.0)
            get_alert_manager(config)
            assert received == [config]
        finally:
            module.PerformanceAlertManager = orig_cls

    def test_config_ignored_after_initialized(self):
        """已初始化后再次传 config 不触发重建"""
        first = get_alert_manager()
        assert get_alert_manager(AlertConfig(cpu_threshold=90.0)) is first

    def test_factory_unpacks_config_channel(self):
        """工厂：SingletonManager dict 通道含 alert_config 键时解包"""
        config = AlertConfig(cpu_threshold=70.0)
        mgr = module._create_alert_manager({"alert_config": config})
        assert mgr.config.cpu_threshold == 70.0

    def test_factory_passes_plain_object_directly(self):
        """工厂：直接传入 AlertConfig 对象原样传递"""
        config = AlertConfig(cpu_threshold=65.0)
        mgr = module._create_alert_manager(config)
        assert mgr.config.cpu_threshold == 65.0

    def test_factory_default_when_none(self):
        """工厂：无 config 用默认配置"""
        mgr = module._create_alert_manager(None)
        assert mgr.config.cpu_threshold == 80.0

    def test_reset_returns_new_instance(self):
        first = get_alert_manager()
        module.reset_performance_alert_manager()
        second = get_alert_manager()
        assert first is not second

    def test_reset_releases_instance_for_gc(self):
        ref = weakref.ref(get_alert_manager())
        module.reset_performance_alert_manager()
        gc.collect()
        assert ref() is None

    def test_reset_idempotent_when_not_initialized(self):
        module.reset_performance_alert_manager()
        module.reset_performance_alert_manager()


class TestPerformanceAlertManagerLifecycle:
    """config 驱动的生命周期行为测试（该类无 start/stop）"""

    def _make_manager(self, **overrides):
        return PerformanceAlertManager(AlertConfig(**overrides))

    def test_cpu_threshold_trigger(self):
        """CPU 超阈值触发 cpu_high 告警"""
        mgr = self._make_manager(cpu_threshold=70.0)
        alerts = mgr.check_alerts({"cpu_percent": 85.0, "memory_percent": 20.0})
        assert len(alerts) == 1
        assert alerts[0]["alert_type"] == "cpu_high"

    def test_memory_threshold_trigger(self):
        """内存超阈值触发 memory_high 告警"""
        mgr = self._make_manager(memory_threshold=80.0)
        alerts = mgr.check_alerts({"cpu_percent": 20.0, "memory_percent": 95.0})
        assert len(alerts) == 1
        assert alerts[0]["alert_type"] == "memory_high"

    def test_below_threshold_no_alert(self):
        """指标低于阈值不触发告警"""
        mgr = self._make_manager(cpu_threshold=90.0, memory_threshold=95.0)
        alerts = mgr.check_alerts({"cpu_percent": 30.0, "memory_percent": 40.0})
        assert alerts == []

    def test_cooldown_suppresses_repeated_alert(self):
        """冷却期内同类型告警不重复触发"""
        mgr = self._make_manager(cpu_threshold=70.0, cooldown_seconds=60.0)
        first = mgr.check_alerts({"cpu_percent": 90.0, "memory_percent": 20.0})
        second = mgr.check_alerts({"cpu_percent": 90.0, "memory_percent": 20.0})
        assert len(first) == 1
        assert second == []

    def test_callback_invoked_on_alert(self):
        """触发告警时调用回调"""
        mgr = self._make_manager(cpu_threshold=70.0)
        received = []
        mgr.add_alert_callback(lambda alert_type, alert: received.append(alert_type))
        mgr.check_alerts({"cpu_percent": 95.0, "memory_percent": 20.0})
        assert received == ["cpu_high"]

    def test_sustained_alert_with_sampler(self):
        """持续超阈值（窗口内多次）触发持续告警"""
        mgr = self._make_manager(
            cpu_threshold=70.0,
            sustained_threshold_count=3,
            sustained_check_window=5,
        )
        sampler = RuntimeSampler()
        for _ in range(3):
            sampler.samples.append({"cpu_percent": 95.0, "memory_percent": 20.0})
        alerts = mgr.check_alerts({"cpu_percent": 95.0, "memory_percent": 20.0}, sampler)
        assert any(a["alert_type"] == "cpu_sustained_high" for a in alerts)


class TestPerformanceAlertManagerCleanupHook:
    """清理钩子测试（该类无 start/stop → 无 cleanup 注册，验证 reset 无副作用）"""

    def test_reset_does_not_break_instance_state(self):
        """reset 仅解除引用，不破坏已创建实例的可操作性"""
        mgr = get_alert_manager()
        mgr.add_alert_callback(lambda t, a: None)
        module.reset_performance_alert_manager()
        # 旧实例仍可正常检查（无资源生命周期，reset 无副作用）
        alerts = mgr.check_alerts({"cpu_percent": 99.0, "memory_percent": 99.0})
        assert len(alerts) >= 1

    def test_reset_after_use_is_safe(self):
        """使用后 reset 安全（无线程/资源需停止）"""
        mgr = get_alert_manager()
        mgr.check_alerts({"cpu_percent": 99.0, "memory_percent": 99.0})
        module.reset_performance_alert_manager()  # 不应抛异常
        module.reset_performance_alert_manager()

    def test_reset_then_get_is_fresh(self):
        """重置后新实例配置独立，无残留状态"""
        get_alert_manager(AlertConfig(cpu_threshold=50.0))
        module.reset_performance_alert_manager()
        fresh = get_alert_manager()
        assert fresh.config.cpu_threshold == 80.0  # 默认配置


class TestPerformanceAlertManagerConcurrency:
    """并发场景测试"""

    def test_concurrent_first_get_initializes_once(self):
        """多线程并发首次 get 只构造一个实例（双重检查锁）"""
        orig_cls = module.PerformanceAlertManager
        created = []

        class CountingManager(orig_cls):
            def __init__(self, config=None):
                created.append(1)
                super().__init__(config)

        module.PerformanceAlertManager = CountingManager
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
            module.PerformanceAlertManager = orig_cls

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


class TestPerformanceAlertManagerFallback:
    """SingletonManager 不可用时的 fallback 行为"""

    def test_fallback_still_singleton(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        a = get_alert_manager()
        b = get_alert_manager()
        assert a is b

    def test_fallback_config_passed_directly(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        config = AlertConfig(cpu_threshold=75.0)
        first = get_alert_manager(config)
        assert first.config.cpu_threshold == 75.0

    def test_fallback_reset_works(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        first = get_alert_manager()
        module.reset_performance_alert_manager()
        second = get_alert_manager()
        assert first is not second
