"""守护 /api/memory/compress 的行为契约（2026-11-10 修 500）。

为什么需要它（同一个坑第三次踩）：
  1. `/api/context/compress` 曾无参调 `MemoryManager.compress()` ⇒ 恒 TypeError，2026-10-14 修；
  2. `/api/memory/compress` 是**同一个坑**，但一直没修 —— 因为 2026 年补的只有
     「legacy 端点存在性」测试（tests/unit/test_legacy_memory_routes.py 只查 url_map），
     端点存在 ≠ 端点可用（本仓判绿口径：覆盖 ≠ 可用）；
  3. 2026-11-10 用户在「记忆管理 → 记忆运维」点「触发压缩」实测 500：
     `MemoryManager.compress() missing 1 required positional argument: 'old_messages'`。

本测试直接调视图裸函数（绕过 `_view` 的 auth 包装），注入假 `app_server._Yunshu`，断言：
  ① 必须走 `compress_now()`；② 绝不无参调 `compress()`；③ 回报字段可核实；
  ④ 失败时不把异常原文回给调用方。
"""
from __future__ import annotations

import json
import logging
import sys
import types

import pytest
from flask import Flask

from plugins import memory as mem_mod


class _FakeMemory:
    def __init__(self) -> None:
        self.calls = []

    def compress(self, *args, **kwargs):  # 旧入口：本路由不允许再调它
        self.calls.append(("compress", args, kwargs))
        raise AssertionError("compress(old_messages) 不该被 /api/memory/compress 调用")

    def compress_now(self):
        self.calls.append(("compress_now", (), {}))
        return {
            "ok": True,
            "summary_version": 96,
            "version_before": 95,
            "messages_compressed": 100,
            "used_prefix": True,
            "prefix_messages": 188,
        }


class _BoomMemory(_FakeMemory):
    def compress_now(self):
        self.calls.append(("compress_now", (), {}))
        raise RuntimeError("internal-detail-should-not-leak")


def _install_fake_app_server(monkeypatch, memory) -> None:
    fake = types.ModuleType("app_server")
    fake._Yunshu = types.SimpleNamespace(_memory=memory)
    fake.require_token = lambda f: f
    fake.log_request = lambda **kwargs: (lambda f: f)
    fake.logger = logging.getLogger("test.app_server")
    monkeypatch.setitem(sys.modules, "app_server", fake)


def _call_route(memory, monkeypatch):
    _install_fake_app_server(monkeypatch, memory)
    raw = mem_mod.api_memory_compress.__wrapped__
    app = Flask("memory_compress_route_test")
    with app.test_request_context("/api/memory/compress", method="POST"):
        resp = raw()
    status = 200
    if isinstance(resp, tuple):
        resp, status = resp[0], int(resp[1])
    return resp.get_json(), status


def test_route_goes_through_compress_now(monkeypatch):
    memory = _FakeMemory()
    body, status = _call_route(memory, monkeypatch)
    assert memory.calls == [("compress_now", (), {})], "必须且只能走 compress_now()"
    assert status == 200
    data = body.get("data") or body
    assert data["ok"] is True
    assert data["summary_version"] == 96 and data["version_before"] == 95
    assert data["messages_compressed"] == 100
    assert data["used_prefix"] is True and data["prefix_messages"] == 188


def test_failure_returns_generic_text_and_hides_exception(monkeypatch):
    body, status = _call_route(_BoomMemory(), monkeypatch)
    assert status == 500
    assert body.get("ok") is False
    assert body.get("error") == "操作失败（详见服务日志）"
    assert "internal-detail-should-not-leak" not in json.dumps(body, ensure_ascii=False)
