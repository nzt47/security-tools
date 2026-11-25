"""E1-2a Goal —— 会话级单目标的**事件溯源**存储（无模型工具、无 HTTP 路由）。

【为什么先做服务层】设计页 §9：E1-2 拆四片，a 片只做"唯一事实源 + 投影 + 命令"。
没有 UI/工具之前**不造 HTTP 路由**（避免无引用路由债，同 E0-3 口径）；模型工具放 b 片。

【存储】``data/goals/<sha256(session)[:32]>/``
  - ``events.jsonl``：**唯一事实源**，append-only；每行 ``{seq, at, type, payload}``；
  - ``state.json``：投影快照（唯一作用是把"读状态"从 O(事件数) 降到 O(1)；
    损坏/缺失时由 events 重放重建）。

【为什么不复用 todo_write / planning / 调度】设计页 §1：todo 是"做哪些步骤"的清单、
planning 是内部任务 DAG、调度是周期性任务、交接是跨会话人工文档；Goal 是
"一个会话持续推进直到完成"的**单一目标**，四者都不承载它的生命周期与轮次。

【安全】目录名 = sha256(session_id) 前 32 位 + 正则捕获后再进路径（CodeQL 三铁律）；
删除只按常量文件名 + 由投影里 int 值决定的 v 序号，不拿 ``os.listdir`` 结果拼路径。
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

#: 计划目录（``set_root`` 覆盖，仅测试/探针使用）
_ROOT_OVERRIDE: Optional[str] = None

#: 同一进程内多会话/多线程读写的整体性保护（追加事件 + 重写投影必须原子）
_LOCK = threading.RLock()

#: 投影里保留的最近事件条数（供 UI/交接查看；完整历史在 events.jsonl）
RECENT_EVENTS = 20

#: 轮次上限（DSH 默认 256；创建时可指定 1–1000）
DEFAULT_MAX_ROUNDS = 256
MIN_ROUNDS = 1
MAX_ROUNDS = 1000

#: 阻塞原因码（稳定 code，便于 UI/交接分流）
BLOCK_CODES = ("need_user", "external", "permission", "unclear")

#: 目标状态
STATES = ("idle", "active", "paused", "blocked", "completed")

#: 事件类型
EVENT_TYPES = (
    "goal_created", "goal_edited", "goal_paused", "goal_resumed",
    "goal_completed", "goal_blocked", "goal_round", "goal_cleared",
    "goal_accept",
)

_KEY_RE = re.compile(r"^(?P<key>[0-9a-f]{32})$")

#: 请求级会话绑定：工作台流式入口在开流时 bind，工具（goal 等）据此定位"当前会话"。
#: 为什么用 ContextVar 而不是全局变量：并发两个会话时全局会被互相覆盖（与权限档位同因）。
_BOUND_SESSION: contextvars.ContextVar[str] = contextvars.ContextVar(
    "yunshu_goal_session", default="")


class GoalError(ValueError):
    """状态机拒绝的操作（命令层据此回人读提示，而不是 500）"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ════════════════════════════════════════════════════════════
#  路径与基础工具
# ════════════════════════════════════════════════════════════

def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def set_root(path: Optional[str]) -> None:
    global _ROOT_OVERRIDE
    _ROOT_OVERRIDE = str(path) if path else None


def get_root() -> str:
    if _ROOT_OVERRIDE:
        return _ROOT_OVERRIDE
    return os.path.join(_repo_root(), "data", "goals")


def session_key(session_id: str) -> str:
    """会话 ID → 目录名（sha256 派生 + 正则捕获，用户串不进路径表达式）"""
    sid = str(session_id or "").strip()
    if not sid:
        raise GoalError("invalid_session", "session_id 不能为空")
    digest = hashlib.sha256(sid.encode("utf-8")).hexdigest()[:32]
    match = _KEY_RE.match(digest)
    if not match:  # pragma: no cover 纯 static 兜底
        raise GoalError("invalid_key", "会话键派生失败")
    return match.group("key")


def _dir(key: str) -> str:
    return os.path.join(get_root(), key)


def _events_path(key: str) -> str:
    return os.path.join(_dir(key), "events.jsonl")


