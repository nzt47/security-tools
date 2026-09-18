"""叶子契约：底层模块的 best-effort 留痕出口（S11-10 / R2）。

【为什么存在】
`agent.utils.cross_process_lock` 与 `agent.observability.trace_v2` 在锁争用/写路径降级时
都要向事件层做 **best-effort 留痕**；而 `agent.observability.events` 又**模块级**依赖
`agent.utils.cross_process_lock`（事件文件写入要取跨进程锁，见 `events.py:44`）。
底层若直接 import 事件层，就形成 `events ↔ cross_process_lock`、
`events ↔ trace_v2` 两个环，被 architecture-check 的 `no_circular_dependency` 阻断。

【怎么解】把"留痕出口"下沉为本**叶子模块**（自身不依赖任何 `agent.*`）：

    observability.events ──注册──▶ obs_hooks ◀──调用── utils.cross_process_lock
                                          ◀──调用── observability.trace_v2

边方向单向汇入叶子 ⇒ 环消散。等价于依赖倒置：底层只声明"我要留痕"，
"留痕具体发给谁"由上层在导入时注册进来。

【行为边界（必须知情，对应 S11-10 硬约束 #3）】
未注册时调用 `emit_event` 只记 debug 日志、**不落痕** —— 即"留痕需事件层已被导入"。
原实现是函数内 `from agent.observability.events import emit`（按需 import，首次降级时
一定会把事件层拉起来）。在服务与常规测试运行期事件层都会被导入，实际影响≈0，
但这是**真实的行为变化**，已在 S11-10 报告与提交信息中显式声明。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

EventEmitter = Callable[..., Any]

_EMITTERS: List[EventEmitter] = []


def register_event_emitter(fn: EventEmitter) -> None:
    """注册事件发射器（幂等）。由事件层在**导入时**调用。"""
    if fn not in _EMITTERS:
        _EMITTERS.append(fn)
        logger.debug("留痕出口已注册: %r", getattr(fn, "__qualname__", fn))


def emit_event(
    event_type: str,
    payload: Optional[Dict[str, Any]] = None,
    *,
    actor: str = "auto",
) -> bool:
    """best-effort 留痕：逐个发射器尝试，任一失败不影响其它，**绝不抛出**。

    Args:
        event_type: 事件类型
        payload: 结构化载荷
        actor: 触发者标识

    Returns:
        True 表示至少一个发射器成功；False 表示无发射器或全部失败。
    """
    if not _EMITTERS:
        logger.debug(
            "留痕出口未注册，事件 %s 未落痕（事件层尚未导入）", event_type
        )
        return False
    ok = False
    for fn in list(_EMITTERS):
        try:
            fn(event_type, payload, actor=actor)
            ok = True
        except Exception as exc:  # noqa: BLE001 留痕失败不阻断主流程
            logger.warning("留痕失败（%s）: %s", event_type, exc)
    return ok
