# -*- coding: utf-8 -*-
"""观测事件读取的**文件白名单**守卫（C-6-B1，2026-11-03）

`iter_events(files=[...])` 原先直接 `list(files)` —— 调用方（面板/接口）传什么路径就读什么，
`py/path-injection` 因此在 open() 处报警。现在的口径：请求里的路径**只当名字**参与集合比较，
真正进文件操作的是 `event_files(directory)` 列出来的那些路径（§8 陷阱 14）。
这条用例锁住"外部路径读不到"，而不是只锁住"能读到"。
"""
from __future__ import annotations

import shutil

from agent.observability.events import EventStore, emit, iter_events


def _seed(events_dir):
    store = EventStore(path=str(events_dir / "events.jsonl"))
    emit("test.guard.event", {"n": 1}, store=store)
    return events_dir / "events.jsonl"


def test_请求里的外部路径不会被读取(tmp_path):
    inside = tmp_path / "events"
    inside.mkdir()
    seed = _seed(inside)
    outside = tmp_path / "outside.jsonl"
    shutil.copy(seed, outside)

    assert iter_events(directory=str(inside), files=[str(outside)]) == [], \
        "事件目录之外的文件不得因为被请求就读取"
    assert len(iter_events(directory=str(inside), files=[str(seed)])) == 1


def test_不带_files_时照旧读目录内全部(tmp_path):
    inside = tmp_path / "events"
    inside.mkdir()
    _seed(inside)
    assert len(iter_events(directory=str(inside))) == 1