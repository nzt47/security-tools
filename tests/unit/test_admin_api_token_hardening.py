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
    # 只需"确定"而非"保密"：让签名在多次断言间可复现（真实密钥来自 .env，从不进源码）
    monkeypatch.setenv("FLASK_API_TOKEN", "unit-test-signing-seed")
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
        """口令完全由环境变量决定：换掉环境变量值，旧值立刻失效。

        【为什么用两个"像口令的假值"而不是历史上那个演示默认值】
        本仓 CI 有用 gitleaks 扫源码里的口令字面量；测试文件也在扫描范围内，
        在里面写死一个"演示口令"会重新触发同一条门禁（见本文件末的源码守卫）。
        这里改用 `old-pw` 充当"上一版环境变量值"，语义等价且不留可疑字面量。
        """
        monkeypatch.setenv("YUNSHU_ADMIN_PASSWORD", "s3cret-pw")
        r = client.post("/api/auth/login", json={"username": "admin", "password": "old-pw"})
        assert r.get_json()["code"] != 200, "旧口令改环境变量后仍然能登 ⇒ 口令来源不对"
        r2 = client.post("/api/auth/login", json={"username": "admin", "password": "s3cret-pw"})
        assert r2.get_json()["code"] == 200

    def test_用户不存在与口令错误返回同一文案(self, client, monkeypatch):
        """防用户枚举：两种失败不得可区分。"""
        monkeypatch.setenv("YUNSHU_ADMIN_PASSWORD", "s3cret-pw")
        a = client.post("/api/auth/login", json={"username": "nobody", "password": "x"}).get_json()
        b = client.post("/api/auth/login", json={"username": "admin", "password": "x"}).get_json()
        assert a["message"] == b["message"], (a, b)
        assert a["code"] == b["code"]

    def test_未配置口令时一律拒绝_fail_closed(self, client, monkeypatch):
        """口令只来自环境变量，源码里**不留任何字面量**（CI gitleaks 门禁要求）。

        【为什么不给"演示默认口令"兜底】首版留了一个 6 位数字常量，被 gitleaks 规则
        `hardcoded-password-assignment` 判失败。正确做法不是改名躲扫描，而是真的不留：
        未配置 ⇒ **拒绝登录并点名要配哪个变量**（fail-closed，且不静默）。
        【这一条同时也是回归守卫】若哪天有人又加了默认口令并让未配置时能登进去，本测试转红。
        """
        monkeypatch.delenv("YUNSHU_ADMIN_PASSWORD", raising=False)
        body = client.post("/api/auth/login",
                           json={"username": "admin", "password": "any-pw"}).get_json()
        assert body["code"] != 200, "口令未配置却登录成功了 ⇒ 说明又出现了默认口令兜底"
        assert "YUNSHU_ADMIN_PASSWORD" in body["message"], \
            "失败文案必须点名要配置的环境变量，否则运维无从下手"

    def test_源码中不得出现口令字面量(self):
        """镜像 CI 的 gitleaks 规则 `hardcoded-password-assignment`。

        【为什么要在这里再守一道】CI 那条规则是**正则匹配**（名字含 PASSWORD 的变量被赋字面量），
        连注释里复现该形态都会命中。本仓的规矩是"门禁失败要变成常设守卫"，
        否则同一个人在下一个改动里很容易再写回去（本次就是我自己写回去的）。
        """
        import os
        import re
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        with open(os.path.join(root, "plugins", "admin_api.py"), encoding="utf-8") as fh:
            src = fh.read()
        # 判据 = 「变量名含 PASSWORD/SECRET，且**值不像环境变量名**」。
        # 【为什么必须加后半句】`_PASSWORD_ENV = "YUNSHU_ADMIN_PASSWORD"` 这种把**变量名**
        #   存进常量的写法是正当的（那不是口令本身），CI 的 gitleaks 也没判它失败。
        #   守卫若只按左半边匹配，就会逼出"改名躲开自己的正则"这种自欺——
        #   真正要禁的是**口令值**出现在源码里。
        hits = []
        for ln, line in enumerate(src.splitlines(), 1):
            m = re.search(r"(\w*(?:PASSWORD|PASSWD|SECRET)\w*)\s*=\s*[\"']([^\"']*)[\"']",
                          line)
            if not m:
                continue
            value = m.group(2)
            if re.fullmatch(r"[A-Z][A-Z0-9_]*", value):
                continue  # 值是全大写标识符 ⇒ 存的是"环境变量名"，不是口令
            hits.append(ln)
        assert hits == [], (
            "plugins/admin_api.py 出现了口令字面量赋值（CI gitleaks 会失败）: 行 %r" % (hits,))

    def test_未登录访问受保护端点返回401(self, client):
        body = client.get("/api/user/info").get_json()
        assert body["code"] == 401
