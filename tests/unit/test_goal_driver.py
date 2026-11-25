"""E1-2c round driver：目标轮次 + 自动续跑事件（2026-11-25）

钉死设计页 §4：
  · 一轮收尾时 active 目标轮次 +1，且 ``goal`` 事件（带 auto_continue/next_prompt）
    仍排在 ``done`` **之前**；
  · 同 turn_id 幂等（由 goal.record_round 保证，这里验证只 +1）；
  · 到 max_rounds ⇒ 写 budget 阻塞、**不再**续跑；
  · paused/completed 目标不计轮次、也不续跑；无目标不产生事件。
"""
from __future__ import annotations

import json
import sys
import types

import pytest

import memory.llm_service as llm_module
import plugins.chat as chat_module
from agent.context import goal as G


class FakeLLM:
    instances: list = []

    def __init__(self, **kwargs):
        FakeLLM.instances.append(self)

    def chat_stream(self, messages, system_prompt="", max_tokens=1024, temperature=0.7,
                    on_tool_call=None, tools=None, on_reasoning=None):
        yield "推进"
        yield "一轮"


class _Counter:
    def count(self, text):
        return len(text or "")


class _SessionMgr:
    def __init__(self):
        self.by_session = {}

    def get_session(self, sid):
        return self.by_session.get(sid)

    def create_session(self, session_id=None, title=""):
        self.by_session[session_id] = {"id": session_id, "messages": []}
        return {"id": session_id}

    def get_messages(self, sid, limit=50):
        return list(self.by_session.get(sid, {}).get("messages", []))

    def add_message(self, sid, role, content, **kw):
        entry = {"role": role, "content": content}
        entry.update(kw)
        self.by_session.setdefault(sid, {"id": sid}).setdefault("messages", []).append(entry)
        return entry

    def set_current(self, sid):
        return True


@pytest.fixture(autouse=True)
def _root(tmp_path):
    G.set_root(str(tmp_path / "goals"))
    yield
    G.set_root(None)


@pytest.fixture()
def client(monkeypatch):
    from flask import Flask
    from plugins.chat import bp

    FakeLLM.instances = []
    monkeypatch.setattr(llm_module, "LLMService", lambda **kw: FakeLLM(**kw), raising=True)
    fake = types.ModuleType("app_server")
    fake._Yunshu = types.SimpleNamespace()
    fake._session_mgr = _SessionMgr()
    fake._get_current_session_id = lambda: "s1"
    fake._get_token_counter = lambda: _Counter()
    fake._cfg = types.SimpleNamespace(get=lambda s, k, default=None: default)
    fake.logger = types.SimpleNamespace(
        info=lambda *a, **k: None, warning=lambda *a, **k: None,
        error=lambda *a, **k: None, debug=lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, "app_server", fake)
    app = Flask("goal_driver")
    app.config.update(TESTING=True)
    app.register_blueprint(bp)
    return app.test_client()


def _events(resp):
    body = b"".join(resp.response).decode("utf-8", "replace")
    out = []
    for line in body.splitlines():
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload:
                out.append(json.loads(payload))
    return out


def _turn(client, message="继续做"):
    return _events(client.post("/api/chat/stream",
                               json={"message": message, "session_id": "s1"}))


class TestRoundDriver:
    def test_active_目标收尾记轮次并续跑(self, client):
        G.create("s1", "把驱动做完", acceptance=["一轮一事件"])
        events = _turn(client)
        kinds = [e["type"] for e in events]
        assert "goal" in kinds and "done" in kinds
        assert kinds.index("goal") < kinds.index("done"), "goal 事件必须先于 done"
        payload = [e for e in events if e["type"] == "goal"][-1]["goal"]
        assert payload["auto_continue"] is True
        assert payload["round"] == 1
        assert payload["title"] == "把驱动做完"
        assert "把驱动做完" in payload["next_prompt"]

    def test_只加一轮_同_turn_不重复(self, client):
        G.create("s1", "幂等目标")
        _turn(client)
        # 再发一轮：新 turn_id ⇒ 第二轮
        events = _turn(client)
        payload = [e for e in events if e["type"] == "goal"][-1]["goal"]
        assert payload["round"] == 2

    def test_到上限写_budget_阻塞且不续跑(self, client):
        G.create("s1", "短目标", max_rounds=1)
        events = _turn(client)
        payload = [e for e in events if e["type"] == "goal"][-1]["goal"]
        assert payload["state"] == "blocked"
        assert "budget" in (payload.get("blocked") or {}).get("reason", "")
        assert payload.get("auto_continue") is not True
        assert G.get_state("s1")["state"] == "blocked"

    def test_暂停目标不计轮次不续跑(self, client):
        G.create("s1", "暂停目标")
        G.pause("s1", "等用户")
        events = _turn(client)
        payload = [e for e in events if e["type"] == "goal"][-1]["goal"]
        assert payload["state"] == "paused"
        assert payload["round"] == 0
        assert payload.get("auto_continue") is not True

    def test_无目标不产生_goal_事件(self, client):
        events = _turn(client)
        assert "goal" not in [e["type"] for e in events]
