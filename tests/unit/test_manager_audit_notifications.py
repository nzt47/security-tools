"""管理后台 操作审计 + 消息中心 后端接口测试（M4/M5 后端契约，2026-08-20）

覆盖：
- /api/audit/logs 双轨：管理后台用户 token → {code, data:{list,total}}；无凭据 → 网关 401
- /api/notification/* 四个端点：列表/未读计数/单条已读/全部已读 + 用户 token 鉴权
"""
import pytest

import app_server as srv


@pytest.fixture
def client(monkeypatch):
    """TestClient + 内存数据隔离（不落盘 data/*.json）"""
    monkeypatch.setattr(srv, "_MANAGER_AUDIT", srv._seed_manager_audit())
    monkeypatch.setattr(srv, "_MANAGER_NOTIFICATIONS", srv._seed_manager_notifications())
    # 已读写操作不落盘，避免污染真实 data/ 目录
    monkeypatch.setattr(srv, "_save_manager_notifications", lambda: None)
    srv.app.config.update(TESTING=True)
    return srv.app.test_client()


def _login(client) -> str:
    resp = client.post(
        "/api/auth/login",
        json={"username": srv._ADMIN_USERNAME, "password": srv._ADMIN_PASSWORD},
    )
    body = resp.get_json()
    assert body["code"] == 200, body
    return body["data"]["token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── 操作审计（/api/audit/logs） ──

def test_audit_logs_without_credential_rejected(client):
    """无任何凭据：网关按 T8.4 开放端点规则返回 401"""
    resp = client.get("/api/audit/logs")
    assert resp.status_code == 401
    assert resp.get_json()["status_code"] == 401


def test_audit_logs_admin_contract(client):
    """管理后台（用户 token）：返回 {code, data:{list,total}}，元素对齐前端 AuditLogItem"""
    token = _login(client)
    resp = client.get("/api/audit/logs", headers=_auth(token))
    body = resp.get_json()
    assert body["code"] == 200
    assert body["data"]["total"] == 32
    assert len(body["data"]["list"]) == 10  # 默认 pageSize=10
    first = body["data"]["list"][0]
    assert {"id", "traceId", "operator", "action", "target", "result", "createdAt"} <= set(first)


def test_audit_logs_admin_filter_and_pagination(client):
    """筛选（action/operator/keyword）与分页参数生效"""
    token = _login(client)
    resp = client.get("/api/audit/logs?action=delete&page=1&pageSize=20", headers=_auth(token))
    body = resp.get_json()
    assert body["code"] == 200
    assert body["data"]["total"] > 0
    assert all(r["action"] == "delete" for r in body["data"]["list"])

    resp2 = client.get("/api/audit/logs?operator=admin&keyword=登录系统", headers=_auth(token))
    body2 = resp2.get_json()
    assert body2["code"] == 200
    assert all("登录系统" in r["target"] for r in body2["data"]["list"])


# ── 消息中心（/api/notification/*） ──

def test_notification_requires_token(client):
    """未携带用户 token：业务 401（HTTP 200 + code 401）"""
    resp = client.get("/api/notification/list")
    body = resp.get_json()
    assert body["code"] == 401


def test_notification_list_and_filters(client):
    """列表分页 + 类型/未读筛选"""
    token = _login(client)
    resp = client.get("/api/notification/list", headers=_auth(token))
    body = resp.get_json()
    assert body["code"] == 200
    assert body["data"]["total"] == 24
    assert len(body["data"]["list"]) == 10

    unread = client.get("/api/notification/list?unreadOnly=true", headers=_auth(token)).get_json()
    assert unread["data"]["total"] > 0
    assert all(not n["read"] for n in unread["data"]["list"])

    alert = client.get("/api/notification/list?type=alert", headers=_auth(token)).get_json()
    assert alert["data"]["total"] > 0
    assert all(n["type"] == "alert" for n in alert["data"]["list"])


def test_notification_unread_count(client):
    """未读计数：24 条种子中 i%3==0 的 8 条未读"""
    token = _login(client)
    resp = client.get("/api/notification/unread-count", headers=_auth(token))
    assert resp.get_json()["data"]["unread"] == 8


def test_notification_mark_read_and_read_all(client):
    """单条已读（含不存在 404）+ 全部已读 → 未读归零"""
    token = _login(client)
    resp = client.post("/api/notification/1/read", headers=_auth(token))
    assert resp.get_json()["code"] == 200

    not_found = client.post("/api/notification/9999/read", headers=_auth(token))
    assert not_found.get_json()["code"] == 404

    client.post("/api/notification/read-all", headers=_auth(token))
    count = client.get("/api/notification/unread-count", headers=_auth(token))
    assert count.get_json()["data"]["unread"] == 0
