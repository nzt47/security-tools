"""E4-5 第六批：LiveShare 路由（公开只读读面 + 登录态管理面；默认关）。"""
from __future__ import annotations

import json
import sys
import types

import pytest
from flask import Flask

from agent import liveshare
from agent.server_routes import routes_liveshare as mod

MESSAGES = [
    {"role": "user", "content": "第一问", "timestamp": "2026-11-28T10:00:00Z",
     "tool_calls": [{"id": "x"}]},
    {"role": "assistant", "content": "第一答", "timestamp": "2026-11-28T10:00:05Z",
     "steps": [{"id": "s1", "detail": "内部步骤"}]},
    {"role": "user", "content": "第二问", "timestamp": "2026-11-28T10:01:00Z"},
]


class _FakeMgr:
    def __init__(self, messages=None, title="分享会话", exists=True, boom=False):
        self.messages = list(MESSAGES if messages is None else messages)
        self.title = title
        self.exists = exists
        self.boom = boom

    def get_session(self, session_id):
        return {"id": session_id} if self.exists else None

    def get_session_metadata(self, session_id):
        return {"title": self.title} if self.exists else None

    def get_messages(self, session_id, limit=None):
        if self.boom:
            raise RuntimeError("SECRET-BOOM")
        return list(self.messages)


@pytest.fixture
def make_client(monkeypatch, tmp_path):
    def _make(mgr=None):
        fake = mgr or _FakeMgr()
        monkeypatch.setitem(sys.modules, "app_server",
                            types.SimpleNamespace(_session_mgr=fake))
        monkeypatch.setattr(liveshare, "SHARES_DIR", tmp_path / "liveshare")
        monkeypatch.setattr(mod, "_mgr", lambda: fake)
        mod.reset_rate_limit_for_tests()
        app = Flask(__name__)
        app.config.update(TESTING=True)
        mod.register_routes(app)
        return app.test_client(), fake

    return _make


def _create(client, session_id="sess_share1", **body):
    resp = client.post("/api/sessions/%s/share" % session_id, json=body or {})
    return resp


class TestGate:
    def test_默认关_公开面404_管理面disabled(self, make_client, monkeypatch):
        monkeypatch.delenv(mod.ENABLED_ENV, raising=False)
        client, _mgr = make_client()
        public = client.get("/api/liveshare/messages")
        assert public.status_code == 404 and public.get_json()["code"] == "not_found"
        manage = client.get("/api/sessions/sess_share1/share")
        body = manage.get_json()
        assert body["ok"] is False and body["code"] == "disabled"

    def test_开1才可用(self, make_client, monkeypatch):
        monkeypatch.setenv(mod.ENABLED_ENV, "1")
        client, _mgr = make_client()
        assert _create(client).get_json()["ok"] is True


class TestManage:
    def test_建分享_明文只回一次(self, make_client, monkeypatch, tmp_path):
        monkeypatch.setenv(mod.ENABLED_ENV, "1")
        client, _mgr = make_client()
        body = _create(client).get_json()
        token = body["token"]
        assert body["ok"] is True and len(token) >= 32
        assert body["url_path"] == "/#/live/" + token
        assert body["expires_at"]
        raw = (tmp_path / "liveshare" / liveshare.SHARES_FILE).read_text(encoding="utf-8")
        assert token not in raw and liveshare.token_digest(token) in raw

    def test_ttl_可覆盖且被夹取(self, make_client, monkeypatch):
        monkeypatch.setenv(mod.ENABLED_ENV, "1")
        client, _mgr = make_client()
        body = _create(client, ttl_seconds=600).get_json()
        assert body["ttl_seconds"] == 600
        body2 = _create(client, ttl_seconds=1).get_json()
        assert body2["ttl_seconds"] == liveshare.MIN_TTL_SECONDS

    def test_状态不含令牌(self, make_client, monkeypatch):
        monkeypatch.setenv(mod.ENABLED_ENV, "1")
        client, _mgr = make_client()
        _create(client)
        body = client.get("/api/sessions/sess_share1/share").get_json()
        assert body["ok"] is True and len(body["shares"]) == 1
        assert body["shares"][0]["active"] is True
        assert "token" not in json.dumps(body) and "sha256" not in json.dumps(body)

    def test_撤销后公开面立即404(self, make_client, monkeypatch):
        monkeypatch.setenv(mod.ENABLED_ENV, "1")
        client, _mgr = make_client()
        token = _create(client).get_json()["token"]
        assert client.get("/api/liveshare/messages",
                          headers={mod.TOKEN_HEADER: token}).status_code == 200
        assert client.delete("/api/sessions/sess_share1/share").get_json()["revoked"] == 1
        assert client.get("/api/liveshare/messages",
                          headers={mod.TOKEN_HEADER: token}).status_code == 404

    def test_会话不存在_404(self, make_client, monkeypatch):
        monkeypatch.setenv(mod.ENABLED_ENV, "1")
        client, _mgr = make_client(_FakeMgr(exists=False))
        assert _create(client).status_code == 404


