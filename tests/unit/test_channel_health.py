# -*- coding: utf-8 -*-
"""出站通道投递健康台账 + `channel` 事件源（2026-11-13）。

【现场】Owner 点「测试发送」失败时只拿到一行文案，顶栏铃铛没有任何告警 ——
"通道发不出去"这件事只有点按钮的人知道。本文件钉住补上的那一环：

1. 记账在**唯一发送收口** `ChannelInstaller.send_message`：成功清零、失败累加；
2. 台账**只存类别**（auth/tls/timeout/disconnected/other），不存异常原文与通道配置；
3. 采集器只在通道**仍启用**时报；停用/卸载 ⇒ 下一轮自动恢复（配置决定不是故障）；
4. 台账缺失 = 没有测量 ⇒ 源不可用：不报警、也不把旧告警判恢复（keep）。
"""
from __future__ import annotations

import json
import smtplib
import ssl

import pytest

from agent.alarms import collector
from agent.alarms.collector import channel_candidates, collect_once
from agent.alarms.store import STATE_FIRING, STATE_RESOLVED, AlarmStore
from agent.extensions import channel_health
from agent.extensions.channels_installer import ChannelInstaller
from agent.extensions.store import ExtensionStore


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    """把台账指到 tmp（否则会写生产 data/extensions/channel_health.json）。"""
    path = tmp_path / "channel_health.json"
    monkeypatch.setattr(channel_health, "STATE_PATH", path)
    monkeypatch.setattr("agent.extensions.channels_installer.SEND_RETRY_BACKOFF_SEC", 0.0)
    return path


@pytest.fixture()
def installer(tmp_path):
    store = ExtensionStore(data_file=str(tmp_path / "extensions.json"))
    inst = ChannelInstaller(store)
    inst.install_builtin_channel("email_smtp")
    return inst


@pytest.fixture()
def alarm_store(tmp_path) -> AlarmStore:
    return AlarmStore(tmp_path / "state.json", tmp_path / "events.jsonl")


@pytest.fixture(autouse=True)
def _no_outbound(monkeypatch):
    """采集一轮会顺手外发告警（真发邮件）—— 单测里必须断掉。

    其余默认源同样置空：本机真实台账/CI 状态会让"注入的 channel 源"用例多出候选。
    """
    from agent.alarms import notify as alarm_notify
    monkeypatch.setattr(alarm_notify, "dispatch_pending", lambda **kw: {"status": "disabled"})
    for name in ("read_health_point", "read_units", "read_audit_verification",
                 "read_rule_states", "read_scheduler_failures", "read_ci_status",
                 "read_dependency_probe"):
        monkeypatch.setattr(collector, name, lambda: None)


class _Handler:
    def __init__(self, exc=None, result="ok"):
        self.exc = exc
        self.result = result
        self.calls = 0

    def __call__(self, config, message, **kwargs):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.result


class TestLedger:
    def test_成功把连续失败清零(self, ledger):
        channel_health.record_failure("email_smtp", channel_health.CODE_TLS)
        channel_health.record_failure("email_smtp", channel_health.CODE_TLS)
        row = channel_health.snapshot()["channels"]["email_smtp"]
        assert row["consecutive_failures"] == 2 and row["last_code"] == "tls"

        channel_health.record_success("email_smtp")

        row = channel_health.snapshot()["channels"]["email_smtp"]
        assert row["consecutive_failures"] == 0
        assert row["last_code"] == ""
        assert row["total_ok"] == 1 and row["total_fail"] == 2

    def test_只存类别_不落异常原文与配置(self, ledger):
        raw_exc = "_ssl.c:983: The handshake operation timed out"
        channel_health.record_failure("email_smtp", channel_health.CODE_TLS)
        text = ledger.read_text(encoding="utf-8")
        assert "_ssl" not in text and "handshake" not in text
        assert channel_health.snapshot()["channels"]["email_smtp"]["last_code"] == "tls"
        assert "password" not in text.lower() and "auth_code" not in text.lower()

    def test_未知类别归一成other(self, ledger):
        channel_health.record_failure("email_smtp", "boom")
        assert channel_health.snapshot()["channels"]["email_smtp"]["last_code"] == "other"

    def test_空通道id不记账(self, ledger):
        channel_health.record_failure("", channel_health.CODE_TLS)
        assert not ledger.exists()

    def test_台账缺失快照为None(self, ledger):
        assert channel_health.snapshot() is None

    def test_坏JSON不抛且不写脏(self, ledger):
        ledger.write_text("{not json", encoding="utf-8")
        assert channel_health.load() == {"updated_at": None, "channels": {}}
        channel_health.record_failure("email_smtp", channel_health.CODE_TIMEOUT)
        assert json.loads(ledger.read_text(encoding="utf-8"))["channels"]["email_smtp"]["last_code"] == "timeout"


