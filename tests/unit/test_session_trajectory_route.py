"""E4-5 Inspector 路由：GET /api/sessions/<sid>/trajectory（只读、默认关）。"""
from __future__ import annotations

import json
import sys
import types

import pytest
from flask import Flask

from agent.server_routes.routes_session_insight import register_routes

MSGS = [
    {"role": "user", "content": "帮我看检查点", "timestamp": "2026-11-28T10:00:00"},
    {"role": "assistant", "content": "好的", "timestamp": "2026-11-28T10:00:05",
     "steps": [{"id": "t1", "title": "工具调用：read_file",
                "detail": "结果: ok", "status": "done", "at": "1"}]},
    {"role": "user", "content": "继续", "timestamp": "2026-11-28T10:01:00"},
]


class _FakeMgr:
    def __init__(self, messages=None, boom=False):
        self.messages = list(MSGS if messages is None else messages)
        self.boom = boom
        self.calls = 0

    def get_session_metadata(self, session_id):
        self.calls += 1
        return {"title": "检查点", "message_count": len(self.messages)}

    def get_messages(self, session_id, limit=None):
        if self.boom:
            raise RuntimeError("SECRET-BOOM")
        return list(self.messages)


@pytest.fixture
def make_client(monkeypatch):
    def _make(mgr):
        monkeypatch.setitem(sys.modules, "app_server",
                           types.SimpleNamespace(_session_mgr=mgr))
        app = Flask(__name__)
        app.config.update(TESTING=True)
        register_routes(app)
        return app.test_client()

    return _make


class TestDisabledByDefault:
    def test_默认关_回_disabled_且不读会话(self, make_client, monkeypatch):
        monkeypatch.delenv("CP_INSPECTOR_ENABLED", raising=False)
        mgr = _FakeMgr()
        client = make_client(mgr)
        body = client.get("/api/sessions/s1/trajectory").get_json()
        assert body["ok"] is False and body["code"] == "disabled"
        assert mgr.calls == 0, "开关关时不应触碰会话管理器"

    def test_开_1_才可读(self, make_client, monkeypatch):
        monkeypatch.setenv("CP_INSPECTOR_ENABLED", "1")
        client = make_client(_FakeMgr())
        body = client.get("/api/sessions/s1/trajectory").get_json()
        assert body["ok"] is True


class TestShape:
    def test_回消息与步骤(self, make_client, monkeypatch):
        monkeypatch.setenv("CP_INSPECTOR_ENABLED", "true")
        client = make_client(_FakeMgr())
        body = client.get("/api/sessions/s1/trajectory").get_json()
        assert body["ok"] is True and body["session_id"] == "s1"
        assert body["title"] == "检查点" and body["total"] == 3
        assert [e["role"] for e in body["entries"]] == ["user", "assistant", "user"]
        step = body["entries"][1]["steps"][0]
        assert step["title"] == "工具调用：read_file" and step["detail"] == "结果: ok"

    def test_limit_offset_透传(self, make_client, monkeypatch):
        monkeypatch.setenv("CP_INSPECTOR_ENABLED", "1")
        client = make_client(_FakeMgr())
        body = client.get("/api/sessions/s1/trajectory?limit=1&offset=1").get_json()
        assert body["total"] == 3 and body["limit"] == 1 and body["offset"] == 1
        assert [e["index"] for e in body["entries"]] == [1]
        assert body["truncated"] is True

    def test_会话不存在_404(self, make_client, monkeypatch):
        monkeypatch.setenv("CP_INSPECTOR_ENABLED", "1")

        class _Missing(_FakeMgr):
            def get_session_metadata(self, session_id):
                return None

        client = make_client(_Missing())
        r = client.get("/api/sessions/nope/trajectory")
        assert r.status_code == 404 and r.get_json()["ok"] is False

    def test_异常不回原文(self, make_client, monkeypatch):
        monkeypatch.setenv("CP_INSPECTOR_ENABLED", "1")
        client = make_client(_FakeMgr(boom=True))
        r = client.get("/api/sessions/s1/trajectory")
        assert r.status_code == 500
        assert "SECRET-BOOM" not in json.dumps(r.get_json(), ensure_ascii=False)

    def test_默认_limit_50(self, make_client, monkeypatch):
        monkeypatch.setenv("CP_INSPECTOR_ENABLED", "1")
        client = make_client(_FakeMgr())
        body = client.get("/api/sessions/s1/trajectory").get_json()
        assert body["limit"] == 50 and body["offset"] == 0
