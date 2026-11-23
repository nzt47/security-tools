"""插件包签名校验（P2，不接线）测试。"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent.extensions import plugin_signature as ps


def _write_pkg(root, files):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _pub_b64(private_key):
    raw = private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def _trusted(private_key, key_id="k1", status="active", not_after=None):
    record = {
        "key_id": key_id,
        "alg": "ed25519",
        "public_key_b64": _pub_b64(private_key),
        "status": status,
    }
    if not_after is not None:
        record["not_after"] = not_after
    return {key_id: record}


def _sign(root, private_key, key_id="k1", package_sha256=None, signed_at="2026-11-23T12:00:00Z"):
    files = ps.scan_package(root)
    body = {
        "schema": 1,
        "key_id": key_id,
        "signed_at": signed_at,
        "package_sha256": package_sha256 or ps._sha256_hex(ps.canonical_json(files)),
        "files": files,
    }
    signature = private_key.sign(ps.canonical_json(body))
    return dict(body, signature=base64.b64encode(signature).decode())


@pytest.fixture()
def pkg(tmp_path):
    root = tmp_path / "demo_plugin"
    _write_pkg(root, {"plugin.json": '{"id":"demo"}', "pkg/__init__.py": "x = 1\n"})
    return root


@pytest.fixture()
def key():
    return Ed25519PrivateKey.generate()


def test_valid_package_verifies(pkg, key):
    envelope = _sign(pkg, key)
    result = ps.verify_package(pkg, envelope, _trusted(key))
    assert result["key_id"] == "k1"
    assert result["file_count"] == 2
    assert len(result["package_sha256"]) == 64


def test_tampered_file_rejected(pkg, key):
    envelope = _sign(pkg, key)
    (pkg / "pkg" / "__init__.py").write_text("x = 999\n", encoding="utf-8")
    with pytest.raises(ps.PluginSignatureError) as exc:
        ps.verify_package(pkg, envelope, _trusted(key))
    assert exc.value.code == "PLUGIN_SIGNATURE_TAMPERED"


def test_extra_file_rejected(pkg, key):
    envelope = _sign(pkg, key)
    (pkg / "evil.py").write_text("print(1)\n", encoding="utf-8")
    with pytest.raises(ps.PluginSignatureError) as exc:
        ps.verify_package(pkg, envelope, _trusted(key))
    assert exc.value.code == "PLUGIN_SIGNATURE_TAMPERED"


def test_missing_file_rejected(pkg, key):
    envelope = _sign(pkg, key)
    (pkg / "pkg" / "__init__.py").unlink()
    with pytest.raises(ps.PluginSignatureError) as exc:
        ps.verify_package(pkg, envelope, _trusted(key))
    assert exc.value.code == "PLUGIN_SIGNATURE_TAMPERED"


def test_unknown_key_rejected(pkg, key):
    envelope = _sign(pkg, key)
    with pytest.raises(ps.PluginSignatureError) as exc:
        ps.verify_package(pkg, envelope, {})
    assert exc.value.code == "PLUGIN_SIGNATURE_UNKNOWN_KEY"


def test_revoked_key_rejected(pkg, key):
    envelope = _sign(pkg, key)
    with pytest.raises(ps.PluginSignatureError) as exc:
        ps.verify_package(pkg, envelope, _trusted(key, status="revoked"),
                          allow_retired=True)
    assert exc.value.code == "PLUGIN_SIGNATURE_KEY_REVOKED"


def test_retired_key_requires_explicit_allow(pkg, key):
    envelope = _sign(pkg, key)
    with pytest.raises(ps.PluginSignatureError) as exc:
        ps.verify_package(pkg, envelope, _trusted(key, status="retired"))
    assert exc.value.code == "PLUGIN_SIGNATURE_KEY_RETIRED"
    assert ps.verify_package(pkg, envelope, _trusted(key, status="retired"),
                             allow_retired=True)["file_count"] == 2


def test_expired_key_rejected(pkg, key):
    now = datetime(2026, 11, 23, tzinfo=timezone.utc)
    envelope = _sign(pkg, key)
    expired = (now - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    with pytest.raises(ps.PluginSignatureError) as exc:
        ps.verify_package(pkg, envelope, _trusted(key, not_after=expired), now=now)
    assert exc.value.code == "PLUGIN_SIGNATURE_KEY_EXPIRED"


def test_bad_signature_rejected(pkg, key):
    envelope = _sign(pkg, key)
    envelope["signature"] = base64.b64encode(b"\x00" * 64).decode()
    with pytest.raises(ps.PluginSignatureError) as exc:
        ps.verify_package(pkg, envelope, _trusted(key))
    assert exc.value.code == "PLUGIN_SIGNATURE_INVALID"


def test_package_digest_mismatch_rejected(pkg, key):
    envelope = _sign(pkg, key, package_sha256="0" * 64)
    with pytest.raises(ps.PluginSignatureError) as exc:
        ps.verify_package(pkg, envelope, _trusted(key))
    assert exc.value.code == "PLUGIN_SIGNATURE_TAMPERED"


def test_missing_signed_at_is_invalid_not_crash(pkg, key):
    envelope = _sign(pkg, key)
    envelope.pop("signed_at")
    with pytest.raises(ps.PluginSignatureError) as exc:
        ps.verify_package(pkg, envelope, _trusted(key))
    assert exc.value.code == "PLUGIN_SIGNATURE_INVALID"


def test_malformed_envelope_rejected(pkg, key):
    envelope = _sign(pkg, key)
    envelope.pop("files")
    with pytest.raises(ps.PluginSignatureError) as exc:
        ps.verify_package(pkg, envelope, _trusted(key))
    assert exc.value.code == "PLUGIN_SIGNATURE_MALFORMED"


def test_unsafe_envelope_path_rejected(pkg, key):
    envelope = _sign(pkg, key)
    envelope["files"] = {"../escape.py": "a" * 64}
    with pytest.raises(ps.PluginSignatureError) as exc:
        ps.verify_package(pkg, envelope, _trusted(key))
    assert exc.value.code == "PLUGIN_SIGNATURE_MALFORMED"


def test_symlink_rejected(pkg, key):
    envelope = _sign(pkg, key)
    link = pkg / "link.py"
    try:
        link.symlink_to(pkg / "plugin.json")
    except (OSError, NotImplementedError):
        pytest.skip("当前环境不能创建符号链接")
    with pytest.raises(ps.PluginSignatureError) as exc:
        ps.verify_package(pkg, envelope, _trusted(key))
    assert exc.value.code == "PLUGIN_PACKAGE_UNSAFE_PATH"


def test_read_signature_and_installed_verify(pkg, key, tmp_path):
    envelope = _sign(pkg, key)
    (pkg / ps.SIGNATURE_FILENAME).write_text(
        json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    keys_path = tmp_path / "plugin_trusted_keys.json"
    keys_path.write_text(json.dumps({"schema": 1, "keys": list(_trusted(key).values())}),
                        encoding="utf-8")
    assert ps.read_signature(pkg)["key_id"] == "k1"
    assert ps.verify_installed_package(pkg, keys_path)["file_count"] == 2


def test_load_trusted_keys_rejects_duplicates(tmp_path, key):
    record = _trusted(key)["k1"]
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({"schema": 1, "keys": [record, record]}), encoding="utf-8")
    with pytest.raises(ps.PluginSignatureError) as exc:
        ps.load_trusted_keys(path)
    assert exc.value.code == "PLUGIN_TRUST_MALFORMED"


def test_load_trusted_keys_rejects_bad_public_key(tmp_path):
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({"schema": 1, "keys": [{
        "key_id": "k1", "alg": "ed25519",
        "public_key_b64": base64.b64encode(b"short").decode(), "status": "active"}]}),
        encoding="utf-8")
    with pytest.raises(ps.PluginSignatureError) as exc:
        ps.load_trusted_keys(path)
    assert exc.value.code == "PLUGIN_TRUST_MALFORMED"
