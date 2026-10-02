"""工作台流式路径与编排器路径的**侧效应对齐**（2026-10-02）

背景：工作台（主 UI）走 `/api/chat/stream`，而它此前**只写会话存储**：
  · 不写 `_memory` ⇒ 长期记忆 / 压缩 / 召回只反映另一半对话（`/api/chat` 那条）；
  · 不写 `turn_state` ⇒ `/api/status` 之类的消费方在同一会话里读到的东西与 /api/chat 不同。

本文件把两条路径的侧效应口径钉死，并特别强调**只在正常收尾时写记忆**：
客户端中途断开时回复是残缺的，写进长期记忆会污染后续召回。
"""
from __future__ import annotations

import sys
import types

import pytest

import memory.llm_service as llm_module
import plugins.chat as chat_module  # noqa: F401  （保证插件模块已导入）


class FakeLLM:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.seen = {}

    def chat_stream(self, messages, system_prompt="", max_tokens=1024, temperature=0.7,
                    on_tool_call=None, tools=None, on_reasoning=None):
        self.seen = {"messages": list(messages), "max_tokens": max_tokens}
        if on_reasoning is not None:
            on_reasoning("先想一下。")
        yield "云枢"
        yield "回答"


class _Counter:
    def count(self, text):
        return len(text or "")


class _Memory:
    def __init__(self, boom=False):
        self.writes = []
        self._boom = boom

    def add_message(self, role, content):
        if self._boom:
            raise RuntimeError("记忆写盘失败（模拟）")
        self.writes.append((role, content))
        return "ts-%d" % len(self.writes)


class _Yunshu:
    def __init__(self, memory=None, with_setter=True):
        self._memory = memory if memory is not None else _Memory()
        self.turn_state = {}
        if with_setter:
            self._set_turn_state = self._record_turn_state

    def _record_turn_state(self, session_id=None, **kw):
        self.turn_state = {"session_id": session_id, **kw}


class _SessionMgr:
    def __init__(self):
        self.by_session = {}

    def get_session(self, sid):
        return self.by_session.get(sid)

    def create_session(self, session_id=None, title=""):
        self.by_session[session_id] = {"id": session_id, "message_count": 0,
                                       "messages": []}
        return {"id": session_id}

    def get_messages(self, sid, limit=50):
        msgs = list(self.by_session.get(sid, {}).get("messages", []))
        return msgs if limit <= 0 else msgs[-limit:]

    def add_message(self, sid, role, content, **kw):
        entry = {"role": role, "content": content}
        entry.update(kw)
        slot = self.by_session.setdefault(sid, {"id": sid, "message_count": 0})
        slot.setdefault("messages", []).append(entry)
        return entry


@pytest.fixture()
def env(monkeypatch):
    """真 Flask test client + 桩 app_server（_Yunshu / _memory / _session_mgr）"""
    from flask import Flask

    from plugins.chat import bp

    def _factory(**kw):
        kw.setdefault("model", "deepseek-flash")
        return FakeLLM(**kw)

    monkeypatch.setattr(llm_module, "LLMService", _factory, raising=True)
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_API_KEY", "sk-" + "x" * 40)
    monkeypatch.setenv("LLM_MODEL", "deepseek-flash")

    memory = _Memory()
    yunshu = _Yunshu(memory)
    sessions = _SessionMgr()
    fake = types.ModuleType("app_server")
    fake._Yunshu = yunshu
    fake._session_mgr = sessions
    fake._get_current_session_id = lambda: "sess-x"
    fake._get_token_counter = lambda: _Counter()
    fake._cfg = types.SimpleNamespace(get=lambda s, k, default=None: default)
    fake.logger = types.SimpleNamespace(
        info=lambda *a, **k: None, warning=lambda *a, **k: None,
        error=lambda *a, **k: None, debug=lambda *a, **k: None)
    fake.require_token = lambda f: f
    fake.log_request = lambda *a, **k: (lambda f: f)
    monkeypatch.setitem(sys.modules, "app_server", fake)

    app = Flask("wb_parity")
    app.config.update(TESTING=True)
    app.register_blueprint(bp)
    return app.test_client(), memory, yunshu, sessions


def _drain(resp):
    return b"".join(resp.response).decode("utf-8", errors="replace")


class TestNormalCompletion:
    def test_正常收尾_写入记忆与会话(self, env):
        client, memory, yunshu, sessions = env
        resp = client.post("/api/chat/stream",
                           json={"message": "你好", "session_id": "sess-1"})
        assert resp.status_code == 200
        body = _drain(resp)
        assert "云枢" in body and "回答" in body

        # ① 记忆：与 /api/chat 同口径（user + assistant，顺序一致）
        assert [r for r, _ in memory.writes] == ["user", "assistant"]
        assert memory.writes[0][1] == "你好"
        assert memory.writes[1][1] == "云枢回答"
        # ② 会话存储照旧（既有行为）
        rows = sessions.get_messages("sess-1", limit=0)
        assert [r["role"] for r in rows] == ["user", "assistant"]

    def test_本轮状态写入_且只带真实工具步骤(self, env):
        client, _mem, yunshu, _sessions = env
        _drain(client.post("/api/chat/stream",
                           json={"message": "你好", "session_id": "sess-1"}))
        st = yunshu.turn_state
        assert st.get("session_id") == "sess-1"
        # 推理内容来自真实的 reasoning 事件（不是拟态文案）
        assert st.get("reasoning") == "先想一下。"
        # 本轮没有真工具调用 ⇒ 不得凭空塞工具步骤（拟态阶段已删除）
        assert st.get("tool_steps") == []


class TestClientDisconnect:
    def test_中途断开_不写记忆但保留部分回复(self, env):
        """残缺回复进长期记忆会污染召回，故只落会话存储"""
        client, memory, _yunshu, _sessions = env
        resp = client.post("/api/chat/stream",
                           json={"message": "你好", "session_id": "sess-2"},
                           buffered=False)
        # 只读一个事件就断开（模拟前端点"停止生成"/关标签页）
        next(resp.response)
        resp.close()

        assert memory.writes == [], "断流时不得写入长期记忆"


class TestFailSoft:
    def test_记忆写入失败不影响回复(self, env):
        client, _mem, yunshu, _sessions = env
        yunshu._memory = _Memory(boom=True)  # add_message 抛异常
        body = _drain(client.post("/api/chat/stream",
                                  json={"message": "你好", "session_id": "sess-3"}))
        assert "云枢" in body and "回答" in body, "记忆写失败不得打断已经生成的内容"
        assert '"type": "done"' in body

    def test_没有_Yunshu_时静默返回(self, env):
        client, _mem, _yunshu, _sessions = env
        sys.modules["app_server"]._Yunshu = None
        body = _drain(client.post("/api/chat/stream",
                                  json={"message": "你好", "session_id": "sess-4"}))
        assert "云枢" in body

    def test_编排器没有_set_turn_state_也不炸(self, env):
        client, _mem, _yunshu, _sessions = env
        sys.modules["app_server"]._Yunshu = _Yunshu(_Memory(), with_setter=False)
        body = _drain(client.post("/api/chat/stream",
                                  json={"message": "你好", "session_id": "sess-5"}))
        assert "云枢" in body
        assert sys.modules["app_server"]._Yunshu._memory.writes, "记忆仍应写入"
