"""`goal` 工具（E1-2b）—— 模型侧的目标读写入口。

设计页 `docs/plan/2026-11-25-e1-2-goal-design.md` §3：**单工具多动作**
（create/edit/pause/resume/complete/block/clear/status），状态机与 `/goal` 命令族
**完全同源**（都走 `agent.context.goal`），不造第二条写入路径。

【会话从哪来】工具参数里没有 session_id（模型不知道）。解析顺序：
  ① 请求级 ContextVar（`plugins/chat.py` 开流时 bind，见 `goal.bind_session`）；
  ② `TraceContext.subject_id`（编排器任务级 Trace 起点写入，与 todo_write 同源）；
  ③ 都拿不到 ⇒ 返回 `ok=False`（**绝不**落到共享默认键：目标写错会话比失败更糟）。
"""
from __future__ import annotations

import logging

import agent.tools as _tools
from agent.context import goal as _goal

logger = logging.getLogger(__name__)

#: 工具对模型暴露的动作
ACTIONS = ("create", "edit", "pause", "resume", "complete", "block", "clear", "status")


def resolve_session() -> str:
    """当前工具调用所属的会话（取不到返回空串，由 handler 转成 ok=False）"""
    try:
        sid = _goal.current_session()
    except Exception:  # noqa: BLE001 目标模块自身故障不得击穿工具链
        sid = ""
    return sid


def _summary(action: str, st: dict) -> dict:
    """只回状态摘要（≤400 字符），不把事件流全量塞回模型（预算）"""
    blocked = st.get("blocked") or {}
    return {
        "ok": True,
        "action": action,
        "goal_id": st.get("goal_id", ""),
        "state": st.get("state", "idle"),
        "title": st.get("title", ""),
        "round": int(st.get("round") or 0),
        "max_rounds": int(st.get("max_rounds") or 0),
        "acceptance_count": len(st.get("acceptance") or []),
        "blocked_code": blocked.get("code", ""),
        "blocked_reason": blocked.get("reason", ""),
        "evidence_provided": bool(st.get("evidence")),
    }


def run_goal_action(action: str, session_id: str, **kwargs) -> dict:
    """执行一个 goal 动作（纯函数，便于单测；异常一律收口为 ok=False）"""
    act = str(action or "").strip().lower()
    if act not in ACTIONS:
        return {"ok": False, "error": "未知动作：%s（可用：%s）" % (action, "/".join(ACTIONS))}
    if not session_id:
        return {"ok": False,
                "error": "无法确定会话上下文（目标工具必须在一次会话对话里调用）"}
    try:
        if act == "create":
            st = _goal.create(
                session_id,
                str(kwargs.get("title") or ""),
                why=str(kwargs.get("why") or ""),
                acceptance=list(kwargs.get("acceptance") or []),
                max_rounds=kwargs.get("max_rounds") or _goal.DEFAULT_MAX_ROUNDS,
                budget_tokens=kwargs.get("budget_tokens"),
            )
        elif act == "edit":
            st = _goal.edit(
                session_id,
                title=kwargs.get("title"),
                why=kwargs.get("why"),
                acceptance=kwargs.get("acceptance"),
            )
        elif act == "pause":
            st = _goal.pause(session_id, str(kwargs.get("reason") or ""))
        elif act == "resume":
            st = _goal.resume(session_id)
        elif act == "complete":
            st = _goal.complete(
                session_id,
                evidence=str(kwargs.get("evidence") or ""),
                summary=str(kwargs.get("summary") or ""),
            )
        elif act == "block":
            st = _goal.block(
                session_id,
                code=str(kwargs.get("code") or ""),
                reason=str(kwargs.get("reason") or ""),
            )
        elif act == "clear":
            st = _goal.clear(session_id)
        else:  # status
            st = _goal.get_state(session_id)
    except _goal.GoalError as exc:
        return {"ok": False, "error": exc.message, "code": exc.code}
    except Exception as exc:  # noqa: BLE001 工具链路上绝不外抛异常
        logger.error("[goal] 动作 %s 异常: %s", act, exc, exc_info=True)
        return {"ok": False, "error": "目标操作异常: %s" % exc}
    return _summary(act, st)


def register_all(dl):
    """注册目标工具（``goal``）"""
    @_tools.register(
        "goal",
        "维护本会话的**单一持续目标**（goal / 目标 / 长期任务）：create 创建（必填 title，"
        "建议给 acceptance 验收条件）；edit 修改；pause/resume 暂停恢复；complete 完成"
        "（**必须带 evidence**：命令输出/文件/检查点 id）；block 阻塞（必须带 "
        "code∈{need_user,external,permission,unclear} + reason）；clear 清除；status 查看。"
        "一个会话只有一个目标；目标会进入系统提示，每轮收尾由 driver 决定是否自动续跑"
        "（轮次上限默认 256）。多步推进请配合 todo_write 记录步骤。"
        "Manage the single persistent goal of this session",
        schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(ACTIONS),
                    "description": "create/edit/pause/resume/complete/block/clear/status",
                },
                "title": {"type": "string", "description": "目标标题（create 必填，≤200 字）"},
                "why": {"type": "string", "description": "为什么要做（可选，≤2000 字）"},
                "acceptance": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "验收条件列表（建议 create 时给出：怎样才算完成）",
                },
                "max_rounds": {
                    "type": "integer",
                    "minimum": _goal.MIN_ROUNDS,
                    "maximum": _goal.MAX_ROUNDS,
                    "description": "轮次上限（默认 256）",
                },
                "budget_tokens": {"type": "integer", "description": "可选 token 预算"},
                "evidence": {
                    "type": "string",
                    "description": "complete 必填：完成证据（命令输出/文件路径/检查点 id）",
                },
                "summary": {"type": "string", "description": "完成摘要（可选）"},
                "code": {
                    "type": "string",
                    "enum": list(_goal.BLOCK_CODES),
                    "description": "block 必填：need_user/external/permission/unclear",
                },
                "reason": {"type": "string", "description": "block 必填：阻塞原因"},
            },
            "required": ["action"],
        },
    )
    def _goal_tool(**kwargs):
        # action 既作位置参数又可能留在 kwargs 里 ⇒ 先摘掉，避免 "multiple values" 崩溃
        action = str(kwargs.pop("action", "") or "")
        return run_goal_action(action, resolve_session(), **kwargs)


__all__ = ["ACTIONS", "register_all", "resolve_session", "run_goal_action"]
