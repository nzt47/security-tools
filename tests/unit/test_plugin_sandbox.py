"""AppContainer 沙箱辅助函数与 fail-closed 选择测试（不真正启动进程）。"""
from __future__ import annotations

import os
import sys

import pytest

from agent.extensions import appcontainer, plugin_policy, plugin_process
from agent.extensions.plugin_process import PluginProcess, PluginProcessError


def test_profile_name_is_stable_and_safe():
    a = appcontainer.profile_name("demo.plugin/v1")
    b = appcontainer.profile_name("demo.plugin/v1")
    assert a == b
    assert a.startswith("YunshuPlugin-")
    assert len(a) <= 64
    assert "/" not in a and "\\" not in a
    assert appcontainer.profile_name("other") != a


def test_sandbox_data_dir_under_shared_root():
    path = appcontainer.sandbox_data_dir("demo")
    assert path.name == "data"
    assert path.parent.name == appcontainer.profile_name("demo")


def test_build_environment_is_whitelist_and_redirects_temp(tmp_path, monkeypatch):
    monkeypatch.setenv("FLASK_API_TOKEN", "should-not-leak")
    monkeypatch.setenv("YUNSHU_POC_SECRET", "should-not-leak")
    deps = tmp_path / "deps"
    deps.mkdir()
    env = appcontainer.build_environment(tmp_path, deps)
    assert "FLASK_API_TOKEN" not in env
    assert "YUNSHU_POC_SECRET" not in env
    assert env["TEMP"] == str(tmp_path)
    assert env["TMP"] == str(tmp_path)
    assert env["HOME"] == str(tmp_path)
    assert env["PYTHONPATH"] == str(deps)
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    if os.name == "nt":
        assert "SYSTEMROOT" in env
        assert "USERPROFILE" in env


def test_build_env_block_is_utf16_sorted_double_null():
    block = appcontainer.build_env_block({"B": "2", "A": "1"})
    assert block.startswith("A=1\0B=2\0".encode("utf-16-le"))
    assert block.endswith(b"\0\0")
    assert block.decode("utf-16-le").rstrip("\0") == "A=1\0B=2"


def test_sandbox_mode_default_and_invalid(monkeypatch):
    monkeypatch.delenv(plugin_policy.ENV_PLUGIN_SANDBOX, raising=False)
    assert plugin_policy.sandbox_mode() == "off"
    monkeypatch.setenv(plugin_policy.ENV_PLUGIN_SANDBOX, "appcontainer")
    assert plugin_policy.sandbox_mode() == "appcontainer"
    monkeypatch.setenv(plugin_policy.ENV_PLUGIN_SANDBOX, "nonsense")
    assert plugin_policy.sandbox_mode() == "off"


def test_sandbox_mode_fail_closed_when_unavailable(tmp_path, monkeypatch):
    plugin = tmp_path / "demo_plugin"
    plugin.mkdir()
    (plugin / "__init__.py").write_text("class Plugin:\n    pass\n", encoding="utf-8")
    monkeypatch.setattr(plugin_policy, "sandbox_mode", lambda: "appcontainer")
    monkeypatch.setattr(appcontainer, "is_available", lambda: False)
    monkeypatch.setattr(appcontainer, "unavailable_reason", lambda: "test")
    proc = PluginProcess("unavailable", str(plugin), timeout=5)
    with pytest.raises(PluginProcessError) as exc:
        proc.start()
    assert exc.value.error_code == "PLUGIN_SANDBOX_UNAVAILABLE"
    proc.close()


def test_appcontainer_unavailable_on_current_platform():
    if sys.platform == "win32":
        pytest.skip("Windows 上可加载 Win32 API")
    assert appcontainer.is_available() is False
    with pytest.raises(appcontainer.AppContainerUnavailable):
        appcontainer.AppContainerSandbox("x", appcontainer.sandbox_data_dir("x"), [])
