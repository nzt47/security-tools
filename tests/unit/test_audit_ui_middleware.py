"""TASK-S2-02 UI 写路由审计包装单测（`agent/audit/ui_middleware.py`）

覆盖：身份解析（诚实降级）/ 动作推导 / 写方法过滤 / 跳过前缀 / 状态码归类 /
请求体指纹不落原文 / 显式装饰器去重 / 无请求上下文可用性 / UI 与 Agent 同表。
"""
from __future__ import annotations

import io
import json

import pytest
from flask import Flask, jsonify

from agent.audit import facade as facade_mod
from agent.audit.chain import AuditChain, reset_audit_chains
from agent.audit.facade import AuditFacade, get_ui_context
from agent.audit.ui_middleware import (
    DEFAULT_SKIP_PREFIXES,
    UIAuditRecorder,
    _status_from_code,
    action_from_request,
    audit_action,
    install_flask_audit,
    reset_ui_recorders,
    resolve_ui_actor,
    token_fingerprint,
)


# ════════════════════════════════════════════════════════════
#  夹具
# ════════════════════════════════════════════════════════════


@pytest.fixture
def chain(tmp_path):
    reset_audit_chains()
    reset_ui_recorders()
    c = AuditChain(str(tmp_path / "audit_chain.db"),
                   roots_path=str(tmp_path / "roots.jsonl"),
                   signing_key_path=str(tmp_path / "k.pem"), auto_seal=False)
    yield c
    c.close(timeout=2.0)
    reset_audit_chains()
    reset_ui_recorders()


@pytest.fixture
def facade(chain):
    return AuditFacade(chain=chain, enabled=True, db_path=chain.db_path)


@pytest.fixture
def app(facade, chain):
    """真实 Flask app + 全局写路由审计包装（+ 一个显式装饰的写路由）

    同时把**进程级门面**绑定到测试台账：路由内部的 audit.record 调用与包装层
    落进同一条链（审计平权：UI 与 Agent 同表）。
    """
    reset_ui_recorders()
    application = Flask("s2_02_ui_test")
    application.config["PROPAGATE_EXCEPTIONS"] = False
    recorder = install_flask_audit(application, UIAuditRecorder(facade=facade))

    @application.route("/api/thing/<thing_id>", methods=["POST", "GET"])
    @audit_action("thing.update", subject_arg="thing_id", payload_keys=("name",))
    def thing(thing_id):
        return jsonify({"ok": True, "id": thing_id})

    @application.route("/api/plain", methods=["POST"])
    def plain():
        return jsonify({"ok": True})

    @application.route("/api/boom", methods=["POST"])
    def boom():
        raise RuntimeError("handler exploded")

    @application.route("/api/denied", methods=["POST"])
    def denied():
        return jsonify({"ok": False, "error": "业务拒绝"}), 400

    @application.route("/api/health/ping", methods=["POST"])
    def health_ping():
        return jsonify({"ok": True})

    @application.route("/api/nested-audit", methods=["POST"])
    def nested_audit():
        from agent.audit import audit as process_audit
        process_audit.record("nested.act", subject="inner", source="agent")
        return jsonify({"ok": True})

    previous = facade_mod.audit.bind(chain)
    old_enabled = facade_mod.audit.enabled
    facade_mod.audit.enabled = True
    yield {"app": application, "recorder": recorder, "facade": facade}
    facade_mod.audit.bind(previous)
    facade_mod.audit.enabled = old_enabled


def _flat(entry) -> dict:
    """审计记录的业务字段（门面把 extra 落在顶层）"""
    return entry.payload


# ════════════════════════════════════════════════════════════
#  1. 身份解析（诚实口径）
# ════════════════════════════════════════════════════════════


