"""TASK-S4-01 审批 HTTP 面端到端单测（`agent/server_routes/routes_approval.py`）

用 Flask test client 走完整安全链（会话 → CSRF → 链接 → 二次认证 → §7.0 矩阵），
覆盖验收项：
- 分享式链接失效（换会话）/ 链接时效 ≤15min / 链接一次性；
- destructive 审批无二次认证不可通过；
- auto(skill) / sub_agent 不进审批面（开会话即被矩阵拒绝）；
- 前端审批按钮区 DOM 隔离 + CSRF 头；
- 原始 IP 不落盘（响应与记录中只有掩码/HMAC）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask

import agent.server_auth as sa
from agent.security import approval_session as S
from agent.security.identity import TokenMap, reset_identity, set_token_map
from agent.server_routes import routes_approval as R
from agent.skills_mgmt.approval import ApprovalFlow


# ════════════════════════════════════════════════════════════════
#  fixtures
# ════════════════════════════════════════════════════════════════


class _Clock:
    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def _no_shared_token(monkeypatch):
    """共享令牌旁路（既有语义：未配置 ⇒ 不校验）；身份由映射表提供"""
    monkeypatch.setattr(sa, "_API_TOKEN_ENABLED", False)


@pytest.fixture
def clock():
    return _Clock()


@pytest.fixture
def store(clock):
    store = S.ApprovalSessionStore(clock=clock)
    previous = S.set_session_store(store)
    yield store
    S.set_session_store(previous)


@pytest.fixture
def flow(tmp_path):
    return ApprovalFlow(records_path=str(tmp_path / "approvals.jsonl"))


@pytest.fixture
def app(store, flow):
    previous = R.set_approval_flow(flow)
    repo_templates = str(Path(__file__).resolve().parents[2] / "templates")
    flask_app = Flask(__name__, template_folder=repo_templates)
    flask_app.config["TESTING"] = True
    R.register_routes(flask_app)
    yield flask_app
    R.set_approval_flow(previous)


def _client(app):
    return app.test_client()


def _token_map(entries: str):
    set_token_map(TokenMap(entries))


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _csrf(client) -> str:
    cookie = client.get_cookie(S.SESSION_COOKIE_NAME)
    assert cookie is not None
    csrf = client.get_cookie("cp_approval_csrf")
    assert csrf is not None
    return csrf.value


def _open(client, token="tokA"):
    response = client.post("/api/approval/session", headers=_auth(token))
    return response


def _issue_link(client, token, record_id):
    return client.post("/api/approval/link", headers={**_auth(token),
                                                     S.CSRF_HEADER_NAME: _csrf(client)},
                       json={"record_id": record_id})


# ════════════════════════════════════════════════════════════════
#  身份自述与会话
# ════════════════════════════════════════════════════════════════


class TestWhoamiAndSession:
    def test_whoami_uses_token_map(self, app):
        _token_map("tokA:alice")
        response = _client(app).get("/api/approval/whoami", headers=_auth("tokA"))
        body = response.get_json()
        assert response.status_code == 200
        assert body["actor"] == "alice"
        assert body["identity_source"] == "token_map"
        assert body["identity_degraded"] is False
        assert body["is_human"] is True

    def test_whoami_marks_degraded_without_token_map(self, app):
        """映射表为空 ⇒ 既有降级链（如实标注 degraded，不臆造用户名）"""
        set_token_map(TokenMap(""))
        body = _client(app).get("/api/approval/whoami").get_json()
        assert body["identity_source"] == "remote_addr"
        assert body["identity_degraded"] is True
        assert body["actor"].startswith("ui:")
        # 既有头身份仍可用（S2-02 口径未变）
        headed = _client(app).get("/api/approval/whoami",
                                  headers={"X-Audit-Actor": "carol"}).get_json()
        assert headed["actor"] == "carol"
        assert headed["identity_source"] == "header:X-Audit-Actor"

    def test_open_session_sets_cookies(self, app):
        _token_map("tokA:alice")
        client = _client(app)
        response = _open(client)
        assert response.status_code == 200
        assert response.get_json()["session"]["actor"] == "alice"
        assert client.get_cookie(S.SESSION_COOKIE_NAME) is not None
        assert client.get_cookie("cp_approval_csrf") is not None

    def test_session_close_invalidates(self, app):
        _token_map("tokA:alice")
        client = _client(app)
        _open(client)
        assert client.delete("/api/approval/session",
                             headers=_auth("tokA")).status_code == 200
        body = client.get("/api/approval/whoami", headers=_auth("tokA")).get_json()
        assert body["session"] is None

    def test_auto_identity_cannot_open_approval_panel(self, app):
        """§7.0：auto 仅自身 scope / sub_agent ❌ ⇒ 开审批面板即被拒"""
        _token_map("tokAuto:auto:evolver::auto")
        client = _client(app)
        response = client.post("/api/approval/session", headers=_auth("tokAuto"))
        assert response.status_code == 403
        body = response.get_json()
        assert body["ok"] is False
        assert body["decision"]["denied_by_matrix"] is True

    def test_sub_agent_identity_cannot_open_panel(self, app):
        _token_map("tokSub:sub_agent:x::sub_agent")
        response = _client(app).post("/api/approval/session", headers=_auth("tokSub"))
        assert response.status_code == 403


# ════════════════════════════════════════════════════════════════
#  待审批清单
# ════════════════════════════════════════════════════════════════


class TestPendingList:
    def test_human_sees_pending_with_governance_fields(self, app, flow):
        _token_map("tokA:alice")
        flow.submit("stage.promote", "cap-1", action="promote", actor="owner",
                    payload={"undo_hint": "回滚至 shadow", "taint": True})
        client = _client(app)
        _open(client)
        body = client.get("/api/approval/pending", headers=_auth("tokA")).get_json()
        assert body["ok"] is True and body["count"] == 1
        item = body["items"][0]
        assert item["object_type"] == "stage.promote"
        assert item["undo_hint"] == "回滚至 shadow"
        assert item["undo_hint_status"] == "resolved"
        assert item["taint"] is True          # TaintBadge 数据源

    def test_degraded_identity_can_still_view_panel(self, app, flow):
        """硬约束 4：映射表为空不得导致后台无法审批（降级身份仍是 human）"""
        set_token_map(TokenMap(""))
        flow.submit("skill", "s-1", action="params_submit")
        body = _client(app).get("/api/approval/pending").get_json()
        assert body["ok"] is True and body["count"] == 1
        assert body["identity"]["degraded"] is True


# ════════════════════════════════════════════════════════════════
#  链接：CSRF / 会话绑定 / 时效
# ════════════════════════════════════════════════════════════════


class TestLinkEndpoint:
    def test_issue_link_requires_csrf(self, app, flow):
        _token_map("tokA:alice")
        record = flow.submit("skill", "s-1", action="params_submit")
        client = _client(app)
        _open(client)
        response = client.post("/api/approval/link", headers=_auth("tokA"),
                               json={"record_id": record.record_id})
        assert response.status_code == 403
        assert response.get_json()["code"] == S.CHECK_CSRF_MISMATCH

    def test_issue_link_ok_with_csrf(self, app, flow):
        _token_map("tokA:alice")
        record = flow.submit("skill", "s-1", action="params_submit")
        client = _client(app)
        _open(client)
        response = _issue_link(client, "tokA", record.record_id)
        assert response.status_code == 200
        link = response.get_json()["link"]
        assert link["record_id"] == record.record_id
        assert link["expires_in"] <= 900

    def test_issue_link_requires_session(self, app, flow):
        _token_map("tokA:alice")
        record = flow.submit("skill", "s-1", action="params_submit")
        response = _client(app).post("/api/approval/link", headers=_auth("tokA"),
                                     json={"record_id": record.record_id})
        assert response.status_code == 401

    def test_issue_link_unknown_record(self, app):
        _token_map("tokA:alice")
        client = _client(app)
        _open(client)
        response = _issue_link(client, "tokA", "appr-nope")
        assert response.status_code == 404

    def test_inspect_link_detects_shared_link(self, app, flow):
        """把链接拿给另一个会话打开 ⇒ `session_mismatch`"""
        _token_map("tokA:alice,tokB:bob")
        record = flow.submit("skill", "s-1", action="params_submit")
        alice = _client(app)
        _open(alice, "tokA")
        token = _issue_link(alice, "tokA", record.record_id).get_json()["link"]["token"]

        bob = _client(app)
        _open(bob, "tokB")
        body = bob.get(f"/api/approval/link/{token}?record_id={record.record_id}",
                       headers=_auth("tokB")).get_json()
        assert body["ok"] is False
        assert body["code"] == S.CHECK_SESSION_MISMATCH

    def test_link_expires_after_ttl(self, app, flow, clock):
        _token_map("tokA:alice")
        record = flow.submit("skill", "s-1", action="params_submit")
        client = _client(app)
        _open(client)
        token = _issue_link(client, "tokA", record.record_id).get_json()["link"]["token"]
        clock.advance(901)
        body = client.get(f"/api/approval/link/{token}?record_id={record.record_id}",
                          headers=_auth("tokA")).get_json()
        assert body["ok"] is False
        assert body["code"] == S.CHECK_EXPIRED

    def test_link_inspection_records_violation(self, app, flow, s401_audit_chain,
                                              s401_events_dir):
        _token_map("tokA:alice,tokB:bob")
        record = flow.submit("skill", "s-1", action="params_submit")
        alice = _client(app)
        _open(alice, "tokA")
        token = _issue_link(alice, "tokA", record.record_id).get_json()["link"]["token"]
        bob = _client(app)
        _open(bob, "tokB")
        bob.get(f"/api/approval/link/{token}?record_id={record.record_id}",
                headers=_auth("tokB"))
        actions = [e.action for e in s401_audit_chain.entries()]
        assert actions.count("policy.denied") == 1


# ════════════════════════════════════════════════════════════════
#  审批动作
# ════════════════════════════════════════════════════════════════


class TestDecisionEndpoints:
    def test_approve_happy_path(self, app, flow):
        _token_map("tokA:alice")
        record = flow.submit("stage.promote", "cap-1", action="promote",
                             actor="owner")
        client = _client(app)
        _open(client)
        token = _issue_link(client, "tokA", record.record_id).get_json()["link"]["token"]
        response = client.post(f"/api/approval/{record.record_id}/approve",
                              headers={**_auth("tokA"),
                                       S.CSRF_HEADER_NAME: _csrf(client)},
                              json={"link_token": token, "note": "人工核对通过"})
        assert response.status_code == 200
        body = response.get_json()
        assert body["record"]["state"] == "approved"
        assert body["record"]["actor"] == "alice"
        assert body["record"]["identity_source"] == "token_map"
        assert flow.get(record.record_id).state == "approved"

    def test_approve_requires_csrf(self, app, flow):
        _token_map("tokA:alice")
        record = flow.submit("skill", "s-1", action="params_submit")
        client = _client(app)
        _open(client)
        token = _issue_link(client, "tokA", record.record_id).get_json()["link"]["token"]
        response = client.post(f"/api/approval/{record.record_id}/approve",
                               headers=_auth("tokA"),
                               json={"link_token": token, "note": ""})
        assert response.status_code == 403
        assert flow.get(record.record_id).state == "pending_review"

    def test_approve_requires_link(self, app, flow):
        """缺少链接凭据 ⇒ 401（未持有审批链接），且状态不变"""
        _token_map("tokA:alice")
        record = flow.submit("skill", "s-1", action="params_submit")
        client = _client(app)
        _open(client)
        response = client.post(f"/api/approval/{record.record_id}/approve",
                               headers={**_auth("tokA"),
                                        S.CSRF_HEADER_NAME: _csrf(client)},
                               json={"note": ""})
        assert response.status_code == 401
        assert response.get_json()["code"] == S.CHECK_UNKNOWN
        assert flow.get(record.record_id).state == "pending_review"

    def test_shared_link_cannot_approve(self, app, flow):
        _token_map("tokA:alice,tokB:bob")
        record = flow.submit("skill", "s-1", action="params_submit")
        alice = _client(app)
        _open(alice, "tokA")
        token = _issue_link(alice, "tokA", record.record_id).get_json()["link"]["token"]

        bob = _client(app)
        _open(bob, "tokB")
        response = bob.post(f"/api/approval/{record.record_id}/approve",
                            headers={**_auth("tokB"),
                                     S.CSRF_HEADER_NAME: _csrf(bob)},
                            json={"link_token": token, "note": ""})
        assert response.status_code == 403
        assert response.get_json()["code"] == S.CHECK_SESSION_MISMATCH
        assert flow.get(record.record_id).state == "pending_review"

    def test_link_is_single_use(self, app, flow):
        _token_map("tokA:alice")
        record = flow.submit("skill", "s-1", action="params_submit")
        client = _client(app)
        _open(client)
        token = _issue_link(client, "tokA", record.record_id).get_json()["link"]["token"]
        first = client.post(f"/api/approval/{record.record_id}/approve",
                            headers={**_auth("tokA"),
                                     S.CSRF_HEADER_NAME: _csrf(client)},
                            json={"link_token": token, "note": ""})
        assert first.status_code == 200
        # 重放同一 token：记录已 approved，先被链接一次性拦下
        second = client.post(f"/api/approval/{record.record_id}/approve",
                             headers={**_auth("tokA"),
                                      S.CSRF_HEADER_NAME: _csrf(client)},
                             json={"link_token": token, "note": ""})
        assert second.status_code == 403
        assert second.get_json()["code"] == S.CHECK_ALREADY_USED

    def test_reject_requires_reason(self, app, flow):
        _token_map("tokA:alice")
        record = flow.submit("skill", "s-1", action="params_submit")
        client = _client(app)
        _open(client)
        token = _issue_link(client, "tokA", record.record_id).get_json()["link"]["token"]
        response = client.post(f"/api/approval/{record.record_id}/reject",
                               headers={**_auth("tokA"),
                                        S.CSRF_HEADER_NAME: _csrf(client)},
                               json={"link_token": token, "note": ""})
        assert response.status_code == 400
        assert response.get_json()["code"] == "reason_required"

    def test_reject_happy_path(self, app, flow):
        _token_map("tokA:alice")
        record = flow.submit("skill", "s-1", action="params_submit")
        client = _client(app)
        _open(client)
        token = _issue_link(client, "tokA", record.record_id).get_json()["link"]["token"]
        response = client.post(f"/api/approval/{record.record_id}/reject",
                               headers={**_auth("tokA"),
                                        S.CSRF_HEADER_NAME: _csrf(client)},
                               json={"link_token": token, "note": "证据不足"})
        assert response.status_code == 200
        assert response.get_json()["record"]["state"] == "rejected"

    def test_unknown_record_link_refused(self, app, flow):
        _token_map("tokA:alice")
        client = _client(app)
        _open(client)
        response = client.post("/api/approval/appr-nope/approve",
                               headers={**_auth("tokA"),
                                        S.CSRF_HEADER_NAME: _csrf(client)},
                               json={"link_token": "bogus", "note": ""})
        assert response.status_code == 401
        assert response.get_json()["code"] == S.CHECK_UNKNOWN

    def test_auto_identity_denied_at_decision_stage(self, app, flow):
        """auto 即使拿到链接也无法审批（矩阵单表校验，前端不拥有额外权限）"""
        _token_map("tokA:alice,tokAuto:auto:evolver::auto")
        record = flow.submit("skill", "s-1", action="params_submit")
        alice = _client(app)
        _open(alice, "tokA")
        token = _issue_link(alice, "tokA", record.record_id).get_json()["link"]["token"]

        auto = _client(app)
        auto.post("/api/approval/session", headers=_auth("tokAuto"))   # 会被拒（无会话）
        response = auto.post(f"/api/approval/{record.record_id}/approve",
                             headers={**_auth("tokAuto"),
                                      S.CSRF_HEADER_NAME: "whatever"},
                             json={"link_token": token, "note": ""})
        assert response.status_code in (401, 403)
        assert flow.get(record.record_id).state == "pending_review"

    def test_degraded_identity_blocked_when_require_authoritative(
            self, app, flow, monkeypatch):
        monkeypatch.setenv("CP_APPROVAL_REQUIRE_AUTHORITATIVE", "1")
        set_token_map(TokenMap(""))
        record = flow.submit("skill", "s-1", action="params_submit")
        client = _client(app)
        _open(client)
        token = _issue_link(client, "tokA", record.record_id).get_json()["link"]["token"]
        response = client.post(f"/api/approval/{record.record_id}/approve",
                               headers={S.CSRF_HEADER_NAME: _csrf(client)},
                               json={"link_token": token, "note": ""})
        assert response.status_code == 403
        assert response.get_json()["code"] == "identity_degraded"

    def test_raw_ip_not_in_response_or_records(self, app, flow, s401_ip_key):
        _token_map("tokA:alice")
        record = flow.submit("skill", "s-1", action="params_submit")
        client = _client(app)
        _open(client)
        token = _issue_link(client, "tokA", record.record_id).get_json()["link"]["token"]
        response = client.post(f"/api/approval/{record.record_id}/approve",
                               headers={**_auth("tokA"),
                                        S.CSRF_HEADER_NAME: _csrf(client)},
                               json={"link_token": token, "note": ""})
        assert response.status_code == 200
        dumped = json.dumps(response.get_json(), ensure_ascii=False)
        assert "127.0.0.1" not in dumped
        assert response.get_json()["pii"]["actor_ip_masked"].startswith("127.0.")
        raw = flow._records_path.read_text(encoding="utf-8")
        assert "127.0.0.1" not in raw


# ════════════════════════════════════════════════════════════════
#  destructive 二次认证
# ════════════════════════════════════════════════════════════════


class TestDestructiveSecondFactor:
    @pytest.fixture
    def destructive_record(self, flow):
        return flow.submit("skill", "s-destructive", action="params_submit",
                           payload={"risk_level": "destructive"})

    def test_link_marks_second_factor_required(self, app, flow, destructive_record):
        _token_map("tokA:alice")
        client = _client(app)
        _open(client)
        body = _issue_link(client, "tokA", destructive_record.record_id).get_json()
        assert body["link"]["requires_second_factor"] is True
        assert body["link"]["destructive"] is True

    def test_approve_without_second_factor_refused(self, app, flow,
                                                   destructive_record):
        _token_map("tokA:alice")
        client = _client(app)
        _open(client)
        token = _issue_link(client, "tokA",
                            destructive_record.record_id).get_json()["link"]["token"]
        response = client.post(f"/api/approval/{destructive_record.record_id}/approve",
                               headers={**_auth("tokA"),
                                        S.CSRF_HEADER_NAME: _csrf(client)},
                               json={"link_token": token, "note": ""})
        assert response.status_code == 403
        assert response.get_json()["code"] == S.CHECK_SECOND_FACTOR_REQUIRED
        assert flow.get(destructive_record.record_id).state == "pending_review"

    def test_approve_with_second_factor_code_succeeds(self, app, flow,
                                                      destructive_record):
        _token_map("tokA:alice")
        client = _client(app)
        _open(client)
        token = _issue_link(client, "tokA",
                            destructive_record.record_id).get_json()["link"]["token"]
        code = client.post("/api/approval/second-factor",
                           headers={**_auth("tokA"),
                                    S.CSRF_HEADER_NAME: _csrf(client)},
                           json={"record_id": destructive_record.record_id}
                           ).get_json()["code"]
        response = client.post(f"/api/approval/{destructive_record.record_id}/approve",
                               headers={**_auth("tokA"),
                                        S.CSRF_HEADER_NAME: _csrf(client)},
                               json={"link_token": token, "note": "",
                                     "second_factor": code})
        assert response.status_code == 200
        assert response.get_json()["second_factor_ok"] is True
        assert flow.get(destructive_record.record_id).state == "approved"

    def test_wrong_second_factor_code_refused(self, app, flow, destructive_record):
        _token_map("tokA:alice")
        client = _client(app)
        _open(client)
        token = _issue_link(client, "tokA",
                            destructive_record.record_id).get_json()["link"]["token"]
        client.post("/api/approval/second-factor",
                    headers={**_auth("tokA"), S.CSRF_HEADER_NAME: _csrf(client)},
                    json={"record_id": destructive_record.record_id})
        response = client.post(f"/api/approval/{destructive_record.record_id}/approve",
                               headers={**_auth("tokA"),
                                        S.CSRF_HEADER_NAME: _csrf(client)},
                               json={"link_token": token, "note": "",
                                     "second_factor": "000000"})
        assert response.status_code == 403
        assert flow.get(destructive_record.record_id).state == "pending_review"

    def test_second_factor_endpoint_needs_session(self, app, destructive_record):
        _token_map("tokA:alice")
        response = _client(app).post(
            "/api/approval/second-factor", headers=_auth("tokA"),
            json={"record_id": destructive_record.record_id})
        assert response.status_code == 401


# ════════════════════════════════════════════════════════════════
#  前端控制台
# ════════════════════════════════════════════════════════════════


class TestConsole:
    def test_console_serves_dom_isolated_shell(self, app):
        response = _client(app).get("/api/approval/console")
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert "approval_console.css" in html
        assert "approval_console.js" in html
        assert "cp-approval-console-host" in html
        assert "Shadow DOM" in html
