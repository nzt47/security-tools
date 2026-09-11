"""TASK-S4-01 身份层与 PII 口径单测（裁定 A3 / 裁定 B）

验收对应：
- **【S2-02 #1 / S2-03 #13】** 令牌 → 用户映射落地：命中标 `identity_source=token_map`，
  未命中断言降级并标注 degraded；解析层可替换（P5 → A1/A2）；
- **映射表为空 → 完全回退既有行为**（新机制绝不导致后台无法审批）；
- **【S2-02 #11】** 认证 IP：掩码 + HMAC-SHA256 双字段入链，**原始 IP 不落盘**，
  同一 IP → 同哈希（可关联），无密钥 → 显式降级而非写原文。
"""

from __future__ import annotations

import json

import pytest

from agent.security import identity as ID
from agent.security import pii


# ════════════════════════════════════════════════════════════════
#  裁定 A3：令牌 → 用户映射
# ════════════════════════════════════════════════════════════════


class TestTokenMapParsing:
    def test_parse_plain_entries(self):
        tm = ID.TokenMap("tokA:alice,tokB:bob")
        assert len(tm) == 2
        assert tm.lookup("tokA").actor == "alice"
        assert tm.lookup("tokB").actor == "bob"
        assert tm.lookup("tokC") is None

    def test_parse_fingerprint_form_avoids_plaintext(self):
        import hashlib
        digest = hashlib.sha256(b"secret-token").hexdigest()
        tm = ID.TokenMap(f"sha256:{digest}:carol")
        assert len(tm) == 1
        assert tm.lookup("secret-token").actor == "carol"
        # 配置中不含令牌原文
        assert tm.entries[0].token is None

    def test_parse_scope_and_actor_type_extensions(self):
        tm = ID.TokenMap("tokA:alice:team-1:human,tokB:evolver:scope-2:auto")
        assert tm.lookup("tokA").actor == "alice"
        assert tm.lookup("tokA").scope == "team-1"
        assert tm.lookup("tokA").actor_type == "human"
        assert tm.lookup("tokB").actor == "evolver"
        assert tm.lookup("tokB").scope == "scope-2"
        assert tm.lookup("tokB").actor_type == "auto"

    def test_parse_multi_line_and_comments(self):
        tm = ID.TokenMap("# 注释\n\ntokA:alice\ntokB:bob\n")
        assert len(tm) == 2

    def test_invalid_lines_skipped_and_counted(self):
        tm = ID.TokenMap("garbage,tokA:alice,sha256:short:x,:noname")
        assert tm.lookup("tokA").actor == "alice"
        assert tm.invalid_lines >= 2

    def test_empty_table(self):
        tm = ID.TokenMap("")
        assert tm.empty is True
        assert tm.lookup("anything") is None

    def test_from_env_reads_env_table(self):
        tm = ID.TokenMap.from_env({"CP_UI_TOKENS": "tokA:alice"})
        assert tm.lookup("tokA").actor == "alice"

    def test_from_env_reads_file(self, tmp_path):
        path = tmp_path / "tokens.txt"
        path.write_text("# 令牌表\ntokA:alice\n", encoding="utf-8")
        tm = ID.TokenMap.from_env({"CP_UI_TOKENS_FILE": str(path)})
        assert tm.lookup("tokA").actor == "alice"

    def test_from_env_missing_file_falls_back_to_empty(self, tmp_path):
        tm = ID.TokenMap.from_env(
            {"CP_UI_TOKENS_FILE": str(tmp_path / "nope.txt")})
        assert tm.empty is True

    def test_multi_token_isolation(self):
        """多令牌：A 的令牌不得解析成 B 的用户"""
        tm = ID.TokenMap("tokA:alice,tokB:bob")
        assert tm.lookup("tokA").actor != tm.lookup("tokB").actor


