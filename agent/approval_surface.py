# -*- coding: utf-8 -*-
"""审批"呈现面"标记（inline = 对话内弹窗；空 = 收件箱）—— 极小叶模块。

【为什么要有这个标记（2026-11-08 Operator 要求）】
「碰到需要审批的工具直接弹窗给选择，不要放审批收件箱」。审批链本身不变（仍要挂单 →
人工裁决 → 单次消费，审计与时效都在），变的是**人从哪里看到它**：
对话里发起、本该在对话里确认的审批，不该在收件箱里堆成待办等人去翻。

做法：对话链路在**执行工具的那一小段**把 surface 置为 `inline`（contextvar，随请求走），
`tool_gate` 挂单时把它写进审批记录 payload 的 `origin`；`GET /api/approval/pending`
默认不列 `origin=inline` 的记录（要看用 `?include_inline=1`）。

【为什么用 contextvar 而不是给工具加参数】
`request_approval` 的签名会被闸门、桥接层、审批流三处调用；把"我这一轮是弹窗还是收件箱"
塞进工具参数等于污染业务签名。contextvar 天然按请求/协程隔离，且不会跨请求泄漏
（对话链路用 `with set_surface("inline"):` 包住，退出即复位）。
"""
from __future__ import annotations

import contextvars
from typing import Iterator
from contextlib import contextmanager

#: payload.origin 的取值：对话内弹窗
ORIGIN_INLINE = "inline_chat"
#: payload.origin 的取值：走收件箱（默认，空串也按它处理）
ORIGIN_INBOX = "inbox"

_SURFACE: "contextvars.ContextVar[str]" = contextvars.ContextVar("approval_surface", default="")


def current_surface() -> str:
    """当前审批呈现面（`""` = 未声明，按收件箱处理）。"""
    return str(_SURFACE.get() or "")


def set_surface(surface: str) -> "contextvars.Token[str]":
    """设置呈现面，返回 token（调用方负责 reset）。"""
    return _SURFACE.set(str(surface or ""))


def reset_surface(token: "contextvars.Token[str]") -> None:
    try:
        _SURFACE.reset(token)
    except Exception:  # noqa: BLE001 reset 失败不影响请求（只是标记）
        pass


@contextmanager
def inline_surface() -> Iterator[None]:
    """`with inline_surface():` —— 这段里的挂单一律标成"对话内弹窗"。"""
    token = set_surface(ORIGIN_INLINE)
    try:
        yield
    finally:
        reset_surface(token)


def origin_of(payload: object) -> str:
    """从审批记录 payload 里取 origin（缺省/非映射 ⇒ 空串）。"""
    if isinstance(payload, dict):
        return str(payload.get("origin") or "")
    return ""


def is_inline_payload(payload: object) -> bool:
    return origin_of(payload) == ORIGIN_INLINE
