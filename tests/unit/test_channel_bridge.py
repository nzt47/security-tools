"""扩展通道事件接线层（channel_bridge）单元测试

【回归什么】2026-10-19 之前 `/api/extensions/channels/send` 没有任何产品调用方，
通道"装了但不会自己发"。本轮把告警（触发/恢复）接到通道上，本测试钉住五件事：

  1. 出站摘要**只含白名单字段**（密钥型 label 必须被丢掉）；
  2. 只发给**已装且 enabled** 的通道；
  3. 同一告警同一事件在冷却窗口内只发一次；
  4. 失败重试 1 次；两次都失败只记 failed，**不抛**；
  5. `CP_CHANNEL_EVENTS=0` ⇒ 整体关闭，零调用；桥接层任何异常都不得外抛。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.extensions import channel_bridge as bridge


class _FakeInstaller:
    """记录 send_message 调用的最小安装器"""

    def __init__(self, channels, fail_times=0):
        self.channels = channels
        self.fail_times = fail_times
        self.sent = []

    def list_installed_channels(self):
        return self.channels

    def send_message(self, channel_id, message):
        self.sent.append((channel_id, message))
        if self.fail_times > 0:
            self.fail_times -= 1
            return False, "boom"
        return True, "ok"


def _alert(**kw):
    base = dict(name="HighCPU", severity=SimpleNamespace(value="warning"),
                value=91.2, threshold=85.0, condition="cpu > 85",
                message="CPU 持续偏高", labels={"env": "prod"},
                started_at=100.0, resolved_at=160.0)
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.delenv("CP_CHANNEL_EVENTS", raising=False)
    bridge._reset_state_for_tests()
    yield
    bridge._reset_state_for_tests()


class TestRender:
    def test_firing_summary_carries_value_and_drops_secret_labels(self):
        msg = bridge.render_alert(
            _alert(labels={"env": "prod", "api_token": "SECRET-DO-NOT-LEAK"}), "firing")

        assert "HighCPU" in msg and "触发" in msg
        assert "91.2" in msg and "85.0" in msg
        assert "env=prod" in msg
        assert "SECRET" not in msg          # 密钥型 label 一律不出站
        assert "api_token" not in msg

    def test_resolved_summary_reports_duration(self):
        msg = bridge.render_alert(_alert(), "resolved")
        assert "恢复" in msg
        assert "60s" in msg                 # resolved_at - started_at

    def test_long_message_is_truncated_but_keeps_lines(self):
        msg = bridge.render_alert(_alert(message="x" * 5000), "firing")
        assert len(msg) <= bridge._MAX_MESSAGE_CHARS
        assert "\n" in msg


class TestDispatch:
    def test_no_enabled_channel_is_a_noop(self):
        inst = _FakeInstaller([{"ext_id": "webhook", "enabled": False}])
        out = bridge.dispatch_alert(_alert(), "firing", installer=inst)

        assert out["ok"] is True and out["reason"] == "no_enabled_channel"
        assert inst.sent == []

    def test_sends_only_to_enabled_channels(self):
        inst = _FakeInstaller([
            {"ext_id": "on", "enabled": True},
            {"ext_id": "off", "enabled": False},
            {"ext_id": "gone", "enabled": True, "status": "uninstalled"},
        ])
        out = bridge.dispatch_alert(_alert(), "firing", installer=inst)

        assert out["sent"] == ["on"]
        assert [c for c, _ in inst.sent] == ["on"]
        assert "HighCPU" in inst.sent[0][1]

    def test_cooldown_suppresses_duplicate_within_window(self):
        inst = _FakeInstaller([{"ext_id": "on", "enabled": True}])

        first = bridge.dispatch_alert(_alert(), "firing", installer=inst, now=1000.0)
        second = bridge.dispatch_alert(_alert(), "firing", installer=inst, now=1010.0)

        assert first["sent"] == ["on"]
        assert second["sent"] == [] and second["skipped"] == ["on"]
        assert len(inst.sent) == 1

    def test_cooldown_expires(self):
        inst = _FakeInstaller([{"ext_id": "on", "enabled": True}])
        bridge.dispatch_alert(_alert(), "firing", installer=inst, now=1000.0)
        later = bridge.dispatch_alert(_alert(), "firing", installer=inst,
                                      now=1000.0 + bridge._COOLDOWN_SEC + 1)

        assert later["sent"] == ["on"]
        assert len(inst.sent) == 2

    def test_retries_once_then_succeeds(self):
        inst = _FakeInstaller([{"ext_id": "on", "enabled": True}], fail_times=1)
        out = bridge.dispatch_alert(_alert(), "firing", installer=inst)

        assert out["sent"] == ["on"]
        assert len(inst.sent) == 2          # 首发失败 + 1 次重试

    def test_both_attempts_fail_records_failed_without_raising(self):
        inst = _FakeInstaller([{"ext_id": "on", "enabled": True}], fail_times=99)
        out = bridge.dispatch_alert(_alert(), "firing", installer=inst)

        assert out["ok"] is False
        assert out["failed"][0]["channel_id"] == "on"
        assert "boom" in out["failed"][0]["error"]

    def test_env_switch_disables_everything(self, monkeypatch):
        monkeypatch.setenv("CP_CHANNEL_EVENTS", "0")
        inst = _FakeInstaller([{"ext_id": "on", "enabled": True}])
        out = bridge.dispatch_alert(_alert(), "firing", installer=inst)

        assert out["reason"] == "disabled_by_env"
        assert inst.sent == []

    def test_installer_errors_are_swallowed(self):
        class _Boom:
            def list_installed_channels(self):
                raise RuntimeError("store broken")

        out = bridge.dispatch_alert(_alert(), "firing", installer=_Boom())

        assert out["ok"] is False
        assert "store broken" in out["reason"]

    def test_dispatch_event_uses_same_pipeline(self):
        inst = _FakeInstaller([{"ext_id": "on", "enabled": True}])
        out = bridge.dispatch_event("task.done", "夜间任务完成",
                                    fields={"job": "nightly", "token": "LEAK"},
                                    installer=inst)

        assert out["sent"] == ["on"]
        body = inst.sent[0][1]
        assert "task.done" in body and "nightly" in body
        assert "LEAK" not in body


class TestAlertManagerWiring:
    """AlertManager 是告警状态变化的唯一汇合点 —— 这里锁"两条路径都接线"与"不外抛"."""

    def _manager(self):
        # 绕过 __init__（它要读配置/起线程），只测接线方法本身
        from agent.monitoring.alert_manager import AlertManager
        return object.__new__(AlertManager)

    def _state_change(self, mgr, alert, prev, new):
        from agent.monitoring.alert_evaluator import AlertState
        mgr._send_alert_notification = lambda a: None
        mgr._send_recovery_notification = lambda a: None
        mgr._check_heal_action = lambda a: None
        mgr._on_alert_callback = None
        mgr._on_evaluator_state_change(alert, prev, new)

    def test_firing_and_resolved_both_dispatch(self, monkeypatch):
        calls = []
        monkeypatch.setattr(bridge, "dispatch_alert",
                            lambda alert, transition: calls.append(transition) or {"ok": True})

        from agent.monitoring.alert_evaluator import AlertState
        mgr = self._manager()
        alert = _alert()
        self._state_change(mgr, alert, AlertState.PENDING, AlertState.FIRING)
        self._state_change(mgr, alert, AlertState.FIRING, AlertState.INACTIVE)

        assert calls == ["firing", "resolved"]

    def test_dispatch_failure_does_not_break_state_change(self, monkeypatch):
        def _boom(alert, transition):
            raise RuntimeError("channel exploded")

        monkeypatch.setattr(bridge, "dispatch_alert", _boom)

        from agent.monitoring.alert_evaluator import AlertState
        mgr = self._manager()
        # 不应抛：通道故障绝不能反过来打断告警/自愈主流程
        self._state_change(mgr, _alert(), AlertState.PENDING, AlertState.FIRING)


class TestSchedulerWiring:
    """TaskScheduler.run_task 是"任务完成/失败"的唯一执行汇合点。

    默认**只发失败**（心跳/零 token 作业失败才是运维要看的）；
    任务自己写 `notify_on_complete: true` 才成功也发。
    """

    def _scheduler(self, body):
        from agent.task_scheduler import TaskScheduler
        s = object.__new__(TaskScheduler)
        s._run_task_body = body
        return s

    def test_failure_dispatches_with_severity_and_dedup_key(self, monkeypatch):
        calls = []
        monkeypatch.setattr(bridge, "dispatch_event",
                            lambda *a, **kw: calls.append((a, kw)) or {"ok": True})
        s = self._scheduler(lambda task: {"status": "failed", "error": "boom",
                                          "duration_ms": 5})
        out = s.run_task({"task_id": "t1", "name": "nightly"}, trigger="manual")

        assert out["status"] == "failed"
        assert calls, "失败任务必须出站"
        assert calls[0][0][0] == "task.failed"
        assert calls[0][1]["severity"] == "warning"
        assert calls[0][1]["dedup_key"] == "t1"
        assert calls[0][1]["fields"]["error"] == "boom"

    def test_success_is_silent_unless_task_opts_in(self, monkeypatch):
        calls = []
        monkeypatch.setattr(bridge, "dispatch_event",
                            lambda *a, **kw: calls.append((a, kw)) or {"ok": True})
        s = self._scheduler(lambda task: {"status": "success", "duration_ms": 1})
        s.run_task({"task_id": "t1", "name": "heartbeat"}, trigger="manual")

        assert calls == [], "成功任务默认不出站（否则通道变日志倾倒口）"

    def test_success_with_opt_in_dispatches(self, monkeypatch):
        calls = []
        monkeypatch.setattr(bridge, "dispatch_event",
                            lambda *a, **kw: calls.append((a, kw)) or {"ok": True})
        s = self._scheduler(lambda task: {"status": "success", "duration_ms": 1})
        s.run_task({"task_id": "t2", "name": "report",
                    "notify_on_complete": True}, trigger="manual")

        assert calls and calls[0][0][0] == "task.completed"

    def test_channel_failure_does_not_break_task_execution(self, monkeypatch):
        def _boom(*a, **kw):
            raise RuntimeError("channel down")

        monkeypatch.setattr(bridge, "dispatch_event", _boom)
        s = self._scheduler(lambda task: {"status": "failed", "error": "x",
                                          "duration_ms": 1})
        out = s.run_task({"task_id": "t1", "name": "nightly"}, trigger="manual")

        assert out["status"] == "failed", "通道故障不得改变任务结果"

