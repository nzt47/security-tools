# -*- coding: utf-8 -*-
"""mem0 后端（内置降级）的列出/删除：按域过滤，不越域导出/擦除。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.memory.adapters.mem0_adapter import Mem0Adapter
from agent.memory.scoped_store import _BackendOpUnsupported, _Mem0Backend, ScopedMemoryScope


def _scope():
    return ScopedMemoryScope(tenant_id="t1", workspace_id="w1", subject_id="s1")


def _seed(tmp_path):
    a = Mem0Adapter(storage_path=str(tmp_path / "facts.json"))
    asyncio.run(a.save("k1", "v1", {"tenant_id": "t1", "workspace_id": "w1", "subject_id": "s1"}))
    asyncio.run(a.save("k2", "v2", {"tenant_id": "t2", "workspace_id": "w1", "subject_id": "s1"}))
    return a


def test_builtin_list_filters_foreign_scope(tmp_path):
    a = _seed(tmp_path)
    assert a.uses_mem0_engine is False
    rows = asyncio.run(_Mem0Backend(a).list_entries(_scope()))
    assert [r.metadata["key"] for r in rows] == ["k1"]


def test_delete_is_scope_guarded(tmp_path):
    a = _seed(tmp_path)
    b = _Mem0Backend(a)
    rows = asyncio.run(b.list_entries(_scope()))
    foreign = [r for r in asyncio.run(a.list_entries()) if r.metadata["key"] == "k2"][0]
    assert asyncio.run(b.delete_entry(foreign, _scope())) is False   # 越域不删
    assert asyncio.run(b.delete_entry(rows[0], _scope())) is True    # 本域删掉
    assert [r.metadata["key"] for r in asyncio.run(a.list_entries())] == ["k2"]


def test_engine_path_fail_closed(monkeypatch, tmp_path):
    a = _seed(tmp_path)
    monkeypatch.setattr(type(a), "uses_mem0_engine", property(lambda self: True))
    with pytest.raises(_BackendOpUnsupported):
        asyncio.run(_Mem0Backend(a).list_entries(_scope()))
