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

【一条刻意的例外（2026-11-12）】`dispatch_gaps`（覆盖缺口首现提示）**不受**
`CP_ALARM_NOTIFY_WARNING` 过滤：它由调用方按 `kind:id` 指纹去重、每条缺口一辈子只走
一次，不是"每轮都会响"的告警。把"新能力没人监控"压成不发，等于让这条线继续静默。
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

    # 上一轮攒下的 warning 摘要先补发（每小时最多一条；见 dispatch_warning_digest）
    # 【必须用调用方的 dispatcher】否则单测会绕过注入、真的走 channel_bridge 发邮件
    _flush_digest(stats, dispatcher)
    # 本轮新出现的 warning 先攒进摘要（不再逐条压死在站内）
    digest_state = load_digest() if warning_digest_enabled() else None

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
        severity = str(record.get("severity") or "")
        if severity not in allowed:
            if (digest_state is not None and severity == "warning"
                    and str(record.get("key") or "") not in silenced_keys):
                _append_digest(digest_state, record)
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
    if digest_state is not None:
        save_digest(digest_state)
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


def _flush_digest(stats: Dict[str, Any],
                  dispatcher: Optional[Callable[..., Dict[str, Any]]] = None) -> None:
    """到点就把攒下的 warning 合并发出（**永不抛**，失败只进 stats）。"""
    if not warning_digest_enabled():
        return
    try:
        state = load_digest()
        if not (state.get("pending") or []):
            return
        result = dispatch_warning_digest(dispatcher=dispatcher or _default_dispatcher())
        if result.get("sent") or result.get("items"):
            stats["digest"] = result
    except Exception as exc:  # noqa: BLE001 摘要失败不影响逐条外发
        stats["digest"] = {"sent": False, "reason": type(exc).__name__}


def _warn_failure_throttled(stats: Dict[str, Any]) -> None:
    """通道装了但发不出去时，每 30 分钟警告一次（而不是每条事件一条）。"""
    global _last_failure_log_at
    now = time.time()
    if now - _last_failure_log_at < _FAILURE_LOG_INTERVAL_SEC:
        return
    _last_failure_log_at = now
    logger.warning("[AlarmNotify] 外发失败：本轮 %d 条全部未发出（通道已安装但可能未配置；"
                   "详见 /api/alarms/coverage 的 notify.last_round）", stats.get("failed", 0))


# ── warning 摘要（2026-11-13）──────────────────────────────────
# 【为什么】默认只外发 critical，warning 全压在站内 —— 用户不盯页面时，"单位 warn /
# CI 失败 / P99 抖动"这类 warning 等于没有。逐条发又会变成噪音源（§11.104 的顾虑）。
# 折中：**每小时最多一条汇总**，只列"新增/仍在"的 warning 键与次数，静默优先。
ENV_WARNING_DIGEST = "CP_ALARM_NOTIFY_WARNING_DIGEST"
DIGEST_REL_PATH = "data/alarms/warning_digest.json"
#: 两条摘要之间的最小间隔（秒）
DIGEST_INTERVAL_SEC = 3600
#: 一条摘要里最多列几条（超出的只给数量，避免长邮件）
DIGEST_MAX_ITEMS = 20
#: 摘要标题前缀（与逐条告警区分开，收件人一眼知道这是汇总）
DIGEST_TITLE = "告警摘要（warning）"


def warning_digest_enabled() -> bool:
    raw = (os.environ.get(ENV_WARNING_DIGEST) or "").strip().lower()
    if not raw:
        return True
    return raw in _TRUTHY


def _digest_path(path: Optional[Path] = None) -> Path:
    return Path(path) if path is not None else _root() / DIGEST_REL_PATH


def load_digest(path: Optional[Path] = None) -> Dict[str, Any]:
    """读摘要待发状态（缺失/坏文件 ⇒ 空）。"""
    try:
        raw = json.loads(_digest_path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"pending": [], "last_sent_at": None, "last_attempt_at": None}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[AlarmNotify] 摘要状态读取失败（%s），按空处理", type(exc).__name__)
        return {"pending": [], "last_sent_at": None, "last_attempt_at": None}
    if not isinstance(raw, dict):
        return {"pending": [], "last_sent_at": None, "last_attempt_at": None}
    raw.setdefault("pending", [])
    raw.setdefault("last_sent_at", None)
    raw.setdefault("last_attempt_at", None)
    if not isinstance(raw["pending"], list):
        raw["pending"] = []
    return raw


def save_digest(state: Dict[str, Any], path: Optional[Path] = None) -> None:
    target = _digest_path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(str(tmp), str(target))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[AlarmNotify] 摘要状态写入失败（%s）", type(exc).__name__)


def _parse_iso(value: Any) -> Optional[float]:
    text = str(value or "")
    if not text:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _append_digest(state: Dict[str, Any], record: Dict[str, Any]) -> None:
    key = str(record.get("key") or "")
    if not key:
        return
    for item in state["pending"]:
        if item.get("key") == key:
            item["count"] = int(item.get("count") or 0) + 1
            item["last_at"] = record.get("at")
            return
    state["pending"].append({"key": key, "title": str(record.get("title") or key),
                             "severity": str(record.get("severity") or "warning"),
                             "count": 1, "first_at": record.get("at"),
                             "last_at": record.get("at")})


