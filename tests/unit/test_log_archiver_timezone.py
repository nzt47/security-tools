# -*- coding: utf-8 -*-
"""归档「今天」的时区口径（2026-11-27 修）

背景（本机实测）：事件行的 `ts` 是 **UTC**（`datetime.now(timezone.utc).isoformat()`），
而 `archive_daily_file` 原先拿**本地** `date.today()` 当"今天"。在 UTC+8 的 00:00–08:00，
UTC 日期比本地日期小一天 ⇒ **当天的行被当成历史行**：归档器把刚写入的事件搬进
`<stem>-<UTC日>.jsonl` 并删掉活动文件（追加后活动文件消失；4 条台账用例在凌晨窗口恒红，
CI 跑在 UTC 所以看不出来）。

本文件钉住：**判"今天"必须用 UTC 日期**，且历史行仍照常按日分片。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from agent.skills_mgmt import log_archiver


def _line(ts: str, tag: str) -> str:
    return json.dumps({"ts": ts, "stage": "tool", "tool": tag}, ensure_ascii=False)


def test_今天的行留在活动文件_历史行进分片(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    today_line = _line("2026-01-01T17:00:00+00:00", "today")
    old_line = _line("2025-12-31T23:00:00+00:00", "old")
    path.write_text(today_line + "\n" + old_line + "\n", encoding="utf-8")

    class _FixedDatetime:
        @staticmethod
        def now(tz=None):
            # 【这条断言就是本文件的核心】不许拿本地时间当"今天"
            assert tz is timezone.utc, "归档的今天必须取 UTC 日期（与 ts 同源）"
            return datetime(2026, 1, 1, 17, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(log_archiver, "datetime", _FixedDatetime)

    result = log_archiver.archive_daily_file(path)
    assert result["archived"] == 1, result
    assert result["today"] == 1, result
    # 活动文件只剩今天那条（历史那条已搬到分片）
    remaining = path.read_text(encoding="utf-8")
    assert "\"today\"" in remaining and "\"old\"" not in remaining
    shard = tmp_path / "events-2025-12-31.jsonl"
    assert shard.exists(), "历史行必须落到按日分片"
    assert "\"old\"" in shard.read_text(encoding="utf-8")

