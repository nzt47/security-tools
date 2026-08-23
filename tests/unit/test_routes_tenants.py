"""routes_tenants 多租户管理路由单元测试（T8.1）

覆盖 /api/open/tenants/* 的 CRUD/用户/角色/配置/用量：
- 创建组织（自动 owner）/ 缺参 400
- 列表 / 详情 / 删除（404 分支）
- 创建用户+角色 / 未知角色 400
- 分配角色 / 用户租户列表
- 配置 GET/POST / 用量查询

路由层测试：tenant_manager / tenant_config_manager / billing_manager 均 mock，
不触达真实 agent/data/*.json（TenantManager 单例的持久化由 test_multi_tenant.py 覆盖）。
"""
from types import SimpleNamespace
from unittest import mock

import pytest
from flask import Flask

from agent.server_routes import routes_tenants


def _fake_tenant(tenant_id="org_x", name="测试组织", parent_id=""):
    return SimpleNamespace(
        id=tenant_id, name=name, type=SimpleNamespace(value="organization"),
        parent_id=parent_id, metadata={},
        created_at="2026-08-16T00:00:00", updated_at="2026-08-16T00:00:00",
    )


def _fake_user(user_id="user_x"):
    return SimpleNamespace(id=user_id, email="u@x.com", name="U",
                           metadata={}, created_at="2026-08-16T00:00:00")


@pytest.fixture
def client():
    app = Flask(__name__)
    app.config["TESTING"] = True
    with mock.patch("agent.server_routes.routes_tenants.require_token", lambda f: f), \
         mock.patch("agent.server_routes.routes_tenants.log_request", lambda **kw: lambda f: f), \
         mock.patch("agent.server_routes.routes_tenants.trace_route", lambda *a, **kw: lambda f: f):
        routes_tenants.register_routes(app, None)
    return app.test_client()


class TestTenantCreate:
    def test_create_organization_with_owner(self, client):
        with mock.patch.object(routes_tenants.tenant_manager, "create_user",
                               return_value=_fake_user()) as m_user, \
             mock.patch.object(routes_tenants.tenant_manager, "create_organization",
                               return_value=_fake_tenant()) as m_org:
            resp = client.post("/api/open/tenants",
                               json={"name": "测试组织", "owner_email": "a@x.com"})
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["ok"] is True
        assert data["organization"]["id"] == "org_x"
        assert data["owner"]["id"] == "user_x"
        m_user.assert_called_once_with("a@x.com", "a@x.com")
        m_org.assert_called_once()

    def test_create_missing_owner_email_400(self, client):
        resp = client.post("/api/open/tenants", json={"name": "无主组织"})
        assert resp.status_code == 400


class TestTenantList:
    def test_list_tenants(self, client):
        with mock.patch.object(routes_tenants.tenant_manager, "list_tenants",
                               return_value=[_fake_tenant()]):
            resp = client.get("/api/open/tenants")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True and data["count"] == 1

    def test_list_invalid_type_400(self, client):
        resp = client.get("/api/open/tenants?type=bogus")
        assert resp.status_code == 400


class TestTenantGetDelete:
    def test_get_tenant_ok(self, client):
        with mock.patch.object(routes_tenants.tenant_manager, "get_tenant",
                               return_value=_fake_tenant()):
            resp = client.get("/api/open/tenants/org_x")
        assert resp.status_code == 200
        assert resp.get_json()["tenant"]["id"] == "org_x"

    def test_get_tenant_404(self, client):
        with mock.patch.object(routes_tenants.tenant_manager, "get_tenant", return_value=None):
            resp = client.get("/api/open/tenants/none")
        assert resp.status_code == 404

    def test_delete_tenant_ok(self, client):
        with mock.patch.object(routes_tenants.tenant_manager, "get_tenant",
                               return_value=_fake_tenant()) as m_get, \
             mock.patch.object(routes_tenants.tenant_manager, "delete_tenant") as m_del:
            resp = client.delete("/api/open/tenants/org_x")
        assert resp.status_code == 200
        assert resp.get_json()["deleted"] == "org_x"
        m_del.assert_called_once_with("org_x")

    def test_delete_tenant_404(self, client):
        with mock.patch.object(routes_tenants.tenant_manager, "get_tenant", return_value=None):
            resp = client.delete("/api/open/tenants/none")
        assert resp.status_code == 404


