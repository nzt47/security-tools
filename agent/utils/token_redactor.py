
import logging
import json
import uuid

logger = logging.getLogger(__name__)


def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通用敏感 Token 脱敏工具

封装 P0-SEC-001 / P0-SEC-002 修复后的正则脱敏逻辑，供多个模块复用。

设计原则：
- Bearer 模式独立处理（P0-SEC-001 教训：split('=') 保留 token 值）
- 正则使用 [^&\\s]+ 限定边界（P0-SEC-002 教训：\\S+ 贪婪吞噬 URL 参数）
- 纯函数无副作用，输入不被修改

使用示例：
    from agent.utils.token_redactor import redact_sensitive_tokens

    # 字符串内嵌 token 脱敏
    redacted = redact_sensitive_tokens("token=secret&page=1")
    # → 'token=[REDACTED]&page=1'

    # Bearer token 脱敏
    redacted = redact_sensitive_tokens("Bearer abc.def.ghi+jkl=")
    # → 'Bearer [REDACTED]'
"""

import re
from typing import Any, Callable, List, Optional, Pattern

# 脱敏占位符
REDACTED_PLACEHOLDER = "[REDACTED]"

# P0-SEC-002 修复：[^&\s]+ 遇 & 或空白停止，保留相邻 URL 参数
# 默认敏感 token 关键词（不区分大小写）
DEFAULT_SENSITIVE_KEYS = [
    "token", "api_key", "api-key", "apikey",
    "secret", "password", "passwd", "pwd",
    "access_token", "refresh_token",
]

# Bearer token 正则：Bearer\s+ 后跟字母数字和特殊字符，= 可选作为填充
BEARER_PATTERN: Pattern[str] = re.compile(
    r"(?i)Bearer\s+[A-Za-z0-9\-._~+/]+=*"
)


def _build_token_pattern(sensitive_keys: Optional[List[str]] = None) -> Pattern[str]:
    """构建敏感 token 正则模式

    Args:
        sensitive_keys: 自定义敏感关键词列表，None 则使用默认

    Returns:
        编译后的正则 Pattern，匹配格式 key=value 或 key:value
    """
    keys = sensitive_keys if sensitive_keys is not None else DEFAULT_SENSITIVE_KEYS
    # 转义关键词中的正则特殊字符
    escaped = "|".join(re.escape(k) for k in keys)
    # P0-SEC-002: [^&\s]+ 限定边界，避免贪婪吞噬
    return re.compile(rf"(?i)({escaped})\s*[=:]\s*[^&\s]+")


def redact_token_match(m: "re.Match[str]") -> str:
    """敏感 token 匹配替换函数

    P0-SEC-001 修复：Bearer 模式独立处理，避免 split('=') 保留 token 值。
    - Bearer xxx → Bearer [REDACTED]（完整脱敏 token 值）
    - key=value → key=[REDACTED]
    - key:value → key: [REDACTED]

    Args:
        m: 正则匹配对象

    Returns:
        脱敏后的替换字符串
    """
    matched = m.group(0)
    # Bearer 模式：整段替换，不保留 token 值
    if matched.lower().startswith("bearer"):
        return f"Bearer {REDACTED_PLACEHOLDER}"
    # key=value 模式：保留 key，脱敏 value
    if "=" in matched:
        return matched.split("=")[0] + f"={REDACTED_PLACEHOLDER}"
    # key:value 模式：保留 key，脱敏 value
    if ":" in matched:
        return matched.split(":")[0] + f": {REDACTED_PLACEHOLDER}"
    return REDACTED_PLACEHOLDER


def redact_bearer_token(text: str) -> str:
    """专门处理字符串中的 Bearer Token

    Args:
        text: 输入字符串

    Returns:
        脱敏后的字符串，Bearer token 值被替换为 [REDACTED]
    """
    if not isinstance(text, str):
        return text
    return BEARER_PATTERN.sub(redact_token_match, text)


def redact_sensitive_tokens(
    text: str,
    sensitive_keys: Optional[List[str]] = None,
) -> str:
    """脱敏字符串中内嵌的敏感 token

    依次应用 token=xxx 正则和 Bearer 正则，确保两种模式都被覆盖。

    Args:
        text: 输入字符串
        sensitive_keys: 自定义敏感关键词列表，None 则使用默认

    Returns:
        脱敏后的字符串

    Examples:
        >>> redact_sensitive_tokens("token=secret&page=1")
        'token=[REDACTED]&page=1'
        >>> redact_sensitive_tokens("Bearer abc.def.ghi=")
        'Bearer [REDACTED]'
    """
    if not isinstance(text, str):
        return text

    pattern = _build_token_pattern(sensitive_keys)
    result = pattern.sub(redact_token_match, text)
    result = BEARER_PATTERN.sub(redact_token_match, result)
    return result


def redact_recursive(
    obj: Any,
    sensitive_keys: Optional[List[str]] = None,
    is_sensitive_key_fn: Optional[Callable[[str], bool]] = None,
) -> Any:
    """递归脱敏任意数据结构

    - dict：键命中敏感模式 → 值替换 [REDACTED]
    - list/tuple：递归每个元素
    - str：内嵌 token=xxx 模式 → 替换为 token=[REDACTED]
    - 其他：原样返回

    Args:
        obj: 待脱敏的对象（dict/list/tuple/str/其他）
        sensitive_keys: 自定义敏感关键词列表
        is_sensitive_key_fn: 自定义键名判定函数，接收 str 返回 bool

    Returns:
        脱敏后的新对象（原对象不被修改）
    """
    if isinstance(obj, dict):
        return {
            k: (
                REDACTED_PLACEHOLDER
                if is_sensitive_key_fn and is_sensitive_key_fn(k)
                else redact_recursive(v, sensitive_keys, is_sensitive_key_fn)
            )
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [redact_recursive(item, sensitive_keys, is_sensitive_key_fn) for item in obj]
    if isinstance(obj, tuple):
        return tuple(redact_recursive(item, sensitive_keys, is_sensitive_key_fn) for item in obj)
    if isinstance(obj, str):
        return redact_sensitive_tokens(obj, sensitive_keys)
    return obj


__all__ = [
    "REDACTED_PLACEHOLDER",
    "DEFAULT_SENSITIVE_KEYS",
    "BEARER_PATTERN",
    "redact_token_match",
    "redact_bearer_token",
    "redact_sensitive_tokens",
    "redact_recursive",
]


def _safe_call(func, *args, action="safe_call", **kwargs):
    """安全调用包装器——捕获异常并记录结构化日志后重新抛出

    用于边界显性化：可能失败的操作应通过此包装器调用，
    确保异常被记录后再向上传播，而非静默吞掉。
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.error(json.dumps({
            "trace_id": _trace_id(),
            "module_name": "token_redactor",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
