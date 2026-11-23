"""插件包签名校验（P2，2026-11-23）。

【边界：本模块只做校验】
- 不签包：签名工具是离线脚本，私钥不进仓库/服务器/CI（见
  docs/audit/2026-11-23-plugin-p2-decision.md §3）。
- 不接线：安装器 `plugins_installer.install_plugin` 尚未调用本模块；
  D4 只批准「方案 + 校验器（不接线）」，接线要单独一批 + 开关登记。
- 不解决沙箱：签名只回答「这堆字节是不是受信发布者签的」，
  已签名的代码仍以当前用户身份运行。

【包格式】分离签名文件 `plugin.sig.json`（不参与自身摘要）：
    {"schema": 1, "key_id": "yunshu-release-2026",
     "signed_at": "2026-11-23T12:00:00Z",
     "package_sha256": "<sha256(canonical_json(files))>",
     "files": {"plugin.json": "<sha256>", ...},
     "signature": "<base64(Ed25519(canonical_json(body)))>"}

【校验顺序】签名与文件树都必须过：
1. key_id 在受信表、未撤销、未过期；安装要求 active，加载可放行 retired；
2. Ed25519 验签（对规范化 JSON，排除 signature 字段）；
3. package_sha256 == sha256(canonical_json(files))；
4. 实际文件树与 files **完全相等**（多/少一个文件都算篡改）；
5. 拒符号链接 / 路径穿越 / 超限。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

SIGNATURE_FILENAME = "plugin.sig.json"
SIGNATURE_SCHEMA = 1
_TRUSTED_KEYS_SCHEMA = 1
_MAX_FILES = 5000
_MAX_TOTAL_BYTES = 64 * 1024 * 1024
_HEX_LEN = 64
_KEY_STATUSES = frozenset({"active", "retired", "revoked"})


class PluginSignatureError(RuntimeError):
    """签名校验失败；code 是稳定的机器可读错误码。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


