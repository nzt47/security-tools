# -*- coding: utf-8 -*-
"""外部记忆导入 API：信封 / 鉴权 / dry-run 判据 / 域错误映射。

【为什么单独测路由层】service 层已有 18 条用例；这里只钉"在线上的契约"：
   - 无令牌 401（令牌按运行期 env 判定，见 agent/server_auth.py）
   - 成功响应必须是统一信封（X-Envelope: v2）+ data
   - 域错误必须映射成 RFC 9457（title/detail 分开，detail 不含明文）
   - commit 的"真写"判据在后端：dry_run 默认 true；confirm 缺失时 400
"""
from __future__ import annotations

import pytest
from flask import Flask

from agent.api_envelope import ENVELOPE_HEADER, ENVELOPE_VERSION
from agent.memory_import.models import ConfirmationRequired, PlanNotFound
from agent.server_routes import routes_memory_import as routes

TOKEN = "test-token-memory-import"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("FLASK_API_TOKEN", TOKEN)
    app = Flask(__name__)
    routes.register_routes(app, None)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture()
def auth():
    return {"X-API-Token": TOKEN}


PLAN = {
    "plan_id": "mp_test",
    "generated_at": "2026-10-24T00:00:00+08:00",
    "plan_path": "data/ingestion/memory_import/plans/mp_test.json",
    "stats": {"scanned": 10, "denoised": 1, "duplicates": 2, "kept": 7},
    "redaction": {"hits": {"openai_sk": 1}, "masked_records": 1, "blocked": 0},
    "batches": [{
        "batch_id": "mb_card", "carrier": "knowledge_card",
        "carrier_label": "知识卡片", "title": "claude 偏好与规则",
        "records": 4, "chars": 300, "record_ids": ["mr_1"],
        "digest": "# 规则", "items": [{"record_id": f"mr_{i}"} for i in range(9)],
        "stats": {}, "tags": [],
    }],
    "requires_confirm": True,
}


def test_sources_requires_token(client):
    resp = client.get("/api/memory-import/sources")
    assert resp.status_code == 401


def test_sources_lists_claude_and_trae(client, auth):
    resp = client.get("/api/memory-import/sources", headers=auth)
    assert resp.status_code == 200
    assert resp.headers.get(ENVELOPE_HEADER) == ENVELOPE_VERSION
    body = resp.get_json()
    ids = [item["id"] for item in body["data"]["sources"]]
    assert ids == ["claude", "trae"]
    assert all("available" in item for item in body["data"]["sources"])


def test_preview_passes_sources_and_trims_items(client, auth, monkeypatch):
    calls = {}

    def fake_preview(**kwargs):
        calls.update(kwargs)
        return dict(PLAN)

    monkeypatch.setattr(routes.import_service, "preview", fake_preview)
    resp = client.post("/api/memory-import/preview",
                       json={"sources": ["trae"], "include_transcripts": False},
                       headers=auth)
    assert resp.status_code == 200
    assert calls == {"sources": ["trae"], "include_transcripts": False, "save": True}
    data = resp.get_json()["data"]
    assert data["plan_id"] == "mp_test"
    assert len(data["batches"][0]["items"]) <= routes._PREVIEW_ITEMS
    # 快照里有 record_ids，但预览响应不回传（界面只需要条数）
    assert "record_ids" not in data["batches"][0]


def test_preview_rejects_unknown_source(client, auth):
    resp = client.post("/api/memory-import/preview", json={"sources": ["nope"]}, headers=auth)
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["status"] == 400
    assert "不支持的来源" in body["detail"]
    assert resp.mimetype == "application/problem+json"


def test_preview_maps_domain_error(client, auth, monkeypatch):
    def boom(**_kwargs):
        raise PlanNotFound("找不到预览计划", detail="期望文件: x.json")

    monkeypatch.setattr(routes.import_service, "preview", boom)
    resp = client.post("/api/memory-import/preview", json={}, headers=auth)
    assert resp.status_code == 409
    assert resp.get_json()["type"].endswith("plan-not-found")


def test_commit_requires_plan_id(client, auth):
    resp = client.post("/api/memory-import/commit", json={"batch_ids": ["mb_x"]}, headers=auth)
    assert resp.status_code == 400
    assert resp.get_json()["type"].endswith("missing-plan-id")


