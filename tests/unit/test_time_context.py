# -*- coding: utf-8 -*-
"""E0-4① 每步时钟（time-context，2026-11-26）

为什么默认关：时间戳会改变「同一问题同一答案」的复现性；要开才开。
为什么只有一处注入点：时间每轮都变 ⇒ 必须走「易变尾簇」（请求最后一条消息），
否则会把整条请求的缓存前缀钉死（2026-11-08 实测过同一类事故）。

本文件钉住：
  1. 默认关、显式开才注入（拼错/空值一律当关，不猜）；
  2. 内容含本地时刻 + 星期 + 本轮耗时，并如实声明「不是硬约束」；
  3. 进的是**最后一条用户消息**，system prompt 逐字不变（缓存前缀不动）；
  4. 参数再脏也不抛（提示词装配失败绝不影响对话）。
"""
from __future__ import annotations

import pytest

import agent.time_context as tc


class TestTailText:
    def test_默认关不注入(self, monkeypatch):
        monkeypatch.delenv(tc.ENV_TIME_CONTEXT, raising=False)
        assert tc.enabled() is False
        assert tc.tail_text() == ""

    def test_拼错当关(self, monkeypatch):
        monkeypatch.setenv(tc.ENV_TIME_CONTEXT, "maybe")
        assert tc.enabled() is False

    def test_开启后含时刻_星期_耗时_声明(self, monkeypatch):
        monkeypatch.setenv(tc.ENV_TIME_CONTEXT, "1")
        text = tc.tail_text(started_at=1000.0, now=1012.3)
        assert "【时间上下文】" in text
        assert tc.stamp(1012.3) in text
        assert "本轮已进行 12.3s" in text
        assert "不是硬约束" in text

    def test_不给起点就只报时刻(self, monkeypatch):
        monkeypatch.setenv(tc.ENV_TIME_CONTEXT, "true")
        text = tc.tail_text(now=1012.3)
        assert "本轮已进行" not in text
        assert tc.stamp(1012.3) in text

    def test_起点是脏数据也不抛(self, monkeypatch):
        monkeypatch.setenv(tc.ENV_TIME_CONTEXT, "yes")
        text = tc.tail_text(started_at="not-a-number", now=1012.3)
        assert "【时间上下文】" in text

    def test_星期中文(self):
        # 2026-11-27 是周五（本地时区，UTC+8 的 09:00）
        assert "周五" in tc.stamp(1795741200.0)


class TestInjectionPoint:
    """真机链路：时间块必须落在「请求最后一条消息」，system prompt 逐字不变"""

    def test_时间块进最后一条消息而不进系统前缀(self, monkeypatch):
        import memory.llm_service as llm_module
        import plugins.chat as chat_module
        from tests.unit.test_workbench_volatile_context import FakeLLMService, Q

        FakeLLMService.instances = []
        monkeypatch.setattr(llm_module, "LLMService", FakeLLMService, raising=True)
        monkeypatch.setenv("LLM_PROVIDER", "deepseek")
        monkeypatch.setenv("LLM_API_KEY", "sk-" + "x" * 40)
        monkeypatch.setenv("LLM_MODEL", "deepseek-v4-flash")
        monkeypatch.delenv("YUNSHU_PROMPT_VOLATILE_TAIL", raising=False)
        monkeypatch.setenv(tc.ENV_TIME_CONTEXT, "1")
        monkeypatch.setattr(chat_module, "_retrieve_context_extra", lambda q: "检索片段")

        for _ in chat_module._workbench_real_stream(Q, "", mode="retrieval"):
            pass
        call = FakeLLMService.instances[-1].seen_call
        assert "【时间上下文】" in call["messages"][-1]["content"]
        assert "【时间上下文】" not in call["system_prompt"], "时间块不得进缓存前缀"
        assert Q in call["messages"][-1]["content"]

    def test_关着时提示词里没有时间块(self, monkeypatch):
        import memory.llm_service as llm_module
        import plugins.chat as chat_module
        from tests.unit.test_workbench_volatile_context import FakeLLMService, Q

        FakeLLMService.instances = []
        monkeypatch.setattr(llm_module, "LLMService", FakeLLMService, raising=True)
        monkeypatch.setenv("LLM_PROVIDER", "deepseek")
        monkeypatch.setenv("LLM_API_KEY", "sk-" + "x" * 40)
        monkeypatch.setenv("LLM_MODEL", "deepseek-v4-flash")
        monkeypatch.delenv("YUNSHU_PROMPT_VOLATILE_TAIL", raising=False)
        monkeypatch.delenv(tc.ENV_TIME_CONTEXT, raising=False)
        monkeypatch.setattr(chat_module, "_retrieve_context_extra", lambda q: "检索片段")

        for _ in chat_module._workbench_real_stream(Q, "", mode="retrieval"):
            pass
        call = FakeLLMService.instances[-1].seen_call
        assert "【时间上下文】" not in call["system_prompt"]
        assert "【时间上下文】" not in call["messages"][-1]["content"]

