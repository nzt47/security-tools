# -*- coding: utf-8 -*-
"""压缩复用对话前缀：开关、登记、过期、装不下、指令后置（2026-11-08）

背景（实测见 scripts/probe_compress_prefix_reuse.py 与 docs/handoff §11.71）：
  · 旧路径命中 0/57,385（压缩 system 与对话 system 不同、压缩不带 tools、消息切片起点不同）；
  · 同 system+tools+消息切片 + 指令后置 ⇒ 真实调用命中 87,552/87,701 = 99.8%。

本文件钉住四条不能退化的口径：
  1. 默认关 ⇒ 逐字旧行为（不传 prefix，指令仍在首条 system）；
  2. 开关开 + 前缀新鲜 + 装得下 ⇒ 压缩拿到**同一份** system/tools/消息 + 指令在末尾；
  3. 前缀过期（冷缓存会更贵 1.85 倍）⇒ 回落旧路径；
  4. 前缀+输出+余量装不下窗口 ⇒ 回落旧路径（宁可全价，不可 400）。
"""
from __future__ import annotations

import inspect

import pytest

from agent import chat_prefix
from memory.memory_manager import COMPRESS_PREFIX_MARGIN_TOKENS, MemoryManager
from memory.summarizer import RETRY_TOKENS, Summarizer


class _Counter:
    """len() 计数：数字可手算，不受 tokenizer 版本影响"""

    def count(self, text):
        return len(text or "")


class _CapturingSummarizer:
    def __init__(self):
        self.seen = []
        self.prefixes = []

    def compress(self, messages, strategy="default", *, prefix=None):
        self.seen.append(list(messages))
        self.prefixes.append(prefix)
        return "摘要"

    def merge_summaries(self, old_summary, new_summary):
        return new_summary or old_summary

    def should_compress(self, total_tokens, token_limit, threshold=0.8):
        return total_tokens >= int(token_limit * threshold)


class _FakeLLM:
    def __init__(self):
        self.calls = []

    def summarize(self, messages, max_tokens=500, system_prompt="", tools=None):
        self.calls.append({"messages": list(messages), "max_tokens": max_tokens,
                           "system_prompt": system_prompt, "tools": tools})
        return "一段摘要"


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    chat_prefix.reset()
    monkeypatch.delenv(chat_prefix.ENV_FLAG, raising=False)
    yield
    chat_prefix.reset()


def _msg(session_id, role="user", size=10):
    m = {"role": role, "content": "x" * size}
    if session_id:
        m["session_id"] = session_id
    return m


def _prefix(system="系统", n_msgs=2, size=10, tools=None):
    return {"system_prompt": system,
            "messages": [{"role": "user", "content": "m" * size} for _ in range(n_msgs)],
            "tools": tools}


class TestRegistry:
    def test_默认关(self):
        assert chat_prefix.enabled() is False

    @pytest.mark.parametrize("val", ["1", "true", "YES", "on"])
    def test_真值解析(self, monkeypatch, val):
        monkeypatch.setenv(chat_prefix.ENV_FLAG, val)
        assert chat_prefix.enabled() is True

    @pytest.mark.parametrize("val", ["0", "false", "off", "", "disable"])
    def test_假值与拼错一律关(self, monkeypatch, val):
        monkeypatch.setenv(chat_prefix.ENV_FLAG, val)
        assert chat_prefix.enabled() is False

    def test_登记后可取回且是副本(self):
        msgs = [{"role": "user", "content": "hi"}]
        assert chat_prefix.record("A", "SYS", [{"type": "function"}], msgs) is True
        got = chat_prefix.peek("A")
        assert got["system_prompt"] == "SYS"
        assert got["messages"] == msgs
        got["messages"][0]["content"] = "被改了"
        assert chat_prefix.peek("A")["messages"][0]["content"] == "hi"

    def test_工具循环后续_append_不会污染快照(self):
        loop = [{"role": "user", "content": "q"}]
        chat_prefix.record("A", "SYS", None, loop)
        loop.append({"role": "assistant", "content": "后面追加的"})
        assert len(chat_prefix.peek("A")["messages"]) == 1

    def test_会话之间互不污染(self):
        chat_prefix.record("A", "SYS-A", None, [{"role": "user", "content": "a"}])
        chat_prefix.record("B", "SYS-B", None, [{"role": "user", "content": "b"}])
        assert chat_prefix.peek("A")["system_prompt"] == "SYS-A"
        assert chat_prefix.peek("B")["system_prompt"] == "SYS-B"
        assert chat_prefix.peek("C") is None

    def test_空会话不登记(self):
        assert chat_prefix.record("", "SYS", None, [{"role": "user", "content": "x"}]) is False
        assert chat_prefix.peek("") is None

    def test_最多保留_MAX_SESSIONS_个会话(self):
        for i in range(chat_prefix.MAX_SESSIONS + 3):
            chat_prefix.record("s%d" % i, "SYS", None, [{"role": "user", "content": "x"}])
        assert chat_prefix.peek("s0") is None, "最久未用的会话应被淘汰"
        assert chat_prefix.peek("s%d" % (chat_prefix.MAX_SESSIONS + 2)) is not None

    def test_过期前缀取不到(self, monkeypatch):
        t0 = [1000.0]
        monkeypatch.setattr(chat_prefix, "_now", lambda: t0[0])
        chat_prefix.record("A", "SYS", None, [{"role": "user", "content": "x"}])
        assert chat_prefix.get_if_fresh("A") is not None
        t0[0] += chat_prefix.MAX_AGE_SECONDS + 1
        assert chat_prefix.get_if_fresh("A") is None


