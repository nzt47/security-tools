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
        #: 最近一次 chat_stream 的实参（2026-10-02：用于钉死 max_tokens / messages 口径）
        self.seen_call: dict = {}
        FakeLLMService.instances.append(self)

    def chat_stream(self, messages, system_prompt="", max_tokens=1024, temperature=0.7,
                    on_tool_call=None, tools=None, on_reasoning=None):
        self.seen_on_reasoning = on_reasoning
        self.seen_call = {"messages": list(messages), "max_tokens": max_tokens,
                          "system_prompt": system_prompt, "tools": tools}
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

# ════════════════════════════════════════════════════════════════════════════
#  2026-10-02：工作台（主 UI）必须真正吃到"上下文最大 Token / 单次回复"两个旋钮
#
#  修复前本路径把 `max_tokens=2048` 写死、历史写死"最近 8 条" ——
#  面板上把窗口调到 131072、回复上限调到 16384，对**主 UI 的对话链路**毫无作用。
#  本类用例把它钉死：上限来自配置（同一份口径），历史按 token 预算裁。
# ════════════════════════════════════════════════════════════════════════════

class _CounterStub:
    def count(self, text: str) -> int:
        return len(text or "")


class _SessionMgrStub:
    def __init__(self, count: int, size: int):
        self._msgs = [{"role": "user" if i % 2 == 0 else "assistant",
                       "content": "x" * size} for i in range(count)]

    def get_messages(self, session_id, limit=50):
        return list(self._msgs) if limit <= 0 else list(self._msgs)[-limit:]


class _CfgStub:
    def __init__(self, **kw):
        self.values = dict(kw)

    def get(self, section, key, default=None):
        return self.values.get(key, default)


class _YunshuStub:
    def __init__(self, window: int, recv_limit: int = 0):
        self._memory_token_limit = window
        self._recv_limit = recv_limit

    def context_limit_info(self):
        if self._memory_token_limit <= 0:
            return {"limit_tokens": None, "limit_source": "unavailable"}
        return {"limit_tokens": self._memory_token_limit, "limit_source": "test"}

    def _resolve_max_output_tokens(self, model):
        from agent.chat_limits import resolve_max_output_tokens
        return resolve_max_output_tokens(self._recv_limit, model)


def _install_app_server(monkeypatch, *, window=131072, msg_count=0, msg_size=0, **cfg):
    """装一个桩 app_server（工作台流式路径会惰性 import 它取会话/配置/窗口）"""
    import sys
    import types

    fake = types.ModuleType("app_server")
    fake._session_mgr = _SessionMgrStub(msg_count, msg_size)
    fake._get_token_counter = lambda: _CounterStub()
    fake._cfg = _CfgStub(**cfg)
    fake._Yunshu = _YunshuStub(window, cfg.get("per_message_recv_limit", 0))
    fake.logger = types.SimpleNamespace(
        info=lambda *a, **k: None, warning=lambda *a, **k: None,
        error=lambda *a, **k: None, debug=lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, "app_server", fake)
    return fake


def _events_with_session(question="帮我看看云枢", session_id="sess-A") -> list[dict]:
    raw = list(chat_module._workbench_real_stream(question, session_id))
    out = []
    for block in raw:
        payload = block[len("data:"):].strip()
        if payload:
            out.append(json.loads(payload))
    return out


class TestWorkbenchHonoursContextKnobs:
    def test_单次回复上限来自配置而非写死的2048(self, monkeypatch):
        """回归锚：本路径曾把 max_tokens 写死 2048（模型的 1/192）"""
        _install_app_server(monkeypatch, window=131072, per_message_recv_limit=32768)
        _events_with_session()
        assert FakeLLMService.instances
        assert FakeLLMService.instances[0].seen_call["max_tokens"] == 32768

    def test_未配置时按模型档位兜底而不是2048(self, monkeypatch):
        """配置里没有该键 ⇒ 走共享规则：模型档位下限 8192（deepseek-v4-flash 非 pro 档）

        【为什么也要装桩】不装桩的话，本函数会去 import 真实的 app_server（重量级副作用），
        测试既慢又依赖"盘上那份 config" —— 那就不是单元测试了。
        """
        _install_app_server(monkeypatch, window=131072)  # 有意不带 per_message_recv_limit
        _events_with_session()
        assert FakeLLMService.instances[0].seen_call["max_tokens"] == 8192

    def test_历史不再固定8条(self, monkeypatch):
        """窗口够大 ⇒ 30 条历史应当**全部**带入（修复前恒为 8 条）"""
        _install_app_server(monkeypatch, window=131072, msg_count=30, msg_size=40)
        _events_with_session()
        sent = FakeLLMService.instances[0].seen_call["messages"]
        assert len(sent) > 8, f"历史仍被按条数截断：{len(sent)} 条"
        assert sent[-1]["content"].startswith("帮我看看云枢"), "末条必须是本轮用户输入"

    def test_窗口小_按token预算裁到最少保留条数(self, monkeypatch):
        """窗口极小 ⇒ 预算收敛到下限，且至少保留 2 条（能接上"上一句话"）"""
        _install_app_server(monkeypatch, window=1000, msg_count=30, msg_size=2000)
        _events_with_session()
        sent = FakeLLMService.instances[0].seen_call["messages"]
        assert len(sent) == 2, f"应至少保留 2 条（含本轮输入），实得 {len(sent)}"

    def test_窗口不可得时回落原行为8条而不是全量(self, monkeypatch):
        """审计 P2-2：窗口拿不到时若"原样返回全部"，prompt 会从 8 条涨到最多 400 条 ——
        那不是保持原行为，而是**静默放大**（成本与超窗风险都上去）"""
        _install_app_server(monkeypatch, window=0, msg_count=50, msg_size=40)
        _events_with_session()
        sent = FakeLLMService.instances[0].seen_call["messages"]
        assert len(sent) == 8, "窗口不可得 ⇒ 必须回落到改造前的 8 条上限，实得 %d" % len(sent)

    def test_上下文装配以_thinking_事件可见(self, monkeypatch):
        """此前"带了多少上下文"完全不可观测 —— 用户只能猜"为什么它忘了我刚说的" """
        _install_app_server(monkeypatch, window=131072, msg_count=5, msg_size=40)
        events = _events_with_session()
        budget_events = [e for e in events if e.get("id") == "context-budget"]
        assert budget_events, "未外发上下文装配事件"
        detail = budget_events[0]["detail"]
        assert "历史预算" in detail and "窗口" in detail
        assert budget_events[0]["status"] == "done"

    def test_单条消息超阈值只告警不截断(self, monkeypatch):
        """与 /api/chat 同一产品决定：告警可见、原文完整"""
        _install_app_server(monkeypatch, window=131072, per_message_send_limit=10)
        long_q = "这是一条明显超过阈值的用户消息" * 6
        events = _events_with_session(question=long_q)
        warn = [e for e in events if e.get("id") == "send-limit"]
        assert warn, "未外发单次发送告警事件"
        assert "未截断" in warn[0]["detail"] or "未截断" in str(warn[0])
        sent = FakeLLMService.instances[0].seen_call["messages"]
        assert sent[-1]["content"] == long_q, "原文被截断了（违反只告警不截断的契约）"

    def test_未超阈值时不发告警事件(self, monkeypatch):
        _install_app_server(monkeypatch, window=131072, per_message_send_limit=100000)
        events = _events_with_session()
        assert not [e for e in events if e.get("id") == "send-limit"]
