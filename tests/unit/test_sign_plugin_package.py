"""离线签名 CLI（scripts/sign_plugin_package.py）测试。"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "scripts" / "sign_plugin_package.py"


def _run(*args):
    return subprocess.run([sys.executable, str(TOOL), *args],
                          cwd=str(ROOT), capture_output=True, text=True, timeout=60)


def _setup(tmp_path):
    key = Ed25519PrivateKey.generate()
    key_path = tmp_path / "release.pem"
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    package = tmp_path / "demo_plugin"
    (package / "pkg").mkdir(parents=True)
    (package / "plugin.json").write_text('{"id":"demo"}', encoding="utf-8")
    (package / "pkg" / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    pub = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    keys_path = tmp_path / "plugin_trusted_keys.json"
    keys_path.write_text(json.dumps({"schema": 1, "keys": [{
        "key_id": "test-key", "alg": "ed25519",
        "public_key_b64": base64.b64encode(pub).decode(), "status": "active"}]}),
        encoding="utf-8")
    return package, key_path, keys_path


def test_sign_then_verify(tmp_path):
    package, key_path, keys_path = _setup(tmp_path)
    signed = _run("sign", "--package", str(package), "--key", str(key_path),
                  "--key-id", "test-key")
    assert signed.returncode == 0, signed.stderr
    payload = json.loads(signed.stdout)
    assert payload["file_count"] == 2
    assert (package / "plugin.sig.json").is_file()

    verified = _run("verify", "--package", str(package), "--keys", str(keys_path))
    assert verified.returncode == 0, verified.stderr
    assert json.loads(verified.stdout)["key_id"] == "test-key"


def test_verify_rejects_tamper(tmp_path):
    package, key_path, keys_path = _setup(tmp_path)
    assert _run("sign", "--package", str(package), "--key", str(key_path),
                "--key-id", "test-key").returncode == 0
    (package / "pkg" / "__init__.py").write_text("x = 999\n", encoding="utf-8")
    verified = _run("verify", "--package", str(package), "--keys", str(keys_path))
    assert verified.returncode == 2
    assert json.loads(verified.stdout)["code"] == "PLUGIN_SIGNATURE_TAMPERED"
