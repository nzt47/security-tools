"""工作台 SSE · 思考过程（reasoning）与工具调用事件的真实生成器测试

对应需求：「恢复工具调用过程和思考过程的显示功能」。

链路契约（前端 yunshu-ui/src/lib/sse.ts 与 store 依赖它）：
  - LLMService.chat_stream 新增的 `on_reasoning` 回调（additive）逐段拿到
    DeepSeek reasoning_content；
  - plugins/chat.py 的 `_workbench_real_stream` 把新增推理增量转成
    `{"type":"thinking","id":"reasoning","title":"思考过程",...}` 事件（running），
    本轮结束后补一条 status=done；
  - 工具调用仍按 `工具调用：<name>` 的 thinking 事件（running → done）外发。

本测试用假 LLMService 替换真实服务，逐条核对 SSE 事件，不发起任何真实网络调用。
"""
from __future__ import annotations

import json

import pytest

import memory.llm_service as llm_module
import plugins.chat as chat_module


class FakeLLMService:
    """假 LLM 服务：产出一段推理 + 两片正文（可选带上一次工具调用）"""

    instances: list = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.seen_on_reasoning = None
        FakeLLMService.instances.append(self)

    def chat_stream(self, messages, system_prompt="", max_tokens=1024, temperature=0.7,
                    on_tool_call=None, tools=None, on_reasoning=None):
        self.seen_on_reasoning = on_reasoning
        if on_reasoning is not None:
            on_reasoning("先判断意图。")
            on_reasoning("再决定要不要调工具。")
        yield "你好"
        yield "，世界"


@pytest.fixture(autouse=True)
def _fake_llm(monkeypatch):
    """替换 LLMService + 提供可用 key（否则降级为演示流，测不到 reasoning 链路）"""
    FakeLLMService.instances = []
    monkeypatch.setattr(llm_module, "LLMService", FakeLLMService, raising=True)
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_API_KEY", "sk-" + "x" * 40)
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-flash")
    yield


def _events(question="帮我查一下云枢") -> list[dict]:
    raw = list(chat_module._workbench_real_stream(question, ""))
    out = []
    for block in raw:
        payload = block[len("data:"):].strip()
        if payload:
            out.append(json.loads(payload))
    return out


class TestReasoningEvents:
    def test_思考过程以_thinking_事件外发且带增量内容(self):
        events = _events()
        reasoning = [e for e in events if e.get("id") == "reasoning"]
        assert reasoning, "未外发 reasoning 思考事件"
        running = [e for e in reasoning if e["status"] == "running"]
        assert running
        assert running[0]["title"] == "思考过程"
        # 两段推理被拼成一段增量（不是每段一个事件，前端按 running 累加）
        assert running[0]["detail"] == "先判断意图。再决定要不要调工具。"
        # 本轮结束补 done（前端据此把思考块标记完成）
        assert reasoning[-1]["status"] == "done"

    def test_正文分片与_done_事件保持原契约(self):
        events = _events()
        chunks = [e for e in events if e["type"] == "chunk"]
        assert "".join(c["text"] for c in chunks) == "你好，世界"
        assert [c["seq"] for c in chunks] == [1, 2]
        assert events[-1] == {"type": "done"}

    def test_推理事件先于正文分片(self):
        events = _events()
        first_reasoning = next(i for i, e in enumerate(events) if e.get("id") == "reasoning")
        first_chunk = next(i for i, e in enumerate(events) if e["type"] == "chunk")
        assert first_reasoning < first_chunk

    def test_on_reasoning_回调被传入假_LLM(self):
        _events()
        assert FakeLLMService.instances
        assert FakeLLMService.instances[0].seen_on_reasoning is not None
