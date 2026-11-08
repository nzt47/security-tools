# -*- coding: utf-8 -*-
"""工作台 SSE：易变上下文**不进 system prompt**（2026-11-08）

背景（实测见 scripts/probe_compress_vs_cache.py）：
  检索/完整·流式档把「工作记忆·会话摘要+最近消息」（ContextAssembler 截 500 字）拼进
  system prompt。该块每轮都变，而 system prompt 是缓存前缀的第 0 个 token 起 ⇒
  块一变，其后 99.87% 的 prompt（含 7k token 的 tools 段与全部历史）全部 miss
  （实测 13:11:31 那一次 0/62,415）。

处置与编排器 F3-1 同一原则：**稳定前缀 + 易变尾部**（尾部本来就是新的）。
下面把这条行为钉住，并验证逃生开关能回滚到旧顺序。
"""
from __future__ import annotations

import json

import pytest

import memory.llm_service as llm_module
import plugins.chat as chat_module

MARKER = "【检索片段】工作记忆：会话摘要+最近消息 …"

#: 必须避开「本地直答」的零 token 模板（问候/道谢/时间），否则根本不会调用模型
Q = "帮我查一下这个模块的作用"


class FakeLLMService:
    instances: list = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.seen_call: dict = {}
        FakeLLMService.instances.append(self)

    def chat_stream(self, messages, system_prompt="", max_tokens=1024, temperature=0.7,
                    on_tool_call=None, tools=None, on_reasoning=None):
        self.seen_call = {"messages": list(messages), "max_tokens": max_tokens,
                          "system_prompt": system_prompt, "tools": tools}
        yield "好"


@pytest.fixture(autouse=True)
def _fake_llm(monkeypatch):
    FakeLLMService.instances = []
    monkeypatch.setattr(llm_module, "LLMService", FakeLLMService, raising=True)
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_API_KEY", "sk-" + "x" * 40)
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.delenv("YUNSHU_PROMPT_VOLATILE_TAIL", raising=False)
    yield


class TestVolatileContextGoesToTail:
    def test_检索片段不再进_system_prompt(self, monkeypatch):
        monkeypatch.setattr(chat_module, "_retrieve_context_extra", lambda q: MARKER)
        for _ in chat_module._workbench_real_stream(Q, "", mode="retrieval"):
            pass
        call = FakeLLMService.instances[-1].seen_call
        assert MARKER not in call["system_prompt"], "易变上下文又回到系统前缀了"
        assert MARKER in call["messages"][-1]["content"]
        assert Q in call["messages"][-1]["content"]

    def test_不同问题的_system_prompt_逐字相同(self, monkeypatch):
        """缓存命中的行为级判据：稳定前缀必须跨请求一致（检索片段随问题变）"""
        seen = []
        for q in ("第一个问题", "第二个完全不同的问题"):
            monkeypatch.setattr(chat_module, "_retrieve_context_extra",
                                lambda _q, q=q: "ctx:" + q)
            FakeLLMService.instances = []
            for _ in chat_module._workbench_real_stream(q, "", mode="retrieval"):
                pass
            seen.append(FakeLLMService.instances[-1].seen_call)
        assert seen[0]["system_prompt"] == seen[1]["system_prompt"]
        assert seen[0]["messages"][-1]["content"] != seen[1]["messages"][-1]["content"]

    def test_逃生开关回滚到旧顺序(self, monkeypatch):
        monkeypatch.setenv("YUNSHU_PROMPT_VOLATILE_TAIL", "0")
        monkeypatch.setattr(chat_module, "_retrieve_context_extra", lambda q: MARKER)
        for _ in chat_module._workbench_real_stream(Q, "", mode="retrieval"):
            pass
        call = FakeLLMService.instances[-1].seen_call
        assert MARKER in call["system_prompt"], "关闭开关后应回到旧顺序（拼进系统提示词）"
        assert MARKER not in call["messages"][-1]["content"]

    def test_轻量档没有检索片段时系统提示词不变(self, monkeypatch):
        monkeypatch.setattr(chat_module, "_retrieve_context_extra",
                            lambda q: pytest.fail("plain 档不该检索"))
        for _ in chat_module._workbench_real_stream(Q, "", mode="plain"):
            pass
        call = FakeLLMService.instances[-1].seen_call
        assert MARKER not in call["system_prompt"]
        assert call["messages"][-1]["content"] == Q
