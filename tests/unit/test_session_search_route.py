"""E1-4b `GET /api/session-search` 契约测试（含索引失败时的降级形状）。"""
from __future__ import annotations

import pytest

from agent.context import session_index as S


@pytest.fixture(autouse=True)
def _db(monkeypatch, tmp_path):
    monkeypatch.delenv(S.ENABLED_ENV, raising=False)
    monkeypatch.setenv("CP_SESSION_INDEX_PATH", str(tmp_path / "idx.db"))
    yield


@pytest.fixture()
def client():
    from flask import Flask
    from agent.server_routes.routes_session_search import register_routes

    app = Flask("session_search")
    app.config.update(TESTING=True)
    register_routes(app)
    return app.test_client()


def test_命中返回片段与会话(client):
    S.index_message("sess_a", "user", "帮我看看检查点与变更文件卡")
    data = client.get("/api/session-search?q=检查点").get_json()
    assert data["ok"] is True and data["mode"] == "match"
    assert len(data["hits"]) == 1
    assert data["hits"][0]["session_id"] == "sess_a"
    assert "检查点" in data["hits"][0]["snippet"]
    assert data["took_ms"] >= 0


def test_空查询返回空清单(client):
    data = client.get("/api/session-search?q=").get_json()
    assert data["ok"] is True and data["hits"] == [] and data["mode"] == "empty"


def test_短查询回落_like(client):
    S.index_message("sess_a", "user", "关于知识库的回答")
    data = client.get("/api/session-search?q=知识").get_json()
    assert data["mode"] == "like" and data["hits"]


def test_limit_参数被钳制(client):
    for i in range(60):
        S.index_message("sess_big", "user", "关键字内容 %d" % i)
    data = client.get("/api/session-search?q=关键字内容&limit=999").get_json()
    assert len(data["hits"]) == S.MAX_LIMIT


def test_索引关闭时_ok_false_且不抛(client, monkeypatch):
    monkeypatch.setenv(S.ENABLED_ENV, "0")
    data = client.get("/api/session-search?q=检查点").get_json()
    assert data["ok"] is False and data["hits"] == []
    assert "关闭" in data["error"]


def test_非法查询字符不会500(client):
    S.index_message("sess_a", "user", "检查点")
    data = client.get('/api/session-search?q="检查点"*(^)').get_json()
    assert data["ok"] is True and data["hits"]
