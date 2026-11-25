"""E1-3a /api/ask/* 路由：待答问题读取 + 回答（落台账 + 落用户消息 + next_message）。"""
from __future__ import annotations

import sys
import types

import pytest

from agent.context import ask as A


@pytest.fixture(autouse=True)
def _root(tmp_path):
    A.set_root(str(tmp_path / "ask"))
    yield
    A.set_root(None)


SID = "ask-route-1"


class _SessionMgr:
    def __init__(self):
        self.by_session = {}

    def add_message(self, sid, role, content, **kw):
        entry = {"role": role, "content": content}
        entry.update(kw)
        self.by_session.setdefault(sid, []).append(entry)
        return entry

    def get_messages(self, sid, limit=50):
        return list(self.by_session.get(sid, []))


@pytest.fixture()
def client(monkeypatch):
    from flask import Flask
    import agent.server_auth as server_auth
    from agent.server_routes.routes_ask import register_routes

    mgr = _SessionMgr()
    fake = types.ModuleType("app_server")
    fake._session_mgr = mgr
    fake.logger = types.SimpleNamespace(warning=lambda *a, **k: None,
                                        info=lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, "app_server", fake)
    monkeypatch.setattr(server_auth, "authorize_request", lambda: (True, "tester", "test"))
    app = Flask("ask_routes")
    app.config.update(TESTING=True)
    register_routes(app)
    return app.test_client(), mgr


class TestPending:
    def test_无待答时返回_null(self, client):
        c, _ = client
        r = c.get("/api/ask/pending?session_id=%s" % SID)
        assert r.status_code == 200
        assert r.get_json() == {"ok": True, "pending": None}

    def test_缺_session_400(self, client):
        c, _ = client
        assert c.get("/api/ask/pending").status_code == 400

    def test_创建后能读到(self, client):
        c, _ = client
        A.create_question(SID, "选哪个？", options=["A", "B"])
        doc = c.get("/api/ask/pending?session_id=%s" % SID).get_json()["pending"]
        assert doc["question"] == "选哪个？" and len(doc["options"]) == 2


class TestAnswer:
    def test_回答成功_带_next_message_并落用户消息(self, client):
        c, mgr = client
        doc = A.create_question(SID, "选哪个？", options=["A", "B"])
        r = c.post("/api/ask/answer", json={
            "session_id": SID, "question_id": doc["question_id"],
            "answers": ["o2"], "custom": "",
        })
        data = r.get_json()
        assert r.status_code == 200 and data["ok"] is True
        assert "选哪个？" in data["next_message"] and "o2" in data["next_message"]
        assert mgr.by_session[SID][-1]["role"] == "user"
        assert A.get_pending(SID) is None

    def test_没有待答问题_409(self, client):
        c, _ = client
        r = c.post("/api/ask/answer", json={"session_id": SID, "answers": ["o1"]})
        assert r.status_code == 409
        assert r.get_json()["code"] == "no_pending"

    def test_旧问题_id_409(self, client):
        c, _ = client
        old = A.create_question(SID, "旧")
        A.create_question(SID, "新")
        r = c.post("/api/ask/answer", json={
            "session_id": SID, "question_id": old["question_id"], "answers": ["o1"]})
        assert r.status_code == 409
        assert r.get_json()["code"] == "stale_question"

    def test_空回答_409(self, client):
        c, _ = client
        doc = A.create_question(SID, "选", options=["A"])
        r = c.post("/api/ask/answer", json={
            "session_id": SID, "question_id": doc["question_id"], "answers": [], "custom": ""})
        assert r.status_code == 409
        assert r.get_json()["code"] == "empty_answer"
