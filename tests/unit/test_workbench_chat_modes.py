"""工作台「对话模式」三档（plain / retrieval / full）的路由与行为（2026-10-02）

三档是**取舍**而不是"越好越好"，所以由用户在工具栏自己选（默认 plain = 引入开关前的行为）：
  · plain      真流式、1 次模型调用、不查记忆/知识库；
  · retrieval  在 plain 之上注入检索片段（走编排器的同一份 ContextAssembler 实现）；
  · full       委托编排器全链路（意图分层 + 检索 + 规划 + 工具）——**非流式**、可能多轮调用。

本文件钉死：模式如何选路、非法值如何回落、检索是否真的进了 system prompt、
以及 full 模式**不得重复写记忆**（编排器自己会写）。
"""
from __future__ import annotations

import sys
import types

import pytest

import memory.llm_service as llm_module
import plugins.chat as chat_module


class FakeLLM:
    instances: list = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.seen = {}
        FakeLLM.instances.append(self)

    def chat_stream(self, messages, system_prompt="", max_tokens=1024, temperature=0.7,
                    on_tool_call=None, tools=None, on_reasoning=None):
        self.seen = {"messages": list(messages), "system_prompt": system_prompt,
                     "max_tokens": max_tokens}
        yield "轻量"
        yield "回答"


class _Counter:
    def count(self, text):
        return len(text or "")


class _Memory:
    def __init__(self):
        self.writes = []

    def add_message(self, role, content):
        self.writes.append((role, content))
        return "ts"


class _SessionMgr:
    def __init__(self):
        self.by_session = {}

    def get_session(self, sid):
        return self.by_session.get(sid)

    def create_session(self, session_id=None, title=""):
        self.by_session[session_id] = {"id": session_id, "messages": []}
        return {"id": session_id}

    def get_messages(self, sid, limit=50):
        msgs = list(self.by_session.get(sid, {}).get("messages", []))
        return msgs if limit <= 0 else msgs[-limit:]

    def add_message(self, sid, role, content, **kw):
        entry = {"role": role, "content": content}
        entry.update(kw)
        self.by_session.setdefault(sid, {"id": sid}).setdefault("messages", []).append(entry)
        return entry


class _Yunshu:
    """桩编排器：检索返回固定文本；chat() 记录入参并返回固定回答"""

    def __init__(self, extra="", answer="完整链路回答", chat_raises=None):
        self._extra = extra
        self._answer = answer
        self._chat_raises = chat_raises
        self._memory = _Memory()
        self.chat_calls = []
        self.turn_state = {}

    def _context_assembler_extra(self, question, mode="default"):
        self.last_extra_call = (question, mode)
        return self._extra

    def chat(self, question, *, session_id=None, session_mgr=None):
        self.chat_calls.append({"question": question, "session_id": session_id,
                                "session_mgr": session_mgr})
        if self._chat_raises:
            raise self._chat_raises
        return self._answer

    def last_response_metadata(self, session_id=None):
        return {}

    def _set_turn_state(self, session_id=None, **kw):
        self.turn_state = {"session_id": session_id, **kw}


@pytest.fixture()
def env(monkeypatch):
    from flask import Flask

    from plugins.chat import bp

    FakeLLM.instances = []
    monkeypatch.setattr(llm_module, "LLMService", lambda **kw: FakeLLM(**kw), raising=True)
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_API_KEY", "sk-" + "x" * 40)
    monkeypatch.setenv("LLM_MODEL", "deepseek-flash")

    def _make(yunshu):
        fake = types.ModuleType("app_server")
        fake._Yunshu = yunshu
        fake._session_mgr = _SessionMgr()
        fake._get_current_session_id = lambda: "sess-x"
        fake._get_token_counter = lambda: _Counter()
        fake._cfg = types.SimpleNamespace(get=lambda s, k, default=None: default)
        fake.logger = types.SimpleNamespace(
            info=lambda *a, **k: None, warning=lambda *a, **k: None,
            error=lambda *a, **k: None, debug=lambda *a, **k: None)
        monkeypatch.setitem(sys.modules, "app_server", fake)
        app = Flask("wb_modes")
        app.config.update(TESTING=True)
        app.register_blueprint(bp)
        return app.test_client(), fake

    return _make


def _body(resp) -> str:
    return b"".join(resp.response).decode("utf-8", errors="replace")


def _events(body: str) -> list:
    import json
    out = []
    for line in body.splitlines():
        if line.startswith("data:"):
            payload = line[len("data:"):].strip()
            if payload:
                out.append(json.loads(payload))
    return out


class TestPlainMode:
    def test_缺省即轻量_且是真流式(self, env):
        client, _fake = env(_Yunshu())
        body = _body(client.post("/api/chat/stream",
                                 json={"message": "你好", "session_id": "s1"}))
        assert FakeLLM.instances, "轻量模式应当调用模型（真流式）"
        assert [e["type"] for e in _events(body) if e["type"] == "chunk"] == ["chunk", "chunk"]
        # 默认路径不得出现检索/完整链路阶段
        ids = {e.get("id") for e in _events(body) if e["type"] == "thinking"}
        assert "retrieval" not in ids and "full-pipeline" not in ids

    def test_未知模式回落为轻量且不报错(self, env):
        client, _fake = env(_Yunshu())
        resp = client.post("/api/chat/stream",
                           json={"message": "你好", "session_id": "s1", "mode": "随便写的"})
        assert resp.status_code == 200
        assert FakeLLM.instances, "回落轻量后仍应正常调用模型"


