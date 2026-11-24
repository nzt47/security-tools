"""E1-1 计划模式（plan mode）—— 会话级"只调研、先出计划"状态机。

【为什么独立存储，而不是塞进 session_manager 的 meta.json】
  `data/sessions/<id>/meta.json` 是 SessionManager 的世界（时区/设备/项目/分身），
  它按"单次 update 覆盖若干个键"工作；计划模式除了开关，还要带**版本化计划全文**、
  决策历史与回滚目标。把这些塞进 meta 会让每次写计划都重写会话元数据，也让
  "会话恢复"要多绕一层。这里用独立目录 + 原子写，读侧只读一次文件。

【路径】``data/plan_mode/<session-key>/state.json`` / ``plan.md`` / ``v<N>.md``
  - 不落 ``data/plans/``：那里是 planning 引擎（SQLite/旧 JSON 检查点）的领域
    （``planning/core.py`` 默认 persist_dir），两者语义不同，混放会互相污染。
  - 计划全文单独 ``plan.md``（路线图"计划以 Markdown 落盘、可读可 diff"）；
    ``state.json`` 只放状态机与版本索引，二者同目录、同一把锁更新。
  - ``delete_session`` 会调用 :func:`delete_plan` 清掉整个目录（随会话生命周期）。

【为什么不用 env 开关】测试要重定向目录；这里用模块级 ``set_root()``
（仿 ``agent/tools/plan_tools._TODOS_PATH_OVERRIDE``），不新增 env 读取点。

状态机::

    idle --enter--> planning --record_turn_plan--> pending_approval
    pending_approval --approve--> approved (plan_mode=False)
    pending_approval --reject--> rejected (plan_mode 保持 True)
    pending_approval --feedback--> planning (plan_mode 保持 True)
    approved --rollback(vN)--> pending_approval (plan_mode=True)
    任意 --exit--> idle (plan_mode=False；计划文件保留)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from typing import Any, Optional

#: 计划目录（``set_root`` 覆盖，供单测重定向）
_ROOT_OVERRIDE: Optional[str] = None

#: 并发保护：同一进程内多会话/多线程读写 state.json 与 plan.md 必须是一个整体动作
_LOCK = threading.RLock()

#: 历史版本最多保留个数（超出删除最老的 v*.md，避免无限增长）
MAX_VERSIONS = 20

#: 派生键的形态（全 hex；与 spill.py 的 locator 同一写法：正则捕获后再进路径表达式）
_KEY_RE = re.compile(r"^(?P<key>[0-9a-f]{32})$")

#: 合法状态
STATUSES = ("idle", "planning", "pending_approval", "approved", "rejected")

#: 计划模式下的系统提示（稳定块：跨轮不变 ⇒ 不进易变尾簇）
PLAN_DIRECTIVE = (
    "[计划模式] 当前会话处于计划模式。本轮你只能做只读调研（读文件/搜索/查看状态），"
    "禁止修改文件、执行有副作用的命令或调用会改变外部状态的工具。"
    "完成调研后，请直接输出一份可执行的 Markdown 计划：目标、分步做法、涉及的文件/模块、"
    "验证方式、风险与回滚。你这条回复会被保存为该会话的计划草稿并交给用户批准；"
    "用户批准之前不要执行计划。"
)

#: 计划为空时的占位（Markdown 文件始终存在，便于 UI 与人类查看）
_EMPTY_PLAN = "# （尚无计划）\n\n本会话处于计划模式，助手的第一条回复会成为计划草稿。\n"


class PlanModeError(ValueError):
    """状态机拒绝的操作（routes 层据此返回 4xx，而不是 500）"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ════════════════════════════════════════════════════════════
#  路径与基础工具
# ════════════════════════════════════════════════════════════

def _repo_root() -> str:
    # agent/context/plan_mode.py → 上溯三级 = 仓库根
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def set_root(path: Optional[str]) -> None:
    """重定向计划目录（``None`` 恢复默认；仅供测试/探针）"""
    global _ROOT_OVERRIDE
    _ROOT_OVERRIDE = str(path) if path else None


def get_root() -> str:
    if _ROOT_OVERRIDE:
        return _ROOT_OVERRIDE
    return os.path.join(_repo_root(), "data", "plan_mode")


def session_key(session_id: str) -> str:
    """会话 ID → 目录名（**sha256 派生，用户串不进路径表达式**）

    【为什么全部哈希、且用 sha256】CodeQL ``py/path-injection`` 认的是"用户串有没有
    进路径表达式"，白名单正则不算消毒（口径 §4 三铁律之一：常量键→常量值 / 列目录
    等值 / sha256）。这里统一 sha256 前 32 位：一一对应、不含路径分隔符，也顺带
    避免 ``py/weak-sensitive-data-hashing``（sha1 会被判弱哈希）。
    """
    sid = str(session_id or "").strip()
    if not sid:
        raise PlanModeError("invalid_session", "session_id 不能为空")
    digest = hashlib.sha256(sid.encode("utf-8")).hexdigest()[:32]
    match = _KEY_RE.match(digest)
    if not match:  # pragma: no cover 纯 static 兜底：sha256 hexdigest 必然满足
        raise PlanModeError("invalid_key", "会话键派生失败")
    return match.group("key")


