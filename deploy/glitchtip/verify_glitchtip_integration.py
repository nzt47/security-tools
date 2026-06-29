#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================================
# 生成日志摘要
# ----------------------------------------------------------------------------
# 生成时间戳: 2026-06-28
# 内容描述: GlitchTip 生产部署集成验证脚本 v1.0.0
# 生成参数: --dsn <GlitchTip DSN> --glitchtip-url <URL> --timeout <秒>
# 模型配置: GLM-5.2
# 关键状态变化:
#   - 初始版本，对接 agent.error_reporting_config 模块
#   - 支持 DSN 初始化 → 异常触发 → 事件捕获 → REST API 验证全链路
#   - 结构化日志输出到 stderr，验证报告输出到 stdout
# 文件权限: chmod +x verify_glitchtip_integration.py
# ============================================================================
"""
GlitchTip 生产部署集成验证脚本

设计目的：
  验证 deploy/glitchtip/ 部署的 GlitchTip 实例能正确接收并处理
  agent.error_reporting_config 模块上报的错误事件。

验证流程：
  1. 调用 init_sentry(dsn) 初始化 Sentry SDK
  2. 触发带 trace_id 上下文的测试异常
  3. 调用 capture_error 捕获并上报
  4. 轮询 GlitchTip REST API 确认事件已入库
  5. 输出 JSON 验证报告到 stdout

使用方式：
  # 基础用法（仅验证事件捕获，不查询 API）
  python verify_glitchtip_integration.py --dsn https://abc@localhost:8001/1

  # 完整验证（含 API 事件确认，需要 API Token）
  python verify_glitchtip_integration.py \
      --dsn https://abc@localhost:8001/1 \
      --glitchtip-url http://localhost:8001 \
      --api-token <your-glitchtip-api-token> \
      --timeout 30

  # API Token 获取：GlitchTip Web → 个人设置 → API Keys → 创建（勾选 project:read）

可观测性约束（按项目硬约束）：
  - 结构化日志：所有关键节点输出 JSON 日志（trace_id/module_name/action/duration_ms）
  - 边界显性化：失败分支抛 GlitchTipVerificationError(code, message)
  - 健康检查：末尾输出依赖项状态（可达性/DSN 有效性/事件接收确认）
  - 幂等性：每次运行生成独立 trace_id，可重复执行
"""

import argparse
import json
import os
import sys
import time
import traceback
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

# ============================================================================
# 模块导入：复用项目现有的错误上报模块
# ============================================================================
# 状态同步机制说明：
# - 使用 agent.error_reporting_config 的 init_sentry/capture_error/is_sentry_enabled
# - 通过 force=True 强制重新初始化，避免全局单例残留导致 DSN 切换失效
# - capture_error 返回权威 event_id，作为后续 API 验证的查询依据
# ============================================================================

# 延迟导入 agent 模块，在 main 中处理 ImportError（边界显性化）
_AGENT_MODULE_AVAILABLE: bool = False
init_sentry = None  # type: ignore
capture_error = None  # type: ignore
is_sentry_enabled = None  # type: ignore

try:
    # 尝试从 agent 包导入（需在项目根目录运行或 PYTHONPATH 包含项目根）
    from agent.error_reporting_config import (  # type: ignore
        init_sentry,
        capture_error,
        is_sentry_enabled,
    )
    _AGENT_MODULE_AVAILABLE = True
except ImportError:
    # 延迟到 main() 中抛出明确错误，避免导入期崩溃
    pass


# ============================================================================
# 错误码常量（边界显性化：所有失败分支携带业务错误码）
# ============================================================================

