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
