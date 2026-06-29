"""技能管理系统可观测性

遵循项目硬约束:
    - 结构化日志: JSON 格式，必含 trace_id / module_name / action / duration_ms
    - 埋点预留: 关键交互点调用 trackEvent()
    - 业务指标: 复用 agent.monitoring.business_metrics.BusinessMetricsCollector
      指标命名: yunshu_skill_<动作>
"""

from __future__ import annotations
import json
import time
import uuid
import logging
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

logger = logging.getLogger("agent.skills_mgmt")

# 尝试引入业务指标收集器（按硬约束要求）；失败则降级为 no-op
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
    """输出结构化 JSON 日志 (遵循可观测性强制约束)"""
    record = {
        "trace_id": trace_id or str(uuid.uuid4()),
        "module_name": "skills_mgmt",
        "action": action,
        "duration_ms": round(duration_ms, 2),
        "ts": time.time(),
        **payload,
    }
    getattr(logger, level, logger.info)(json.dumps(record, ensure_ascii=False, default=str))


@contextmanager
def traced_action(action: str, *, trace_id: Optional[str] = None,
                  **payload: Any) -> Iterator[Dict[str, Any]]:
    """追踪一个业务动作的耗时与结果

    用法:
        with traced_action("skill_create", skill_id="foo") as ctx:
            ...
            ctx["result"] = "ok"
    """
    tid = trace_id or str(uuid.uuid4())
    ctx: Dict[str, Any] = {"trace_id": tid, "payload": payload}
    t0 = time.time()
    try:
        _emit_structured_log(f"{action}.start", trace_id=tid, duration_ms=0.0,
                             **payload)
        yield ctx
        elapsed = (time.time() - t0) * 1000
        # 合并 payload 与 ctx，但过滤掉与显式参数冲突的保留键
        merged = {**payload}
        for k, v in ctx.items():
            if k in ("trace_id", "payload", "status", "error",
                     "error_type", "level", "duration_ms"):
                continue
            merged[k] = v
        _emit_structured_log(f"{action}.end", trace_id=tid, duration_ms=elapsed,
                             status="ok", **merged)
    except Exception as e:
        elapsed = (time.time() - t0) * 1000
        # 避免 payload 中的关键字与显式参数冲突
        safe_payload = {
            k: v for k, v in payload.items()
            if k not in ("status", "error", "error_type", "level")
        }
        _emit_structured_log(f"{action}.error", trace_id=tid, duration_ms=elapsed,
                             status="error", error=str(e), error_type=type(e).__name__,
                             level="error", **safe_payload)
        raise


def track_event(event_name: str, payload: Optional[Dict[str, Any]] = None) -> None:
    """前端埋点占位符 (前后端通用)

    后端调用时仅记录日志；前端通过同名函数上传到分析系统。
    失败不影响主流程（按硬约束要求）。
    """
    try:
        _emit_structured_log("track_event", event_name=event_name,
                             payload=payload or {})
    except Exception:  # noqa: BLE001
        logger.debug("track_event 失败，已忽略", exc_info=True)


def emit_metric(name: str, *, value: float = 1.0, labels: Optional[Dict[str, str]] = None,
                kind: str = "counter") -> None:
    """发射业务指标

    Args:
        name: 指标名，遵循 yunshu_skill_<动作> 命名
        value: 计数或耗时
        labels: 必须含 success/failure 标签（按硬约束）
        kind: counter / histogram / gauge
    """
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
    except Exception:  # noqa: BLE001  埋点失败不影响主流程
        logger.debug("emit_metric 失败: %s", name, exc_info=True)