GT_ERR_AGENT_MODULE_MISSING = "GT_ERR_001"   # agent.error_reporting_config 模块缺失
GT_ERR_DSN_MISSING = "GT_ERR_002"             # DSN 参数未提供
GT_ERR_DSN_INVALID = "GT_ERR_003"             # DSN 格式无效
GT_ERR_INIT_FAILED = "GT_ERR_004"             # Sentry SDK 初始化失败
GT_ERR_CAPTURE_FAILED = "GT_ERR_005"          # 事件捕获失败（capture_error 返回 None）
GT_ERR_NETWORK_UNREACHABLE = "GT_ERR_006"     # GlitchTip 网络不可达
GT_ERR_API_TOKEN_MISSING = "GT_ERR_007"       # API Token 缺失（查询需要）
GT_ERR_API_REQUEST_FAILED = "GT_ERR_008"      # GlitchTip REST API 请求失败
GT_ERR_EVENT_NOT_RECEIVED = "GT_ERR_009"      # 事件在超时时间内未被 GlitchTip 接收
GT_ERR_TIMEOUT = "GT_ERR_010"                 # 等待超时
GT_ERR_INTERNAL = "GT_ERR_500"                # 未预期的内部错误


class GlitchTipVerificationError(Exception):
    """GlitchTip 验证异常，携带业务错误码

    边界显性化：所有可能失败的分支必须抛出此异常，而非静默返回 None。
    """

    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(f"[{code}] {message}")


# ============================================================================
# 结构化日志辅助（可观测性约束：JSON 格式，含 trace_id/module_name/action/duration_ms）
# ============================================================================

_MODULE_NAME = "glitchtip_verify"


def _emit_log(action: str, trace_id: str, log_level: str = "info",
              duration_ms: Optional[float] = None, **fields: Any) -> None:
    """输出 JSON 格式结构化日志到 stderr

    统一字段：trace_id / module_name / action / duration_ms
    附加字段通过 **fields 传入，自动序列化。

    Args:
        action: 动作名称（如 init_sentry/capture_error/api_query）
        trace_id: 链路追踪 ID
        log_level: 日志级别（info/warning/error/debug）
        duration_ms: 耗时（毫秒）
        **fields: 附加字段
    """
    payload: Dict[str, Any] = {
        "trace_id": trace_id,
        "module_name": _MODULE_NAME,
        "action": action,
    }
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms
    payload.update(fields)
    # 输出到 stderr，避免污染 stdout 的 JSON 报告
    print(json.dumps(payload, ensure_ascii=False, default=str), file=sys.stderr, flush=True)


def _ms(t0: float) -> float:
    """计算耗时（毫秒，保留 2 位小数）"""
    return round((time.time() - t0) * 1000, 2)


# ============================================================================
# HTTP 请求辅助（使用标准库 urllib，避免引入第三方依赖）
# ============================================================================

def _http_get(url: str, headers: Optional[Dict[str, str]] = None,
              timeout: int = 10) -> Tuple[int, bytes, Dict[str, str]]:
    """执行 HTTP GET 请求（边界显性化：网络失败抛异常）

    Args:
        url: 请求 URL
        headers: 请求头
        timeout: 超时秒数
    Returns:
        (status_code, body_bytes, response_headers)
    Raises:
        GlitchTipVerificationError: 网络不可达或请求异常时抛出
    """
    try:
        req = urllib_request.Request(url, method="GET")
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)
        with urllib_request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib_error.HTTPError as e:
        # HTTP 错误码（4xx/5xx），返回响应体供调用方分析
        body = b""
        try:
            body = e.read()
        except Exception:
            pass
        return e.code, body, dict(e.headers) if e.headers else {}
    except (urllib_error.URLError, OSError) as e:
        # 网络不可达（连接超时/DNS 解析失败/拒绝连接）
        raise GlitchTipVerificationError(
            GT_ERR_NETWORK_UNREACHABLE,
            f"网络请求失败: {url} - {type(e).__name__}: {e}",
            details={"url": url, "error_type": type(e).__name__}
        ) from e


