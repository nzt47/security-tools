"""E4-5（Inspector）：会话轨迹的**只读投影**（纯函数，不碰 Flask/磁盘）

【不易·不新采一份事实】轨迹 = 会话存储里已经有的 messages（含 steps）。
本模块只做「截断 + 白名单字段 + 分页」，绝不写回、绝不改 messages.jsonl，
也不读 data/messages.jsonl 的冻结池（那是无 session_id 的历史，口径见 docs/handoff/口径.md §13）。

【变易·上限是硬的】正文 2000 字符、单个 step 800 字符、单条消息最多 40 个 step、
一次最多 200 条消息 —— Inspector 是"回看"，不是把整段上下文再搬一遍。

【简易·白名单投影】entry 只有 index/role/ts/chars/body/body_truncated/steps；
step 只有 id/title/status/detail/at；新增字段必须显式加进白名单（防"顺手把内部字段带出去"）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence

#: 单条消息正文保留的最大字符数
MAX_BODY_CHARS = 2000
#: 单个 step.detail 保留的最大字符数
MAX_STEP_DETAIL_CHARS = 800
#: 单条消息最多保留多少个 step
MAX_STEPS_PER_MESSAGE = 40
#: 一次最多回多少条消息（越界夹回）
MAX_ENTRIES = 200
#: 默认窗口
DEFAULT_LIMIT = 50


def _text(value: Any) -> str:
    return str(value if value is not None else "")


def _steps(message: Dict[str, Any]) -> List[Dict[str, Any]]:
    """steps 白名单投影（detail 截断；非字典项跳过）"""
    raw = message.get("steps")
    if not isinstance(raw, (list, tuple)):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        detail = _text(item.get("detail"))
        truncated = len(detail) > MAX_STEP_DETAIL_CHARS
        row: Dict[str, Any] = {
            "id": _text(item.get("id")),
            "title": _text(item.get("title")),
            "status": _text(item.get("status")),
            "detail": detail[:MAX_STEP_DETAIL_CHARS],
            "at": _text(item.get("at")),
        }
        if truncated:
            row["detail_truncated"] = True
        out.append(row)
        if len(out) >= MAX_STEPS_PER_MESSAGE:
            break
    return out


def _clamp_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def build(messages: Sequence[Dict[str, Any]], limit: Any = DEFAULT_LIMIT,
          offset: Any = 0) -> Dict[str, Any]:
    """把 messages 压成轨迹页（旧→新顺序；调用方负责展示顺序）

    Returns:
        {"total": 匹配条数, "offset": n, "limit": n, "entries": [...], "truncated": bool}
    """
    window = _clamp_int(limit, DEFAULT_LIMIT, 1, MAX_ENTRIES)
    start = _clamp_int(offset, 0, 0, max(0, len(messages or ())))
    rows: List[Dict[str, Any]] = []
    for index, message in enumerate(messages or ()):
        if not isinstance(message, dict):
            continue
        body = _text(message.get("content"))
        rows.append({
            "index": index,
            "role": _text(message.get("role")),
            "ts": _text(message.get("timestamp")),
            "chars": len(body),
            "body": body[:MAX_BODY_CHARS],
            "body_truncated": len(body) > MAX_BODY_CHARS,
            "steps": _steps(message),
        })
    total = len(rows)
    page = rows[start:start + window]
    return {"total": total, "offset": start, "limit": window,
            "entries": page, "truncated": start + window < total}


__all__ = [
    "DEFAULT_LIMIT", "MAX_BODY_CHARS", "MAX_ENTRIES", "MAX_STEPS_PER_MESSAGE",
    "MAX_STEP_DETAIL_CHARS", "build",
]
