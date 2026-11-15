# -*- coding: utf-8 -*-
"""观测事件读取的**文件白名单**守卫（C-6-B1，2026-11-03）

`iter_events(files=[...])` 原先直接 `list(files)` —— 调用方（面板/接口）传什么路径就读什么，
`py/path-injection` 因此在 open() 处报警。现在的口径：请求里的路径**只当名字**参与集合比较，
真正进文件操作的是 `event_files(directory)` 列出来的那些路径（§8 陷阱 14）。
这条用例锁住"外部路径读不到"，而不是只锁住"能读到"。
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from agent import safe_paths
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


def test_解析不到的目录会留一次警告且不回落默认目录(tmp_path, caplog):
    """解析不到 ≠ 静默：`event_files` 返回空集，但**日志里留一次痕迹**

    【2026-11-15】windows 上"事件目录解析不到"（跨盘 / 8.3 短名）会让成本护栏
    `spent` 恒 0 而**任何接口都不报错**，7 条红被藏了两天。语义不变（绝不回落
    默认目录 —— 那会把"你指的目录不存在"伪装成"默认目录里的事件"），但必须可见。
    每种写法只报一次（防轮询刷屏）。
    """
    missing = str(tmp_path / "no_such_events_dir")
    with caplog.at_level("WARNING", logger="agent.observability.events"):
        assert iter_events(directory=missing) == []
        assert iter_events(directory=missing) == []          # 第二次不再刷
    hits = [r for r in caplog.records if "无法解析" in str(r.getMessage())]
    assert len(hits) == 1, [r.getMessage() for r in caplog.records]


def test_另一个盘上的事件目录也能读(tmp_path):
    """事件目录在**另一个真实存在的盘**上时，读侧照样看得到（windows CI 的真根因）

    【2026-11-15】windows CI 上 `test_s5_03_cost_brake::TestDailyBreaker` ×5 与
    `test_judge_llm_runtime::TestBudgetGuardSeesItsOwnSpend` ×2 全红在「成本台账
    spent=0.0」：写侧把事件写进了 `D:\`（仓库盘）或 `C:\`（TEMP 盘）里的目录，读侧
    `event_files()` 因**跨盘**判定失败返回空集 —— 且不报错。这条用例钉住"跨盘可读"。
    """
    if os.name != "nt":
        pytest.skip("跨盘是 Windows 的失效形态")
    here = Path(os.path.abspath(tmp_path)).drive.upper()
    others = [d for d in safe_paths._drive_roots() if d[:2].upper() != here]
    if not others:
        pytest.skip("本机只有一个盘，跨盘行为无法验证")
    probe = Path(tempfile.mkdtemp(prefix="_events_xdrive_", dir=others[0])) / "events"
    try:
        probe.mkdir()
        _seed(probe)
        rows = iter_events(directory=str(probe))
        assert len(rows) == 1, "跨盘的事件目录必须读得到（否则护栏 spent 恒 0）"
    finally:
        shutil.rmtree(str(probe.parent), ignore_errors=True)