def _state_path(key: str) -> str:
    return os.path.join(_dir(key), "state.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:  # noqa: BLE001
            pass
    os.replace(tmp, path)


# ════════════════════════════════════════════════════════════
#  投影
# ════════════════════════════════════════════════════════════

def _default_state() -> dict:
    return {
        "seq": 0,
        "goal_id": "",
        "title": "",
        "why": "",
        "acceptance": [],
        "state": "idle",
        "round": 0,
        "max_rounds": DEFAULT_MAX_ROUNDS,
        "budget_tokens": None,
        "blocks": [],
        "blocked": None,
        "evidence": "",
        "summary": "",
        "pause_reason": "",
        "last_turn_id": "",
        "created_at": "",
        "updated_at": "",
        "recent_events": [],
    }


def _apply(state: dict, event: dict) -> dict:
    """纯函数：把一条事件投影到状态（重放与增量写都走它，保证两条路径同源）"""
    etype = str(event.get("type") or "")
    p = event.get("payload") or {}
    state["seq"] = int(event.get("seq") or state.get("seq") or 0)
    state["updated_at"] = str(event.get("at") or state.get("updated_at") or "")

    if etype == "goal_created":
        state.update({
            "goal_id": str(p.get("goal_id") or uuid.uuid4().hex[:12]),
            "title": str(p.get("title") or "").strip(),
            "why": str(p.get("why") or "").strip(),
            "acceptance": [str(x).strip() for x in (p.get("acceptance") or []) if str(x).strip()],
            "state": "active",
            "round": 0,
            "max_rounds": int(p.get("max_rounds") or DEFAULT_MAX_ROUNDS),
            "budget_tokens": p.get("budget_tokens"),
            "blocked": None,
            "evidence": "",
            "summary": "",
            "pause_reason": "",
            "last_turn_id": "",
            "created_at": str(event.get("at") or ""),
        })
    elif etype == "goal_edited":
        for field in ("title", "why"):
            if p.get(field) is not None:
                state[field] = str(p[field]).strip()
        if p.get("acceptance") is not None:
            state["acceptance"] = [str(x).strip() for x in p["acceptance"] if str(x).strip()]
    elif etype == "goal_paused":
        if state["state"] == "active":
            state["state"] = "paused"
            state["pause_reason"] = str(p.get("reason") or "").strip()
    elif etype == "goal_resumed":
        if state["state"] in ("paused", "blocked") and state["goal_id"]:
            state["state"] = "active"
            state["pause_reason"] = ""
            state["blocked"] = None
    elif etype == "goal_completed":
        state["state"] = "completed"
        state["evidence"] = str(p.get("evidence") or "").strip()
        state["summary"] = str(p.get("summary") or "").strip()
    elif etype == "goal_blocked":
        state["state"] = "blocked"
        state["blocked"] = {
            "code": str(p.get("code") or "unclear"),
            "reason": str(p.get("reason") or "").strip(),
        }
    elif etype == "goal_round":
        try:
            state["round"] = int(p.get("round") or state["round"])
        except (TypeError, ValueError):
            pass
        state["last_turn_id"] = str(p.get("turn_id") or "")
    elif etype == "goal_cleared":
        keep = {"seq": state["seq"], "updated_at": state["updated_at"]}
        state.clear()
        state.update(_default_state())
        state.update(keep)
        state["state"] = "idle"
    elif etype == "goal_accept":
        item = str(p.get("item") or "").strip()
        if item and item not in state["acceptance"]:
            state["acceptance"].append(item)

    state["recent_events"] = (state.get("recent_events") or [])[-RECENT_EVENTS:]
    state["recent_events"].append({"seq": state["seq"], "at": state["updated_at"], "type": etype})
    state["recent_events"] = state["recent_events"][-RECENT_EVENTS:]
    return state


def _read_events(key: str) -> list:
    path = _events_path(key)
    if not os.path.isfile(path):
        return []
    out = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue  # 半截行（进程被杀）跳过；后续事件仍可读
                if isinstance(event, dict) and event.get("type") in EVENT_TYPES:
                    out.append(event)
    except OSError:
        return []
    return out


def _replay(key: str) -> dict:
    state = _default_state()
    for event in _read_events(key):
        state = _apply(state, event)
    return state


def _load(key: str) -> dict:
    """读投影；快照与事件流对不上（被手改/半截）时以**事件流**为准重建"""
    path = _state_path(key)
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                snap = json.load(f)
            if isinstance(snap, dict) and int(snap.get("seq") or 0) >= 0:
                events = _read_events(key)
                if events and int(snap.get("seq") or 0) == int(events[-1].get("seq") or 0):
                    state = _default_state()
                    state.update({k: snap[k] for k in state if k in snap})
                    return state
                return _replay(key)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    return _replay(key)


def _append(key: str, etype: str, payload: dict) -> dict:
    """追加一条事件并重写投影（调用方必须已持有 _LOCK）"""
    state = _load(key)
    event = {
        "seq": int(state.get("seq") or 0) + 1,
        "at": _now(),
        "type": etype,
        "payload": payload,
    }
    path = _events_path(key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:  # noqa: BLE001
            pass
    new_state = _apply(state, event)
    _atomic_write(_state_path(key), json.dumps(new_state, ensure_ascii=False, indent=1))
    return new_state


# ════════════════════════════════════════════════════════════
#  公开 API
# ════════════════════════════════════════════════════════════

def bind_session(session_id: str):
    """把当前执行上下文绑定到某会话（返回 token，供调用方 reset）"""
    return _BOUND_SESSION.set(str(session_id or ""))


def reset_session(token) -> None:
    try:
        _BOUND_SESSION.reset(token)
    except (ValueError, RuntimeError):  # 跨线程 reset 失败不影响主链路
        pass


def current_session() -> str:
    """当前工具调用所属会话：请求级绑定优先，回退编排器 TraceContext 的 subject_id"""
    sid = _BOUND_SESSION.get()
    if sid:
        return sid
    try:
        from agent.observability.trace_v2 import TraceContext  # 惰性导入（与 todo_write 同源）
        ctx = TraceContext.current()
        return str(getattr(ctx, "subject_id", "") or "").strip()
    except Exception:  # noqa: BLE001 观测上下文不可用不影响工具失败语义
        return ""


def get_state(session_id: str) -> dict:
    key = session_key(session_id)
    with _LOCK:
        state = _load(key)
        state["session_id"] = str(session_id)
        state["key"] = key
        return state


def create(session_id: str, title: str, why: str = "",
           acceptance: Optional[list] = None,
           max_rounds: int = DEFAULT_MAX_ROUNDS,
           budget_tokens: Optional[int] = None) -> dict:
    """创建目标（自动清旧目标：同一事件流里先写 goal_cleared，可回放）"""
    name = str(title or "").strip()
    if not name:
        raise GoalError("empty_title", "目标标题不能为空")
    try:
        rounds = int(max_rounds)
    except (TypeError, ValueError):
        rounds = DEFAULT_MAX_ROUNDS
    if rounds < MIN_ROUNDS or rounds > MAX_ROUNDS:
        raise GoalError("invalid_max_rounds",
                        "max_rounds 必须在 %d–%d 之间" % (MIN_ROUNDS, MAX_ROUNDS))
    key = session_key(session_id)
    with _LOCK:
        if _load(key).get("goal_id"):
            _append(key, "goal_cleared", {})
        _append(key, "goal_created", {
            "goal_id": uuid.uuid4().hex[:12],
            "title": name[:200],
            "why": str(why or "").strip()[:2000],
            "acceptance": [str(x)[:300] for x in (acceptance or [])][:20],
            "max_rounds": rounds,
            "budget_tokens": budget_tokens,
        })
    return get_state(session_id)


def edit(session_id: str, title: Optional[str] = None, why: Optional[str] = None,
         acceptance: Optional[list] = None) -> dict:
    key = session_key(session_id)
    with _LOCK:
        if not _load(key).get("goal_id"):
            raise GoalError("no_goal", "当前会话没有目标")
        _append(key, "goal_edited", {
            "title": title, "why": why, "acceptance": acceptance,
        })
    return get_state(session_id)


def pause(session_id: str, reason: str = "") -> dict:
    key = session_key(session_id)
    with _LOCK:
        state = _load(key)
        if not state.get("goal_id"):
            raise GoalError("no_goal", "当前会话没有目标")
        _append(key, "goal_paused", {"reason": str(reason or "").strip()[:500]})
    return get_state(session_id)


def resume(session_id: str) -> dict:
    key = session_key(session_id)
    with _LOCK:
        state = _load(key)
        if not state.get("goal_id"):
            raise GoalError("no_goal", "当前会话没有目标")
        _append(key, "goal_resumed", {})
    return get_state(session_id)


def complete(session_id: str, evidence: str, summary: str = "") -> dict:
    """完成目标：**必须带证据**（命令输出/文件/检查点 id），否则拒绝"""
    proof = str(evidence or "").strip()
    if not proof:
        raise GoalError("missing_evidence", "完成目标必须提供 evidence（命令输出/文件/检查点 id）")
    key = session_key(session_id)
    with _LOCK:
        state = _load(key)
        if not state.get("goal_id"):
            raise GoalError("no_goal", "当前会话没有目标")
        _append(key, "goal_completed", {
            "evidence": proof[:2000], "summary": str(summary or "").strip()[:1000],
        })
    return get_state(session_id)


def block(session_id: str, code: str, reason: str) -> dict:
    """阻塞目标：必须带稳定 code + 原因（need_user 时 driver 不再自动续跑）"""
    c = str(code or "").strip().lower()
    if c not in BLOCK_CODES:
        raise GoalError("invalid_block_code",
                        "code 必须是 %s 之一" % "/".join(BLOCK_CODES))
    why = str(reason or "").strip()
    if not why:
        raise GoalError("missing_reason", "阻塞目标必须提供 reason")
    key = session_key(session_id)
    with _LOCK:
        if not _load(key).get("goal_id"):
            raise GoalError("no_goal", "当前会话没有目标")
        _append(key, "goal_blocked", {"code": c, "reason": why[:1000]})
    return get_state(session_id)


def clear(session_id: str) -> dict:
    key = session_key(session_id)
    with _LOCK:
        if not _load(key).get("goal_id"):
            return get_state(session_id)
        _append(key, "goal_cleared", {})
    return get_state(session_id)


def accept(session_id: str, item: str) -> dict:
    text = str(item or "").strip()
    if not text:
        raise GoalError("empty_item", "验收条件不能为空")
    key = session_key(session_id)
    with _LOCK:
        if not _load(key).get("goal_id"):
            raise GoalError("no_goal", "当前会话没有目标")
        _append(key, "goal_accept", {"item": text[:300]})
    return get_state(session_id)


def record_round(session_id: str, turn_id: str = "") -> dict:
    """记一轮推进（driver 在收尾时调用）。

    - 仅 active 目标计数；``turn_id`` 相同 ⇒ 幂等（前端重发/断线重连不重复计数）；
    - 达到 ``max_rounds`` ⇒ 写 ``goal_blocked(code=budget)``，不是静默停止。
    """
    key = session_key(session_id)
    with _LOCK:
        state = _load(key)
        if not state.get("goal_id") or state.get("state") != "active":
            return get_state(session_id)
        tid = str(turn_id or "").strip()
        if tid and tid == state.get("last_turn_id"):
            return get_state(session_id)
        nxt = int(state.get("round") or 0) + 1
        _append(key, "goal_round", {"round": nxt, "turn_id": tid[:80]})
        if nxt >= int(state.get("max_rounds") or DEFAULT_MAX_ROUNDS):
            _append(key, "goal_blocked", {
                "code": "unclear",
                "reason": "达到轮次上限 %d（budget）" % int(state.get("max_rounds") or 0),
            })
    return get_state(session_id)


def delete_goal(session_id: str) -> bool:
    """删除某会话的全部目标文件（会话删除时调用；失败返回 False，不抛异常）"""
    try:
        key = session_key(session_id)
    except GoalError:
        return False
    path = _dir(key)
    if not os.path.isdir(path):
        return False
    with _LOCK:
        names = ["events.jsonl", "events.jsonl.tmp", "state.json", "state.json.tmp"]
        try:
            for name in names:
                try:
                    os.remove(os.path.join(path, name))
                except OSError:
                    pass
            try:
                os.rmdir(path)
            except OSError:
                pass
            return True
        except OSError:
            return False


def system_directive(session_id: Optional[str]) -> str:
    """目标进 system prompt 的稳定块（active 才注入；E1-2b 再加"用 goal 工具"指引）"""
    if not session_id:
        return ""
    try:
        state = get_state(session_id)
    except Exception:  # noqa: BLE001 目标状态读失败不得弄坏对话
        return ""
    if state.get("state") != "active":
        return ""
    lines = [
        "[目标] 本会话有一个持续目标：%s" % state.get("title", ""),
    ]
    if state.get("why"):
        lines.append("为什么：%s" % state["why"])
    if state.get("acceptance"):
        lines.append("验收条件：" + "；".join(
            "%d) %s" % (i + 1, a) for i, a in enumerate(state["acceptance"])))
    lines.append("当前轮次：%d/%d。请推进目标并在本轮结束时说明进展；"
                 "完成需要给出证据（命令输出/文件/检查点）；无法推进时说明阻塞原因。"
                 % (int(state.get("round") or 0), int(state.get("max_rounds") or 0)))
    return "\n".join(lines)


def next_prompt(session_id: str) -> Optional[str]:
    """round driver 的续跑提示（仅 active；由前端作为普通用户消息发出）"""
    try:
        state = get_state(session_id)
    except Exception:  # noqa: BLE001
        return None
    if state.get("state") != "active":
        return None
    return ("继续推进目标「%s」（第 %d/%d 轮）。不要重复已完成的工作；"
            "若已完成，给出完成证据；若被阻塞，说明原因。"
            % (state.get("title", ""), int(state.get("round") or 0) + 1,
               int(state.get("max_rounds") or DEFAULT_MAX_ROUNDS)))


__all__ = [
    "BLOCK_CODES", "DEFAULT_MAX_ROUNDS", "EVENT_TYPES", "GoalError", "STATES",
    "accept", "bind_session", "block", "clear", "complete", "create", "current_session",
    "delete_goal", "edit", "reset_session",
    "get_root", "get_state", "next_prompt", "pause", "record_round", "resume",
    "session_key", "set_root", "system_directive",
]
