"""network 模块可观测性埋点

遵循 yunshu_<模块>_<动作> 命名规范，使用 BusinessMetricsCollector 统一收集。
埋点失败不影响主流程（吞掉异常，仅日志记录）。
"""

from __future__ import annotations
import json
import time
import uuid
import logging
from typing import Any, Dict, Optional
from agent.logging_utils import log_dict

logger = logging.getLogger("agent.network")

try:
    from agent.monitoring.business_metrics import get_business_metrics_collector
    # 【2026-10-02 修「指标写进没人读的实例」】必须用**全局单例** get_business_metrics_collector()，
    #   不能 BusinessMetricsCollector() —— 后者每个模块各 new 一个实例，记录全落在自己那份里，
    #   而 /api/business/prometheus 端点读的是单例 _global_business_collector ⇒ 端点**恒空**
    #   （实测：74 行只有 # HELP/# TYPE、**样本行 0 条**，导致所有依赖业务指标的告警恒不触发）。
    #   同一修法见 agent/skills_mgmt/observability.py:32-36（该处早已改对，本次把其余模块补齐）。
    _metrics = get_business_metrics_collector()
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
        "module_name": "network",
        "action": action,
        "duration_ms": round(duration_ms, 2),
        **payload,
    }
    getattr(logger, level, logger.info)(json.dumps(record, ensure_ascii=False, default=str))


def trackEvent(event_name: str, payload: Optional[Dict[str, Any]] = None) -> None:
    """埋点函数——记录用户交互/业务事件

    埋点失败不影响主流程（吞掉异常，仅日志记录）。
    指标命名遵循 yunshu_network_<event_name> 格式。
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
    # 【2026-10-02 修单位】指标名是 yunshu_interaction_duration_seconds ⇒ 必须传**秒**。
    #   原写法 * 1000 传的是毫秒，量纲大了 1000 倍（延迟类告警的阈值会因此失真）。
            _metrics.record_interaction(event_name, "network", True, (time.time() - t0))
    except Exception as e:
        logger.error(log_dict({'module_name': 'network', 'action': 'trackEvent.failed', 'error': f'{type(e).__name__}: {e}', 'event_name': event_name}))
