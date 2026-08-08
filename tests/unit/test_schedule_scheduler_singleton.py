"""scheduling 单例迁移单元测试

覆盖：
- 单例行为：唯一性、注册、reset/GC/幂等
- 生命周期（重点）：start 创建 daemon 线程、start 幂等、stop 置标志、未启动 stop 安全、任务注册
- cleanup 钩子（重点）：reset 停止运行中实例、未启动 reset 安全、重置后新实例干净
- 并发首次初始化、fallback 行为
"""
import gc
import threading
import weakref

import pytest

import agent.scheduling as module
from agent.scheduling import Scheduler, get_schedule_scheduler
from agent.utils.singleton_manager import get_singleton, is_initialized


@pytest.fixture(autouse=True)
def _cleanup_singleton():
    """每个用例前后重置单例（reset 触发 cleanup 停止后台线程）"""
    module.reset_schedule_scheduler()
    yield
    module.reset_schedule_scheduler()


class TestScheduleSchedulerSingleton:
    """单例行为测试"""

    def test_get_schedule_scheduler_returns_same_instance(self):
        a = get_schedule_scheduler()
        b = get_schedule_scheduler()
        assert a is b

    def test_registers_in_singleton_manager(self):
        get_schedule_scheduler()
        assert is_initialized("schedule_scheduler")

    def test_singleton_manager_channel_returns_same_instance(self):
        s = get_schedule_scheduler()
        assert get_singleton("schedule_scheduler") is s

    def test_factory_returns_scheduler(self):
        assert isinstance(module._create_schedule_scheduler(), Scheduler)

    def test_reset_returns_new_instance(self):
        first = get_schedule_scheduler()
        module.reset_schedule_scheduler()
        second = get_schedule_scheduler()
        assert first is not second

    def test_reset_releases_instance_for_gc(self):
        ref = weakref.ref(get_schedule_scheduler())
        module.reset_schedule_scheduler()
        gc.collect()
        assert ref() is None

    def test_reset_idempotent_when_not_initialized(self):
        module.reset_schedule_scheduler()
        module.reset_schedule_scheduler()


class TestSchedulerLifecycle:
    """生命周期测试（重点）"""

    def test_start_creates_daemon_thread(self):
        """start 创建守护线程"""
        sched = get_schedule_scheduler()
        sched.start()
        assert sched._running is True
        assert sched._thread is not None
        assert sched._thread.daemon is True
        sched.stop()

    def test_start_is_idempotent(self):
        """重复 start 不重建线程（已在运行直接返回）"""
        sched = get_schedule_scheduler()
        sched.start()
        first_thread = sched._thread
        sched.start()
        assert sched._thread is first_thread
        sched.stop()

    def test_stop_sets_running_false_and_stop_event(self):
        """stop 置 _running=False 且 stop_event 置位"""
        sched = get_schedule_scheduler()
        sched.start()
        assert sched._running is True
        sched.stop()
        assert sched._running is False
        assert sched._stop_event.is_set() is True

    def test_stop_when_not_started_is_safe(self):
        """未启动时 stop 安全（幂等）"""
        sched = get_schedule_scheduler()
        sched.stop()
        sched.stop()
        assert sched._running is False

    def test_add_task_registers_task(self, monkeypatch):
        """add_task 注册任务到 _tasks（持久化打桩避免写文件）"""
        sched = get_schedule_scheduler()
        monkeypatch.setattr(sched, "save_to_file", lambda: None)
        result = sched.add_task("backup", action="run_backup", interval_minutes=30)
        assert result["ok"] is True
        assert len(sched._tasks) == 1
        task = result["task"]
        assert task["name"] == "backup"
        assert task["interval_minutes"] == 30

    def test_add_task_validation(self):
        """add_task 校验：空名称 / 无间隔无 cron 均拒绝"""
        sched = get_schedule_scheduler()
        assert sched.add_task("  ").get("ok") is False
        assert sched.add_task("x").get("ok") is False

    def test_remove_task_removes_from_tasks(self, monkeypatch):
        """remove_task 从 _tasks 移除"""
        sched = get_schedule_scheduler()
        monkeypatch.setattr(sched, "save_to_file", lambda: None)
        result = sched.add_task("cleanup", interval_minutes=10)
        task_id = result["task"]["id"]
        removed = sched.remove_task(task_id)
        assert removed["ok"] is True
        assert task_id not in sched._tasks


class TestSchedulerCleanupHook:
    """cleanup 钩子测试（重点）"""

    def test_reset_stops_running_instance(self):
        """reset 触发 cleanup：运行中实例被 stop（_running=False + stop_event 置位）"""
        sched = get_schedule_scheduler()
        sched.start()
        assert sched._running is True
        module.reset_schedule_scheduler()
        assert sched._running is False
        assert sched._stop_event.is_set() is True

    def test_reset_when_not_started_is_safe(self):
        """未启动时 reset 安全（cleanup stop 幂等）"""
        get_schedule_scheduler()
        module.reset_schedule_scheduler()
        module.reset_schedule_scheduler()

    def test_reset_new_instance_is_clean(self):
        """重置后新实例无残留（_tasks 空、未运行）"""
        sched = get_schedule_scheduler()
        sched.add_task("t1", interval_minutes=5)
        module.reset_schedule_scheduler()
        fresh = get_schedule_scheduler()
        assert len(fresh._tasks) == 0
        assert fresh._running is False


class TestScheduleSchedulerConcurrency:
    """并发场景测试"""

    def test_concurrent_first_get_initializes_once(self):
        """多线程并发首次 get 只构造一个实例（双检锁）"""
        orig_cls = module.Scheduler
        created = []

        class CountingScheduler(orig_cls):
            def __init__(self):
                created.append(1)
                super().__init__()

        module.Scheduler = CountingScheduler
        try:
            results = []
            errors = []
            barrier = threading.Barrier(8)

            def worker():
                barrier.wait()
                try:
                    results.append(get_schedule_scheduler())
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
            module.Scheduler = orig_cls

    def test_concurrent_get_after_init_returns_same_instance(self):
        get_schedule_scheduler()
        instances = []

        def worker():
            instances.append(get_schedule_scheduler())

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(i is instances[0] for i in instances)


class TestScheduleSchedulerFallback:
    """SingletonManager 不可用时的 fallback 行为"""

    def test_fallback_still_singleton(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        a = get_schedule_scheduler()
        b = get_schedule_scheduler()
        assert a is b

    def test_fallback_reset_works(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        first = get_schedule_scheduler()
        module.reset_schedule_scheduler()
        second = get_schedule_scheduler()
        assert first is not second

    def test_fallback_lifecycle_works(self, monkeypatch):
        """fallback 模式下生命周期行为一致"""
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        sched = get_schedule_scheduler()
        sched.start()
        assert sched._running is True
        sched.stop()
        assert sched._running is False
