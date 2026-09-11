"""TASK-S4-04 §5.9 第三方执行默认隔离 单元测试

覆盖「第三方 MCP Server 默认隔离容器（**无宿主网络 / 无 SSH agent / 无 $HOME**）」：
- 默认隔离值三件套均为「无」
- 宿主凭据类环境变量（AWS_ / GH_TOKEN / OPENAI_ …）**不继承**
- ``trusted=True`` 显式声明才豁免（默认 False = 隔离）
- 隔离声明（``isolation_policy_report``）机器可读，可入审计载荷
"""

from __future__ import annotations

import pytest

from agent.subagent.sandbox import (
    ISOLATION_ENV_BLOCKLIST_PREFIXES,
    ISOLATION_ENV_OVERRIDES,
    ISOLATION_POLICY_ID,
    THIRD_PARTY_DENIED_CAPABILITIES,
    apply_isolation_env,
    isolation_env_overrides,
    isolation_policy_report,
)


class TestIsolationDefaults:
    def test_three_denied_capabilities_per_section_5_9(self):
        assert set(THIRD_PARTY_DENIED_CAPABILITIES) == {"host_network", "ssh_agent", "home"}

    def test_policy_id_stable(self):
        assert ISOLATION_POLICY_ID == "third_party_mcp_default"

    def test_home_blanked_by_default(self):
        env = isolation_env_overrides()
        assert env["HOME"] == ""
        assert env["USERPROFILE"] == ""

    def test_ssh_agent_blanked(self):
        env = isolation_env_overrides()
        assert env["SSH_AUTH_SOCK"] == ""
        assert env["SSH_AGENT_PID"] == ""

    def test_host_network_flag_zero(self):
        env = isolation_env_overrides()
        assert env["CP_SANDBOX_HOST_NETWORK"] == "0"
        assert env["CP_SANDBOX_SSH_AGENT"] == "0"
        assert env["CP_SANDBOX_HOME"] == "0"

    def test_proxies_blanked(self):
        env = isolation_env_overrides()
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            assert env[key] == ""

    def test_container_root_becomes_home(self):
        env = isolation_env_overrides(container_root="/sandbox/root")
        assert env["HOME"] == "/sandbox/root"
        assert env["USERPROFILE"] == "/sandbox/root"

    def test_overrides_map_is_documented_source(self):
        assert ISOLATION_ENV_OVERRIDES["SSH_AUTH_SOCK"] == ""


class TestApplyIsolationEnv:
    def test_returns_new_dict(self):
        base = {"A": "1"}
        out = apply_isolation_env(base)
        assert out is not base
        assert base == {"A": "1"}

    def test_isolated_env_has_no_effective_home(self):
        env = apply_isolation_env({"HOME": "/home/user", "PATH": "/usr/bin"})
        assert env["HOME"] == ""
        assert env["PATH"] == "/usr/bin"

    def test_host_ssh_agent_removed(self):
        env = apply_isolation_env({"SSH_AUTH_SOCK": "/tmp/agent.sock"})
        assert env["SSH_AUTH_SOCK"] == ""

    @pytest.mark.parametrize("key", [
        "AWS_SECRET_ACCESS_KEY", "AWS_ACCESS_KEY_ID", "GH_TOKEN", "GITHUB_TOKEN",
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "KUBECONFIG", "DOCKER_HOST",
        "AZURE_CLIENT_SECRET", "GOOGLE_APPLICATION_CREDENTIALS", "CP_UI_TOKENS",
    ])
    def test_host_credentials_not_inherited(self, key):
        env = apply_isolation_env({key: "leaked", "PATH": "/usr/bin"})
        assert key not in env
        assert env["PATH"] == "/usr/bin"

    def test_blocklist_prefixes_are_uppercase(self):
        for prefix in ISOLATION_ENV_BLOCKLIST_PREFIXES:
            assert prefix == prefix.upper()

    def test_trusted_mode_skips_isolation(self):
        base = {"HOME": "/home/user", "AWS_SECRET_ACCESS_KEY": "keep", "PATH": "/usr/bin"}
        env = apply_isolation_env(base, trusted=True)
        assert env == base

    def test_container_root_applied_when_isolated(self):
        env = apply_isolation_env({}, container_root="/sandbox/root")
        assert env["HOME"] == "/sandbox/root"
        assert env["CP_SANDBOX_HOST_NETWORK"] == "0"


class TestIsolationReport:
    def test_default_report_declares_all_denied(self):
        report = isolation_policy_report()
        assert report["policy_id"] == ISOLATION_POLICY_ID
        assert report["trusted"] is False
        assert report["host_network"] is False
        assert report["ssh_agent"] is False
        assert report["home"] is False
        assert set(report["denied"]) == {"host_network", "ssh_agent", "home"}

    def test_trusted_report_flips_flags_and_drops_overrides(self):
        report = isolation_policy_report(trusted=True)
        assert report["host_network"] is True
        assert report["env_overrides"] == {}

    def test_report_is_json_serialisable(self):
        import json
        json.dumps(isolation_policy_report(container_root="/x"))

    def test_report_records_container_root(self):
        assert isolation_policy_report(container_root="/x")["container_root"] == "/x"
