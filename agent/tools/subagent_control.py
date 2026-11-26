"""`subagent_control` 工具（E2-1）—— 运行中子代理的发消息 / 打断 / 发现 / 收件箱。

设计页 `docs/plan/2026-11-26-e2-multiagent-design.md` §4.2：**单工具多动作**
（list/send/interrupt/inbox），与 `goal` 同形，避免 4 个独立工具带来的 4 倍连锁。

【语义】
  send       不打断当前轮：进 run 的 inbox，执行器下一轮开始前 drain 成 user 消息
  interrupt  协作式：置 cancel_event，执行器在每轮之间检查 ⇒ status=interrupted、保留已产出
  list/inbox 只读

【能力边界（如实说）】只有**内部 LLM 执行器**有逐轮钩子；外部 CLI 通道（子进程）没有，
对它 send/interrupt 不生效（不假装支持）。run 是进程内登记，进程重启即失效。
"""
from __future__ import annotations

import logging

import agent.tools as _tools

logger = logging.getLogger(__name__)

#: 对模型暴露的动作
ACTIONS = ("list", "send", "interrupt", "inbox")

#: 动作分档（list/inbox 只读；send/interrupt 会改变运行中的任务）
READ_ACTIONS = ("list", "inbox")
WRITE_ACTIONS = ("send", "interrupt")


def _disabled() -> dict:
    return {"ok": False, "code": "disabled",
            "error": "运行中子代理控制已关闭（CP_SUBAGENT_CONTROL_ENABLED=0）"}


def _find(run_registry, run_id: str):
    """按 run_id 找句柄；找不到时允许用 delegation_id 兜底（探针/UI 更好用）"""
    target = str(run_id or "").strip()
    if not target:
        return None
    handle = run_registry.get(target)
    if handle is not None:
        return handle
    return run_registry.by_delegation(target)


def run_list(status: str = "running", limit: int = 20) -> dict:
    """列出 run（默认只看正在跑的）"""
    from agent.subagent.runs import enabled, run_registry

    if not enabled():
        return _disabled()
    run_registry.prune()
    runs = run_registry.list_runs(status=status or "running", limit=limit)
    return {"ok": True, "action": "list", "count": len(runs), "runs": runs}


def run_send(run_id: str, message: str, source: str = "tool") -> dict:
    """给运行中的子代理追加一条指令（下一轮生效）"""
    from agent.subagent.runs import enabled, run_registry

    if not enabled():
        return _disabled()
    body = str(message or "").strip()
    if not body:
        return {"ok": False, "action": "send", "error": "message 不能为空"}
    handle = _find(run_registry, run_id)
    if handle is None:
        return {"ok": False, "action": "send", "code": "run_not_found",
                "error": "没有这个 run（可能已结束；先 action=list 看正在跑的）"}
    row = run_registry.append_inbox(handle.run_id, body, source=source)
    if row is None:
        return {"ok": False, "action": "send", "code": "inbox_write_failed",
                "error": "追加指令写入失败"}
    return {"ok": True, "action": "send", "run_id": handle.run_id,
            "status": handle.status, "queued": row}


def run_interrupt(run_id: str, reason: str = "") -> dict:
    """协作式打断（置位，执行器下一轮之间结束）"""
    from agent.subagent.runs import enabled, run_registry

    if not enabled():
        return _disabled()
    handle = _find(run_registry, run_id)
    if handle is None:
        return {"ok": False, "action": "interrupt", "code": "run_not_found",
                "error": "没有这个 run（可能已结束）"}
    if handle.status != "running":
        return {"ok": True, "action": "interrupt", "run_id": handle.run_id,
                "status": handle.status, "already_finished": True}
    handle.reason = str(reason or "运行中被中断")
    handle.cancel_event.set()
    return {"ok": True, "action": "interrupt", "run_id": handle.run_id,
            "status": handle.status, "cancelled": True, "reason": handle.reason}


