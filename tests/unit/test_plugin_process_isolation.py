"""第三方插件包进程隔离（P2）测试。"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from agent.extensions import plugin_policy
from agent.extensions.plugin_process import PluginProcess, PluginProcessError, PluginProcessTimeout

PLUGIN_SRC = '''
import os
import time

class Plugin:
    def on_load(self, context):
        self.plugin_id = context.get("plugin_id", "")

    def get_tools(self):
        return [
            {"name": "probe_echo", "description": "echo", "schema": {"type": "object"},
             "handler": self.echo},
            {"name": "probe_env", "description": "env", "schema": {"type": "object"},
             "handler": self.env},
            {"name": "probe_sleep", "description": "sleep", "schema": {"type": "object"},
             "handler": self.sleep},
        ]

    def echo(self, text="", **kwargs):
        return {"ok": True, "text": text, "plugin": self.plugin_id}

    def env(self):
        return {"ok": True, "token": os.environ.get("FLASK_API_TOKEN", "")}

    def sleep(self, seconds=0):
        time.sleep(float(seconds))
        return {"ok": True}
'''


@pytest.fixture
def plugin_dir(tmp_path):
    d = tmp_path / "demo_plugin"
    d.mkdir()
    (d / "__init__.py").write_text(PLUGIN_SRC, encoding="utf-8")
    return d


def test_process_start_call_and_env_scrub(plugin_dir, monkeypatch):
    monkeypatch.setenv("FLASK_API_TOKEN", "SECRET_SHOULD_NOT_LEAK")
    proc = PluginProcess("demo", str(plugin_dir), timeout=5.0)
    try:
        tools = proc.start()
        assert {t["name"] for t in tools} == {"probe_echo", "probe_env", "probe_sleep"}
        assert proc.call("probe_echo", {"text": "hi"}) == {
            "ok": True, "text": "hi", "plugin": "demo"}
        assert proc.call("probe_env", {})["token"] == ""
        assert proc.alive is True
    finally:
        proc.close()
    assert proc.alive is False


def test_process_timeout_kills_worker(plugin_dir):
    proc = PluginProcess("demo", str(plugin_dir), timeout=0.5)
    try:
        proc.start()
        with pytest.raises(PluginProcessTimeout):
            proc.call("probe_sleep", {"seconds": 5})
        assert proc.alive is False
    finally:
        proc.close()


def test_process_tool_error_is_structured(plugin_dir):
    proc = PluginProcess("demo", str(plugin_dir), timeout=5.0)
    try:
        proc.start()
        with pytest.raises(PluginProcessError):
            proc.call("not_a_tool", {})
    finally:
        proc.close()


def test_installer_loads_plugin_in_subprocess(plugin_dir, tmp_path, monkeypatch):
    from agent.extensions.base import ExtensionMetadata, ExtensionStatus, ExtensionType
    from agent.extensions.plugins_installer import PluginInstaller
    from agent.extensions.store import ExtensionStore

    monkeypatch.setattr(plugin_policy, "plugin_packages_enabled", lambda: True)
    monkeypatch.setattr(plugin_policy, "source_allowed", lambda source: True)
    monkeypatch.setattr(plugin_policy, "plugin_process_isolation_enabled", lambda: True)
    monkeypatch.setattr(plugin_policy, "process_timeout", lambda: 5.0)

    store = ExtensionStore(str(tmp_path / "extensions.json"))
    store.add(ExtensionMetadata(
        ext_id="demo", ext_type=ExtensionType.PLUGIN, name="demo",
        version="1.0.0", status=ExtensionStatus.INSTALLED,
        install_path=str(plugin_dir), source="local:/demo",
    ))
    registered = []
    unregistered = []

    def reg(name, description, handler, schema, source, source_id):
        registered.append((name, handler, source, source_id))

    def unreg(source, source_id):
        unregistered.append((source, source_id))
        return 1

    inst = PluginInstaller(store, tool_register_fn=reg, tool_unregister_fn=unreg)
    ok, msg = inst.load_plugin("demo")
    try:
        assert ok is True, msg
        names = {x[0] for x in registered}
        assert {"probe_echo", "probe_env", "probe_sleep"} <= names
        proc = inst._loaded_plugins["demo"]
        assert proc.alive is True
        handler = next(h for n, h, _s, _i in registered if n == "probe_echo")
        assert handler(text="x")["text"] == "x"
    finally:
        inst.unload_plugin("demo")
    assert unregistered == [("plugin", "demo")]
