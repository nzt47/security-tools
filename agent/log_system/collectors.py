"""
采集层 — 多维度日志数据采集器

提供 5 种采集器覆盖不同维度：
- OperationCollector: 操作记录（函数调用、API请求）
- PerformanceCollector: 性能指标（响应时间、系统资源）
- ErrorCollector: 错误信息（异常捕获、告警）
- BehaviorCollector: 用户行为（对话、操作序列）
- SystemEventCollector: 系统事件（启动、停止、配置变更）
"""

import time
import logging
import functools
import traceback
import threading
from typing import Optional, Dict, Any, Callable

from .models import (
    LogLevel, LogCategory, LogEntry,
    PerformanceRecord, ErrorRecord, BehaviorRecord,
)
from .storage import get_storage

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# 操作日志采集 — 装饰器方式
# ─────────────────────────────────────────────────────────────

def log_operation(category: str = "operation", level: str = "info",
                  source: str = "", capture_result: bool = False):
    """
    操作日志记录装饰器

    用法:
        @log_operation(category="system", source="task_scheduler")
        def my_function(param):
            ...

        @log_operation(capture_result=True)  # 记录返回值到 metadata
        def api_call():
            return "result"
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            storage = get_storage()
            start = time.perf_counter()
            err = None
            result = None
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                err = e
                raise
            finally:
                elapsed = (time.perf_counter() - start) * 1000
                if storage:
                    entry = LogEntry(
                        category=LogCategory.OPERATION,
                        level=LogLevel(level) if level in LogLevel._value2member_map_ else LogLevel.INFO,
                        message=f"{func.__module__}.{func.__name__}",
                        source=source or func.__module__,
                        timestamp=time.time(),
                        tags=[category],
                        metadata={
                            'args_repr': repr(args)[:100],
                            'kwargs_repr': repr({k: v for k, v in kwargs.items() if k != 'self'})[:200],
                            'error': str(err) if err else None,
                            'result_repr': repr(result)[:200] if capture_result and result else None,
                        },
                        duration_ms=elapsed,
                    )
                    storage.write_entry(entry)
                    if err:
                        storage.write_raw('error', {
                            'timestamp': entry.timestamp,
                            'function': f"{func.__module__}.{func.__name__}",
                            'error': str(err),
                            'traceback': traceback.format_exc(),
                            'duration_ms': elapsed,
                        })
        return wrapper
    return decorator


class OperationCollector:
    """操作记录采集器"""

    def __init__(self):
        self._storage = None

    @property
    def storage(self):
        if self._storage is None:
            self._storage = get_storage()
        return self._storage

    def record(self, operation: str, status: str = "done",
               level: str = "info", source: str = "",
               duration_ms: float = 0.0, user_id: str = "",
               metadata: dict = None, tags: list = None):
        """记录一条操作"""
        if not self.storage:
            return
        entry = LogEntry(
            category=LogCategory.OPERATION,
            level=LogLevel(level) if level in ('debug', 'info', 'warning', 'error', 'critical') else LogLevel.INFO,
            message=operation,
            source=source,
            timestamp=time.time(),
            tags=tags or [],
            metadata=metadata or {},
            user_id=user_id,
            duration_ms=duration_ms,
        )
        self.storage.write_entry(entry)


class PerformanceCollector:
    """性能指标采集器"""

    def __init__(self):
        self._storage = None
        self._timers: Dict[str, float] = {}

    @property
    def storage(self):
        if self._storage is None:
            self._storage = get_storage()
        return self._storage

    def record(self, metric_name: str, value: float, unit: str = "ms",
               source: str = "", tags: dict = None):
        """记录一条性能指标"""
        if not self.storage:
            return
        record = PerformanceRecord(
            metric_name=metric_name,
            value=value,
            unit=unit,
            timestamp=time.time(),
            tags=tags or {},
            source=source,
        )
        self.storage.write_performance(record)

    def start_timer(self, timer_id: str = None) -> str:
        """启动计时器，返回计时器ID"""
        tid = timer_id or f"timer_{time.time()}"
        self._timers[tid] = time.perf_counter()
        return tid

    def stop_timer(self, timer_id: str, metric_name: str = None,
                   source: str = "", tags: dict = None):
        """停止计时器并记录性能数据"""
        start = self._timers.pop(timer_id, None)
        if start is None:
            return
        elapsed = (time.perf_counter() - start) * 1000
        name = metric_name or f"timer.{timer_id}"
        self.record(name, elapsed, source=source, tags=tags)

    def time_context(self, metric_name: str, source: str = "", tags: dict = None):
        """计时上下文管理器

        用法:
            with perf_collector.time_context("llm_request"):
                response = llm.call(prompt)
        """
        class _TimeContext:
            def __init__(self, collector, metric_name, source, tags):
                self.collector = collector
                self.metric_name = metric_name
                self.source = source
                self.tags = tags or {}
                self.timer_id = None

            def __enter__(self):
                self.timer_id = self.collector.start_timer()
                return self

            def __exit__(self, *args):
                self.collector.stop_timer(self.timer_id, self.metric_name, self.source, self.tags)

        return _TimeContext(self, metric_name, source, tags)


class ErrorCollector:
    """错误信息采集器"""

    def __init__(self):
        self._storage = None

    @property
    def storage(self):
        if self._storage is None:
            self._storage = get_storage()
        return self._storage

    def record(self, message: str, severity: str = "error",
               source: str = "", exc_info: bool = False,
               context: dict = None):
        """记录一条错误"""
        if not self.storage:
            return
        exc_type = ""
        tb = ""
        if exc_info:
            exc_type = traceback.format_exc().split('\n')[-2] if traceback.format_exc() else ""
            tb = traceback.format_exc()

        record = ErrorRecord(
            message=message,
            severity=severity,
            source=source,
            timestamp=time.time(),
            exception_type=exc_type or type(exc_info).__name__ if exc_info and not isinstance(exc_info, bool) else "",
            traceback=tb,
            context=context or {},
        )
        self.storage.write_error(record)
        # 同时写入原始日志
        self.storage.write_raw('error', record.to_dict())

    def catch(self, source: str = ""):
        """异常捕获上下文管理器

        用法:
            with error_collector.catch(source="llm_call"):
                response = llm.call(prompt)
        """
        class _CatchContext:
            def __init__(self, collector, source):
                self.collector = collector
                self.source = source

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                if exc_val:
                    self.collector.record(
                        message=str(exc_val),
                        severity="error",
                        source=self.source,
                        exc_info=True,
                        context={'exception_type': exc_type.__name__ if exc_type else ''}
                    )
                    return False  # 不吞异常
        return _CatchContext(self, source)


class BehaviorCollector:
    """用户行为采集器"""

    def __init__(self):
        self._storage = None

    @property
    def storage(self):
        if self._storage is None:
            self._storage = get_storage()
        return self._storage

    def record(self, user_id: str, action_type: str,
               session_id: str = "", payload: dict = None,
               duration_ms: float = 0.0):
        """记录一条用户行为"""
        if not self.storage:
            return
        record = BehaviorRecord(
            user_id=user_id,
            action_type=action_type,
            session_id=session_id,
            timestamp=time.time(),
            payload=payload or {},
            duration_ms=duration_ms,
        )
        self.storage.write_behavior(record)


class SystemEventCollector:
    """系统事件采集器"""

    def __init__(self):
        self._storage = None

    @property
    def storage(self):
        if self._storage is None:
            self._storage = get_storage()
        return self._storage

    def record(self, event_type: str, source: str = "",
               level: str = "info", data: dict = None):
        """记录一条系统事件

        事件类型约定:
            startup     — 系统启动
            shutdown    — 系统关闭
            config_change — 配置变更
            module_load — 模块加载
            task_start  — 任务开始
            task_end    — 任务结束
            heartbeat   — 心跳
        """
        if not self.storage:
            return
        entry = LogEntry(
            category=LogCategory.SYSTEM,
            level=LogLevel(level) if level in ('debug', 'info', 'warning', 'error', 'critical') else LogLevel.INFO,
            message=event_type,
            source=source,
            timestamp=time.time(),
            tags=[event_type],
            metadata=data or {},
        )
        self.storage.write_entry(entry)
        # 也写入原始日志
        self.storage.write_raw('system', {
            'event_type': event_type,
            'source': source,
            'level': level,
            'data': data or {},
            'timestamp': entry.timestamp,
        })


# ── 便捷全局实例 ─────────────────────────────────────────────

_operation_collector = None
_performance_collector = None
_error_collector = None
_behavior_collector = None
_system_event_collector = None


def get_operation_collector():
    global _operation_collector
    if _operation_collector is None:
        _operation_collector = OperationCollector()
    return _operation_collector


def get_performance_collector():
    global _performance_collector
    if _performance_collector is None:
        _performance_collector = PerformanceCollector()
    return _performance_collector


def get_error_collector():
    global _error_collector
    if _error_collector is None:
        _error_collector = ErrorCollector()
    return _error_collector


def get_behavior_collector():
    global _behavior_collector
    if _behavior_collector is None:
        _behavior_collector = BehaviorCollector()
    return _behavior_collector


def get_system_event_collector():
    global _system_event_collector
    if _system_event_collector is None:
        _system_event_collector = SystemEventCollector()
    return _system_event_collector
