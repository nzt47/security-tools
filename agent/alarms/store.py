# -*- coding: utf-8 -*-
"""报警器事件库（2026-11-11）：把「运行期发现的问题」变成可读/可确认/可静默的台账。

【为什么不是第四个收件箱】
三个面语义不同，不能混：
  · 审批收件箱 = **要动作**（批/驳，单次消费 + 落审计）—— 报警器**不碰**它；
  · 消息中心   = **要知悉**（未读/已读、可分页查历史）；
  · 报警器     = **要知悉的源头**（事件 + 当前态 + 恢复），它把事件写进**同一张表**，
    由消息中心列表与顶栏铃铛共同消费。
所以本模块只提供"事件→当前态"的唯一事实源；顶栏铃铛与消息中心都是它的投影，
不存在两套未读。

【不易】
- 一个 key（如 `health.l3_llm_tool`）**只有一条当前态**：重复发现 = `count+1`、刷新
  `last_seen`，不刷屏（去重语义）。
- 条件消失 ⇒ `resolved`（写 `resolved_at`），不删除 —— 恢复本身也要被汇报；
  再次出现 ⇒ 重新 `firing`（`count` 继续累计 = 第几次发生）。
- **无数据不报警**（L5）：调用方只在"确实测到降级"时才 raise；拿不到数据时不 raise、
  也不替人把旧告警 resolve 掉（那等于宣布"恢复了"）。
- 原始 IP / 凭据一律不进 detail（沿用裁定 B：只掩码 + 指纹；本模块不采集这类字段）。
- 落盘原子写（tmp + replace）；events.jsonl 只追加，供追溯"谁在什么时候被告知"。

【变易】新事件源只需在 collector 里产出一个 candidate dict（见 `collector.collect_once`）。
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

__version__ = "1.0.0"

#: 事件库目录（运行期状态，gitignored）
DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "alarms"
STATE_FILE = DATA_DIR / "state.json"
EVENTS_FILE = DATA_DIR / "events.jsonl"

STATE_FIRING = "firing"
STATE_RESOLVED = "resolved"
STATES = (STATE_FIRING, STATE_RESOLVED)

SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"
SEVERITIES = (SEVERITY_INFO, SEVERITY_WARNING, SEVERITY_CRITICAL)
_SEVERITY_RANK = {SEVERITY_CRITICAL: 3, SEVERITY_WARNING: 2, SEVERITY_INFO: 1}

#: 台账上限（超出时先丢最旧的已恢复事件；firing 永远保留）
MAX_ALARMS = 500

#: 单次静默的默认时长（分钟）
DEFAULT_SILENCE_MINUTES = 60
#: 静默上限（防止"永久静默"把报警器变成摆设）
MAX_SILENCE_MINUTES = 24 * 60

_VERSION = 1


def _now(now: Optional[datetime] = None) -> datetime:
    return now if now is not None else datetime.now().astimezone()


def _iso(ts: datetime) -> str:
    return ts.isoformat(timespec="seconds")


def _parse(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


@dataclass
class Alarm:
    """一条报警（**当前态**；历史在 events.jsonl 里）"""
    id: int
    key: str
    kind: str
    severity: str
    title: str
    detail: str = ""
    subject: str = ""
    source: str = ""
    state: str = STATE_FIRING
    first_seen: str = ""
    last_seen: str = ""
    resolved_at: str = ""
    count: int = 1
    read: bool = False
    acked: bool = False
    acked_by: str = ""
    acked_at: str = ""
    silenced_until: str = ""

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)

    @staticmethod
    def from_dict(raw: Dict[str, object]) -> "Alarm":
        known = {f for f in Alarm.__dataclass_fields__}  # type: ignore[attr-defined]
        return Alarm(**{k: v for k, v in raw.items() if k in known})  # type: ignore[arg-type]

    def silenced(self, now: Optional[datetime] = None) -> bool:
        until = _parse(self.silenced_until)
        return until is not None and until > _now(now)


class AlarmStore:
    """报警台账（进程内单例用法 + 可注入路径用于测试）"""

    def __init__(self, state_path: Optional[Path] = None,
                 events_path: Optional[Path] = None) -> None:
        self._state_path = Path(state_path) if state_path else STATE_FILE
        self._events_path = Path(events_path) if events_path else EVENTS_FILE
        self._lock = threading.RLock()
        self._alarms: Dict[str, Alarm] = {}
        self._next_id = 1
        self._loaded = False

    # ── 载入 / 落盘 ────────────────────────────────────────────
    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        self._next_id = int(raw.get("next_id") or 1)
        for item in raw.get("alarms") or []:
            if not isinstance(item, dict):
                continue
            try:
                alarm = Alarm.from_dict(item)
            except TypeError:
                continue
            self._alarms[alarm.key] = alarm
        if self._alarms:
            self._next_id = max(self._next_id, max(a.id for a in self._alarms.values()) + 1)

    def _save(self) -> None:
        payload = {
            "version": _VERSION,
            "next_id": self._next_id,
            "alarms": [a.to_dict() for a in self._sorted()],
        }
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self._state_path)

    def _append_event(self, event: str, alarm: Alarm, now: datetime,
                      extra: Optional[Dict[str, object]] = None) -> None:
        record = {"event": event, "at": _iso(now), "id": alarm.id, "key": alarm.key,
                  "kind": alarm.kind, "severity": alarm.severity, "state": alarm.state,
                  "title": alarm.title, "count": alarm.count}
        if extra:
            record.update(extra)
        try:
            self._events_path.parent.mkdir(parents=True, exist_ok=True)
            with self._events_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:  # 台账写入失败不影响内存态（但要留痕）
            logger.warning("报警事件追加失败（%s）: %s", self._events_path.name, type(exc).__name__)
        logger.info(json.dumps({"module_name": "alarms", "action": "alarm_" + event,
                                "key": alarm.key, "severity": alarm.severity,
                                "state": alarm.state}, ensure_ascii=False))

    # ── 写入 ──────────────────────────────────────────────────
    def raise_alarm(self, key: str, *, kind: str, severity: str, title: str,
                    detail: str = "", subject: str = "", source: str = "",
                    now: Optional[datetime] = None) -> Alarm:
        """上报一条报警（同 key 去重；已恢复的会 reopen）。"""
        ts = _now(now)
        if severity not in SEVERITIES:
            severity = SEVERITY_WARNING
        with self._lock:
            self._ensure_loaded()
            alarm = self._alarms.get(key)
            if alarm is None:
                alarm = Alarm(id=self._next_id, key=key, kind=kind, severity=severity,
                              title=title, detail=detail, subject=subject, source=source,
                              first_seen=_iso(ts), last_seen=_iso(ts), count=1)
                self._next_id += 1
                self._alarms[key] = alarm
                self._append_event("firing", alarm, ts, {"detail": detail})
            else:
                reopened = alarm.state == STATE_RESOLVED
                alarm.state = STATE_FIRING
                alarm.severity = severity
                alarm.title = title
                alarm.detail = detail
                alarm.subject = subject or alarm.subject
                alarm.source = source or alarm.source
                alarm.last_seen = _iso(ts)
                alarm.count += 1
                if reopened:
                    alarm.resolved_at = ""
                    alarm.read = False          # 新一次发生 = 新的未读
                    alarm.acked = False
                    alarm.acked_by = ""
                    alarm.acked_at = ""
                    self._append_event("reopen", alarm, ts, {"detail": detail})
                else:
                    self._append_event("recount", alarm, ts)
            self._prune()
            self._save()
            return alarm

    def resolve(self, key: str, *, detail: str = "",
                now: Optional[datetime] = None) -> Optional[Alarm]:
        """把一条 firing 报警标记为已恢复（不删除，保留恢复时间）。"""
        ts = _now(now)
        with self._lock:
            self._ensure_loaded()
            alarm = self._alarms.get(key)
            if alarm is None or alarm.state != STATE_FIRING:
                return None
            alarm.state = STATE_RESOLVED
            alarm.resolved_at = _iso(ts)
            if detail:
                alarm.detail = detail
            # 恢复也要被看到：置未读（除静默中）
            alarm.read = False
            self._append_event("resolved", alarm, ts)
            self._save()
            return alarm

    def reconcile(self, candidates: Dict[str, Dict[str, object]],
                  now: Optional[datetime] = None,
                  keep: Iterable[str] = ()) -> Dict[str, int]:
        """用一轮采集结果对齐当前态：candidates 里的 → raise；不再出现的 firing → resolve。

        【调用方契约】candidates 只包含**确实测到问题**的项；数据源不可用时不要传
        "空"来代表一切正常 —— 那样会把上一轮的告警误判为恢复。确实读不到的数据源，
        把它的 key 放进 `keep`：这些告警保持原状（既不上报新值，也不宣布恢复）。
        """
        protected = {str(k) for k in keep}
        raised = 0
        for key, cand in candidates.items():
            self.raise_alarm(
                key,
                kind=str(cand.get("kind") or "system"),
                severity=str(cand.get("severity") or SEVERITY_WARNING),
                title=str(cand.get("title") or key),
                detail=str(cand.get("detail") or ""),
                subject=str(cand.get("subject") or ""),
                source=str(cand.get("source") or ""),
                now=now,
            )
            raised += 1
        resolved = 0
        with self._lock:
            self._ensure_loaded()
            for key, alarm in list(self._alarms.items()):
                if key in candidates or key in protected or alarm.state != STATE_FIRING:
                    continue
                if self.resolve(key, now=now) is not None:
                    resolved += 1
        return {"raised": raised, "resolved": resolved}

    # ── 读 ────────────────────────────────────────────────────
    def _sorted(self) -> List[Alarm]:
        return sorted(
            self._alarms.values(),
            key=lambda a: (a.state != STATE_FIRING,
                           -_SEVERITY_RANK.get(a.severity, 0),
                           a.last_seen),
            reverse=False,
        )

    def list(self, *, state: Optional[str] = None, unread_only: bool = False,
             kind: Optional[str] = None, limit: int = 200, offset: int = 0,
             now: Optional[datetime] = None) -> List[Dict[str, object]]:
        with self._lock:
            self._ensure_loaded()
            rows = []
            for alarm in self._sorted():
                if state and alarm.state != state:
                    continue
                if kind and alarm.kind != kind:
                    continue
                if unread_only and (alarm.read or alarm.silenced(now)):
                    continue
                rows.append(alarm.to_dict())
            return rows[offset:offset + limit]

    def count(self, *, state: Optional[str] = None) -> int:
        with self._lock:
            self._ensure_loaded()
            return sum(1 for a in self._alarms.values() if not state or a.state == state)

    def unread_count(self, now: Optional[datetime] = None) -> int:
        """未读数 = 未读且**不在静默期**（静默只是不催，不是删）"""
        with self._lock:
            self._ensure_loaded()
            return sum(1 for a in self._alarms.values() if not a.read and not a.silenced(now))

    def summary(self, now: Optional[datetime] = None) -> Dict[str, object]:
        with self._lock:
            self._ensure_loaded()
            by_state = {s: 0 for s in STATES}
            by_severity = {s: 0 for s in SEVERITIES}
            for alarm in self._alarms.values():
                by_state[alarm.state] = by_state.get(alarm.state, 0) + 1
                if alarm.state == STATE_FIRING:
                    by_severity[alarm.severity] = by_severity.get(alarm.severity, 0) + 1
            return {
                "total": len(self._alarms),
                "unread": self.unread_count(now),
                "firing": by_state.get(STATE_FIRING, 0),
                "resolved": by_state.get(STATE_RESOLVED, 0),
                "firing_by_severity": by_severity,
            }

    def get(self, alarm_id: int) -> Optional[Alarm]:
        with self._lock:
            self._ensure_loaded()
            for alarm in self._alarms.values():
                if alarm.id == alarm_id:
                    return alarm
            return None

    # ── 已读 / 确认 / 静默 ────────────────────────────────────
    def mark_read(self, alarm_id: int) -> bool:
        with self._lock:
            alarm = self.get(alarm_id)
            if alarm is None:
                return False
            alarm.read = True
            self._save()
            return True

    def mark_all_read(self) -> int:
        with self._lock:
            self._ensure_loaded()
            n = 0
            for alarm in self._alarms.values():
                if not alarm.read:
                    alarm.read = True
                    n += 1
            if n:
                self._save()
            return n

    def ack(self, alarm_id: int, *, by: str = "owner",
            now: Optional[datetime] = None) -> bool:
        with self._lock:
            alarm = self.get(alarm_id)
            if alarm is None:
                return False
            ts = _now(now)
            alarm.acked = True
            alarm.acked_by = by or "owner"
            alarm.acked_at = _iso(ts)
            alarm.read = True
            self._append_event("acked", alarm, ts, {"acked_by": alarm.acked_by})
            self._save()
            return True

    def silence(self, alarm_id: int, *, minutes: int = DEFAULT_SILENCE_MINUTES,
                now: Optional[datetime] = None) -> Optional[str]:
        """静默 N 分钟（不改状态、不删除；到期自动重新计入未读）。"""
        with self._lock:
            alarm = self.get(alarm_id)
            if alarm is None:
                return None
            try:
                span = int(minutes)
            except (TypeError, ValueError):
                span = DEFAULT_SILENCE_MINUTES
            span = max(1, min(span, MAX_SILENCE_MINUTES))
            ts = _now(now)
            alarm.silenced_until = _iso(ts + timedelta(minutes=span))
            self._append_event("silenced", alarm, ts, {"minutes": span})
            self._save()
            return alarm.silenced_until

    def _prune(self) -> None:
        if len(self._alarms) <= MAX_ALARMS:
            return
        removable = [a for a in self._sorted() if a.state == STATE_RESOLVED]
        for alarm in removable[: max(0, len(self._alarms) - MAX_ALARMS)]:
            self._alarms.pop(alarm.key, None)


_default_store: Optional[AlarmStore] = None
_store_lock = threading.Lock()


def get_store() -> AlarmStore:
    """进程内默认台账（路由/采集线程共用同一份）"""
    global _default_store
    with _store_lock:
        if _default_store is None:
            _default_store = AlarmStore()
        return _default_store


def reset_store(store: Optional[AlarmStore] = None) -> None:
    """替换/清空默认台账（测试隔离用）"""
    global _default_store
    with _store_lock:
        _default_store = store
