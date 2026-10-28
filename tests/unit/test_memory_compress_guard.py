# -*- coding: utf-8 -*-
"""记忆自动整理的三个「跑了但没效果」缺口（2026-10-28 实测）：

1. 空摘要照样落盘：生产 30 次压缩里 22 次摘要 0 字符（推理型模型把 max_tokens
   全花在 reasoning 上），旧代码仍 save_summary + 版本号 +1 ⇒ summary.txt 0 字节、
   版本已到 88，还清了 _need_compress。
2. 摘要首次为空直接当失败：现在放大预算重试一次。
3. 反思经验读写键不匹配：写入侧用 task_description/failure_point/solution，
   装配侧只认 note/content/lesson/experience ⇒ lessons.json 永远 0 注入。
"""
from __future__ import annotations

import json

import pytest

from memory.memory_manager import MemoryManager
from memory.summarizer import RETRY_TOKENS, Summarizer


class _FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def summarize(self, messages, max_tokens=0):
        self.calls.append(max_tokens)
        return self.replies.pop(0) if self.replies else ""


class _FakeStorage:
    def __init__(self, summary="旧摘要", version=7, messages=None):
        self.saved = []
        self._summary = (summary, version) if summary is not None else None
        self._messages = messages if messages is not None else [{"role": "user", "content": "x"}]

    def load_summary(self):
        return self._summary

    def load_recent_messages(self, limit=100):
        return self._messages

    def save_summary(self, text, version):
        self.saved.append((text, version))


class _FakeBlackBox:
    def __init__(self):
        self.events = []

    def log(self, action, payload):
        self.events.append((action, payload))


def _manager(summary_text, summary="旧摘要", version=7, replies=None):
    mm = MemoryManager.__new__(MemoryManager)
    mm._storage = _FakeStorage(summary=summary, version=version)
    # 第一次是 compress，第二次（若需要合并）是 merge_summaries
    mm._summarizer = Summarizer(_FakeLLM(replies if replies is not None else [summary_text]))
    mm._black_box = _FakeBlackBox()
    mm._need_compress = True
    mm._summary_cache = None
    return mm


def test_compress_retries_with_bigger_budget_on_empty():
    llm = _FakeLLM(["", "真正的摘要"])
    s = Summarizer(llm)
    assert s.compress([{"role": "user", "content": "hi"}]) == "真正的摘要"
    assert llm.calls == [500, RETRY_TOKENS]


def test_compress_returns_empty_when_both_attempts_empty():
    llm = _FakeLLM(["", ""])
    assert Summarizer(llm).compress([{"role": "user", "content": "hi"}]) == ""


def test_empty_summary_is_not_persisted():
    mm = _manager("")
    assert mm._execute_compression() is False
    assert mm._storage.saved == [], "空摘要不得写盘（版本号也不得涨）"
    assert mm._need_compress is True, "失败要保留标记，下一轮还能重试"
    assert any(a == "memory_compress_empty" for a, _ in mm._black_box.events)


def test_non_empty_summary_still_persisted():
    mm = _manager("", replies=["一份有效摘要", "合并后的摘要"])
    assert mm._execute_compression() is True
    assert mm._storage.saved and mm._storage.saved[0][0] == "合并后的摘要"
    assert mm._storage.saved[0][1] == 8, "成功时版本号才 +1"
    assert mm._empty_compress_streak == 0


def test_three_consecutive_empty_summaries_stop_retrying():
    mm = _manager("")
    for _ in range(3):
        assert mm._execute_compression() is False
    assert mm._need_compress is False, "连续空摘要要停手，别每分钟烧 LLM"
    assert mm._empty_compress_streak == 3


def test_reflection_lesson_keys_are_injected(tmp_path, monkeypatch):
    from agent.orchestrator.orchestrator import Orchestrator

    refl = tmp_path / "data" / "reflection"
    refl.mkdir(parents=True)
    (refl / "experiences.json").write_text("[]", encoding="utf-8")
    (refl / "lessons.json").write_text(json.dumps([
        {"task_description": "做 X 失败", "failure_point": "漏了 Y", "solution": "先做 Y"},
        {"comment": "没有可注入字段的条目"},
    ], ensure_ascii=False), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    chunks = Orchestrator._context_assembler_long_term(None, "任务")
    assert len(chunks) == 1, chunks
    content = chunks[0]["content"]
    assert "做 X 失败" in content and "先做 Y" in content