class TestSendHook:
    def test_发送成功记账(self, installer, ledger):
        installer.register_handler("email_smtp", _Handler(result="ok"))
        ok, _msg = installer.send_message("email_smtp", "hi")

        assert ok is True
        row = channel_health.snapshot()["channels"]["email_smtp"]
        assert row["consecutive_failures"] == 0 and row["total_ok"] == 1
        assert row["last_ok_at"]

    def test_一次最终失败就记账且类别正确(self, installer, ledger):
        installer.register_handler(
            "email_smtp", _Handler(exc=ssl.SSLError("_ssl.c:983: handshake timed out")))
        ok, msg = installer.send_message("email_smtp", "hi", retry_transient=True)

        assert ok is False and "已自动重试一次" in msg
        row = channel_health.snapshot()["channels"]["email_smtp"]
        assert row["consecutive_failures"] == 1 and row["last_code"] == "tls"
        assert row["total_fail"] == 1

    def test_认证失败归auth类别(self, installer, ledger):
        installer.register_handler(
            "email_smtp", _Handler(exc=smtplib.SMTPAuthenticationError(535, b"no")))
        installer.send_message("email_smtp", "hi")

        assert channel_health.snapshot()["channels"]["email_smtp"]["last_code"] == "auth"

    def test_投递台账坏掉不影响发送(self, installer, ledger, monkeypatch):
        def boom(*_a, **_kw):
            raise RuntimeError("disk full")

        monkeypatch.setattr(channel_health, "record_success", boom)
        installer.register_handler("email_smtp", _Handler(result="ok"))

        ok, msg = installer.send_message("email_smtp", "hi")

        assert ok is True and "消息已发送" in msg, "记账是旁路，不许把发送搞挂"

    def test_通道被停用时不记账(self, installer, ledger):
        installer.register_handler("email_smtp", _Handler(exc=ssl.SSLError("handshake")))
        installer.toggle_channel("email_smtp", enabled=False)

        ok, msg = installer.send_message("email_smtp", "hi")

        assert ok is False and "禁用" in msg
        assert channel_health.snapshot() is None, "没有真正发送 ⇒ 不该有投递记录"


class TestCandidates:
    def _payload(self, failures=1, enabled=("email_smtp",), code="tls", last_ok=None):
        return {
            "channels": {"email_smtp": {
                "consecutive_failures": failures, "last_code": code,
                "last_ok_at": last_ok}},
            "enabled": list(enabled),
        }

    def test_达阈值即报critical(self):
        rows = channel_candidates(self._payload(failures=1))

        assert [r["key"] for r in rows] == ["channel.email_smtp"]
        assert rows[0]["severity"] == "critical"
        assert "TLS 握手超时" in rows[0]["detail"]
        assert "从未成功" in rows[0]["detail"]
        assert "自动恢复" in rows[0]["detail"]

    def test_阈值是常量且可调(self, monkeypatch):
        monkeypatch.setattr(channel_health, "FAIL_ALARM_THRESHOLD", 2)
        assert channel_candidates(self._payload(failures=1)) == []
        assert channel_candidates(self._payload(failures=2))

    def test_停用的通道不报(self):
        assert channel_candidates(self._payload(failures=3, enabled=())) == []

    def test_没有失败不报(self):
        assert channel_candidates(self._payload(failures=0)) == []

    def test_空载荷不抛(self):
        assert channel_candidates(None) == [] and channel_candidates({}) == []


class TestCollectOnce:
    def test_失败上报_成功后自动恢复(self, alarm_store):
        stats = collect_once(alarm_store, sources={"channel": lambda: {
            "channels": {"email_smtp": {"consecutive_failures": 1, "last_code": "tls",
                                        "last_ok_at": None}},
            "enabled": ["email_smtp"]}})
        assert stats["raised"] == 1
        assert alarm_store.list()[0]["state"] == STATE_FIRING
        assert alarm_store.list()[0]["kind"] == "channel"

        stats = collect_once(alarm_store, sources={"channel": lambda: {
            "channels": {"email_smtp": {"consecutive_failures": 0, "last_code": "",
                                        "last_ok_at": "2026-11-13T19:00:00+08:00"}},
            "enabled": ["email_smtp"]}})
        assert stats["resolved"] == 1
        assert alarm_store.list()[0]["state"] == STATE_RESOLVED

    def test_台账读不到时保留旧告警(self, alarm_store):
        collect_once(alarm_store, sources={"channel": lambda: {
            "channels": {"email_smtp": {"consecutive_failures": 2, "last_code": "tls",
                                        "last_ok_at": None}},
            "enabled": ["email_smtp"]}})
        stats = collect_once(alarm_store, sources={"channel": lambda: None})

        assert stats["checked"]["channel"] is False
        assert stats["resolved"] == 0
        assert stats["kept"] == ["channel.email_smtp"]
        assert alarm_store.list()[0]["state"] == STATE_FIRING

    def test_停用通道后自动恢复(self, alarm_store):
        collect_once(alarm_store, sources={"channel": lambda: {
            "channels": {"email_smtp": {"consecutive_failures": 2, "last_code": "auth",
                                        "last_ok_at": None}},
            "enabled": ["email_smtp"]}})
        stats = collect_once(alarm_store, sources={"channel": lambda: {
            "channels": {"email_smtp": {"consecutive_failures": 2, "last_code": "auth",
                                        "last_ok_at": None}},
            "enabled": []}})

        assert stats["resolved"] == 1

