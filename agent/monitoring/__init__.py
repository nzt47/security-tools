#!/usr/bin/env python3
"""
性能监控模块

提供分布式追踪和性能指标收集功能。

包含:
- TraceContext: 追踪上下文管理器
- MetricsCollector: 指标收集器
- ErrorReporter: 错误上报器
- monitor_latency: 延迟监控装饰器
- monitor_counter: 计数器监控装饰器
- trace_operation: 追踪装饰器

快速开始:
    from agent.monitoring import TraceContext, get_metrics_collector, get_error_reporter

    # 追踪操作
    with TraceContext("MyService", "myOperation"):
        # ... 执行操作 ...
        pass

    # 收集指标
    collector = get_metrics_collector()
    collector.record_latency("latency.myOperation", 0.5)
    collector.increment_counter("count.myOperation")

    # 上报错误
    reporter = get_error_reporter()
    reporter.report_error(exception, context={"user_id": "123"})
"""

# ════════════════════════════════════════════════════════════════════════════
#  PEP 562 模块级懒加载
# ════════════════════════════════════════════════════════════════════════════
# Why: monitoring 下 7 个子模块在顶层相互依赖较少, 但部分子模块(如 prometheus)
#   会拉入重依赖链. 顶层全量导入会让 `import agent.monitoring` 触发全部子模块加载,
#   既拖慢启动, 也增加与反向引用 monitoring 包级符号的代码形成循环依赖的风险.
# 不变量(不易): __all__ 与历史导出完全一致, `from agent.monitoring import X` 100% 向后兼容.
#   子模块直接访问(`from agent.monitoring import tracing` / `import agent.monitoring.metrics`)
#   由 Python 原生导入机制处理, 无需 __getattr__ 介入.
# 向后兼容: 首次访问符号时懒加载子模块并缓存到 globals(), 后续访问零开销.
_PKG = __name__  # "agent.monitoring"

# 子模块名 → 该模块对外暴露的符号清单(与历史 from ... import 完全一致)
_LAZY_MODULES = {
    "tracing": [
        "TraceContext", "get_trace_id", "set_trace_id", "get_span_id", "set_span_id",
        "extract_trace_context", "inject_trace_context", "trace", "format_trace_log",
        "TraceContextError", "InvalidTraceParentError",
        "safe_extract_trace_context", "safe_inject_trace_context",
        "check_tracing_health", "validate_trace_context",
        "detect_context_loss_scenarios", "capture_context", "restore_context",
        "run_with_context", "is_opentelemetry_available",
        "diagnose_opentelemetry_config", "print_diagnosis_report", "print_context_diagnosis",
    ],
    "metrics": [
        "MetricsCollector", "Metric", "get_metrics_collector",
        "record_latency", "increment_counter", "get_all_metrics",
    ],
    "error_reporter": [
        "ErrorReporter", "ErrorReport", "AlertLevel", "ReporterType",
        "get_error_reporter", "report_error",
        "BaseReporter", "ConsoleReporter", "WebhookReporter",
        "SlackReporter", "EmailReporter", "FileReporter",
    ],
    "decorators": [
        "monitor_latency", "monitor_counter", "monitor_both",
        "trace_operation", "monitored",
    ],
    "performance": [
        "ModuleInitRecord", "InitPerformanceTracker", "Timer",
        "log_module_load_time", "get_performance_recorder",
        "RuntimeSampler", "AlertConfig", "PerformanceAlertManager",
        "create_default_alert_callback", "get_alert_manager", "setup_performance_monitoring",
        "CacheEntry", "LLMCacheStats", "LLMCache",
        "AsyncSaveMonitor", "PerformanceLogger",
        "llm_cache", "async_save_monitor", "perf_logger",
    ],
    "search": [
        "SearchPerformanceMonitor", "get_performance_monitor",
        "start_performance_monitor", "stop_performance_monitor",
        "run_manual_performance_check", "get_performance_monitor_status",
        "get_performance_history", "get_performance_summary",
    ],
    "prometheus": [
        "_PROMETHEUS_AVAILABLE", "_ERROR_HANDLER_AVAILABLE",
        "PrometheusMetricsExporter", "create_exporter_from_digital_life",
        "RetryablePrometheusOperation",
    ],
}

# 反向映射: 符号名 → 来源子模块名 (O(1) 查找)
_LAZY_IMPORTS = {}
for _submod, _symbols in _LAZY_MODULES.items():
    for _sym in _symbols:
        _LAZY_IMPORTS[_sym] = _submod
del _submod, _symbols


def __getattr__(name):
    """PEP 562: 仅在访问具体符号时才导入对应子模块, 降低启动开销与循环依赖风险.

    子模块直接访问(如 `from agent.monitoring import tracing`)由 Python 原生
    导入机制处理, 不会走到这里.
    """
    submod = _LAZY_IMPORTS.get(name)
    if submod is not None:
        import importlib
        attr = getattr(importlib.import_module(f"{_PKG}.{submod}"), name)
        globals()[name] = attr  # 缓存到全局, 后续访问零开销
        return attr
    raise AttributeError(f"module {_PKG!r} has no attribute {name!r}")


def __dir__():
    """补全 dir(agent.monitoring), 让懒加载符号可被发现 (REPL/IDE 自动补全兼容)."""
    return sorted(set(globals()) | set(_LAZY_IMPORTS))


__all__ = [
    # 追踪相关
    'TraceContext', 'get_trace_id', 'set_trace_id', 'get_span_id', 'set_span_id',
    'extract_trace_context', 'inject_trace_context', 'trace', 'format_trace_log',
    'TraceContextError', 'InvalidTraceParentError',
    'safe_extract_trace_context', 'safe_inject_trace_context',
    'check_tracing_health', 'validate_trace_context',
    'detect_context_loss_scenarios', 'capture_context', 'restore_context',
    'run_with_context', 'is_opentelemetry_available',
    'diagnose_opentelemetry_config', 'print_diagnosis_report', 'print_context_diagnosis',

    # 指标相关
    'MetricsCollector', 'Metric', 'get_metrics_collector',
    'record_latency', 'increment_counter', 'get_all_metrics',

    # 错误上报相关
    'ErrorReporter', 'ErrorReport', 'AlertLevel', 'ReporterType',
    'get_error_reporter', 'report_error',
    'BaseReporter', 'ConsoleReporter', 'WebhookReporter',
    'SlackReporter', 'EmailReporter', 'FileReporter',

    # 装饰器
    'monitor_latency', 'monitor_counter', 'monitor_both',
    'trace_operation', 'monitored',

    # 性能日志
    'ModuleInitRecord', 'InitPerformanceTracker', 'Timer',
    'log_module_load_time', 'get_performance_recorder',
    'RuntimeSampler', 'AlertConfig', 'PerformanceAlertManager',
    'create_default_alert_callback', 'get_alert_manager', 'setup_performance_monitoring',
    'CacheEntry', 'LLMCacheStats', 'LLMCache',
    'AsyncSaveMonitor', 'PerformanceLogger',
    'llm_cache', 'async_save_monitor', 'perf_logger',

    # 搜索引擎性能监控
    'SearchPerformanceMonitor',
    'get_performance_monitor', 'start_performance_monitor', 'stop_performance_monitor',
    'run_manual_performance_check', 'get_performance_monitor_status',
    'get_performance_history', 'get_performance_summary',

    # Prometheus
    '_PROMETHEUS_AVAILABLE', '_ERROR_HANDLER_AVAILABLE',
    'PrometheusMetricsExporter', 'create_exporter_from_digital_life', 'RetryablePrometheusOperation',
]

__version__ = '1.1.0'
