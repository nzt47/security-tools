"""E1-3 余项：变更卡的会话归属（读取侧按 session_id 过滤）

锁死：
  1. scope=session 只在 manifest.session_id 严格相等的快照里取；
  2. 老快照没有 session_id ⇒ 只出现在「看全部」（workspace）里，不被误认成任何会话；
  3. 指定 checkpoint 但属于别的会话 + scope=session ⇒ 404 not_in_session（不静默换一份）；
  4. 本会话没有检查点 ⇒ 200 + 空清单 + 说明（不是 404/500）。
"""
from __future__ import annotations

import json

import pytest
from flask import Flask

from agent.context import checkpoint as cp
from agent.server_routes.routes_changes import register_routes

DIFF = """diff --git a/docs/a.md b/docs/a.md
--- a/docs/a.md
+++ b/docs/a.md
@@ -1 +1 @@
-old
+new
"""


def _write(root, cid, session_id, created="2026-11-26T01:00:00+00:00"):
    d = root / cid
    d.mkdir(parents=True)
    manifest = {
        "id": cid, "created_at": created, "trigger_tool": "write_file",
        "session_id": session_id, "changed_total": 1, "files_captured": 1,
        "changed": [{"path": "docs/a.md", "status": "M", "size": 10,
                     "captured": True, "binary": False}],
    }
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (d / "diff.patch").write_text(DIFF, encoding="utf-8")


@pytest.fixture()
def cpdir(tmp_path, monkeypatch):
    root = tmp_path / "checkpoints"
    root.mkdir()
    monkeypatch.setattr(cp, "checkpoint_dir", lambda: root)
    _write(root, "20261126T010000Z-aaaaaaaa", "s1")
    _write(root, "20261126T020000Z-bbbbbbbb", "s2")
    return root


@pytest.fixture()
def client(cpdir):
    app = Flask(__name__)
    app.config.update(TESTING=True)
    register_routes(app)
    return app.test_client()


class TestEngine:
    def test_按会话过滤列表(self, cpdir):
        assert [r["id"] for r in cp.list_checkpoints(10)] == [
            "20261126T020000Z-bbbbbbbb", "20261126T010000Z-aaaaaaaa"]
        only = cp.list_checkpoints(10, session_id="s1")
        assert [r["id"] for r in only] == ["20261126T010000Z-aaaaaaaa"]
        assert only[0]["session_id"] == "s1"

    def test_取该会话最近一份(self, cpdir):
        doc = cp.changes(session_id="s1")
        assert doc["checkpoint"]["id"] == "20261126T010000Z-aaaaaaaa"
        assert doc["files"][0]["path"] == "docs/a.md"

    def test_老快照没有会话归属只出现在工作区级(self, cpdir):
        _write(cpdir, "20261126T030000Z-cccccccc", "")
        # 工作区级（不过滤）能看到它，且是最新一份
        assert cp.list_checkpoints(1)[0]["session_id"] == ""
        assert cp.changes()["checkpoint"]["id"] == "20261126T030000Z-cccccccc"
        # 但任何具名会话都匹配不到它（老快照没有归属，不冒充任何会话）
        assert all(r["id"] != "20261126T030000Z-cccccccc"
                   for r in cp.list_checkpoints(10, session_id="s1"))
        assert cp.changes(session_id="s1")["checkpoint"]["id"] == "20261126T010000Z-aaaaaaaa"


class TestRoute:
    def test_默认工作区级(self, client):
        body = client.get("/api/changes?session_id=s1").get_json()
        assert body["ok"] is True
        assert body["scope"] == "workspace"
        assert body["checkpoint"]["id"] == "20261126T020000Z-bbbbbbbb"
        assert body["note"]

    def test_只看本会话(self, client):
        body = client.get("/api/changes?session_id=s1&scope=session").get_json()
        assert body["scope"] == "session"
        assert body["checkpoint"]["id"] == "20261126T010000Z-aaaaaaaa"
        assert body["files"][0]["path"] == "docs/a.md"
        assert body["note"] == ""

    def test_本会话没有检查点给空清单(self, client):
        body = client.get("/api/changes?session_id=s9&scope=session").get_json()
        assert body["ok"] is True and body["scope"] == "session"
        assert body["checkpoint"] is None and body["files"] == []
        assert "本会话还没有检查点" in body["note"]

    def test_指定别的会话的检查点被拒(self, client):
        r = client.get("/api/changes?session_id=s1&scope=session&checkpoint=20261126T020000Z-bbbbbbbb")
        assert r.status_code == 404
        assert r.get_json()["code"] == "not_in_session"

    def test_缺_session_id_仍400(self, client):
        assert client.get("/api/changes").status_code == 400

class TestPrune:
    def _mk_many(self, root, n):
        for i in range(n):
            cid = "2026112%dT000000Z-%08x" % (i, i + 1)
            _write(root, cid, "s%d" % i)
        return sorted([p.name for p in root.iterdir() if p.is_dir()], reverse=True)

    def test_按条数清理且永远保留最新一份(self, cpdir):
        ids = self._mk_many(cpdir, 5)
        out = cp.prune(keep=2, days=365)
        remaining = sorted([p.name for p in cpdir.iterdir() if p.is_dir()], reverse=True)
        assert out["removed"] == len(ids) - 2
        assert remaining == ids[:2], "保留的必须是**最新**两份"

    def test_按天数清理(self, cpdir):
        import os
        import time as _t
        cid = "20200101T000000Z-0000abcd"
        _write(cpdir, cid, "s-old")
        old = _t.time() - 40 * 86400
        os.utime(cpdir / cid / "manifest.json", (old, old))
        out = cp.prune(keep=99, days=1)
        assert cid in out["ids"]
        assert not (cpdir / cid).exists()

    def test_dry_run_不删除(self, cpdir):
        ids = self._mk_many(cpdir, 4)
        out = cp.prune(keep=1, days=365, dry_run=True)
        assert out["removed"] == len(ids) - 1
        assert len([p for p in cpdir.iterdir() if p.is_dir()]) == len(ids)

    def test_阈值来自环境变量且有收敛(self, cpdir, monkeypatch):
        monkeypatch.setenv(cp.KEEP_ENV, "1")
        assert cp.keep_count() == 5, "下限 5：不允许配置成反复删到只剩 1 份"
        monkeypatch.setenv(cp.MAX_AGE_ENV, "0")
        assert cp.max_age_days() == 1
        monkeypatch.setenv(cp.MAX_AGE_ENV, "9999")
        assert cp.max_age_days() == 365