class TestIdentityResolution:
    def test_token_map_hit_is_authoritative(self):
        ID.set_token_map(ID.TokenMap("tokA:alice:team-1"))
        got = ID.resolve_identity(token="tokA", remote_addr="10.0.0.7")
        assert got.actor == "alice"
        assert got.identity_source == ID.SRC_TOKEN_MAP
        assert got.scope == "team-1"
        assert got.degraded is False
        assert got.authority == ID.AUTHORITY_AUTHORITATIVE

    def test_token_map_hit_from_bearer_header(self):
        ID.set_token_map(ID.TokenMap("tokA:alice"))
        got = ID.resolve_identity(headers={"Authorization": "Bearer tokA"})
        assert (got.actor, got.identity_source) == ("alice", ID.SRC_TOKEN_MAP)

    def test_token_map_miss_degrades_and_marks(self):
        """未命中映射表 ⇒ 降级并**如实标注** degraded（裁定 A3 第 3 条）

        以未知令牌发起请求时，降级口径与既有 S2-02 一致：落到**令牌指纹**
        （`bearer_token_fingerprint`），仍然 degraded —— 不臆造用户名。
        """
        ID.set_token_map(ID.TokenMap("tokA:alice"))
        got = ID.resolve_identity(token="tokOTHER", remote_addr="10.0.0.7")
        assert got.actor == ID.token_fingerprint("tokOTHER")
        assert got.identity_source == ID.SRC_BEARER_FINGERPRINT
        assert got.degraded is True
        assert got.authority == ID.AUTHORITY_DEGRADED

    def test_token_map_miss_without_token_degrades_to_remote_addr(self):
        ID.set_token_map(ID.TokenMap("tokA:alice"))
        got = ID.resolve_identity(remote_addr="10.0.0.7")
        assert (got.actor, got.identity_source) == ("ui:10.0.0.7", ID.SRC_REMOTE_ADDR)
        assert got.degraded is True

    def test_empty_table_preserves_legacy_chain(self):
        """**硬约束 4**：映射表为空 ⇒ 既有降级链逐字不变"""
        ID.set_token_map(ID.TokenMap(""))
        first = ID.resolve_identity(headers={"X-Audit-Actor": "alice"})
        assert (first.actor, first.identity_source) == ("alice", "header:X-Audit-Actor")
        second = ID.resolve_identity(cookies={"user": "bob"})
        assert (second.actor, second.identity_source) == ("bob", "cookie:user")
        third = ID.resolve_identity(headers={"Authorization": "Bearer t"})
        assert (third.actor, third.identity_source) == (
            ID.token_fingerprint("t"), "bearer_token_fingerprint")
        fourth = ID.resolve_identity(remote_addr="10.0.0.9")
        assert (fourth.actor, fourth.identity_source) == ("ui:10.0.0.9", "remote_addr")
        fifth = ID.resolve_identity()
        assert (fifth.actor, fifth.identity_source) == ("ui:unknown",
                                                        "degraded_no_identity")

    def test_resolution_order_map_before_header(self):
        ID.set_token_map(ID.TokenMap("tokA:alice"))
        got = ID.resolve_identity(token="tokA",
                                  headers={"X-Audit-Actor": "spoofed"})
        assert got.actor == "alice"
        assert got.identity_source == ID.SRC_TOKEN_MAP

    def test_explicit_actor_wins_over_header(self):
        ID.set_token_map(ID.TokenMap(""))
        got = ID.resolve_identity(actor="router-known",
                                  headers={"X-Audit-Actor": "spoofed"})
        assert got.actor == "router-known"
        assert got.identity_source == ID.SRC_EXPLICIT

    def test_identity_audit_fields_unified(self):
        """S2-03 #13：审计与埋点共用同一口径字段"""
        ID.set_token_map(ID.TokenMap("tokA:alice"))
        fields = ID.resolve_identity(token="tokA").to_audit_fields()
        assert fields["identity_source"] == ID.SRC_TOKEN_MAP
        assert fields["identity_tier"] == ID.AUTHORITY_AUTHORITATIVE
        assert fields["identity_degraded"] is False
        assert fields["actor_type"] == "human"

    def test_actor_type_declared_in_mapping_table(self):
        ID.set_token_map(ID.TokenMap("tokA:skill-x:scope-1:auto"))
        got = ID.resolve_identity(token="tokA")
        assert got.actor == "skill-x"
        assert got.actor_type == "auto"
        assert got.scope == "scope-1"

    def test_declared_actor_type_falls_back_to_name_inference(self):
        ID.set_token_map(ID.TokenMap("tokA:reviewer"))
        assert ID.resolve_identity(token="tokA").actor_type == "human"
        ID.set_token_map(ID.TokenMap("tokB:sub_agent:42"))
        assert ID.resolve_identity(token="tokB").actor_type == "sub_agent"

    def test_token_fingerprint_never_leaks_token(self):
        fp = ID.token_fingerprint("super-secret-token")
        assert "super-secret-token" not in fp
        assert fp.startswith("tok_") and len(fp) == 16

    def test_resolver_is_replaceable(self):
        """裁定 A3 第 5 条：解析层可替换（P5 → A1/A2）"""

        class SessionResolver(ID.IdentityResolver):
            name = "session_a1"

            def resolve(self, **kwargs):
                return ID.ResolvedIdentity(
                    actor="session-user", identity_source=ID.SRC_SESSION,
                    actor_type="human", degraded=False,
                    authority=ID.AUTHORITY_AUTHORITATIVE)

        previous = ID.set_resolver(SessionResolver())
        try:
            got = ID.resolve_identity(remote_addr="10.0.0.1")
            assert got.actor == "session-user"
            assert got.identity_source == ID.SRC_SESSION
        finally:
            ID.set_resolver(previous)

    def test_resolver_failure_degrades_instead_of_raising(self):
        class Boom(ID.IdentityResolver):
            def resolve(self, **kwargs):
                raise RuntimeError("boom")

        previous = ID.set_resolver(Boom())
        try:
            got = ID.resolve_identity(remote_addr="10.0.0.1")
            assert got.identity_source == ID.SRC_NO_IDENTITY
            assert got.degraded is True
        finally:
            ID.set_resolver(previous)

    def test_env_configured_table_loaded_by_default(self, monkeypatch):
        monkeypatch.setenv("CP_UI_TOKENS", "tokEnv:envuser")
        ID.reset_identity()
        got = ID.resolve_identity(token="tokEnv")
        assert got.actor == "envuser"
        assert got.identity_source == ID.SRC_TOKEN_MAP

    def test_ui_middleware_uses_same_resolver(self):
        """S2-02 与 S2-03 收口：UI 审计身份解析走同一入口"""
        from agent.audit.ui_middleware import resolve_ui_actor
        ID.set_token_map(ID.TokenMap("tokA:alice"))
        assert resolve_ui_actor(headers={"Authorization": "Bearer tokA"}) == (
            "alice", ID.SRC_TOKEN_MAP)
        ID.set_token_map(ID.TokenMap(""))
        assert resolve_ui_actor(remote_addr="10.0.0.9") == (
            "ui:10.0.0.9", ID.SRC_REMOTE_ADDR)


