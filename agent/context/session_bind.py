"""请求级会话绑定（ContextVar）—— 工具侧"当前会话"的单一来源。

【为什么单独一层】`goal` / `ask_user_question` 等工具都需要"这次调用属于哪个会话"，
但工具参数里没有 session_id（模型不知道）。绑定由会话入口（`plugins/chat.py` 开流时）
写入；并发两个会话时 ContextVar 各自持有，不会互相覆盖。

解析顺序：请求级绑定 → `TraceContext.subject_id`（编排器任务级 Trace 起点写入）
→ 空串（调用方据此返回 `ok=False`，绝不落到共享默认键）。
"""
from __future__ import annotations

import contextvars

_BOUND: contextvars.ContextVar[str] = contextvars.ContextVar(
    "yunshu_bound_session", default="")


def bind(session_id: str):
    """绑定当前执行上下文到某会话（返回 token，供 reset）"""
    return _BOUND.set(str(session_id or ""))


def reset(token) -> None:
    try:
        _BOUND.reset(token)
    except (ValueError, RuntimeError):  # 跨线程 reset 失败不影响主链路
        pass


def current() -> str:
    """当前会话：请求级绑定优先，回退 TraceContext 的 subject_id"""
    sid = _BOUND.get()
    if sid:
        return sid
    try:
        from agent.observability.trace_v2 import TraceContext  # 惰性导入
        ctx = TraceContext.current()
        return str(getattr(ctx, "subject_id", "") or "").strip()
    except Exception:  # noqa: BLE001 观测上下文不可用不影响失败语义
        return ""


__all__ = ["bind", "current", "reset"]
