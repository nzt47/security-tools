"""TASK-S4-04 §5.9 临时凭据 单元测试

覆盖：
- **TTL ≤ 任务时长**：超出**直接拒绝**（不静默截断）
- **任务结束即销毁**：``credential_scope`` 在 ``finally`` 销毁；异常/中断路径同样销毁
- 销毁的**可断言证据**：明文被擦除（``wipe_verified``）、销毁后读取抛
  ``CredentialDestroyed``、``destroy()`` 幂等
- **每来源独立凭据**：同名不同来源 = 两条独立凭据（独立 id / 环境变量 / 销毁）
- **manifest 禁存长期密钥**：已知密钥值 / 可疑键名 / 密钥形态三路检出
"""

from __future__ import annotations

import pytest

from agent.subagent.credentials import (
    E_CREDENTIAL_DESTROYED,
    E_CREDENTIAL_TTL_TOO_LONG,
    E_MANIFEST_SECRET_LEAK,
    ENV_PREFIX,
    CredentialDestroyed,
    CredentialError,
    CredentialTTLTooLong,
    ManifestSecretLeak,
    TemporaryCredentialManager,
    assert_manifest_secret_free,
    credential_scope,
    env_var_name,
    find_manifest_secrets,
    fingerprint,
)

TASK_TIMEOUT = 300.0


@pytest.fixture()
def manager():
    mgr = TemporaryCredentialManager()
    yield mgr
    mgr.destroy_all(reason="test_teardown")


# ════════════════════════════════════════════════════════════
#  签发
# ════════════════════════════════════════════════════════════


class TestIssue:
    def test_basic_fields(self, manager):
        cred = manager.issue("GITHUB_TOKEN", "ghp_secret_value", source="mcp:github",
                             task_timeout_seconds=TASK_TIMEOUT)
        assert cred.credential_id.startswith("cred-")
        assert cred.name == "GITHUB_TOKEN"
        assert cred.source == "mcp:github"
        assert cred.ttl_seconds == TASK_TIMEOUT
        assert cred.expires_at > cred.issued_at
        assert cred.destroyed is False

    def test_env_var_name_shape(self, manager):
        cred = manager.issue("api-key", "v", source="mcp:github",
                             task_timeout_seconds=TASK_TIMEOUT)
        assert cred.env_var == f"{ENV_PREFIX}MCP_GITHUB_API_KEY"

    def test_ttl_defaults_to_task_timeout(self, manager):
        cred = manager.issue("K", "v", source="s", task_timeout_seconds=42)
        assert cred.ttl_seconds == 42

    def test_fingerprint_is_not_plaintext(self, manager):
        cred = manager.issue("K", "super-secret", source="s",
                             task_timeout_seconds=TASK_TIMEOUT)
        assert cred.value_fingerprint == fingerprint("super-secret")
        assert "super-secret" not in cred.value_fingerprint

    def test_to_dict_never_contains_plaintext(self, manager):
        cred = manager.issue("K", "super-secret", source="s",
                             task_timeout_seconds=TASK_TIMEOUT)
        assert "super-secret" not in str(cred.to_dict())

    def test_ttl_equal_to_task_timeout_allowed(self, manager):
        cred = manager.issue("K", "v", source="s", task_timeout_seconds=10,
                             ttl_seconds=10)
        assert cred.ttl_seconds == 10

    def test_ttl_shorter_than_task_timeout_allowed(self, manager):
        cred = manager.issue("K", "v", source="s", task_timeout_seconds=100,
                             ttl_seconds=5)
        assert cred.ttl_seconds == 5

    def test_ttl_longer_than_task_timeout_rejected(self, manager):
        """§5.9：凭据 TTL ≤ 任务时长——超出的申请**直接拒绝**，不静默截断"""
        with pytest.raises(CredentialTTLTooLong) as excinfo:
            manager.issue("K", "v", source="s", task_timeout_seconds=100,
                          ttl_seconds=101)
        assert excinfo.value.code == E_CREDENTIAL_TTL_TOO_LONG
        assert excinfo.value.ttl_seconds == 101
        assert excinfo.value.task_timeout_seconds == 100
        assert manager.active_count() == 0

    def test_ttl_above_manager_max_rejected(self):
        mgr = TemporaryCredentialManager(max_ttl_seconds=60)
        with pytest.raises(CredentialTTLTooLong):
            mgr.issue("K", "v", source="s", task_timeout_seconds=600, ttl_seconds=120)

    def test_max_ttl_from_env(self, monkeypatch):
        monkeypatch.setenv("CP_SUBAGENT_CRED_TTL_MAX", "30")
        mgr = TemporaryCredentialManager()
        assert mgr.snapshot()["max_ttl_seconds"] == 30.0
        with pytest.raises(CredentialTTLTooLong):
            mgr.issue("K", "v", source="s", task_timeout_seconds=600, ttl_seconds=31)

    @pytest.mark.parametrize("bad", ["abc", "0", "-5"])
    def test_illegal_max_ttl_env_falls_back(self, monkeypatch, bad):
        monkeypatch.setenv("CP_SUBAGENT_CRED_TTL_MAX", bad)
        mgr = TemporaryCredentialManager()
        assert mgr.snapshot()["max_ttl_seconds"] > 0

    @pytest.mark.parametrize("kwargs,reason", [
        ({"name": "", "value": "v", "source": "s"}, "凭据名"),
        ({"name": "K", "value": "", "source": "s"}, "明文"),
        ({"name": "K", "value": "v", "source": ""}, "来源"),
    ])
    def test_illegal_inputs_rejected(self, manager, kwargs, reason):
        with pytest.raises(CredentialError) as excinfo:
            manager.issue(task_timeout_seconds=TASK_TIMEOUT, **kwargs)
        assert reason in str(excinfo.value)

    @pytest.mark.parametrize("bad", [0, -1])
    def test_non_positive_task_timeout_rejected(self, manager, bad):
        with pytest.raises(CredentialError):
            manager.issue("K", "v", source="s", task_timeout_seconds=bad)

    @pytest.mark.parametrize("bad", [0, -1])
    def test_non_positive_ttl_rejected(self, manager, bad):
        with pytest.raises(CredentialError):
            manager.issue("K", "v", source="s", task_timeout_seconds=TASK_TIMEOUT,
                          ttl_seconds=bad)

    def test_issue_for_sources_returns_independent_credentials(self, manager):
        creds = manager.issue_for_sources(
            "API_KEY", {"mcp:a": "va", "mcp:b": "vb"},
            task_timeout_seconds=TASK_TIMEOUT)
        assert len(creds) == 2
        assert creds[0].credential_id != creds[1].credential_id
        assert creds[0].env_var != creds[1].env_var