# ════════════════════════════════════════════════════════════════
#  裁定 B：IP 掩码 + HMAC
# ════════════════════════════════════════════════════════════════


class TestIpPii:
    def test_mask_matches_repo_convention(self):
        assert pii.mask_ip("10.0.0.7") == "10.0.xxx.xxx"
        assert pii.mask_ip("192.168.1.42") == "192.168.xxx.xxx"
        # 与仓库既有实现逐值一致（单一口径来源）
        from agent.utils.sensitive_data_filter import mask_ip as repo_mask
        for ip in ("10.0.0.7", "172.16.5.5", "not-an-ip", ""):
            assert pii.mask_ip(ip) == repo_mask(ip)

    def test_hash_ip_is_hmac_not_bare_hash(self, s401_ip_key):
        digest = pii.hash_ip("10.0.0.7")
        import hashlib
        bare = hashlib.sha256(b"10.0.0.7").hexdigest()
        assert digest and digest != bare
        assert len(digest) == 64

    def test_same_ip_same_hash_allows_association(self, s401_ip_key):
        assert pii.hash_ip("10.0.0.7") == pii.hash_ip("10.0.0.7")
        assert pii.hash_ip("10.0.0.7") != pii.hash_ip("10.0.0.8")

    def test_different_key_different_hash(self):
        pii.set_hmac_key(b"key-one-aaaaaaaaaaaaaaaa")
        first = pii.hash_ip("10.0.0.7")
        pii.set_hmac_key(b"key-two-bbbbbbbbbbbbbbbb")
        second = pii.hash_ip("10.0.0.7")
        assert first != second

    def test_fields_have_no_raw_ip(self, s401_ip_key):
        fields = pii.ip_pii_fields("10.0.0.7")
        assert fields["actor_ip_masked"] == "10.0.xxx.xxx"
        assert fields["actor_ip_hash_status"] == pii.STATUS_HMAC
        assert "actor_ip_hash" in fields
        # 原始 IP 绝不出现在任何字段值/键中
        assert "10.0.0.7" not in json.dumps(fields, ensure_ascii=False)

    def test_no_key_degrades_without_raw_ip(self):
        pii.set_hmac_key(None)
        fields = pii.ip_pii_fields("10.0.0.7")
        assert fields["actor_ip_hash_status"] == pii.STATUS_DEGRADED
        assert "actor_ip_hash" not in fields       # 不写空哈希（避免下游误读）
        assert "10.0.0.7" not in json.dumps(fields, ensure_ascii=False)
        assert pii.hash_ip("10.0.0.7") is None      # **不退化**为裸哈希

    def test_no_ip_returns_no_ip_status(self, s401_ip_key):
        fields = pii.ip_pii_fields("")
        assert fields["actor_ip_present"] is False
        assert fields["actor_ip_hash_status"] == pii.STATUS_NO_IP
        assert "actor_ip_masked" not in fields

    def test_same_source_association(self, s401_ip_key):
        a = pii.ip_pii_fields("10.0.0.7")
        b = pii.ip_pii_fields("10.0.0.7")
        c = pii.ip_pii_fields("10.0.0.8")
        assert pii.same_source(a, b) is True
        assert pii.same_source(a, c) is False
        # 无哈希（无密钥）⇒ 不猜测
        pii.set_hmac_key(None)
        assert pii.same_source(pii.ip_pii_fields("10.0.0.7"),
                               pii.ip_pii_fields("10.0.0.7")) is None

    def test_key_from_env_hex(self):
        key = pii.resolve_hmac_key({"CP_IP_HMAC_KEY": "ab" * 16})
        assert key == bytes.fromhex("ab" * 16)

    def test_key_from_env_raw(self):
        key = pii.resolve_hmac_key({"CP_IP_HMAC_KEY": "plain-key-material"})
        assert key == b"plain-key-material"

    def test_key_from_file(self, tmp_path):
        path = tmp_path / "ip.key"
        path.write_bytes(b"file-key-material-1234567890")
        assert pii.resolve_hmac_key(
            {"CP_IP_HMAC_KEY_FILE": str(path)}) == b"file-key-material-1234567890"

    def test_missing_key_file_degrades(self, tmp_path):
        assert pii.resolve_hmac_key(
            {"CP_IP_HMAC_KEY_FILE": str(tmp_path / "none")}) is None

    def test_autogen_off_by_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pii, "DEFAULT_KEY_PATH", str(tmp_path / "auto.key"))
        assert pii.resolve_hmac_key({}) is None
        assert not (tmp_path / "auto.key").exists()

    def test_autogen_on_creates_key(self, tmp_path, monkeypatch):
        target = tmp_path / "auto.key"
        monkeypatch.setattr(pii, "DEFAULT_KEY_PATH", str(target))
        key = pii.resolve_hmac_key({"CP_IP_HMAC_AUTOGEN": "1"})
        assert key and target.exists()
        assert target.read_bytes() == key

    def test_contains_raw_ip_selfcheck(self):
        assert pii.contains_raw_ip("client 10.0.0.7") is True
        assert pii.contains_raw_ip("client 10.0.xxx.xxx") is False
        assert pii.contains_raw_ip({"actor_ip_hash": "deadbeef"}) is False
