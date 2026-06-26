#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Digital Life 错误上报配置文件

设计原则（按项目硬约束）：
1. 结构化日志：所有关键节点输出 JSON 格式日志（trace_id / module_name / action / duration_ms）
2. 边界显性化：失败分支抛出带业务错误码的 Error，不静默返回 None
3. 健康检查：通过 init_sentry / is_sentry_enabled 反映依赖状态
4. 幂等性：init_sentry 使用线程锁，可重复调用
5. 后端权威原则：capture_error 直接走 sentry_sdk 权威链路，前端不自行推导状态

错误码：
- SENTRY_ERR_RATE_INVALID   : 采样率配置无效
- SENTRY_ERR_DSN_INVALID    : DSN 格式无效
- SENTRY_ERR_INIT_FAILED    : SDK 初始化失败
- SENTRY_ERR_SDK_MISSING    : sentry_sdk 未安装
- SENTRY_ERR_NOT_INIT       : 未初始化即调用上报
- SENTRY_ERR_CAPTURE_FAILED : 上报过程异常

后端对接：通过 sentry_sdk 协议直连 GlitchTip（自建 Sentry 兼容后端）。
"""

import json
import logging
import os
import re
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# 错误码常量
# ═══════════════════════════════════════════════════════════════

SENTRY_ERR_RATE_INVALID = "SENTRY_ERR_001"     # 采样率格式或范围无效
SENTRY_ERR_DSN_INVALID = "SENTRY_ERR_002"       # DSN 缺失或格式无效
SENTRY_ERR_INIT_FAILED = "SENTRY_ERR_003"        # SDK init 抛出异常
SENTRY_ERR_SDK_MISSING = "SENTRY_ERR_004"        # sentry_sdk 模块未安装
SENTRY_ERR_NOT_INIT = "SENTRY_ERR_005"           # 未初始化即调用上报
SENTRY_ERR_CAPTURE_FAILED = "SENTRY_ERR_006"     # capture_exception/message 异常

# ═══════════════════════════════════════════════════════════════
# 全局单例（延迟初始化）
# ═══════════════════════════════════════════════════════════════

_sentry_initialized: bool = False
_sentry_init_lock: Optional[threading.Lock] = None  # 延迟创建避免循环导入

# 敏感字段名集合（连字符/下划线统一归一化匹配）
_DEFAULT_SENSITIVE_PATTERNS: List[str] = [
    "password", "passwd", "pwd",
    "token", "access_token", "refresh_token", "auth_token",
    "api_key", "apikey", "api-key",
    "secret", "client_secret",
    "authorization",
    "id_card", "idcard", "id_number",
    "bank_card", "bankcard", "card_number", "cvv", "ssn",
    "phone", "mobile",
]

_sensitive_patterns: List[str] = list(_DEFAULT_SENSITIVE_PATTERNS)


class SentryConfigError(Exception):
    """Sentry 配置/初始化异常，携带业务错误码"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


# ═══════════════════════════════════════════════════════════════
# 结构化日志辅助
# ═══════════════════════════════════════════════════════════════

def _emit_log(action: str, log_level: str, trace_id: Optional[str], **fields) -> None:
    """输出 JSON 格式结构化日志（遵循可观测性约束）

    统一字段：trace_id / module_name / action / duration_ms
    附加字段通过 **fields 传入，例如 dsn="https://...key@host/1"（脱敏后输出）

    注意：log_level 是日志级别参数（info/warning/error），调用方如需记录业务
    severity，应使用 severity= 等其他键名，避免与 log_level 冲突。
    """
    payload: Dict[str, Any] = {
        "trace_id": trace_id or _safe_get_trace_id(),
        "module_name": "error_reporting_config",
        "action": action,
    }
    payload.update(fields)
    # 永远先脱敏一遍，避免 DSN / token 漏到日志
    payload = _filter_sensitive_recursive(payload)
    getattr(logger, log_level, logger.info)(json.dumps(payload, ensure_ascii=False, default=str))


def _safe_get_trace_id() -> str:
    """安全获取当前线程上下文的 trace_id，无上下文时返回新的 uuid

    用于日志关联 OpenTelemetry 链路；失败不影响主流程。
    """
    try:
        # 优先从 contextvars 取（OpenTelemetry / loguru 等会注入）
        try:
            from contextvars import ContextVar
            cv: Optional[ContextVar] = ContextVar("_yunshu_trace_id", default="")
            tid = cv.get()
            if tid:
                return tid
        except Exception:
            pass
        return uuid.uuid4().hex
    except Exception:
        return "no-trace"


