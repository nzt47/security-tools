"""T8 RBAC 权限矩阵自动化测试（对照 docs/zh/T8RBAC权限矩阵_20260816.md）

覆盖：
1. 角色 × 权限矩阵（真实 TenantManager + has_permission）：
   owner 5 权限 / admin 4（无 admin）/ member 2 / viewer 1
2. scope → PermissionType 映射（check_scopes，mock has_permission 记录实参）：
   read/write/delete/manage/admin 逐 scope 正确映射，AND 语义，未知 scope 拒绝
3. 端点访问控制（真实租户+角色分配 + 网关 check_scopes）：
   开放只读端点(read) viewer+ 全通过；write 端点 member+ 通过、viewer 403；
   manage 端点 owner/admin 通过、member 403；admin scope 仅 owner
4. 未绑定 Key 回退自带 scopes 白名单
"""
import tempfile
from unittest import mock

import pytest

from agent.api_gateway import ApiGateway
from agent.multi_tenant import (
    TenantManager, RoleType, PermissionType,
)


def _make_manager():
    return TenantManager(data_dir=tempfile.mkdtemp())


def _org_with_users(manager):
    """创建组织，返回 (org_id, {role: user_id})"""
    owner = manager.create_user("owner@t.local", "Owner")
    org = manager.create_organization("Matrix Org", owner.id)
    manager.assign_role(owner.id, org.id, RoleType.OWNER)
    users = {RoleType.OWNER: owner.id}
    for role in (RoleType.ADMIN, RoleType.MEMBER, RoleType.VIEWER):
        u = manager.create_user(f"{role.value}@t.local", role.value)
        manager.assign_role(u.id, org.id, role)
        users[role] = u.id
    return org.id, users


# 真实权限矩阵（multi_tenant.has_permission permission_map）
EXPECTED_MATRIX = {
    RoleType.OWNER: {PermissionType.READ, PermissionType.WRITE,
                     PermissionType.DELETE, PermissionType.MANAGE, PermissionType.ADMIN},
    RoleType.ADMIN: {PermissionType.READ, PermissionType.WRITE,
                     PermissionType.DELETE, PermissionType.MANAGE},
    RoleType.MEMBER: {PermissionType.READ, PermissionType.WRITE},
    RoleType.VIEWER: {PermissionType.READ},
}


class TestRolePermissionMatrix:
    """角色 × 权限矩阵（对照权限矩阵文档 §2）"""

    def test_matrix_exact(self):
        manager = _make_manager()
        org_id, users = _org_with_users(manager)
        for role, perms in EXPECTED_MATRIX.items():
            for perm in PermissionType:
                expected = perm in perms
                assert manager.has_permission(users[role], org_id, perm) is expected, \
                    f"{role.value} x {perm.value} 期望 {expected}"


class TestCheckScopesScopeMapping:
    """scope → PermissionType 映射（对照权限矩阵文档 §2.1）"""

    def _gw(self, scopes=None):
        gw = ApiGateway()
        return gw

    def _key(self, tenant_id="org_x", scopes=("read", "write")):
        return {"key": "k" * 64, "user_id": "u1", "scopes": list(scopes),
                "tenant_id": tenant_id, "role": "member", "compat_until": "",
                "enabled": True, "usage_count": 0, "quota_remaining": 10000,
                "total_quota": 10000, "last_used_at": "", "created_at": "2026-08-16T00:00:00"}

    @pytest.mark.parametrize("scope,perm", [
        ("read", PermissionType.READ), ("write", PermissionType.WRITE),
        ("delete", PermissionType.DELETE), ("manage", PermissionType.MANAGE),
        ("admin", PermissionType.ADMIN),
    ])
    def test_scope_maps_to_permission(self, scope, perm):
        gw = self._gw()
        with mock.patch("agent.multi_tenant.tenant_manager.has_permission",
                        return_value=True) as m:
            assert gw.check_scopes(self._key(tenant_id="org_x"), [scope]) is True
        m.assert_called_once_with("u1", "org_x", perm)

    def test_and_semantics_requires_all(self):
        gw = self._gw()
        with mock.patch("agent.multi_tenant.tenant_manager.has_permission",
                        return_value=True):
            assert gw.check_scopes(self._key(), ["read", "write"]) is True

    def test_unknown_scope_rejected(self):
        gw = self._gw()
        with mock.patch("agent.multi_tenant.tenant_manager.has_permission",
                        return_value=True):
            assert gw.check_scopes(self._key(), ["root"]) is False

    def test_no_required_scopes_always_ok(self):
        gw = self._gw()
        assert gw.check_scopes(self._key(), []) is True


