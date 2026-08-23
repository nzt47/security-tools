"""T8.4 开放接口（8 个）鉴权单元测试：正常访问 + 鉴权失败

覆盖（真实 Flask test_client 完整链路：before_request → 网关认证 → 分发）：
  - 8 个开放端点：无 Key → 401 / 有效 Key(read) → 200 / scope 不足 → 403
  - 无效 Key → 401
  - 探活端点 /api/open/echo：无 Key → 200（auth_required=False）
  - 创建 Key 端点：POST /api/open/keys → 201 且返回明文 key

隔离：ApiKeyManager 落盘读写 mock 掉（不触碰 agent/data/api_keys.json）；
      get_api_gateway mock 为独立实例（不污染全局单例）。
"""
from unittest import mock

import pytest
from flask import Flask, jsonify

from agent.api_gateway import ApiGateway, ApiKeyManager
from agent.api_gateway_flask import (
    register_gateway,
    _INTERNAL_OPEN_BATCH1,
    _INTERNAL_OPEN_BATCH2,
)

# 8 个灰度开放的内部只读端点（T8.4 两批）
OPEN_PATHS = [p for p, _m, _s in _INTERNAL_OPEN_BATCH1 + _INTERNAL_OPEN_BATCH2]


def _make_app() -> Flask:
    """构造含 8 个内部视图的最小 Flask 应用（视图返回 JSON，鉴权由网关接管）"""
    app = Flask(__name__)
    for path in OPEN_PATHS:
        name = "open_" + path.strip("/").replace("/", "_")

        def view(_p: str = path):
            return jsonify({"ok": True, "endpoint": _p})

        view.__name__ = name
        app.add_url_rule(path, endpoint=name, view_func=view, methods=["GET"])
    return app


def _key_info(key: str, scopes=None, enabled: bool = True) -> dict:
    """构造最小 Key 信息（与 ApiGateway.create_key 结构一致）"""
    return {
        "key": key, "user_id": "u1", "scopes": scopes or ["read"],
        "tenant_id": "", "role": "", "compat_until": "", "enabled": enabled,
        "usage_count": 0, "quota_remaining": 10000, "total_quota": 10000,
        "last_used_at": "", "created_at": "2026-08-16T00:00:00",
    }


@pytest.fixture
def client_gw():
    """test_client + 独立网关实例（Key 落盘读写被 mock，内存注入）"""
    app = _make_app()
    gw = ApiGateway()
    with mock.patch.object(ApiKeyManager, "_load_keys", lambda self: None), \
         mock.patch.object(ApiKeyManager, "_save_keys", lambda self: None), \
         mock.patch("agent.api_gateway_flask.get_api_gateway", return_value=gw):
        register_gateway(app)
    app.testing = True
    return app.test_client(), gw


class TestOpenEndpointsAuth:
    """8 个开放端点：正常访问 + 鉴权失败"""

    @pytest.mark.parametrize("path", OPEN_PATHS)
    def test_no_key_returns_401(self, client_gw, path):
        client, _gw = client_gw
        resp = client.get(path)
        assert resp.status_code == 401

    @pytest.mark.parametrize("path", OPEN_PATHS)
    def test_invalid_key_returns_401(self, client_gw, path):
        client, _gw = client_gw
        resp = client.get(path, headers={"X-API-Key": "deadbeef" * 8})
        assert resp.status_code == 401

    @pytest.mark.parametrize("path", OPEN_PATHS)
    def test_valid_read_key_returns_200(self, client_gw, path):
        client, gw = client_gw
        key = "k" * 64
        gw._api_key_manager._api_keys[key] = _key_info(key, scopes=["read"])
        resp = client.get(path, headers={"X-API-Key": key})
        assert resp.status_code == 200
        assert resp.get_json()["endpoint"] == path

    @pytest.mark.parametrize("path", OPEN_PATHS)
    def test_insufficient_scope_returns_403(self, client_gw, path):
        client, gw = client_gw
        key = "k" * 64
        # 端点要求 read，Key 只有 write → 403
        gw._api_key_manager._api_keys[key] = _key_info(key, scopes=["write"])
        resp = client.get(path, headers={"X-API-Key": key})
        assert resp.status_code == 403

    def test_disabled_key_returns_401(self, client_gw):
        client, gw = client_gw
        key = "k" * 64
        gw._api_key_manager._api_keys[key] = _key_info(key, enabled=False)
        resp = client.get(OPEN_PATHS[0], headers={"X-API-Key": key})
        assert resp.status_code == 401


class TestGatewayManagementEndpoints:
    """网关管理端点（无需 Key / 探活）"""

    def test_echo_returns_200_without_key(self, client_gw):
        client, _gw = client_gw
        resp = client.get("/api/open/echo")
        assert resp.status_code == 200

    def test_create_key_returns_201_with_plaintext(self, client_gw):
        client, gw = client_gw
        before = set(gw._api_key_manager._api_keys)
        resp = client.post("/api/open/keys", json={"user_id": "demo@example.com"})
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["ok"] is True
        assert body["user_id"] == "demo@example.com"
        new_keys = set(gw._api_key_manager._api_keys) - before
        assert len(new_keys) == 1
        assert body["api_key"] in new_keys  # 明文仅返回一次

    def test_create_key_requires_user_id(self, client_gw):
        client, _gw = client_gw
        resp = client.post("/api/open/keys", json={})
        assert resp.status_code == 400


class TestInternalEndpointsStayInternal:
    """未被开放的内部端点仍走原生路由（网关不劫持）"""

    def test_unopened_internal_path_passthrough(self, client_gw):
        # 未注册到网关注册表的内部路径：应走 Flask 原生路由（404 而非 401）
        client, _gw = client_gw
        resp = client.get("/api/status")
        assert resp.status_code == 404  # mock app 未定义该视图；关键是不返回 401
