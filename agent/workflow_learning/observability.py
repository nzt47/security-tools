"""工作流学习系统可观测性 (与 skills_mgmt 同构)"""

from __future__ import annotations
import json
import time
import uuid
import logging
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

logger = logging.getLogger("agent.workflow_learning")

try:
    from agent.monitoring.business_metrics import BusinessMetricsCollector
    _metrics = BusinessMetricsCollector()
    _METRICS_AVAILABLE = True
except Exception:  # noqa: BLE001
    _metrics = None
    _METRICS_AVAILABLE = False


def _emit_structured_log(action: str, *, trace_id: Optional[str] = None,
                         duration_ms: float = 0.0, level: str = "info",
                         **payload: Any) -> None:
    record = {
        "trace_id": trace_id or str(uuid.uuid4()),
        "module_name": "workflow_learning",
        "action": action,
        "duration_ms": round(duration_ms, 2),
        "ts": time.time(),
        **payload,
    }
    getattr(logger, level, logger.info)(json.dumps(record, ensure_ascii=False, default=str))


@contextmanager
def traced_action(action: str, *, trace_id: Optional[str] = None,
                  **payload: Any) -> Iterator[Dict[str, Any]]:
    tid = trace_id or str(uuid.uuid4())
    ctx: Dict[str, Any] = {"trace_id": tid, "payload": payload}
    t0 = time.time()
    try:
        _emit_structured_log(f"{action}.start", trace_id=tid, duration_ms=0.0, **payload)
        yield ctx
        elapsed = (time.time() - t0) * 1000
        _emit_structured_log(f"{action}.end", trace_id=tid, duration_ms=elapsed,
                             status="ok", **payload, **{
                                 k: v for k, v in ctx.items()
                                 if k not in ("trace_id", "payload")
                             })
    except Exception as e:
        elapsed = (time.time() - t0) * 1000
        _emit_structured_log(f"{action}.error", trace_id=tid, duration_ms=elapsed,
                             status="error", error=str(e),
                             error_type=type(e).__name__, level="error", **payload)
        raise


def track_event(event_name: str, payload: Optional[Dict[str, Any]] = None) -> None:
    try:
        _emit_structured_log("track_event", event_name=event_name, payload=payload or {})
    except Exception:  # noqa: BLE001
        logger.debug("track_event 失败，已忽略", exc_info=True)


def emit_metric(name: str, *, value: float = 1.0, labels: Optional[Dict[str, str]] = None,
                kind: str = "counter") -> None:
    if not _METRICS_AVAILABLE:
        return
    try:
        labels = labels or {}
        if "success" not in labels and "failure" not in labels:
            labels = {**labels, "success": "true"}
        if kind == "counter" and hasattr(_metrics, "inc_counter"):
            _metrics.inc_counter(name, labels=labels, value=value)
        elif kind == "histogram" and hasattr(_metrics, "observe_histogram"):
            _metrics.observe_histogram(name, value=value, labels=labels)
        elif kind == "gauge" and hasattr(_metrics, "set_gauge"):
            _metrics.set_gauge(name, value=value, labels=labels)
    except Exception:  # noqa: BLE001
        logger.debug("emit_metric 失败: %s", name, exc_info=True)
