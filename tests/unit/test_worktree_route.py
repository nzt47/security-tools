"""E4-5 第三批路由：GET /api/git/worktrees（只读、默认关）。"""
from __future__ import annotations

import json

import pytest
from flask import Flask

from agent.server_routes.routes_changes import register_routes


@pytest.fixture
def client():
    app = Flask(__name__)
    app.config.update(TESTING=True)
    register_routes(app)
    return app.test_client()


class TestDisabled:
    def test_默认关_回_disabled_且不读仓库(self, client, monkeypatch):
        monkeypatch.delenv("CP_WORKTREE_PANEL_ENABLED", raising=False)
        from agent import git_worktree

        def _boom(*a, **k):
            raise AssertionError("开关关时不应读仓库")

        monkeypatch.setattr(git_worktree, "collect", _boom)
        body = client.get("/api/git/worktrees").get_json()
        assert body["ok"] is False and body["code"] == "disabled"


class TestEnabled:
    def test_开_1_回投影(self, client, monkeypatch):
        monkeypatch.setenv("CP_WORKTREE_PANEL_ENABLED", "1")
        from agent import git_worktree

        monkeypatch.setattr(git_worktree, "collect", lambda *a, **k: {
            "ok": True, "root": "C:/repo", "truncated": False,
            "worktrees": [{"name": "repo", "path": "C:/repo", "branch": "master",
                           "head": "abc", "dirty": False, "counts": {"M": 0},
                           "diff_stat": "", "diff_truncated": False}],
        })
        body = client.get("/api/git/worktrees").get_json()
        assert body["ok"] is True
        assert body["worktrees"][0]["name"] == "repo"

    def test_不是仓库也_200_回_code(self, client, monkeypatch):
        monkeypatch.setenv("CP_WORKTREE_PANEL_ENABLED", "true")
        from agent import git_worktree

        monkeypatch.setattr(git_worktree, "collect", lambda *a, **k: {
            "ok": False, "code": "not_a_repo", "error": "不是仓库"})
        body = client.get("/api/git/worktrees").get_json()
        assert body["ok"] is False and body["code"] == "not_a_repo"

    def test_异常不回原文(self, client, monkeypatch):
        monkeypatch.setenv("CP_WORKTREE_PANEL_ENABLED", "1")
        from agent import git_worktree

        def _boom(*a, **k):
            raise RuntimeError("SECRET-BOOM")

        monkeypatch.setattr(git_worktree, "collect", _boom)
        r = client.get("/api/git/worktrees")
        assert r.status_code == 500
        assert "SECRET-BOOM" not in json.dumps(r.get_json(), ensure_ascii=False)
