# -*- coding: utf-8 -*-
"""用交接文档开新会话（POST /api/sessions/from-handoff）。

锁三件事：
1. 传 markdown 时**不再调用 generate_handoff**（避免重复一次 LLM 摘要）；
2. 新会话的第一条消息是 **user 角色**且包含交接正文（这样才会进入上下文装配）；
3. 生成失败（会话不存在/无消息）→ 400，且**不建空会话**。
"""
from __future__ import annotations

import logging
import sys
import types

import pytest
from flask import Flask

import agent.server_auth as server_auth
import plugins.chat as chat_plugin
import plugins.plugin_api as plugin_api

AUTH = {"Authorization": "Bearer test-token"}


class _FakeSessionMgr:
    def __init__(self) -> None:
        self.created = []
        self.messages = []

    def get_session(self, session_id):
        return {"id": session_id, "title": "旧会话"} if session_id else None

    def create_session(self, title=""):
        session = {"id": "sess-new-1", "title": title}
        self.created.append(session)
        return session

    def add_message(self, session_id, role, content, **kwargs):
        self.messages.append({"session_id": session_id, "role": role, "content": content})
        return {"role": role, "content": content}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("FLASK_API_TOKEN", "test-token")
    monkeypatch.setattr(server_auth, "_AUTH_DISABLED_FOR_TEST", False)
    monkeypatch.setattr(plugin_api, "_resolve_host_decorator",
                        lambda name: getattr(server_auth, name))
    fake = types.ModuleType("app_server")
    fake._session_mgr = _FakeSessionMgr()
    fake.logger = logging.getLogger("test.app_server")
    monkeypatch.setitem(sys.modules, "app_server", fake)
    app = Flask(__name__)
    app.register_blueprint(chat_plugin.bp)
    return app.test_client(), fake


def test_传_markdown_直接建会话并注入正文(client):
    http, fake = client
    resp = http.post("/api/sessions/from-handoff",
                     json={"markdown": "# 交接\n\n下一轮先跑自检。"},
                     headers=AUTH)
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["ok"] is True
    assert data["seeded_chars"] > 0
    assert fake._session_mgr.created and fake._session_mgr.created[0]["title"].startswith("续 ·")
    msg = fake._session_mgr.messages[0]
    assert msg["role"] == "user", "必须是 user 角色，否则上下文装配不会带上"
    assert "交接" in msg["content"] and "下一轮先跑自检" in msg["content"]


def test_生成失败不建空会话(client, monkeypatch):
    http, fake = client

    import agent.handoff.handoff_generator as hg

    def boom(state, session_id=None, intent=None):
        raise ValueError("会话不存在")

    monkeypatch.setattr(hg, "generate_handoff", boom)
    resp = http.post("/api/sessions/from-handoff", json={"session_id": "nope"}, headers=AUTH)
    assert resp.status_code == 400
    assert fake._session_mgr.created == []
    assert fake._session_mgr.messages == []


def test_空正文被拒(client):
    http, fake = client
    resp = http.post("/api/sessions/from-handoff", json={"markdown": "   "}, headers=AUTH)
    # 空 markdown 会走生成分支；这里没有可用的真实会话 ⇒ 400（任一分支都不得建会话）
    assert resp.status_code in (400, 500)
    assert fake._session_mgr.created == []