# ═══════════════════════════════════════════════════════════════
# 配置读取（保留向后兼容的 get_config）
# ═══════════════════════════════════════════════════════════════

def get_config() -> Dict[str, Any]:
    """获取错误上报配置

    保留向后兼容结构（console/file/webhook/slack/email）。
    新增 sentry 子段用于自建 GlitchTip 对接。
    """
    raw_sample_rate = os.environ.get("SENTRY_SAMPLE_RATE", "1.0")
    raw_traces_rate = os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.0")

    return {
        # 控制台上报（始终启用）
        "console": {
            "enabled": True,
            "min_level": os.environ.get("ERROR_REPORTING_CONSOLE_LEVEL", "warning"),
        },
        # 文件上报
        "file": {
            "enabled": os.environ.get("ERROR_REPORTING_FILE_ENABLED", "true").lower() == "true",
            "file_path": os.environ.get("ERROR_REPORTING_FILE_PATH", "./logs/digital_life_errors.log"),
            "min_level": os.environ.get("ERROR_REPORTING_FILE_LEVEL", "error"),
        },
        # Webhook 上报
        "webhook": {
            "enabled": os.environ.get("ERROR_REPORTING_WEBHOOK_ENABLED", "false").lower() == "true",
            "url": os.environ.get("ERROR_REPORTING_WEBHOOK_URL", ""),
            "headers": {"Content-Type": "application/json"},
            "timeout": int(os.environ.get("ERROR_REPORTING_WEBHOOK_TIMEOUT", "5")),
            "min_level": os.environ.get("ERROR_REPORTING_WEBHOOK_LEVEL", "error"),
        },
        # Slack 上报
        "slack": {
            "enabled": os.environ.get("ERROR_REPORTING_SLACK_ENABLED", "false").lower() == "true",
            "webhook_url": os.environ.get("ERROR_REPORTING_SLACK_WEBHOOK_URL", ""),
            "channel": os.environ.get("ERROR_REPORTING_SLACK_CHANNEL", "#digital-life-alerts"),
            "username": os.environ.get("ERROR_REPORTING_SLACK_USERNAME", "Digital Life Bot"),
            "icon_emoji": os.environ.get("ERROR_REPORTING_SLACK_ICON", ":robot_face:"),
            "min_level": os.environ.get("ERROR_REPORTING_SLACK_LEVEL", "warning"),
        },
        # Email 上报（暂未实现）
        "email": {"enabled": False},
        # Sentry/GlitchTip 对接（新增）
        "sentry": {
            "enabled": bool(os.environ.get("SENTRY_DSN", "").strip()),
            "dsn": os.environ.get("SENTRY_DSN", ""),
            "environment": os.environ.get("SENTRY_ENVIRONMENT", "development"),
            "sample_rate": _parse_sample_rate(raw_sample_rate, 1.0, "SENTRY_SAMPLE_RATE"),
            "traces_sample_rate": _parse_sample_rate(raw_traces_rate, 0.0, "SENTRY_TRACES_SAMPLE_RATE"),
            "release": os.environ.get("SENTRY_RELEASE", ""),
            "server_name": os.environ.get("SENTRY_SERVER_NAME", ""),
            "min_level": os.environ.get("SENTRY_MIN_LEVEL", "error"),
        },
    }


# ═══════════════════════════════════════════════════════════════
# 采样率解析（边界显性化）
# ═══════════════════════════════════════════════════════════════

def _parse_sample_rate(raw: Optional[str], default: float, field_name: str) -> float:
    """解析采样率（0.0~1.0），失败抛 ValueError 携带业务错误码

    Args:
        raw: 原始字符串
        default: 默认值（raw 为空时返回）
        field_name: 字段名（用于日志上下文）
    Returns:
        float: 0.0~1.0
    Raises:
        ValueError: 含 SENTRY_ERR_RATE_INVALID 错误码
    """
    action = "parse_sample_rate"
    if raw is None or str(raw).strip() == "":
        _emit_log(action, "debug", None, field=field_name, result="empty_uses_default", default=default)
        return default
    try:
        val = float(raw)
    except (TypeError, ValueError) as e:
        err = SentryConfigError(SENTRY_ERR_RATE_INVALID, f"{field_name}={raw!r} 非数字")
        _emit_log(action, "error", None, field=field_name, raw=raw, error=str(e))
        raise ValueError(str(err)) from e
    if val < 0.0 or val > 1.0:
        err = SentryConfigError(SENTRY_ERR_RATE_INVALID, f"{field_name}={val!r} 超出 [0.0, 1.0]")
        _emit_log(action, "error", None, field=field_name, value=val, error="out_of_range")
        raise ValueError(str(err))
    _emit_log(action, "debug", None, field=field_name, value=val)
    return val


