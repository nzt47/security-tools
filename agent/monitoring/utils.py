#!/usr/bin/env python3
"""
可观测性模块公共工具函数

提供跨模块共享的工具函数和常量：
- 统计计算（百分位数、平均值等）
- 日志格式化
- 通用验证函数
- 时间工具函数
"""

import time
import logging
import uuid
import re
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)


# ============================================================================
# 统计计算工具
# ============================================================================

def calculate_percentiles(values: List[float]) -> Dict[str, float]:
    """计算数据的统计值（count, sum, avg, min, max, p50, p95, p99）

    Args:
        values: 数值列表

    Returns:
        包含统计值的字典
    """
    if not values:
        return {
            'count': 0,
            'sum': 0.0,
            'avg': 0.0,
            'min': 0.0,
            'max': 0.0,
            'p50': 0.0,
            'p95': 0.0,
            'p99': 0.0
        }

    sorted_values = sorted(values)
    n = len(sorted_values)
    total = sum(values)

    def _percentile(p: float) -> float:
        """计算百分位数"""
        idx = min(int(n * p), n - 1)
        return sorted_values[idx]

    return {
        'count': n,
        'sum': total,
        'avg': total / n,
        'min': sorted_values[0],
        'max': sorted_values[-1],
        'p50': _percentile(0.50),
        'p95': _percentile(0.95),
        'p99': _percentile(0.99)
    }


def calculate_histogram_stats(values: List[float]) -> Dict[str, float]:
    """计算直方图统计数据（calculate_percentiles 的别名）"""
    return calculate_percentiles(values)


# ============================================================================
# 追踪 ID 工具
# ============================================================================

def generate_trace_id(length: int = 16) -> str:
    """生成十六进制追踪 ID

    Args:
        length: ID 长度（字符数），默认 16

    Returns:
        十六进制字符串
    """
    return uuid.uuid4().hex[:length]


def generate_span_id() -> str:
    """生成 16 位十六进制 Span ID"""
    return uuid.uuid4().hex[:16]


def is_valid_hex_string(s: str, min_length: int = 1, max_length: int = 64) -> bool:
    """验证字符串是否为有效的十六进制字符串

    Args:
        s: 要验证的字符串
        min_length: 最小长度
        max_length: 最大长度

    Returns:
        True 如果是有效的十六进制字符串
    """
    if not s or len(s) < min_length or len(s) > max_length:
        return False
    try:
        int(s, 16)
        return True
    except ValueError:
        return False


# ============================================================================
# 日志格式化工具
# ============================================================================

def format_structured_log(
    trace_id: Optional[str] = None,
    module_name: str = "",
    action: str = "",
    duration_ms: Optional[float] = None,
    **extra
) -> str:
    """格式化结构化日志为 JSON 字符串

    遵循"存在即可见"原则，所有核心业务逻辑节点必须输出包含
    trace_id、module_name、action、duration_ms 字段的 JSON 日志。

    Args:
        trace_id: 追踪 ID
        module_name: 模块名称
        action: 操作名称
        duration_ms: 耗时（毫秒）
        **extra: 额外字段

    Returns:
        JSON 格式的日志字符串
    """
    log_data = {
        "trace_id": trace_id or "unknown",
        "module_name": module_name,
        "action": action,
        "timestamp": time.time(),
    }
    if duration_ms is not None:
        log_data["duration_ms"] = round(duration_ms, 2)
    log_data.update(extra)
    return _safe_json_dumps(log_data)


def _safe_json_dumps(data: Dict[str, Any]) -> str:
    """安全的 JSON 序列化，失败时返回简单字符串"""
    try:
        import json
        return json.dumps(data, ensure_ascii=False, default=str)
    except Exception:
        return str(data)


# ============================================================================
# 安全工具
# ============================================================================

# 敏感字段名称模式（不区分大小写）
_SENSITIVE_PATTERNS = [
    re.compile(r'password', re.IGNORECASE),
    re.compile(r'secret', re.IGNORECASE),
    re.compile(r'token', re.IGNORECASE),
    re.compile(r'api[_-]?key', re.IGNORECASE),
    re.compile(r'private[_-]?key', re.IGNORECASE),
    re.compile(r'credit[_-]?card', re.IGNORECASE),
    re.compile(r'ssn', re.IGNORECASE),
    re.compile(r'phone', re.IGNORECASE),
    re.compile(r'email', re.IGNORECASE),
]


def mask_sensitive_value(value: str) -> str:
    """脱敏敏感值

    Args:
        value: 原始值

    Returns:
        脱敏后的值
    """
    if not value or not isinstance(value, str):
        return value
    if len(value) <= 4:
        return "****"
    return value[:2] + "****" + value[-2:]


def is_sensitive_field(field_name: str) -> bool:
    """判断字段名是否为敏感字段

    Args:
        field_name: 字段名称

    Returns:
        True 如果是敏感字段
    """
    for pattern in _SENSITIVE_PATTERNS:
        if pattern.search(field_name):
            return True
    return False


def filter_sensitive_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    """过滤字典中的敏感数据

    Args:
        data: 原始字典

    Returns:
        脱敏后的字典
    """
    result = {}
    for key, value in data.items():
        if is_sensitive_field(key):
            if isinstance(value, (str, int, float)):
                result[key] = mask_sensitive_value(str(value))
            else:
                result[key] = "***REDACTED***"
        elif isinstance(value, dict):
            result[key] = filter_sensitive_dict(value)
        elif isinstance(value, list):
            result[key] = [
                filter_sensitive_dict(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            result[key] = value
    return result


# ============================================================================
# 时间工具
# ============================================================================

def current_timestamp_ms() -> float:
    """获取当前时间戳（毫秒）"""
    return time.time() * 1000


def current_timestamp_s() -> float:
    """获取当前时间戳（秒）"""
    return time.time()


def format_duration_ms(duration_ms: float) -> str:
    """格式化耗时为可读字符串

    Args:
        duration_ms: 耗时（毫秒）

    Returns:
        格式化后的字符串（如 "1.23s", "456ms", "1.2m"）
    """
    if duration_ms < 1000:
        return f"{duration_ms:.0f}ms"
    elif duration_ms < 60000:
        return f"{duration_ms / 1000:.2f}s"
    else:
        return f"{duration_ms / 60000:.2f}m"


# ============================================================================
# 标签键工具
# ============================================================================

def make_label_key(labels: Dict[str, str]) -> str:
    """生成标签键（用于字典索引）

    Args:
        labels: 标签字典

    Returns:
        排序后的标签键字符串
    """
    return ",".join(f"{k}={v}" for k, v in sorted(labels.items()))


def parse_label_key(label_key: str) -> Dict[str, str]:
    """解析标签键

    Args:
        label_key: 标签键字符串

    Returns:
        标签字典
    """
    labels = {}
    if label_key:
        for part in label_key.split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                labels[k] = v
    return labels


# ============================================================================
# 单例模式工具
# ============================================================================

class SingletonMeta(type):
    """单例元类"""

    _instances: Dict[type, Any] = {}
    _lock = None

    def __call__(cls, *args, **kwargs):
        if cls._lock is None:
            import threading
            cls._lock = threading.Lock()

        if cls not in cls._instances:
            with cls._lock:
                if cls not in cls._instances:
                    cls._instances[cls] = super().__call__(*args, **kwargs)
        return cls._instances[cls]
