# -*- coding: utf-8 -*-
"""记忆整理候选扫描（agent/memory/consolidation.py）的守卫（2026-11-13）。

【为什么需要】
线上"记忆审查"以前只看长期记忆库（0 行），真正的对话记忆从来没被整理过。新增的扫描
必须钉住三条口径，否则很容易退化成"按内容去重"（会把用户真的说了 17 次的「继续」删掉）：

1. 只有 **无响应近重复** 才算候选：同角色 + 同内容 + 时间窗内 + 两次之间没有任何对方消息；
2. 有回复、超窗、角色不同 ⇒ 都不算；
3. 报告只放摘要（sha256 前 12 位 + ≤40 字预览），**不落全文**；无 session_id 的冻结池只计数。
"""
from __future__ import annotations

import json

from agent.memory import consolidation as c


def _write(tmp_path, rows):
    path = tmp_path / "messages.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return str(path)


def _row(role, content, ts, session_id="s1"):
    return {"role": role, "content": content, "timestamp": ts, "session_id": session_id}


class TestNoResponseRepeat:
    def test_same_content_twice_without_reply_is_a_candidate(self, tmp_path):
        path = _write(tmp_path, [
            _row("user", "继续", "2026-11-13T10:00:00+00:00"),
            _row("user", "继续", "2026-11-13T10:01:00+00:00"),
        ])
        result = c.scan(path)
        assert result["no_response_repeat_groups"] == 1
        assert result["candidates"][0]["count"] == 2
        assert result["candidates"][0]["role"] == "user"

    def test_reply_in_between_is_not_a_candidate(self, tmp_path):
        path = _write(tmp_path, [
            _row("user", "继续", "2026-11-13T10:00:00+00:00"),
            _row("assistant", "好的", "2026-11-13T10:00:30+00:00"),
            _row("user", "继续", "2026-11-13T10:01:00+00:00"),
        ])
        result = c.scan(path)
        assert result["no_response_repeat_groups"] == 0

    def test_outside_window_is_not_a_candidate(self, tmp_path):
        path = _write(tmp_path, [
            _row("user", "继续", "2026-11-13T10:00:00+00:00"),
            _row("user", "继续", "2026-11-13T12:00:00+00:00"),
        ])
        result = c.scan(path, window_sec=600)
        assert result["no_response_repeat_groups"] == 0

    def test_different_roles_do_not_group(self, tmp_path):
        path = _write(tmp_path, [
            _row("user", "好的", "2026-11-13T10:00:00+00:00"),
            _row("assistant", "好的", "2026-11-13T10:00:10+00:00"),
        ])
        result = c.scan(path)
        assert result["no_response_repeat_groups"] == 0


class TestReportHygiene:
    def test_report_has_no_full_content(self, tmp_path):
        secret = "这是一段很长的内容" * 10
        path = _write(tmp_path, [
            _row("user", secret, "2026-11-13T10:00:00+00:00"),
            _row("user", secret, "2026-11-13T10:00:30+00:00"),
        ])
        result = c.scan(path)
        blob = json.dumps(result, ensure_ascii=False)
        assert secret not in blob, "报告不许落全文"
        assert result["candidates"][0]["content_sha256_12"]
        assert len(result["candidates"][0]["content_sha256_12"]) == 12
        assert len(result["candidates"][0]["preview"]) <= c.PREVIEW_CHARS + 1

    def test_frozen_pool_counts_rows_without_session_id(self, tmp_path):
        path = _write(tmp_path, [
            _row("user", "a", "2026-11-13T10:00:00+00:00", session_id=""),
            _row("user", "b", "2026-11-13T10:00:01+00:00", session_id="s1"),
        ])
        assert c.scan(path)["frozen_pool"]["count"] == 1

    def test_scan_is_read_only(self, tmp_path):
        path = _write(tmp_path, [_row("user", "x", "2026-11-13T10:00:00+00:00")])
        before = open(path, "rb").read()
        c.scan(path)
        assert open(path, "rb").read() == before, "扫描不许改对话记忆"

    def test_write_and_read_latest_report(self, tmp_path):
        result = c.scan(_write(tmp_path, [_row("user", "x", "2026-11-13T10:00:00+00:00")]))
        written = c.write_report(result, out_dir=str(tmp_path / "reports"))
        assert written.endswith(".json")
        latest = c.latest_report(str(tmp_path / "reports"))
        assert latest and latest["total_messages"] == 1
