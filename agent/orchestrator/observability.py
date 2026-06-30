"""orchestrator 模块可观测性埋点

遵循 yunshu_<模块>_<动作> 命名规范，使用 BusinessMetricsCollector 统一收集。
埋点失败不影响主流程（吞掉异常，仅日志记录）。
"""

from __future__ import annotations
import json
import time
import uuid
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("agent.orchestrator")

try:
    from agent.monitoring.business_metrics import BusinessMetricsCollector
    _metrics = BusinessMetricsCollector()
    _METRICS_AVAILABLE = True
except Exception:
    _metrics = None
    _METRICS_AVAILABLE = False


def _trace_id() -> str:
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


def _emit_structured_log(action: str, *, trace_id: Optional[str] = None,
                         duration_ms: float = 0.0, level: str = "info",
                         **payload: Any) -> None:
    """输出结构化日志"""
    record = {
        "trace_id": trace_id or _trace_id(),
        "module_name": "orchestrator",
        "action": action,
        "duration_ms": round(duration_ms, 2),
        **payload,
    }
    getattr(logger, level, logger.info)(json.dumps(record, ensure_ascii=False, default=str))


def trackEvent(event_name: str, payload: Optional[Dict[str, Any]] = None) -> None:
    """埋点函数——记录用户交互/业务事件

    埋点失败不影响主流程（吞掉异常，仅日志记录）。
    指标命名遵循 yunshu_orchestrator_<event_name> 格式。
    """
    tid = _trace_id()
    t0 = time.time()
    _RESERVED = {"action", "trace_id", "duration_ms", "level", "module_name"}
    safe_payload = {k: v for k, v in (payload or {}).items() if k not in _RESERVED}
    try:
        _emit_structured_log(
            f"track.{event_name}",
            trace_id=tid,
            duration_ms=0.0,
            event_name=event_name,
            **safe_payload,
        )
        if _METRICS_AVAILABLE:
            _metrics.record_interaction(event_name, "orchestrator", True, (time.time() - t0) * 1000)
    except Exception as e:
        logger.error(json.dumps({
            "trace_id": tid,
            "module_name": "orchestrator",
            "action": "trackEvent.failed",
            "error": f"{type(e).__name__}: {e}",
            "event_name": event_name,
        }, ensure_ascii=False))
