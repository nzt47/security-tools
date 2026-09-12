"""TASK-S7-01 开关中心 HTTP 面单测（三组路由 + 双人确认端点）

【守护的验收项】
    - `GET /api/cp/settings` 返回全部条目（元数据 + 当前值 + **生效来源** +
      是否被覆盖 + 置灰原因），C 级不含明文；
    - `POST /api/cp/settings/<key>`：A 直接生效 / B 二次认证 + 双人确认 / C 403；
    - `POST /api/cp/settings/<key>/reset`：清除覆盖层；
    - 被 env 锁定的项在**响应里**就是 `editable=false` + 可读原因（UI 据此置灰）；
    - **没有批量端点**（数组体 / keys 体一律 400）；
    - auto / sub_agent 一律被拒（矩阵）；
    - 全部路由带 `@require_token`（令牌启用时无令牌 → 401）；
    - 变更入链式审计且 `verify_chain` 仍通过。
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib

import pytest
from flask import Flask

from agent.audit import facade as facade_mod
from agent.audit.chain import AuditChain
from agent.settings import masking
from agent.settings.overrides import reset_override_store
from agent.settings.service import reset_pending, reset_settings_service

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PROTECTED = (REPO_ROOT / ".env", REPO_ROOT / "config.yaml")
B_KEY = "CP_BUDGET_BRAKE_ENABLED"


def _digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


@pytest.fixture(autouse=True)
def isolated_audit(tmp_path):
    chain = AuditChain(str(tmp_path / "audit.db"),
                       roots_path=str(tmp_path / "roots.jsonl"),
                       signing_key_path=str(tmp_path / "k.pem"), auto_seal=False)
    previous = facade_mod.audit.bind(chain)
    facade_mod.audit.enabled = True
    facade_mod.audit.reset_counters()
    yield chain
    facade_mod.audit.bind(previous)
    try:
        chain.close(timeout=2.0)
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    snapshot = dict(os.environ)
    monkeypatch.setenv("CP_UI_SETTINGS_PATH", str(tmp_path / "ui_settings.json"))
    monkeypatch.setenv("CP_EVENTS_DIR", str(tmp_path / "events"))
    monkeypatch.setenv("CP_APPROVAL_SECOND_FACTOR_CODE", "unit-2fa-code")
    for name in ("LOCK_PROFILE", B_KEY, "SMTP_PASSWORD", "LOCK_WATCHDOG_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    import agent.observability.events as events_mod
    events_mod.reset_event_stores()
    reset_override_store()
    reset_settings_service()
    before = {str(p): _digest(p) for p in PROTECTED}
    yield tmp_path
    os.environ.clear()
    os.environ.update(snapshot)
    reset_override_store()
    reset_settings_service()
    reset_pending()
    events_mod.reset_event_stores()
    assert {str(p): _digest(p) for p in PROTECTED} == before, "受保护配置文件被改动"


@pytest.fixture()
def routes():
    from agent.server_routes import routes_settings as RS
    app = Flask(__name__)
    RS.register_routes(app, None)
    app.config["TESTING"] = True
    return app.test_client(), RS


def _force_actor(monkeypatch, module, *, actor: str, actor_type: str = "human",
                 session_id: str = "sess-1") -> None:
    """把路由侧身份固定为指定执行体（请求体声明本来就不被采信）"""
    from agent.security import approval_guard as guard

    monkeypatch.setattr(module, "_identity_fields", lambda: {
        "actor": actor, "actor_type": actor_type,
        "session_id": session_id, "identity_source": "test"})

    def _ctx():
        return guard.ActorContext(actor=actor, actor_type=actor_type,
                                  identity_source="test", scope="",
                                  session_id=session_id, actor_ip="127.0.0.1")

    monkeypatch.setattr(module, "_actor_ctx", _ctx)


def _open_session(client, *, actor: str = "owner") -> str:
    """开一个审批会话并把 cookie 挂到测试客户端（二次认证要会话绑定）"""
    from agent.security import approval_session as session_mod
    session = session_mod.get_session_store().open_session(
        actor=actor, actor_type="human", identity_source="test")
    client.set_cookie(session_mod.SESSION_COOKIE_NAME, session.session_id)
    return session.session_id


# ════════════════════════════════════════════════════════════
#  一、GET /api/cp/settings
# ════════════════════════════════════════════════════════════


class TestIndexEndpoint:
    def test_returns_full_registry_with_sources(self, routes, monkeypatch):
        client, RS = routes
        _force_actor(monkeypatch, RS, actor="owner")
        resp = client.get("/api/cp/settings")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["source_priority"] == ["env", "ui_override", "config", "default"]
        assert body["counts"]["total"] == len(body["items"])
        assert body["registry_size"] == len(body["items"])
        assert len(body["categories"]) == 6
        assert body["read_only_notice"]
        assert body["panel"]["datasources"]

    def test_every_item_carries_display_metadata(self, routes, monkeypatch):
        client, RS = routes
        _force_actor(monkeypatch, RS, actor="owner")
        items = client.get("/api/cp/settings").get_json()["items"]
        for item in items:
            assert item["description"].strip()
            assert item["risk"] in ("A", "B", "C")
            assert item["category_label"]
            assert item["source"] in ("env", "ui_override", "config", "default")
            assert item["source_label"]
            assert item["effect"] in ("hot", "needs_restart", "next_task")
            assert item["effect_label"]
            assert item["locked"] == (not item["editable"])
            if item["locked"]:
                assert item["locked_reason"].strip()

    def test_env_locked_item_is_greyed_with_reason(self, routes, monkeypatch):
        client, RS = routes
        _force_actor(monkeypatch, RS, actor="owner")
        monkeypatch.setenv("LOCK_PROFILE", "1")
        items = {i["key"]: i for i in
                 client.get("/api/cp/settings").get_json()["items"]}
        item = items["LOCK_PROFILE"]
        assert item["source"] == "env"
        assert item["editable"] is False and item["locked"] is True
        assert "LOCK_PROFILE" in item["locked_reason"]
        assert item["env_present"] is True

    def test_counts_disclose_lock_and_override_numbers(self, routes, monkeypatch):
        client, RS = routes
        _force_actor(monkeypatch, RS, actor="owner")
        monkeypatch.setenv("LOCK_PROFILE", "1")
        client.post("/api/cp/settings/LOCK_PROFILE_BATCH", json={"value": 700})
        body = client.get("/api/cp/settings").get_json()
        assert body["counts"]["locked_by_env"] >= 1
        assert body["counts"]["overridden"] >= 1
        assert body["counts"]["secret"] >= 1
        assert body["counts"]["env_only"] >= 1

    def test_secret_values_never_in_response(self, routes, monkeypatch):
        client, RS = routes
        _force_actor(monkeypatch, RS, actor="owner")
        secret = "sk-live-abcdef0123456789"
        monkeypatch.setenv("LLM_API_KEY", secret)
        raw = client.get("/api/cp/settings").get_data(as_text=True)
        assert secret not in raw
        items = json.loads(raw)["items"]
        item = next(i for i in items if i["key"] == "LLM_API_KEY")
        assert item["value"] is None
        assert item["masked"] is True
        assert item["configured"] is True
        assert item["fingerprint"] == masking.fingerprint(secret)

    def test_sub_agent_cannot_read_panel(self, routes, monkeypatch):
        client, RS = routes
        _force_actor(monkeypatch, RS, actor="sub-1", actor_type="sub_agent")
        resp = client.get("/api/cp/settings")
        assert resp.status_code == 403
        assert resp.get_json()["code"] == "panel_denied"

    def test_response_is_no_store(self, routes, monkeypatch):
        client, RS = routes
        _force_actor(monkeypatch, RS, actor="owner")
        resp = client.get("/api/cp/settings")
        assert resp.headers["Cache-Control"] == "no-store"


# ════════════════════════════════════════════════════════════
#  二、POST 改值
# ════════════════════════════════════════════════════════════


class TestChangeEndpoint:
    def test_a_level_applies_hot(self, routes, monkeypatch, isolated_audit):
        client, RS = routes
        _force_actor(monkeypatch, RS, actor="owner")
        resp = client.post("/api/cp/settings/LOCK_PROFILE", json={"value": True})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] and body["applied"] is True
        assert body["source"] == "ui_override"
        assert body["effect"] == "hot"
        assert body["receipt"]["never_touched"] == [".env", "config.yaml"]
        assert os.environ.get("LOCK_PROFILE") == "true"
        assert isolated_audit.verify_chain().ok is True

    def test_missing_value_is_400(self, routes, monkeypatch):
        client, RS = routes
        _force_actor(monkeypatch, RS, actor="owner")
        resp = client.post("/api/cp/settings/LOCK_PROFILE", json={"reason": "x"})
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "missing_value"

    def test_unknown_key_is_404(self, routes, monkeypatch):
        client, RS = routes
        _force_actor(monkeypatch, RS, actor="owner")
        resp = client.post("/api/cp/settings/NOPE_SWITCH", json={"value": True})
        assert resp.status_code == 404
        assert resp.get_json()["code"] == "unknown_key"

    def test_c_level_is_403_without_plaintext(self, routes, monkeypatch):
        client, RS = routes
        _force_actor(monkeypatch, RS, actor="owner")
        secret = "smtp-pass-9999"
        monkeypatch.setenv("SMTP_PASSWORD", secret)
        resp = client.post("/api/cp/settings/SMTP_PASSWORD",
                           json={"value": "new-secret-1111"})
        assert resp.status_code == 403
        raw = resp.get_data(as_text=True)
        assert resp.get_json()["code"] == "read_only_secret"
        assert secret not in raw and "new-secret-1111" not in raw

    def test_env_locked_is_403_with_reason(self, routes, monkeypatch):
        client, RS = routes
        _force_actor(monkeypatch, RS, actor="owner")
        monkeypatch.setenv("LOCK_PROFILE", "1")
        resp = client.post("/api/cp/settings/LOCK_PROFILE", json={"value": False})
        assert resp.status_code == 403
        assert resp.get_json()["code"] == "locked_by_env"
        assert "LOCK_PROFILE" in resp.get_json()["message"]

    def test_invalid_value_is_400(self, routes, monkeypatch):
        client, RS = routes
        _force_actor(monkeypatch, RS, actor="owner")
        resp = client.post("/api/cp/settings/LOCK_PROFILE_BATCH",
                           json={"value": "abc"})
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "invalid_value"

    def test_auto_actor_is_denied(self, routes, monkeypatch):
        client, RS = routes
        _force_actor(monkeypatch, RS, actor="auto:bot", actor_type="auto")
        resp = client.post("/api/cp/settings/LOCK_PROFILE", json={"value": True})
        assert resp.status_code == 403
        assert resp.get_json()["code"] == "settings_denied"
        assert os.environ.get("LOCK_PROFILE") is None

    @pytest.mark.parametrize("payload", [
        [{"key": "LOCK_PROFILE", "value": True}],
        {"keys": ["LOCK_PROFILE"], "value": True},
        {"items": [{"key": "LOCK_PROFILE"}]},
    ])
    def test_batch_submission_is_rejected(self, routes, monkeypatch, payload):
        """★ B 级不得被"批量提交"绕过：不存在可批量的入口"""
        client, RS = routes
        _force_actor(monkeypatch, RS, actor="owner")
        resp = client.post("/api/cp/settings/LOCK_PROFILE", json=payload)
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "batch_not_supported"
        assert not os.environ.get("LOCK_PROFILE")


# ════════════════════════════════════════════════════════════
#  三、B 级：二次认证 + 双人确认（HTTP 面）
# ════════════════════════════════════════════════════════════


class TestChangeEndpointLevelB:
    def test_without_session_is_403(self, routes, monkeypatch):
        client, RS = routes
        _force_actor(monkeypatch, RS, actor="owner")
        resp = client.post(f"/api/cp/settings/{B_KEY}", json={"value": True})
        assert resp.status_code == 403
        body = resp.get_json()
        assert body["code"] == "second_factor_required"
        assert "二次认证" in body["message"]
        assert os.environ.get(B_KEY) is None

    def test_with_wrong_code_is_403(self, routes, monkeypatch):
        client, RS = routes
        _force_actor(monkeypatch, RS, actor="owner")
        _open_session(client)
        resp = client.post(f"/api/cp/settings/{B_KEY}",
                           json={"value": True, "second_factor": "wrong"})
        assert resp.status_code == 403
        assert resp.get_json()["code"] == "second_factor_required"
        assert os.environ.get(B_KEY) is None

    def test_pending_then_dual_confirmation(self, routes, monkeypatch,
                                            isolated_audit):
        client, RS = routes
        _force_actor(monkeypatch, RS, actor="owner")
        _open_session(client, actor="owner")
        first = client.post(f"/api/cp/settings/{B_KEY}",
                            json={"value": True, "second_factor": "unit-2fa-code"})
        assert first.status_code == 202
        body = first.get_json()
        assert body["pending"] is True and body["pending_id"]
        assert body["requires_dual_approval"] is True
        assert os.environ.get(B_KEY) is None              # 首位提交不改变状态

        # 同一个人不能自我确认
        same = client.post(f"/api/cp/settings/{B_KEY}/confirm",
                           json={"pending_id": body["pending_id"],
                                 "second_factor": "unit-2fa-code"})
        assert same.status_code == 403
        assert same.get_json()["code"] == "second_approver_must_differ"

        # 第二位人工（另一个会话 + 独立二次认证）确认后才生效
        _force_actor(monkeypatch, RS, actor="reviewer", session_id="sess-2")
        _open_session(client, actor="reviewer")
        done = client.post(f"/api/cp/settings/{B_KEY}/confirm",
                           json={"pending_id": body["pending_id"],
                                 "second_factor": "unit-2fa-code",
                                 "reason": "复核通过"})
        assert done.status_code == 200
        payload = done.get_json()
        assert payload["ok"] and payload["applied"] is True
        assert payload["receipt"]["second_approver"] == "owner"
        assert os.environ.get(B_KEY) == "true"
        assert isolated_audit.verify_chain().ok is True

    def test_confirm_without_pending_id_is_400(self, routes, monkeypatch):
        client, RS = routes
        _force_actor(monkeypatch, RS, actor="owner")
        _open_session(client)
        resp = client.post(f"/api/cp/settings/{B_KEY}/confirm",
                           json={"second_factor": "unit-2fa-code"})
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "missing_pending_id"

    def test_confirm_unknown_pending_is_404(self, routes, monkeypatch):
        client, RS = routes
        _force_actor(monkeypatch, RS, actor="owner")
        _open_session(client)
        resp = client.post(f"/api/cp/settings/{B_KEY}/confirm",
                           json={"pending_id": "setp-nope",
                                 "second_factor": "unit-2fa-code"})
        assert resp.status_code == 404


# ════════════════════════════════════════════════════════════
#  四、reset
# ════════════════════════════════════════════════════════════


class TestResetEndpoint:
    def test_reset_clears_overlay(self, routes, monkeypatch):
        client, RS = routes
        _force_actor(monkeypatch, RS, actor="owner")
        client.post("/api/cp/settings/LOCK_PROFILE", json={"value": True})
        resp = client.post("/api/cp/settings/LOCK_PROFILE/reset", json={})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] and body["source"] == "default"
        assert "LOCK_PROFILE" not in os.environ

    def test_reset_env_locked_does_not_touch_env(self, routes, monkeypatch):
        client, RS = routes
        _force_actor(monkeypatch, RS, actor="owner")
        monkeypatch.setenv("LOCK_PROFILE", "1")
        resp = client.post("/api/cp/settings/LOCK_PROFILE/reset", json={})
        # 无覆盖层记录 → 视为无需重置；env 不被改动
        assert resp.status_code == 200
        assert os.environ["LOCK_PROFILE"] == "1"

    def test_reset_auto_actor_denied(self, routes, monkeypatch):
        client, RS = routes
        _force_actor(monkeypatch, RS, actor="auto:bot", actor_type="auto")
        resp = client.post("/api/cp/settings/LOCK_PROFILE/reset", json={})
        assert resp.status_code == 403


# ════════════════════════════════════════════════════════════
#  五、令牌与装饰器
# ════════════════════════════════════════════════════════════


class TestTokenGuard:
    def test_all_endpoints_require_token(self, routes):
        """★ 全部端点都带 `@require_token`（functools.wraps ⇒ 有 __wrapped__）"""
        client, _RS = routes
        app = client.application
        names = [n for n in app.view_functions if n.startswith("cp_settings")]
        assert sorted(names) == ["cp_settings_change", "cp_settings_confirm",
                                 "cp_settings_index", "cp_settings_reset"]
        for name in names:
            assert getattr(app.view_functions[name], "__wrapped__", None) is not None

    def test_401_when_token_enabled_and_missing(self, routes, monkeypatch):
        from agent import server_auth as SA
        client, RS = routes
        monkeypatch.setattr(SA, "_API_TOKEN_ENABLED", True)
        monkeypatch.setenv("FLASK_API_TOKEN", "unit-token")
        _force_actor(monkeypatch, RS, actor="owner")
        assert client.get("/api/cp/settings").status_code == 401
        ok = client.get("/api/cp/settings",
                        headers={"Authorization": "Bearer unit-token"})
        assert ok.status_code == 200