class TestEndpointRoleAccess:
    """端点访问控制：真实租户角色 → check_scopes（对照权限矩阵文档 §3.2）"""

    def _patched_gateway(self):
        """网关 + 真实 TenantManager 替代全局单例（mock 模块属性）"""
        manager = _make_manager()
        org_id, users = _org_with_users(manager)
        gw = ApiGateway()
        patch = mock.patch("agent.multi_tenant.tenant_manager", manager)
        patch.start()
        return gw, org_id, users, patch

    def _check(self, gw, user_id, org_id, scopes):
        key_info = {"key": "k" * 64, "user_id": user_id, "scopes": ["read", "write"],
                    "tenant_id": org_id, "role": "", "compat_until": "",
                    "enabled": True, "usage_count": 0, "quota_remaining": 10000,
                    "total_quota": 10000, "last_used_at": "", "created_at": ""}
        return gw.check_scopes(key_info, scopes)

    @pytest.mark.parametrize("role", ["OWNER", "ADMIN", "MEMBER", "VIEWER"])
    def test_read_endpoint_all_roles_allowed(self, role):
        """开放只读端点（scope=read）：全部角色通过"""
        gw, org_id, users, patch = self._patched_gateway()
        try:
            assert self._check(gw, users[RoleType[role]], org_id, ["read"]) is True
        finally:
            patch.stop()

    @pytest.mark.parametrize("role,expected", [
        ("OWNER", True), ("ADMIN", True), ("MEMBER", True), ("VIEWER", False)])
    def test_write_endpoint(self, role, expected):
        """写操作端点（scope=write）：member+ 通过，viewer 403"""
        gw, org_id, users, patch = self._patched_gateway()
        try:
            assert self._check(gw, users[RoleType[role]], org_id, ["write"]) is expected
        finally:
            patch.stop()

    @pytest.mark.parametrize("role,expected", [
        ("OWNER", True), ("ADMIN", True), ("MEMBER", False), ("VIEWER", False)])
    def test_manage_endpoint(self, role, expected):
        """配置管理端点（scope=manage）：owner/admin 通过，member+ 403"""
        gw, org_id, users, patch = self._patched_gateway()
        try:
            assert self._check(gw, users[RoleType[role]], org_id, ["manage"]) is expected
        finally:
            patch.stop()

    @pytest.mark.parametrize("role,expected", [
        ("OWNER", True), ("ADMIN", False), ("MEMBER", False), ("VIEWER", False)])
    def test_admin_scope_owner_only(self, role, expected):
        """全局管理（scope=admin）：仅 owner 通过（admin 角色无 ADMIN 权限）"""
        gw, org_id, users, patch = self._patched_gateway()
        try:
            assert self._check(gw, users[RoleType[role]], org_id, ["admin"]) is expected
        finally:
            patch.stop()

    @pytest.mark.parametrize("role,expected", [
        ("OWNER", True), ("ADMIN", True), ("MEMBER", False), ("VIEWER", False)])
    def test_delete_endpoint(self, role, expected):
        """删除端点（scope=delete）：owner/admin 通过，member/viewer 403"""
        gw, org_id, users, patch = self._patched_gateway()
        try:
            assert self._check(gw, users[RoleType[role]], org_id, ["delete"]) is expected
        finally:
            patch.stop()


class TestLegacyKeyFallback:
    """未绑定租户 Key 回退自带 scopes 白名单（对照权限矩阵文档 §4）"""

    def test_unbound_key_uses_own_scopes(self):
        gw = ApiGateway()
        key_info = {"key": "k" * 64, "user_id": "u1", "scopes": ["read"],
                    "tenant_id": "", "role": "", "compat_until": "",
                    "enabled": True, "usage_count": 0, "quota_remaining": 10000,
                    "total_quota": 10000, "last_used_at": "", "created_at": ""}
        assert gw.check_scopes(key_info, ["read"]) is True
        assert gw.check_scopes(key_info, ["write"]) is False
