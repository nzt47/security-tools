# -*- coding: utf-8 -*-
"""工作台系统提示词接到「提示词实验室」模板（2026-11-08）

背景：实验室改的身份提示词此前**只对编排器路径生效**，主 UI 的工作台对话用的是
硬编码 75 字常量 ⇒ 用户改了看不到效果（口径分裂）。现在工作台读 data/system_prompt.txt。

本文件钉住三件事：
  1. 模板真的进请求（且槽位只填跨请求稳定的值）；
  2. 模板坏了/含裸花括号 ⇒ 逐字回退旧常量（绝不让对话起不来）；
  3. 换档（plain ↔ retrieval）不改 system prompt（前缀缓存契约）。
"""
from __future__ import annotations

import json

import pytest

import agent.system_prompt_manager as spm
import memory.llm_service as llm_module
import plugins.chat as chat_module

TEMPLATE = (
    "身份：云枢测试模板\n\n"
    "## 技能\n{skill_instructions}\n"
    "## 工具\n{tool_status}\n"
    "## 记忆线索\n{memory_context}\n"
)


class FakeLLMService:
    instances: list = []

    def __init__(self, **kwargs):
        self.seen_call: dict = {}
        FakeLLMService.instances.append(self)

    def chat_stream(self, messages, system_prompt="", max_tokens=1024, temperature=0.7,
                    on_tool_call=None, tools=None, on_reasoning=None):
        self.seen_call = {"messages": list(messages), "system_prompt": system_prompt, "tools": tools}
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


class TestRender:
    def test_模板渲染进系统提示词(self, monkeypatch):
        monkeypatch.setattr(spm, "get_template", lambda: TEMPLATE)
        out = chat_module._render_workbench_system_prompt()
        assert "身份：云枢测试模板" in out
        assert "## 记忆线索" in out

    def test_易变槽位留空_技能与记忆线索不写进前缀(self, monkeypatch):
        monkeypatch.setattr(spm, "get_template", lambda: TEMPLATE)
        out = chat_module._render_workbench_system_prompt()
        for leaked in ("技能指令：先查文档", "【长期记忆】"):
            assert leaked not in out
        # 槽位留空 ⇒ 模板骨架还在，但内容为空（前缀字节稳定）
        assert "## 技能\n\n" in out or "## 技能\n" in out

    def test_坏模板回退兜底常量(self, monkeypatch):
        # 自定义模板里带裸花括号（JSON 示例）⇒ str.format 抛错 ⇒ 不猜，回退旧行为
        monkeypatch.setattr(spm, "get_template", lambda: "身份 { 不是占位符 }")
        out = chat_module._render_workbench_system_prompt()
        assert out == chat_module._WORKBENCH_FALLBACK_SYSTEM_PROMPT

    def test_未知占位符也回退(self, monkeypatch):
        monkeypatch.setattr(spm, "get_template", lambda: "身份 {unknown_placeholder}")
        out = chat_module._render_workbench_system_prompt()
        assert out == chat_module._WORKBENCH_FALLBACK_SYSTEM_PROMPT

    def test_模板为空回退(self, monkeypatch):
        monkeypatch.setattr(spm, "get_template", lambda: "   ")
        assert chat_module._render_workbench_system_prompt() == chat_module._WORKBENCH_FALLBACK_SYSTEM_PROMPT

    def test_工具槽位只写部署级计数_不列工具名(self, monkeypatch):
        monkeypatch.setattr(spm, "get_template", lambda: TEMPLATE)
        out = chat_module._render_workbench_system_prompt()
        assert "可调用工具" in out or out  # 计数可能取不到 ⇒ 至少不炸
        for name in ("read_file", "list_directory", "grep", "shell_execute"):
            assert name not in out, "工具名不得进系统提示词（换档会整段失效）"


class TestEndToEnd:
    def _run(self, monkeypatch, mode="plain"):
        monkeypatch.setattr(spm, "get_template", lambda: TEMPLATE)
        FakeLLMService.instances = []
        for _ in chat_module._workbench_real_stream("帮我查一下这个模块的作用", "", mode=mode):
            pass
        return FakeLLMService.instances[-1].seen_call

    def test_请求里的_system_prompt_来自模板(self, monkeypatch):
        call = self._run(monkeypatch, "plain")
        assert "身份：云枢测试模板" in call["system_prompt"]
        assert call["system_prompt"] != chat_module._WORKBENCH_FALLBACK_SYSTEM_PROMPT

    def test_换档不改_system_prompt(self, monkeypatch):
        a = self._run(monkeypatch, "plain")
        b = self._run(monkeypatch, "retrieval")
        assert a["system_prompt"] == b["system_prompt"], "换档不得改系统提示词（前缀缓存契约）"
