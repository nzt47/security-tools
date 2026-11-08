# -*- coding: utf-8 -*-
"""压缩触发口径：**按会话**取池、**同窗口**量水位（2026-11-08）

背景（实测见 scripts/probe_compress_vs_cache.py）：
  · 触发原先算的是**全库最近 200 条**（data/messages.jsonl 无 session 字段），
    而界面占用率算的是**本会话** ⇒ 全库 99.5%、本会话 52% 时压缩照样触发，
    用户看到的就是「还没到 80% 就压缩」；
  · floor 水位却按 load_recent_messages(limit=100) 记 ⇒ counted200 恒 ≥ 1.1×counted100，
    「长 10% 再压」形同虚设（同一份消息池：失配 186 次 vs 同窗口 6 次）。

本文件把这两条钉住：池按会话、水位与触发同窗口。
"""
from __future__ import annotations

import pytest

from memory.memory_manager import (
    COMPRESS_INPUT_WINDOW,
    COMPRESS_TRIGGER_WINDOW,
    MemoryManager,
)


class _Counter:
    """len() 计数：数字可手算，不受 tiktoken 版本影响"""

    def count(self, text):
        return len(text or "")

    def count_messages(self, msgs):
        return sum(len((m or {}).get("content", "") or "") for m in msgs or [])


class _CapturingSummarizer:
    def __init__(self):
        self.seen = []

    def compress(self, messages, strategy="default"):
        self.seen.append(list(messages))
        return "摘要"

    def merge_summaries(self, old_summary, new_summary):
        return new_summary or old_summary

    def should_compress(self, total_tokens, token_limit, threshold=0.8):
        """与 memory/summarizer.py 同一判据（假件也要能回答触发问题）"""
        return total_tokens >= int(token_limit * threshold)


@pytest.fixture
def manager(tmp_path):
    mgr = MemoryManager(config={
        "data_dir": str(tmp_path / "memory_data"),
        "token_limit": 1000,
        "compress_threshold": 0.8,
        "llm": {"provider": "openai", "api_key": "sk-test-key-valid-12345", "model": "gpt-4"},
        "async_compress": {"enabled": False},
    })
    mgr._token_counter = _Counter()
    return mgr


def _msg(session_id, role="user", size=100):
    m = {"role": role, "content": "x" * size}
    if session_id:
        m["session_id"] = session_id
    return m


class TestSessionScopedPool:
    def test_别的会话再长也不触发本会话(self, manager):
        """这是用户看到的那条：全库 900+ 字（>阈值 800），本会话只有 100 字 ⇒ 不压"""
        manager._message_window.append(_msg("B", size=900))   # 另一条会话
        manager._message_window.append(_msg("A", size=100))   # 本会话
        assert manager._recent_messages("A") == [manager._message_window[-1]]
        assert manager._should_compress_now(manager._recent_messages("A"), "A") is False
        # 反过来：B 会话自己确实到阈值了（口径没被削弱）
        assert manager._should_compress_now(manager._recent_messages("B"), "B") is True

    def test_本会话到阈值照样触发(self, manager):
        manager._message_window.append(_msg("A", size=500))
        manager._message_window.append(_msg("A", role="assistant", size=400))
        assert manager._should_compress_now(manager._recent_messages("A"), "A") is True

    def test_触发时记住是哪个会话(self, manager):
        manager._message_window.append(_msg("A", size=900))
        ok = manager._maybe_request_compress("A")
        assert ok is True
        assert manager._need_compress is True
        assert manager._pending_session == "A"

    def test_未分会话保持旧口径_不筛全库(self, manager):
        """旧调用方（维护线程/手动记忆）不传 session ⇒ 行为与改动前一致"""
        manager._message_window.append(_msg("", size=300))
        manager._message_window.append(_msg("A", size=300))
        assert len(manager._recent_messages("")) == 2
        assert manager._should_compress_now(manager._recent_messages(""), "") is False

    def test_压缩输入切片只含本会话(self, manager, tmp_path):
        manager._summarizer = _CapturingSummarizer()
        for _ in range(3):
            manager._message_window.append(_msg("B", size=50))
        for _ in range(3):
            manager._message_window.append(_msg("A", size=50))
        assert manager._execute_compression("A") is True
        seen = manager._summarizer.seen[-1]
        assert seen, "压缩没有拿到输入"
        assert {m.get("session_id") for m in seen} == {"A"}
        assert len(seen) <= COMPRESS_INPUT_WINDOW

    def test_窗口为空时从落盘回退并按会话筛(self, manager):
        """进程刚重启：内存窗口是空的，必须从 messages.jsonl 读回来再筛"""
        for sid in ("A", "B"):
            for _ in range(3):
                manager._storage.save_message(_msg(sid, size=10))
        manager._message_window.clear()
        assert [m["session_id"] for m in manager._recent_messages("A", 10)] == ["A"] * 3


class TestFloorSameWindow:
    def test_水位按触发窗口量而不是摘要输入切片(self, manager):
        """前 100 条大、后 100 条小 ⇒ counted200 远大于 counted100；floor 必须是前者

        若仍按 load_recent_messages(limit=100) 记，闸门 compare 会恒成立（下一轮又压）。
        """
        manager._summarizer = _CapturingSummarizer()
        for _ in range(COMPRESS_TRIGGER_WINDOW - COMPRESS_INPUT_WINDOW):
            manager._message_window.append(_msg("A", size=50))
        for _ in range(COMPRESS_INPUT_WINDOW):
            manager._message_window.append(_msg("A", size=10))
        assert manager._execute_compression("A") is True
        counted_200 = sum(len(m["content"]) for m in manager._recent_messages("A", COMPRESS_TRIGGER_WINDOW))
        counted_100 = sum(len(m["content"]) for m in manager._recent_messages("A", COMPRESS_INPUT_WINDOW))
        assert counted_200 > counted_100 * 2, "用例前提：两个窗口的量必须差够大"
        assert manager._floor_for("A") == counted_200

    def test_压完立刻再判不会重复压(self, manager):
        manager._summarizer = _CapturingSummarizer()
        for _ in range(COMPRESS_TRIGGER_WINDOW):
            manager._message_window.append(_msg("A", size=10))
        manager._token_limit = 100
        manager._compress_threshold = 0.8
        assert manager._should_compress_now(manager._recent_messages("A"), "A") is True
        assert manager._execute_compression("A") is True
        # 只多了一条一模一样的消息：没长够 10% ⇒ 不该再压
        manager._message_window.append(_msg("A", size=10))
        assert manager._should_compress_now(manager._recent_messages("A"), "A") is False

    def test_水位按会话分桶(self, manager):
        manager._set_floor("A", 900)
        assert manager._floor_for("A") == 900
        assert manager._floor_for("B") == 0      # B 不受 A 的水位影响
        # 旧口径（未分会话）仍读 _compress_floor_tokens，兼容既有调用
        manager._compress_floor_tokens = 700
        assert manager._floor_for("") == 700
