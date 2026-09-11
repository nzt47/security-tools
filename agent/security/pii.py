"""认证 IP 的 PII 口径（S2-02 遗留 #11 收口）

【Owner 裁定（2026-09-11，「掩码 + HMAC 哈希」双字段）】
    审计链永久保留（删不得），真实 IP 入链等于永久留存 PII。故入链**两个字段**：
      1. ``actor_ip_masked``——沿用仓库既有脱敏口径（`10.0.0.7` → `10.0.xxx.xxx`，
         `agent/utils/sensitive_data_filter.py::mask_ip`）；
      2. ``actor_ip_hash``——HMAC-SHA256，密钥存 SecretStore，**不可还原**；
    **原始 IP 不落盘**。保留关联能力：同一 IP → 同哈希，可做「同一来源多次越权」分析。

【不做】
    - 不写 IP 原文入链（方案②）；
    - 不用无密钥裸哈希（可被枚举反查）。

【密钥（SecretStore 口径）】
    云枢当前无外部 Keychain（审计根签名键同样按「无密钥则显式降级」处理，
    见 `agent/audit/chain.py::_DEGRADED_NO_KEY`）。本模块沿用同一诚实口径，
    解析顺序：
      1. ``CP_IP_HMAC_KEY``      环境变量/`.env`（十六进制或任意字节串）；
      2. ``CP_IP_HMAC_KEY_FILE`` 密钥文件路径（600 权限，内容即密钥）；
      3. 默认路径 ``data/audit/ip_hmac_key``（**存在才用**，不自动生成）。
    **三者皆无 → 显式降级**：仍写入掩码字段，``actor_ip_hash_status`` 标注
    ``degraded_no_key``，**绝不退化为写入 IP 原文**。
    显式开启 ``CP_IP_HMAC_AUTOGEN=1`` 时才在默认路径生成 32 字节随机密钥
    （0600 权限；默认关闭以免测试/只读环境产生落盘副作用）。
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

logger = logging.getLogger("agent.security.pii")

# ── 入链字段名（审计/事件载荷的稳定契约；勿改名，改则历史链不可关联） ──
FIELD_IP_MASKED = "actor_ip_masked"
FIELD_IP_HASH = "actor_ip_hash"
FIELD_IP_HASH_STATUS = "actor_ip_hash_status"
FIELD_IP_PRESENT = "actor_ip_present"

#: 哈希状态口径
STATUS_HMAC = "hmac_sha256"            # 正常：HMAC-SHA256 已计算
STATUS_DEGRADED = "degraded_no_key"    # 无密钥：仅掩码，显式标注
STATUS_NO_IP = "no_ip"                 # 无 IP 线索（不写掩码/哈希）

_ENV_KEY = "CP_IP_HMAC_KEY"
_ENV_KEY_FILE = "CP_IP_HMAC_KEY_FILE"
_ENV_AUTOGEN = "CP_IP_HMAC_AUTOGEN"

#: 默认密钥落点（与审计根签名键同目录；**仅存在才读**）
DEFAULT_KEY_PATH = str(Path(__file__).resolve().parent.parent.parent
                       / "data" / "audit" / "ip_hmac_key")

#: 原始 IPv4 形态（`contains_raw_ip` 自检用；掩码字段 `10.0.xxx.xxx` 不构成匹配）
_IPV4_RE = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")

_lock = threading.RLock()
_cached_key: Optional[bytes] = None
_key_loaded = False


def mask_ip(ip: str) -> str:
    """脱敏 IP（保留前两段；对齐仓库既有口径）

    优先复用 `agent.utils.sensitive_data_filter.mask_ip`（单一实现来源），
    导入失败时用同口径兜底（仅处理 IPv4 四段；其余原样返回）。

    注意：IPv6 / 非法形态**不做猜测性截断**，由调用方按 `identity` 侧
    `remote_addr` 口径自行收敛（`ui:<addr>`）。
    """
    text = str(ip or "")
    if not text:
        return ""
    try:
        from agent.utils.sensitive_data_filter import mask_ip as _mask
        return _mask(text)
    except Exception:  # noqa: BLE001 兜底：同口径，不阻断审批/审计
        parts = text.split(".")
        if len(parts) == 4:
            return f"{parts[0]}.{parts[1]}.xxx.xxx"
        return text


def resolve_hmac_key(env: Optional[Mapping[str, str]] = None,
                     *, key_path: str = "") -> Optional[bytes]:
    """解析 HMAC 密钥（**只返回密钥字节，绝不返回其来源文件之外的内容**）

    Returns:
        密钥字节；无密钥 → None（调用方据此显式降级）。
    """
    env = env if env is not None else os.environ
    raw = str(env.get(_ENV_KEY, "") or "").strip()
    if raw:
        return _decode_key(raw)
    explicit_path = str(env.get(_ENV_KEY_FILE, "") or "").strip() or str(key_path or "")
    if explicit_path:
        data = _read_key_file(explicit_path)
        return data if data else None
    data = _read_key_file(DEFAULT_KEY_PATH)
    if data:
        return data
    # 显式开启才自动生成（默认关闭：不产生落盘副作用）
    if _autogen_enabled(env):
        return _autogen_key()
    return None


def _read_key_file(path: str) -> Optional[bytes]:
    """读密钥文件（不存在 / 为空 / 不可读 → None，且不抛异常）"""
    try:
        if not path or not os.path.exists(path):
            return None
        data = Path(path).read_bytes().strip()
        if not data:
            logger.warning("[PII] HMAC 密钥文件为空（按无密钥降级）: %s", path)
            return None
        return data
    except OSError as e:
        logger.warning("[PII] HMAC 密钥文件读取失败（按无密钥降级）: %s", e)
        return None


def _decode_key(raw: str) -> bytes:
    """密钥解码：偶数长度纯十六进制 → 按 hex；否则按原始字节串"""
    text = raw.strip()
    if len(text) >= 16 and len(text) % 2 == 0 and all(
            c in "0123456789abcdefABCDEF" for c in text):
        try:
            return bytes.fromhex(text)
        except ValueError:
            pass
    return text.encode("utf-8")


def _autogen_enabled(env: Mapping[str, str]) -> bool:
    return str(env.get(_ENV_AUTOGEN, "") or "").strip().lower() in (
        "1", "true", "yes", "on")


def _autogen_key() -> Optional[bytes]:
    """生成并落盘 32 字节随机密钥（0600；失败 → 降级，不阻断）"""
    try:
        path = Path(DEFAULT_KEY_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        key = secrets.token_bytes(32)
        path.write_bytes(key)
        try:
            os.chmod(path, 0o600)
        except OSError as e:  # Windows/受限文件系统：权限设置失败不阻断
            logger.warning("[PII] HMAC 密钥文件权限设置失败: %s", e)
        logger.info("[PII] 已生成 IP HMAC 密钥（CP_IP_HMAC_AUTOGEN=1）: %s", path)
        return key
    except OSError as e:
        logger.warning("[PII] HMAC 密钥自动生成失败（按无密钥降级）: %s", e)
        return None


def current_hmac_key() -> Optional[bytes]:
    """进程级密钥（首次访问解析一次；`reset_pii()` 可复位）"""
    global _cached_key, _key_loaded
    with _lock:
        if not _key_loaded:
            _cached_key = resolve_hmac_key()
            _key_loaded = True
            if _cached_key is None:
                logger.info("[PII] 未配置 IP HMAC 密钥 → 降级为仅掩码"
                            "（原始 IP 仍不落盘；配置 %s 可启用可关联哈希）", _ENV_KEY)
        return _cached_key


def set_hmac_key(key: Optional[bytes]) -> None:
    """注入密钥（测试隔离 / 密钥轮换）；传 None 使下次访问重新解析"""
    global _cached_key, _key_loaded
    with _lock:
        _cached_key = key
        _key_loaded = key is not None


def reset_pii() -> None:
    """复位密钥缓存（测试隔离）"""
    global _cached_key, _key_loaded
    with _lock:
        _cached_key = None
        _key_loaded = False


def hash_ip(ip: str, *, key: Optional[bytes] = None) -> Optional[str]:
    """HMAC-SHA256(ip)（不可还原；同 IP → 同哈希，可做同源分析）

    Returns:
        十六进制摘要；无密钥 → None（**不退化**为裸哈希，裸哈希可被枚举反查）。
    """
    text = str(ip or "").strip()
    if not text:
        return None
    resolved = key if key is not None else current_hmac_key()
    if not resolved:
        return None
    return hmac.new(resolved, text.encode("utf-8"), hashlib.sha256).hexdigest()


def ip_pii_fields(ip: str, *, key: Optional[bytes] = None) -> Dict[str, Any]:
    """构造入链的 IP PII 叶子字段（**原始 IP 绝不出现**）

    Returns:
        {
          "actor_ip_present": bool,
          "actor_ip_masked": "10.0.xxx.xxx",
          "actor_ip_hash": "<hex>" | 缺省（无密钥时**不写该键**，避免下游误读为空哈希）,
          "actor_ip_hash_status": "hmac_sha256" | "degraded_no_key" | "no_ip",
        }
    """
    text = str(ip or "").strip()
    if not text:
        return {FIELD_IP_PRESENT: False, FIELD_IP_HASH_STATUS: STATUS_NO_IP}
    fields: Dict[str, Any] = {
        FIELD_IP_PRESENT: True,
        FIELD_IP_MASKED: mask_ip(text),
    }
    digest = hash_ip(text, key=key)
    if digest:
        fields[FIELD_IP_HASH] = digest
        fields[FIELD_IP_HASH_STATUS] = STATUS_HMAC
    else:
        fields[FIELD_IP_HASH_STATUS] = STATUS_DEGRADED
    return fields


def same_source(payload_a: Mapping[str, Any],
                payload_b: Mapping[str, Any]) -> Optional[bool]:
    """两个已入链载荷是否来自同一 IP（关联能力）

    Returns:
        True/False（两侧都有哈希时）；任一侧无哈希 → None（**不猜测**）。
    """
    ha = str((payload_a or {}).get(FIELD_IP_HASH, "") or "")
    hb = str((payload_b or {}).get(FIELD_IP_HASH, "") or "")
    if not ha or not hb:
        return None
    return hmac.compare_digest(ha, hb)


def contains_raw_ip(payload: Any) -> bool:
    """自检工具：载荷/文本中是否出现**原始 IPv4**（测试与门禁断言用）

    Why 需要：裁定 B 的硬约束是「原始 IP 不落盘」。掩码字段本身形如
    `10.0.xxx.xxx`，不构成 IP，故不会被本函数命中。
    """
    text = str(payload or "")
    return bool(_IPV4_RE.search(text))


__all__ = [
    "FIELD_IP_MASKED", "FIELD_IP_HASH", "FIELD_IP_HASH_STATUS", "FIELD_IP_PRESENT",
    "STATUS_HMAC", "STATUS_DEGRADED", "STATUS_NO_IP", "DEFAULT_KEY_PATH",
    "mask_ip", "hash_ip", "ip_pii_fields", "same_source", "contains_raw_ip",
    "resolve_hmac_key", "current_hmac_key", "set_hmac_key", "reset_pii",
]
