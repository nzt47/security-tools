"""插件 manifest：API 版本 + 加载失败可见（P1）。"""
from __future__ import annotations

import pytest
from flask import Flask

from plugins import plugin_api
from plugins.plugin_api import Plugin, manifest


@pytest.fixture(autouse=True)
def _isolated():
    saved = list(plugin_api._REGISTRY)
    plugin_api._REGISTRY.clear()
    plugin_api.clear_load_failures()
    yield
    plugin_api._REGISTRY[:] = saved
    plugin_api.clear_load_failures()


def test_manifest_has_api_version_status_and_host_flags(monkeypatch):
    monkeypatch.delenv("CP_PLUGIN_PACKAGES_ENABLED", raising=False)
    monkeypatch.delenv("CP_PLUGIN_CLIENT_UI_ENABLED", raising=False)
    plugin_api.register_plugin(Plugin(name="alpha", version="1.0.0"))
    data = manifest(Flask("m"))
    entry = data["plugins"][0]
    assert entry["api_version"] == plugin_api.PLUGIN_API_VERSION
    assert entry["status"] == "ok"
    assert entry["load_error"] == ""
    assert data["host"]["plugin_packages_enabled"] is False
    assert data["host"]["plugin_client_ui_enabled"] is False
    assert data["host"]["plugin_api_version"] == plugin_api.PLUGIN_API_VERSION


def test_wrong_api_version_is_rejected():
    with pytest.raises(ValueError, match="api_version"):
        plugin_api.register_plugin(Plugin(name="bad", version="1", api_version="999"))


def test_load_failure_is_visible_not_silent():
    plugin_api.record_load_failure("broken_module", RuntimeError("secret detail"))
    data = manifest(Flask("m"))
    assert data["load_failures"] == [{"module": "broken_module", "error": "RuntimeError"}]
    assert "secret detail" not in str(data)