def _check_glitchtip_reachable(glitchtip_url: str, trace_id: str, timeout: int = 10) -> bool:
    """检测 GlitchTip 心跳端点是否可达

    Args:
        glitchtip_url: GlitchTip 基地址
        trace_id: 链路追踪 ID
        timeout: 超时秒数
    Returns:
        bool: True=可达
    """
    action = "check_reachable"
    t0 = time.time()
    heartbeat_url = glitchtip_url.rstrip("/") + "/api/0/heartbeat/"
    try:
        status, body, _ = _http_get(heartbeat_url, timeout=timeout)
        reachable = (status == 200)
        _emit_log(action, trace_id, "info" if reachable else "warning",
                  duration_ms=_ms(t0),
                  heartbeat_url=heartbeat_url,
                  status_code=status,
                  reachable=reachable)
        return reachable
    except GlitchTipVerificationError as e:
        _emit_log(action, trace_id, "error",
                  duration_ms=_ms(t0),
                  heartbeat_url=heartbeat_url,
                  error=e.message, code=e.code)
        return False


# ============================================================================
# DSN 校验
# ============================================================================

def _validate_dsn(dsn: str, trace_id: str) -> None:
    """校验 DSN 格式（边界显性化：无效则抛异常）

    DSN 格式：http(s)://publickey@host/project_id
    """
    action = "validate_dsn"
    t0 = time.time()
    try:
        parsed = urllib_parse.urlparse(dsn)
    except Exception as e:
        raise GlitchTipVerificationError(
            GT_ERR_DSN_INVALID,
            f"DSN 解析失败: {e}",
            details={"dsn_preview": dsn[:20] + "..."}
        ) from e

    if not parsed.scheme or parsed.scheme not in ("http", "https"):
        raise GlitchTipVerificationError(
            GT_ERR_DSN_INVALID,
            f"DSN 协议无效（需 http/https）: scheme={parsed.scheme}",
            details={"dsn_preview": dsn[:20] + "..."}
        )
    if not parsed.username:
        raise GlitchTipVerificationError(
            GT_ERR_DSN_INVALID,
            "DSN 缺少 public key（username 部分）",
            details={"dsn_preview": dsn[:20] + "..."}
        )
    if not parsed.hostname:
        raise GlitchTipVerificationError(
            GT_ERR_DSN_INVALID,
            "DSN 缺少主机名",
            details={"dsn_preview": dsn[:20] + "..."}
        )
    # project_id 在 path 中（如 /1）
    if not parsed.path or parsed.path == "/":
        raise GlitchTipVerificationError(
            GT_ERR_DSN_INVALID,
            "DSN 缺少 project_id（path 部分）",
            details={"dsn_preview": dsn[:20] + "..."}
        )

    _emit_log(action, trace_id, "info", duration_ms=_ms(t0),
              dsn_host=parsed.hostname, dsn_scheme=parsed.scheme,
              dsn_project=parsed.path.strip("/"))


# ============================================================================
# 核心验证流程
# ============================================================================

def _init_sentry_sdk(dsn: str, trace_id: str) -> None:
    """调用 agent.error_reporting_config.init_sentry 初始化 SDK

    状态同步机制：使用 force=True 强制重新初始化，
    避免全局单例残留导致 DSN 切换失效。
    """
    action = "init_sentry"
    t0 = time.time()

    # 构造 init_sentry 期望的 config 结构（与 get_config() 返回结构对齐）
    config: Dict[str, Any] = {
        "sentry": {
            "dsn": dsn,
            "environment": "verification",
            "sample_rate": 1.0,           # 验证场景全量采样
            "traces_sample_rate": 0.0,    # 不采集性能数据
            "release": "glitchtip-verify-1.0.0",
            "server_name": "verify-script",
        }
    }

    try:
        result = init_sentry(config=config, force=True)
        if not result:
            # init_sentry 返回 False 表示初始化未成功（如 DSN 无效或 SDK 缺失）
            if not is_sentry_enabled():
                raise GlitchTipVerificationError(
                    GT_ERR_INIT_FAILED,
                    "init_sentry 返回 False 且 is_sentry_enabled 为 False",
                    details={"reason": "init_sentry 未成功启用 SDK"}
                )
        _emit_log(action, trace_id, "info", duration_ms=_ms(t0),
                  result="initialized", enabled=is_sentry_enabled())
    except GlitchTipVerificationError:
        raise
    except Exception as e:
        raise GlitchTipVerificationError(
            GT_ERR_INIT_FAILED,
            f"init_sentry 异常: {type(e).__name__}: {e}",
            details={"exception_type": type(e).__name__}
        ) from e