@pytest.mark.parametrize("bad_plan", ["../../etc/passwd", "mp_../x", ""])
def test_commit_rejects_path_like_plan_id(client, auth, bad_plan):
    """plan_id 来自请求体：非法格式一律 400/409，绝不被拼进文件路径。"""
    resp = client.post("/api/memory-import/commit",
                       json={"plan_id": bad_plan, "batch_ids": ["mb_card"]},
                       headers=auth)
    assert resp.status_code in (400, 409)


def test_internal_error_does_not_leak_exception_text(client, auth, monkeypatch):
    """500 的 detail 不能带异常文本（可能含路径/键名）。"""
    def boom(**_kwargs):
        raise RuntimeError("secret-internal-path C:/very/private")

    monkeypatch.setattr(routes.import_service, "preview", boom)
    resp = client.post("/api/memory-import/preview", json={"sources": ["trae"]}, headers=auth)
    assert resp.status_code == 500
    detail = resp.get_json()["detail"]
    assert "secret-internal-path" not in detail
    assert "详见服务日志" in detail


def test_commit_defaults_to_dry_run(client, auth, monkeypatch):
    seen = {}

    def fake_commit(plan_id, batch_ids, *, confirm, dry_run, explain_errors=True):
        seen.update(plan_id=plan_id, batch_ids=batch_ids, confirm=confirm,
                    dry_run=dry_run, explain_errors=explain_errors)
        return {"dry_run": True, "plan_id": plan_id, "would": [], "requires_confirm": True}

    monkeypatch.setattr(routes.import_service, "commit", fake_commit)
    resp = client.post("/api/memory-import/commit",
                       json={"plan_id": "mp_test", "batch_ids": ["mb_card"]},
                       headers=auth)
    assert resp.status_code == 200
    assert seen == {"plan_id": "mp_test", "batch_ids": ["mb_card"],
                    "confirm": False, "dry_run": True, "explain_errors": False}


def test_commit_without_confirm_is_rejected_before_service(client, auth, monkeypatch):
    def must_not_run(*_a, **_k):
        raise AssertionError("未确认的写入不应到达 service")

    monkeypatch.setattr(routes.import_service, "commit", must_not_run)
    resp = client.post("/api/memory-import/commit",
                       json={"plan_id": "mp_test", "batch_ids": ["mb_card"], "dry_run": False},
                       headers=auth)
    assert resp.status_code == 400
    assert resp.get_json()["type"].endswith("confirmation-required")


def test_commit_confirmed_writes(client, auth, monkeypatch):
    seen = {}

    def fake_commit(plan_id, batch_ids, *, confirm, dry_run, explain_errors=True):
        seen.update(confirm=confirm, dry_run=dry_run, batch_ids=batch_ids,
                    explain_errors=explain_errors)
        return {"dry_run": False, "plan_id": plan_id,
                "committed": [{"batch_id": "mb_card", "carrier": "knowledge_card",
                               "records": 4, "chars": 300, "ref": "slug-1",
                               "audit": {"seq": 900}}],
                "failed": [], "audit_flushed": True}

    monkeypatch.setattr(routes.import_service, "commit", fake_commit)
    resp = client.post("/api/memory-import/commit",
                       json={"plan_id": "mp_test", "batch_ids": ["mb_card"],
                             "dry_run": False, "confirm": True},
                       headers=auth)
    assert resp.status_code == 200
    assert seen == {"confirm": True, "dry_run": False, "batch_ids": ["mb_card"],
                    "explain_errors": False}
    data = resp.get_json()["data"]
    assert data["committed"][0]["audit"]["seq"] == 900
    assert data["dry_run"] is False


def test_commit_maps_confirmation_required_from_service(client, auth, monkeypatch):
    def boom(*_a, **_k):
        raise ConfirmationRequired("需要显式确认")

    monkeypatch.setattr(routes.import_service, "commit", boom)
    resp = client.post("/api/memory-import/commit",
                       json={"plan_id": "mp_test", "batch_ids": ["mb_card"],
                             "dry_run": False, "confirm": True},
                       headers=auth)
    assert resp.status_code == 400
    assert resp.get_json()["type"].endswith("confirmation-required")