def canonical_json(obj: Any) -> bytes:
    """签名/摘要使用的唯一序列化形态（排序键、无空白、UTF-8）。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _b64decode(value: str, code: str) -> bytes:
    try:
        return base64.b64decode(str(value), validate=True)
    except (ValueError, binascii.Error):
        raise PluginSignatureError(code, "签名材料不是合法 base64") from None


def _is_hex_digest(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != _HEX_LEN:
        return False
    return all(ch in "0123456789abcdef" for ch in value)


def _safe_rel_path(rel: str) -> bool:
    if not rel or rel.startswith("/") or rel.startswith("\\"):
        return False
    parts = rel.replace("\\", "/").split("/")
    return all(part not in ("", ".", "..") for part in parts)


def scan_package(root: Path) -> Dict[str, str]:
    """枚举包内文件并算 sha256；拒符号链接、路径穿越与超限。"""
    root = Path(root)
    if root.is_symlink():
        raise PluginSignatureError("PLUGIN_PACKAGE_UNSAFE_PATH", "插件包根目录不能是符号链接")
    if not root.is_dir():
        raise PluginSignatureError("PLUGIN_PACKAGE_MISSING", "插件包目录不存在")
    files: Dict[str, str] = {}
    total = 0
    for path in root.rglob("*"):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise PluginSignatureError("PLUGIN_PACKAGE_UNSAFE_PATH", "插件包含符号链接")
        if path.is_dir():
            continue
        if not path.is_file():
            raise PluginSignatureError("PLUGIN_PACKAGE_UNSAFE_PATH", "插件包含非普通文件")
        if rel == SIGNATURE_FILENAME:
            continue
        if not _safe_rel_path(rel):
            raise PluginSignatureError("PLUGIN_PACKAGE_UNSAFE_PATH", "插件包含非法相对路径")
        try:
            size = path.stat().st_size
        except OSError:
            raise PluginSignatureError("PLUGIN_PACKAGE_UNREADABLE", "插件文件不可读") from None
        total += size
        if len(files) + 1 > _MAX_FILES or total > _MAX_TOTAL_BYTES:
            raise PluginSignatureError("PLUGIN_PACKAGE_TOO_LARGE", "插件包超过校验上限")
        try:
            files[rel] = _sha256_hex(path.read_bytes())
        except OSError:
            raise PluginSignatureError("PLUGIN_PACKAGE_UNREADABLE", "插件文件不可读") from None
    return files


def read_signature(root: Path) -> Dict[str, Any]:
    """读包根的 plugin.sig.json；缺失/非对象都按稳定错误码返回。"""
    path = Path(root) / SIGNATURE_FILENAME
    if not path.is_file():
        raise PluginSignatureError("PLUGIN_SIGNATURE_MISSING", "插件包缺少签名文件")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise PluginSignatureError("PLUGIN_SIGNATURE_MALFORMED", "签名文件不可解析") from None
    if not isinstance(payload, dict):
        raise PluginSignatureError("PLUGIN_SIGNATURE_MALFORMED", "签名文件不是对象")
    return payload


def load_trusted_keys(path: Path) -> Dict[str, Dict[str, Any]]:
    """读受信公钥表；返回 key_id -> record。公钥只从固定文件读。"""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise PluginSignatureError("PLUGIN_TRUST_MALFORMED", "受信公钥表不可读") from None
    if not isinstance(payload, dict) or payload.get("schema") != _TRUSTED_KEYS_SCHEMA:
        raise PluginSignatureError("PLUGIN_TRUST_MALFORMED", "受信公钥表 schema 不匹配")
    keys = payload.get("keys")
    if not isinstance(keys, list):
        raise PluginSignatureError("PLUGIN_TRUST_MALFORMED", "受信公钥表缺少 keys")
    result: Dict[str, Dict[str, Any]] = {}
    for item in keys:
        if not isinstance(item, dict):
            raise PluginSignatureError("PLUGIN_TRUST_MALFORMED", "受信公钥记录不是对象")
        key_id = str(item.get("key_id") or "").strip()
        if not key_id or key_id in result:
            raise PluginSignatureError("PLUGIN_TRUST_MALFORMED", "受信公钥 key_id 缺失或重复")
        if str(item.get("alg") or "").lower() != "ed25519":
            raise PluginSignatureError("PLUGIN_TRUST_MALFORMED", "只支持 ed25519")
        if str(item.get("status") or "") not in _KEY_STATUSES:
            raise PluginSignatureError("PLUGIN_TRUST_MALFORMED", "受信公钥 status 非法")
        raw = _b64decode(str(item.get("public_key_b64") or ""), "PLUGIN_TRUST_MALFORMED")
        if len(raw) != 32:
            raise PluginSignatureError("PLUGIN_TRUST_MALFORMED", "ed25519 公钥长度必须是 32")
        result[key_id] = dict(item, key_id=key_id)
    return result


def _now_utc(now: Optional[datetime]) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _key_expired(record: Mapping[str, Any], now: datetime) -> bool:
    raw = str(record.get("not_after") or "").strip()
    if not raw:
        return False
    try:
        deadline = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return True
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return now > deadline


def _select_key(key_id: str, trusted_keys: Mapping[str, Mapping[str, Any]],
                *, allow_retired: bool, now: datetime) -> Mapping[str, Any]:
    record = trusted_keys.get(key_id)
    if not isinstance(record, Mapping):
        raise PluginSignatureError("PLUGIN_SIGNATURE_UNKNOWN_KEY", "签名 key 不在受信表内")
    status = str(record.get("status") or "")
    if status not in _KEY_STATUSES:
        raise PluginSignatureError("PLUGIN_TRUST_MALFORMED", "受信公钥 status 非法")
    if status == "revoked":
        raise PluginSignatureError("PLUGIN_SIGNATURE_KEY_REVOKED", "签名 key 已撤销")
    if status == "retired" and not allow_retired:
        raise PluginSignatureError("PLUGIN_SIGNATURE_KEY_RETIRED", "签名 key 已退役，不再接受新安装")
    if _key_expired(record, now):
        raise PluginSignatureError("PLUGIN_SIGNATURE_KEY_EXPIRED", "签名 key 已过期")
    return record


def _validate_envelope(envelope: Mapping[str, Any]) -> None:
    if not isinstance(envelope, Mapping):
        raise PluginSignatureError("PLUGIN_SIGNATURE_MALFORMED", "签名文件不是对象")
    if envelope.get("schema") != SIGNATURE_SCHEMA:
        raise PluginSignatureError("PLUGIN_SIGNATURE_MALFORMED", "签名 schema 不匹配")
    if not str(envelope.get("key_id") or "").strip():
        raise PluginSignatureError("PLUGIN_SIGNATURE_MALFORMED", "签名缺少 key_id")
    files = envelope.get("files")
    if not isinstance(files, Mapping) or not files:
        raise PluginSignatureError("PLUGIN_SIGNATURE_MALFORMED", "签名缺少 files")
    for rel, digest in files.items():
        if not _safe_rel_path(str(rel)) or str(rel) == SIGNATURE_FILENAME:
            raise PluginSignatureError("PLUGIN_SIGNATURE_MALFORMED", "签名包含非法文件路径")
        if not _is_hex_digest(digest):
            raise PluginSignatureError("PLUGIN_SIGNATURE_MALFORMED", "文件摘要格式非法")
    if not _is_hex_digest(envelope.get("package_sha256")):
        raise PluginSignatureError("PLUGIN_SIGNATURE_MALFORMED", "package_sha256 格式非法")
    if not str(envelope.get("signature") or "").strip():
        raise PluginSignatureError("PLUGIN_SIGNATURE_MALFORMED", "缺少 signature")


def verify_package(root: Path, envelope: Mapping[str, Any],
                   trusted_keys: Mapping[str, Mapping[str, Any]],
                   *, allow_retired: bool = False,
                   now: Optional[datetime] = None) -> Dict[str, Any]:
    """校验包目录；通过返回摘要信息，失败抛 PluginSignatureError。"""
    _validate_envelope(envelope)
    key_id = str(envelope["key_id"]).strip()
    record = _select_key(key_id, trusted_keys, allow_retired=allow_retired,
                         now=_now_utc(now))

    raw_key = _b64decode(str(record.get("public_key_b64") or ""),
                         "PLUGIN_TRUST_MALFORMED")
    try:
        public_key = Ed25519PublicKey.from_public_bytes(raw_key)
    except ValueError:
        raise PluginSignatureError("PLUGIN_TRUST_MALFORMED", "ed25519 公钥不可用") from None

    body = {key: envelope.get(key) for key in
            ("schema", "key_id", "signed_at", "package_sha256", "files")}
    signature = _b64decode(str(envelope["signature"]), "PLUGIN_SIGNATURE_MALFORMED")
    try:
        public_key.verify(signature, canonical_json(body))
    except InvalidSignature:
        raise PluginSignatureError("PLUGIN_SIGNATURE_INVALID", "签名校验失败") from None

    declared = dict(body["files"])
    expected = _sha256_hex(canonical_json(declared))
    if expected != str(envelope["package_sha256"]):
        raise PluginSignatureError("PLUGIN_SIGNATURE_TAMPERED", "package_sha256 与文件清单不一致")

    actual = scan_package(root)
    if actual != declared:
        raise PluginSignatureError("PLUGIN_SIGNATURE_TAMPERED", "包内文件与签名清单不一致")
    return {
        "key_id": key_id,
        "package_sha256": expected,
        "file_count": len(actual),
    }


def verify_installed_package(root: Path, trusted_keys_path: Path,
                             *, allow_retired: bool = True,
                             now: Optional[datetime] = None) -> Dict[str, Any]:
    """已安装目录的复验入口（加载前调用；当前生产尚未接线）。"""
    envelope = read_signature(root)
    keys = load_trusted_keys(trusted_keys_path)
    return verify_package(root, envelope, keys,
                          allow_retired=allow_retired, now=now)
