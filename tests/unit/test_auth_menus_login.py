"""菜单下发链路集成测试（真实登录接口 → 菜单树）

走完整 HTTP 链路：POST /api/auth/login（admin / user 账号）→ 拿 token →
GET /api/auth/menus（Authorization: Bearer）→ 校验按角色过滤后的菜单结构。

与 test_auth_menus.py 的区别：本文件通过真实登录接口获取令牌（而非内部签发），
覆盖「账号鉴权 → 用户解析 → 权限过滤 → 菜单下发」全链路。
"""
import pytest

import app_server as srv


def _client():
    return srv.app.test_client()


def _login(username: str, password: str):
    resp = _client().post("/api/auth/login", json={"username": username, "password": password})
    body = resp.get_json()
    assert body["code"] == 200, f"登录失败: {body}"
    return body["data"]


def _menus(token: str) -> list[dict]:
    resp = _client().get("/api/auth/menus", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["code"] == 200
    return body["data"]


def _titles(menus: list[dict]) -> list[str]:
    titles: list[str] = []
    for node in menus:
        titles.append(node["title"])
        for child in node.get("children") or []:
            titles.append(child["title"])
    return titles


class TestMenuLoginFlow:

    def test_admin_login_returns_full_menu(self):
        """admin 登录：系统管理分组及全部子项下发；permissions 含系统日志导出权限码"""
        data = _login(srv._ADMIN_USERNAME, srv._ADMIN_PASSWORD)
        assert data["user"]["role"] == "admin"
        assert "system:log:export" in data["user"]["permissions"]
        titles = _titles(_menus(data["token"]))
        assert "仪表盘" in titles
        assert "系统管理" in titles
        for expected in ("用户列表", "角色权限", "菜单管理", "操作审计", "消息中心", "系统日志"):
            assert expected in titles

    def test_user_login_sees_system_log_but_not_user_list(self):
        """user 登录：拥有 system:view + system:notification:view → 系统管理分组、
        系统日志与消息中心可见；无 system:user:view → 用户列表/角色权限等被过滤"""
        data = _login("user", "123456")
        assert data["user"]["role"] == "user"
        assert "system:view" in data["user"]["permissions"]
        assert "system:notification:view" in data["user"]["permissions"]
        titles = _titles(_menus(data["token"]))
        assert "仪表盘" in titles
        assert "系统管理" in titles
        assert "系统日志" in titles
        assert "消息中心" in titles
        assert "用户列表" not in titles
        assert "角色权限" not in titles
        assert "操作审计" not in titles

    def test_manager_login_sees_partial_system_menu(self):
        """manager 登录：拥有 system:view/system:role:view/system:audit:view/system:notification:view →
        系统管理分组可见，角色权限/菜单管理/操作审计/消息中心/系统日志下发；
        仅用户列表被过滤"""
        data = _login("manager", "123456")
        assert data["user"]["role"] == "manager"
        titles = _titles(_menus(data["token"]))
        assert "系统管理" in titles
        for expected in ("角色权限", "菜单管理", "操作审计", "消息中心", "系统日志"):
            assert expected in titles
        assert "用户列表" not in titles

    def test_wrong_password_login_rejected(self):
        """错误密码：登录被拒（业务 401），拿不到菜单"""
        resp = _client().post("/api/auth/login", json={"username": "user", "password": "wrong"})
        body = resp.get_json()
        assert body["code"] == 401

    def test_unknown_account_login_rejected(self):
        """未知账号：登录被拒"""
        resp = _client().post("/api/auth/login", json={"username": "ghost", "password": "123456"})
        body = resp.get_json()
        assert body["code"] == 401

    def test_menu_requires_login(self):
        """未登录直接请求菜单：业务 401"""
        resp = _client().get("/api/auth/menus")
        assert resp.get_json()["code"] == 401
