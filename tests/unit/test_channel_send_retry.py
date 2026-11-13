# -*- coding: utf-8 -*-
"""通道发送的**瞬时抖动**处理与失败文案（2026-11-13 现场：TLS 握手超时）。

【现场】Owner 在 消息中心 → 通道 → email_smtp 点「测试发送」，报：
    发送失败：发送失败: _ssl.c:983: The handshake operation timed out
同一分钟手工探针到 smtp.139.com:465：TCP 132ms、TLS 握手 103ms(TLS1.3) ⇒ **瞬时抖动**，
不是配置错、不是 587 那种连接超时。三个问题一次修：

1. 手动发送**没有重试**（报警外发有 channel_bridge 的重试，界面路径没有）⇒ 抖动一次就失败；
2. 失败文案是异常原文（_ssl.c:983 ...），既不可操作，也有 py/stack-trace-exposure 风险；
3. 原文在「发送失败: 」里，界面又加一层「发送失败：」⇒ 双重前缀。

本文件钉住：瞬时错误重试一次、非瞬时错误不重试、失败文案不含异常原文、两条路径的重试次数不同。
"""
from __future__ import annotations

import smtplib
import ssl

import pytest

from agent.extensions.channels_installer import (
    SEND_ERR_GENERIC,
    SEND_ERR_TLS,
    SEND_RETRY_ATTEMPTS,
    ChannelInstaller,
)
from agent.extensions.store import ExtensionStore


@pytest.fixture()
def installer(tmp_path, monkeypatch):
    monkeypatch.setattr("agent.extensions.channels_installer.SEND_RETRY_BACKOFF_SEC", 0.0)
    store = ExtensionStore(data_file=str(tmp_path / "extensions.json"))
    inst = ChannelInstaller(store)
    inst.install_builtin_channel("email_smtp")
    return inst


class _Boom:
    """按次数抛错的假处理器：第 1 次抛 handshake 超时，第 2 次成功。"""

    def __init__(self, errors, ok_result="ok"):
        self.errors = list(errors)
        self.ok_result = ok_result
        self.calls = 0

    def __call__(self, config, message, **kwargs):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return self.ok_result


def test_瞬时握手超时重试一次后成功(installer):
    handler = _Boom([ssl.SSLError("_ssl.c:983: The handshake operation timed out")])
    installer.register_handler("email_smtp", handler)

    ok, msg = installer.send_message("email_smtp", "hi", retry_transient=True)

    assert ok is True and handler.calls == 2
    assert "消息已发送" in msg


def test_非瞬时错误不重试(installer):
    handler = _Boom([ValueError("配置错误")])
    installer.register_handler("email_smtp", handler)

    ok, msg = installer.send_message("email_smtp", "hi", retry_transient=True)

    assert ok is False and handler.calls == 1
    assert msg == SEND_ERR_GENERIC


def test_默认路径只尝试一次_避免与桥接重试叠加(installer):
    """报警外发（channel_bridge）自己按次数重试 ⇒ installer 默认不得再翻倍。"""
    handler = _Boom([ssl.SSLError("handshake timed out")])
    installer.register_handler("email_smtp", handler)

    ok, _msg = installer.send_message("email_smtp", "hi")

    assert ok is False and handler.calls == 1


def test_失败文案不含异常原文(installer):
    raw = "_ssl.c:983: The handshake operation timed out"
    # 两次都失败（手动路径会重试一次）⇒ 才走得到对外文案
    installer.register_handler("email_smtp", _Boom([ssl.SSLError(raw), ssl.SSLError(raw)]))

    ok, msg = installer.send_message("email_smtp", "hi", retry_transient=True)

    assert ok is False
    assert msg == SEND_ERR_TLS
    assert "handshake" not in msg and "_ssl" not in msg, "异常原文不许进响应体"
    assert "重试" in msg, "要告诉用户下一步（已重试过一次）"


def test_认证失败给可操作文案(installer):
    installer.register_handler(
        "email_smtp", _Boom([smtplib.SMTPAuthenticationError(535, b"bad auth")]))

    ok, msg = installer.send_message("email_smtp", "hi", retry_transient=True)

    assert ok is False
    assert "登录" in msg and "授权码" in msg


def test_重试次数常量至少为二(installer):
    assert SEND_RETRY_ATTEMPTS >= 2
