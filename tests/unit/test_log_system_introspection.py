"""内省式学习层 — IdleDetector 单元测试（2026-11-13 改判据后重写）。

【为什么重写】旧实现只看进程内 `_last_activity`，测试直接改这个私有属性。2026-11-13 起判据下沉到
`agent/system_idle`（用户输入空闲 GetLastInputInfo + 进程内活动 + CPU，取 min）——
私有属性没有了，测试必须用**同一个公开缝**：

    system_idle.reset_activity_clock(ts)   # 拨进程内时钟
    monkeypatch.setattr(system_idle, "user_idle_seconds", lambda: None)  # 去掉系统级信号

为什么显式去掉 "用户输入" 信号：在 Windows 上跑测试时，用户刚操作过 ⇒ 真实读数可能是 0s，
这会让"进程内空闲"的用例偶发红（本机实测过）。CI 是 Linux，本来就没有这个信号。
"""
import time

import pytest

from agent import system_idle
from agent.log_system.introspection import IdleDetector


@pytest.fixture(autouse=True)
def _process_clock_only(monkeypatch):
    """只用进程内时钟判定（去掉系统级输入信号与 CPU 干扰）。"""
    monkeypatch.setattr(system_idle, "user_idle_seconds", lambda: None)
    monkeypatch.setattr(system_idle, "cpu_percent", lambda: 0.0)
    system_idle.reset_activity_clock(0.0)
    yield
    system_idle.reset_activity_clock()


class TestIdleDetector:
    """空闲检测器测试（idle_timeout=1s，max_cpu=100 ⇒ CPU 不参与）"""

    def setup_method(self):
        self.detector = IdleDetector(idle_timeout=1, max_cpu=100.0)

    def test_initial_state_is_idle(self):
        # 进程启动即算"很久没活动"（时钟在导入时固定），应视为空闲
        assert self.detector.is_idle() is True

    def test_detect_idle_after_timeout(self):
        system_idle.reset_activity_clock(time.time() - 2)
        assert self.detector.is_idle() is True

    def test_not_idle_recent_activity(self):
        system_idle.reset_activity_clock(time.time())
        assert self.detector.is_idle() is False

    def test_mark_activity(self):
        self.detector.mark_activity()
        assert self.detector.is_idle() is False

    def test_reset_after_mark(self):
        self.detector.mark_activity()
        time.sleep(1.5)
        assert self.detector.is_idle() is True

    def test_state_exposes_freshness(self):
        """读数要能回答"这个判断新不新鲜"：来源 / 两个维度秒数 / CPU / degraded。"""
        state = self.detector.state()
        assert state["idle_source"] == "process_only"   # 系统级信号被本夹具去掉了
        assert state["degraded"] is True
        assert state["process_idle_seconds"] >= 0
        assert state["cpu_percent"] == 0.0
