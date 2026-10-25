# -*- coding: utf-8 -*-
"""向量写入不许"假成功"（2026-10-25）。

两条防线：
1. VectorStore.add：sqlite-vec 插入失败时**抛错**，不再照旧返回一个查不到的 id
   （调用方会据此删源数据 ⇒ 唯一副本丢失）；
2. targets._default_vector_sink：写入回执之后**回读**一次，查不到就当场失败
   —— 导入器只有在"向量确实可读"之后才允许删除原消息。
"""
from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.memory_import import targets  # noqa: E402
from memory.vector_store.vector_store import VectorStore  # noqa: E402


class _Encoder:
    def encode(self, texts):
        class _V:
            def tolist(self_inner):
                return [[0.0] * 8 for _ in texts]
        return _V()


class _Backend:
    def __init__(self, ok):
        self.ok = ok
        self.calls = []

    def add(self, **kwargs):
        self.calls.append(kwargs)
        return self.ok

    def count(self):
        return len(self.calls)


def _bare_store(backend):
    store = object.__new__(VectorStore)
    store._backend = "sqlite_vec"
    store._query_cache = None
    store._encoder = _Encoder()
    store._sqlite_vec_backend = backend
    return store


def test_add_raises_when_backend_insert_fails():
    backend = _Backend(ok=False)
    store = _bare_store(backend)
    with pytest.raises(RuntimeError, match="未落库"):
        store.add("正文", {"source": "t"})
    assert backend.calls, "确实尝试过插入"


def test_add_returns_id_when_backend_succeeds():
    backend = _Backend(ok=True)
    store = _bare_store(backend)
    item_id = store.add("正文", {"source": "t"})
    assert item_id.startswith("mem_")
    assert backend.calls[0]["item_id"] == item_id
    assert backend.calls[0]["metadata"]["created_at"]


class _FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _dispatcher(monkeypatch, *, get_status=200, get_item=None):
    seen = []

    def fake_urlopen(request, timeout=None):
        method = request.get_method()
        seen.append((method, request.full_url))
        if method == "POST":
            return _FakeResponse({"ok": True, "item_id": "mem_new"})
        if get_status == 404:
            raise urllib.error.HTTPError(request.full_url, 404, "not found", {},
                                         io.BytesIO(b"{}"))
        return _FakeResponse(get_item or {"id": "mem_new", "content": "正文"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return seen


def test_sink_verifies_written_item_is_readable(monkeypatch):
    seen = _dispatcher(monkeypatch)
    out = targets._default_vector_sink("正文", metadata={"source": "memory_import"})
    assert out["detail"]["vector_ids"] == ["mem_new"]
    assert [m for m, _u in seen] == ["POST", "GET"], "写完必须再回读一次"


def test_sink_raises_when_item_not_found_after_add(monkeypatch):
    _dispatcher(monkeypatch, get_status=404)
    with pytest.raises(RuntimeError, match="查不到"):
        targets._default_vector_sink("正文", metadata={"source": "memory_import"})


def test_verify_vector_item_rejects_id_mismatch(monkeypatch):
    _dispatcher(monkeypatch, get_item={"id": "other", "content": "x"})
    with pytest.raises(RuntimeError, match="不符"):
        targets.verify_vector_item("mem_new")
