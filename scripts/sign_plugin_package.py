#!/usr/bin/env python3
"""离线签/验第三方插件包（P2，2026-11-23）。

用法：
  python scripts/sign_plugin_package.py sign --package <dir> --key <pem> --key-id <id>
  python scripts/sign_plugin_package.py verify --package <dir> --keys config/plugin_trusted_keys.json

私钥只从本地文件读，**不打印、不写日志、不进仓库**；CI 只跑 verify，不持有私钥。
输出固定为 JSON（不含路径以外的敏感信息），退出码 0=通过 / 2=失败。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
)

from agent.extensions import plugin_signature as ps  # noqa: E402


def _load_private_key(path: Path, password_env: str = "") -> Ed25519PrivateKey:
    password = None
    if password_env:
        raw = os.environ.get(password_env, "")
        if not raw:
            raise ValueError("私钥口令环境变量为空")
        password = raw.encode("utf-8")
    data = path.read_bytes()
    key = serialization.load_pem_private_key(data, password=password)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("私钥不是 Ed25519")
    return key


def _public_b64(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def build_envelope(package: Path, key: Ed25519PrivateKey, key_id: str,
                   signed_at: str = "") -> dict:
    files = ps.scan_package(package)
    body = {
        "schema": ps.SIGNATURE_SCHEMA,
        "key_id": str(key_id),
        "signed_at": signed_at or datetime.now(timezone.utc).replace(
            microsecond=0).isoformat().replace("+00:00", "Z"),
        "package_sha256": ps._sha256_hex(ps.canonical_json(files)),
        "files": files,
    }
    signature = key.sign(ps.canonical_json(body))
    return dict(body, signature=base64.b64encode(signature).decode())


def cmd_sign(args) -> int:
    package = Path(args.package)
    key = _load_private_key(Path(args.key), args.password_env)
    envelope = build_envelope(package, key, args.key_id, args.signed_at)
    out = Path(args.out) if args.out else package / ps.SIGNATURE_FILENAME
    out.write_text(json.dumps(envelope, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "key_id": envelope["key_id"],
        "package_sha256": envelope["package_sha256"],
        "file_count": len(envelope["files"]),
        "out": str(out),
        "public_key_b64": _public_b64(key),
    }, ensure_ascii=False, sort_keys=True))
    return 0


def cmd_verify(args) -> int:
    package = Path(args.package)
    keys = ps.load_trusted_keys(Path(args.keys))
    envelope = ps.read_signature(package)
    result = ps.verify_package(package, envelope, keys)
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, sort_keys=True))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="云枢插件包离线签名/校验")
    sub = parser.add_subparsers(dest="command", required=True)

    sign = sub.add_parser("sign", help="为插件包生成 plugin.sig.json")
    sign.add_argument("--package", required=True)
    sign.add_argument("--key", required=True, help="Ed25519 私钥 PEM 路径")
    sign.add_argument("--key-id", required=True)
    sign.add_argument("--password-env", default="", help="私钥口令所在环境变量名")
    sign.add_argument("--signed-at", default="")
    sign.add_argument("--out", default="")
    sign.set_defaults(func=cmd_sign)

    verify = sub.add_parser("verify", help="按受信公钥表校验插件包")
    verify.add_argument("--package", required=True)
    verify.add_argument("--keys", default=str(_REPO_ROOT / "config" / "plugin_trusted_keys.json"))
    verify.set_defaults(func=cmd_verify)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ps.PluginSignatureError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "error": exc.message},
                         ensure_ascii=False, sort_keys=True))
        return 2
    except Exception as exc:  # noqa: BLE001 CLI 只回类型，不回堆栈/路径
        print(json.dumps({"ok": False, "code": "SIGN_TOOL_ERROR",
                          "error": type(exc).__name__}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
