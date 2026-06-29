
import logging
import json
import uuid

logger = logging.getLogger(__name__)


def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]

"""
性能监控模块 - 薄包装
实际代码位于 agent/monitoring/performance.py
"""
from agent.monitoring.performance import (
    ModuleInitRecord,
    InitPerformanceTracker,
    Timer,
    log_module_load_time,
    get_performance_recorder,
    RuntimeSampler,
    AlertConfig,
    PerformanceAlertManager,
    create_default_alert_callback,
    get_alert_manager,
    setup_performance_monitoring,
)

__all__ = [
    "ModuleInitRecord", "InitPerformanceTracker", "Timer",
    "log_module_load_time", "get_performance_recorder",
    "RuntimeSampler", "AlertConfig", "PerformanceAlertManager",
    "create_default_alert_callback", "get_alert_manager",
    "setup_performance_monitoring",
]


def _safe_call(func, *args, action="safe_call", **kwargs):
    """安全调用包装器——捕获异常并记录结构化日志后重新抛出

    用于边界显性化：可能失败的操作应通过此包装器调用，
    确保异常被记录后再向上传播，而非静默吞掉。
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.error(json.dumps({
            "trace_id": _trace_id(),
            "module_name": "performance_monitor",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