# ═══════════════════════════════════════════════════════════════
# Sentry SDK 初始化
# ═══════════════════════════════════════════════════════════════

# DSN 格式：https://publickey@host/project_id 或 http://...
_DSN_PATTERN = re.compile(r"^https?://[a-zA-Z0-9_-]+@[^\s/]+(?:/\d+)?/?$")


def init_sentry(config: Optional[Dict[str, Any]] = None, force: bool = False) -> bool:
    """初始化 Sentry SDK（线程安全、幂等）

    Args:
        config: 自定义配置（None 时从 get_config() 读取）
        force: True 时强制重新初始化（用于测试或 DSN 切换）
    Returns:
        bool: True=初始化成功，False=未启用或失败
    """
    global _sentry_initialized, _sentry_init_lock
    action = "init_sentry"
    t0 = time.time()

    # 延迟创建锁，避免模块导入时副作用
    if _sentry_init_lock is None:
        _sentry_init_lock = threading.Lock()

    with _sentry_init_lock:
        if _sentry_initialized and not force:
            _emit_log(action, "debug", None, result="already_initialized", duration_ms=0.0)
            return True

        cfg = config or get_config()
        sentry_cfg = cfg.get("sentry", {})
        dsn = sentry_cfg.get("dsn", "").strip()

        if not dsn:
            _emit_log(action, "info", None, result="no_dsn_disabled",
                      duration_ms=_ms(t0), reason="SENTRY_DSN 未配置")
            _sentry_initialized = False
            return False

        if not _DSN_PATTERN.match(dsn):
            err = SentryConfigError(SENTRY_ERR_DSN_INVALID, f"DSN 格式无效: {dsn[:20]}...")
            _emit_log(action, "warning", None, result="invalid_dsn",
                      duration_ms=_ms(t0), error=err.message, code=err.code)
            _sentry_initialized = False
            return False

        # 延迟导入 sentry_sdk，未安装时降级（边界显性化）
        try:
            import sentry_sdk  # noqa: F401
            from sentry_sdk.integrations import (  # type: ignore
                flask as flask_integration,
                logging as logging_integration,
                threading as threading_integration,
            )
        except ImportError as e:
            err = SentryConfigError(SENTRY_ERR_SDK_MISSING, f"sentry_sdk 未安装: {e}")
            _emit_log(action, "warning", None, result="sdk_missing",
                      duration_ms=_ms(t0), error=err.message, code=err.code)
            _sentry_initialized = False
            return False

        try:
            sentry_sdk.init(
                dsn=dsn,
                environment=sentry_cfg.get("environment", "development"),
                sample_rate=sentry_cfg.get("sample_rate", 1.0),
                traces_sample_rate=sentry_cfg.get("traces_sample_rate", 0.0),
                release=sentry_cfg.get("release") or None,
                server_name=sentry_cfg.get("server_name") or None,
                before_send=_sentry_before_send,
                # 启用默认 integrations（Flask/Logging/Threading）
                integrations=[
                    flask_integration.FlaskIntegration(),
                    logging_integration.LoggingIntegration(
                        level=logging.INFO,
                        event_level=logging.ERROR,
                    ),
                    threading_integration.ThreadingIntegration(),
                ],
            )
            _sentry_initialized = True
            _emit_log(action, "info", None, result="initialized",
                      duration_ms=_ms(t0),
                      environment=sentry_cfg.get("environment"),
                      sample_rate=sentry_cfg.get("sample_rate"))
            return True
        except Exception as e:
            err = SentryConfigError(SENTRY_ERR_INIT_FAILED, f"sentry_sdk.init 异常: {e}")
            _emit_log(action, "error", None, result="init_failed",
                      duration_ms=_ms(t0), error=str(e), code=err.code,
                      exception_type=type(e).__name__)
            _sentry_initialized = False
            return False


def is_sentry_enabled() -> bool:
    """查询 Sentry 是否已成功初始化"""
    return _sentry_initialized


def _reset_for_test() -> None:
    """测试辅助：重置全局状态（仅测试用）"""
    global _sentry_initialized, _sentry_init_lock
    _sentry_initialized = False
    _sentry_init_lock = None


# ═══════════════════════════════════════════════════════════════
# 敏感信息过滤（before_send 钩子）
# ═══════════════════════════════════════════════════════════════

def set_sensitive_patterns(patterns: List[str]) -> None:
    """覆盖默认敏感字段模式列表

    Args:
        patterns: 新的敏感字段名列表（连字符/下划线归一化匹配）
    """
    global _sensitive_patterns
    _sensitive_patterns = list(patterns)
    _emit_log("set_sensitive_patterns", "info", None,
              count=len(_sensitive_patterns))


