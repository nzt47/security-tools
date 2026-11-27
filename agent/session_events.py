"""P5 / E5-1 Phase B+C：会话事件**双写**与**读灰度（shadow）**

【不易·不变量】全部来自 docs/plan/2026-11-27-p5-会话事件溯源迁移方案.md：

1. **append-only**：本模块只追加事件行，**从不**改 `messages.jsonl`（一个字节都不碰）；
2. **先消息后事件**：`SessionManager.add_message` 先写 messages.jsonl，再调本模块；
   事件写失败**只记指标 + 日志**，绝不回滚消息、绝不抛回调用方（诊断 §6）；
3. **幂等键与 Phase A 投影同源**：`evt_ + sha256(sid|ts|role|content)[:16]` ——
   投影器与本模块用同一个函数，重放/双写不会产生两条 id 不同的事件；
4. **默认关**：`CP_SESSION_EVENTS_WRITE=0`、`CP_SESSION_EVENTS_READ=off` 都是默认值，
   关掉即回到「只有 messages.jsonl」的今天行为（回滚 = 两个开关）；
5. **冻结池不碰**：本模块的入口只有 `add_message`（天然带 session_id），
   不读也不写 `data/messages.jsonl`。

【变易·seq 口径】会话内单调递增；首次写某会话时**扫已有事件文件**取 max(seq)，
之后按文件 mtime 校验缓存（投影器原子替换文件后缓存自动失效，不会写出重复 seq）。

【简易·读灰度只做 shadow】`CP_SESSION_EVENTS_READ=shadow` 时读 events **只为对拍**：
逐条比较 payload 与 messages 行，差异只记日志/计数，**返回值仍是 messages**。
`events`（以事件为准）需要「按会话白名单 + 连续 30 天零差异」才放行（方案 §3 D 阶段），
本期**不实现**；非法取值一律回落 `off`（不静默当成更激进的档）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[1]
#: 事件目录（与 data/sessions 同盘同权限；运行期状态，不进 git）
EVENTS_DIR = _ROOT / "data" / "session_events"
#: 双写开关（默认 0 = 关）
WRITE_ENV = "CP_SESSION_EVENTS_WRITE"
#: 读灰度开关（off | shadow；默认 off）
READ_ENV = "CP_SESSION_EVENTS_READ"
READ_MODES: Tuple[str, ...] = ("off", "shadow")

#: 会话 id 允许的字符（防 path traversal；不合法的 sid 直接拒绝写事件）
_SID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

#: 事件模型版本
EVENT_VERSION = 1

_STATE_LOCK = threading.Lock()
#: sid -> (max_seq, file_mtime_ns)
_SEQ_STATE: Dict[str, Tuple[int, int]] = {}
_APPEND_LOCK = threading.Lock()
_WRITE_FAILURES = {"count": 0}
_SHADOW_DIFFS = {"count": 0}


def _truthy(raw: str) -> bool:
    return str(raw or "").strip().lower() in ("1", "true", "yes", "on")


def write_enabled() -> bool:
    """双写开关（唯一判据）"""
    return _truthy(os.environ.get(WRITE_ENV, ""))


def read_mode() -> str:
    """读灰度档（off|shadow；非法值强制回落 off 并告警一次）"""
    raw = str(os.environ.get(READ_ENV, "") or "").strip().lower()
    if raw in READ_MODES:
        return raw
    if raw:
        logger.warning("[session_events] %s=%r 不是合法档位 %s ⇒ 按 off 处理",
                       READ_ENV, raw, list(READ_MODES))
    return "off"


def event_id(sid: str, ts: str, role: str, content: str) -> str:
    """幂等键（与 scripts/session_events_project.py **同一个算法**，不要各写一份）"""
    raw = "\x1f".join([str(sid), str(ts), str(role), str(content)])
    return "evt_" + hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]


_ROLES = {"user", "assistant", "system", "tool"}


def event_type(role: str) -> str:
    """角色 → 事件类型（message.user / assistant / system / tool / other）"""
    role = str(role or "").strip().lower()
    return "message." + (role if role in _ROLES else "other")


def events_path(sid: str, root: Optional[Path] = None) -> Optional[Path]:
    """会话事件文件路径；sid 不合法（路径字符/空）⇒ None（**不**抛，也不写到别处）"""
    sid = str(sid or "")
    if not _SID_RE.match(sid):
        return None
    return (root or EVENTS_DIR) / (sid + ".jsonl")


def _max_seq_on_disk(path: Path) -> int:
    """扫事件文件取 max(seq)（只读；坏行忽略）"""
    try:
        if not path.exists():
            return 0
        top = 0
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    try:
                        top = max(top, int(rec.get("seq") or 0))
                    except (TypeError, ValueError):
                        continue
        return top
    except OSError:
        return 0


def _next_seq(path: Path, sid: str) -> int:
    """会话内下一个 seq（按文件 mtime 失效缓存；投影器原子替换后自动重算）"""
    try:
        mtime = path.stat().st_mtime_ns if path.exists() else 0
    except OSError:
        mtime = 0
    with _STATE_LOCK:
        cached = _SEQ_STATE.get(sid)
        if cached is not None and cached[1] == mtime:
            value = cached[0] + 1
            _SEQ_STATE[sid] = (value, mtime)
            return value
        value = _max_seq_on_disk(path) + 1
        _SEQ_STATE[sid] = (value, mtime)
        return value


def append_live_event(session_id: str, record: Dict[str, Any],
                      root: Optional[Path] = None) -> bool:
    """把一条**刚落盘**的消息追加为事件（src=live）

    Args:
        session_id: 会话 id（非法 ⇒ 返回 False，不写）
        record: 与 messages.jsonl 里那一行**同一个 dict**（payload 原样保存）
        root: 事件根目录（测试注入；缺省 data/session_events）

    Returns:
        True = 已追加；False = 开关关 / sid 非法 / 写失败（失败已记指标，不抛）
    """
    if not write_enabled():
        return False
    sid = str(session_id or "")
    path = events_path(sid, root)
    if path is None:
        logger.warning("[session_events] 会话 id 不合法，跳过双写（长度=%d）", len(sid))
        return False
    if not isinstance(record, dict):
        return False
    ts = str(record.get("timestamp") or "").strip()
    role = str(record.get("role") or "").strip().lower()
    if not ts:
        # 没有时间戳就无法生成幂等键（与投影器同一判据：缺 timestamp 进 quarantine）
        logger.warning("[session_events] 消息缺 timestamp，跳过双写（sid=%s）", _mask(sid))
        return False
    try:
        with _APPEND_LOCK:
            seq = _next_seq(path, sid)
            event = {
                "v": EVENT_VERSION,
                "type": event_type(role),
                "sid": sid,
                "seq": seq,
                "id": event_id(sid, ts, role, str(record.get("content") or "")),
                "ts": ts,
                "payload": record,
                "src": "live",
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return True
    except Exception as exc:  # noqa: BLE001 事件写失败**不得**影响消息落盘（方案 §6）
        with _STATE_LOCK:
            _WRITE_FAILURES["count"] += 1
        # 【不记正文】只记会话 id 摘要与异常类型（口径 §4.2：凭据/正文不进日志）
        logger.warning("[session_events] session_events_write_failed sid=%s err=%s",
                       _mask(sid), type(exc).__name__)
        return False


def clear_events(session_id: str, root: Optional[Path] = None) -> bool:
    """清空某会话的事件文件（**只由"用户显式清空会话"调用**）

    【为什么不违反 append-only】append-only 保护的是"对话历史不被后台改写"；
    清空会话是用户显式动作，语义就是"这段对话不存在了"。若只清 messages 而留下事件，
    Phase C 的 events 读会把已删消息"复活"，shadow 也会永远对不上。
    """
    sid = str(session_id or "")
    path = events_path(sid, root)
    if path is None:
        return False
    try:
        with _APPEND_LOCK:
            if path.exists():
                path.write_text("", encoding="utf-8")
            with _STATE_LOCK:
                _SEQ_STATE.pop(sid, None)
        return True
    except OSError as exc:
        logger.warning("[session_events] 清空事件失败 sid=%s err=%s",
                       _mask(sid), type(exc).__name__)
        return False


def read_events(session_id: str, root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """读某会话的事件（坏行忽略；不做任何写）"""
    path = events_path(str(session_id or ""), root)
    if path is None or not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    except OSError:
        return []
    return out


def shadow_compare(session_id: str, messages: List[Dict[str, Any]],
                   root: Optional[Path] = None) -> Dict[str, Any]:
    """读灰度 shadow 的对拍（**只读**；返回差异计数，不改变任何读结果）

    Returns:
        {"available": bool, "messages": n, "events": n, "mismatched": k,
         "first_mismatch": {"index": i, "reason": "..."} | None}
    """
    events = read_events(session_id, root)
    out: Dict[str, Any] = {"available": bool(events), "messages": len(messages),
                           "events": len(events), "mismatched": 0,
                           "first_mismatch": None}
    if not events:
        return out
    if len(events) != len(messages):
        out["mismatched"] = abs(len(events) - len(messages))
        out["first_mismatch"] = {"index": min(len(events), len(messages)),
                                 "reason": "count"}
        with _STATE_LOCK:
            _SHADOW_DIFFS["count"] += 1
        return out
    for index, (event, message) in enumerate(zip(events, messages)):
        payload = event.get("payload")
        if not isinstance(payload, dict):
            out["mismatched"] += 1
            out["first_mismatch"] = out["first_mismatch"] or {
                "index": index, "reason": "payload_not_object"}
            continue
        # 只比**语义三要素**（role/content/timestamp）：其余字段是工具细节，
        # 老版本 messages.jsonl 里本来就可能缺（投影是原样搬运，不是补全）
        for key in ("role", "content", "timestamp"):
            if str(payload.get(key) or "") != str(message.get(key) or ""):
                out["mismatched"] += 1
                if out["first_mismatch"] is None:
                    out["first_mismatch"] = {"index": index, "reason": key}
                break
    if out["mismatched"]:
        with _STATE_LOCK:
            _SHADOW_DIFFS["count"] += 1
        logger.warning("[session_events] shadow 差异：sid=%s messages=%d events=%d "
                       "mismatched=%d first=%s",
                       _mask(session_id), out["messages"], out["events"],
                       out["mismatched"], out["first_mismatch"])
    return out


def _mask(sid: str) -> str:
    """会话 id 的**非凭据**摘要（日志里不出现原 id 全文；sha256 前 8 位）"""
    return hashlib.sha256(str(sid or "").encode("utf-8", "replace")).hexdigest()[:8]


def write_failure_count() -> int:
    with _STATE_LOCK:
        return int(_WRITE_FAILURES["count"])


def shadow_diff_count() -> int:
    with _STATE_LOCK:
        return int(_SHADOW_DIFFS["count"])


def reset_for_tests() -> None:
    """清进程内缓存与计数（**测试专用**）"""
    with _STATE_LOCK:
        _SEQ_STATE.clear()
        _WRITE_FAILURES["count"] = 0
        _SHADOW_DIFFS["count"] = 0


__all__ = [
    "EVENTS_DIR", "EVENT_VERSION", "READ_ENV", "READ_MODES", "WRITE_ENV",
    "append_live_event", "clear_events", "event_id", "event_type", "events_path",
    "read_events",
    "read_mode", "reset_for_tests", "shadow_compare", "shadow_diff_count",
    "write_enabled", "write_failure_count",
]