def _trigger_and_capture_error(trace_id: str) -> str:
    """触发测试异常并通过 capture_error 上报

    状态同步机制：
    - 异常消息含唯一 trace_id，便于后续 API 查询定位
    - capture_error 返回权威 event_id，作为 API 验证依据
    - 后端权威原则：不自行推导状态，以 capture_error 返回值为准

    Args:
        trace_id: 链路追踪 ID
    Returns:
        str: Sentry event_id
    Raises:
        GlitchTipVerificationError: 捕获失败时抛出
    """
    action = "capture_error"
    t0 = time.time()

    # 构造带 trace_id 的测试异常（消息唯一，便于 API 查询）
    trace_id_short = trace_id[:16]
    test_error = RuntimeError(
        f"GlitchTip integration test - trace_id={trace_id_short}"
    )

    # 附加上下文（会被 error_reporting_config 自动脱敏）
    context: Dict[str, Any] = {
        "test_scenario": "glitchtip_production_verification",
        "triggered_by": "verify_glitchtip_integration.py",
        "trace_id_full": trace_id,
        "timestamp": time.time(),
    }

    try:
        event_id = capture_error(
            error=test_error,
            level="error",
            context=context,
            trace_id=trace_id,
            user_id="verify-script",
        )
    except Exception as e:
        _emit_log(action, trace_id, "error", duration_ms=_ms(t0),
                  result="capture_exception_failed",
                  error=str(e), exception_type=type(e).__name__)
        raise GlitchTipVerificationError(
            GT_ERR_CAPTURE_FAILED,
            f"capture_error 抛出异常: {type(e).__name__}: {e}",
            details={"exception_type": type(e).__name__}
        ) from e

    if not event_id:
        # capture_error 返回 None 表示上报未成功（SDK 未初始化或上报过程异常）
        raise GlitchTipVerificationError(
            GT_ERR_CAPTURE_FAILED,
            "capture_error 返回 None（SDK 未初始化或上报失败）",
            details={"sentry_enabled": is_sentry_enabled()}
        )

    _emit_log(action, trace_id, "info", duration_ms=_ms(t0),
              result="captured", event_id=event_id,
              error_type=type(test_error).__name__,
              message_preview=str(test_error)[:100])
    return event_id