def _dir(key: str) -> str:
    """目录 = 根 + **已派生的键**（不要在这里再调 session_key，否则会二次哈希）"""
    return os.path.join(get_root(), key)


def _state_path(key: str) -> str:
    return os.path.join(_dir(key), "state.json")


def _plan_path(key: str) -> str:
    return os.path.join(_dir(key), "plan.md")


def _version_path(key: str, version: int) -> str:
    return os.path.join(_dir(key), "v%d.md" % int(version))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write(path: str, text: str) -> None:
    """原子写：先写 ``.tmp`` 再 ``os.replace``（Windows 同分区亦原子）

    【为什么】进程在写到一半被杀时，读者只会看到"旧的完整文件"或"新的完整文件"，
    不会读到半截 JSON（那会让整个会话的计划不可读）。
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:  # noqa: BLE001 fsync 失败不影响 replace 的原子性
            pass
    os.replace(tmp, path)


def _default_state() -> dict:
    return {
        "plan_mode": False,
        "status": "idle",
        "version": 0,
        "created_at": "",
        "updated_at": "",
        "revisions": [],
        "feedback": [],
    }


def _load_raw(key: str) -> dict:
    """读 state.json；不存在/损坏一律按默认态（绝不抛异常给调用方）"""
    path = _state_path(key)
    if not os.path.isfile(path):
        return _default_state()
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, json.JSONDecodeError):
        return _default_state()
    if not isinstance(doc, dict):
        return _default_state()
    state = _default_state()
    state.update({k: doc[k] for k in state if k in doc})
    if state["status"] not in STATUSES:
        state["status"] = "idle"
    try:
        state["version"] = int(state.get("version") or 0)
    except (TypeError, ValueError):
        state["version"] = 0
    if not isinstance(state["revisions"], list):
        state["revisions"] = []
    if not isinstance(state["feedback"], list):
        state["feedback"] = []
    state["plan_mode"] = bool(state.get("plan_mode"))
    return state


def _save_raw(key: str, state: dict) -> None:
    state["updated_at"] = _now()
    _atomic_write(_state_path(key), json.dumps(state, ensure_ascii=False, indent=2))


def _read_plan(key: str) -> str:
    path = _plan_path(key)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _trim_versions(key: str, state: dict) -> None:
    """只保留最近 ``MAX_VERSIONS`` 个版本文件（版本号只增不减）"""
    revisions = [r for r in state.get("revisions", []) if isinstance(r, dict)]
    revisions.sort(key=lambda r: int(r.get("version") or 0))
    for old in revisions[:-MAX_VERSIONS]:
        try:
            os.remove(_version_path(key, int(old.get("version") or 0)))
        except OSError:
            pass
    state["revisions"] = revisions[-MAX_VERSIONS:]


# ════════════════════════════════════════════════════════════
#  公开 API
# ════════════════════════════════════════════════════════════

def get_state(session_id: str) -> dict:
    """读状态（含最新计划全文与版本列表）；会话从未用过计划模式时返回 idle 默认态"""
    key = session_key(session_id)
    with _LOCK:
        state = _load_raw(key)
        state["session_id"] = str(session_id)
        state["plan"] = _read_plan(key)
        state["key"] = key
        return state


def enter(session_id: str) -> dict:
    """进入计划模式（幂等）；已批准的计划会保留，新回复开新版本"""
    key = session_key(session_id)
    with _LOCK:
        state = _load_raw(key)
        if not state["plan_mode"]:
            state["plan_mode"] = True
            if state["status"] in ("idle", "approved", "rejected"):
                state["status"] = "planning"
            if not state.get("created_at"):
                state["created_at"] = _now()
            if not _read_plan(key):
                _atomic_write(_plan_path(key), _EMPTY_PLAN)
        _save_raw(key, state)
    return get_state(session_id)


def exit(session_id: str) -> dict:
    """退出计划模式（保留计划文件与历史；待批计划不自动批准）"""
    key = session_key(session_id)
    with _LOCK:
        state = _load_raw(key)
        state["plan_mode"] = False
        if state["status"] != "approved":
            state["status"] = "idle"
        _save_raw(key, state)
    return get_state(session_id)


def record_turn_plan(session_id: str, text: str) -> Optional[dict]:
    """把本轮助手回复记为一个计划版本（仅在计划模式激活且正文非空时）

    Returns:
        记录成功返回最新状态；未激活计划模式/正文为空返回 None（**不改任何状态**）。
    """
    body = str(text or "").strip()
    if not body:
        return None
    key = session_key(session_id)
    with _LOCK:
        state = _load_raw(key)
        if not state["plan_mode"]:
            return None
        version = int(state.get("version") or 0) + 1
        _atomic_write(_version_path(key, version), body + "\n")
        _atomic_write(_plan_path(key), body + "\n")
        state["version"] = version
        state["status"] = "pending_approval"
        state["revisions"].append({
            "version": version,
            "at": _now(),
            "status": "pending_approval",
            "chars": len(body),
        })
        _trim_versions(key, state)
        _save_raw(key, state)
    return get_state(session_id)


def decide(session_id: str, action: str,
           feedback: Optional[str] = None,
           version: Optional[int] = None) -> dict:
    """处理用户对当前计划的决策。

    Args:
        action: ``approve`` / ``reject`` / ``feedback`` / ``rollback``
        feedback: reject/feedback 的意见文本（可空）
        version: rollback 的目标版本号

    Raises:
        PlanModeError: 状态不允许（routes 映射为 409/400）
    """
    key = session_key(session_id)
    act = str(action or "").strip().lower()
    note = str(feedback or "").strip()
    with _LOCK:
        state = _load_raw(key)
        if act == "approve":
            if state["status"] != "pending_approval":
                raise PlanModeError("no_pending_plan", "当前没有待批准的计划")
            state["status"] = "approved"
            state["plan_mode"] = False
            _mark_latest(state, "approved")
        elif act == "reject":
            if state["status"] != "pending_approval":
                raise PlanModeError("no_pending_plan", "当前没有待拒绝的计划")
            state["status"] = "planning"
            state["plan_mode"] = True
            _mark_latest(state, "rejected")
            if note:
                state["feedback"].append({"at": _now(), "text": note, "kind": "reject"})
        elif act == "feedback":
            if not state["plan_mode"] and state["status"] != "pending_approval":
                raise PlanModeError("not_in_plan_mode", "计划模式未开启，无法提交反馈")
            if not note:
                raise PlanModeError("empty_feedback", "反馈内容不能为空")
            state["status"] = "planning"
            state["plan_mode"] = True
            _mark_latest(state, "changes_requested")
            state["feedback"].append({"at": _now(), "text": note, "kind": "feedback"})
        elif act == "rollback":
            try:
                target = int(version or 0)
            except (TypeError, ValueError):
                target = 0
            if target <= 0 or not os.path.isfile(_version_path(key, target)):
                raise PlanModeError("unknown_version", "目标版本不存在")
            with open(_version_path(key, target), "r", encoding="utf-8") as f:
                content = f.read().strip()
            if not content:
                raise PlanModeError("empty_version", "目标版本内容为空")
            new_version = int(state.get("version") or 0) + 1
            _atomic_write(_version_path(key, new_version), content + "\n")
            _atomic_write(_plan_path(key), content + "\n")
            state["version"] = new_version
            state["status"] = "pending_approval"
            state["plan_mode"] = True
            state["revisions"].append({
                "version": new_version,
                "at": _now(),
                "status": "pending_approval",
                "chars": len(content),
                "rollback_of": target,
            })
            _trim_versions(key, state)
        else:
            raise PlanModeError("unknown_action", "未知动作：%s" % act)
        _save_raw(key, state)
    return get_state(session_id)


def _mark_latest(state: dict, status: str) -> None:
    revisions = [r for r in state.get("revisions", []) if isinstance(r, dict)]
    if revisions:
        revisions[-1]["status"] = status


def delete_plan(session_id: str) -> bool:
    """删除某会话的全部计划文件（会话删除时调用；失败返回 False，不抛异常）"""
    try:
        key = session_key(session_id)
    except PlanModeError:
        return False
    path = os.path.join(get_root(), key)
    if not os.path.isdir(path):
        return False
    with _LOCK:
        # 【不易】不用 os.listdir 拼路径（CodeQL 会把"目录项 → 路径"判成注入）：
        # 只删**我们自己按常量命名**的文件，版本号由 state 里的 int 决定。
        state = _load_raw(key)
        names = ["state.json", "state.json.tmp", "plan.md", "plan.md.tmp"]
        for version in range(1, int(state.get("version") or 0) + 1):
            names.append("v%d.md" % version)
            names.append("v%d.md.tmp" % version)
        try:
            for name in names:
                try:
                    os.remove(os.path.join(path, name))
                except OSError:
                    pass  # 单个文件不存在不影响清理其余文件
            try:
                os.rmdir(path)
            except OSError:
                pass  # 目录里还有未知文件时保留目录；我们自己写的已删
            return True
        except OSError:
            return False


def system_directive(session_id: Optional[str]) -> str:
    """计划模式系统提示片段（未激活/无会话返回空串；读失败按未激活处理）"""
    if not session_id:
        return ""
    try:
        state = get_state(session_id)
    except Exception:  # noqa: BLE001 计划状态读失败不得弄坏对话
        return ""
    if not state.get("plan_mode"):
        return ""
    return PLAN_DIRECTIVE


__all__ = [
    "PLAN_DIRECTIVE", "PlanModeError", "STATUSES", "decide", "delete_plan",
    "enter", "exit", "get_root", "get_state", "record_turn_plan",
    "session_key", "set_root", "system_directive",
]
