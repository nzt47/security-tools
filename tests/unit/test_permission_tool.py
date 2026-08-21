"""PermissionManager 权限工具类单元测试

纯逻辑测试（不依赖 Flask/app_server，执行快）：
- has_permission / has_any_permission：操作级权限判定
- filter_menus：菜单树过滤（admin 通配 / 权限码命中 / 空分组剔除 / 自定义目录）
- register_role / get_role：角色注册扩展点
"""
from agent.permission_tool import PermissionManager

CATALOG = [
    {"path": "/", "title": "仪表盘"},
    {
        "path": "/system",
        "title": "系统管理",
        "authority": "system:view",
        "children": [
            {"path": "/system/user", "title": "用户列表", "authority": "system:user:view"},
            {"path": "/system/log", "title": "系统日志", "authority": "system:view"},
        ],
    },
]


def _user(role: str, permissions: list[str] | None = None) -> dict:
    return {"role": role, "permissions": permissions or []}


def _titles(menus: list[dict]) -> list[str]:
    titles: list[str] = []
    for node in menus:
        titles.append(node["title"])
        for child in node.get("children") or []:
            titles.append(child["title"])
    return titles


class TestHasPermission:

    def test_empty_code_is_public(self):
        pm = PermissionManager(CATALOG)
        assert pm.has_permission(_user("user"), "") is True

    def test_admin_wildcard(self):
        pm = PermissionManager(CATALOG)
        assert pm.has_permission(_user("admin"), "system:user:view") is True
        assert pm.has_permission(_user("admin"), "any:unknown:code") is True

    def test_permission_hit(self):
        pm = PermissionManager(CATALOG)
        assert pm.has_permission(_user("user", ["system:view"]), "system:view") is True

    def test_permission_miss(self):
        pm = PermissionManager(CATALOG)
        assert pm.has_permission(_user("user", ["system:view"]), "system:user:view") is False

    def test_has_any_permission_or_semantics(self):
        pm = PermissionManager(CATALOG)
        assert pm.has_any_permission(_user("user", ["system:view"]), ["system:user:view", "system:view"]) is True
        assert pm.has_any_permission(_user("user", []), ["system:user:view", "system:role:view"]) is False


class TestFilterMenus:

    def test_admin_gets_all(self):
        pm = PermissionManager(CATALOG)
        assert _titles(pm.filter_menus(_user("admin"))) == ["仪表盘", "系统管理", "用户列表", "系统日志"]

    def test_user_partial_permission_keeps_group(self):
        pm = PermissionManager(CATALOG)
        titles = _titles(pm.filter_menus(_user("user", ["system:view"])))
        assert titles == ["仪表盘", "系统管理", "系统日志"]

    def test_no_permission_removes_empty_group(self):
        pm = PermissionManager(CATALOG)
        assert _titles(pm.filter_menus(_user("user", []))) == ["仪表盘"]

    def test_custom_catalog_override(self):
        pm = PermissionManager(CATALOG)
        custom = [{"path": "/x", "title": "X", "authority": "x:view"}]
        assert pm.filter_menus(_user("user", []), catalog=custom) == []
        assert _titles(pm.filter_menus(_user("user", ["x:view"]), catalog=custom)) == ["X"]


class TestRoleRegistry:

    def test_register_and_get_role(self):
        pm = PermissionManager(CATALOG)
        pm.register_role({"name": "auditor", "label": "审计员", "permissions": ["system:audit:view"]})
        assert pm.has_role("auditor") is True
        assert pm.get_role("auditor")["label"] == "审计员"

    def test_unknown_role_returns_none(self):
        pm = PermissionManager(CATALOG)
        assert pm.get_role("ghost") is None
        assert pm.has_role("ghost") is False
