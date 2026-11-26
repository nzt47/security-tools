# -*- coding: utf-8 -*-
"""归档「今天」的时区口径（2026-11-27 修）

背景（本机实测）：事件行的 `ts` 是 **UTC**（`datetime.now(timezone.utc).isoformat()`），
而 `archive_daily_file` 原先拿**本地** `date.today()` 当"今天"。在 UTC+8 的 00:00–08:00，
UTC 日期比本地日期小一天 ⇒ **当天的行被当成历史行**：归档器把刚写入的事件搬进
`<stem>-<UTC日>.jsonl` 并删掉活动文件（追加后活动文件消失；4 条台账用例在凌晨窗口恒红，
CI 跑在 UTC 所以看不出来）。

本文件钉住两件事：
  1. **行为**：伪造"今天"= 真实当下（UTC），今天的行留在活动文件、历史行进分片；
  2. **源码**：归档侧不许再用 `date.today(`（本地日期）——这条静态断言在"写下的那一刻"
     就能抓住回退，不依赖运行时是否正好跨日（那是 `test_date_bomb_guard` 的口径）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent.skills_mgmt import log_archiver


def _line(ts: str, tag: str) -> str:
    return json.dumps({"ts": ts, "stage": "tool", "tool": tag}, ensure_ascii=False)


def test_今天的行留在活动文件_历史行进分片(tmp_path, monkeypatch):
    # 不硬编码任何日期：以真实当下（UTC）为"今天"，历史行取昨天（口径同 ts）
    real_now = datetime.now(timezone.utc)
    day = real_now.date()
    path = tmp_path / "events.jsonl"
    today_line = _line(day.isoformat() + "T00:00:00+00:00", "today")
    old_line = _line((day - timedelta(days=1)).isoformat() + "T23:00:00+00:00", "old")
    path.write_text(today_line + "\n" + old_line + "\n", encoding="utf-8")

    class _FixedDatetime:
        @staticmethod
        def now(tz=None):
            # 【核心断言】不许拿本地时间当"今天"（tz 必须是 UTC）
            assert tz is timezone.utc, "归档的今天必须取 UTC 日期（与 ts 同源）"
            return real_now

    monkeypatch.setattr(log_archiver, "datetime", _FixedDatetime)

    result = log_archiver.archive_daily_file(path)
    assert result["archived"] == 1, result
    assert result["today"] == 1, result
    remaining = path.read_text(encoding="utf-8")
    assert "today" in remaining and "old" not in remaining
    shard = tmp_path / f"events-{(day - timedelta(days=1)).isoformat()}.jsonl"
    assert shard.exists(), "历史行必须落到按日分片"
    assert "old" in shard.read_text(encoding="utf-8")


def test_源码不许回退到本地日期():
    """静态守卫：归档侧再出现 `date.today()` **调用**就是回退（UTC ts vs 本地 today 差一天）

    【为什么用 AST 而不是 grep 字符串】注释里也会出现 `date.today()` 这五个字（本文件的
    说明性注释就是），字符串匹配会把注释当违规；AST 只看真实调用。
    """
    import ast

    src = Path(log_archiver.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    bad = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "today"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "date"
    ]
    assert not bad, (
        "log_archiver 又用本地 date.today() 当「今天」了 —— 会让 00:00–08:00 的当天事件被当历史行搬走")
    assert "timezone.utc" in src, "「今天」必须与事件 ts 同源（UTC）"

