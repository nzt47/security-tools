"""菜单下发接口（/api/auth/menus）权限码校验集成测试

覆盖链路：token 鉴权 → 用户名解析 → 用户角色/权限 → 菜单树过滤下发。
场景：
- 未携带 token → 业务错误（HTTP 200 + code:401）
- 无效 token → HTTP 401
- admin 角色 → 全量下发（含系统管理全部子项，角色通配）
- user 角色 → 权限码未命中，系统管理分组整体不下发
- 部分授权（user 仅拥有 system:view）→ 分组下发，仅保留命中子项
"""
import pytest

import app_server as srv


def _client():
    return srv.app.test_client()


def _titles(menus: list[dict]) -> list[str]:
    """提取菜单树一级 + 二级标题，便于断言过滤结果"""
    titles: list[str] = []
    for node in menus:
        titles.append(node["title"])
        for child in node.get("children") or []:
            titles.append(child["title"])
    return titles


class TestAuthMenus:

    def test_missing_token_returns_business_401(self):
        resp = _client().get("/api/auth/menus")
        body = resp.get_json()
        assert resp.status_code == 200
        assert body["code"] == 401
        assert body["data"] is None

    def test_invalid_token_returns_http_401(self):
        resp = _client().get("/api/auth/menus", headers={"Authorization": "Bearer invalid-token"})
        assert resp.status_code == 401
        body = resp.get_json()
        assert body["code"] == 401

    def test_admin_role_gets_full_menu(self):
        """admin 角色通配：系统管理分组及其全部子项均下发"""
        token = srv._issue_user_token(srv._ADMIN_USERNAME)
        resp = _client().get("/api/auth/menus", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["code"] == 200
        titles = _titles(body["data"])
        assert "系统管理" in titles
        for expected in ("用户列表", "角色权限", "菜单管理", "操作审计", "消息中心", "系统日志"):
            assert expected in titles

    def test_user_role_gets_filtered_menu(self):
        """user 角色拥有 system:view + system:notification:view：系统管理分组、
        系统日志与消息中心可见，但用户列表/角色权限/操作审计等需更细权限码的子项被过滤"""
        token = srv._issue_user_token("user")
        resp = _client().get("/api/auth/menus", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["code"] == 200
        titles = _titles(body["data"])
        assert "仪表盘" in titles
        assert "工作台" in titles
        assert "系统管理" in titles
        assert "系统日志" in titles
        assert "消息中心" in titles
        assert "用户列表" not in titles
        assert "角色权限" not in titles
        assert "操作审计" not in titles

    def test_partial_permission_keeps_only_allowed_children(self, monkeypatch):
        """部分授权：user 拥有 system:view 但无 system:user:view →
        系统管理分组下发，仅保留权限码命中的子项（系统日志），用户列表被过滤"""
        user = dict(srv._MOCK_USER_ACCOUNT)
        user["permissions"] = ["dashboard:view", "workbench:use", "system:view"]
        monkeypatch.setattr(srv, "_get_user_by_username", lambda username: user)
        token = srv._issue_user_token("user")
        resp = _client().get("/api/auth/menus", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["code"] == 200
        titles = _titles(body["data"])
        assert "系统管理" in titles
        assert "系统日志" in titles
        assert "用户列表" not in titles

    def test_manager_role_gets_partial_menu(self):
        """manager 角色（中间权限）：拥有 system:view + system:role:view + system:audit:view
        + system:notification:view → 系统管理分组下发，角色权限/菜单管理/操作审计/消息中心/系统日志可见，
        仅用户列表被过滤"""
        token = srv._issue_user_token("manager")
        resp = _client().get("/api/auth/menus", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["code"] == 200
        titles = _titles(body["data"])
        assert "系统管理" in titles
        for expected in ("角色权限", "菜单管理", "操作审计", "消息中心", "系统日志"):
            assert expected in titles
        assert "用户列表" not in titles

    def test_unknown_username_falls_back_to_admin(self):
        """未知用户名（token 存在但无用户记录）：回退 admin 全量菜单（防御性）"""
        token = srv._issue_user_token("ghost")
        resp = _client().get("/api/auth/menus", headers={"Authorization": f"Bearer {token}"})
        body = resp.get_json()
        assert body["code"] == 200
        assert "系统管理" in _titles(body["data"])


@pytest.mark.parametrize(
    "permissions,expected_hidden",
    [
        (["system:view"], "用户列表"),                 # 仅分组权限：分组/系统日志可见，用户列表不可见
        (["system:view", "system:user:view"], "角色权限"),  # 分组+用户列表权限：角色权限仍不可见（子项权限码相互独立）
    ],
)
def test_permission_code_granularity(monkeypatch, permissions, expected_hidden):
    """权限码粒度：同一分组内不同子项权限码相互独立，按各自权限码过滤"""
    user = dict(srv._MOCK_USER_ACCOUNT)
    user["permissions"] = ["dashboard:view", *permissions]
    monkeypatch.setattr(srv, "_get_user_by_username", lambda username: user)
    token = srv._issue_user_token("user")
    resp = _client().get("/api/auth/menus", headers={"Authorization": f"Bearer {token}"})
    body = resp.get_json()
    assert body["code"] == 200
    titles = _titles(body["data"])
    assert "系统管理" in titles
    assert expected_hidden not in titles