class TestUsersRoles:
    def test_create_user_with_role(self, client):
        with mock.patch.object(routes_tenants.tenant_manager, "get_tenant",
                               return_value=_fake_tenant()), \
             mock.patch.object(routes_tenants.tenant_manager, "create_user",
                               return_value=_fake_user()) as m_user, \
             mock.patch.object(routes_tenants.tenant_manager, "assign_role") as m_role:
            resp = client.post("/api/open/tenants/org_x/users",
                               json={"email": "u@x.com", "role": "admin"})
        assert resp.status_code == 201
        assert resp.get_json()["role"] == "admin"
        m_role.assert_called_once_with("user_x", "org_x", routes_tenants.RoleType.ADMIN)

    def test_create_user_unknown_role_400(self, client):
        with mock.patch.object(routes_tenants.tenant_manager, "get_tenant",
                               return_value=_fake_tenant()):
            resp = client.post("/api/open/tenants/org_x/users",
                               json={"email": "u@x.com", "role": "superadmin"})
        assert resp.status_code == 400

    def test_assign_role_ok(self, client):
        with mock.patch.object(routes_tenants.tenant_manager, "get_tenant",
                               return_value=_fake_tenant()), \
             mock.patch.object(routes_tenants.tenant_manager, "assign_role") as m_role:
            resp = client.post("/api/open/tenants/org_x/users/user_x/roles",
                               json={"role": "viewer"})
        assert resp.status_code == 200
        assert resp.get_json()["role"] == "viewer"
        m_role.assert_called_once_with("user_x", "org_x", routes_tenants.RoleType.VIEWER)

    def test_user_tenants(self, client):
        with mock.patch.object(routes_tenants.tenant_manager, "get_user_tenants",
                               return_value=[_fake_tenant()]):
            resp = client.get("/api/open/users/user_x/tenants")
        assert resp.status_code == 200
        assert resp.get_json()["tenants"][0]["id"] == "org_x"


class TestConfigAndUsage:
    def test_config_get(self, client):
        with mock.patch.object(routes_tenants.tenant_manager, "get_tenant",
                               return_value=_fake_tenant()), \
             mock.patch.object(routes_tenants.tenant_config_manager, "get_all_configs",
                               return_value={"billing_plan": "pro"}):
            resp = client.get("/api/open/tenants/org_x/config")
        assert resp.status_code == 200
        assert resp.get_json()["config"]["billing_plan"] == "pro"

    def test_config_post(self, client):
        with mock.patch.object(routes_tenants.tenant_manager, "get_tenant",
                               return_value=_fake_tenant()), \
             mock.patch.object(routes_tenants.tenant_config_manager, "set_config") as m_set, \
             mock.patch.object(routes_tenants.tenant_config_manager, "get_all_configs",
                               return_value={"billing_plan": "pro"}):
            resp = client.post("/api/open/tenants/org_x/config",
                               json={"billing_plan": "pro"})
        assert resp.status_code == 200
        m_set.assert_called_once_with("org_x", "billing_plan", "pro")

    def test_usage(self, client):
        with mock.patch.object(routes_tenants.tenant_manager, "get_tenant",
                               return_value=_fake_tenant()), \
             mock.patch.object(routes_tenants.billing_manager, "get_usage",
                               return_value={"tenant_id": "org_x", "total": 5}):
            resp = client.get("/api/open/tenants/org_x/usage")
        assert resp.status_code == 200
        assert resp.get_json()["usage"]["total"] == 5

    def test_config_unknown_tenant_404(self, client):
        with mock.patch.object(routes_tenants.tenant_manager, "get_tenant", return_value=None):
            resp = client.get("/api/open/tenants/none/config")
        assert resp.status_code == 404
