# -*- coding: utf-8 -*-
"""工作台各档共用同一份 tools 段（2026-11-08 默认关掉轻量档减法）

背景（实测见交接文档 §11.68）：plain(17 个工具) → 检索(26 个) 那一次前缀命中只有
12.8%（miss≈6,946 —— 整段 tools 段作废）。自动档会按问句长短在两档间来回切 ⇒ 每次
都付一次 tools 段全价。统一工具段后，换档不再改 tools 段，只改请求尾部的检索片段。

本文件钉住：
  1. 默认**不**做减法（_plain_subset_enabled() 为假）；
  2. 减法只在显式开关打开时才会被调用（CP_WORKBENCH_PLAIN_TOOL_SUBSET=1 可回滚）；
  3. 端到端：plain 与 retrieval 两次请求的 tools 名列表逐字相同。
"""
from __future__ import annotations

import json

import pytest

import memory.llm_service as llm_module
import plugins.chat as chat_module


class FakeLLMService:
    instances: list = []

    def __init__(self, **kwargs):
        self.seen_call: dict = {}
        FakeLLMService.instances.append(self)

    def chat_stream(self, messages, system_prompt="", max_tokens=1024, temperature=0.7,
                    on_tool_call=None, tools=None, on_reasoning=None):
        self.seen_call = {"messages": list(messages), "system_prompt": system_prompt,
                          "tools": [json.loads(json.dumps(t)) for t in (tools or [])]}
        yield "好"


@pytest.fixture(autouse=True)
def _fake_llm(monkeypatch):
    FakeLLMService.instances = []
    monkeypatch.setattr(llm_module, "LLMService", FakeLLMService, raising=True)
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_API_KEY", "sk-" + "x" * 40)
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.delenv(chat_module._WORKBENCH_PLAIN_SUBSET_ENV, raising=False)
    yield


def _run(mode, monkeypatch, question="帮我查一下这个模块的作用"):
    monkeypatch.setattr(chat_module, "_retrieve_context_extra", lambda q: "检索片段")
    FakeLLMService.instances = []
    for _ in chat_module._workbench_real_stream(question, "", mode=mode):
        pass
    return FakeLLMService.instances[-1].seen_call


def _names(call):
    return [((t or {}).get("function") or {}).get("name") for t in (call.get("tools") or [])]


class TestFlag:
    def test_默认关闭(self):
        assert chat_module._plain_subset_enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
    def test_显式打开(self, monkeypatch, value):
        monkeypatch.setenv(chat_module._WORKBENCH_PLAIN_SUBSET_ENV, value)
        assert chat_module._plain_subset_enabled() is True

    def test_关闭值(self, monkeypatch):
        monkeypatch.setenv(chat_module._WORKBENCH_PLAIN_SUBSET_ENV, "0")
        assert chat_module._plain_subset_enabled() is False


class TestSubsetOnlyWhenEnabled:
    """减法函数只在开关打开时被调用（默认连"摸一下"都不摸）"""

    def _patch_line(self, monkeypatch):
        names = ["t%02d" % i for i in range(26)]
        monkeypatch.setattr("agent.lines.line_whitelist",
                            lambda _: (names, type("R", (), {"line_id": "fake"})()), raising=False)

    def test_默认不调用减法(self, monkeypatch):
        calls = []
        monkeypatch.setattr(chat_module, "_plain_tool_subset",
                            lambda wl, boost: (calls.append(1), wl)[1])
        self._patch_line(monkeypatch)
        _run("plain", monkeypatch)
        assert calls == [], "默认不得再做轻量档减法（否则换档会重算 tools 段）"

    def test_开关打开才调用减法(self, monkeypatch):
        calls = []
        monkeypatch.setattr(chat_module, "_plain_tool_subset",
                            lambda wl, boost: (calls.append(1), wl)[1])
        self._patch_line(monkeypatch)
        monkeypatch.setenv(chat_module._WORKBENCH_PLAIN_SUBSET_ENV, "1")
        _run("plain", monkeypatch)
        assert calls, "开关打开后应恢复减法（回滚路径必须可达）"


class TestToolParityEndToEnd:
    def test_two_modes_share_same_tools(self, monkeypatch):
        plain = _run("plain", monkeypatch)
        retrieval = _run("retrieval", monkeypatch)
        a, b = _names(plain), _names(retrieval)
        assert a == b, "两档工具段必须逐字相同（换档不得重算前缀）"
        if not a:
            pytest.skip("本环境未下发工具（工具加载失败/空白名单），端到端比对无意义")
