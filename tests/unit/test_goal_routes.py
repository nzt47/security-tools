"""E1-2d /api/goal/* 路由：读状态 / 暂停恢复清除 / 追加验收条件（2026-11-25）

与 /api/plan-mode/* 同款契约：GET 只读、POST 走令牌；扁平响应（无 {code,data} 信封）；
无目标时安全返回 idle，而不是 404/500。
"""
from __future__ import annotations

import pytest

from agent.context import goal as G


@pytest.fixture(autouse=True)
def _root(tmp_path):
    G.set_root(str(tmp_path / "goals"))
    yield
    G.set_root(None)


SID = "route-goal-1"


@pytest.fixture()
def client(monkeypatch):
    from flask import Flask
    import agent.server_auth as server_auth
    from agent.server_routes.routes_goal import register_routes

    monkeypatch.setattr(server_auth, "authorize_request", lambda: (True, "tester", "test"))
    app = Flask("goal_routes")
    app.config.update(TESTING=True)
    register_routes(app)
    return app.test_client()


class TestState:
    def test_无目标时_idle(self, client):
        r = client.get("/api/goal/state?session_id=%s" % SID)
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"] is True and data["state"] == "idle" and data["goal_id"] == ""

    def test_缺_session_400(self, client):
        assert client.get("/api/goal/state").status_code == 400

    def test_创建后读回标题与轮次(self, client):
        G.create(SID, "路由目标", acceptance=["a"])
        data = client.get("/api/goal/state?session_id=%s" % SID).get_json()
        assert data["title"] == "路由目标" and data["round"] == 0
        assert data["acceptance"] == ["a"]


class TestMode:
    def test_暂停_恢复_清除(self, client):
        G.create(SID, "目标")
        r = client.post("/api/goal/mode", json={"session_id": SID, "action": "pause",
                                                "reason": "等确认"})
        assert r.status_code == 200 and r.get_json()["state"] == "paused"
        r = client.post("/api/goal/mode", json={"session_id": SID, "action": "resume"})
        assert r.get_json()["state"] == "active"
        r = client.post("/api/goal/mode", json={"session_id": SID, "action": "clear"})
        assert r.get_json()["state"] == "idle"

    def test_非法_action_400(self, client):
        r = client.post("/api/goal/mode", json={"session_id": SID, "action": "destroy"})
        assert r.status_code == 400

    def test_无目标暂停_409(self, client):
        r = client.post("/api/goal/mode", json={"session_id": SID, "action": "pause"})
        assert r.status_code == 409
        assert r.get_json()["code"] == "no_goal"


class TestAccept:
    def test_追加验收条件(self, client):
        G.create(SID, "目标", acceptance=["a"])
        r = client.post("/api/goal/accept", json={"session_id": SID, "item": "b"})
        assert r.status_code == 200 and r.get_json()["acceptance"] == ["a", "b"]

    def test_空条目_409(self, client):
        G.create(SID, "目标")
        r = client.post("/api/goal/accept", json={"session_id": SID, "item": "  "})
        assert r.status_code == 409
        assert r.get_json()["code"] == "empty_item"

    def test_无目标_409(self, client):
        r = client.post("/api/goal/accept", json={"session_id": SID, "item": "x"})
        assert r.status_code == 409
