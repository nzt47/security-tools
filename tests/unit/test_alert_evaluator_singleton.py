"""alert_evaluator 单例迁移单元测试

覆盖：
- 单例行为：唯一性、注册、工厂 config 通道解包（evaluation_interval/pending_duration）
- start/stop 生命周期：状态往返、模块级 start、重复 start 幂等、后台线程
- cleanup 钩子：重置时停止运行中的实例、线程收敛、未启动重置安全
- 并发首次初始化、fallback 行为
"""
import gc
import threading
import weakref

import pytest

import agent.monitoring.alert_evaluator as module
from agent.monitoring.alert_evaluator import get_alert_evaluator
from agent.utils.singleton_manager import get_singleton, is_initialized


@pytest.fixture(autouse=True)
def _cleanup_singleton():
    """每个用例前后重置单例，保证测试隔离"""
    module.reset_alert_evaluator()
    yield
    module.reset_alert_evaluator()


class TestAlertEvaluatorSingleton:
    """单例行为测试"""

    def test_get_alert_evaluator_returns_same_instance(self):
        a = get_alert_evaluator()
        b = get_alert_evaluator()
        assert a is b

    def test_registers_in_singleton_manager(self):
        get_alert_evaluator()
        assert is_initialized("alert_evaluator")

    def test_singleton_manager_channel_returns_same_instance(self):
        """get_singleton 与 getter 共享同一实例"""
        ev = get_alert_evaluator()
        assert get_singleton("alert_evaluator") is ev

    def test_factory_unpacks_config_channel(self):
        """工厂：SingletonManager dict 通道含 evaluation_interval/pending_duration 时解包"""
        ev = module._create_alert_evaluator(
            {"evaluation_interval": 5.0, "pending_duration": 10.0}
        )
        assert ev.evaluation_interval == 5.0
        assert ev.pending_duration == 10.0

    def test_factory_ignores_plain_dict(self):
        """工厂：非通道 dict（不含特定键）用默认参数，不误解包"""
        ev = module._create_alert_evaluator({"some_key": 1})
        assert ev.evaluation_interval == 30.0
        assert ev.pending_duration == 60.0

    def test_factory_default_when_none(self):
        """工厂：无 config 用默认参数"""
        ev = module._create_alert_evaluator(None)
        assert ev.evaluation_interval == 30.0
        assert ev.pending_duration == 60.0

    def test_reset_returns_new_instance(self):
        first = get_alert_evaluator()
        module.reset_alert_evaluator()
        second = get_alert_evaluator()
        assert first is not second

    def test_reset_releases_instance_for_gc(self):
        ref = weakref.ref(get_alert_evaluator())
        module.reset_alert_evaluator()
        gc.collect()
        assert ref() is None

    def test_reset_idempotent_when_not_initialized(self):
        module.reset_alert_evaluator()
        module.reset_alert_evaluator()


class TestAlertEvaluatorLifecycle:
    """start/stop 生命周期测试（重点）"""

    def test_start_then_stop_restores_state(self):
        """生命周期：start → running，stop → stopped"""
        ev = get_alert_evaluator()
        ev.start()
        assert ev._running is True
        ev.stop()
        assert ev._running is False

    def test_start_creates_background_thread(self):
        """start 创建 daemon 后台评估线程"""
        ev = get_alert_evaluator()
        ev.start()
        assert ev._evaluation_thread is not None
        assert ev._evaluation_thread.daemon is True
        ev.stop()

    def test_start_is_idempotent(self):
        """重复 start 不重复启动（running 时直接返回）"""
        ev = get_alert_evaluator()
        ev.start()
        thread = ev._evaluation_thread
        ev.start()
        assert ev._running is True
        assert ev._evaluation_thread is thread  # 未重建线程
        ev.stop()

    def test_module_level_start_then_instance_stop(self):
        """模块级 start_alert_evaluator 启动 + 实例 stop 状态往返"""
        ev = module.start_alert_evaluator(evaluation_interval=0.1)
        assert ev._running is True
        ev.stop()
        assert ev._running is False

    def test_stop_when_not_started_is_safe(self):
        """未启动直接 stop 安全（stop 幂等）"""
        ev = get_alert_evaluator()
        ev.stop()
        assert ev._running is False


class TestAlertEvaluatorCleanupHook:
    """cleanup 钩子测试（重点）"""

    def test_reset_stops_running_instance(self):
        """cleanup 钩子：重置时停止运行中的实例"""
        ev = get_alert_evaluator()
        ev.start()
        assert ev._running is True
        module.reset_alert_evaluator()
        assert ev._running is False  # 旧实例已被 cleanup 钩子停止

    def test_reset_joins_evaluation_thread(self):
        """cleanup 钩子：重置后评估线程已收敛（stop 内 join）

        注意：默认单例 evaluation_interval=30s，线程处于长 sleep，
        stop() 的 join(timeout=5) 无法等到收敛；此处用 SingletonManager
        通道首建小间隔实例验证线程真实退出。
        """
        ev = get_singleton(
            "alert_evaluator",
            {"evaluation_interval": 0.1, "pending_duration": 0.5},
        )
        ev.start()
        thread = ev._evaluation_thread
        module.reset_alert_evaluator()
        assert not thread.is_alive()

    def test_reset_without_start_is_safe(self):
        """未启动实例重置安全（cleanup 对 stopped 实例幂等）"""
        get_alert_evaluator()
        module.reset_alert_evaluator()  # 不应抛异常
        module.reset_alert_evaluator()

    def test_reset_then_get_starts_fresh(self):
        """重置后新实例可正常 start/stop（无残留状态）"""
        ev = get_alert_evaluator()
        ev.start()
        module.reset_alert_evaluator()
        fresh = get_alert_evaluator()
        assert fresh is not ev
        fresh.start()
        assert fresh._running is True
        fresh.stop()


class TestAlertEvaluatorConcurrency:
    """并发场景测试"""

    def test_concurrent_first_get_initializes_once(self):
        """多线程并发首次 get 只构造一个实例（双重检查锁）"""
        orig_cls = module.AlertEvaluator
        created = []

        class CountingEvaluator(orig_cls):
            def __init__(self, evaluation_interval=30.0, pending_duration=60.0):
                created.append(1)
                super().__init__(evaluation_interval, pending_duration)

        module.AlertEvaluator = CountingEvaluator
        try:
            results = []
            errors = []
            barrier = threading.Barrier(8)

            def worker():
                barrier.wait()
                try:
                    results.append(get_alert_evaluator())
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
            module.AlertEvaluator = orig_cls

    def test_concurrent_get_after_init_returns_same_instance(self):
        get_alert_evaluator()
        instances = []

        def worker():
            instances.append(get_alert_evaluator())

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(i is instances[0] for i in instances)


class TestAlertEvaluatorFallback:
    """SingletonManager 不可用时的 fallback 行为"""

    def test_fallback_still_singleton(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        a = get_alert_evaluator()
        b = get_alert_evaluator()
        assert a is b

    def test_fallback_reset_works(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        first = get_alert_evaluator()
        module.reset_alert_evaluator()
        second = get_alert_evaluator()
        assert first is not second

    def test_fallback_start_alert_evaluator_works(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        ev = module.start_alert_evaluator(evaluation_interval=0.1)
        assert ev._running is True
        ev.stop()