# ════════════════════════════════════════════════════════════
#  销毁（可断言证据）
# ════════════════════════════════════════════════════════════


class TestDestruction:
    def test_destroy_returns_true_once(self, manager):
        cred = manager.issue("K", "v", source="s", task_timeout_seconds=TASK_TIMEOUT)
        assert manager.destroy(cred.credential_id) is True
        assert manager.destroy(cred.credential_id) is False

    def test_value_unreadable_after_destroy(self, manager):
        cred = manager.issue("K", "super-secret", source="s",
                             task_timeout_seconds=TASK_TIMEOUT)
        manager.destroy(cred.credential_id)
        with pytest.raises(CredentialDestroyed) as excinfo:
            _ = cred.value
        assert excinfo.value.code == E_CREDENTIAL_DESTROYED

    def test_plaintext_wiped_after_destroy(self, manager):
        cred = manager.issue("K", "super-secret", source="s",
                             task_timeout_seconds=TASK_TIMEOUT)
        manager.destroy(cred.credential_id)
        assert cred.wipe_verified is True
        assert cred._value == ""

    def test_destroy_marks_state(self, manager):
        cred = manager.issue("K", "v", source="s", task_timeout_seconds=TASK_TIMEOUT)
        manager.destroy(cred.credential_id, reason="delegation_end")
        assert cred.destroyed is True
        assert cred.destroy_reason == "delegation_end"
        assert cred.destroyed_at >= cred.issued_at

    def test_destroy_unknown_id_returns_false(self, manager):
        assert manager.destroy("cred-nope") is False

    def test_destroy_credential_object_helper(self, manager):
        cred = manager.issue("K", "v", source="s", task_timeout_seconds=TASK_TIMEOUT)
        assert manager.destroy_credential(cred) is True
        assert cred.destroyed is True

    def test_destroy_source_only_that_source(self, manager):
        a = manager.issue("K", "va", source="mcp:a", task_timeout_seconds=TASK_TIMEOUT)
        b = manager.issue("K", "vb", source="mcp:b", task_timeout_seconds=TASK_TIMEOUT)
        assert manager.destroy_source("mcp:a") == 1
        assert a.destroyed is True
        assert b.destroyed is False

    def test_destroy_all(self, manager):
        for idx in range(3):
            manager.issue(f"K{idx}", "v", source="s", task_timeout_seconds=TASK_TIMEOUT)
        assert manager.destroy_all() == 3
        assert manager.active_count() == 0

    def test_active_and_for_source(self, manager):
        a = manager.issue("K", "v", source="mcp:a", task_timeout_seconds=TASK_TIMEOUT)
        assert manager.active() == [a]
        assert manager.for_source("mcp:a") == [a]
        manager.destroy(a.credential_id)
        assert manager.active() == []

    def test_get_by_id(self, manager):
        cred = manager.issue("K", "v", source="s", task_timeout_seconds=TASK_TIMEOUT)
        assert manager.get(cred.credential_id) is cred
        assert manager.get("cred-nope") is None

    def test_sweep_expired_uses_clock(self):
        now = [1000.0]
        mgr = TemporaryCredentialManager(clock=lambda: now[0])
        cred = mgr.issue("K", "v", source="s", task_timeout_seconds=10)
        assert cred.is_expired is False
        now[0] += 11
        assert cred.is_expired is True
        assert mgr.sweep_expired() == 1
        assert cred.destroyed is True

    def test_assert_all_destroyed_raises_then_passes(self, manager):
        cred = manager.issue("K", "v", source="s", task_timeout_seconds=TASK_TIMEOUT)
        with pytest.raises(CredentialError):
            manager.assert_all_destroyed()
        manager.destroy(cred.credential_id)
        manager.assert_all_destroyed()  # 不抛

    def test_snapshot_has_no_plaintext(self, manager):
        manager.issue("K", "super-secret", source="s", task_timeout_seconds=TASK_TIMEOUT)
        assert "super-secret" not in str(manager.snapshot())

    def test_snapshot_totals(self, manager):
        cred = manager.issue("K", "v", source="s", task_timeout_seconds=TASK_TIMEOUT)
        manager.destroy(cred.credential_id)
        snap = manager.snapshot()
        assert snap["issued"] == 1
        assert snap["destroyed"] == 1
        assert snap["active"] == 0
        assert snap["sources"] == ["s"]


