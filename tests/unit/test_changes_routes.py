"""E1-3b 变更文件卡（/api/changes）契约测试。

覆盖：逐文件 diff 切块与行数统计（纯函数）、检查点 → 逐文件变更（含未跟踪文件与截断）、
路由的必填/未知 id/无检查点/会话归属（scope=session vs workspace）。
全部在临时 git 仓库里跑，不碰真实工作区。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent.context import checkpoint


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    for key in (checkpoint.CHECKPOINT_ENABLED_ENV, checkpoint.MAX_FILES_ENV,
                checkpoint.MAX_FILE_BYTES_ENV, checkpoint.DEBOUNCE_ENV):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(checkpoint, "checkpoint_dir", lambda: tmp_path / "checkpoints")
    checkpoint.reset_state()
    yield
    checkpoint.reset_state()


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "ci@example.com")
    _git(root, "config", "user.name", "ci")
    (root / "a.txt").write_text("a1\na2\n", encoding="utf-8")
    (root / "keep.txt").write_text("keep\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


# ── 纯函数 ──────────────────────────────────────────────────


def test_parse_diff_by_file_切块与路径归一():
    text = (
        "diff --git a/a.txt b/a.txt\n"
        "index 111..222 100644\n"
        "--- a/a.txt\n+++ b/a.txt\n@@ -1,2 +1,3 @@\n a1\n-a2\n+a2b\n+a3\n"
        "diff --git a/dir/b.txt b/dir/b.txt\n"
        "new file mode 100644\n+++ b/dir/b.txt\n+one\n"
    )
    files = checkpoint.parse_diff_by_file(text)
    assert set(files) == {"a.txt", "dir/b.txt"}
    assert files["a.txt"].startswith("diff --git a/a.txt")
    # 第二块不能把第一块的内容带进来
    assert "a2b" not in files["dir/b.txt"]


def test_count_diff_lines_不计文件头():
    diff = ("diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n+extra\n")
    added, removed = checkpoint.count_diff_lines(diff)
    assert (added, removed) == (2, 1)


def test_parse_diff_by_file_空文本():
    assert checkpoint.parse_diff_by_file("") == {}


# ── checkpoint.changes ──────────────────────────────────────


def test_changes_无检查点返回_None(tmp_path):
    assert checkpoint.changes() is None


def test_changes_逐文件_diff_与未跟踪文件(repo):
    (repo / "a.txt").write_text("a1\na2\nA3\n", encoding="utf-8")
    (repo / "c.txt").write_text("new file\n", encoding="utf-8")
    manifest = checkpoint.snapshot(root=repo, trigger_tool="pytest", session_id="s-1")
    assert manifest is not None

    doc = checkpoint.changes()
    assert doc is not None
    meta = doc["checkpoint"]
    assert meta["id"] == manifest["id"]
    assert meta["session_id"] == "s-1"
    by_path = {f["path"]: f for f in doc["files"]}
    assert set(by_path) == {"a.txt", "c.txt"}
    assert by_path["a.txt"]["added"] >= 1 and by_path["a.txt"]["diff"]
    # 未跟踪文件：git diff 里没有块 ⇒ diff 为空、标 new_file（内容已整文件捕获）
    assert by_path["c.txt"]["new_file"] is True
    assert by_path["c.txt"]["diff"] == "" and by_path["c.txt"]["captured"] is True
    assert doc["truncated"] is False


def test_changes_逐文件截断标记(repo):
    (repo / "a.txt").write_text("a1\n" + "".join("line%d\n" % i for i in range(400)),
                                encoding="utf-8")
    checkpoint.snapshot(root=repo, trigger_tool="pytest")
    doc = checkpoint.changes(max_chars_per_file=200)
    entry = next(f for f in doc["files"] if f["path"] == "a.txt")
    assert entry["truncated"] is True
    assert "已截断" in entry["diff"]
    assert doc["truncated"] is True


# ── 路由 ────────────────────────────────────────────────────


@pytest.fixture()
def client(monkeypatch):
    from flask import Flask
    import agent.server_auth as server_auth
    from agent.server_routes.routes_changes import register_routes

    monkeypatch.setattr(server_auth, "authorize_request", lambda: (True, "tester", "test"))
    app = Flask("changes_routes")
    app.config.update(TESTING=True)
    register_routes(app)
    return app.test_client()


class TestRoute:
    def test_缺_session_400(self, client):
        r = client.get("/api/changes")
        assert r.status_code == 400
        assert r.get_json()["ok"] is False

    def test_没有检查点时_空清单(self, client):
        r = client.get("/api/changes?session_id=s-1")
        data = r.get_json()
        assert r.status_code == 200 and data["ok"] is True
        assert data["checkpoint"] is None and data["files"] == []
        assert data["counts"]["files"] == 0

    def test_未知检查点_404(self, client):
        r = client.get("/api/changes?session_id=s-1&checkpoint=20260101T000000Z-deadbeef")
        assert r.status_code == 404
        assert r.get_json()["code"] == "unknown_checkpoint"

    def test_已有检查点时_未知_id仍_404_不回落最新(self, client, repo):
        # 这条钉住「有检查点 ⇒ 打错的 id 不能回落成最近一次」：没有检查点时 404 是
        # 因为"没有快照"，有检查点时 404 必须是因为"这个 id 不存在"。
        (repo / "a.txt").write_text("a1\na2\nA3\n", encoding="utf-8")
        checkpoint.snapshot(root=repo, trigger_tool="pytest", session_id="s-1")
        r = client.get("/api/changes?session_id=s-1&checkpoint=20260101T000000Z-deadbeef")
        assert r.status_code == 404
        assert r.get_json()["code"] == "unknown_checkpoint"

    def test_最近检查点_会话一致_scope_session(self, client, repo):
        (repo / "a.txt").write_text("a1\na2\nA3\n", encoding="utf-8")
        checkpoint.snapshot(root=repo, trigger_tool="pytest", session_id="s-1")
        data = client.get("/api/changes?session_id=s-1").get_json()
        assert data["ok"] is True and data["scope"] == "session"
        assert data["checkpoint"]["trigger_tool"] == "pytest"
        assert data["counts"]["files"] == 1
        assert data["files"][0]["path"] == "a.txt"
        assert data["note"] == ""

    def test_会话不一致_scope_workspace_且带说明(self, client, repo):
        (repo / "a.txt").write_text("a1\na2\nA3\n", encoding="utf-8")
        checkpoint.snapshot(root=repo, trigger_tool="pytest", session_id="other")
        data = client.get("/api/changes?session_id=s-1").get_json()
        assert data["scope"] == "workspace"
        assert "工作区级快照" in data["note"]

    def test_指定检查点_id(self, client, repo):
        (repo / "a.txt").write_text("a1\na2\nA3\n", encoding="utf-8")
        first = checkpoint.snapshot(root=repo, trigger_tool="pytest")
        data = client.get("/api/changes?session_id=s-1&checkpoint=%s" % first["id"]).get_json()
        assert data["checkpoint"]["id"] == first["id"]
