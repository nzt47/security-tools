"""E2-2 Team 任务板 HTTP 面测试（/api/subagent/board*）

锁死：
  1. 读面形状 {ok, records, count, counts, total}，且静态路径不被 /api/subagent/<name> 抢走；
  2. claim 200；他人已认领 ⇒ 409 + code=task_claimed；force=true 可抢占；
  3. release 清空 owner；非持有者 ⇒ 409；
  4. handoff 缺 to ⇒ 400；正常交接 ⇒ 200 且 owner=to；
  5. CP_TEAM_BOARD_ENABLED=0 ⇒ 四条路由都 404；
  6. 真实入口（import app_server）包含四条规则。
"""
from __future__ import annotations

import pytest
from flask import Flask

from agent.server_routes.routes_subagent import register_routes
from agent.subagent.task_board import task_board


class _FakeYunshu:
    _subagent_mgr = None
    _llm = None

    def list_subagents(self):
        return []


@pytest.fixture()
def make_client(tmp_path, monkeypatch):
    board = task_board
    old_path = board.path
    path = str(tmp_path / "board.jsonl")
    board._path = path
    monkeypatch.delenv("CP_TEAM_BOARD_ENABLED", raising=False)
    board.record({"task_id": "t-1", "status": "pending", "goal": "把设计稿拆成步骤",
                  "subagent": "alpha", "source": "tool"})
    board.record({"task_id": "t-2", "status": "done", "goal": "另一件事",
                  "subagent": "beta", "source": "fan_out"})

    def _make():
        state = type("S", (), {"Yunshu": _FakeYunshu()})()
        app = Flask(__name__)
        app.config.update(TESTING=True)
        register_routes(app, state)
        return app.test_client()
    yield _make
    board._path = old_path


class TestBoardRead:
    def test_读面形状与分组(self, make_client):
        client = make_client()
        r = client.get("/api/subagent/board")
        assert r.status_code == 200, r.get_data(as_text=True)
        body = r.get_json()
        assert body["ok"] is True and body["count"] == 2
        assert body["counts"]["pending"] == 1 and body["counts"]["done"] == 1
        assert "分身不存在" not in str(body), "静态路由被 /<name> 抢走"

    def test_按状态过滤与_limit(self, make_client):
        client = make_client()
        body = client.get("/api/subagent/board?status=done&limit=5").get_json()
        assert [r["task_id"] for r in body["records"]] == ["t-2"]


class TestClaimRelease:
    def test_认领成功_重复他人认领_409_force_抢占(self, make_client):
        client = make_client()
        ok = client.post("/api/subagent/board/claim", json={"task_id": "t-1", "owner": "alice"})
        assert ok.status_code == 200 and ok.get_json()["task"]["owner"] == "alice"
        conflict = client.post("/api/subagent/board/claim", json={"task_id": "t-1", "owner": "bob"})
        assert conflict.status_code == 409
        assert conflict.get_json()["code"] == "task_claimed"
        forced = client.post("/api/subagent/board/claim",
                             json={"task_id": "t-1", "owner": "ops", "force": True})
        assert forced.status_code == 200 and forced.get_json()["task"]["owner"] == "ops"

    def test_认领缺_owner_400_任务不存在_404(self, make_client):
        client = make_client()
        assert client.post("/api/subagent/board/claim",
                           json={"task_id": "t-1"}).status_code == 400
        assert client.post("/api/subagent/board/claim",
                           json={"task_id": "t-nope", "owner": "a"}).status_code == 404

    def test_释放(self, make_client):
        client = make_client()
        client.post("/api/subagent/board/claim", json={"task_id": "t-1", "owner": "alice"})
        wrong = client.post("/api/subagent/board/release",
                            json={"task_id": "t-1", "owner": "bob"})
        assert wrong.status_code == 409
        ok = client.post("/api/subagent/board/release",
                         json={"task_id": "t-1", "owner": "alice"})
        assert ok.status_code == 200 and ok.get_json()["task"]["owner"] == ""


class TestHandoff:
    def test_交接(self, make_client):
        client = make_client()
        client.post("/api/subagent/board/claim", json={"task_id": "t-1", "owner": "alice"})
        r = client.post("/api/subagent/board/handoff",
                        json={"task_id": "t-1", "to": "bob", "owner": "alice", "note": "换人"})
        assert r.status_code == 200, r.get_data(as_text=True)
        task = r.get_json()["task"]
        assert task["owner"] == "bob" and task["board_action"] == "handoff"

    def test_缺_to_400(self, make_client):
        client = make_client()
        r = client.post("/api/subagent/board/handoff", json={"task_id": "t-1", "to": ""})
        assert r.status_code == 400
        assert r.get_json()["code"] == "invalid_handoff"


class TestDisabled:
    def test_关闭开关四条路由都_404(self, make_client, monkeypatch):
        client = make_client()
        monkeypatch.setenv("CP_TEAM_BOARD_ENABLED", "0")
        assert client.get("/api/subagent/board").status_code == 404
        for path, body in (
            ("/api/subagent/board/claim", {"task_id": "t-1", "owner": "a"}),
            ("/api/subagent/board/release", {"task_id": "t-1"}),
            ("/api/subagent/board/handoff", {"task_id": "t-1", "to": "b"}),
        ):
            assert client.post(path, json=body).status_code == 404


@pytest.fixture(scope="module")
def real_url_paths() -> set:
    from agent import tools as _tools

    saved = dict(_tools._registry)
    try:
        import app_server  # noqa: PLC0415 真实入口
        yield {str(r.rule) for r in app_server.app.url_map.iter_rules()}
    finally:
        _tools._registry.clear()
        _tools._registry.update(saved)
        _tools._registry_version += 1


def test_四条看板路由已在真实入口注册(real_url_paths):
    for p in ("/api/subagent/board", "/api/subagent/board/claim",
              "/api/subagent/board/release", "/api/subagent/board/handoff"):
        assert p in real_url_paths