# ════════════════════════════════════════════════════════════
#  作用域：finally 销毁（含异常路径）
# ════════════════════════════════════════════════════════════


class TestCredentialScope:
    SPECS = [{"name": "GITHUB_TOKEN", "value": "ghp_x", "source": "mcp:github"},
             {"name": "AWS_KEY", "value": "ak", "source": "mcp:aws"}]

    def test_normal_exit_destroys(self, manager):
        with credential_scope(manager, self.SPECS,
                              task_timeout_seconds=TASK_TIMEOUT) as creds:
            assert len(creds) == 2
            assert manager.active_count() == 2
        assert manager.active_count() == 0
        for cred in creds:
            assert cred.destroyed is True
            assert cred.wipe_verified is True

    def test_exception_path_destroys(self, manager):
        with pytest.raises(RuntimeError):
            with credential_scope(manager, self.SPECS,
                                  task_timeout_seconds=TASK_TIMEOUT):
                raise RuntimeError("delegation blew up")
        assert manager.active_count() == 0

    def test_timeout_like_exception_destroys(self, manager):
        """超时/取消不走 ``except Exception``，只有 ``finally`` 能保证销毁"""
        with pytest.raises(TimeoutError):
            with credential_scope(manager, self.SPECS,
                                  task_timeout_seconds=TASK_TIMEOUT):
                raise TimeoutError("channel timeout")
        assert manager.active_count() == 0

    def test_destroy_reason_recorded(self, manager):
        with credential_scope(manager, self.SPECS,
                              task_timeout_seconds=TASK_TIMEOUT,
                              reason="delegation_end") as creds:
            pass
        assert all(c.destroy_reason == "delegation_end" for c in creds)

    def test_invalid_spec_ttl_still_destroys_earlier_credentials(self, manager):
        """第二个凭据 TTL 非法 → 已签发的第一个仍必须在 finally 路径被销毁"""
        specs = [{"name": "A", "value": "va", "source": "s"},
                 {"name": "B", "value": "vb", "source": "s", "ttl_seconds": 9999}]
        with pytest.raises(CredentialTTLTooLong):
            with credential_scope(manager, specs, task_timeout_seconds=TASK_TIMEOUT):
                pass
        assert manager.active_count() == 0

    def test_scope_env_injection_and_destruction(self, manager):
        with credential_scope(manager, self.SPECS,
                              task_timeout_seconds=TASK_TIMEOUT) as creds:
            env = manager.env_for(creds)
            assert env[creds[0].env_var] == "ghp_x"
            assert env[creds[1].env_var] == "ak"
        assert manager.env_for() == {}


# ════════════════════════════════════════════════════════════
#  每来源独立
# ════════════════════════════════════════════════════════════