class TestActorResolution:
    def test_header_actor_wins(self):
        actor, src = resolve_ui_actor(headers={"X-Audit-Actor": "admin"})
        assert actor == "admin" and src == "header:X-Audit-Actor"

    def test_x_user_header(self):
        actor, src = resolve_ui_actor(headers={"X-User": "alice"})
        assert actor == "alice" and src == "header:X-User"

    def test_cookie_actor(self):
        actor, src = resolve_ui_actor(cookies={"username": "bob"})
        assert actor == "bob" and src == "cookie:username"

    def test_bearer_token_uses_fingerprint_not_plaintext(self):
        token = "sk-test-SECRET-TOKEN-VALUE"
        actor, src = resolve_ui_actor(headers={"Authorization": f"Bearer {token}"})
        assert token not in actor
        assert actor == token_fingerprint(token)
        assert src == "bearer_token_fingerprint"

    def test_fallback_to_remote_addr(self):
        actor, src = resolve_ui_actor(remote_addr="10.0.0.9")
        assert actor == "ui:10.0.0.9" and src == "remote_addr"

    def test_degraded_when_no_identity_at_all(self):
        actor, src = resolve_ui_actor()
        assert actor == "ui:unknown" and src == "degraded_no_identity"

    def test_fingerprint_is_stable_and_prefixed(self):
        assert token_fingerprint("x") == token_fingerprint("x")
        assert token_fingerprint("x").startswith("tok_")
        assert token_fingerprint("x") != token_fingerprint("y")


# ════════════════════════════════════════════════════════════
#  2. 动作推导与过滤
# ════════════════════════════════════════════════════════════


class TestActionAndFiltering:
    @pytest.mark.parametrize("method,verb", [("POST", "post"), ("PUT", "put"),
                                             ("PATCH", "patch"), ("DELETE", "delete")])
    def test_action_uses_endpoint_and_verb(self, method, verb):
        assert action_from_request(method, "/api/x", "api_x") == f"ui.api_x.{verb}"

    def test_action_falls_back_to_path_when_no_endpoint(self):
        assert action_from_request("POST", "/api/skills-mgmt/abc", "") == \
            "ui.api_skills-mgmt_abc.post"

    def test_action_unmatched_path(self):
        assert action_from_request("POST", "/", "") == "ui.unmatched.post"

    def test_write_methods_audited_and_get_skipped(self, facade):
        rec = UIAuditRecorder(facade=facade)
        assert rec._should_audit("POST", "/api/x") is True
        assert rec._should_audit("DELETE", "/api/x") is True
        assert rec._should_audit("GET", "/api/x") is False
        assert rec._should_audit("HEAD", "/api/x") is False

    def test_default_skip_prefixes(self, facade):
        rec = UIAuditRecorder(facade=facade)
        for prefix in DEFAULT_SKIP_PREFIXES:
            assert rec._should_audit("POST", f"{prefix}/x") is False

    def test_env_extra_skip_prefixes(self, facade, monkeypatch):
        monkeypatch.setenv("AUDIT_UI_SKIP_PREFIXES", "/api/internal")
        rec = UIAuditRecorder(facade=facade)
        assert rec._should_audit("POST", "/api/internal/x") is False
        assert rec._should_audit("POST", "/api/other") is True

    def test_enabled_switch(self, facade):
        rec = UIAuditRecorder(facade=facade, enabled=False)
        assert rec._should_audit("POST", "/api/x") is False
        rec.enabled = True
        assert rec._should_audit("POST", "/api/x") is True

    @pytest.mark.parametrize("code,expect", [(200, "ok"), (302, "ok"), (400, "rejected"),
                                             (404, "rejected"), (500, "error")])
    def test_status_from_code(self, code, expect):
        assert _status_from_code(code) == expect


# ════════════════════════════════════════════════════════════
#  3. 真实 Flask 路由落账
# ════════════════════════════════════════════════════════════


