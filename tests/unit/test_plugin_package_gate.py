"""第三方插件包默认关闭 + 来源白名单（P0）守卫。"""
from __future__ import annotations

from agent.extensions.plugins_installer import PluginInstaller
from agent.extensions.store import ExtensionStore


def _installer(tmp_path):
    return PluginInstaller(ExtensionStore(str(tmp_path / "extensions.json")))


def test_install_disabled_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("CP_PLUGIN_PACKAGES_ENABLED", raising=False)
    ok, msg = _installer(tmp_path).install_plugin("github:owner/repo")
    assert ok is False
    assert "暂未开放" in msg


def test_install_requires_allowlist(monkeypatch, tmp_path):
    monkeypatch.setenv("CP_PLUGIN_PACKAGES_ENABLED", "1")
    monkeypatch.delenv("CP_PLUGIN_INSTALL_ALLOWLIST", raising=False)
    ok, msg = _installer(tmp_path).install_plugin("github:owner/repo")
    assert ok is False
    assert "白名单" in msg


def test_pip_source_always_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("CP_PLUGIN_PACKAGES_ENABLED", "1")
    monkeypatch.setenv("CP_PLUGIN_INSTALL_ALLOWLIST", "pip:requests")
    ok, msg = _installer(tmp_path).install_plugin("pip:requests")
    assert ok is False
    assert "pip:" in msg


def test_toggle_reports_load_failure(monkeypatch, tmp_path):
    from agent.extensions.base import ExtensionMetadata, ExtensionStatus, ExtensionType
    store = ExtensionStore(str(tmp_path / "extensions.json"))
    store.add(ExtensionMetadata(
        ext_id="ghost", ext_type=ExtensionType.PLUGIN, name="ghost",
        version="1.0.0", status=ExtensionStatus.DISABLED,
        install_path=str(tmp_path / "missing"), source="local:/missing",
    ))
    inst = PluginInstaller(store)
    ok, msg, enabled = inst.toggle_plugin("ghost", True)
    assert ok is False and enabled is False
    assert "路径不存在" in msg
