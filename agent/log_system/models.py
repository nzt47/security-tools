"""
数据模型 — 日志系统的核心类型定义
"""

import json
import time
from enum import Enum
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any, List
from datetime import datetime


class LogLevel(Enum):
    """日志级别"""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class LogCategory(Enum):
    """日志分类"""
    OPERATION = "operation"       # 操作记录
    PERFORMANCE = "performance"   # 性能指标
    ERROR = "error"               # 错误信息
    BEHAVIOR = "behavior"         # 用户行为
    SYSTEM = "system"             # 系统事件
    INSIGHT = "insight"           # 内省洞察


@dataclass
class LogEntry:
    """通用日志条目"""
    category: LogCategory
    level: LogLevel = LogLevel.INFO
    message: str = ""
    source: str = ""                    # 来源模块名
    timestamp: float = field(default_factory=time.time)
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    trace_id: str = ""                  # 关联追踪ID
    user_id: str = ""                   # 关联用户
    duration_ms: float = 0.0            # 操作耗时

    def to_dict(self) -> dict:
        """序列化为字典"""
        d = asdict(self)
        d['category'] = self.category.value
        d['level'] = self.level.value
        d['datetime'] = datetime.fromtimestamp(self.timestamp).isoformat()
        return d

    def to_json(self) -> str:
        """序列化为 JSON 字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict) -> 'LogEntry':
        """从字典反序列化"""
        category = LogCategory(data.pop('category', 'operation'))
        level = LogLevel(data.pop('level', 'info'))
        data.pop('datetime', None)
        return cls(category=category, level=level, **data)


@dataclass
class PerformanceRecord:
    """性能记录"""
    metric_name: str                     # 指标名称
    value: float                         # 指标值
    unit: str = "ms"                     # 单位
    timestamp: float = field(default_factory=time.time)
    tags: Dict[str, str] = field(default_factory=dict)
    source: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d['datetime'] = datetime.fromtimestamp(self.timestamp).isoformat()
        return d


@dataclass
class ErrorRecord:
    """错误记录"""
    message: str
    severity: str = "error"              # error / warning / critical
    source: str = ""
    timestamp: float = field(default_factory=time.time)
    exception_type: str = ""
    traceback: str = ""
    context: Dict[str, Any] = field(default_factory=dict)
    resolved: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d['datetime'] = datetime.fromtimestamp(self.timestamp).isoformat()
        return d


@dataclass
class BehaviorRecord:
    """用户行为记录"""
    user_id: str
    action_type: str                     # 行为类型
    session_id: str = ""
    timestamp: float = field(default_factory=time.time)
    payload: Dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d['datetime'] = datetime.fromtimestamp(self.timestamp).isoformat()
        return d


@dataclass
class Insight:
    """内省洞察"""
    type: str                            # pattern / trend / anomaly / optimization
    summary: str                         # 简短摘要
    detail: str = ""                     # 详细分析
    confidence: float = 0.0              # 置信度 0-1
    evidence: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    generated_at: float = field(default_factory=time.time)
    source_analysis: str = ""            # LLM 或规则引擎

    def to_dict(self) -> dict:
        d = asdict(self)
        d['generated_at_iso'] = datetime.fromtimestamp(self.generated_at).isoformat()
        return d


@dataclass
class ActionItem:
    """行动建议"""
    priority: str                        # high / medium / low
    category: str                        # performance / reliability / ux / security
    title: str
    description: str = ""
    rationale: str = ""
    expected_impact: str = ""
    effort: str = ""                     # small / medium / large
    status: str = "open"                 # open / in_progress / done / dismissed
    created_at: float = field(default_factory=time.time)
    insight_id: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d['created_at_iso'] = datetime.fromtimestamp(self.created_at).isoformat()
        return d


@dataclass
class KnowledgeFinding:
    """知识发现"""
    domain: str                          # system_behavior / user_pattern / error_pattern
    finding: str                         # 认知发现内容
    tags: List[str] = field(default_factory=list)
    confidence: float = 0.0
    evidence_refs: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = asdict(self)
        d['created_at_iso'] = datetime.fromtimestamp(self.created_at).isoformat()
        return d


@dataclass
class LogQuery:
    """日志查询参数"""
    categories: List[LogCategory] = field(default_factory=list)
    levels: List[LogLevel] = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0
    source: str = ""
    user_id: str = ""
    tags: List[str] = field(default_factory=list)
    text_search: str = ""
    limit: int = 100
    offset: int = 0
    order_by: str = "timestamp"
    order_desc: bool = True


@dataclass
class LogStats:
    """日志统计"""
    total_count: int = 0
    by_category: Dict[str, int] = field(default_factory=dict)
    by_level: Dict[str, int] = field(default_factory=dict)
    top_sources: List[tuple] = field(default_factory=list)
    error_rate: float = 0.0
    avg_duration_ms: float = 0.0
    p95_duration_ms: float = 0.0
    p99_duration_ms: float = 0.0
    time_range_hours: float = 0.0
