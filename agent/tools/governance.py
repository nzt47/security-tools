"""工具治理小件（E0-4）：重复调用提醒。

【为什么放在 `tools.call`】它是工具分发的**唯一汇聚点**（工作台 SSE 与 orchestrator 直连
都走它）：放这一层一次覆盖所有调用方，不必逐个业务工具去补（与 spill/污点打标同一理由）。

【与 DSH 的差异（如实写，不冒充等价）】
- DSH `repeat-tool-reminder` 注入一条 user-role 提醒消息；本实现把提醒放进**结果字段**
  `_repeat`（模型读到的工具结果本来就是 JSON），并记审计。语义相同（提醒、不阻断、可退避），
  形状按云枢「结果即上下文」的口径。
- DSH `time-context`（每步时钟）**本次未做**：把时钟塞进每个工具结果会被「结果整字典\n  相等」的既有契约断言拦住（实测 7 处 unit 失败），正确做法是在 chat/orchestrator 的\n  消息通道里选一个正式注入点；已登记为余项（data/handoff_next.json）。\n- 通用工具超时本仓**已有**（`agent/timeout_budget.py` 的分层上界 + run 级 deadline，
  `CP_TOOL_HANDLER_TIMEOUT_SEC`），本模块不重复造一套；缺的「协作式取消」登记为余项。

【退避】两条都只改结果形状、不阻断调用；任一开关置 0 即回到逐字旧行为。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

REPEAT_ENABLED_ENV = "CP_TOOL_REPEAT_REMINDER"
REPEAT_THRESHOLDS_ENV = "CP_TOOL_REPEAT_THRESHOLDS"
REPEAT_WINDOW_ENV = "CP_TOOL_REPEAT_WINDOW_SECONDS"

#: 默认提醒点（与 DSH 默认 [3,5,8] 对齐）
_DEFAULT_THRESHOLDS: Tuple[int, ...] = (3, 5, 8)
_DEFAULT_WINDOW_SEC = 300.0
_WINDOW_MIN, _WINDOW_MAX = 10.0, 3600.0
_MAX_TRACKED_KEYS = 256

#: 不参与重复检测的工具：轮询/状态类「同参重复」是合法用法，提醒只会干扰
_EXCLUDE = frozenset({
    "todo_write", "get_status", "get_sensor_summary", "get_task_status",
    "get_task_result", "list_async_tasks", "list_processes", "read_spill",
})

_local = threading.local()


def repeat_enabled() -> bool:
    # 逐条字面量读取（settings 零缺口门禁不接受经函数参数传递的动态读取）
    raw = str(os.environ.get("CP_TOOL_REPEAT_REMINDER", "1") or "").strip().lower()
    return raw not in ("0", "false", "no", "off", "disable", "disabled")


def thresholds() -> Tuple[int, ...]:
    """提醒点（逗号分隔，去重升序；非法/不足两个值回落默认）。"""
    raw = str(os.environ.get(REPEAT_THRESHOLDS_ENV, "") or "").strip()
    if not raw:
        return _DEFAULT_THRESHOLDS
    out = []
    for piece in raw.split(","):
        try:
            value = int(float(piece.strip()))
        except (TypeError, ValueError):
            continue
        if value >= 2:
            out.append(value)
    return tuple(sorted(set(out))) if out else _DEFAULT_THRESHOLDS


def window_seconds() -> float:
    raw = str(os.environ.get(REPEAT_WINDOW_ENV, "") or "").strip()
    if not raw:
        return _DEFAULT_WINDOW_SEC
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_WINDOW_SEC
    return max(_WINDOW_MIN, min(_WINDOW_MAX, value))


def _params_digest(params: Any) -> str:
    try:
        text = json.dumps(params or {}, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        text = str(params)
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def _state() -> Dict[str, list]:
    state = getattr(_local, "seen", None)
    if state is None:
        state = {}
        _local.seen = state
    return state


def reset_state() -> None:
    """清空本线程的重复计数（测试/会话边界用）。"""
    _local.seen = {}


def note_call(name: str, params: Any, *, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """记账一次调用；命中提醒点返回提醒字典，否则 None。"""
    if not repeat_enabled():
        return None
    tool = str(name or "")
    if not tool or tool in _EXCLUDE:
        return None
    ts = float(now if now is not None else time.time())
    key = tool + "|" + _params_digest(params)
    state = _state()
    entry = state.get(key)
    if entry is None or (ts - float(entry[1])) > window_seconds():
        entry = [0, ts, ts]  # [count, first_ts, last_ts]
        if len(state) >= _MAX_TRACKED_KEYS:
            # 有界：满了就清最旧的一批（不做 LRU 精确排序，避免每条调用都 O(n log n)）
            for stale in list(state)[:_MAX_TRACKED_KEYS // 2]:
                state.pop(stale, None)
    entry[0] += 1
    entry[2] = ts
    state[key] = entry
    count = int(entry[0])
    points = thresholds()
    if count not in points:
        return None
    return {
        "tool": tool,
        "count": count,
        "thresholds": list(points),
        "window_seconds": window_seconds(),
        "hint": (f"同一工具 + 完全相同的参数已第 {count} 次调用：先读上一次结果，"
                 "确认是否真的需要重试，或换参数/换做法；连续重复通常意味着卡住了"),
    }


def _audit_repeat(reminder: Dict[str, Any]) -> None:
    try:
        from agent.audit.facade import audit
        audit.record("tool_repeat_suspected", actor="tools.governance",
                     subject=str(reminder.get("tool") or "tool"),
                     payload={"count": reminder.get("count"),
                              "thresholds": reminder.get("thresholds"),
                              "params_recorded": False},
                     source="agent")
    except Exception as exc:  # noqa: BLE001  审计是旁路
        logger.debug("[governance] 重复提醒审计失败: %s", type(exc).__name__)


def annotate_result(name: str, params: Any, result: Any, *,
                    now: Optional[float] = None) -> None:
    """给 dict 结果补 `_repeat`（命中提醒点）。就地修改，不返回值。"""
    if not isinstance(result, dict):
        return
    reminder = note_call(name, params, now=now)
    if reminder is not None:
        result["_repeat"] = reminder
        _audit_repeat(reminder)


def status() -> Dict[str, Any]:
    return {
        "repeat_reminder": repeat_enabled(),
        "thresholds": list(thresholds()),
        "window_seconds": window_seconds(),
        "tracked_keys": len(_state()),
        "excluded": sorted(_EXCLUDE),
        "tool_timeout": "agent/timeout_budget.py（CP_TOOL_HANDLER_TIMEOUT_SEC，已存在）",
    }


__all__ = [
    "REPEAT_ENABLED_ENV", "REPEAT_THRESHOLDS_ENV", "REPEAT_WINDOW_ENV",
    "repeat_enabled", "thresholds", "window_seconds",
    "note_call", "annotate_result", "reset_state", "status",
]



