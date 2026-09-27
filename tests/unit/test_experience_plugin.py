# -*- coding: utf-8 -*-
"""experience 插件（P3）单测：七接口 + 鉴权 + 脱敏拒收 + 批次回滚。"""
from __future__ import annotations

import json
import os

import pytest
from flask import Flask

import agent.server_auth as sa
import plugins.experience as ex


def _sample(sid="s1", **kw):
    d = {
        "id": sid,
        "task": "修复 pytest 失败",
        "task_type": "bugfix",
        "stack": {"lang": "python", "frameworks": [], "files_changed": 1},
        "diffs": [{"path": "a.py", "op": "edit", "diff": "x", "bytes": 1}],
        "pitfalls": [],
        "verified": "pass",
        "created_at": "2026-09-27T00:00:00",
        "deprecated_after": None,
    }
    d.update(kw)
    return d


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "_BASE", str(tmp_path))
    monkeypatch.setattr(ex, "_CORPUS", str(tmp_path / "samples.ndjson"))
    monkeypatch.setattr(ex, "_REVIEWS", str(tmp_path / "reviews.jsonl"))
    monkeypatch.setattr(ex, "_BATCHES", str(tmp_path / "batches.jsonl"))
    # 测试语料仅数条，BM25 原始分远低于生产阈值 30 —— 关闭下限以测接口行为本身
    monkeypatch.setattr(ex, "_MIN_SCORE", 0.0)
    # 让 require_token 进入"已配置令牌"分支
    monkeypatch.setenv("FLASK_API_TOKEN", "test-token")
    monkeypatch.setattr(sa, "_API_TOKEN_ENABLED", True)
    app = Flask(__name__)
    app.register_blueprint(ex.bp)
    return app.test_client()


AUTH = {"Authorization": "Bearer test-token"}


# ── 鉴权 ──

def test_mutating_routes_require_token(client):
    r = client.post("/api/experience/ingest", json={"samples": [_sample()]})
    assert r.status_code == 401, "变更型接口必须鉴权"
    r2 = client.post("/api/experience/s1/review", json={"action": "accept", "confirmed": True})
    assert r2.status_code == 401


def test_read_routes_open(client):
    assert client.get("/api/experience/list").status_code == 200
    assert client.get("/api/experience/stats").status_code == 200


# ── ingest ──

def test_ingest_accepts_and_creates_batch(client):
    r = client.post("/api/experience/ingest", json={"samples": [_sample("a1")]}, headers=AUTH)
    j = r.get_json()
    assert r.status_code == 200 and j["ok"]
    assert j["accepted"] == ["a1"] and j["batch_id"]
    assert client.get("/api/experience/list").get_json()["total"] == 1


def test_ingest_rejects_raw_session_fields(client):
    bad = _sample("b1"); bad["messages"] = [{"role": "user"}]
    j = client.post("/api/experience/ingest", json={"samples": [bad]}, headers=AUTH).get_json()
    assert not j["ok"] and "原始会话" in j["error"]


def test_ingest_rejects_hard_block(client):
    bad = _sample("c1"); bad["diffs"][0]["diff"] = "-----BEGIN RSA PRIVATE KEY-----"
    j = client.post("/api/experience/ingest", json={"samples": [bad]}, headers=AUTH).get_json()
    assert j["ok"] and j["accepted"] == []
    assert j["rejected"][0]["reason"] == "desensitize_hard_block"
    assert j["rejected"][0]["pattern"] == "private_key_block"
    # 不得落库
    assert client.get("/api/experience/list").get_json()["total"] == 0


def test_ingest_rejection_does_not_leak_value(client):
    """拒收原因只含模式名，不得回显命中值。"""
    secret = "-----BEGIN RSA PRIVATE KEY-----"
    bad = _sample("c2"); bad["diffs"][0]["diff"] = secret
    j = client.post("/api/experience/ingest", json={"samples": [bad]}, headers=AUTH).get_json()
    assert secret not in json.dumps(j, ensure_ascii=False)


# ── detail / stats ──

def test_detail_404(client):
    assert client.get("/api/experience/nope").status_code == 404


