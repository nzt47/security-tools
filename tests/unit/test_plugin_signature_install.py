"""插件包签名接线（安装暂存 + 加载复验）测试。"""
from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent.extensions import plugin_policy, plugin_signature as ps
from agent.extensions import plugins_installer
from agent.extensions.base import ExtensionStatus, ExtensionType
from agent.extensions.plugins_installer import PluginInstaller
from agent.extensions.store import ExtensionStore


def _pub_b64(key):
    raw = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def _write_plugin(root, extra=None):
    files = {"plugin.json": json.dumps({"id": "demo", "version": "1.0.0"}),
             "__init__.py": "class Plugin:\n    pass\n"}
    files.update(extra or {})
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _sign(root, key, key_id="k1"):
    files = ps.scan_package(root)
    body = {
        "schema": 1,
        "key_id": key_id,
        "signed_at": "2026-11-23T12:00:00Z",
        "package_sha256": ps._sha256_hex(ps.canonical_json(files)),
        "files": files,
    }
    signature = key.sign(ps.canonical_json(body))
    envelope = dict(body, signature=base64.b64encode(signature).decode())
    (root / ps.SIGNATURE_FILENAME).write_text(
        json.dumps(envelope, ensure_ascii=False), encoding="utf-8")


@pytest.fixture()
def setup(tmp_path, monkeypatch):
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    keys_path = tmp_path / "plugin_trusted_keys.json"
    key = Ed25519PrivateKey.generate()
    keys_path.write_text(json.dumps({"schema": 1, "keys": [{
        "key_id": "k1", "alg": "ed25519",
        "public_key_b64": _pub_b64(key), "status": "active"}]}), encoding="utf-8")
    monkeypatch.setattr(plugins_installer, "_PLUGINS_DIR", plugins_dir)
    monkeypatch.setattr(plugin_policy, "TRUSTED_KEYS_PATH", keys_path)
    monkeypatch.setenv(plugin_policy.ENV_PLUGIN_PACKAGES_ENABLED, "1")
    monkeypatch.delenv(plugin_policy.ENV_PLUGIN_SIGNATURE_MODE, raising=False)
    src = tmp_path / "src_plugin"
    src.mkdir()
    _write_plugin(src)
    monkeypatch.setenv(plugin_policy.ENV_PLUGIN_INSTALL_ALLOWLIST, f"local:{src}")
    store = ExtensionStore(str(tmp_path / "extensions.json"))
    return {
        "plugins_dir": plugins_dir,
        "keys_path": keys_path,
        "key": key,
        "src": src,
        "store": store,
        "installer": PluginInstaller(store),
    }


def test_enforced_install_moves_signed_package(setup):
    ctx = setup
    _sign(ctx["src"], ctx["key"])
    ok, msg = ctx["installer"].install_plugin(f"local:{ctx['src']}")
    assert ok is True, msg
    final = ctx["plugins_dir"] / "src_plugin"
    assert (final / ps.SIGNATURE_FILENAME).is_file()
    record = ctx["store"].get(ExtensionType.PLUGIN, "demo")
    assert record and record["install_path"] == str(final)
    assert list((ctx["plugins_dir"] / ".staging").iterdir()) == []


def test_enforced_install_rejects_tampered_package(setup):
    ctx = setup
    _sign(ctx["src"], ctx["key"])
    (ctx["src"] / "__init__.py").write_text("class Plugin:\n    evil = 1\n", encoding="utf-8")
    ok, msg = ctx["installer"].install_plugin(f"local:{ctx['src']}")
    assert ok is False
    assert "PLUGIN_SIGNATURE_TAMPERED" in msg
    assert (ctx["plugins_dir"] / "src_plugin").exists() is False
    assert ctx["store"].list_all(ExtensionType.PLUGIN) == []


def test_enforced_install_rejects_missing_signature(setup):
    ctx = setup
    ok, msg = ctx["installer"].install_plugin(f"local:{ctx['src']}")
    assert ok is False
    assert "PLUGIN_SIGNATURE_MISSING" in msg
    assert (ctx["plugins_dir"] / "src_plugin").exists() is False


def test_warn_mode_allows_unsigned_package(setup, monkeypatch):
    ctx = setup
    monkeypatch.setenv(plugin_policy.ENV_PLUGIN_SIGNATURE_MODE, "warn")
    ok, msg = ctx["installer"].install_plugin(f"local:{ctx['src']}")
    assert ok is True, msg


def test_off_mode_allows_unsigned_package(setup, monkeypatch):
    ctx = setup
    monkeypatch.setenv(plugin_policy.ENV_PLUGIN_SIGNATURE_MODE, "off")
    ok, msg = ctx["installer"].install_plugin(f"local:{ctx['src']}")
    assert ok is True, msg


def test_load_rechecks_signature_and_marks_error(setup):
    ctx = setup
    _sign(ctx["src"], ctx["key"])
    ok, msg = ctx["installer"].install_plugin(f"local:{ctx['src']}")
    assert ok is True, msg
    final = ctx["plugins_dir"] / "src_plugin"
    (final / "__init__.py").write_text("class Plugin:\n    evil = 1\n", encoding="utf-8")
    ok, msg = ctx["installer"].load_plugin("demo")
    assert ok is False
    assert "PLUGIN_SIGNATURE_TAMPERED" in msg
    assert ctx["store"].get(ExtensionType.PLUGIN, "demo")["status"] == ExtensionStatus.ERROR.value


def test_enforced_install_rejects_single_file(setup, monkeypatch):
    ctx = setup
    single = ctx["src"].parent / "single_plugin.py"
    single.write_text("class Plugin:\n    pass\n", encoding="utf-8")
    monkeypatch.setenv(plugin_policy.ENV_PLUGIN_INSTALL_ALLOWLIST, f"local:{single}")
    ok, msg = ctx["installer"].install_plugin(f"local:{single}")
    assert ok is False
    # local 单文件会被包成同名目录；没有 plugin.sig.json ⇒ 缺签名。
    assert "PLUGIN_SIGNATURE_MISSING" in msg
