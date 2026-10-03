"""回归守卫（2026-10-03 · 审计 H1 / M-39）：管理后台守卫 + 登录限速。

【背景】plugins/admin_api.py 的 21 条路由里，原只有 2 条（/api/user/info、/api/auth/menus）
校验会话令牌，其余 19 条**无任何鉴权装饰器**。本部署 CP_API_AUTH_MODE=enforce_all 时全局闸门
还能兜住，但闸门一旦被下调（enforce/shadow/off），用户/角色 CRUD、/api/export/users 全量导出
这些**立即裸奔** —— 而审计实测该档位本身没有任何监控告警。

【本文件的守卫目标】**不是**"断言为 0 未鉴权路由"（那样太脆），而是钉住三条行为：
  ① 无任何令牌 ⇒ 拒绝（且必须是 fail-closed，组件异常也拒绝）；
  ② 共享 API 令牌 ⇒ 放行（保持既有可用路径，不把后台打挂）；
  ③ 登录失败到阈值 ⇒ 锁定（审计 M-39：原实现可在线爆破口令）。
"""
from __future__ import annotations

import importlib

import pytest
from flask import Flask


@pytest.fixture
def admin_client(monkeypatch):
    """最小 Flask app + 真实令牌/口令配置。"""
    monkeypatch.setenv("FLASK_API_TOKEN", "unit-test-token-" + "y" * 40)
    monkeypatch.setenv("YUNSHU_ADMIN_PASSWORD", "unit-test-password")
    monkeypatch.delenv("CP_UI_TOKENS", raising=False)
    import agent.server_auth as server_auth
    importlib.reload(server_auth)
    import plugins.admin_api as admin_api
    importlib.reload(admin_api)  # 顺带清空 _login_failures（模块级状态，测试隔离需要）
    app = Flask(__name__)
    app.register_blueprint(admin_api.bp)
    app.config.update(TESTING=True)
    return app.test_client(), admin_api


def _code(resp):
    return (resp.get_json() or {}).get("code")


class TestAdminGuard:
    def test_无令牌访问用户列表被拒(self, admin_client):
        client, _ = admin_client
        resp = client.get("/api/user/list")
        assert _code(resp) == 401, "GET /api/user/list 无令牌时必须拒绝（原实现无鉴权装饰器）"

    def test_无令牌导出用户被拒(self, admin_client):
        client, _ = admin_client
        assert _code(client.get("/api/export/users")) == 401, "全量导出尤其不能裸奔"

    @pytest.mark.parametrize("method,path", [
        ("delete", "/api/user/1"),
        ("post", "/api/user"),
        ("put", "/api/user/1"),
        ("post", "/api/role"),
        ("put", "/api/role/1/permissions"),
        ("put", "/api/role/1/data-scope"),
        ("put", "/api/role/1"),
        ("delete", "/api/role/1"),
        ("post", "/api/notification/1/read"),
        ("post", "/api/notification/read-all"),
    ])
    def test_无令牌写端点一律被拒(self, admin_client, method, path):
        client, _ = admin_client
        resp = getattr(client, method)(path, json={})
        assert _code(resp) == 401, method.upper() + " " + path + " 无令牌时必须拒绝"

    def test_共享API令牌放行(self, admin_client, monkeypatch):
        client, _ = admin_client
        h = {"Authorization": "Bearer " + "unit-test-token-" + "y" * 40}
        assert _code(client.get("/api/user/list", headers=h)) == 200, (
            "持共享 API 令牌必须放行 —— 否则会把既有可用路径打挂（本仓约定要求浏览器先配令牌）"
        )

    def test_错误令牌被拒(self, admin_client):
        client, _ = admin_client
        assert _code(client.get("/api/user/list", headers={"Authorization": "Bearer nope"})) == 401


class TestLoginRateLimit:
    def test_连续失败到阈值后锁定(self, admin_client):
        client, admin_api = admin_client
        body = {"username": "admin", "password": "wrong"}
        codes = []
        for _ in range(admin_api._LOGIN_MAX_FAILURES):
            codes.append(_code(client.post("/api/auth/login", json=body)))
        assert codes == [400] * admin_api._LOGIN_MAX_FAILURES, "阈值内应返回口令错误"
        locked = _code(client.post("/api/auth/login", json=body))
        assert locked == 429, (
            "达到 " + str(admin_api._LOGIN_MAX_FAILURES) + " 次失败后必须锁定（审计 M-39：原实现可在线爆破）"
        )

    def test_锁定后即使口令正确也拒绝(self, admin_client):
        client, admin_api = admin_client
        for _ in range(admin_api._LOGIN_MAX_FAILURES):
            client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
        resp = client.post("/api/auth/login", json={"username": "admin", "password": "unit-test-password"})
        assert _code(resp) == 429, "锁定期内正确口令也应被拒（否则锁定形同虚设）"

    def test_成功登录不累计失败(self, admin_client):
        client, admin_api = admin_client
        ok = client.post("/api/auth/login", json={"username": "admin", "password": "unit-test-password"})
        assert _code(ok) == 200
        assert not admin_api._login_failures, "成功登录后应清空该 key 的失败记录"

    def test_未知用户名同样计数(self, admin_client):
        client, admin_api = admin_client
        for _ in range(admin_api._LOGIN_MAX_FAILURES):
            client.post("/api/auth/login", json={"username": "nosuchuser", "password": "x"})
        resp = client.post("/api/auth/login", json={"username": "nosuchuser", "password": "x"})
        assert _code(resp) == 429, "对不存在的用户名也必须限速，否则换名字即可无限撞"