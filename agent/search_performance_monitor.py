"""
搜索引擎性能检测模块 - 薄包装
实际代码位于 agent/monitoring/search.py
"""
from agent.monitoring.search import (
    SearchPerformanceMonitor,
    get_performance_monitor,
    start_performance_monitor,
    stop_performance_monitor,
    run_manual_performance_check,
    get_performance_monitor_status,
    get_performance_history,
    get_performance_summary,
)

__all__ = [
    "SearchPerformanceMonitor",
    "get_performance_monitor",
    "start_performance_monitor",
    "stop_performance_monitor",
    "run_manual_performance_check",
    "get_performance_monitor_status",
    "get_performance_history",
    "get_performance_summary",
]