class TestFlaskRecording:
    @staticmethod
    def _entries(chain):
        chain.flush()
        return chain.entries()

    def test_post_write_route_audited(self, app, chain):
        r = app["app"].test_client().post("/api/plain", json={"k": "v"})
        assert r.status_code == 200
        rows = self._entries(chain)
        assert len(rows) == 1
        e = rows[0]
        assert e.source == "ui"
        assert e.action == "ui.plain.post"          # endpoint = 视图函数名
        assert e.subject == "ui:/api/plain"
        assert _flat(e)["status_code"] == 200
        assert _flat(e)["method"] == "POST"
        assert _flat(e)["duration_ms"] >= 0
        assert _flat(e)["audit_scope"] == "ui_write_route"
        assert e.payload["status"] == "ok"

    def test_get_route_not_audited(self, app, chain):
        app["app"].test_client().get("/api/thing/abc")
        assert self._entries(chain) == []

    def test_health_prefix_not_audited(self, app, chain):
        app["app"].test_client().post("/api/health/ping")
        assert self._entries(chain) == []

    def test_actor_from_header(self, app, chain):
        app["app"].test_client().post("/api/plain",
                                      headers={"X-Audit-Actor": "admin@yunshu"})
        e = self._entries(chain)[0]
        assert e.actor == "admin@yunshu"
        assert _flat(e)["identity_source"] == "header:X-Audit-Actor"
        assert _flat(e)["actor_source"] == "explicit"   # actor 由本层显式给定

    def test_actor_degraded_to_remote_addr(self, app, chain):
        app["app"].test_client().post("/api/plain")
        e = self._entries(chain)[0]
        assert e.actor.startswith("ui:")
        assert _flat(e)["identity_source"] == "remote_addr"

    def test_body_hash_recorded_but_body_not(self, app, chain):
        secret = "sk-test-UI-BODY-LEAK"
        app["app"].test_client().post("/api/plain", json={"api_key": secret})
        e = self._entries(chain)[0]
        assert len(_flat(e)["body_hash"]) == 64
        assert _flat(e)["body_bytes"] > 0
        assert secret not in json.dumps(e.payload, ensure_ascii=False)

    def test_5xx_audited_as_error(self, app, chain):
        r = app["app"].test_client().post("/api/boom")
        assert r.status_code == 500
        rows = self._entries(chain)
        assert len(rows) == 1
        assert _flat(rows[0])["status_code"] == 500
        assert rows[0].payload["status"] == "error"

    def test_4xx_audited_as_rejected(self, app, chain):
        r = app["app"].test_client().post("/api/denied")
        assert r.status_code == 400
        rows = self._entries(chain)
        assert rows[0].payload["status"] == "rejected"

    def test_unmatched_write_route_audited(self, app, chain):
        r = app["app"].test_client().post("/api/does-not-exist")
        assert r.status_code == 404
        rows = self._entries(chain)
        assert rows[0].payload["status"] == "rejected"

    def test_explicit_decorator_records_semantic_action(self, app, chain):
        app["app"].test_client().post("/api/thing/t-1", json={"name": "n1"})
        rows = self._entries(chain)
        assert len(rows) == 1                       # 不重复落账
        e = rows[0]
        assert e.action == "thing.update"
        assert e.subject == "t-1"
        assert _flat(e)["request_fields"] == {"name": "n1"}
        assert _flat(e)["audit_scope"] == "ui_write_route_explicit"

    def test_explicit_decorator_skipped_on_get(self, app, chain):
        app["app"].test_client().get("/api/thing/t-1")
        assert self._entries(chain) == []

    def test_nested_audit_inherits_ui_actor(self, app, chain):
        app["app"].test_client().post("/api/nested-audit",
                                      headers={"X-Audit-Actor": "admin"})
        rows = self._entries(chain)
        by_action = {r.action: r for r in rows}
        assert "nested.act" in by_action
        inner = by_action["nested.act"]
        assert inner.actor == "admin"
        assert inner.payload["actor_source"] == "ui_request_context"

    def test_ui_records_share_one_chain(self, app, chain):
        app["app"].test_client().post("/api/plain")
        app["app"].test_client().post("/api/nested-audit")
        chain.flush()
        assert chain.verify_chain().ok is True
        assert {r.source for r in chain.entries()} == {"ui", "agent"}

    def test_large_body_skipped(self, facade, chain):
        application = Flask("big")
        install_flask_audit(application, UIAuditRecorder(facade=facade,
                                                         max_body_bytes=16))

        @application.route("/api/big", methods=["POST"])
        def big():
            return jsonify({"ok": True})

        application.test_client().post("/api/big", data=b"x" * 100)
        chain.flush()
        assert _flat(chain.entries()[0])["body_hash"] == "(skipped:large)"

    def test_multipart_body_skipped(self, facade, chain):
        application = Flask("mp")
        install_flask_audit(application, UIAuditRecorder(facade=facade))

        @application.route("/api/upload", methods=["POST"])
        def upload():
            return jsonify({"ok": True})

        data = {"file": (io.BytesIO(b"binary"), "a.txt")}
        application.test_client().post("/api/upload", data=data,
                                       content_type="multipart/form-data")
        chain.flush()
        assert _flat(chain.entries()[0])["body_hash"] == "(skipped:multipart)"

    def test_query_keys_recorded_without_values(self, app, chain):
        app["app"].test_client().post("/api/plain?skill_id=abc&token=sk-test-Q")
        facts = _flat(self._entries(chain)[0])
        assert sorted(facts["query_keys"]) == ["skill_id", "token"]
        assert "sk-test-Q" not in json.dumps(facts, ensure_ascii=False)

    def test_recorder_counters(self, app, chain):
        client = app["app"].test_client()
        client.post("/api/plain")
        client.get("/api/thing/x")
        assert app["recorder"].recorded_count >= 1
        assert app["recorder"].skipped_count >= 1

    def test_install_is_idempotent(self, facade):
        application = Flask("twice")
        rec = install_flask_audit(application, UIAuditRecorder(facade=facade))
        rec2 = install_flask_audit(application, rec)
        assert rec2 is rec and rec2.registered is True


