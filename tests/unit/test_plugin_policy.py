"""插件包策略开关（P0）单元测试。"""
from __future__ import annotations

from agent.extensions import plugin_policy


def test_defaults_are_safe(monkeypatch):
    for key in (
        plugin_policy.ENV_PLUGIN_PACKAGES_ENABLED,
        plugin_policy.ENV_PLUGIN_CLIENT_UI_ENABLED,
        plugin_policy.ENV_PLUGIN_INSTALL_ALLOWLIST,
        plugin_policy.ENV_PLUGIN_MAX_CONCURRENT,
    ):
        monkeypatch.delenv(key, raising=False)
    assert plugin_policy.plugin_packages_enabled() is False
    assert plugin_policy.plugin_client_ui_enabled() is False
    assert plugin_policy.install_allowlist() == ()
    assert plugin_policy.source_allowed("github:owner/repo") is False
    assert plugin_policy.max_concurrent() == 4


def test_bool_and_allowlist_parsing(monkeypatch):
    monkeypatch.setenv(plugin_policy.ENV_PLUGIN_PACKAGES_ENABLED, "YES")
    monkeypatch.setenv(plugin_policy.ENV_PLUGIN_CLIENT_UI_ENABLED, "0")
    monkeypatch.setenv(plugin_policy.ENV_PLUGIN_INSTALL_ALLOWLIST,
                       " GitHub:Owner/Repo , local:/tmp/plugin ")
    assert plugin_policy.plugin_packages_enabled() is True
    assert plugin_policy.plugin_client_ui_enabled() is False
    assert plugin_policy.install_allowlist() == ("github:owner/repo", "local:/tmp/plugin")
    assert plugin_policy.source_allowed("github:owner/repo") is True
    assert plugin_policy.source_allowed("github:owner/repo@v1.2.3") is False
    assert plugin_policy.source_allowed("github:owner/repo-evil") is False
    assert plugin_policy.source_allowed("https://owner/repo") is False


def test_max_concurrent_clamped(monkeypatch):
    monkeypatch.setenv(plugin_policy.ENV_PLUGIN_MAX_CONCURRENT, "999")
    assert plugin_policy.max_concurrent() == 64
    monkeypatch.setenv(plugin_policy.ENV_PLUGIN_MAX_CONCURRENT, "-1")
    assert plugin_policy.max_concurrent() == 0
    monkeypatch.setenv(plugin_policy.ENV_PLUGIN_MAX_CONCURRENT, "not-int")
    assert plugin_policy.max_concurrent() == 4
