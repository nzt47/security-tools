# -*- coding: utf-8 -*-
"""摘要按会话分家（2026-11-09）：开关、读写分家、首压迁移、当前会话缺省。

交接条 per-session-summary（P2③）。Owner 2026-11-09 选：①迁移=用全局摘要作基底；②范围=全链路。

本文件钉住四条：
  1. 开关关 ⇒ 逐字旧行为（全仓一份 summary.txt，任何人读到同一份）；
  2. 开关开 ⇒ 压缩写**该会话**文件，别的会话读不到；
  3. 首压迁移 ⇒ 该会话没有自己的摘要时，以全局摘要为基底（不丢历史要点）；
  4. 读侧缺省 = 当前会话（agent/current_session，由 SessionManager 发布）。
"""
from __future__ import annotations

import json

import pytest

from agent import current_session
from memory.memory_manager import ENV_SESSION_SUMMARY_SCOPED, MemoryManager


class _Counter:
    def count(self, text):
        return len(text or "")

    def count_messages(self, msgs):
        return sum(len((m or {}).get("content", "") or "") for m in msgs or [])


class _Summarizer:
    def __init__(self):
        self.merges = []

    def compress(self, messages, strategy="default", *, prefix=None):
        return "新摘要"

    def merge_summaries(self, old_summary, new_summary):
        self.merges.append(old_summary)
        return (old_summary or "") + "|" + new_summary

    def should_compress(self, total_tokens, token_limit, threshold=0.8):
        return True


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    current_session.clear()
    monkeypatch.delenv(ENV_SESSION_SUMMARY_SCOPED, raising=False)
    yield
    current_session.clear()


@pytest.fixture
def manager(tmp_path):
    mgr = MemoryManager(config={
        "data_dir": str(tmp_path / "memory_data"),
        "token_limit": 100000,
        "compress_threshold": 0.8,
        "llm": {"provider": "openai", "api_key": "sk-test-key-valid-12345", "model": "gpt-4"},
        "async_compress": {"enabled": False},
    })
    mgr._token_counter = _Counter()
    mgr._summarizer = _Summarizer()
    return mgr


def _msg(sid, size=10):
    m = {"role": "user", "content": "x" * size}
    if sid:
        m["session_id"] = sid
    return m


def _compress(mgr, sid):
    mgr._message_window.append(_msg(sid))
    mgr._pending_session = sid
    assert mgr._execute_compression(sid) is True


def test_开关关时仍是一份全局摘要(manager):
    _compress(manager, "sess_A")
    assert manager._storage.load_summary("") is not None
    assert not [p for p in manager._storage.data_dir.glob("summary_*.txt")
                if p.name.startswith("summary_") and p.name[8:24].isalnum()]
    # 换任何会话读到同一份（旧行为：这正是要修的串味，但默认关时保持兼容）
    assert manager.load_summary("sess_B") is not None


def test_开关开时压缩写会话自己的文件(manager, monkeypatch):
    monkeypatch.setenv(ENV_SESSION_SUMMARY_SCOPED, "1")
    _compress(manager, "sess_A")
    assert manager.load_summary("sess_A") is not None
    assert manager.load_summary("sess_B") is None, "B 不该看到 A 的摘要"
    assert manager.load_summary("") is None, "全局文件不该被会话压缩写入"
    files = [p.name for p in manager._storage.data_dir.glob("summary_*.txt")
             if not p.name.endswith(".version.txt")]
    assert len(files) == 1


def test_首压迁移_以全局摘要为基底(manager, monkeypatch):
    monkeypatch.setenv(ENV_SESSION_SUMMARY_SCOPED, "1")
    manager._storage.save_summary("全局要点", 5)
    _compress(manager, "sess_A")
    assert manager._summarizer.merges[-1] == "全局要点", "第一次压缩必须带上全局摘要"
    summary, version = manager.load_summary("sess_A")
    assert "全局要点" in summary
    assert version == 6, "版本接着全局的 5 往上走"


def test_第二次压缩不再拿全局(manager, monkeypatch):
    monkeypatch.setenv(ENV_SESSION_SUMMARY_SCOPED, "1")
    manager._storage.save_summary("全局要点", 5)
    _compress(manager, "sess_A")
    _compress(manager, "sess_A")
    assert manager._summarizer.merges[-1].startswith("全局要点|"), "第二次的基底是自己的摘要"


def test_读侧缺省用当前会话(manager, monkeypatch):
    monkeypatch.setenv(ENV_SESSION_SUMMARY_SCOPED, "1")
    _compress(manager, "sess_A")
    current_session.set_current("sess_A")
    assert manager.load_summary() is not None, "缺省应读到当前会话的摘要"
    current_session.set_current("sess_B")
    assert manager.load_summary() is None, "切到 B 就不该再看到 A 的"


def test_get_context_按会话取摘要(manager, monkeypatch):
    monkeypatch.setenv(ENV_SESSION_SUMMARY_SCOPED, "1")
    _compress(manager, "sess_A")
    ctx_a = manager.get_context(1000, "sess_A")
    assert any(m.get("role") == "system" and "新摘要" in m.get("content", "")
               for m in ctx_a)
    ctx_b = manager.get_context(1000, "sess_B")
    assert not any("新摘要" in m.get("content", "") for m in ctx_b)


def test_开关关时_get_context_恒全局(manager):
    _compress(manager, "sess_A")
    ctx_b = manager.get_context(1000, "sess_B")
    assert any(m.get("role") == "system" and "新摘要" in m.get("content", "")
               for m in ctx_b), "开关关 ⇒ 任何会话读到同一份（旧行为）"


def test_当前会话指针(manager):
    assert current_session.get_current() == ""
    current_session.set_current("sess_X")
    assert current_session.get_current() == "sess_X"
    current_session.clear()
    assert current_session.get_current() == ""


def test_开关状态可热读(manager, monkeypatch):
    assert manager.session_summary_scoped() is False
    monkeypatch.setenv(ENV_SESSION_SUMMARY_SCOPED, "on")
    assert manager.session_summary_scoped() is True
