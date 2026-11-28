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

【简易·读灰度分两档】`CP_SESSION_EVENTS_READ`：
  · `off`（默认）—— 只读 messages.jsonl；
  · `shadow` —— 读 events **只为对拍**：差异只记日志/计数与「每日零差异报告」，返回值仍是 messages；
  · `events` —— **以事件为准**，但只对 `CP_SESSION_EVENTS_READ_SESSIONS` 白名单内的会话生效
    （名单外回落 off；事件为空而 messages 非空时回落 messages 并计数 —— 宁可退化也不丢对话）。
    放行条件仍是「白名单 + 连续 30 天 shadow 零差异」，报告见 `shadow_daily()` 与
    `scripts/audit/session_events_shadow_report.py`。
  非法取值一律回落 `off`（不静默当成更激进的档）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[1]
#: 事件目录（与 data/sessions 同盘同权限；运行期状态，不进 git）
EVENTS_DIR = _ROOT / "data" / "session_events"
#: 双写开关（默认 0 = 关）
WRITE_ENV = "CP_SESSION_EVENTS_WRITE"
#: 读灰度开关（off | shadow | events；默认 off）
READ_ENV = "CP_SESSION_EVENTS_READ"
#: events 档的**会话白名单**（逗号分隔；空 = 谁都不读 events）
READ_SESSIONS_ENV = "CP_SESSION_EVENTS_READ_SESSIONS"
READ_MODES: Tuple[str, ...] = ("off", "shadow", "events")
#: 白名单最多认多少个会话（越界只取前 N 个，避免巨大环境变量拖慢每次读）
MAX_READ_WHITELIST = 200
#: 「每日零差异报告」文件名与保留天数（30 天窗口的判据载体，多留一倍便于回看）
SHADOW_DAILY_FILE = "_shadow_daily.json"
SHADOW_KEEP_DAYS = 60

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
#: events 档被白名单拒绝的次数（配置了 events 但会话不在名单 → 回落 off）
_READ_DENIED = {"count": 0}
#: events 档读到空事件但 messages 非空 → 回落 messages 的次数（宁可退化也不丢对话）
_READ_FALLBACKS = {"count": 0}
_DAILY_LOCK = threading.Lock()


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


def read_whitelist() -> Tuple[str, ...]:
    """events 档的会话白名单（逗号分隔；非法 id 丢弃、去重、最多 MAX_READ_WHITELIST 个）

    【为什么白名单要校验字符】名单来自环境变量，若允许 `../` 之类就会变成"任选文件读"。
    这里与 `events_path` 用同一个 `_SID_RE`：名单里的 id 必须是**能对应事件文件**的合法会话 id。
    """
    raw = str(os.environ.get(READ_SESSIONS_ENV, "") or "")
    out: List[str] = []
    for part in raw.split(","):
        sid = part.strip()
        if not sid or sid in out or not _SID_RE.match(sid):
            continue
        out.append(sid)
        if len(out) >= MAX_READ_WHITELIST:
            break
    return tuple(out)


def read_mode_for(session_id: str) -> str:
    """本会话**实际生效**的读档：events 只对白名单内会话生效，名单外回落 off。"""
    mode = read_mode()
    if mode != "events":
        return mode
    if str(session_id or "") in read_whitelist():
        return "events"
    with _STATE_LOCK:
        _READ_DENIED["count"] += 1
    return "off"


