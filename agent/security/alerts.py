"""安全告警通道（越权尝试 → 告警）

【任务定位】
    §5.7⑦：**越权尝试 → 告警 + 审计**。本模块是「告警」那一半的落点，
    「审计」那一半由 `policy.denied` 事件的 **S2-02 链式镜像**承担
    （`agent/observability/events.py::AUDIT_MIRROR_TYPES` 已含 `policy.denied`）。

【为什么不做成 AlertManager 客户端】
    `agent/monitoring/alert_manager.py` 是**规则驱动 + 需 start() 的常驻组件**，
    在审批路径上实例化它会引入生命周期与线程副作用（治理路径必须轻、必须能失败）。
    故本模块提供**依赖倒置的告警汇聚点**：
      - 默认行为：结构化 ERROR 日志（稳定 marker 便于日志侧检索/接现有采集器）
        + 进程内计数（供「同一来源多次越权」分析 / 面板 / SLO）；
      - 外部通道：`set_alert_sink(cb)` 注入（AlertManager / webhook / Slack，
        由 S4-03/S5-03 或运维接线），**未接线时零副作用**。

【阈值升级（默认关闭外部动作，只升级日志等级）】
    `CP_SECURITY_ALERT_THRESHOLD`（默认 3）与
    `CP_SECURITY_ALERT_WINDOW_SECONDS`（默认 300）：同一来源在窗口内累计达到阈值
    时，日志升级为 CRITICAL 并调用 sink 的 `escalate` 形态载荷。
    阈值 ≤0 → 关闭升级（只逐条告警）。

【PII 纪律】
    告警键一律用 `actor_ip_masked` / `actor_ip_hash`（裁定 B），**原始 IP 不入日志、
    不入计数键**。
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional

logger = logging.getLogger("agent.security.alerts")

#: 稳定日志 marker（日志采集侧据以建索引/触发通知）
ALERT_MARKER = "[SECURITY][APPROVAL_VIOLATION]"
ALERT_MARKER_ESCALATED = "[SECURITY][APPROVAL_VIOLATION][ESCALATED]"

#: 告警等级
LEVEL_WARNING = "warning"
LEVEL_CRITICAL = "critical"

_ENV_THRESHOLD = "CP_SECURITY_ALERT_THRESHOLD"
_ENV_WINDOW = "CP_SECURITY_ALERT_WINDOW_SECONDS"
_ENV_ENABLED = "CP_SECURITY_ALERTS_ENABLED"

_DEFAULT_THRESHOLD = 3
_DEFAULT_WINDOW = 300.0
#: 近期告警保留条数（供面板/分析；有界，不随运行时长增长）
_MAX_RECENT = 200

#: 外部告警通道签名：(event: Dict[str, Any]) -> None
AlertSink = Callable[[Dict[str, Any]], None]

_lock = threading.RLock()
_sink: Optional[AlertSink] = None
_recent: List[Dict[str, Any]] = []
_by_source: Dict[str, List[float]] = {}
_counts: Dict[str, int] = {}
_seq = 0
#: 进程级命名空间（pid + 随机盐）
#:
#: Why：越权事件的幂等键含 `seq`，而 `seq` **每个进程从 1 重新开始**。若不加入
#: 进程命名空间，重启后第一条越权会与上一进程的第一条**撞键**，被事件存储判为重放
#: 而丢弃 —— 审计链静默缺一条（本任务实现期在真实 `data/events/` 上实测到的缺陷）。
_SEQ_NAMESPACE = f"{os.getpid()}-{secrets.token_hex(4)}"


def _process_namespace() -> str:
    """当前进程的越权事件命名空间（测试可复位以复现撞键场景）"""
    return _SEQ_NAMESPACE


def denial_event_key(seq: int, *parts: Any) -> str:
    """越权事件的幂等键（**进程命名空间 + 序号** ⇒ 跨进程/跨次尝试均不撞键）"""
    tail = ":".join(str(p or "") for p in parts)
    return f"policy.denied:{_SEQ_NAMESPACE}:{int(seq)}:{tail}"


def next_denial_seq() -> int:
    """分配一个**单调递增**的越权序号

    Why 必须有：`events.emit` 以 `idempotency_key` 去重（重放不重复计数），而
    「越权尝试」**每次都是独立事实**——同一执行体第 2 次越权必须留下第 2 条链上
    记录，否则「同一来源多次越权」分析失真（本任务实现期实测到的真实缺陷：
    未加序号时，同操作/同对象的重复越权在事件侧被幂等吞掉，审计链只剩第一条）。
    """
    global _seq
    with _lock:
        _seq += 1
        return _seq


def _env_flag(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() not in ("0", "false", "no", "off")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def alerts_enabled() -> bool:
    return _env_flag(_ENV_ENABLED, "1")


def alert_threshold() -> int:
    try:
        return int(os.getenv(_ENV_THRESHOLD, str(_DEFAULT_THRESHOLD)))
    except (TypeError, ValueError):
        return _DEFAULT_THRESHOLD


def alert_window_seconds() -> float:
    return max(0.0, _env_float(_ENV_WINDOW, _DEFAULT_WINDOW))


def set_alert_sink(sink: Optional[AlertSink]) -> Optional[AlertSink]:
    """注入外部告警通道（返回旧 sink；None → 恢复仅日志+计数）"""
    global _sink
    with _lock:
        previous = _sink
        _sink = sink
    return previous


def reset_alerts() -> None:
    """清空计数与 sink（测试隔离）"""
    global _sink
    with _lock:
        _sink = None
        _recent.clear()
        _by_source.clear()
        _counts.clear()


def report_denial(*, operation: str = "", actor: str = "", actor_type: str = "",
                  reason: str = "", object_type: str = "", object_id: str = "",
                  identity_source: str = "", session_id: str = "",
                  record_id: str = "", ip_fields: Optional[Mapping[str, Any]] = None,
                  extra: Optional[Mapping[str, Any]] = None,
                  seq: Optional[int] = None,
                  now: Optional[float] = None) -> Dict[str, Any]:
    """记录一次越权/拒绝尝试并告警（**best-effort：绝不抛异常阻断主路径**）

    Args:
        seq: 越权序号（不传则自动分配）。调用方**应**先取 `next_denial_seq()` 并
            用它构造事件幂等键，再传入本函数，保证「告警事件」与「审计事件」
            一一对应同一条尝试。

    Returns:
        告警事件（叶子字段 dict；同时传给 sink）。
    """
    ts = float(now if now is not None else time.time())
    ip = dict(ip_fields or {})
    source_key = str(ip.get("actor_ip_hash") or ip.get("actor_ip_masked") or "unknown")
    event: Dict[str, Any] = {
        "marker": ALERT_MARKER,
        "kind": "approval_violation",
        "seq": int(seq if seq is not None else next_denial_seq()),
        "operation": str(operation or ""),
        "actor": str(actor or ""),
        "actor_type": str(actor_type or ""),
        "reason": str(reason or "")[:400],
        "object_type": str(object_type or ""),
        "object_id": str(object_id or ""),
        "record_id": str(record_id or ""),
        "identity_source": str(identity_source or ""),
        "session_id": str(session_id or ""),
        "source_key": source_key,
        "ts": ts,
        "escalated": False,
    }
    event.update({k: v for k, v in (extra or {}).items() if v is not None})
    # PII 字段：掩码 + HMAC 哈希（裁定 B；**原始 IP 绝不进入告警事件**）
    event.update({k: v for k, v in ip.items() if v is not None})
    try:
        threshold = alert_threshold()
        window = alert_window_seconds()
        with _lock:
            _counts["total"] = _counts.get("total", 0) + 1
            key_total = f"op:{event['operation']}"
            _counts[key_total] = _counts.get(key_total, 0) + 1
            key_type = f"actor_type:{event['actor_type']}"
            _counts[key_type] = _counts.get(key_type, 0) + 1
            stamps = _by_source.setdefault(source_key, [])
            stamps.append(ts)
            if window > 0:
                cutoff = ts - window
                _by_source[source_key] = [s for s in stamps if s >= cutoff]
                stamps = _by_source[source_key]
            count_in_window = len(stamps)
            event["source_count_in_window"] = count_in_window
            escalated = threshold > 0 and count_in_window >= threshold
            event["escalated"] = bool(escalated)
            _recent.append(event)
            if len(_recent) > _MAX_RECENT:
                del _recent[:len(_recent) - _MAX_RECENT]
    except Exception as e:  # noqa: BLE001 计数失败不影响告警与主路径
        logger.debug("[Security] 越权计数失败: %s", e)
        escalated = False

    if not alerts_enabled():
        return event

    try:
        if event.get("escalated"):
            event["marker"] = ALERT_MARKER_ESCALATED
            event["level"] = LEVEL_CRITICAL
            logger.critical(
                "%s operation=%s actor=%s actor_type=%s identity_source=%s "
                "source=%s count=%s reason=%s",
                ALERT_MARKER_ESCALATED, event["operation"], event["actor"],
                event["actor_type"], event["identity_source"], source_key,
                event.get("source_count_in_window"), event["reason"])
        else:
            event["level"] = LEVEL_WARNING
            logger.error(
                "%s operation=%s actor=%s actor_type=%s identity_source=%s "
                "source=%s reason=%s",
                ALERT_MARKER, event["operation"], event["actor"],
                event["actor_type"], event["identity_source"], source_key,
                event["reason"])
    except Exception as e:  # noqa: BLE001 日志失败不阻断
        logger.debug("[Security] 告警日志失败: %s", e)

    sink = _sink
    if sink is not None:
        try:
            sink(event)
        except Exception as e:  # noqa: BLE001 外部通道失败绝不阻断审批
            logger.warning("[Security] 告警通道调用失败（已忽略）: %s", e)
    return event


def get_denial_stats() -> Dict[str, Any]:
    """越权统计（供面板 / SLO / 「同一来源多次越权」分析）"""
    with _lock:
        return {
            "total": int(_counts.get("total", 0)),
            "counts": dict(_counts),
            "sources": {k: len(v) for k, v in _by_source.items()},
            "recent": [dict(e) for e in _recent[-20:]],
            "threshold": alert_threshold(),
            "window_seconds": alert_window_seconds(),
            "enabled": alerts_enabled(),
        }


def recent_denials(limit: int = 20) -> List[Dict[str, Any]]:
    with _lock:
        return [dict(e) for e in _recent[-max(0, int(limit)):]]


__all__ = [
    "ALERT_MARKER", "ALERT_MARKER_ESCALATED", "LEVEL_WARNING", "LEVEL_CRITICAL",
    "AlertSink", "report_denial", "get_denial_stats", "recent_denials",
    "set_alert_sink", "reset_alerts", "alerts_enabled", "alert_threshold",
    "alert_window_seconds", "next_denial_seq", "denial_event_key",
]