def test_detail_and_stats(client):
    client.post("/api/experience/ingest", json={"samples": [_sample("d1"), _sample("d2")]}, headers=AUTH)
    assert client.get("/api/experience/d1").get_json()["item"]["id"] == "d1"
    st = client.get("/api/experience/stats").get_json()["stats"]
    assert st["corpus_size"] == 2
    assert st["by_verified"]["pass"] == 2
    assert st["by_lang"]["python"] == 2
    assert st["batches"] == 1


# ── review（L2）──

def test_review_requires_l2_confirmation(client):
    client.post("/api/experience/ingest", json={"samples": [_sample("r1")]}, headers=AUTH)
    r = client.post("/api/experience/r1/review", json={"action": "accept"}, headers=AUTH)
    assert r.status_code == 400 and "L2" in r.get_json()["error"]
    ok = client.post("/api/experience/r1/review",
                     json={"action": "accept", "confirmed": True}, headers=AUTH)
    assert ok.status_code == 200
    assert client.get("/api/experience/r1").get_json()["item"]["review_status"] == "accept"


def test_review_rejects_bad_action(client):
    client.post("/api/experience/ingest", json={"samples": [_sample("r2")]}, headers=AUTH)
    r = client.post("/api/experience/r2/review",
                    json={"action": "explode", "confirmed": True}, headers=AUTH)
    assert r.status_code == 400


def test_review_404_for_unknown_id(client):
    r = client.post("/api/experience/zz/review",
                    json={"action": "accept", "confirmed": True}, headers=AUTH)
    assert r.status_code == 404


# ── rollback ──

def test_rollback_removes_batch(client):
    j = client.post("/api/experience/ingest",
                    json={"samples": [_sample("x1"), _sample("x2")]}, headers=AUTH).get_json()
    bid = j["batch_id"]
    r = client.post("/api/experience/batch/%s/rollback" % bid, json={}, headers=AUTH)
    assert r.status_code == 400, "回滚必须确认"
    r2 = client.post("/api/experience/batch/%s/rollback" % bid,
                     json={"confirmed": True}, headers=AUTH)
    j2 = r2.get_json()
    assert j2["ok"] and j2["removed"] == 2
    assert client.get("/api/experience/list").get_json()["total"] == 0


def test_rollback_idempotent_guard(client):
    bid = client.post("/api/experience/ingest",
                      json={"samples": [_sample("y1")]}, headers=AUTH).get_json()["batch_id"]
    client.post("/api/experience/batch/%s/rollback" % bid, json={"confirmed": True}, headers=AUTH)
    again = client.post("/api/experience/batch/%s/rollback" % bid,
                        json={"confirmed": True}, headers=AUTH)
    assert again.status_code == 409


def test_rollback_unknown_batch(client):
    r = client.post("/api/experience/batch/nope/rollback", json={"confirmed": True}, headers=AUTH)
    assert r.status_code == 404


# ── search ──

def test_search_requires_query(client):
    assert client.get("/api/experience/search").status_code == 400


def test_search_returns_hits(client):
    """注意：语料需多条 —— 单文档时 BM25 的 IDF 为负，得分 <=0 会被过滤
    （bm25_searcher.py:304），这是 BM25 固有特性而非缺陷。"""
    client.post("/api/experience/ingest", json={"samples": [
        _sample("q1", task="修复 pytest 断言失败的问题"),
        _sample("q2", task="前端 React 组件样式调整"),
        _sample("q3", task="数据库 连接池 配置"),
        _sample("q4", task="日志 轮转 策略"),
    ]}, headers=AUTH)
    j = client.get("/api/experience/search?q=pytest").get_json()
    assert j["ok"] and j["hits"], "多文档语料下应有命中"
    assert j["hits"][0]["id"] == "q1"


def test_search_empty_on_tiny_corpus_is_known_limit(client):
    """记录已知边界：单文档语料检索为空（BM25 负 IDF 被过滤）。"""
    client.post("/api/experience/ingest",
                json={"samples": [_sample("only1", task="修复 pytest 断言失败")]}, headers=AUTH)
    j = client.get("/api/experience/search?q=pytest").get_json()
    assert j["ok"] is True
    assert j["hits"] == [], "单文档下 BM25 得分 <=0 被过滤 —— 已知限制"