def messages_from_events(session_id: str, root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """把事件还原成消息列表（**只读**）：取每条事件的 payload，按 seq 升序。

    【为什么按 seq 排】文件是 append-only，正常就是升序；但投影器原子替换/人工修复后
    可能出现乱序行，按 seq 排一次比信任文件顺序便宜且更稳。
    """
    rows: List[Tuple[int, Dict[str, Any]]] = []
    for rec in read_events(session_id, root):
        payload = rec.get("payload")
        if not isinstance(payload, dict):
            continue
        try:
            seq = int(rec.get("seq") or 0)
        except (TypeError, ValueError):
            seq = 0
        rows.append((seq, payload))
    rows.sort(key=lambda item: item[0])
    return [payload for _seq, payload in rows]


def note_read_fallback(session_id: str) -> None:
    """events 档读到空事件、回落 messages 时记一次（计数 + 脱敏日志）。"""
    with _STATE_LOCK:
        _READ_FALLBACKS["count"] += 1
    logger.warning("[session_events] events 档事件为空，回落 messages（sid=%s）", _mask(session_id))


def _today() -> str:
    """报告用的日期键（UTC；与事件 ts 同为 UTC，避免本地日期跨时区错位）"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _daily_path(root: Optional[Path] = None) -> Path:
    return (root or EVENTS_DIR) / SHADOW_DAILY_FILE


def _record_shadow_daily(session_id: str, compared: int, diff: int,
                         root: Optional[Path] = None) -> None:
    """把一次 shadow 对拍结果累进「每日零差异报告」（best-effort：失败只记日志）。

    结构（只存会话 id 的 sha256 前 16 位，不存正文/原 id）：

        {"version": 1, "days": {"2026-11-28": {"compared": {"<digest>": n},
                                                 "diff": {"<digest>": n}}}}
    """
    if compared <= 0:
        return
    day = _today()
    digest = hashlib.sha256(str(session_id or "").encode("utf-8", "replace")).hexdigest()[:16]
    path = _daily_path(root)
    try:
        with _DAILY_LOCK:
            payload: Dict[str, Any] = {"version": 1, "days": {}}
            if path.exists():
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    payload = {"version": 1, "days": {}}
            days = payload.get("days")
            if not isinstance(days, dict):
                days = {}
            bucket = days.get(day)
            if not isinstance(bucket, dict):
                bucket = {"compared": {}, "diff": {}}
            for key, value in (("compared", 1), ("diff", 1 if diff else 0)):
                table = bucket.get(key)
                if not isinstance(table, dict):
                    table = {}
                if value:
                    try:
                        table[digest] = int(table.get(digest) or 0) + value
                    except (TypeError, ValueError):
                        table[digest] = value
                bucket[key] = table
            days[day] = bucket
            # 只留最近 SHADOW_KEEP_DAYS 天（按日期字符串排序即可，ISO 日期天然可比）
            for stale in sorted(days)[:-SHADOW_KEEP_DAYS]:
                days.pop(stale, None)
            payload["days"] = days
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001 报告是旁路：写失败不得影响读链路
        logger.debug("[session_events] 每日报告写入失败 err=%s", type(exc).__name__)


def shadow_daily(days: int = 30, root: Optional[Path] = None) -> Dict[str, Any]:
    """读「每日零差异报告」→ 30 天放行判据（**只读**，不创建文件）

    Returns:
        {"days": [{date, compared, diff, clean}], "total_days": n,
         "diff_days": n, "consecutive_clean_days": n, "sessions": {digest: {...}}}
    """
    try:
        window = max(1, min(int(days or 30), SHADOW_KEEP_DAYS))
    except (TypeError, ValueError):
        window = 30
    payload: Dict[str, Any] = {}
    path = _daily_path(root)
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            payload = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            payload = {}
    raw_days = payload.get("days") if isinstance(payload.get("days"), dict) else {}
    rows: List[Dict[str, Any]] = []
    sessions: Dict[str, Dict[str, int]] = {}
    for day in sorted(raw_days)[-window:]:
        bucket = raw_days.get(day) if isinstance(raw_days.get(day), dict) else {}
        compared_table = bucket.get("compared") if isinstance(bucket.get("compared"), dict) else {}
        diff_table = bucket.get("diff") if isinstance(bucket.get("diff"), dict) else {}
        compared = sum(int(v or 0) for v in compared_table.values())
        diff = sum(int(v or 0) for v in diff_table.values())
        rows.append({"date": day, "compared": compared, "diff": diff, "clean": diff == 0})
        for digest in set(compared_table) | set(diff_table):
            row = sessions.setdefault(digest, {"compared": 0, "diff": 0})
            row["compared"] += int(compared_table.get(digest) or 0)
            row["diff"] += int(diff_table.get(digest) or 0)
    consecutive = 0
    for row in reversed(rows):
        if row["compared"] > 0 and row["diff"] == 0:
            consecutive += 1
        else:
            break
    return {
        "days": rows,
        "total_days": len(rows),
        "diff_days": sum(1 for row in rows if row["diff"]),
        "consecutive_clean_days": consecutive,
        "sessions": sessions,
        "window": window,
    }


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
        _record_shadow_daily(session_id, compared=len(messages), diff=out["mismatched"],
                             root=root)
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
    _record_shadow_daily(session_id, compared=len(messages),
                         diff=out["mismatched"], root=root)
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


def events_read_denied_count() -> int:
    """配置了 events 档但会话不在白名单 ⇒ 回落 off 的次数"""
    with _STATE_LOCK:
        return int(_READ_DENIED["count"])


def events_read_fallback_count() -> int:
    """events 档事件为空、回落 messages 的次数"""
    with _STATE_LOCK:
        return int(_READ_FALLBACKS["count"])


def reset_for_tests() -> None:
    """清进程内缓存与计数（**测试专用**）"""
    with _STATE_LOCK:
        _SEQ_STATE.clear()
        _WRITE_FAILURES["count"] = 0
        _SHADOW_DIFFS["count"] = 0
        _READ_DENIED["count"] = 0
        _READ_FALLBACKS["count"] = 0


__all__ = [
    "EVENTS_DIR", "EVENT_VERSION", "MAX_READ_WHITELIST", "READ_ENV", "READ_MODES",
    "READ_SESSIONS_ENV", "SHADOW_DAILY_FILE", "SHADOW_KEEP_DAYS", "WRITE_ENV",
    "append_live_event", "clear_events", "event_id", "event_type", "events_path",
    "events_read_denied_count", "events_read_fallback_count",
    "messages_from_events", "note_read_fallback",
    "read_events", "read_mode", "read_mode_for", "read_whitelist", "reset_for_tests",
    "shadow_compare", "shadow_daily", "shadow_diff_count",
    "write_enabled", "write_failure_count",
]
