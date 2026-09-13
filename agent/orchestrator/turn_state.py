"""TASK-S9-01：会话级「最近一轮」状态存储（tool_steps / reasoning）。

**为什么需要本模块**（真机现象，见 docs/zh/真用前置_模型凭证核查_20260913.md §八 D2）:

修复前这两个值直接挂在全局单例 ``Orchestrator`` 的实例属性
（``_last_tool_steps`` / ``_last_reasoning``）上，是 last-write-wins 的全局槽位：

1. **跨轮串台** —— 本轮没写入时，HTTP 响应装配读到的是**上一轮**（乃至其它会话）的值，
   实测「2 加 3 等于多少」的 ``reasoning`` 与上一轮「列文件被截断」的思考逐字相同；
2. **``or`` 回退放大** —— ``self._last_reasoning = _result.get("reasoning") or self._last_reasoning``
   在 ``None`` 时主动保留旧值，是串台最直接的注入点。

**本模块的契约**（对应 TASK-S9-01 验收判据 1 / 3）:

- 按会话键隔离：每个会话各自持有 ``current``（本轮）与 ``previous``（上一轮）；
- ``snapshot()`` 只返回**本轮**内容，本轮没写就返回空（``[]`` / ``None``），
  **绝不**回退上一轮或其它会话的值；
- ``set()`` 是唯一的写入口，**显式赋值**：显式传 ``None`` 就真实落 ``None``，
  不存在任何 ``value or old_value`` 形式的回退；
- 会话数有界（``OrderedDict`` + 上限 + 淘汰最旧），长跑进程不因会话增长而泄漏内存；
- 读写均由内部锁保护，多用户并发下不同会话互不干扰。

本模块**只做存储**，不承担路由、上下文装配或响应装配语义。
"""

from __future__ import annotations

import collections
import os
import threading
from typing import Any, Dict, Iterator, List, Optional

#: 会话键缺省值：调用方无法提供 session_id 时（CLI / 单会话场景）的归一化键
DEFAULT_SESSION_KEY = "__default__"

#: 「未传该字段」的哨兵 —— 与「显式传 None」严格区分（显式 None 必须真实落 None）
UNSET: Any = object()

#: 会话数上限：超出后淘汰最久未更新的会话，保证内存有界（env 可配）
DEFAULT_MAX_SESSIONS = int(os.environ.get("ORCHESTRATOR_TURN_STATE_MAX_SESSIONS", "256"))


def _empty_state() -> Dict[str, Any]:
    return {"tool_steps": [], "reasoning": None}


class TurnStateStore:
    """按会话隔离的「本轮 / 上一轮」状态存储。

    线程安全。所有返回给调用方的快照都是**新建对象**（含 ``tool_steps`` 列表的浅拷贝），
    调用方修改返回值不会反向污染内部状态。
    """

    def __init__(self, max_sessions: int = DEFAULT_MAX_SESSIONS) -> None:
        self._max_sessions = max(1, int(max_sessions))
        self._lock = threading.Lock()
        #: key -> {"current": state, "previous": state}
        self._sessions: "collections.OrderedDict[str, Dict[str, Dict[str, Any]]]" = \
            collections.OrderedDict()

    # ── 内部 ────────────────────────────────────────────────────

    def _entry_locked(self, key: str) -> Dict[str, Dict[str, Any]]:
        """取（或建）会话条目；调用方必须已持锁。顺带做 LRU 淘汰。"""
        entry = self._sessions.get(key)
        if entry is None:
            entry = {"current": _empty_state(), "previous": _empty_state()}
            self._sessions[key] = entry
            # 超出上限 → 淘汰最旧（队首）
            while len(self._sessions) > self._max_sessions:
                self._sessions.popitem(last=False)
        else:
            self._sessions.move_to_end(key)
        return entry

    @staticmethod
    def _copy(state: Dict[str, Any]) -> Dict[str, Any]:
        steps = state.get("tool_steps") or []
        return {"tool_steps": list(steps), "reasoning": state.get("reasoning")}

    # ── 写 ──────────────────────────────────────────────────────

    def begin(self, key: str) -> None:
        """本轮开始：把 ``current`` 滚入 ``previous``，并清空 ``current``。

        每轮入口调用一次。清空是「本轮无内容就返回空」的关键 —— 没有它，
        走短路路径（模板 / 语义层）的轮次会读到上一轮残留。
        """
        with self._lock:
            entry = self._entry_locked(key)
            entry["previous"] = entry["current"]
            entry["current"] = _empty_state()

    def set(self, key: str, *, tool_steps: Any = UNSET,
            reasoning: Any = UNSET) -> None:
        """显式写入本轮状态（唯一写入口）。

        未传的字段保持原值（不是「回退旧值」——是「本次不涉及该字段」）；
        显式传入 ``None`` 时真实落 ``None``，**不做任何 ``or`` 回退**。
        """
        with self._lock:
            current = self._entry_locked(key)["current"]
            if tool_steps is not UNSET:
                current["tool_steps"] = list(tool_steps or [])
            if reasoning is not UNSET:
                current["reasoning"] = reasoning

    # ── 读 ──────────────────────────────────────────────────────

    def snapshot(self, key: str) -> Dict[str, Any]:
        """本轮快照；本轮无内容 → ``{"tool_steps": [], "reasoning": None}``。"""
        with self._lock:
            entry = self._sessions.get(key)
            if entry is None:
                return _empty_state()
            return self._copy(entry["current"])

    def previous(self, key: str) -> Dict[str, Any]:
        """上一轮快照（供上下文装配复用）；无记录 → 空。"""
        with self._lock:
            entry = self._sessions.get(key)
            if entry is None:
                return _empty_state()
            return self._copy(entry["previous"])

    # ── 观测 / 维护 ─────────────────────────────────────────────

    def sessions(self) -> List[str]:
        """当前持有状态的会话键列表（有界性断言 / 诊断用）。"""
        with self._lock:
            return list(self._sessions.keys())

    def clear(self) -> None:
        """清空全部会话状态（测试隔离 / 生命周期重置用）。"""
        with self._lock:
            self._sessions.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)

    def __iter__(self) -> Iterator[str]:
        return iter(self.sessions())


def normalize_session_key(session_id: Optional[str]) -> str:
    """会话键归一化：``None`` / 空串 → :data:`DEFAULT_SESSION_KEY`。"""
    if session_id is None:
        return DEFAULT_SESSION_KEY
    key = str(session_id).strip()
    return key or DEFAULT_SESSION_KEY