def _query_glitchtip_events(
    glitchtip_url: str,
    api_token: str,
    trace_id_short: str,
    trace_id: str,
    timeout: int,
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """轮询 GlitchTip REST API 确认事件已接收

    状态同步机制：
    - 使用 Request ID（trace_id）作为查询标识，避免旧请求结果污染
    - 轮询间隔 2 秒，最多 timeout 秒
    - 查询条件：is:unresolved 且消息含 trace_id_short

    Args:
        glitchtip_url: GlitchTip 基地址
        api_token: API Token（Authorization: Bearer）
        trace_id_short: 短 trace_id（用于消息匹配）
        trace_id: 完整 trace_id（用于日志）
        timeout: 总超时秒数
    Returns:
        (received: bool, issue: Optional[dict])
    """
    action = "api_query_events"
    t0 = time.time()

    # GlitchTip issues 查询：query 参数支持全文搜索 + level 过滤
    # 搜索 trace_id_short 以定位本次测试事件
    query_str = f"is:unresolved level:error trace_id={trace_id_short}"
    encoded_query = urllib_parse.quote(query_str)
    api_url = f"{glitchtip_url.rstrip('/')}/api/0/issues/?query={encoded_query}"

    headers = {
        "Authorization": f"Bearer {api_token}",
        "Accept": "application/json",
    }

    deadline = time.time() + timeout
    poll_interval = 2  # 秒
    attempt = 0

    while time.time() < deadline:
        attempt += 1
        poll_t0 = time.time()
        try:
            status, body, _ = _http_get(api_url, headers=headers, timeout=10)
        except GlitchTipVerificationError as e:
            _emit_log(action, trace_id, "error", duration_ms=_ms(poll_t0),
                      attempt=attempt, error=e.message, code=e.code)
            # 网络错误时继续重试（可能在恢复中）
            time.sleep(poll_interval)
            continue

        if status == 401:
            raise GlitchTipVerificationError(
                GT_ERR_API_TOKEN_MISSING,
                "API Token 无效或已过期（HTTP 401）",
                details={"status_code": status}
            )
        if status == 403:
            raise GlitchTipVerificationError(
                GT_ERR_API_TOKEN_MISSING,
                "API Token 权限不足（HTTP 403，需 project:read 权限）",
                details={"status_code": status}
            )
        if status != 200:
            _emit_log(action, trace_id, "warning", duration_ms=_ms(poll_t0),
                      attempt=attempt, status_code=status,
                      body_preview=body[:200].decode("utf-8", errors="replace"))
            time.sleep(poll_interval)
            continue

        # 解析响应
        try:
            issues: List[Dict[str, Any]] = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            _emit_log(action, trace_id, "warning", duration_ms=_ms(poll_t0),
                      attempt=attempt, error=f"响应解析失败: {e}")
            time.sleep(poll_interval)
            continue

        # 遍历 issues 查找匹配 trace_id 的事件
        for issue in issues:
            issue_metadata = issue.get("metadata", {})
            issue_title = str(issue_metadata.get("value", "")) + str(issue_metadata.get("title", ""))
            # 检查 issue 标题/消息是否包含本次 trace_id
            if trace_id_short in issue_title or trace_id_short in json.dumps(issue, ensure_ascii=False):
                _emit_log(action, trace_id, "info", duration_ms=_ms(t0),
                          attempt=attempt, result="event_found",
                          issue_id=issue.get("id"),
                          status_code=status)
                return True, issue

        _emit_log(action, trace_id, "debug", duration_ms=_ms(poll_t0),
                  attempt=attempt, result="not_found_yet",
                  issues_count=len(issues))
        time.sleep(poll_interval)

    # 超时未找到
    _emit_log(action, trace_id, "warning", duration_ms=_ms(t0),
              attempt=attempt, result="timeout",
              timeout=timeout, query=query_str)
    return False, None


# ============================================================================
# 健康检查
# ============================================================================

def _build_health_check(
    glitchtip_reachable: bool,
    dsn_valid: bool,
    sentry_initialized: bool,
    event_captured: bool,
    event_received: bool,
    api_verification_skipped: bool,
) -> Dict[str, Any]:
    """构建依赖项健康状态报告

    健康检查约束：返回所有依赖项的连接/状态信息。
    """
    return {
        "glitchtip_reachable": glitchtip_reachable,
        "dsn_valid": dsn_valid,
        "sentry_initialized": sentry_initialized,
        "event_captured": event_captured,
        "event_received": event_received,
        "api_verification_skipped": api_verification_skipped,
        # 整体健康：可达 + DSN 有效 + SDK 初始化 + 事件捕获成功
        # 若 API 验证未跳过，则要求事件已接收
        "overall_healthy": (
            glitchtip_reachable
            and dsn_valid
            and sentry_initialized
            and event_captured
            and (api_verification_skipped or event_received)
        ),
    }


# ============================================================================
# 主验证流程
# ============================================================================

def run_verification(
    dsn: str,
    glitchtip_url: str,
    timeout: int,
    api_token: Optional[str] = None,
) -> Dict[str, Any]:
    """执行完整验证流程

    Args:
        dsn: GlitchTip DSN（必填）
        glitchtip_url: GlitchTip 基地址
        timeout: 等待事件入库的超时秒数
        api_token: GlitchTip API Token（可选，用于 REST API 验证）
    Returns:
        Dict: 验证报告（JSON 可序列化）
    """
    # 生成唯一 trace_id（贯穿整个验证链路）
    trace_id = uuid.uuid4().hex
    trace_id_short = trace_id[:16]

    _emit_log("verification_start", trace_id, "info",
              dsn_preview=dsn[:30] + "...",
              glitchtip_url=glitchtip_url,
              timeout=timeout,
              api_token_provided=bool(api_token))

    # 健康状态跟踪
    glitchtip_reachable = False
    dsn_valid = False
    sentry_initialized = False
    event_captured = False
    event_received = False
    api_verification_skipped = False
    event_id: Optional[str] = None
    issue_data: Optional[Dict[str, Any]] = None
    error_info: Optional[Dict[str, Any]] = None

    try:
        # ─── 步骤 0：检查 agent 模块可用性 ────────────────
        if not _AGENT_MODULE_AVAILABLE:
            raise GlitchTipVerificationError(
                GT_ERR_AGENT_MODULE_MISSING,
                "无法导入 agent.error_reporting_config 模块，"
                "请在项目根目录运行或设置 PYTHONPATH 包含项目根目录",
                details={"required_module": "agent.error_reporting_config"}
            )

        # ─── 步骤 1：检查 GlitchTip 可达性 ────────────────
        glitchtip_reachable = _check_glitchtip_reachable(glitchtip_url, trace_id)
        if not glitchtip_reachable:
            # 可达性失败不阻断后续（SDK 仍可能通过 DSN 直连上报）
            _emit_log("reachable_check", trace_id, "warning",
                      result="unreachable_but_continue",
                      note="GlitchTip 心跳不可达，SDK 上报仍会尝试")

        # ─── 步骤 2：校验 DSN 格式 ────────────────────────
        _validate_dsn(dsn, trace_id)
        dsn_valid = True

        # ─── 步骤 3：初始化 Sentry SDK ────────────────────
        _init_sentry_sdk(dsn, trace_id)
        sentry_initialized = is_sentry_enabled()
        if not sentry_initialized:
            raise GlitchTipVerificationError(
                GT_ERR_INIT_FAILED,
                "Sentry SDK 初始化后 is_sentry_enabled 仍为 False",
                details={}
            )

        # ─── 步骤 4：触发异常并捕获上报 ────────────────────
        event_id = _trigger_and_capture_error(trace_id)
        event_captured = bool(event_id)

        # ─── 步骤 5：等待 SDK 异步刷新（保证事件已发送） ──
        flush_action = "sentry_flush_wait"
        flush_t0 = time.time()
        try:
            import sentry_sdk
            sentry_sdk.flush(timeout=10)  # 等待事件队列刷新
            _emit_log(flush_action, trace_id, "info", duration_ms=_ms(flush_t0),
                      result="flushed")
        except Exception as e:
            _emit_log(flush_action, trace_id, "warning", duration_ms=_ms(flush_t0),
                      result="flush_failed", error=str(e))

        # ─── 步骤 6：通过 REST API 验证事件已接收 ──────────
        if not api_token:
            # API Token 未提供，跳过 API 验证（边界显性化：记录跳过原因）
            api_verification_skipped = True
            _emit_log("api_verify_skipped", trace_id, "warning",
                      reason="api_token_not_provided",
                      note="未提供 --api-token，跳过 REST API 事件确认。"
                           "如需完整验证，请添加 --api-token 参数。")
        else:
            # 轮询查询事件
            received, issue = _query_glitchtip_events(
                glitchtip_url=glitchtip_url,
                api_token=api_token,
                trace_id_short=trace_id_short,
                trace_id=trace_id,
                timeout=timeout,
            )
            event_received = received
            issue_data = issue
            if not received:
                # 事件未在超时时间内被接收（不抛异常，记录到报告）
                _emit_log("api_verify_result", trace_id, "warning",
                          result="event_not_received",
                          timeout=timeout,
                          trace_id_short=trace_id_short)

    except GlitchTipVerificationError as e:
        # 已知业务错误，记录到报告
        error_info = {
            "code": e.code,
            "message": e.message,
            "details": e.details,
        }
        _emit_log("verification_error", trace_id, "error",
                  code=e.code, message=e.message, details=e.details)
    except Exception as e:
        # 未预期异常，包装为内部错误
        error_info = {
            "code": GT_ERR_INTERNAL,
            "message": f"未预期异常: {type(e).__name__}: {e}",
            "details": {
                "exception_type": type(e).__name__,
                "traceback": traceback.format_exc(),
            },
        }
        _emit_log("verification_internal_error", trace_id, "error",
                  code=GT_ERR_INTERNAL, error=str(e),
                  exception_type=type(e).__name__)
    finally:
        # ─── 步骤 7：构建健康检查与验证报告 ────────────────
        health = _build_health_check(
            glitchtip_reachable=glitchtip_reachable,
            dsn_valid=dsn_valid,
            sentry_initialized=sentry_initialized,
            event_captured=event_captured,
            event_received=event_received,
            api_verification_skipped=api_verification_skipped,
        )

        # 确定最终验证状态
        if error_info:
            status = "failed"
        elif api_verification_skipped:
            status = "partial"  # 部分验证（未做 API 确认）
        elif event_received:
            status = "success"
        else:
            status = "failed"

        report: Dict[str, Any] = {
            "trace_id": trace_id,
            "trace_id_short": trace_id_short,
            "event_id": event_id,
            "glitchtip_url": glitchtip_url,
            "dsn_preview": dsn[:30] + "...",
            "status": status,
            "sentry_initialized": sentry_initialized,
            "event_captured": event_captured,
            "event_received": event_received,
            "api_verification_skipped": api_verification_skipped,
            "issue": issue_data,
            "health_check": health,
            "error": error_info,
            "timestamp": time.time(),
            "duration_total_ms": None,  # 由调用方填充
        }

        _emit_log("verification_end", trace_id, "info" if status == "success" else "warning",
                  status=status,
                  event_captured=event_captured,
                  event_received=event_received,
                  api_verification_skipped=api_verification_skipped)

    return report


# ============================================================================
# CLI 入口
# ============================================================================

def _parse_args() -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="GlitchTip 生产部署集成验证脚本 - 验证错误上报链路完整性",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  # 基础验证（仅验证事件捕获，不查询 API）
  python verify_glitchtip_integration.py --dsn https://abc@localhost:8001/1

  # 完整验证（含 REST API 事件确认）
  python verify_glitchtip_integration.py \\
      --dsn https://abc@localhost:8001/1 \\
      --glitchtip-url http://localhost:8001 \\
      --api-token <your-api-token> \\
      --timeout 30

  # API Token 获取：GlitchTip Web → 个人设置 → API Keys → 创建（勾选 project:read）
        """,
    )
    parser.add_argument(
        "--dsn",
        required=True,
        help="GlitchTip DSN（必填），格式：https://publickey@host/project_id",
    )
    parser.add_argument(
        "--glitchtip-url",
        default="http://localhost:8001",
        help="GlitchTip 基地址（默认 http://localhost:8001）",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="等待 GlitchTip 处理事件的最大超时秒数（默认 30）",
    )
    parser.add_argument(
        "--api-token",
        default=None,
        help="GlitchTip API Token（可选，用于 REST API 查询事件确认。"
             "获取方式：Web → 个人设置 → API Keys → 创建）",
    )
    return parser.parse_args()


def main() -> int:
    """主入口

    Returns:
        int: 进程退出码（0=成功/部分验证，1=验证失败）
    """
    overall_t0 = time.time()
    args = _parse_args()

    # 执行验证
    report = run_verification(
        dsn=args.dsn,
        glitchtip_url=args.glitchtip_url,
        timeout=args.timeout,
        api_token=args.api_token,
    )

    # 填充总耗时
    report["duration_total_ms"] = _ms(overall_t0)

    # 输出 JSON 验证报告到 stdout（唯一输出到 stdout 的内容）
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))

    # 退出码：success/partial → 0，failed → 1
    return 0 if report.get("status") in ("success", "partial") else 1


if __name__ == "__main__":
    # 幂等性：每次运行独立 trace_id，可重复执行
    # 状态同步机制：force=True 强制重新初始化 SDK，避免全局单例残留
    sys.exit(main())