class TestPerSourceIndependence:
    def test_same_name_two_sources_distinct(self, manager):
        a = manager.issue("API_KEY", "va", source="mcp:a", task_timeout_seconds=TASK_TIMEOUT)
        b = manager.issue("API_KEY", "vb", source="mcp:b", task_timeout_seconds=TASK_TIMEOUT)
        assert a.credential_id != b.credential_id
        assert a.env_var != b.env_var
        assert a.value_fingerprint != b.value_fingerprint

    def test_destroying_one_leaves_other_usable(self, manager):
        a = manager.issue("API_KEY", "va", source="mcp:a", task_timeout_seconds=TASK_TIMEOUT)
        b = manager.issue("API_KEY", "vb", source="mcp:b", task_timeout_seconds=TASK_TIMEOUT)
        manager.destroy(a.credential_id)
        assert b.value == "vb"

    def test_independent_ttl_per_source(self, manager):
        a = manager.issue("K", "va", source="mcp:a", task_timeout_seconds=100, ttl_seconds=10)
        b = manager.issue("K", "vb", source="mcp:b", task_timeout_seconds=100, ttl_seconds=90)
        assert a.ttl_seconds != b.ttl_seconds

    def test_env_var_name_sanitises_source(self):
        assert env_var_name("mcp:git hub", "a/b") == f"{ENV_PREFIX}MCP_GIT_HUB_A_B"

    def test_env_for_only_active(self, manager):
        a = manager.issue("A", "va", source="s", task_timeout_seconds=TASK_TIMEOUT)
        b = manager.issue("B", "vb", source="s", task_timeout_seconds=TASK_TIMEOUT)
        manager.destroy(a.credential_id)
        env = manager.env_for()
        assert a.env_var not in env
        assert env[b.env_var] == "vb"


# ════════════════════════════════════════════════════════════
#  manifest 密钥闸门（§2.3 硬约束 / §5.9）
# ════════════════════════════════════════════════════════════


class TestManifestGuard:
    def test_clean_manifest_passes(self):
        assert find_manifest_secrets({"goal": "抽取步骤", "budget_tokens": 100}) == []
        assert_manifest_secret_free({"goal": "抽取步骤"})

    def test_known_secret_value_detected(self):
        offenders = find_manifest_secrets(
            {"nested": {"key": "value is ghp_realtoken123"}},
            known_secrets=["ghp_realtoken123"])
        assert offenders == ["nested.key"]

    def test_suspicious_key_with_value_detected(self):
        offenders = find_manifest_secrets({"api_key": "abc"})
        assert "api_key" in offenders

    def test_suspicious_key_empty_value_allowed(self):
        assert find_manifest_secrets({"api_key": ""}) == []

    @pytest.mark.parametrize("key", ["token", "secret", "password", "client_secret",
                                     "ACCESS_KEY", "private_key"])
    def test_various_suspicious_keys(self, key):
        assert find_manifest_secrets({key: "x"}) != []

    def test_secret_shaped_value_detected(self):
        assert find_manifest_secrets({"note": "sk-" + "a" * 24}) != []

    def test_pem_header_detected(self):
        assert find_manifest_secrets(
            {"blob": "-----BEGIN RSA PRIVATE KEY-----"}) != []

    def test_nested_list_path_reported(self):
        offenders = find_manifest_secrets({"items": [{"password": "p"}]})
        assert offenders == ["items[0].password"]

    def test_offenders_deduplicated_preserving_order(self):
        offenders = find_manifest_secrets({"b": {"token": "x"}, "a": {"secret": "y"}})
        assert len(offenders) == len(set(offenders))

    def test_assert_raises_with_offenders(self):
        with pytest.raises(ManifestSecretLeak) as excinfo:
            assert_manifest_secret_free({"client_secret": "abc"})
        assert excinfo.value.code == E_MANIFEST_SECRET_LEAK
        assert "client_secret" in excinfo.value.offenders

    def test_assert_passes_with_known_secrets_absent(self):
        assert_manifest_secret_free({"goal": "ok"}, known_secrets=["ghp_other"])

    def test_task_file_style_payload_is_clean(self):
        """八要素 task_file 本身不得携带长期密钥"""
        payload = {
            "schema_version": 1, "goal": "抽取步骤", "constraints": ["只读"],
            "prior_artifacts": [], "prohibitions": [], "artifact_format": "JSONL",
            "budget_tokens": 100, "timeout_seconds": 60,
            "callback_url": "internal://x", "tenancy": {"tenant_id": "default"},
        }
        assert find_manifest_secrets(payload) == []
