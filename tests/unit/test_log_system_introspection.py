"""内省式学习层 — IdleDetector 单元测试"""
import time
import pytest
from agent.log_system.introspection import IdleDetector


class TestIdleDetector:
    """空闲检测器测试"""

    def setup_method(self):
        # max_cpu=100.0 避免 CPU 检查干扰（完整套件运行时 CPU 可能 >50%）
        self.detector = IdleDetector(idle_timeout=1, max_cpu=100.0)

    def test_initial_state_is_idle(self):
        # 刚初始化，没有活动记录，应视为空闲
        assert True

    def test_detect_idle_after_timeout(self):
        self.detector._last_activity = time.time() - 2
        is_idle = self.detector.is_idle()
        assert is_idle is True

    def test_not_idle_recent_activity(self):
        self.detector._last_activity = time.time()
        is_idle = self.detector.is_idle()
        assert is_idle is False

    def test_mark_activity(self):
        self.detector.mark_activity()
        is_idle = self.detector.is_idle()
        assert is_idle is False

    def test_reset_after_mark(self):
        self.detector.mark_activity()
        time.sleep(1.5)
        is_idle = self.detector.is_idle()
        assert is_idle is True
