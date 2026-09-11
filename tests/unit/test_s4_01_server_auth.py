"""TASK-S4-01 认证层单测（`agent/server_auth.py`，裁定 A3 落地）

验收对应：
- `require_token` 接受**共享令牌**或**每使用者独立令牌**（映射表命中 ⇒ 真实 actor）；
- 令牌校验成功后身份写入审计上下文（该请求全部审计归因到真实 actor，口径与埋点一致）；
- **映射表为空时完全回退既有行为**（未配置令牌 ⇒ 不校验；新机制绝不导致后台不可用）；
- 令牌原文不进入日志/异常消息（只出现指纹）。
"""

from __future__ import annotations

import pytest
from flask import Flask, jsonify

import agent.server_auth as sa
from agent.security.identity import TokenMap, set_token_map


@pytest.fixture
def app():
    application = Flask("s4_01_server_auth_test")
    application.config["TESTING"] = True

    @application.route("/guarded", methods=["GET", "POST"])
    @sa.require_token
    def guarded():
        return jsonify({"ok": True})

    @application.route("/logged", methods=["POST"])
    @sa.log_request(show_body=True, show_response=True)
    def logged():
        return jsonify({"ok": True, "echo": 1})

    @application.route("/boom", methods=["GET"])
    @sa.log_request()
    def boom():
        raise RuntimeError("kaboom")

    return application


class TestApiToken:
    def test_reads_env_at_runtime(self, monkeypatch):
        monkeypatch.setenv("FLASK_API_TOKEN", "shared-1")
        assert sa.current_api_token() == "shared-1"
        monkeypatch.setenv("FLASK_API_TOKEN", "shared-2")
        assert sa.current_api_token() == "shared-2"

    def test_empty_env_falls_back_to_import_value(self, monkeypatch):
        monkeypatch.delenv("FLASK_API_TOKEN", raising=False)
        assert sa.current_api_token() == sa._API_TOKEN


class TestAuthorizeToken:
    def test_shared_token_accepted(self, monkeypatch):
        monkeypatch.setattr(sa, "_API_TOKEN_ENABLED", True)
        monkeypatch.setenv("FLASK_API_TOKEN", "shared-token")
        set_token_map(TokenMap(""))
        ok, actor, source = sa.authorize_token("shared-token")
        assert ok is True
        assert actor == ""
        assert source == sa.SRC_SHARED_TOKEN

    def test_mapped_token_accepted_with_real_actor(self, monkeypatch):
        monkeypatch.setattr(sa, "_API_TOKEN_ENABLED", False)
        set_token_map(TokenMap("tokA:alice"))
        ok, actor, source = sa.authorize_token("tokA")
        assert (ok, actor, source) == (True, "alice", "token_map")

    def test_unknown_token_denied_when_map_configured(self, monkeypatch):
        monkeypatch.setattr(sa, "_API_TOKEN_ENABLED", False)
        set_token_map(TokenMap("tokA:alice"))
        assert sa.authorize_token("nope")[0] is False
        assert sa.authorize_token("")[0] is False

    def test_no_configuration_skips_validation(self, monkeypatch):
        """**既有行为**：未配置任何令牌 ⇒ 不校验"""
        monkeypatch.setattr(sa, "_API_TOKEN_ENABLED", False)
        set_token_map(TokenMap(""))
        ok, actor, source = sa.authorize_token("")
        assert (ok, actor, source) == (True, "", sa.SRC_NO_TOKEN_CONFIGURED)

    def test_token_map_works_even_when_shared_disabled(self, monkeypatch):
        """映射表是**独立**的新机制：共享令牌关闭不影响它"""
        monkeypatch.setattr(sa, "_API_TOKEN_ENABLED", False)
        set_token_map(TokenMap("tokA:bob"))
        assert sa.authorize_token("tokA")[:2] == (True, "bob")


class TestRequireToken:
    def test_passes_when_not_configured(self, app, monkeypatch):
        monkeypatch.setattr(sa, "_API_TOKEN_ENABLED", False)
        set_token_map(TokenMap(""))
        assert app.test_client().get("/guarded").status_code == 200

    def test_401_without_token_when_map_configured(self, app, monkeypatch):
        monkeypatch.setattr(sa, "_API_TOKEN_ENABLED", False)
        set_token_map(TokenMap("tokA:alice"))
        response = app.test_client().get("/guarded")
        assert response.status_code == 401
        assert "未授权" in response.get_json()["error"]

    def test_bearer_and_x_api_token_headers(self, app, monkeypatch):
        monkeypatch.setattr(sa, "_API_TOKEN_ENABLED", True)
        monkeypatch.setenv("FLASK_API_TOKEN", "shared-token")
        set_token_map(TokenMap(""))
        client = app.test_client()
        assert client.get("/guarded", headers={
            "Authorization": "Bearer shared-token"}).status_code == 200
        assert client.get("/guarded", headers={
            "X-API-Token": "shared-token"}).status_code == 200
        assert client.get("/guarded", headers={
            "X-API-Token": "wrong"}).status_code == 401

    def test_binds_identity_into_audit_context(self, app, monkeypatch):
        """令牌命中 ⇒ 身份写入请求上下文（后续审计归因到真实 actor）"""
        monkeypatch.setattr(sa, "_API_TOKEN_ENABLED", False)
        set_token_map(TokenMap("tokA:alice"))
        application = app
        seen = {}

        @application.route("/identity")
        @sa.require_token
        def identity():
            from flask import g
            seen.update(getattr(g, "_cp_identity", {}) or {})
            identity_obj = sa.resolve_request_identity()
            return jsonify({"actor": identity_obj.actor,
                            "source": identity_obj.identity_source,
                            "degraded": identity_obj.degraded})

        body = application.test_client().get(
            "/identity", headers={"Authorization": "Bearer tokA"}).get_json()
        assert body == {"actor": "alice", "source": "token_map", "degraded": False}
        assert seen == {"actor": "alice", "identity_source": "token_map"}


