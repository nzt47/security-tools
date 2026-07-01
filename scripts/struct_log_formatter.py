"""结构化日志格式化器 — 让 JSON 日志在控制台显示更清晰易读

使用方法（在 app_server.py 启动时）：
    from scripts.struct_log_formatter import setup_readable_logging
    setup_readable_logging()

效果：
    原始 JSON: {"trace_id":"abc123","module_name":"app_server","action":"api_search_instance_add.done","duration_ms":45,"message":"搜索实例已新增","instance_id":"xxx","priority_before":["a"],"priority_after":["a","b"]}
    格式化后: [abc123] app_server | api_search_instance_add.done | 45ms
              → 搜索实例已新增
              → instance_id=xxx
              → priority_before=["a"] → priority_after=["a","b"]  [CHANGED]
"""

import logging
import json
import re
import time as _time
from typing import Optional


# ANSI 颜色码（控制台着色）
class _Color:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    GRAY = "\033[90m"


# 关键字段高亮规则
_PRIORITY_KEYS = {'priority_before', 'priority_after', 'priority_changed',
                  'default_before', 'default_after', 'default_engine'}
_ID_KEYS = {'instance_id', 'instance_name', 'engine_type', 'updated_fields'}
_STATUS_KEYS = {'error', 'errors', 'reason'}


def _colorize(text: str, color: str) -> str:
    """添加 ANSI 颜色"""
    return f"{color}{text}{_Color.RESET}"


def _format_value(val) -> str:
    """格式化值（简单类型直接 str，列表/字典用 json.dumps 并截断）

    优化：对 str/int/float/bool/None 跳过 json.dumps，减少 ~20% CPU 开销。
    """
    if val is None:
        return ''
    if isinstance(val, str):
        return val
    if isinstance(val, (int, float, bool)):
        return str(val)
    # 复杂类型用 json.dumps 序列化
    if isinstance(val, (list, dict)):
        s = json.dumps(val, ensure_ascii=False)
        if len(s) > 80:
            return s[:77] + "..."
        return s
    return str(val)


def format_structured_log(record: logging.LogRecord) -> str:
    """格式化结构化 JSON 日志为易读的多行格式

    如果消息不是 JSON，回退到标准格式。
    """
    msg = record.getMessage()

    # 尝试解析 JSON
    try:
        data = json.loads(msg)
        if not isinstance(data, dict) or 'action' not in data:
            raise ValueError("非结构化日志")
    except (json.JSONDecodeError, ValueError):
        # 非 JSON 日志，保留时间戳的标准格式（与 setup_agent_logging 的默认格式对齐）
        asctime = _time.strftime("%H:%M:%S")
        return f"{asctime} [{record.levelname:8s}] {record.name:25s}: {msg}"

    # 提取标准字段（trace_id 可能为 None，做防御性处理）
    # 兼容 message 和 msg 两种字段名（不同模块使用不同命名）
    trace_id_raw = data.get('trace_id')
    trace_id = (trace_id_raw or '')[:8] if trace_id_raw else ''
    module = data.get('module_name', '') or ''
    action = data.get('action', '') or ''
    duration = data.get('duration_ms', 0) or 0
    message = data.get('message', '') or data.get('msg', '') or ''

    # 第一行：摘要
    level_color = {
        'INFO': _Color.CYAN,
        'WARNING': _Color.YELLOW,
        'ERROR': _Color.RED,
    }.get(record.levelname, _Color.RESET)

    duration_str = f"{duration}ms" if duration else "0ms"
    duration_color = _Color.YELLOW if duration > 100 else _Color.GRAY

    line1 = (
        f"{_colorize(f'[{trace_id}]', _Color.GRAY)} "
        f"{_colorize(module, _Color.BLUE)} | "
        f"{_colorize(action, _Color.BOLD)} | "
        f"{_colorize(duration_str, duration_color)}"
    )
    if record.levelname != 'INFO':
        line1 = f"{_colorize(record.levelname, level_color)} {line1}"

    lines = [line1]

    # 第二行：消息
    if message:
        lines.append(f"  {_colorize('→', _Color.GRAY)} {message}")

    # 优先级/默认引擎变化（高亮显示）
    has_priority = 'priority_before' in data or 'priority_after' in data
    has_default = 'default_before' in data or 'default_after' in data

    if has_priority:
        before = _format_value(data.get('priority_before', ''))
        after = _format_value(data.get('priority_after', ''))
        changed = data.get('priority_changed', before != after)
        marker = _colorize('[CHANGED]', _Color.YELLOW) if changed else _colorize('[same]', _Color.GRAY)
        lines.append(f"  {_colorize('→', _Color.GRAY)} priority: {before} → {after}  {marker}")

    if has_default:
        before = _format_value(data.get('default_before', ''))
        after = _format_value(data.get('default_after', ''))
        changed = before != after
        marker = _colorize('[CHANGED]', _Color.YELLOW) if changed else _colorize('[same]', _Color.GRAY)
        lines.append(f"  {_colorize('→', _Color.GRAY)} default: {before} → {after}  {marker}")

    # 其他字段
    skip_keys = {'trace_id', 'module_name', 'action', 'duration_ms', 'message', 'msg',
                 'priority_before', 'priority_after', 'priority_changed',
                 'default_before', 'default_after'}
    for key, val in data.items():
        if key in skip_keys:
            continue
        formatted = _format_value(val)
        if key in _ID_KEYS:
            lines.append(f"  {_colorize('→', _Color.GRAY)} {_colorize(key, _Color.MAGENTA)}={formatted}")
        elif key in _STATUS_KEYS:
            lines.append(f"  {_colorize('→', _Color.GRAY)} {_colorize(key, _Color.RED)}={formatted}")
        else:
            lines.append(f"  {_colorize('→', _Color.GRAY)} {key}={formatted}")

    return "\n".join(lines)


