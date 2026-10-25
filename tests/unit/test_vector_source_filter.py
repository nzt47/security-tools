# -*- coding: utf-8 -*-
"""GET /api/vector/recent 的 ?source= 过滤（2026-10-25）。

【为什么需要】记忆管理页「向量文档（外部导入）」原先只取最近 limit 条、再在前端按
metadata.source 过滤：本机对话向量一多，导入条目就被挤出窗口 —— 数据还在、只是"看不见"。
改成服务端按 source 过滤（先扫更大窗口再筛），并把 limit/source 的形态错误如实报 400
（原实现 `int()` 遇到非数字会抛 500）。
"""
from __future__ import annotations

import importlib
import logging
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


class _Item:
    def __init__(self, item_id, source, ts="2026-10-25T00:00:00+08:00"):
        self.id = item_id
        self.content = f"content-{item_id}"
        self.metadata = {} if source is None else {"source": source}
        self.timestamp = ts

    def to_dict(self):
        return {"id": self.id, "content": self.content,
                "metadata": dict(self.metadata), "timestamp": self.timestamp}


class _VectorStub:
    def __init__(self, items):
        self._items = items
        self.calls = []

    def get_recent(self, limit=10):
        self.calls.append(limit)
        return self._items[:limit]


def _client(monkeypatch, items):
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    stub = _VectorStub(items)

    class _Yunshu:
        _vector_memory = stub

    fake_app_server = types.ModuleType("app_server")
    fake_app_server._Yunshu = _Yunshu
    fake_app_server.logger = logging.getLogger("fake_app_server")
    fake_app_server.require_token = lambda f: f
    fake_app_server.log_request = lambda *a, **k: (lambda f: f)
    monkeypatch.setitem(sys.modules, "app_server", fake_app_server)

    from flask import Flask

    from agent.api_envelope import install_error_handlers

    mem = importlib.import_module("plugins.memory")
    app = Flask(__name__)
    app.register_blueprint(mem.bp)
    install_error_handlers(app, enabled=True)
    app.config["TESTING"] = True
    return app.test_client(), stub


def _items(n_import=3, n_other=20):
    items = [_Item(f"mem_other{i}", None) for i in range(n_other)]
    items += [_Item(f"mem_imp{i}", "memory_import") for i in range(n_import)]
    return items


def test_without_source_keeps_legacy_shape(monkeypatch):
    http, stub = _client(monkeypatch, _items())
    resp = http.get("/api/vector/recent?limit=5")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["count"] == 5 and len(body["items"]) == 5
    assert "source" not in body
    assert stub.calls == [5], "不带 source 时只取 limit 条，不放大扫描"


def test_source_filter_scans_wider_window(monkeypatch):
    http, stub = _client(monkeypatch, _items())
    resp = http.get("/api/vector/recent?limit=3&source=memory_import")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["source"] == "memory_import"
    assert [i["id"] for i in body["items"]] == ["mem_imp0", "mem_imp1", "mem_imp2"]
    assert body["count"] == 3
    assert stub.calls and stub.calls[0] >= 200, "按来源过滤要先扫更大窗口，否则导入条目会被挤出"


def test_source_filter_caps_result_at_limit(monkeypatch):
    http, _stub = _client(monkeypatch, _items(n_import=10))
    body = http.get("/api/vector/recent?limit=2&source=memory_import").get_json()
    assert body["count"] == 2


@pytest.mark.parametrize("query", ["limit=abc", "limit=0", "limit=-3"])
def test_invalid_limit_is_400_not_500(monkeypatch, query):
    http, _stub = _client(monkeypatch, _items())
    resp = http.get("/api/vector/recent?" + query)
    assert resp.status_code == 400
    assert resp.mimetype == "application/problem+json"
    assert resp.get_json()["type"].endswith("invalid-limit")


def test_overlong_source_is_400(monkeypatch):
    http, _stub = _client(monkeypatch, _items())
    resp = http.get("/api/vector/recent?source=" + "a" * 200)
    assert resp.status_code == 400
    assert resp.get_json()["type"].endswith("invalid-source")
