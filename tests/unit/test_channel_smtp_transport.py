# -*- coding: utf-8 -*-
"""SMTP 通道的**传输分支**守卫（2026-11-12，现场实证"配好了却发不出去"）。

【为什么需要这组用例】
Owner 把 139 邮箱配好后通道仍然发不出去，报错只有 `WinError 10060`。实测：
`smtp.139.com:587` 从本机**连接超时**，而 `:465` 通（同机连 qq:587 / 163:465 都通 ⇒ 不是出网被封）。
原实现只有 `SMTP() + starttls()`（587），465 的隐式 TLS 根本连不上。

【判定（四条，都用假 smtplib 钉住，不联网）】
1. 端口 465 ⇒ 走 `SMTP_SSL`，**不再** `starttls()`；
2. 端口 587 + `use_tls` ⇒ 走 `SMTP` 并 `starttls()`；
3. 显式 `use_ssl` 优先于端口推断（`use_ssl=false` 的 465 ⇒ 仍 STARTTLS，给"端口非标准"的部署留出口）；
4. 收件人缺失 / 服务器缺失 ⇒ 明确 ValueError（不发半截邮件），且两个客户端都带超时。
"""
from __future__ import annotations

import smtplib
import types

import pytest

from agent.extensions import channels_installer as ci


class _FakeClient:
    """假的 SMTP/SMTP_SSL 客户端：只记调用顺序，不联网。"""

    def __init__(self, kind: str, host: str, port: int, timeout=None):
        self.kind = kind
        self.host = host
        self.port = port
        self.timeout = timeout
        self.calls = []
        self._closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._closed = True
        return False

    def starttls(self):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", bool(user), bool(password)))

    def send_message(self, msg):
        self.calls.append(("send", str(msg["To"]), str(msg["Subject"])))


@pytest.fixture()
def fake_smtp(monkeypatch):
    """把 smtplib.SMTP / SMTP_SSL 换成假类，返回"造出来的客户端"列表。"""
    made = []

    def _make(kind):
        def _factory(host, port, timeout=None):
            client = _FakeClient(kind, host, port, timeout)
            made.append(client)
            return client
        return _factory

    monkeypatch.setattr(smtplib, "SMTP", _make("smtp"))
    monkeypatch.setattr(smtplib, "SMTP_SSL", _make("ssl"))
    return made


def _config(**over):
    base = {
        "channel_type": "smtp",
        "smtp_server": "smtp.example.com",
        "smtp_port": 587,
        "smtp_username": "bot@example.com",
        "smtp_password": "auth-code",
        "use_tls": True,
        "from_address": "bot@example.com",
        "default_to": "owner@example.com",
    }
    base.update(over)
    return base


def test_port_465_uses_implicit_tls_without_starttls(fake_smtp):
    out = ci.ChannelInstaller._handle_smtp(_config(smtp_port=465), "hello")
    (client,) = fake_smtp
    assert client.kind == "ssl"
    assert client.port == 465
    assert "starttls" not in client.calls
    assert ("send", "owner@example.com", "来自云枢的消息") in client.calls
    assert "owner@example.com" in out


def test_port_587_uses_starttls(fake_smtp):
    ci.ChannelInstaller._handle_smtp(_config(smtp_port=587, use_ssl=False), "hello")
    (client,) = fake_smtp
    assert client.kind == "smtp"
    assert client.calls[0] == "starttls"


def test_explicit_use_ssl_overrides_port_inference(fake_smtp):
    # 端口非标准但服务端只认隐式 TLS
    ci.ChannelInstaller._handle_smtp(_config(smtp_port=2465, use_ssl=True), "hello")
    (client,) = fake_smtp
    assert client.kind == "ssl" and "starttls" not in client.calls

    fake_smtp.clear()
    ci.ChannelInstaller._handle_smtp(_config(smtp_port=465, use_ssl=False), "hello")
    (client2,) = fake_smtp
    assert client2.kind == "smtp" and client2.calls[0] == "starttls"


def test_use_tls_false_skips_starttls_but_keeps_plain_smtp(fake_smtp):
    ci.ChannelInstaller._handle_smtp(_config(use_tls=False), "hello")
    (client,) = fake_smtp
    assert client.kind == "smtp"
    assert "starttls" not in client.calls


def test_both_clients_get_timeout(fake_smtp):
    ci.ChannelInstaller._handle_smtp(_config(smtp_port=587), "hello")
    ci.ChannelInstaller._handle_smtp(_config(smtp_port=465), "hello")
    assert [c.timeout for c in fake_smtp] == [ci.SMTP_TIMEOUT_SEC, ci.SMTP_TIMEOUT_SEC]


def test_missing_recipient_raises_before_connecting(fake_smtp):
    with pytest.raises(ValueError):
        ci.ChannelInstaller._handle_smtp(_config(default_to=""), "hello")
    assert fake_smtp == [], "缺收件人时不该建连接（避免连上了才报错）"


def test_missing_server_raises(fake_smtp):
    with pytest.raises(ValueError):
        ci.ChannelInstaller._handle_smtp(_config(smtp_server=""), "hello")
    assert fake_smtp == []


def test_to_override_from_call_wins(fake_smtp):
    ci.ChannelInstaller._handle_smtp(_config(), "hello", to="oncall@example.com",
                                    subject="测试")
    (client,) = fake_smtp
    assert ("send", "oncall@example.com", "测试") in client.calls


def test_login_only_when_both_credentials_present(fake_smtp):
    ci.ChannelInstaller._handle_smtp(_config(smtp_password=""), "hello")
    (client,) = fake_smtp
    assert not [c for c in client.calls if isinstance(c, tuple) and c[0] == "login"]


def test_module_constants_are_sane():
    assert ci.SMTP_DEFAULT_PORT == 587
    assert ci.SMTP_SSL_PORT == 465
    assert 0 < ci.SMTP_TIMEOUT_SEC <= 60
