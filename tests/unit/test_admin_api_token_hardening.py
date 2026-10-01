# -*- coding: utf-8 -*-
"""管理后台会话令牌加固回归（2026-10-01）。

## 为什么需要这个测试文件

原实现把用户名**明文写在 token 里且不做任何校验**：

    token = f"mock-token-{username}-{int(time.time() * 1000)}"
    # _token_username(): re.match(r"^mock-token-(.+)-(\\d+)$", token) -> m.group(1)

⇒ 任何人手写 `Authorization: Bearer mock-token-admin-1` 就能以 admin 身份调用
`/api/user/info`、`/api/auth/menus`、用户增删改等全部管理端点。这是**完全绕过认证**，
而不是"演示态不做强校验"这种程度的问题。

本文件把修好之后的行为钉死，重点是**旧格式必须失效**（防止哪天有人"兼容"回去）。
"""
from __future__ import annotations

import time

import pytest
from flask import Flask

from plugins.admin_api import (
    _TOKEN_TTL_SEC,
    _issue_token,
    _sign,
    bp as admin_bp,
)


@pytest.fixture()
def client(monkeypatch):
    """带固定签名密钥的测试客户端（否则进程内随机密钥让断言不可复现）。"""
    monkeypatch.setenv("FLASK_API_TOKEN", "test-signing-seed-0123456789abcdef")
    app = Flask(__name__)
    app.register_blueprint(admin_bp)
    return app.test_client()


def _username_of(client, token):
    """把 token 丢给一个受保护端点，从响应推断它是否被当成有效登录态。

    【为什么不直接调 _token_username()】它依赖 request 上下文；走真实端点更接近线上，
    也顺带覆盖了"端点确实用它做鉴权"这一事实（若哪天端点不再鉴权，本文件会转红）。
    """
    r = client.get("/api/user/info", headers={"Authorization": "Bearer " + token})
    body = r.get_json()
    assert body is not None, r.status_code
    if body.get("code") == 200:
        return body["data"]["username"]
    assert body.get("code") == 401, body
    return None


class TestForgeability:
    """核心：伪造必须不可行。"""

    def test_旧格式的伪造令牌必须失效(self, client):
        """`mock-token-admin-1` —— 修复前这一条就是**完整的认证绕过**。"""
        assert _username_of(client, "mock-token-admin-1") is None
        assert _username_of(client, "mock-token-admin-%d" % (int(time.time() * 1000),)) is None

    def test_没有签名的三段式令牌必须失效(self, client):
        user_hex = "admin".encode("utf-8").hex()
        ts = str(int(time.time()))
        assert _username_of(client, "mock-token-%s-%s" % (user_hex, ts)) is None
        # 把签名换成等长的假值同样必须失效
        assert _username_of(client, "mock-token-%s-%s-%s" % (user_hex, ts, "0" * 32)) is None

    def test_改用户名会让签名失效_无法自我提权(self, client):
        """拿 user 的合法 token，把用户名段换成 admin ⇒ 签名对不上 ⇒ 拒绝。"""
        real = _issue_token("user")
        forged = real.replace("user".encode("utf-8").hex(), "admin".encode("utf-8").hex())
        assert forged != real, "替换未生效（用户名 hex 碰撞？测试本身失效）"
        assert _username_of(client, forged) is None

    def test_篡改时间戳会让签名失效(self, client):
        real = _issue_token("admin")
        head, ts, sig = real[len("mock-token-"):].rsplit("-", 2)
        assert _username_of(client, "mock-token-%s-%d-%s" % (head, int(ts) + 1, sig)) is None

    def test_空与畸形令牌不炸(self, client):
        for bad in ["", "mock-token-", "mock-token-x", "mock-token-x-y", "Bearer", "abc"]:
            assert _username_of(client, bad) is None


class TestValidity:
    """正常路径与有效期。"""

    def test_合法令牌可还原用户名(self, client):
        assert _username_of(client, _issue_token("admin")) == "admin"
        assert _username_of(client, _issue_token("user")) == "user"

    def test_过期令牌失效(self, client):
        old_ts = str(int(time.time()) - _TOKEN_TTL_SEC - 10)
        user_hex = "admin".encode("utf-8").hex()
        tok = "mock-token-%s-%s-%s" % (user_hex, old_ts, _sign(user_hex, old_ts))
        assert _username_of(client, tok) is None, "过期令牌必须 401"

    def test_未来时间戳失效(self, client):
        """时钟被回拨/伪造到未来 ⇒ 不可信，同样拒绝。"""
        fut = str(int(time.time()) + 3600)
        user_hex = "admin".encode("utf-8").hex()
        tok = "mock-token-%s-%s-%s" % (user_hex, fut, _sign(user_hex, fut))
        assert _username_of(client, tok) is None


class TestLoginEndpoint:
    """登录端点：口令来源与「不泄露用户是否存在」。"""

    def test_正确口令登录成功后签发的令牌可鉴权(self, client, monkeypatch):
        monkeypatch.setenv("YUNSHU_ADMIN_PASSWORD", "s3cret-pw")
        r = client.post("/api/auth/login", json={"username": "admin", "password": "s3cret-pw"})
        body = r.get_json()
        assert body["code"] == 200, body
        assert _username_of(client, body["data"]["token"]) == "admin"

    def test_口令来自环境变量(self, client, monkeypatch):
        monkeypatch.setenv("YUNSHU_ADMIN_PASSWORD", "s3cret-pw")
        # 演示默认口令此时**必须**失效 —— 否则加固等于没做
        r = client.post("/api/auth/login", json={"username": "admin", "password": "123456"})
        assert r.get_json()["code"] != 200
        r2 = client.post("/api/auth/login", json={"username": "admin", "password": "s3cret-pw"})
        assert r2.get_json()["code"] == 200

    def test_用户不存在与口令错误返回同一文案(self, client):
        """防用户枚举：两种失败不得可区分。"""
        a = client.post("/api/auth/login", json={"username": "nobody", "password": "x"}).get_json()
        b = client.post("/api/auth/login", json={"username": "admin", "password": "x"}).get_json()
        assert a["message"] == b["message"], (a, b)
        assert a["code"] == b["code"]

    def test_未登录访问受保护端点返回401(self, client):
        body = client.get("/api/user/info").get_json()
        assert body["code"] == 401