# ════════════════════════════════════════════════════════════
#  4. 装饰器离线可用性
# ════════════════════════════════════════════════════════════


class TestDecoratorOffline:
    def test_audit_action_works_without_request_context(self, chain, facade):
        previous = facade_mod.audit.bind(chain)
        try:
            @audit_action("offline.act", subject="offline-subject")
            def handler():
                return "ok"

            assert handler() == "ok"
            chain.flush()
            rows = chain.entries()
            assert rows and rows[0].action == "offline.act"
            assert rows[0].source == "ui"
            assert rows[0].subject == "offline-subject"
        finally:
            facade_mod.audit.bind(previous)

    def test_decorator_preserves_function_metadata(self):
        @audit_action("x.y")
        def my_handler():
            """doc"""

        assert my_handler.__name__ == "my_handler"
        assert my_handler.__doc__ == "doc"

    def test_decorator_returns_handler_result(self):
        @audit_action("x.y")
        def add(a, b):
            return a + b

        assert add(1, 2) == 3

    def test_decorator_records_exception_and_reraises(self, chain):
        previous = facade_mod.audit.bind(chain)
        try:
            @audit_action("boom.act", subject="boom")
            def boom():
                raise ValueError("nope")

            with pytest.raises(ValueError):
                boom()
            chain.flush()
            rows = chain.entries()
            assert rows[0].payload["status"] == "exception"
            assert rows[0].payload["error_type"] == "ValueError"
        finally:
            facade_mod.audit.bind(previous)

    def test_status_code_extraction_variants(self):
        from agent.audit.ui_middleware import _response_status_code

        class _Resp:
            status_code = 200

        assert _response_status_code(_Resp()) == 200
        assert _response_status_code(({"ok": False}, 400)) == 400
        assert _response_status_code(({"ok": False}, "500 Internal Server Error")) == 500
        assert _response_status_code("plain-body") == 0

    def test_ui_context_empty_outside_request(self):
        assert get_ui_context() == {}