def dispatch_warning_digest(*, dispatcher: Callable[..., Dict[str, Any]],
                            path: Optional[Path] = None,
                            now: Optional[float] = None,
                            interval: float = DIGEST_INTERVAL_SEC) -> Dict[str, Any]:
    """到点就把累积的 warning 合并成一条发出去（**永不抛**）。"""
    out: Dict[str, Any] = {"sent": False, "items": 0, "reason": ""}
    state = load_digest(path)
    pending = state.get("pending") or []
    if not pending:
        return out
    ts = time.time() if now is None else float(now)
    last_attempt = _parse_iso(state.get("last_attempt_at"))
    if last_attempt is not None and ts - last_attempt < float(interval):
        out["reason"] = "throttled"
        return out
    shown = pending[:DIGEST_MAX_ITEMS]
    lines = ["%s ×%d（最近 %s）" % (item.get("title") or item.get("key"),
                                    int(item.get("count") or 1),
                                    str(item.get("last_at") or "")[11:19])
             for item in shown]
    if len(pending) > len(shown):
        lines.append("…另有 %d 条（完整清单见告警页）" % (len(pending) - len(shown)))
    alert = SimpleNamespace(name=DIGEST_TITLE, severity="warning",
                            message="；".join(lines)[:240],
                            labels={"kind": "digest", "count": str(len(pending))})
    try:
        result = dispatcher(alert, "firing") or {}
    except Exception as exc:  # noqa: BLE001 通道坏了不影响台账
        state["last_attempt_at"] = _iso(ts)
        save_digest(state, path)
        out["reason"] = "failed:" + type(exc).__name__
        return out
    state["last_attempt_at"] = _iso(ts)
    if result.get("ok") and (result.get("sent") or result.get("reason") == "no_enabled_channel"):
        state["pending"] = []
        state["last_sent_at"] = _iso(ts)
        out.update({"sent": bool(result.get("sent")), "items": len(pending),
                    "reason": result.get("reason") or "sent"})
    else:
        out["reason"] = str(result.get("reason") or "not_sent")
    save_digest(state, path)
    return out


#: 覆盖缺口外发：warning 级、每条只推一次（去重在调用方，见 agent/monitoring/coverage_gaps.py）
GAP_SEVERITY = "warning"
GAP_KEY_LABEL = "coverage_gap"

#: 每轮最多推几条缺口（与 MAX_PER_ROUND 分开：缺口是"第一次出现"的一次性提示，不该挤占告警配额）
MAX_GAP_PER_ROUND = 5


def dispatch_gaps(gaps: List[Dict[str, Any]], *, dispatcher: Optional[Callable[..., Dict[str, Any]]] = None,
                  limit: int = MAX_GAP_PER_ROUND) -> Dict[str, Any]:
    """把「首次出现的覆盖缺口」经出站通道推一次（**warning 级，不受 include_warning 过滤**）。

    【为什么这里能绕过"默认只发 critical"】那条口径防的是**告警风暴**（warning 数量天然多、
    每轮都会响）。覆盖缺口是**一次性提示**：调用方按 `kind:id` 指纹去重，同一处缺口
    在它消失之前只会走到这里一次。把它也压成 critical 会谎报严重程度；压成"不发"则等于
    把"新能力没人监控"这件事继续留在暗处（Owner 明确要求"首次出现经外发推一次，warning 级"）。

    Args:
        gaps: [{kind, id, why, fix}]（coverage_gaps.refresh 传入，最多 MAX_GAP_PER_ROUND 条）
        dispatcher: 注入发送器（测试用；缺省 = channel_bridge.dispatch_alert 的真实现）
        limit: 本轮最多推几条（超出的不记指纹，调用方下一轮继续）

    Returns:
        {"ok": bool, "attempted": n, "sent": [kind:id], "failed": [kind:id], "reason": str}
        —— **永不抛**（外发是旁路，通道坏不影响自检与台账）。
    """
    out: Dict[str, Any] = {"ok": True, "attempted": 0, "sent": [], "failed": [], "reason": ""}
    if not enabled():
        out["ok"] = False
        out["reason"] = "disabled"
        return out
    rows = [g for g in (gaps or []) if isinstance(g, dict)][:max(1, int(limit))]
    if not rows:
        return out
    send = dispatcher or _default_dispatcher()
    for gap in rows:
        out["attempted"] += 1
        fp = "%s:%s" % (gap.get("kind") or "", gap.get("id") or "")
        alert = {
            "name": "覆盖缺口：%s" % str(gap.get("id") or ""),
            "severity": GAP_SEVERITY,
            "message": ("%s。建议：%s" % (str(gap.get("why") or ""), str(gap.get("fix") or "")))[:240],
            "labels": {"kind": GAP_KEY_LABEL, "surface": str(gap.get("kind") or "")},
        }
        try:
            result = send(alert, "firing") or {}
        except Exception as exc:  # noqa: BLE001 通道实现各异，一律不外抛
            out["failed"].append(fp)
            logger.warning("[AlarmNotify] 缺口外发失败（%s）", type(exc).__name__)
            continue
        if result.get("ok") and result.get("sent"):
            out["sent"].append(fp)
        else:
            out["failed"].append(fp)
            out["reason"] = str(result.get("reason") or "not_sent")
            logger.debug("[AlarmNotify] 缺口未发出（reason=%s）", out["reason"])
    out["ok"] = not out["failed"]
    return out


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
    "ENV_ENABLED", "ENV_INCLUDE_WARNING", "MAX_PER_ROUND", "MAX_GAP_PER_ROUND",
    "enabled", "include_warning", "load_cursor", "save_cursor", "read_events",
    "pending", "dispatch_pending", "dispatch_gaps", "status",
]