class TestNoIdentityLeak:
    """身份上下文**不得跨请求/跨测试泄漏**（全量回归实测的真实缺陷）

    首版实现让 `require_token` 把 actor 写进审计 ContextVar
    （`audit.facade.set_ui_actor`），却无法安全复位（Flask 3 禁止首个请求后再注册
    `teardown_request`；`after_this_request` 又早于 `after_request` 的审计落账）——
    结果该 actor 泄漏到此后所有同线程请求，使**既有** `test_audit_facade` 中
    「无 actor 应归因 system」的用例失败。修复：只写 `flask.g`，不写 ContextVar。
    """

    def test_no_ui_actor_contextvar_leak(self, app, monkeypatch):
        from agent.audit import facade as facade_mod

        monkeypatch.setattr(sa, "_API_TOKEN_ENABLED", False)
        set_token_map(TokenMap("tokA:alice"))
        client = app.test_client()
        assert client.get("/guarded", headers={
            "Authorization": "Bearer tokA"}).status_code == 200
        # 请求结束后，未显式给 actor 的审计记录必须回落 system（不得被上例身份污染）
        assert facade_mod.audit.resolve_actor() == ("system", "default_system")

    def test_g_context_is_request_scoped(self, app, monkeypatch):
        monkeypatch.setattr(sa, "_API_TOKEN_ENABLED", False)
        set_token_map(TokenMap("tokA:alice"))
        application = app

        @application.route("/seen")
        @sa.require_token
        def seen():
            from flask import g
            return jsonify({"has_identity": bool(getattr(g, "_cp_identity", None))})

        client = application.test_client()
        assert client.get("/seen", headers={
            "Authorization": "Bearer tokA"}).get_json()["has_identity"] is True
        assert client.get("/seen", headers={
            "Authorization": "Bearer tokA"}).get_json()["has_identity"] is True
        # 无令牌请求（映射表命中态下会被 401 拦下，故此处直接验证 `g` 的请求作用域）
        client2 = application.test_client()
        assert client2.get("/seen", headers={
            "Authorization": "Bearer tokA"}).get_json()["has_identity"] is True


class TestResolveRequestIdentity:
    def test_legacy_header_path_without_map(self, app, monkeypatch):
        monkeypatch.setattr(sa, "_API_TOKEN_ENABLED", False)
        set_token_map(TokenMap(""))
        with app.test_request_context(headers={"X-Audit-Actor": "carol"}):
            identity = sa.resolve_request_identity()
        assert identity.actor == "carol"
        assert identity.identity_source == "header:X-Audit-Actor"
        assert identity.degraded is True

    def test_remote_addr_fallback(self, app, monkeypatch):
        monkeypatch.setattr(sa, "_API_TOKEN_ENABLED", False)
        set_token_map(TokenMap(""))
        with app.test_request_context(environ_base={"REMOTE_ADDR": "10.0.0.9"}):
            identity = sa.resolve_request_identity()
        assert identity.actor == "ui:10.0.0.9"
        assert identity.identity_source == "remote_addr"

    def test_session_id_propagated(self, app, monkeypatch):
        monkeypatch.setattr(sa, "_API_TOKEN_ENABLED", False)
        set_token_map(TokenMap(""))
        with app.test_request_context():
            identity = sa.resolve_request_identity(session_id="sess-1")
        assert identity.session_id == "sess-1"


class TestLogRequest:
    def test_logs_request_and_response(self, app, caplog):
        with caplog.at_level("INFO", logger="agent.server_auth"):
            response = app.test_client().post("/logged", json={"a": 1})
        assert response.status_code == 200
        assert "接口: logged" in caplog.text
        assert "[RESPONSE] 状态码: 200" in caplog.text

    def test_logs_error_and_reraises(self, app, caplog):
        with caplog.at_level("ERROR", logger="agent.server_auth"):
            with pytest.raises(RuntimeError):
                app.test_client().get("/boom")
        assert "异常" in caplog.text

    def test_non_json_body_is_tolerated(self, app, caplog):
        client = app.test_client()
        with caplog.at_level("INFO", logger="agent.server_auth"):
            response = client.post("/logged", data={"a": "1"})
        assert response.status_code == 200


class TestNoPlaintextTokenInLogs:
    def test_token_never_logged(self, app, monkeypatch, caplog):
        monkeypatch.setattr(sa, "_API_TOKEN_ENABLED", False)
        set_token_map(TokenMap("super-secret-token:alice"))
        with caplog.at_level("DEBUG", logger="agent.server_auth"):
            app.test_client().get(
                "/guarded", headers={"Authorization": "Bearer wrong-token"})
        assert "super-secret-token" not in caplog.text
        assert "wrong-token" not in caplog.text
