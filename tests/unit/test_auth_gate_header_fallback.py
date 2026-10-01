# -*- coding: utf-8 -*-
"""鉴权网关的**头部回退**（2026-10-01 实测缺陷修复的守卫）。

## 缺陷现场

两个子系统把**不同的**令牌放进**同一个** `Authorization` 头：

| 子系统 | 令牌 | 写入位置 |
|---|---|---|
| 全局鉴权网关 | `FLASK_API_TOKEN` / token_map | `Authorization: Bearer`（`@/lib/apiToken` 的 `authHeader()`） |
| 管理后台会话 | `mock-token-<signature>` | `Authorization: Bearer`（`utils/request.ts:86` 的请求拦截器） |

旧实现 `_bearer_or_header_token()` 是「**Authorization 存在就只看它**，没有才看 X-API-Token」，
于是 `CP_API_AUTH_MODE=enforce_all` 下：管理后台把**合法的管理会话令牌**放进 Authorization，
网关把它当成「无效的 API 令牌」直接拒掉 ⇒

    GET /api/user/info  (Authorization=管理令牌, X-API-Token=API令牌)  ->  401

**整个管理后台不可用**。这不是前端写法问题：管理端只能读 Authorization，网关又只认它，
两层在同一个头上互斥，前端无解 ⇒ 必须在网关侧允许回退。

## 修复

`authorize_request()`：按 `Authorization` → `X-API-Token` 顺序**逐个**试，任一个通过即放行。
安全性不变：两个头携带的是同一份共享密钥/同一张 token_map，比较逻辑一字未动；
没有候选令牌时仍走 `authorize_token("")`，保留「完全未配置令牌 ⇒ 不校验」的既有语义。
"""
from __future__ import annotations

import pytest
from flask import Flask, jsonify

import agent.server_auth as sa
from agent.security.identity import TokenMap, set_token_map


@pytest.fixture
def app():
    application = Flask("auth_gate_header_fallback_test")
    application.config["TESTING"] = True

    @application.route("/guarded")
    @sa.require_token
    def guarded():
        return jsonify({"ok": True})

    return application


def _ctx(headers):
    """在请求上下文里调用 authorize_request（它读 flask.request）。"""
    app = Flask("ctx")
    with app.test_request_context("/guarded", headers=headers):
        return sa.authorize_request()


class TestAuthorizeRequestFallback:
    """核心：Authorization 不通过时必须回退 X-API-Token，而不是直接判死。"""

    @pytest.fixture(autouse=True)
    def _shared_only(self, monkeypatch):
        monkeypatch.setattr(sa, "_API_TOKEN_ENABLED", True)
        monkeypatch.setenv("FLASK_API_TOKEN", "api-token-xyz")
        set_token_map(TokenMap(""))

    def test_管理令牌在_Authorization_而_API_令牌在_X_API_Token_时必须放行(self):
        """这就是管理后台在 enforce_all 下的真实请求形态（修复前 401）。"""
        ok, _actor, source = _ctx({
            "Authorization": "Bearer mock-token-61646d696e-1790839914-6ab78fdf",
            "X-API-Token": "api-token-xyz",
        })
        assert ok is True, "网关没有回退到 X-API-Token ⇒ 管理后台整体 401"
        assert source == sa.SRC_SHARED_TOKEN

    def test_Authorization_本身是_API_令牌时照常放行(self):
        """既有工作方式（工作台插件面板）不得回归。"""
        ok, _actor, source = _ctx({"Authorization": "Bearer api-token-xyz"})
        assert (ok, source) == (True, sa.SRC_SHARED_TOKEN)

    def test_两个头都是垃圾时必须拒绝(self):
        ok, _actor, source = _ctx({
            "Authorization": "Bearer nope",
            "X-API-Token": "also-nope",
        })
        assert ok is False and source == "denied"

    def test_只有垃圾_Authorization_时必须拒绝(self):
        """回退不是"放宽"：没有可用的第二候选就该拒。"""
        assert _ctx({"Authorization": "Bearer nope"})[0] is False

    def test_两个头都没有时必须拒绝(self):
        assert _ctx({})[0] is False

    def test_同一个令牌同时出现在两个头时不重复校验(self):
        """候选去重：同值只留一份（避免无意义地重复比对）。"""
        ok, _actor, _source = _ctx({
            "Authorization": "Bearer api-token-xyz",
            "X-API-Token": "api-token-xyz",
        })
        assert ok is True

    def test_候选令牌去重保序(self):
        """直接验证候选提取的顺序契约（Authorization 优先）。"""
        app = Flask("cand")
        with app.test_request_context("/x", headers={
                "Authorization": "Bearer A", "X-API-Token": "B"}):
            assert sa._candidate_tokens() == ["A", "B"]
        with app.test_request_context("/x", headers={"X-API-Token": "B"}):
            assert sa._candidate_tokens() == ["B"]
        with app.test_request_context("/x", headers={}):
            assert sa._candidate_tokens() == []


class TestNoRegressionOnUnconfigured:
    """「完全未配置令牌 ⇒ 不校验」的既有语义必须原样保留。"""

    def test_未配置令牌时无候选也放行(self, monkeypatch):
        monkeypatch.setattr(sa, "_AUTH_DISABLED_FOR_TEST", True)
        monkeypatch.delenv("FLASK_API_TOKEN", raising=False)
        monkeypatch.setattr(sa, "_API_TOKEN", "")
        set_token_map(TokenMap(""))
        ok, _actor, source = _ctx({})
        assert (ok, source) == (True, sa.SRC_NO_TOKEN_CONFIGURED)

    def test_require_token_端点在有回退时仍拒绝无令牌请求(self, app, monkeypatch):
        monkeypatch.setattr(sa, "_API_TOKEN_ENABLED", True)
        monkeypatch.setenv("FLASK_API_TOKEN", "api-token-xyz")
        set_token_map(TokenMap(""))
        assert app.test_client().get("/guarded").status_code == 401
        assert app.test_client().get(
            "/guarded", headers={"X-API-Token": "api-token-xyz"}).status_code == 200