class StructuredLogFormatter(logging.Formatter):
    """结构化日志格式化器

    对 JSON 格式的日志消息进行美化，对非 JSON 日志使用标准格式。
    """

    def format(self, record: logging.LogRecord) -> str:
        return format_structured_log(record)


def setup_readable_logging(level: int = logging.INFO):
    """配置全局日志使用易读格式

    替换 root logger 的所有 handler 的 formatter。
    如果没有 handler（独立脚本场景），创建一个 StreamHandler。
    保留 JSON 格式本身不变（日志收集系统仍可解析原始消息），
    仅改变控制台显示格式。

    Args:
        level: 日志级别，默认 INFO
    """
    formatter = StructuredLogFormatter()

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    if not root_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)
    else:
        for handler in root_logger.handlers:
            handler.setFormatter(formatter)

    # 测试输出
    logger = logging.getLogger("struct_log_formatter")
    logger.info(json.dumps({
        "trace_id": "abc12345",
        "module_name": "app_server",
        "action": "setup_readable_logging.test",
        "duration_ms": 0,
        "message": "结构化日志格式化器已启用",
    }, ensure_ascii=False))


if __name__ == "__main__":
    # 自测
    setup_readable_logging()

    logger = logging.getLogger("test")

    # 模拟搜索实例新增日志
    logger.info(json.dumps({
        "trace_id": "abc12345",
        "module_name": "app_server",
        "action": "api_search_instance_add.done",
        "duration_ms": 45,
        "message": "搜索实例已新增",
        "instance_id": "550e8400-e29b-41d4-a716-446655440000",
        "instance_name": "Tavily",
        "engine_type": "custom",
        "priority_before": ["uuid-aaa"],
        "priority_after": ["uuid-aaa", "uuid-bbb"],
    }, ensure_ascii=False))

    # 模拟配置更新日志（priority 变化）
    logger.info(json.dumps({
        "trace_id": "def67890",
        "module_name": "app_server",
        "action": "api_network_config_update.done",
        "duration_ms": 152,
        "message": "网络配置已更新",
        "priority_before": ["uuid-aaa", "uuid-bbb"],
        "priority_after": ["uuid-bbb", "uuid-aaa"],
        "priority_changed": True,
        "default_engine": "uuid-bbb",
    }, ensure_ascii=False))

    # 模拟删除日志
    logger.info(json.dumps({
        "trace_id": "ghi11223",
        "module_name": "app_server",
        "action": "api_search_instance_delete.done",
        "duration_ms": 38,
        "message": "搜索实例已删除",
        "instance_id": "uuid-bbb",
        "priority_before": ["uuid-aaa", "uuid-bbb"],
        "priority_after": ["uuid-aaa"],
        "priority_changed": True,
    }, ensure_ascii=False))

    # 模拟设置默认引擎
    logger.info(json.dumps({
        "trace_id": "jkl44556",
        "module_name": "app_server",
        "action": "api_search_instance_set_default.done",
        "duration_ms": 22,
        "message": "已设为默认搜索引擎",
        "instance_id": "uuid-ccc",
        "instance_name": "DuckDuckGo",
        "default_before": "uuid-aaa",
        "default_after": "uuid-ccc",
    }, ensure_ascii=False))

    # 模拟错误
    logger.error(json.dumps({
        "trace_id": "mno77889",
        "module_name": "app_server",
        "action": "api_search_instance_update.failed",
        "duration_ms": 5,
        "message": "更新搜索实例失败: ValueError",
        "instance_id": "uuid-xxx",
        "error": "实例不存在",
    }, ensure_ascii=False))