def _is_sensitive_key(key: Any) -> bool:
    """判断字段名是否敏感（连字符/下划线归一化匹配）

    匹配规则：
    1. 去掉连字符 - 和下划线 _（统一为无分隔符形式）
    2. 转小写
    3. 完全匹配敏感模式，或后缀匹配（如 access_token 命中 token）
    """
    if not isinstance(key, str):
        return False
    # 归一化：去掉分隔符 - 和 _，转小写
    normalized = key.replace("-", "").replace("_", "").lower()
    for pat in _sensitive_patterns:
        p = pat.replace("-", "").replace("_", "").lower()
        if not p:
            continue
        if normalized == p or normalized.endswith(p):
            return True
    return False


# 敏感 token 模式（用于字符串内嵌场景，如 "token=abc123"）
_SENSITIVE_TOKEN_PATTERNS = [
    re.compile(r"(?i)(token|api[_-]?key|secret|password)\s*[=:]\s*\S+"),
    re.compile(r"(?i)Bearer\s+[A-Za-z0-9\-._~+/]+=*"),
]


def _filter_sensitive_recursive(obj: Any) -> Any:
    """递归过滤敏感字段

    - dict：键命中敏感模式 → 值替换 [REDACTED]
    - list/tuple：递归每个元素
    - str：内嵌 token=xxx 模式 → 替换为 token=[REDACTED]
    - 其他：原样返回
    """
    if isinstance(obj, dict):
        return {
            k: ("[REDACTED]" if _is_sensitive_key(k) else _filter_sensitive_recursive(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_filter_sensitive_recursive(item) for item in obj]
    if isinstance(obj, tuple):
        return tuple(_filter_sensitive_recursive(item) for item in obj)
    if isinstance(obj, str):
        redacted = obj
        for pat in _SENSITIVE_TOKEN_PATTERNS:
            redacted = pat.sub(
                lambda m: m.group(0).split("=")[0] + "=[REDACTED]"
                if "=" in m.group(0) else m.group(0).split(":")[0] + ": [REDACTED]",
                redacted,
            )
        return redacted
    return obj


def _sentry_before_send(event: Dict[str, Any], hint: Dict[str, Any]) -> Dict[str, Any]:
    """Sentry before_send 钩子：脱敏 + 注入 trace_id

    Args:
        event: Sentry 事件原始字典
        hint: 上下文提示（含 exc_info 等）
    Returns:
        处理后的事件字典（脱敏 + 补 trace_id breadcrumb）
    """
    action = "sentry_before_send"
    t0 = time.time()
    try:
        # 1. 递归脱敏（extra / request / breadcrumbs / tags 全部覆盖）
        event = _filter_sensitive_recursive(event) if isinstance(event, dict) else event

        # 2. 注入 trace_id 到 tags
        trace_id = _safe_get_trace_id()
        if isinstance(event, dict):
            tags = event.setdefault("tags", {})
            if isinstance(tags, dict):
                tags.setdefault("trace_id", trace_id)

            # 3. 注入 breadcrumb 便于链路追溯
            breadcrumbs = event.setdefault("breadcrumbs", {})
            if isinstance(breadcrumbs, dict):
                values = breadcrumbs.setdefault("values", [])
                values.append({
                    "type": "debug",
                    "category": "yunshu.before_send",
                    "message": f"trace_id={trace_id}",
                    "timestamp": time.time(),
                    "data": {"trace_id": trace_id},
                })

        _emit_log(action, "debug", trace_id, result="filtered",
                  duration_ms=_ms(t0))
        return event
    except Exception as e:
        _emit_log(action, "error", None, result="filter_failed",
                  duration_ms=_ms(t0), error=str(e),
                  exception_type=type(e).__name__)
        # 过滤失败不阻塞上报，返回原事件
        return event


# ═══════════════════════════════════════════════════════════════
# 错误上报入口
# ═══════════════════════════════════════════════════════════════

def capture_error(
    error: BaseException,
    level: str = "error",
    context: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Optional[str]:
    """上报异常到 Sentry/GlitchTip

    Args:
        error: 异常对象
        level: 严重级别（fatal/error/warning/info/debug）
        context: 附加上下文（会被脱敏）
        trace_id: 链路追踪 ID（None 时自动生成）
        user_id: 用户 ID（用于关联用户操作回放）
    Returns:
        str: Sentry event_id；未启用或失败时返回 None
    """
    action = "capture_error"
    t0 = time.time()
    tid = trace_id or _safe_get_trace_id()

    if not _sentry_initialized:
        _emit_log(action, "warning", tid, result="skipped_not_initialized",
                  duration_ms=_ms(t0), error_type=type(error).__name__)
        return None

    try:
        import sentry_sdk
        with sentry_sdk.push_scope() as scope:
            scope.set_level(level)
            scope.set_tag("trace_id", tid)
            if user_id:
                scope.set_user({"id": user_id})
            if context:
                # 脱敏后再 set_context，避免敏感信息进 Sentry
                scope.set_context("custom", _filter_sensitive_recursive(context))

            event_id = sentry_sdk.capture_exception(error)
            _emit_log(action, "info", tid, result="captured",
                      duration_ms=_ms(t0),
                      event_id=event_id,
                      error_type=type(error).__name__,
                      error_msg=str(error)[:200],
                      level=level)
            return event_id
    except Exception as e:
        err = SentryConfigError(SENTRY_ERR_CAPTURE_FAILED, f"capture_error 异常: {e}")
        _emit_log(action, "error", tid, result="capture_failed",
                  duration_ms=_ms(t0), error=str(e), code=err.code,
                  exception_type=type(e).__name__)
        return None


def capture_message(
    message: str,
    level: str = "info",
    context: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
) -> Optional[str]:
    """上报纯文本消息到 Sentry/GlitchTip

    Args:
        message: 消息内容
        level: 严重级别
        context: 附加上下文（会被脱敏）
        trace_id: 链路追踪 ID
    Returns:
        str: Sentry event_id；未启用或失败时返回 None
    """
    action = "capture_message"
    t0 = time.time()
    tid = trace_id or _safe_get_trace_id()

    if not _sentry_initialized:
        _emit_log(action, "warning", tid, result="skipped_not_initialized",
                  duration_ms=_ms(t0))
        return None

    try:
        import sentry_sdk
        with sentry_sdk.push_scope() as scope:
            scope.set_level(level)
            scope.set_tag("trace_id", tid)
            if context:
                scope.set_context("custom", _filter_sensitive_recursive(context))

            event_id = sentry_sdk.capture_message(message, level=level)
            _emit_log(action, "info", tid, result="captured",
                      duration_ms=_ms(t0),
                      event_id=event_id,
                      msg_preview=message[:100],
                      level=level)
            return event_id
    except Exception as e:
        err = SentryConfigError(SENTRY_ERR_CAPTURE_FAILED, f"capture_message 异常: {e}")
        _emit_log(action, "error", tid, result="capture_failed",
                  duration_ms=_ms(t0), error=str(e), code=err.code,
                  exception_type=type(e).__name__)
        return None


# ═══════════════════════════════════════════════════════════════
# 健康检查（用于 /health 或 /status 接口）
# ═══════════════════════════════════════════════════════════════

def health_check() -> Dict[str, Any]:
    """返回 Sentry/GlitchTip 依赖健康状态（供 /health 接口使用）

    Returns:
        {
            "sentry_sdk_installed": bool,
            "sentry_initialized": bool,
            "dsn_configured": bool,
            "environment": str,
        }
    """
    try:
        import sentry_sdk  # noqa: F401
        sdk_installed = True
    except ImportError:
        sdk_installed = False

    cfg = get_config().get("sentry", {})
    return {
        "sentry_sdk_installed": sdk_installed,
        "sentry_initialized": _sentry_initialized,
        "dsn_configured": bool(cfg.get("dsn", "").strip()),
        "environment": cfg.get("environment", "development"),
    }


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════

def _ms(t0: float) -> float:
    """计算耗时（毫秒，保留 2 位）"""
    return round((time.time() - t0) * 1000, 2)


__all__ = [
    # 错误码
    "SENTRY_ERR_RATE_INVALID",
    "SENTRY_ERR_DSN_INVALID",
    "SENTRY_ERR_INIT_FAILED",
    "SENTRY_ERR_SDK_MISSING",
    "SENTRY_ERR_NOT_INIT",
    "SENTRY_ERR_CAPTURE_FAILED",
    # 异常类
    "SentryConfigError",
    # 配置/初始化
    "get_config",
    "init_sentry",
    "is_sentry_enabled",
    "_reset_for_test",
    # 采样率
    "_parse_sample_rate",
    # 敏感信息过滤
    "set_sensitive_patterns",
    "_is_sensitive_key",
    "_filter_sensitive_recursive",
    "_sentry_before_send",
    # 上报入口
    "capture_error",
    "capture_message",
    # 健康检查
    "health_check",
    # 内部辅助
    "_safe_get_trace_id",
    "_emit_log",
]
