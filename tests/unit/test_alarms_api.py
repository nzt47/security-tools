# -*- coding: utf-8 -*-
"""报警器 HTTP 面（/api/alarms/*）契约测试。

要点：
- 只读端点返回统一信封（code/data/message），未读数**不含静默中的**；
- 写端点（read/read-all/ack/silence/collect）需要令牌；缺令牌是 401 且不改状态；
- 不存在 id 回 404；错误只回固定文案（异常原文不进响应）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.alarms.store import AlarmStore, reset_store


@pytest.fixture
def store(tmp_path: Path) -> AlarmStore:
    s = AlarmStore(tmp_path / "state.json", tmp_path / "events.jsonl")
    reset_store(s)
    yield s
    reset_store(None)


@pytest.fixture
def client(store):
    from flask import Flask
    from agent.alarms.api import register_alarms_api

    app = Flask(__name__)
    register_alarms_api(app)
    app.config["TESTING"] = True
    return app.test_client()


def _seed(store: AlarmStore, key="health.l3_llm_tool", **kw):
    args = dict(kind="health", severity="critical", title="L3 降级", detail="err 40%")
    args.update(kw)
    return store.raise_alarm(key, **args)


def _data(resp):
    body = resp.get_json()
    assert body["code"] == 200, body
    return body["data"]


class TestReadEndpoints:
    def test_列表与概览(self, client, store):
        _seed(store)
        _seed(store, key="unit.skills", kind="unit", severity="warning", title="技能异常")
        payload = _data(client.get("/api/alarms/list"))
        assert payload["total"] == 2
        assert len(payload["items"]) == 2
        assert payload["summary"]["firing"] == 2

    def test_按状态与未读筛选(self, client, store):
        _seed(store)
        _seed(store, key="unit.skills", kind="unit", title="技能异常")
        store.resolve("unit.skills")
        firing = _data(client.get("/api/alarms/list?state=firing"))["items"]
        assert [x["key"] for x in firing] == ["health.l3_llm_tool"]
        unread = _data(client.get("/api/alarms/list?unread_only=1"))["items"]
        assert len(unread) == 2

    def test_未读数排除静默(self, client, store):
        alarm = _seed(store)
        assert _data(client.get("/api/alarms/unread-count"))["unread"] == 1
        client.post(f"/api/alarms/{alarm.id}/silence", json={"minutes": 30},
                    headers={"Authorization": "Bearer "})  # 未配置令牌时放行
        assert _data(client.get("/api/alarms/unread-count"))["unread"] == 0

    def test_概览随列表与未读一起给出(self, client, store):
        """概览不单开端点：列表与未读数都已经带 summary（少一个没人调用的路由）。"""
        _seed(store)
        summary = _data(client.get("/api/alarms/list"))["summary"]
        assert summary["firing"] == 1 and summary["resolved"] == 0
        assert summary["firing_by_severity"]["critical"] == 1
        assert _data(client.get("/api/alarms/unread-count"))["summary"]["firing"] == 1

    def test_缺store文件时返回空列表(self, client):
        payload = _data(client.get("/api/alarms/list"))
        assert payload["items"] == [] and payload["total"] == 0


class TestWriteEndpoints:
    def test_已读_确认_静默(self, client, store):
        alarm = _seed(store)
        assert _data(client.post(f"/api/alarms/{alarm.id}/read",
                                 headers={"Authorization": "Bearer "}))["read"] is True
        assert store.get(alarm.id).read is True

        assert _data(client.post(f"/api/alarms/{alarm.id}/ack", json={"by": "owner"},
                                 headers={"Authorization": "Bearer "}))["acked"] is True
        assert store.get(alarm.id).acked_by == "owner"

        res = _data(client.post(f"/api/alarms/{alarm.id}/silence", json={"minutes": 15},
                                headers={"Authorization": "Bearer "}))
        assert res["silenced_until"]

    def test_全部已读返回条数(self, client, store):
        _seed(store)
        _seed(store, key="unit.skills", kind="unit", title="技能异常")
        assert _data(client.post("/api/alarms/read-all",
                                 headers={"Authorization": "Bearer "}))["updated"] == 2

    def test_不存在_id_回_404(self, client):
        resp = client.post("/api/alarms/9999/read", headers={"Authorization": "Bearer "})
        assert resp.status_code == 404

    def test_无令牌时_401_且不改状态(self, client, store, monkeypatch):
        monkeypatch.setenv("FLASK_API_TOKEN", "test-token-1187")
        monkeypatch.setattr("agent.server_auth._API_TOKEN", "test-token-1187")
        alarm = _seed(store)
        resp = client.post(f"/api/alarms/{alarm.id}/read")
        assert resp.status_code == 401
        assert store.get(alarm.id).read is False, "401 不能有副作用"

    def test_带令牌放行(self, client, store, monkeypatch):
        monkeypatch.setenv("FLASK_API_TOKEN", "test-token-1187")
        monkeypatch.setattr("agent.server_auth._API_TOKEN", "test-token-1187")
        alarm = _seed(store)
        resp = client.post(f"/api/alarms/{alarm.id}/read",
                           headers={"Authorization": "Bearer test-token-1187"})
        assert resp.status_code == 200
        assert store.get(alarm.id).read is True


class TestCollectEndpoint:
    def test_手动采集跑一轮(self, client, store, monkeypatch):
        from agent.alarms import collector

        def fake_collect_once(*_a, **_kw):
            store.raise_alarm("unit.skills", kind="unit", severity="warning", title="技能异常")
            return {"checked": {"units": True}, "raised": 1, "resolved": 0, "kept": []}

        monkeypatch.setattr(collector, "collect_once", fake_collect_once)
        payload = _data(client.post("/api/alarms/collect", headers={"Authorization": "Bearer "}))
        assert payload["raised"] == 1
        assert store.count() == 1

    def test_采集异常只回固定文案(self, client, monkeypatch):
        from agent.alarms import collector

        def boom(*_a, **_kw):
            raise RuntimeError("disk quota exceeded: /secret/path")

        monkeypatch.setattr(collector, "collect_once", boom)
        resp = client.post("/api/alarms/collect", headers={"Authorization": "Bearer "})
        body = resp.get_json()
        assert resp.status_code == 500
        assert "disk quota" not in json.dumps(body, ensure_ascii=False), "异常原文不许进响应"
