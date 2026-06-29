
import logging
import json
import uuid

logger = logging.getLogger(__name__)


def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]

"""
LLM 响应缓存与性能日志模块 - 薄包装
实际代码位于 agent/monitoring/performance.py
"""
from agent.monitoring.performance import (
    CacheEntry,
    LLMCacheStats,
    LLMCache,
    AsyncSaveMonitor,
    PerformanceLogger,
    llm_cache,
    async_save_monitor,
    perf_logger,
)

__all__ = [
    "CacheEntry", "LLMCacheStats", "LLMCache",
    "AsyncSaveMonitor", "PerformanceLogger",
    "llm_cache", "async_save_monitor", "perf_logger",
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
            "module_name": "performance_logging",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