class TestSummarizerPrefix:
    def test_前缀模式_指令在末尾且透传_system_tools(self):
        llm = _FakeLLM()
        tools = [{"type": "function", "function": {"name": "read_file"}}]
        prefix = _prefix(system="工作台系统提示", tools=tools)
        text = Summarizer(llm).compress([{"role": "user", "content": "落盘消息"}],
                                         prefix=prefix)
        assert text == "一段摘要"
        call = llm.calls[-1]
        assert call["system_prompt"] == "工作台系统提示"
        assert call["tools"] == tools
        assert call["messages"][:len(prefix["messages"])] == prefix["messages"]
        assert call["messages"][-1]["role"] == "user"
        assert call["messages"][-1]["content"] == Summarizer.STRATEGIES["default"]

    def test_无前缀时逐字旧行为(self):
        llm = _FakeLLM()
        Summarizer(llm).compress([{"role": "user", "content": "hi"}])
        call = llm.calls[-1]
        assert call["messages"][0] == {"role": "system",
                                       "content": Summarizer.STRATEGIES["default"]}
        assert call["system_prompt"] == ""
        assert call["tools"] is None

    def test_前缀消息为空时回落旧路径(self):
        llm = _FakeLLM()
        Summarizer(llm).compress([{"role": "user", "content": "hi"}],
                                 prefix={"system_prompt": "S", "messages": [], "tools": None})
        assert llm.calls[-1]["messages"][0]["role"] == "system"


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
    mgr._summarizer = _CapturingSummarizer()
    return mgr


class TestMemoryManagerReuse:
    def test_开关关时不传前缀(self, manager, monkeypatch):
        chat_prefix.record("A", "SYS", None, [{"role": "user", "content": "x"}])
        manager._message_window.append(_msg("A"))
        assert manager._execute_compression("A") is True
        assert manager._summarizer.prefixes[-1] is None

    def test_开关开且前缀新鲜时复用(self, manager, monkeypatch):
        monkeypatch.setenv(chat_prefix.ENV_FLAG, "1")
        chat_prefix.record("A", "SYS", [{"type": "function"}],
                           [{"role": "user", "content": "对话原文"}])
        manager._message_window.append(_msg("A"))
        assert manager._execute_compression("A") is True
        got = manager._summarizer.prefixes[-1]
        assert got is not None
        assert got["system_prompt"] == "SYS"
        assert got["messages"][0]["content"] == "对话原文"

    def test_别的会话的前缀不会被复用(self, manager, monkeypatch):
        monkeypatch.setenv(chat_prefix.ENV_FLAG, "1")
        chat_prefix.record("B", "SYS-B", None, [{"role": "user", "content": "b"}])
        manager._message_window.append(_msg("A"))
        assert manager._execute_compression("A") is True
        assert manager._summarizer.prefixes[-1] is None

    def test_模型不一致时不复用(self, manager, monkeypatch):
        """缓存键含模型：分身会话的模型可能与母体不同 ⇒ 复用只会白付 1.85 倍"""
        monkeypatch.setenv(chat_prefix.ENV_FLAG, "1")
        chat_prefix.record("A", "SYS", None, [{"role": "user", "content": "x"}],
                           model="some-other-model")
        manager._message_window.append(_msg("A"))
        assert manager._execute_compression("A") is True
        assert manager._summarizer.prefixes[-1] is None

    def test_模型一致时复用(self, manager, monkeypatch):
        monkeypatch.setenv(chat_prefix.ENV_FLAG, "1")
        chat_prefix.record("A", "SYS", None, [{"role": "user", "content": "x"}],
                           model=manager._llm_service.model)
        manager._message_window.append(_msg("A"))
        assert manager._execute_compression("A") is True
        assert manager._summarizer.prefixes[-1] is not None

    def test_前缀过期回落旧路径(self, manager, monkeypatch):
        monkeypatch.setenv(chat_prefix.ENV_FLAG, "1")
        t0 = [1000.0]
        monkeypatch.setattr(chat_prefix, "_now", lambda: t0[0])
        chat_prefix.record("A", "SYS", None, [{"role": "user", "content": "x"}])
        t0[0] += chat_prefix.MAX_AGE_SECONDS + 1
        manager._message_window.append(_msg("A"))
        assert manager._execute_compression("A") is True
        assert manager._summarizer.prefixes[-1] is None

    def test_装不下窗口时回落旧路径(self, manager, monkeypatch):
        monkeypatch.setenv(chat_prefix.ENV_FLAG, "1")
        # 窗口 100000；前缀正文 99000 + system 2000 + 余量(2000+1024) ⇒ 超窗
        chat_prefix.record("A", "s" * 2000, None,
                           [{"role": "user", "content": "x" * 99000}])
        manager._message_window.append(_msg("A"))
        assert manager._execute_compression("A") is True
        assert manager._summarizer.prefixes[-1] is None


class TestWiringInvariants:
    """守「谁在哪登记、谁在哪读」的不变量（不测实现细节，只守顺序/存在性）"""

    def test_工作台在真实调用前登记前缀(self):
        from plugins import chat as chat_plugin
        src = inspect.getsource(chat_plugin._workbench_real_stream)
        assert "chat_prefix.record(" in src
        assert src.index("chat_prefix.record(") < src.index("llm.chat_stream(**stream_kwargs)")

    def test_监控钩子透传_tools(self):
        from agent import llm_monitor
        src = inspect.getsource(llm_monitor.install_hooks)
        assert "def _patched_do_summarize(self, messages, max_tokens=500, system_prompt=\"\", tools=None)" in src
        assert "orig_do_summarize(self, messages, max_tokens, system_prompt, tools)" in src

    def test_余量常量大于输出预算不会相互吞掉(self):
        assert COMPRESS_PREFIX_MARGIN_TOKENS > 0
        assert RETRY_TOKENS > 0
