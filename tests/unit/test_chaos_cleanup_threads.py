"""chaos_injector stop_cleanup_threads 单元测试 [TLM-AUDIT-P3]

验证：
- inject_cpu_pressure 后 _cleanup_threads 保存引用
- stop_cleanup_threads join 所有线程
- stop 能立即唤醒 Event.wait（替代 time.sleep）
- stop 后子进程被 terminate
- stop_cleanup_threads 幂等
- clear_all 触发 stop_cleanup_threads
"""
import threading
import time
import pytest
from unittest.mock import patch, MagicMock, PropertyMock

from agent.monitoring.chaos_injector import ChaosInjector


class TestCleanupThreadTracking:
    """cleanup_monitor 线程追踪验证"""

    @patch('multiprocessing.cpu_count', return_value=1)
    @patch('multiprocessing.Process')
    def test_cleanup_thread_saved_after_inject(self, mock_process_class, mock_cpu_count):
        """inject_cpu_pressure 后 _cleanup_threads 非空"""
        mock_process = MagicMock()
        mock_process.is_alive.return_value = False
        mock_process_class.return_value = mock_process

        injector = ChaosInjector()
        try:
            injector.inject_cpu_pressure(duration_ms=100)
            # cleanup_monitor 线程应被保存到 _cleanup_threads
            assert len(injector._cleanup_threads) == 1, \
                "inject_cpu_pressure 后应有 1 个 cleanup 线程"
            assert injector._cleanup_threads[0].name == "chaos-cleanup"
        finally:
            injector.stop_cleanup_threads(timeout=2.0)

    def test_stop_cleanup_threads_joins_all(self):
        """stop_cleanup_threads 后所有线程退出"""
        injector = ChaosInjector()

        # 创建一个短时线程模拟 cleanup_monitor
        def short_task():
            time.sleep(0.2)

        t = threading.Thread(target=short_task, daemon=True, name="test-cleanup")
        t.start()
        injector._cleanup_threads.append(t)

        assert t.is_alive()
        injector.stop_cleanup_threads(timeout=2.0)
        assert not t.is_alive(), "线程应已退出"
        assert len(injector._cleanup_threads) == 0, "列表应被清空"

    def test_stop_cleanup_threads_wakes_up_event_wait(self):
        """stop 能立即唤醒 Event.wait（无需等到 timeout）"""
        injector = ChaosInjector()

        # 创建一个阻塞在 Event.wait 的线程（模拟 cleanup_monitor）
        woken_up = threading.Event()

        def waiting_task():
            # 模拟 cleanup_monitor 的 Event.wait
            if injector._cleanup_stop_event.wait(timeout=10.0):
                woken_up.set()
            return

        t = threading.Thread(target=waiting_task, daemon=True, name="test-wait")
        t.start()
        injector._cleanup_threads.append(t)

        # 确保线程已进入 wait
        time.sleep(0.1)
        assert t.is_alive()

        # stop 应立即唤醒（无需等到 10s timeout）
        t0 = time.time()
        injector.stop_cleanup_threads(timeout=2.0)
        elapsed = time.time() - t0

        assert elapsed < 1.0, f"stop 应在 1s 内完成（Event.wait 立即唤醒），实际 {elapsed:.2f}s"
        assert woken_up.is_set(), "线程应被唤醒"
        assert not t.is_alive()

    def test_stop_cleanup_threads_idempotent(self):
        """二次调用 stop_cleanup_threads 不报错（幂等性）"""
        injector = ChaosInjector()

        # 第一次调用（空列表）
        injector.stop_cleanup_threads(timeout=1.0)
        # 第二次调用（仍空）
        injector.stop_cleanup_threads(timeout=1.0)
        # 验证 _cleanup_stop_event 被重置（支持后续注入）
        assert not injector._cleanup_stop_event.is_set(), \
            "stop_event 应被重置以支持后续注入"

    @patch('multiprocessing.cpu_count', return_value=1)
    @patch('multiprocessing.Process')
    def test_cleanup_terminates_child_processes(self, mock_process_class, mock_cpu_count):
        """stop 后子进程被 terminate"""
        mock_process = MagicMock()
        mock_process.is_alive.return_value = True  # 模拟进程仍在运行
        mock_process_class.return_value = mock_process

        injector = ChaosInjector()
        # 注入极短持续时间的 CPU 压力
        injector.inject_cpu_pressure(duration_ms=50)

        # 立即 stop，应唤醒 cleanup_monitor 并 terminate 子进程
        injector.stop_cleanup_threads(timeout=2.0)

        # 验证 mock_process.terminate 被调用
        assert mock_process.terminate.called, \
            "stop 后子进程应被 terminate"

    def test_clear_all_calls_stop_cleanup(self):
        """clear_all 触发 stop_cleanup_threads"""
        injector = ChaosInjector()

        # 用 spy 监控 stop_cleanup_threads 调用
        with patch.object(injector, 'stop_cleanup_threads') as spy_stop:
            injector.clear_all()
            assert spy_stop.called, "clear_all 应调用 stop_cleanup_threads"
            # 验证传入的 timeout 参数
            call_args = spy_stop.call_args
            if call_args.args:
                assert call_args.args[0] > 0, "timeout 应为正数"
