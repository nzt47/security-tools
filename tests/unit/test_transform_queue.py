# -*- coding: utf-8 -*-
"""P4：转化队列（`list_transforms` / `get_transform`）+ 反向补丁回滚。

守四件事：
1. 队列能把 P2 的归档读出来（判定 / 审核计数 / 卡片 / 落地目标），坏目录不静默隐藏；
2. 回滚**默认只预览**，execute 才真删，且删完写 `manifest.rolled_back_at`（归档保留）；
3. 目标文件被人改过 ⇒ 整体拒绝（不半删）；
4. `--into` 只碰 CARD.md，不删技能目录里的 skill.md。
"""
from __future__ import annotations

import json

import pytest
from flask import Flask

from agent import server_auth
from agent.skills_mgmt.transform import queue as tq
from agent.skills_mgmt.transform.apply import apply_plan

SAMPLE = """---
id: demo-queue
name: Demo Queue
description: 演示转化队列与回滚
---
# Demo

## 何时使用

- 需要演示转化队列时

## 步骤

- 第一步：读归档
- 第二步：预览再回滚

## 约束

- 不要删归档目录
"""


@pytest.fixture()
def artifact(tmp_path):
    p = tmp_path / "demo.md"
    p.write_text(SAMPLE, encoding="utf-8")
    return p


@pytest.fixture()
def base(tmp_path, monkeypatch):
    """把 queue 的根指到临时目录（等价于 main() 里的 --root）。"""
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(tq, "ROOT", root)
    return root


def _apply(artifact, root, **kw):
    rep = apply_plan(artifact, execute=True, root=root, **kw)
    assert rep["ok"] is True, rep
    return rep


def test_空队列(base):
    rep = tq.list_transforms()
    assert rep["count"] == 0 and rep["items"] == [] and rep["broken"] == 0


def test_apply_new_后队列可见(base, artifact):
    rep = _apply(artifact, base, new="demo-queue")
    tid = rep["transform_id"]

    got = tq.list_transforms()
    assert got["count"] == 1
    item = got["items"][0]
    assert item["transform_id"] == tid
    assert item["new"] == "demo-queue"
    assert item["revertible"] is True, item["revert_reason"]
    assert {t["rel"] for t in item["targets"]} == {
        "data/skills_repo/demo-queue/skill.md", "data/skills_repo/demo-queue/CARD.md"}
    assert all(t["state"] == "pending" for t in item["targets"])

    detail = tq.get_transform(tid)
    assert detail["ok"] is True
    assert set(detail["texts"]) == {"card.md", "skill.md"}
    assert detail["item"]["card_title"]


def test_坏归档计入_broken_而不是静默跳过(base):
    d = base / tq.TRANSFORMS / "0123456789ab"
    d.mkdir(parents=True)
    rep = tq.list_transforms()
    assert rep["count"] == 0 and rep["broken"] == 1


def test_回滚默认只预览(base, artifact):
    rep = _apply(artifact, base, new="demo-queue")
    tid = rep["transform_id"]

    dry = tq.rollback_transform(tid)
    assert dry["ok"] is True and dry["dry_run"] is True
    assert set(dry["would_delete"]) == {
        "data/skills_repo/demo-queue/skill.md", "data/skills_repo/demo-queue/CARD.md"}
    assert (base / "data/skills_repo/demo-queue/skill.md").is_file(), "预览不得真删"


def test_回滚_execute_删文件并留档(base, artifact):
    rep = _apply(artifact, base, new="demo-queue")
    tid = rep["transform_id"]

    done = tq.rollback_transform(tid, execute=True)
    assert done["ok"] is True and done["dry_run"] is False
    assert not (base / "data/skills_repo/demo-queue").exists(), "新建的空目录应一并收掉"
    assert (base / tq.TRANSFORMS / tid / "manifest.json").is_file(), "归档必须保留"

    manifest = json.loads((base / tq.TRANSFORMS / tid / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["rolled_back_at"]
    assert manifest["rolled_back_files"]

    again = tq.rollback_transform(tid, execute=True)
    assert again["ok"] is True and again["already_rolled_back"] is True


def test_目标被改过则整体拒绝(base, artifact):
    rep = _apply(artifact, base, new="demo-queue")
    tid = rep["transform_id"]
    target = base / "data/skills_repo/demo-queue/skill.md"
    target.write_text(target.read_text(encoding="utf-8") + "\n人工补充\n", encoding="utf-8")

    out = tq.rollback_transform(tid, execute=True)
    assert out["ok"] is False and out["stage"] == "conflict"
    assert [b["state"] for b in out["blocked"]] == ["modified"]
    assert target.is_file(), "拒绝时必须一个文件都不删（不半删）"
    assert (base / "data/skills_repo/demo-queue/CARD.md").is_file()


def test_into_只删_CARD_不动技能正文(base, artifact):
    skill_dir = base / "data" / "skills_repo" / "existing"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.md").write_text("---\nid: existing\n---\n\n# 既有技能\n", encoding="utf-8")

    rep = _apply(artifact, base, into="existing")
    tid = rep["transform_id"]
    assert (skill_dir / "CARD.md").is_file()

    out = tq.rollback_transform(tid, execute=True)
    assert out["ok"] is True
    assert not (skill_dir / "CARD.md").exists()
    assert (skill_dir / "skill.md").is_file(), "into 回滚不得删技能正文"


def test_非法_transform_id(base):
    assert tq.get_transform("../etc/passwd")["ok"] is False
    assert tq.rollback_transform("not-hex")["ok"] is False


def test_路由契约(base, artifact, monkeypatch):
    from agent.server_routes import routes_skills_mgmt
    monkeypatch.setattr(server_auth, "authorize_request", lambda: (True, "t", "t"))
    app = Flask(__name__)
    routes_skills_mgmt.register_routes(app, state=None)

    for name in ("api_skills_mgmt_transforms", "api_skills_mgmt_transform_detail",
                 "api_skills_mgmt_transform_rollback"):
        assert getattr(app.view_functions[name], server_auth.REQUIRES_TOKEN_ATTR, False) is True, name

    rep = _apply(artifact, base, new="demo-queue")
    tid = rep["transform_id"]
    client = app.test_client()

    listed = client.get("/api/skills-mgmt/transforms")
    assert listed.status_code == 200 and listed.get_json()["count"] == 1

    detail = client.get("/api/skills-mgmt/transforms/%s" % tid)
    assert detail.status_code == 200 and detail.get_json()["ok"] is True

    dry = client.post("/api/skills-mgmt/transforms/%s/rollback" % tid, json={})
    assert dry.status_code == 200 and dry.get_json()["dry_run"] is True

    missing = client.post("/api/skills-mgmt/transforms/%s/rollback" % ("0" * 12), json={})
    assert missing.status_code == 409