class TestRetrievalMode:
    def test_命中时注入_system_prompt_并外发真实阶段事件(self, env):
        y = _Yunshu(extra="【长期记忆】云枢的技能审核阈值默认 60/70/50/60。")
        client, _fake = env(y)
        body = _body(client.post("/api/chat/stream",
                                 json={"message": "审核阈值是多少", "session_id": "s1",
                                       "mode": "retrieval"}))
        # 用"包含"而不是"结尾"：请求前还有一次工具一致性守卫（align_system_prompt_with_tools）
        # 会在 system prompt 末尾补工具声明 —— 检索片段仍必须真的在里面
        assert "【长期记忆】云枢的技能审核阈值默认 60/70/50/60。" in \
            FakeLLM.instances[0].seen["system_prompt"], "检索片段必须真的进 system prompt"
        ev = [e for e in _events(body) if e.get("id") == "retrieval"]
        assert ev and ev[0]["status"] == "done"
        assert "命中" in ev[0]["detail"] and "token" in ev[0]["detail"]
        # 走的是编排器的同一份实现（不是工作台自己写的检索）
        assert y.last_extra_call[0] == "审核阈值是多少"

    def test_未命中时如实说明且不改变_system_prompt(self, env):
        y = _Yunshu(extra="")
        client, _fake = env(y)
        before = None
        body = _body(client.post("/api/chat/stream",
                                 json={"message": "你好", "session_id": "s1",
                                       "mode": "retrieval"}))
        ev = [e for e in _events(body) if e.get("id") == "retrieval"]
        assert ev and "未命中" in ev[0]["detail"]
        before = FakeLLM.instances[0].seen["system_prompt"]
        assert "【长期记忆】" not in before

    def test_检索异常按轻量继续(self, env):
        class Boom(_Yunshu):
            def _context_assembler_extra(self, question, mode="default"):
                raise RuntimeError("检索炸了")

        client, _fake = env(Boom())
        resp = client.post("/api/chat/stream",
                           json={"message": "你好", "session_id": "s1", "mode": "retrieval"})
        assert resp.status_code == 200
        assert FakeLLM.instances, "检索失败必须回落轻量，而不是让对话失败"


class TestFullMode:
    def test_委托编排器并分段外发(self, env):
        y = _Yunshu(answer="完整模式的一段较长回答，用于验证分段外发。")
        client, fake = env(y)
        body = _body(client.post("/api/chat/stream",
                                 json={"message": "复杂任务", "session_id": "s1",
                                       "mode": "full"}))
        assert y.chat_calls and y.chat_calls[0]["question"] == "复杂任务"
        assert y.chat_calls[0]["session_id"] == "s1"
        assert y.chat_calls[0]["session_mgr"] is fake._session_mgr
        text = "".join(e["text"] for e in _events(body) if e["type"] == "chunk")
        assert text == "完整模式的一段较长回答，用于验证分段外发。"
        assert not FakeLLM.instances, "完整模式不得再走工作台自己的流式模型调用"

    def test_不覆盖编排器的本轮状态(self, env):
        """审计 P1-3：工作台若再用空值调 _set_turn_state，会**抹掉**编排器刚写的真实值
        （该接口规定"显式传 None 必须真实落 None、禁止回退"）"""
        y = _Yunshu(answer="完整回答")
        client, _fake = env(y)
        _body(client.post("/api/chat/stream",
                          json={"message": "复杂任务", "session_id": "s1", "mode": "full"}))
        assert y.turn_state == {}, "完整模式下工作台不得写本轮状态（权威写入方是编排器）"

    def test_不重复写记忆_但会话照落盘(self, env):
        """编排器 process() 自己会写记忆；工作台再写一次就是同一轮记两遍"""
        y = _Yunshu(answer="完整回答")
        client, fake = env(y)
        _body(client.post("/api/chat/stream",
                          json={"message": "复杂任务", "session_id": "s1", "mode": "full"}))
        assert y._memory.writes == [], "完整模式下工作台不得写记忆"
        rows = fake._session_mgr.get_messages("s1", limit=0)
        assert [r["role"] for r in rows] == ["user", "assistant"]

    def test_编排器不可用_明确告知而不是空回答(self, env):
        client, fake = env(_Yunshu())
        fake._Yunshu = None
        body = _body(client.post("/api/chat/stream",
                                 json={"message": "复杂任务", "session_id": "s1",
                                       "mode": "full"}))
        events = _events(body)
        assert any(e.get("status") == "error" for e in events if e["type"] == "thinking")
        text = "".join(e["text"] for e in events if e["type"] == "chunk")
        assert "不可用" in text
        assert events[-1]["type"] == "done"

    def test_编排器抛异常_明确回显原因(self, env):
        client, _fake = env(_Yunshu(chat_raises=RuntimeError("规划超时")))
        body = _body(client.post("/api/chat/stream",
                                 json={"message": "复杂任务", "session_id": "s1",
                                       "mode": "full"}))
        events = _events(body)
        text = "".join(e["text"] for e in events if e["type"] == "chunk")
        assert "规划超时" in text
        assert events[-1]["type"] == "done"
