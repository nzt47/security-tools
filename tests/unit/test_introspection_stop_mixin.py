"""IntrospectionEngine stop_background_loop 单元测试 [TLM-AUDIT-002]

验证 StopMixin 应用后：
- stop_background_loop 真正 join 线程（修复原仅置 None 的缺陷）
- _stop_event / _should_stop 行为正确
- 幂等性
"""
import threading
import time
import pytest
from unittest.mock import patch, MagicMock

from agent.log_system.introspection import IntrospectionEngine


class TestStopBackgroundLoop:
    """stop_background_loop 优雅关闭行为验证"""

    @pytest.fixture
    def engine(self):
        """创建 IntrospectionEngine 实例（mock 重型依赖避免实际执行）"""
        with patch.object(IntrospectionEngine, 'run_cycle', return_value=None):
            eng = IntrospectionEngine()
            yield eng
            # 兜底清理：确保测试结束时线程已停止
            if not eng._stop_event.is_set():
                eng.stop_background_loop(timeout=2.0)

    def test_stop_joins_thread(self, engine):
        """stop_background_loop 后线程真正退出（不再是仅置 None）"""
        # 用极短间隔启动，验证 stop 能立即唤醒
        engine.start_background_loop(interval_seconds=1)
        assert engine._thread is not None
        assert engine._thread.is_alive()
        # 保存线程引用（stop_background_loop 内部会置 _thread=None）
        thread = engine._thread

        # stop 应在 5s 内 join 完成（实际应 < 1s 因 Event.wait 立即唤醒）
        result = engine.stop_background_loop(timeout=5.0)
        assert result is True, "stop 应返回 True 表示 join 成功"
        assert not thread.is_alive(), "线程应已退出"
        assert engine._thread is None, "_thread 应被清理为 None"

    def test_stop_sets_stop_event(self, engine):
        """stop_background_loop 后 _stop_event 被 set"""
        engine.start_background_loop(interval_seconds=1)
        assert engine._stop_event.is_set() is False

        engine.stop_background_loop(timeout=5.0)
        assert engine._stop_event.is_set() is True
        assert engine._should_stop() is True

    def test_stop_idempotent(self, engine):
        """二次调用 stop_background_loop 不报错（幂等性）"""
        engine.start_background_loop(interval_seconds=1)
        engine.stop_background_loop(timeout=5.0)

        # 二次调用应直接返回 True，不抛异常
        result = engine.stop_background_loop(timeout=5.0)
        assert result is True

    def test_stop_returns_true_when_already_stopped(self, engine):
        """未启动时调用 stop 应返回 True（_stop_event 未 set 但无线程可 join）"""
        result = engine.stop_background_loop(timeout=5.0)
        assert result is True

    def test_stop_wakes_up_long_interval(self, engine):
        """stop 能唤醒长间隔（如 1800s）的 wait，无需等到超时"""
        # 启动 1800s 间隔的循环（模拟生产配置）
        engine.start_background_loop(interval_seconds=1800)
        thread = engine._thread

        # 立即 stop，应在 5s 内完成（实际 < 1s）
        t0 = time.time()
        result = engine.stop_background_loop(timeout=5.0)
        elapsed = time.time() - t0

        assert result is True
        assert elapsed < 2.0, f"stop 应在 2s 内完成（Event.wait 立即唤醒），实际 {elapsed:.2f}s"
        assert not thread.is_alive()

    def test_restart_after_stop(self, engine):
        """stop 后可重启（_stop_event.clear 在 start 中调用）"""
        engine.start_background_loop(interval_seconds=1)
        engine.stop_background_loop(timeout=5.0)
        assert engine._stop_event.is_set() is True

        # 重启：start 应 clear _stop_event
        engine.start_background_loop(interval_seconds=1)
        assert engine._stop_event.is_set() is False
        assert engine._thread.is_alive()

        engine.stop_background_loop(timeout=5.0)
        assert engine._stop_event.is_set() is True


class TestStopMixinIntegration:
    """StopMixin 基类行为验证"""

    def test_stop_mixin_attributes_initialized(self):
        """IntrospectionEngine 实例拥有 StopMixin 的属性"""
        with patch.object(IntrospectionEngine, 'run_cycle', return_value=None):
            eng = IntrospectionEngine()
            assert hasattr(eng, '_stop_event')
            assert hasattr(eng, '_registered_threads')
            assert hasattr(eng, '_thread_lock')
            assert isinstance(eng._stop_event, threading.Event)
            assert isinstance(eng._registered_threads, list)
            assert eng._stop_event.is_set() is False
            assert eng.is_running() is True

    def test_register_thread_tracks_handles(self):
        """register_thread 正确收集线程句柄"""
        with patch.object(IntrospectionEngine, 'run_cycle', return_value=None):
            eng = IntrospectionEngine()

            # 创建一个短时线程并注册
            def dummy():
                time.sleep(0.1)

            t = threading.Thread(target=dummy, daemon=True)
            t.start()
            eng.register_thread(t)
            assert len(eng._registered_threads) == 1
            assert eng._registered_threads[0] is t

            t.join(timeout=2.0)
            # stop 时清理已退出的线程引用
            eng.stop(timeout=1.0)

    def test_on_stop_default_noop(self):
        """默认 _on_stop 是 no-op，不抛异常"""
        with patch.object(IntrospectionEngine, 'run_cycle', return_value=None):
            eng = IntrospectionEngine()
            # 调用 stop 应正常完成（_on_stop 默认 pass）
            result = eng.stop(timeout=1.0)
            assert result is True
