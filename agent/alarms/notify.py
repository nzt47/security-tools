# -*- coding: utf-8 -*-
"""告警外发（2026-11-12）：把**台账里的状态变化**接到已安装的出站通道上。

【为什么是"接线"而不是"造通道"】出站能力早就有了：
`agent/extensions/channel_bridge.py` 已经有 `dispatch_alert(alert, transition)`
（渲染白名单字段、密钥类 label 一律不发、同通道同告警 60s 冷却、失败重试、永不抛）。
缺的是**没有人调用它** —— 报警器只写站内台账（顶栏铃铛 / 消息中心），
所以 critical 告警在无人看页面时会一直沉默。本模块只做三件事：
① 从 `data/alarms/events.jsonl` 读**尚未外发**的状态变化（按行游标，重启不重发）；
② 按级别过滤（默认只发 critical；warning 需显式开 —— 见下）；
③ 调 `channel_bridge.dispatch_alert`，失败只记日志，绝不阻塞采集线程。

【口径（写死，改要同批改测试）】
- **默认只发 critical**：warning 数量天然多（单位 warn、调度失败…），全外发会变成
  "每天都有人被 @ 一次"的噪音源，最后被静音 ⇒ 等于没有。`CP_ALARM_NOTIFY_WARNING=true`
  才带上 warning（建议只在对某个通道做过观察后再开）。
- **静默（silence）期间不发**：silence 的语义就是"别催我"，外发必须尊重它；
  被跳过的条目也推进游标（否则解除静默后会把一小时的旧告警一次性倒出来）。
- **每轮最多 MAX_PER_ROUND 条**：采集线程每 60s 一轮，积压会自然排空；
  一次全发会对通道造成突发流量（webhook 常限流）。
- **游标 = 已消费的 events.jsonl 行数**（不是时间戳：同秒多条事件用时间戳会漏/重）。
  文件被轮转/截断（当前行数 < 游标）⇒ 游标归零重读，宁可重发一条也不静默丢。
- 内容只来自台账既有字段（标题/级别/摘要/键），**不含真凭据与原始 IP**；
  密钥类 label 由 channel_bridge 兜底再过滤一次。

环境变量（均已进 agent/settings/registry.py）：
    CP_ALARM_NOTIFY_ENABLED    总开关（默认 true；通道没装时 dispatch 返回 no_enabled_channel）
    CP_ALARM_NOTIFY_WARNING    是否也外发 warning（默认 false）
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

__version__ = "1.0.0"

#: 台账事件文件与游标（运行期状态；data/alarms 已在 .gitignore）
EVENTS_REL_PATH = "data/alarms/events.jsonl"
CURSOR_REL_PATH = "data/alarms/notify_cursor.json"

ENV_ENABLED = "CP_ALARM_NOTIFY_ENABLED"
ENV_INCLUDE_WARNING = "CP_ALARM_NOTIFY_WARNING"

#: 每轮最多外发几条（积压由下一轮继续）
MAX_PER_ROUND = 5

#: 台账事件 → 外发的 transition（其余事件如 recount 不外发）
_TRANSITIONS = {"firing": "firing", "reopen": "firing", "resolved": "resolved"}

#: 默认只外发 critical
_NOTIFY_SEVERITIES = ("critical",)

_TRUTHY = frozenset({"1", "true", "yes", "on", "enable", "enabled"})
_FALSY = frozenset({"0", "false", "no", "off", "disable", "disabled"})


def enabled() -> bool:
    raw = (os.environ.get(ENV_ENABLED) or "").strip().lower()
    if not raw:
        return True
    if raw in _TRUTHY:
        return True
    if raw in _FALSY:
        return False
    return True


def include_warning() -> bool:
    raw = (os.environ.get(ENV_INCLUDE_WARNING) or "").strip().lower()
    return raw in _TRUTHY


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _events_path(path: Optional[Path] = None) -> Path:
    return Path(path) if path is not None else _root() / EVENTS_REL_PATH


def _cursor_path(path: Optional[Path] = None) -> Path:
    return Path(path) if path is not None else _root() / CURSOR_REL_PATH


def _allowed_severities() -> Tuple[str, ...]:
    return _NOTIFY_SEVERITIES + (("warning",) if include_warning() else ())


def load_cursor(path: Optional[Path] = None) -> int:
    """已消费的事件行数（缺失/坏文件 ⇒ 0）。"""
    try:
        raw = json.loads(_cursor_path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return 0
    except Exception as exc:  # noqa: BLE001 坏游标按 0 处理（宁可重发一条）
        logger.warning("[AlarmNotify] 游标读取失败（%s），按 0 处理", type(exc).__name__)
        return 0
    if not isinstance(raw, dict):
        return 0
    try:
        return max(0, int(raw.get("lines") or 0))
    except (TypeError, ValueError):
        return 0


def save_cursor(lines: int, path: Optional[Path] = None) -> None:
    """原子写游标（失败只记日志：外发是旁路，不该拖垮采集）。"""
    target = _cursor_path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"lines": int(lines), "updated_at": _iso(time.time())},
                                  ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp), str(target))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[AlarmNotify] 游标写入失败（%s）", type(exc).__name__)


def _iso(ts: float) -> str:
    from datetime import datetime
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def read_events(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """读全部事件（坏行跳过；文件不存在 ⇒ 空）。"""
    try:
        with open(_events_path(path), "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []
    except Exception as exc:  # noqa: BLE001
        logger.warning("[AlarmNotify] 事件读取失败（%s）", type(exc).__name__)
        return []
    out: List[Dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def pending(events: List[Dict[str, Any]], cursor: int) -> List[Dict[str, Any]]:
    """尚未消费的事件（游标越界 ⇒ 从头开始）。"""
    start = cursor if 0 <= cursor <= len(events) else 0
    return events[start:]


def _alert_from_event(record: Dict[str, Any]) -> Any:
    """台账事件 → render_alert 认的字段形状（只带白名单字段）。"""
    return SimpleNamespace(
        name=str(record.get("title") or record.get("key") or "告警"),
        severity=str(record.get("severity") or ""),
        message=str(record.get("detail") or "")[:240],
        labels={"key": str(record.get("key") or ""), "kind": str(record.get("kind") or "")},
    )


def _default_dispatcher() -> Callable[..., Dict[str, Any]]:
    """默认发送器：**一轮只建一次** ChannelInstaller（否则每条事件都重读扩展存储）。

    通道没装/全禁用时 dispatch_alert 自己会返回 reason=no_enabled_channel（永不抛），
    所以这里不做"有没有通道"的预判 —— 预判反而会和真实配置漂移。
    """
    from agent.extensions.channel_bridge import dispatch_alert

    box: Dict[str, Any] = {}

    def _send(alert: Any, transition: str) -> Dict[str, Any]:
        if "installer" not in box:
            try:
                from agent.extensions.channels_installer import ChannelInstaller
                from agent.extensions.store import ExtensionStore
                box["installer"] = ChannelInstaller(ExtensionStore())
            except Exception as exc:  # noqa: BLE001 扩展存储坏了 ⇒ 交给 dispatch_alert 的默认路径
                logger.warning("[AlarmNotify] 通道安装器不可用（%s）", type(exc).__name__)
                box["installer"] = None
        return dispatch_alert(alert, transition, installer=box["installer"])

    return _send


def dispatch_pending(*, store: Any = None,
                     cursor_path: Optional[Path] = None,
                     events_path: Optional[Path] = None,
                     dispatcher: Optional[Callable[..., Dict[str, Any]]] = None,
                     now: Optional[float] = None) -> Dict[str, Any]:
    """把尚未外发的告警状态变化发出去（**永不抛**）。返回本轮统计。"""
    stats: Dict[str, Any] = {"status": "ok", "considered": 0, "sent": 0,
                             "skipped": 0, "failed": 0, "cursor": 0}
    if not enabled():
        stats["status"] = "disabled"
        return stats

    events = read_events(events_path)
    cursor = load_cursor(cursor_path)
    todo = pending(events, cursor)
    stats["considered"] = len(todo)

    if not todo:
        stats["cursor"] = len(events) if cursor > len(events) else cursor
        if stats["cursor"] != cursor:
            save_cursor(stats["cursor"], cursor_path)
        return stats

    allowed = set(_allowed_severities())
    send = dispatcher or _default_dispatcher()
    silenced_keys = set()
    if store is not None:
        try:
            # 【注意】store.list() 返回的是 **dict**（Alarm.to_dict()），只有 store.get() 返对象；
            # 2026-11-12 第一版按属性访问导致 AttributeError 被静默吞掉 = 静默形同虚设。
            from agent.alarms.store import Alarm
            for row in store.list(state="firing"):
                alarm = Alarm.from_dict(row) if isinstance(row, dict) else row
                if alarm.silenced():
                    silenced_keys.add(alarm.key)
        except Exception as exc:  # noqa: BLE001 读不到静默态 ⇒ 宁可发（及时性）也不静默丢
            logger.warning("[AlarmNotify] 静默态读取失败（%s），本轮不按静默过滤",
                           type(exc).__name__)
            silenced_keys = set()

    consumed = cursor if 0 <= cursor <= len(events) else 0
    for record in todo:
        consumed += 1
        transition = _TRANSITIONS.get(str(record.get("event") or ""))
        if transition is None:
            stats["skipped"] += 1
            continue
        if str(record.get("severity") or "") not in allowed:
            stats["skipped"] += 1
            continue
        if str(record.get("key") or "") in silenced_keys:
            stats["skipped"] += 1
            continue
        if stats["sent"] + stats["failed"] >= MAX_PER_ROUND:
            # 本轮额度用完：把游标回退到这条之前，下一轮继续
            consumed -= 1
            break
        try:
            result = send(_alert_from_event(record), transition) or {}
        except Exception as exc:  # noqa: BLE001 通道坏了不影响台账与采集
            stats["failed"] += 1
            logger.warning("[AlarmNotify] 外发失败（%s）", type(exc).__name__)
            continue
        if result.get("ok") and (result.get("sent") or result.get("reason") == "no_enabled_channel"):
            stats["sent"] += 1
            if result.get("reason") == "no_enabled_channel":
                # 没有通道是"配置状态"，不是失败：只记一次 debug，避免每轮刷日志
                logger.debug("[AlarmNotify] 未安装/未启用任何出站通道，本轮只推进游标")
        else:
            stats["failed"] += 1
            # 【别每条都 INFO】2026-11-12 实测：通道装了两个但都没配置（webhook_url/smtp_server 空），
            # 每条事件一条 INFO ⇒ 每分钟 5 条噪音。这里降到 debug，另由下面的节流 warning 汇总。
            logger.debug("[AlarmNotify] 未发出（reason=%s）", result.get("reason") or "")

    stats["cursor"] = consumed
    save_cursor(consumed, cursor_path)
    _record_round(stats)
    if stats["failed"] and not stats["sent"]:
        _warn_failure_throttled(stats)
    return stats


#: 最近一轮外发统计（内存；供 /api/alarms/coverage 的 notify 读数披露"到底发没发出去"）
_last_round: Dict[str, Any] = {}
#: 失败 warning 的节流（同一状态每 30 分钟最多一条）
_FAILURE_LOG_INTERVAL_SEC = 1800
_last_failure_log_at: float = 0.0


def _record_round(stats: Dict[str, Any]) -> None:
    global _last_round
    _last_round = {
        "at": _iso(time.time()),
        "considered": stats.get("considered", 0),
        "sent": stats.get("sent", 0),
        "skipped": stats.get("skipped", 0),
        "failed": stats.get("failed", 0),
    }


def _warn_failure_throttled(stats: Dict[str, Any]) -> None:
    """通道装了但发不出去时，每 30 分钟警告一次（而不是每条事件一条）。"""
    global _last_failure_log_at
    now = time.time()
    if now - _last_failure_log_at < _FAILURE_LOG_INTERVAL_SEC:
        return
    _last_failure_log_at = now
    logger.warning("[AlarmNotify] 外发失败：本轮 %d 条全部未发出（通道已安装但可能未配置；"
                   "详见 /api/alarms/coverage 的 notify.last_round）", stats.get("failed", 0))


def status(cursor_path: Optional[Path] = None,
           events_path: Optional[Path] = None) -> Dict[str, Any]:
    """覆盖读数用（只读）：开关 + 游标 + 待发条数 + 事件总数。"""
    events = read_events(events_path)
    cursor = load_cursor(cursor_path)
    queued = len(pending(events, cursor))
    return {
        "enabled": enabled(),
        "include_warning": include_warning(),
        "cursor_lines": cursor,
        "event_lines": len(events),
        "queued": queued,
        "last_round": dict(_last_round) if _last_round else None,
        "channel_switch": "CP_CHANNEL_EVENT_ALERTS",
        "channel_master_switch": "CP_CHANNEL_EVENTS",
    }


__all__: List[str] = [
    "ENV_ENABLED", "ENV_INCLUDE_WARNING", "MAX_PER_ROUND",
    "enabled", "include_warning", "load_cursor", "save_cursor", "read_events",
    "pending", "dispatch_pending", "status",
]
