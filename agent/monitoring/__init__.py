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

# 追踪模块
from agent.monitoring.tracing import (
    TraceContext,
    get_trace_id,
    set_trace_id,
    trace,
    format_trace_log
)

# 指标收集模块
from agent.monitoring.metrics import (
    MetricsCollector,
    Metric,
    get_metrics_collector,
    record_latency,
    increment_counter,
    get_all_metrics
)

# 错误上报模块
from agent.monitoring.error_reporter import (
    ErrorReporter,
    ErrorReport,
    AlertLevel,
    ReporterType,
    get_error_reporter,
    report_error,
    BaseReporter,
    ConsoleReporter,
    WebhookReporter,
    SlackReporter,
    EmailReporter,
    FileReporter
)

# 装饰器模块
from agent.monitoring.decorators import (
    monitor_latency,
    monitor_counter,
    monitor_both,
    trace_operation,
    monitored
)

__all__ = [
    # 追踪相关
    'TraceContext',
    'get_trace_id',
    'set_trace_id',
    'trace',
    'format_trace_log',
    
    # 指标相关
    'MetricsCollector',
    'Metric',
    'get_metrics_collector',
    'record_latency',
    'increment_counter',
    'get_all_metrics',
    
    # 错误上报相关
    'ErrorReporter',
    'ErrorReport',
    'AlertLevel',
    'ReporterType',
    'get_error_reporter',
    'report_error',
    'BaseReporter',
    'ConsoleReporter',
    'WebhookReporter',
    'SlackReporter',
    'EmailReporter',
    'FileReporter',
    
    # 装饰器
    'monitor_latency',
    'monitor_counter',
    'monitor_both',
    'trace_operation',
    'monitored'
]

__version__ = '1.1.0'
