# -*- coding: utf-8 -*-
"""LLM 响应有效性判定与空返回降级（异常 B：「（模型未返回内容）」）

为什么单独成模块
----------------
``content`` 为空的成因分散在多个外呼点（非流式 ``LLMService.chat``、
流式 ``LLMService.chat_stream``、工作台 ``/api/chat/stream`` SSE）。
若每个点各写一套"算不算空"的判断，就会出现**口径不一致**——
这正是异常 B 修复前的状态：全仓只有 ``plugins/chat.py:1270`` 的
``if not emitted:`` 一处兜底文案，且**没有任何结构化日志**
（`event=llm_empty_response` 在修复前全仓不存在）。

所以这里做**唯一口径**：一个判定函数 + 一个结构化日志函数 + 一个降级文案函数，
三处外呼点共用。

判定口径（TASK-01 §3 修复 B 第 1 条）
------------------------------------
``content`` 为空 **且** 无 ``tool_calls`` **且** 无有效推理内容 ⇒ **无效**。

特别注意：``content=""`` + ``finish_reason="tool_calls"`` + 有 ``tool_calls``
是**有效**响应（模型正要调工具），不得当成空返回——否则会把正常的工具轮次
误判成异常，前端会出现"明明在干活却提示没返回内容"。
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Mapping, Sequence

logger = logging.getLogger(__name__)

__all__ = [
    "EMPTY_RESPONSE_TEXT",
    "DEFAULT_RAW_PREFIX_CHARS",
    "is_valid_response",
    "empty_response_fields",
    "log_empty_response",
    "degraded_text",
]

#: 保留的历史文案（用户已见过；改动会破坏前端快照/用户认知）
EMPTY_RESPONSE_TEXT = "（模型未返回内容）"
#: 结构化日志里保留的响应原文前缀长度
DEFAULT_RAW_PREFIX_CHARS = 500


def is_valid_response(content: Any = None,
                      tool_calls: Sequence[Any] | None = None,
                      reasoning: Any = None) -> bool:
    """统一响应有效性判定（**唯一口径**）

    Args:
        content: 上游 ``message.content``（或流式拼接结果）
        tool_calls: 结构化工具调用列表（非空即为有效）
        reasoning: ``reasoning_content``（DeepSeek thinking 模式）

    Returns:
        bool: 是否为**有效**响应
    """
    if isinstance(content, str):
        if content.strip():
            return True
    elif content:
        # 非字符串但非空（例如 content 是 list[dict] 的多模态片段）
        return True
    if tool_calls:
        return True
    if isinstance(reasoning, str) and reasoning.strip():
        return True
    return False


def empty_response_fields(*,
                          provider: str = "",
                          model: str = "",
                          finish_reason: str = "",
                          has_tool_calls: bool = False,
                          prompt_tokens: int | None = None,
                          completion_tokens: int | None = None,
                          elapsed_ms: float | None = None,
                          request_id: str = "",
                          raw_prefix: str = "",
                          source: str = "") -> dict[str, Any]:
    """构造 ``event=llm_empty_response`` 的**结构化字段**

    字段集合按 TASK-01 §3 修复 B 第 2 条固定；``raw_prefix`` 只取前
    :data:`DEFAULT_RAW_PREFIX_CHARS` 个字符（既够定位，又不至于把整段
    上游原文写进日志）。
    """
    prefix = raw_prefix or ""
    if len(prefix) > DEFAULT_RAW_PREFIX_CHARS:
        prefix = prefix[:DEFAULT_RAW_PREFIX_CHARS]
    return {
        "event": "llm_empty_response",
        "source": source,
        "provider": provider,
        "model": model,
        "finish_reason": finish_reason,
        "has_tool_calls": bool(has_tool_calls),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "elapsed_ms": round(elapsed_ms, 1) if isinstance(elapsed_ms, (int, float)) else elapsed_ms,
        "request_id": request_id,
        "raw_prefix": prefix,
    }


def log_empty_response(*, logger_: logging.Logger | None = None, **kwargs: Any) -> dict[str, Any]:
    """记录一条 WARN 级 ``event=llm_empty_response`` 结构化日志

    **这是修复前不存在的证据链**：以前只显示文案、不落任何结构化记录，
    导致线上出现「（模型未返回内容）」时无从判断是上游空 ``choices``、
    ``finish_reason=tool_calls`` 却没带 tool_calls、还是超时降级。

    Returns:
        写入日志的字段字典（便于测试断言 / 调用方二次使用）。
    """
    fields = empty_response_fields(**kwargs)
    lg = logger_ or logger
    try:
        lg.warning("[LLM] 空返回: %s", fields)
    except Exception:  # noqa: BLE001 日志失败不得影响主链路
        pass
    return fields


def degraded_text(finish_reason: str = "", reason: str = "",
                  retry_hint: bool = True) -> str:
    """空返回时的**明确降级文案**（禁止空串）

    保留 ``EMPTY_RESPONSE_TEXT`` 作为前缀（前端与用户都已认识这句话），
    后面补上可操作信息：上游的 ``finish_reason`` 与重试提示。
    """
    detail: list[str] = []
    if finish_reason:
        detail.append("finish_reason=%s" % (finish_reason,))
    if reason:
        detail.append(str(reason))
    tail = ("；" + "，".join(detail)) if detail else ""
    hint = " 请重试，或检查模型/网关是否支持当前请求参数。" if retry_hint else ""
    return "%s（上游未返回文本%s）。%s" % (EMPTY_RESPONSE_TEXT, tail, hint)
