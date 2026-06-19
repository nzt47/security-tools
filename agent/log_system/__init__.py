"""
云枢日志系统 — 采集、存储、分析、内省、展现

功能：
- 多维度日志数据采集（操作、性能、错误、行为、系统事件）
- SQLite + JSONL 混合存储，支持结构化与非结构化数据
- 两阶段分析引擎（规则预过滤 -> 统计深度分析）
- 内省式学习机制（空闲时段自动提炼洞察与行动建议）
- Web 仪表盘与 REST API 双展现通道
"""

from .models import (
    LogLevel, LogCategory, LogEntry,
    PerformanceRecord, ErrorRecord, BehaviorRecord,
    Insight, ActionItem, KnowledgeFinding,
    LogQuery, LogStats,
)
from .storage import LogStorage, get_storage
from .collectors import (
    OperationCollector, PerformanceCollector,
    ErrorCollector, BehaviorCollector, SystemEventCollector,
    log_operation,
)
from .analyzer import LogAnalyzer
from .introspection import IntrospectionEngine


def init_log_system(db_path=None, raw_log_dir=None):
    """初始化日志系统"""
    from .storage import LogStorage, _set_storage
    storage = LogStorage(db_path=db_path, raw_log_dir=raw_log_dir)
    storage.initialize()
    _set_storage(storage)
    return storage


__all__ = [
    'LogLevel', 'LogCategory', 'LogEntry',
    'PerformanceRecord', 'ErrorRecord', 'BehaviorRecord',
    'Insight', 'ActionItem', 'KnowledgeFinding',
    'LogQuery', 'LogStats',
    'LogStorage',
    'OperationCollector', 'PerformanceCollector',
    'ErrorCollector', 'BehaviorCollector', 'SystemEventCollector',
    'log_operation',
    'LogAnalyzer',
    'IntrospectionEngine',
    'init_log_system', 'get_storage',
]
