"""TASK-S4-01 UI 写路由身份层与 PII 收口单测（S2-02 #1 / #11 验收证据）

验收对应：
- **【S2-02 #1】** UI actor 不再**仅**靠 `ui:<remote_addr>` 降级：映射表命中即
  真实 actor（`identity_source=token_map`，权威度 authoritative）；
  未命中/表为空时既有降级链**逐字不变**（零回归）；
- **【S2-03 #13】** 审计（本模块）与埋点/审批共用同一 `identity_source` 口径；
- **【S2-02 #11】** 链上只写 `actor_ip_masked` + `actor_ip_hash`，
  **原始 IP 不再落盘**（既有实现写的是 `remote_addr` 原文）。
"""

from __future__ import annotations

import json

import pytest
from flask import Flask, jsonify

from agent.audit.chain import AuditChain, reset_audit_chains
from agent.audit.facade import AuditFacade
from agent.audit.ui_middleware import UIAuditRecorder, install_flask_audit, reset_ui_recorders
from agent.security.identity import TokenMap, set_token_map


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
def app(facade):
    application = Flask("s4_01_ui_identity_test")
    application.config["PROPAGATE_EXCEPTIONS"] = False
    install_flask_audit(application, UIAuditRecorder(facade=facade))

    @application.route("/api/write", methods=["POST"])
    def write():
        return jsonify({"ok": True})

    return application


def _flat(entry) -> dict:
    payload = entry.payload if isinstance(entry.payload, dict) else {}
    out = dict(payload)
    nested = payload.get("payload")
    if isinstance(nested, dict):
        out.update(nested)
    return out


def _last_write_entry(chain):
    entries = [e for e in chain.entries() if e.action.startswith("ui.")]
    assert entries, [e.action for e in chain.entries()]
    return entries[-1]


class TestTokenMapIdentityInUiAudit:
    def test_mapped_token_yields_authoritative_actor(self, app, chain):
        set_token_map(TokenMap("tokA:alice:team-1"))
        client = app.test_client()
        client.post("/api/write", headers={"Authorization": "Bearer tokA"})

        flat = _flat(_last_write_entry(chain))
        assert flat["identity_source"] == "token_map"
        assert flat["identity_tier"] == "authoritative"
        assert flat["identity_degraded"] is False
        assert flat["actor_type"] == "human"
        assert _last_write_entry(chain).actor == "alice"

    def test_empty_table_keeps_legacy_header_identity(self, app, chain):
        set_token_map(TokenMap(""))
        client = app.test_client()
        client.post("/api/write", headers={"X-Audit-Actor": "carol"})
        flat = _flat(_last_write_entry(chain))
        assert flat["identity_source"] == "header:X-Audit-Actor"
        assert flat["identity_tier"] == "degraded"
        assert flat["identity_degraded"] is True
        assert _last_write_entry(chain).actor == "carol"

    def test_empty_table_keeps_legacy_remote_addr_fallback(self, app, chain):
        set_token_map(TokenMap(""))
        app.test_client().post("/api/write")
        entry = _last_write_entry(chain)
        flat = _flat(entry)
        assert flat["identity_source"] == "remote_addr"
        assert entry.actor.startswith("ui:")


class TestIpPiiInUiAudit:
    def test_masked_and_hash_written_no_raw_ip(self, app, chain, s401_ip_key):
        set_token_map(TokenMap("tokA:alice"))
        app.test_client().post("/api/write",
                               headers={"Authorization": "Bearer tokA"})
        flat = _flat(_last_write_entry(chain))
        assert flat["actor_ip_masked"].startswith("127.0.")
        assert flat["actor_ip_hash"]
        assert flat["actor_ip_hash_status"] == "hmac_sha256"
        # **原始 IP 不落盘**（既有实现写过 `remote_addr` 原文）
        assert "remote_addr" not in flat
        assert "127.0.0.1" not in json.dumps(flat, ensure_ascii=False)

    def test_no_key_degrades_but_still_masks(self, app, chain):
        set_token_map(TokenMap(""))
        app.test_client().post("/api/write")
        flat = _flat(_last_write_entry(chain))
        assert flat["actor_ip_hash_status"] == "degraded_no_key"
        assert "actor_ip_hash" not in flat
        assert "127.0.0.1" not in json.dumps(flat, ensure_ascii=False)

    def test_identity_facts_helper_matches_chain(self, app, chain, s401_ip_key):
        """口径一致性：`identity_facts` 与包装层写出**同源同值**的叶子字段"""
        from agent.audit.ui_middleware import identity_facts

        # 用同一 IP 分别经两条路径计算（测试客户端的 remote_addr 固定为 127.0.0.1）
        expected = identity_facts(headers={"X-Audit-Actor": "carol"},
                                  remote_addr="127.0.0.1")
        set_token_map(TokenMap(""))
        app.test_client().post("/api/write", headers={"X-Audit-Actor": "carol"})
        flat = _flat(_last_write_entry(chain))
        for key in ("identity_source", "identity_tier", "identity_degraded",
                    "actor_type", "actor_ip_masked", "actor_ip_hash"):
            assert flat[key] == expected[key], key
