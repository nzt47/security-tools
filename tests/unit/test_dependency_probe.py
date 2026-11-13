# -*- coding: utf-8 -*-
"""依赖面主动探测（agent/monitoring/dependency_probe.py，2026-11-13）。

【钉住的口径】
1. 只做 TCP/TLS 握手：不发信、不登录、不调 LLM；
2. 失败只记**类别**（dns/timeout/refused/tls/other），异常原文不落盘；
3. 没配的东西不探（没配置 ≠ 故障）；停用的通道不产生目标；
4. 还没跑过 ⇒ 快照 None（采集层按"源不可用"处理，不报警也不判恢复）。
"""
from __future__ import annotations

import socket
import ssl

import pytest

from agent.alarms import collector
from agent.monitoring import dependency_probe as dp


class _Sock:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_连接成功即_ok():
    sock = _Sock()
    out = dp.probe_target({"id": "x", "kind": "channel", "host": "127.0.0.1",
                           "port": 465, "tls": False},
                          connector=lambda addr, timeout: sock)
    assert out["ok"] is True and out["code"] == "" and sock.closed is True


@pytest.mark.parametrize("exc,code", [
    (socket.gaierror("dns"), dp.CODE_DNS),
    (socket.timeout("t"), dp.CODE_TIMEOUT),
    (ConnectionRefusedError("r"), dp.CODE_REFUSED),
    (ssl.SSLError("h"), dp.CODE_TLS),
    (ConnectionResetError("x"), dp.CODE_OTHER),
])
def test_失败只记类别(exc, code):
    def boom(addr, timeout):
        raise exc

    out = dp.probe_target({"id": "x", "kind": "channel", "host": "h", "port": 465},
                          connector=boom)
    assert out["ok"] is False and out["code"] == code
    assert "dns" not in out["detail"] or code != dp.CODE_TLS
    assert out["detail"] == dp.CODE_LABELS[code]


def test_目标不完整不探():
    out = dp.probe_target({"id": "x", "kind": "channel", "host": "", "port": 0})
    assert out["ok"] is False and out["code"] == dp.CODE_OTHER


def test_已启用通道才生成目标():
    rows = [
        {"ext_id": "email_smtp", "enabled": True, "status": "enabled",
         "config": {"channel_type": "smtp", "smtp_server": "smtp.139.com",
                    "smtp_port": 465, "use_ssl": True}},
        {"ext_id": "webhook", "enabled": False, "status": "disabled",
         "config": {"channel_type": "webhook", "webhook_url": "https://example.com/hook"}},
    ]
    targets = dp.targets_from_channels(rows, lambda r: bool(r.get("enabled")))
    assert [t["id"] for t in targets] == ["channel:email_smtp"]
    assert targets[0]["port"] == 465 and targets[0]["tls"] is True


def test_webhook_目标解析_https默认443():
    rows = [{"ext_id": "wh", "enabled": True,
             "config": {"channel_type": "webhook", "webhook_url": "https://example.com/x"}}]
    targets = dp.targets_from_channels(rows, lambda r: True)
    assert targets[0]["host"] == "example.com" and targets[0]["port"] == 443 and targets[0]["tls"]


def test_没配LLM就不探():
    assert dp.targets_from_llm("") == []
    targets = dp.targets_from_llm("https://api.example.com/v1")
    assert targets and targets[0]["id"] == "llm:endpoint" and targets[0]["port"] == 443


def test_快照未跑过为None(tmp_path, monkeypatch):
    monkeypatch.setattr(dp, "STATE_PATH", tmp_path / "probe.json")
    dp.reset_for_tests()
    assert dp.snapshot() is None


def test_run_once落盘并可读(tmp_path, monkeypatch):
    monkeypatch.setattr(dp, "STATE_PATH", tmp_path / "probe.json")
    dp.reset_for_tests()
    snap = dp.run_once([{"id": "channel:x", "kind": "channel", "host": "127.0.0.1",
                         "port": 9, "tls": False}], timeout=0.2)
    assert snap["targets"] == 1 and snap["ok"] is False
    again = dp.snapshot()
    assert again and again["results"][0]["ok"] is False


def test_采集候选_通道critical_端点warning():
    payload = {"results": [
        {"id": "channel:email_smtp", "kind": "channel", "ok": False, "code": "tls", "ms": 1500},
        {"id": "llm:endpoint", "kind": "endpoint", "ok": False, "code": "timeout", "ms": 6000},
        {"id": "channel:ok", "kind": "channel", "ok": True, "code": "", "ms": 10},
    ]}
    rows = collector.probe_candidates(payload)

    assert {r["key"] for r in rows} == {"probe.channel.email_smtp", "probe.llm.endpoint"}
    by_key = {r["key"]: r for r in rows}
    assert by_key["probe.channel.email_smtp"]["severity"] == "critical"
    assert by_key["probe.llm.endpoint"]["severity"] == "warning"
    assert "TLS 握手失败" in by_key["probe.channel.email_smtp"]["detail"]
    assert "没发信" in by_key["probe.channel.email_smtp"]["detail"]
