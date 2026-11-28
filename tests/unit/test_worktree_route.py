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

# ════════════════════════════════════════════════════════════
#  E4-5 第六批续：写路径（建 worktree / 本地提交；Owner 已点头，默认关）
# ════════════════════════════════════════════════════════════

WRITE_ENV = "CP_WORKTREE_WRITE_ENABLED"


class TestWriteGate:
    def test_面板关时两条写路由都回_disabled(self, client, monkeypatch):
        monkeypatch.delenv("CP_WORKTREE_PANEL_ENABLED", raising=False)
        monkeypatch.setenv(WRITE_ENV, "1")
        assert client.post("/api/git/worktrees", json={"name": "p"}).get_json()["code"] == "disabled"
        assert client.post("/api/git/worktrees/commit",
                           json={"name": "p", "message": "m"}).get_json()["code"] == "disabled"

    def test_写开关关时_403_且不调模块(self, client, monkeypatch):
        monkeypatch.setenv("CP_WORKTREE_PANEL_ENABLED", "1")
        monkeypatch.delenv(WRITE_ENV, raising=False)
        from agent import git_worktree

        def _boom(*a, **k):
            raise AssertionError("写开关关时不应调 create/commit")

        monkeypatch.setattr(git_worktree, "create", _boom)
        monkeypatch.setattr(git_worktree, "commit", _boom)
        r = client.post("/api/git/worktrees", json={"name": "p"})
        assert r.status_code == 403 and r.get_json()["code"] == "write_disabled"
        r2 = client.post("/api/git/worktrees/commit", json={"name": "p", "message": "m"})
        assert r2.status_code == 403 and r2.get_json()["code"] == "write_disabled"


class TestWriteMapping:
    @pytest.fixture
    def on(self, monkeypatch):
        monkeypatch.setenv("CP_WORKTREE_PANEL_ENABLED", "1")
        monkeypatch.setenv(WRITE_ENV, "1")

    def test_建成功_200(self, client, on, monkeypatch):
        from agent import git_worktree

        monkeypatch.setattr(git_worktree, "create",
                            lambda name, *a, **k: {"ok": True, "name": name,
                                                   "path": "/repo/.worktrees/x",
                                                   "branch": "yunshu/x"})
        body = client.post("/api/git/worktrees", json={"name": "x"}).get_json()
        assert body["ok"] is True and body["branch"] == "yunshu/x"

    @pytest.mark.parametrize("code,status", [
        ("bad_name", 400), ("exists", 409), ("branch_exists", 409),
        ("create_failed", 500), ("no_git", 503),
    ])
    def test_建失败码映射(self, client, on, monkeypatch, code, status):
        from agent import git_worktree

        monkeypatch.setattr(git_worktree, "create",
                            lambda name, *a, **k: {"ok": False, "code": code})
        r = client.post("/api/git/worktrees", json={"name": "x"})
        assert r.status_code == status and r.get_json()["code"] == code

    def test_提交成功_200_且不回正文(self, client, on, monkeypatch):
        from agent import git_worktree

        monkeypatch.setattr(git_worktree, "commit",
                            lambda name, message, *a, **k: {"ok": True, "name": name,
                                                            "branch": "yunshu/x",
                                                            "commit": "abc1234", "changed": 2})
        body = client.post("/api/git/worktrees/commit",
                           json={"name": "x", "message": "fix"}).get_json()
        assert body["ok"] is True and body["commit"] == "abc1234" and body["changed"] == 2

    @pytest.mark.parametrize("code,status", [
        ("bad_message", 400), ("nothing_to_commit", 409), ("not_found", 404),
        ("commit_failed", 500),
    ])
    def test_提交失败码映射(self, client, on, monkeypatch, code, status):
        from agent import git_worktree

        monkeypatch.setattr(git_worktree, "commit",
                            lambda name, message, *a, **k: {"ok": False, "code": code})
        r = client.post("/api/git/worktrees/commit", json={"name": "x", "message": "m"})
        assert r.status_code == status and r.get_json()["code"] == code

    def test_异常不回原文(self, client, on, monkeypatch):
        from agent import git_worktree

        def _boom(*a, **k):
            raise RuntimeError("SECRET-BOOM")

        monkeypatch.setattr(git_worktree, "create", _boom)
        r = client.post("/api/git/worktrees", json={"name": "x"})
        assert r.status_code == 500
        assert "SECRET-BOOM" not in json.dumps(r.get_json(), ensure_ascii=False)