class TestPublicRead:
    def test_读面字段白名单与分页(self, make_client, monkeypatch):
        monkeypatch.setenv(mod.ENABLED_ENV, "1")
        client, _mgr = make_client()
        token = _create(client).get_json()["token"]
        body = client.get("/api/liveshare/messages?after=0&limit=2",
                          headers={mod.TOKEN_HEADER: token}).get_json()
        assert body["ok"] is True and body["title"] == "分享会话"
        assert [row["seq"] for row in body["messages"]] == [1, 2]
        assert body["total"] == 3 and body["truncated"] is True
        dumped = json.dumps(body, ensure_ascii=False)
        assert "内部步骤" not in dumped and "tool_calls" not in dumped

        page2 = client.get("/api/liveshare/messages?after=2",
                           headers={mod.TOKEN_HEADER: token}).get_json()
        assert [row["seq"] for row in page2["messages"]] == [3]
        assert page2["truncated"] is False

    def test_无效或缺失令牌_同形404(self, make_client, monkeypatch):
        monkeypatch.setenv(mod.ENABLED_ENV, "1")
        client, _mgr = make_client()
        missing = client.get("/api/liveshare/messages")
        wrong = client.get("/api/liveshare/messages", headers={mod.TOKEN_HEADER: "x" * 40})
        assert missing.status_code == wrong.status_code == 404
        assert missing.get_json() == wrong.get_json()

    def test_会话被删_404(self, make_client, monkeypatch):
        monkeypatch.setenv(mod.ENABLED_ENV, "1")
        client, mgr = make_client()
        token = _create(client).get_json()["token"]
        mgr.exists = False
        assert client.get("/api/liveshare/messages",
                          headers={mod.TOKEN_HEADER: token}).status_code == 404

    def test_读失败_500不回原文(self, make_client, monkeypatch):
        monkeypatch.setenv(mod.ENABLED_ENV, "1")
        client, _mgr = make_client(_FakeMgr(boom=True))
        token = _create(client).get_json()["token"]
        resp = client.get("/api/liveshare/messages", headers={mod.TOKEN_HEADER: token})
        assert resp.status_code == 500
        assert "SECRET-BOOM" not in json.dumps(resp.get_json(), ensure_ascii=False)

    def test_限流429(self, make_client, monkeypatch):
        monkeypatch.setenv(mod.ENABLED_ENV, "1")
        monkeypatch.setattr(mod, "RATE_MAX_REQUESTS", 2)
        client, _mgr = make_client()
        token = _create(client).get_json()["token"]
        headers = {mod.TOKEN_HEADER: token}
        assert client.get("/api/liveshare/messages", headers=headers).status_code == 200
        assert client.get("/api/liveshare/messages", headers=headers).status_code == 200
        assert client.get("/api/liveshare/messages", headers=headers).status_code == 429