def run_inbox(run_id: str = "", limit: int = 10) -> dict:
    """读收件箱（不消费）；不指定 run_id 时汇总最近的运行中 run"""
    from agent.subagent.runs import enabled, run_registry

    if not enabled():
        return _disabled()
    try:
        limit = max(1, min(int(limit or 10), 100))
    except (TypeError, ValueError):
        limit = 10
    if run_id:
        handle = _find(run_registry, run_id)
        if handle is None:
            return {"ok": False, "action": "inbox", "code": "run_not_found",
                    "error": "没有这个 run"}
        rows = run_registry.inbox(handle.run_id, limit=limit)
        return {"ok": True, "action": "inbox", "run_id": handle.run_id,
                "count": len(rows), "inbox": rows}
    rows = []
    for item in run_registry.list_runs(status="all", limit=10):
        rows.extend(run_registry.inbox(item["run_id"], limit=limit))
    rows = rows[-limit:]
    return {"ok": True, "action": "inbox", "count": len(rows), "inbox": rows}


def run_action(action: str, **kwargs) -> dict:
    """执行一个 subagent_control 动作（纯函数；异常收口为 ok=False）"""
    act = str(action or "").strip().lower()
    if act not in ACTIONS:
        return {"ok": False, "error": "未知动作：%s（可用：%s）" % (action, "/".join(ACTIONS))}
    try:
        if act == "list":
            return run_list(status=str(kwargs.get("status") or "running"),
                            limit=kwargs.get("limit") or 20)
        if act == "send":
            return run_send(str(kwargs.get("run_id") or ""),
                            str(kwargs.get("message") or ""))
        if act == "interrupt":
            return run_interrupt(str(kwargs.get("run_id") or ""),
                                 str(kwargs.get("reason") or ""))
        return run_inbox(str(kwargs.get("run_id") or ""), limit=kwargs.get("limit") or 10)
    except Exception as exc:  # noqa: BLE001 工具链路绝不外抛
        logger.error("[subagent_control] 动作 %s 异常: %s", act, exc, exc_info=True)
        return {"ok": False, "action": act, "error": "运行中控制异常: %s" % exc}




#: 工具入参 schema（YAML 与注册**同一份**，避免两处漂移）
TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": list(ACTIONS),
            "description": "list/send/interrupt/inbox",
        },
        "run_id": {
            "type": "string",
            "description": "目标 run（send/interrupt/inbox 必填；可用 delegation_id 兜底）",
        },
        "message": {
            "type": "string",
            "description": "send 必填：追加指令（下一轮生效，≤4000 字）",
        },
        "reason": {"type": "string", "description": "interrupt 可选：打断原因"},
        "status": {
            "type": "string",
            "description": "list 可选：running（默认）| all | done | interrupted | failed",
        },
        "limit": {"type": "integer", "description": "列表/收件箱条数（默认 20/10）"},
    },
    "required": ["action"],
}


def register_all(dl=None):
    """注册 `subagent_control` 工具（签名必须能接生命周期管理器传的 self）"""

    @_tools.register(
        "subagent_control",
        (
    "查看/控制**正在运行**的子代理（委派执行中）：list 列出正在跑的 run（含子代理名/"
    "轮次/耗时/最后输出）；send 给某个 run 追加一条指令（**不进断当前轮**，下一轮生效）；"
    "interrupt 协作式打断（执行器在每轮之间结束，已产出保留）；inbox 读该 run 的收件箱。"
    "run_id 可用 delegation_id 兜底。只有内部 LLM 执行器响应 send/interrupt，"
    "外部 CLI 通道不支持。进程重启后登记表失效。"
    "Inspect and steer running subagent delegations"
    ),
        schema=TOOL_SCHEMA,
    )
    def _control_tool(**kwargs):
        action = str(kwargs.pop("action", "") or "")
        return run_action(action, **kwargs)


__all__ = ["ACTIONS", "register_all", "run_action",
           "run_list", "run_send", "run_interrupt", "run_inbox"]
