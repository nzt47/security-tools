"""C 级（只读脱敏）取值口径——**永不返回明文**（TASK-S7-01 硬约束 3）

【裁定来源】
    沿用 S4-01 裁定 B 的掩码口径：**原始值不出现**在任何 HTTP 响应体、日志与
    前端状态里；只暴露三件事：① 是否已配置；② 值的**不可逆指纹**（前 8 位
    sha256，供运维判断"换没换过"）；③ 值长度分档（`short/medium/long`，不暴露
    精确长度，避免成为侧信道）。

【为什么不用 `filter_string()` 做主要手段】
    `agent/utils/sensitive_data_filter.py` 是**启发式脱敏**（按形态匹配），
    对"看起来不像密钥"的值会原样放行。本模块把它当**第二层兜底**：
    先按白名单口径只输出指纹，最后再过一遍脱敏过滤器——
    于是「漏了」也还有一层网（双保险，而不是二选一）。

【反例守卫】
    `assert_no_plaintext()` 供路由与用例调用：若响应体里出现了明文值，
    直接抛错（而不是"测试里断言一下"）——防"断言失败时把明文打进日志"。
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional

#: 指纹长度（前 N 位十六进制）
FINGERPRINT_LEN = 8

#: 长度分档阈值（不暴露精确长度）
_LEN_BUCKETS = ((8, "short"), (32, "medium"), (10 ** 9, "long"))


def fingerprint(value: Any) -> str:
    """值的不可逆指纹（sha256 前 8 位；不可从指纹反推原值）"""
    material = str(value).encode("utf-8", errors="replace")
    return hashlib.sha256(material).hexdigest()[:FINGERPRINT_LEN]


def length_bucket(value: Any) -> str:
    """值长度分档（short / medium / long）"""
    size = len(str(value))
    for limit, label in _LEN_BUCKETS:
        if size <= limit:
            return label
    return "long"                                      # pragma: no cover


def _defensive_filter(text: str) -> str:
    """第二层兜底：再过一遍仓库既有脱敏过滤器（不可用时原样返回）"""
    try:
        from agent.utils.sensitive_data_filter import filter_string
        return str(filter_string(text))
    except Exception:                                  # noqa: BLE001 兜底不阻断
        return text


def mask_display(value: Any, *, configured: Optional[bool] = None) -> Dict[str, Any]:
    """C 级值的对外展示口径（**只出指纹与是否配置，不出明文**）

    `value is None` 一律视为"未配置"（**不能**因为 `str(None) == "None"` 就
    误判成已配置——S7-01 实测踩过这个坑，会让 UI 显示"已配置"的假象）。

    Returns:
        `{display_value, configured, masked, fingerprint, value_len_bucket}`
    """
    if configured is None:
        is_set = value is not None and bool(str(value).strip())
    else:
        is_set = bool(configured)
    if not is_set:
        return {
            "display_value": "未配置", "configured": False, "masked": True,
            "fingerprint": "", "value_len_bucket": "",
        }
    fp = fingerprint(value)
    display = _defensive_filter(f"已配置（指纹 {fp}）")
    return {
        "display_value": display, "configured": True, "masked": True,
        "fingerprint": fp, "value_len_bucket": length_bucket(value),
    }


def assert_no_plaintext(payload: Any, raw_value: Any, *, label: str = "") -> None:
    """守卫：响应体里**不得出现明文原值**

    Args:
        payload: 待检查的响应体（dict/list/str）。
        raw_value: 原始明文值。
        label: 出错时的定位标签。

    Raises:
        AssertionError: 命中明文（**不把明文写进异常信息**，只写指纹）。
    """
    secret = str(raw_value or "")
    if not secret or len(secret) < 4:
        return                       # 过短的值无法可靠匹配（且不构成可用凭据）
    blob = repr(payload)
    if secret in blob:
        raise AssertionError(
            f"发现明文泄漏（{label or 'payload'}），命中值指纹={fingerprint(secret)}")


def mask_for_log(value: Any) -> str:
    """日志口径：只允许指纹进日志（**明文绝不进日志**）"""
    return f"<masked:{fingerprint(value)}>" if str(value or "") else "<empty>"


__all__ = [
    "FINGERPRINT_LEN", "fingerprint", "length_bucket", "mask_display",
    "assert_no_plaintext", "mask_for_log",
]
