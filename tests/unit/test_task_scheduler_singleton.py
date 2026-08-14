"""task_scheduler 单例迁移单元测试

覆盖：单例唯一性、预注册任务、重置（新实例 + GC 回收 + cleanup 钩子）、
并发首次初始化只一次、SingletonManager 不可用时的 fallback、心跳检查不触发创建。
"""
import gc
import threading
import weakref

import pytest

import agent.task_scheduler as module
from agent.utils.singleton_manager import is_initialized


@pytest.fixture(autouse=True)
def _cleanup_singleton():
    """每个用例前后重置 task_scheduler 单例，保证测试隔离"""
    module.reset_scheduler()
    yield
    module.reset_scheduler()


class TestTaskSchedulerSingleton:
    """单例行为测试"""

    def test_get_scheduler_returns_same_instance(self):
        a = module.get_scheduler()
        b = module.get_scheduler()
        assert a is b

    def test_get_scheduler_preregisters_tasks(self):
        """首次创建时预注册周报/日志清理任务（工厂承载初始化）"""
        sched = module.get_scheduler()
        names = {t["name"] for t in sched.tasks}
        assert "生成周报" in names
        assert "清理旧日志" in names

    def test_get_scheduler_registers_in_singleton_manager(self):
        module.get_scheduler()
        assert is_initialized("task_scheduler")

    def test_reset_returns_new_instance(self):
        first = module.get_scheduler()
        module.reset_scheduler()
        second = module.get_scheduler()
        assert first is not second

    def test_reset_releases_instance_for_gc(self):
        """重置后实例仅被管理器持有，可被 GC 回收"""
        ref = weakref.ref(module.get_scheduler())
        module.reset_scheduler()
        gc.collect()
        assert ref() is None

    def test_reset_calls_cleanup_hook(self):
        """cleanup 钩子在 running 时调用 stop()"""
        sched = module.get_scheduler()
        sched.running = True  # 模拟调度器运行中
        module.reset_scheduler()
        assert sched.running is False  # stop() 已将 running 置 False

    def test_reset_idempotent_when_not_initialized(self):
        """未初始化时重复 reset 不报错"""
        module.reset_scheduler()
        module.reset_scheduler()


class TestTaskSchedulerConcurrency:
    """并发场景测试"""

    def test_concurrent_first_get_initializes_once(self):
        """多线程并发首次 get_scheduler 只构造一个实例（双重检查锁）"""
        # 【不易】前置断言：隔离失败时快速暴露。若 manager 缓存残留，
        # get_scheduler 将直接返回旧实例（created==0），掩盖并发语义问题。
        assert not is_initialized("task_scheduler"), (
            "task_scheduler 单例残留：测试隔离失败，请检查 reset 语义"
        )
        orig_cls = module.TaskScheduler
        created = []

        class CountingScheduler(orig_cls):
            def __init__(self, *args, **kwargs):
                created.append(1)
                super().__init__(*args, **kwargs)

        # 替换类以统计真实构造次数（SingletonManager 已捕获工厂引用，
        # 直接替换 module._create_scheduler 不会生效）
        module.TaskScheduler = CountingScheduler
        try:
            results = []
            errors = []
            barrier = threading.Barrier(8)

            def worker():
                barrier.wait()
                try:
                    results.append(module.get_scheduler())
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
            module.TaskScheduler = orig_cls

    def test_concurrent_get_after_init_returns_same_instance(self):
        """已初始化后并发 get 全部返回同一实例"""
        module.get_scheduler()
        instances = []

        def worker():
            instances.append(module.get_scheduler())

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(i is instances[0] for i in instances)


class TestTaskSchedulerFallback:
    """SingletonManager 不可用时的 fallback 行为"""

    def test_fallback_still_singleton(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        a = module.get_scheduler()
        b = module.get_scheduler()
        assert a is b

    def test_fallback_reset_works(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        first = module.get_scheduler()
        module.reset_scheduler()
        second = module.get_scheduler()
        assert first is not second


class TestHeartbeatCheck:
    """perform_heartbeat_check 不应触发单例创建"""

    def test_heartbeat_check_does_not_initialize_singleton(self):
        result = module.perform_heartbeat_check()
        assert not is_initialized("task_scheduler")
        assert result["checks"]["scheduler"]["status"] == "stopped"
