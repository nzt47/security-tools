"""T8.2 网关认证升级（RBAC）单元测试

覆盖：
- create_key 租户/角色绑定字段
- check_scopes：租户绑定 → 角色权限表（has_permission）；旧 Key → 自带 scopes
- handle_request：旧 Key compat_until 过期 → 403；RBAC 无权限 → 403
tenant_manager 均 mock，不触达真实数据。
"""
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

import pytest

from agent.api_gateway import ApiGateway


def _make_gateway():
    gw = ApiGateway()
    gw.register_endpoint(
        path="/test/rbac", method="GET",
        handler=lambda req: {"ok": True},
        auth_required=True, scopes=["write"],
    )
    return gw


def _request():
    return SimpleNamespace(path="/test/rbac", method="GET", headers={})


def _key_info(**overrides):
    info = {
        "key": "k" * 64, "user_id": "u1", "scopes": ["read", "write"],
        "tenant_id": "", "role": "", "compat_until": "", "enabled": True,
        "usage_count": 0, "quota_remaining": 10000, "total_quota": 10000,
        "last_used_at": "", "created_at": "2026-08-16T00:00:00",
    }
    info.update(overrides)
    return info


class TestCreateKeyBinding:
    def test_create_key_with_tenant_binding(self):
        gw = ApiGateway()
        key_info = gw._api_key_manager.create_key(
            user_id="u1", tenant_id="org_x", role="member", scopes=["read"],
        )
        assert key_info["tenant_id"] == "org_x"
        assert key_info["role"] == "member"
        assert key_info["compat_until"] == ""

    def test_create_key_legacy_default(self):
        gw = ApiGateway()
        key_info = gw._api_key_manager.create_key(user_id="u1")
        assert key_info["tenant_id"] == ""
        assert key_info["scopes"] == ["read", "write"]


class TestCheckScopesRbac:
    def test_tenant_key_allows_when_permission_granted(self):
        gw = _make_gateway()
        with mock.patch("agent.multi_tenant.tenant_manager.has_permission",
                        return_value=True) as m_perm:
            ok = gw.check_scopes(_key_info(tenant_id="org_x", user_id="u1"),
                                 ["write"])
        assert ok is True
        m_perm.assert_called_once()
        # PermissionType.WRITE 传入（scope→permission 映射生效）

    def test_tenant_key_denies_when_no_permission(self):
        gw = _make_gateway()
        with mock.patch("agent.multi_tenant.tenant_manager.has_permission",
                        return_value=False):
            ok = gw.check_scopes(_key_info(tenant_id="org_x", user_id="u1"),
                                 ["write"])
        assert ok is False

    def test_tenant_key_denies_unknown_scope(self):
        gw = _make_gateway()
        with mock.patch("agent.multi_tenant.tenant_manager.has_permission",
                        return_value=True):
            ok = gw.check_scopes(_key_info(tenant_id="org_x", user_id="u1"),
                                 ["root"])
        assert ok is False

    def test_legacy_key_uses_own_scopes(self):
        gw = _make_gateway()
        # 旧 Key：无 tenant_id → 自带 scopes 白名单（不触达 tenant_manager）
        assert gw.check_scopes(_key_info(scopes=["read", "write"]), ["write"]) is True
        assert gw.check_scopes(_key_info(scopes=["read"]), ["write"]) is False

    def test_no_required_scopes_always_ok(self):
        gw = _make_gateway()
        assert gw.check_scopes(_key_info(), []) is True


class TestHandleRequestCompatibility:
    def test_legacy_key_expired_returns_403(self):
        gw = _make_gateway()
        expired = (datetime.now() - timedelta(days=1)).isoformat()
        key = "k" * 64
        gw._api_key_manager._api_keys[key] = _key_info(
            compat_until=expired, scopes=["read", "write"])
        req = SimpleNamespace(path="/test/rbac", method="GET",
                              headers={"X-API-Key": key})
        resp = gw.handle_request(req)
        assert resp["status_code"] == 403
        assert "兼容期已过" in resp["error"]

    def test_legacy_key_within_compat_returns_200(self):
        gw = _make_gateway()
        future = (datetime.now() + timedelta(days=30)).isoformat()
        key = "k" * 64
        gw._api_key_manager._api_keys[key] = _key_info(
            compat_until=future, scopes=["read", "write"])
        req = SimpleNamespace(path="/test/rbac", method="GET",
                              headers={"X-API-Key": key})
        resp = gw.handle_request(req)
        assert resp.get("status_code", 200) == 200

    def test_tenant_key_without_permission_returns_403(self):
        gw = _make_gateway()
        key = "k" * 64
        gw._api_key_manager._api_keys[key] = _key_info(
            tenant_id="org_x", user_id="u1")
        with mock.patch("agent.multi_tenant.tenant_manager.has_permission",
                        return_value=False):
            req = SimpleNamespace(path="/test/rbac", method="GET",
                                  headers={"X-API-Key": key})
            resp = gw.handle_request(req)
        assert resp["status_code"] == 403
