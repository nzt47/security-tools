# -*- coding: utf-8 -*-
"""空闲判定真闸门的守卫（2026-11-13，handoff_next: idle-detector-fake-gate）。

【为什么需要这组用例】
原来的 `IdleDetector` 只看进程内 `_last_activity`，而 `mark_activity()` 生产零调用 ⇒
开机 5 分钟后恒判空闲（实测 11-13 日志每 30 分钟准点跑、13 轮 0 洞察）。现在判据下沉到
`agent/system_idle`：用户输入空闲（Windows GetLastInputInfo）+ 进程内活动 + CPU，
两者取 min。本文件钉住：

1. **任一维度"刚活动过"就不算空闲**（不能只看一个）；
2. 取不到的读数返回 None 并标 degraded（**绝不**折算成 0 / "空闲"，L5 口径）；
3. CPU 忙 ⇒ 不空闲，且 reason=cpu_busy（可解释）；
4. `mark_activity()` 真的把进程内时钟拨回现在；
5. IdleDetector 与 HTTP 钩子共用**同一个**实例（否则钩子标记的活动引擎看不到）。
"""
from __future__ import annotations

import sys

import pytest

from agent import system_idle
from agent.log_system import introspection as intro


@pytest.fixture(autouse=True)
def _clean_clock():
    system_idle.reset_activity_clock(0.0)
    yield
    system_idle.reset_activity_clock()


def _patch(monkeypatch, *, user=None, process=0.0, cpu=None):
    monkeypatch.setattr(system_idle, "user_idle_seconds", lambda: user)
    monkeypatch.setattr(system_idle, "seconds_since_activity", lambda: process)
    monkeypatch.setattr(system_idle, "cpu_percent", lambda: cpu)


class TestIdleFormula:
    def test_idle_requires_both_dimensions(self, monkeypatch):
        _patch(monkeypatch, user=600.0, process=700.0, cpu=5.0)
        st = system_idle.state()
        assert st["is_idle"] is True
        assert st["idle_source"] == "user_input+process"
        assert st["idle_seconds"] == 600.0  # min(600, 700)

    def test_recent_user_input_blocks_idle(self, monkeypatch):
        _patch(monkeypatch, user=10.0, process=700.0, cpu=5.0)
        st = system_idle.state()
        assert st["is_idle"] is False
        assert st["reason"] == "user_input"

    def test_recent_process_activity_blocks_idle(self, monkeypatch):
        _patch(monkeypatch, user=700.0, process=12.0, cpu=5.0)
        st = system_idle.state()
        assert st["is_idle"] is False
        assert st["reason"] == "process_activity"

    def test_cpu_busy_blocks_idle_and_says_why(self, monkeypatch):
        _patch(monkeypatch, user=700.0, process=700.0, cpu=88.0)
        st = system_idle.state()
        assert st["is_idle"] is False
        assert st["reason"] == "cpu_busy"
        assert st["cpu_percent"] == 88.0

    def test_missing_user_signal_degrades_not_idles(self, monkeypatch):
        """非 Windows（取不到用户输入）⇒ 标 degraded，只用进程内信号（不能凭空算空闲）。"""
        _patch(monkeypatch, user=None, process=700.0, cpu=5.0)
        st = system_idle.state()
        assert st["degraded"] is True
        assert st["idle_source"] == "process_only"
        assert st["user_input_idle_seconds"] is None
        assert st["is_idle"] is True  # 进程内确实空了 700s ⇒ 可按空闲处理，但读数如实标 degraded

    def test_missing_cpu_is_unknown_not_zero(self, monkeypatch):
        _patch(monkeypatch, user=700.0, process=700.0, cpu=None)
        st = system_idle.state()
        assert st["cpu_percent"] is None
        assert st["is_idle"] is True  # 无 CPU 读数不阻断，但读数保留 None（不折算成 0）

    def test_mark_activity_resets_process_clock(self, monkeypatch):
        _patch(monkeypatch, user=700.0, process=0.0, cpu=5.0)
        monkeypatch.setattr(system_idle, "seconds_since_activity",
                            lambda: max(0.0, 0.0 if system_idle._last_activity > 0 else 999.0))
        system_idle.mark_activity()
        assert system_idle.state()["process_idle_seconds"] == 0.0


class TestIdleDetectorDelegates:
    def test_detector_uses_same_formula(self, monkeypatch):
        _patch(monkeypatch, user=600.0, process=650.0, cpu=3.0)
        assert intro.IdleDetector().is_idle() is True
        _patch(monkeypatch, user=1.0, process=650.0, cpu=3.0)
        assert intro.IdleDetector().is_idle() is False

    def test_detector_is_a_singleton(self):
        assert intro.get_idle_detector() is intro.get_idle_detector()

    def test_engine_shares_the_singleton(self):
        """引擎与 HTTP 钩子必须是同一个检测器 —— 否则钩子标记的活动引擎看不到（两处真相）。"""
        engine = intro.IntrospectionEngine()
        assert engine.idle_detector is intro.get_idle_detector()

    def test_get_status_exposes_idle_freshness(self, monkeypatch):
        _patch(monkeypatch, user=42.0, process=10.0, cpu=7.0)
        status = intro.IntrospectionEngine().get_status()
        idle = status["idle"]
        assert idle["idle_source"] == "user_input+process"
        assert idle["user_input_idle_seconds"] == 42.0
        assert idle["process_idle_seconds"] == 10.0
        assert "last_activity_age_s" in idle and "degraded" in idle


@pytest.mark.skipif(sys.platform != "win32", reason="GetLastInputInfo 是 Windows 专属")
def test_get_last_input_info_is_readable_on_windows():
    """真机校验：这条系统级信号必须能读到一个非负秒数（否则闸门又只能靠进程内信号）。"""
    value = system_idle.user_idle_seconds()
    assert value is not None and value >= 0
