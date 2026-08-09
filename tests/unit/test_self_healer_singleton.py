"""self_healer 单例迁移单元测试

覆盖：
- 单例行为：唯一性、SingletonManager 注册、config 首次传入生效
- 自愈逻辑：gc_collect SUCCESS、未知动作 FAILED、禁用 SKIPPED、执行记录写入
- 异常恢复：动作内部异常返回 FAILED 而非抛出
- 重置：新实例、GC 回收、cleanup 钩子停止健康检查线程
- 并发首次初始化、fallback 行为
"""
import gc as _gc_module
import threading
import weakref

import pytest

import agent.monitoring.self_healer as module
from agent.monitoring.self_healer import (
    HealStatus,
    get_self_healer,
)
from agent.utils.singleton_manager import is_initialized


@pytest.fixture(autouse=True)
def _cleanup_singleton():
    """每个用例前后重置单例，保证测试隔离"""
    module.reset_self_healer()
    yield
    module.reset_self_healer()


class TestSelfHealerSingleton:
    """单例行为测试"""

    def test_get_self_healer_returns_same_instance(self):
        a = get_self_healer()
        b = get_self_healer()
        assert a is b

    def test_get_self_healer_registers_in_manager(self):
        get_self_healer()
        assert is_initialized("self_healer")

    def test_config_passed_on_first_create(self):
        """首次传入 config 时生效（通过 SingletonManager dict 通道）"""
        first = get_self_healer({"enabled": False})
        assert first._enabled is False
        # config 已存为实例配置
        assert first.config.get("enabled") is False

    def test_config_ignored_after_initialized(self):
        """已初始化后再次传 config 不触发重建"""
        first = get_self_healer()
        assert get_self_healer({"enabled": False}) is first

    def test_reset_returns_new_instance(self):
        first = get_self_healer()
        module.reset_self_healer()
        second = get_self_healer()
        assert first is not second

    def test_reset_releases_instance_for_gc(self):
        ref = weakref.ref(get_self_healer())
        module.reset_self_healer()
        _gc_module.collect()
        assert ref() is None

    def test_reset_idempotent_when_not_initialized(self):
        module.reset_self_healer()
        module.reset_self_healer()

    def test_reset_calls_cleanup_hook(self):
        """cleanup 钩子停止健康检查线程（running 时 stop）"""
        healer = get_self_healer()
        healer._running = True  # 模拟健康检查线程运行中
        module.reset_self_healer()
        assert healer._running is False


class TestHealLogic:
    """自愈逻辑测试"""

    def test_gc_collect_succeeds(self):
        result = get_self_healer().execute_action("gc_collect")
        assert result.status is HealStatus.SUCCESS
        assert result.action == "gc_collect"

    def test_unknown_action_fails(self):
        result = get_self_healer().execute_action("no_such_action")
        assert result.status is HealStatus.FAILED
        assert "未知动作" in result.message

    def test_disabled_healer_skips(self):
        healer = get_self_healer()
        healer._enabled = False
        result = healer.execute_action("gc_collect")
        assert result.status is HealStatus.SKIPPED

    def test_execution_record_written(self):
        healer = get_self_healer()
        healer.execute_action("gc_collect")
        records = healer.get_records(action="gc_collect")
        assert any(r["status"] == HealStatus.SUCCESS.value for r in records)


class TestExceptionRecovery:
    """异常恢复场景：动作内部异常返回 FAILED 而非抛出"""

    def test_gc_failure_returns_failed_result(self, monkeypatch):
        """gc.collect 抛异常时 execute_action 返回 FAILED 且不传播异常"""

        def boom():
            raise RuntimeError("模拟 GC 失败")

        monkeypatch.setattr(_gc_module, "collect", boom)
        result = get_self_healer().execute_action("gc_collect")
        assert result.status is HealStatus.FAILED
        assert "模拟 GC 失败" in result.message

    def test_callback_exception_does_not_break_execution(self):
        """回调抛异常被捕获，动作结果正常返回"""
        healer = get_self_healer()
        healer._on_heal_executed = lambda record: (_ for _ in ()).throw(
            RuntimeError("回调失败")
        )
        result = healer.execute_action("gc_collect")
        assert result.status is HealStatus.SUCCESS


class TestSelfHealerConcurrency:
    """并发场景测试"""

    def test_concurrent_first_get_initializes_once(self):
        """多线程并发首次 get_self_healer 只构造一个实例（双重检查锁）"""
        orig_cls = module.SelfHealer
        created = []

        class CountingHealer(orig_cls):
            def __init__(self, *args, **kwargs):
                created.append(1)
                super().__init__(*args, **kwargs)

        module.SelfHealer = CountingHealer
        try:
            results = []
            errors = []
            barrier = threading.Barrier(8)

            def worker():
                barrier.wait()
                try:
                    results.append(get_self_healer())
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
            module.SelfHealer = orig_cls

    def test_concurrent_get_after_init_returns_same_instance(self):
        get_self_healer()
        instances = []

        def worker():
            instances.append(get_self_healer())

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(i is instances[0] for i in instances)


class TestSelfHealerFallback:
    """SingletonManager 不可用时的 fallback 行为"""

    def test_fallback_still_singleton(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        a = get_self_healer()
        b = get_self_healer()
        assert a is b

    def test_fallback_config_passed_directly(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        first = get_self_healer({"enabled": False})
        assert first._enabled is False

    def test_fallback_reset_works(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        first = get_self_healer()
        module.reset_self_healer()
        second = get_self_healer()
        assert first is not second
