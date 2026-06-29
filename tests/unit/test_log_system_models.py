"""Log System 数据模型单元测试"""
import time
import json

import pytest
from agent.log_system.models import (
    LogEntry, LogLevel, LogCategory, PerformanceRecord, ErrorRecord,
    BehaviorRecord, Insight, ActionItem, KnowledgeFinding, LogQuery, LogStats,
)


class TestLogLevel:
    """LogLevel 枚举测试"""

    def test_values(self):
        assert LogLevel.DEBUG.value == "debug"
        assert LogLevel.INFO.value == "info"
        assert LogLevel.WARNING.value == "warning"
        assert LogLevel.ERROR.value == "error"
        assert LogLevel.CRITICAL.value == "critical"


class TestLogCategory:
    """LogCategory 枚举测试"""

    def test_values(self):
        assert LogCategory.OPERATION.value == "operation"
        assert LogCategory.PERFORMANCE.value == "performance"
        assert LogCategory.ERROR.value == "error"
        assert LogCategory.BEHAVIOR.value == "behavior"
        assert LogCategory.SYSTEM.value == "system"
        assert LogCategory.INSIGHT.value == "insight"


class TestLogEntry:
    """LogEntry 数据类测试"""

    def test_create_minimal(self):
        entry = LogEntry(category=LogCategory.OPERATION)
        assert entry.category == LogCategory.OPERATION
        assert entry.level == LogLevel.INFO
        assert entry.message == ""
        assert entry.tags == []
        assert entry.metadata == {}

    def test_create_full(self):
        entry = LogEntry(
            category=LogCategory.ERROR,
            level=LogLevel.CRITICAL,
            message="系统错误",
            source="test_module",
            tags=["critical", "system"],
            metadata={"code": 500},
            trace_id="trace_001",
            user_id="user_1",
            duration_ms=150.0,
        )
        assert entry.message == "系统错误"
        assert entry.trace_id == "trace_001"
        assert entry.duration_ms == 150.0

    def test_to_dict(self):
        entry = LogEntry(category=LogCategory.OPERATION, message="测试")
        d = entry.to_dict()
        assert d["category"] == "operation"
        assert d["level"] == "info"
        assert "datetime" in d
        assert d["message"] == "测试"

    def test_to_json(self):
        entry = LogEntry(category=LogCategory.OPERATION, message="JSON测试")
        json_str = entry.to_json()
        parsed = json.loads(json_str)
        assert parsed["message"] == "JSON测试"
        assert parsed["category"] == "operation"

    def test_from_dict(self):
        data = {
            "category": "error",
            "level": "warning",
            "message": "反序列化测试",
            "source": "test",
            "tags": ["a"],
        }
        entry = LogEntry.from_dict(data)
        assert entry.category == LogCategory.ERROR
        assert entry.level == LogLevel.WARNING
        assert entry.message == "反序列化测试"
        assert entry.source == "test"

    def test_from_dict_roundtrip(self):
        original = LogEntry(
            category=LogCategory.PERFORMANCE,
            level=LogLevel.INFO,
            message="往返测试",
            source="perf",
            duration_ms=42.0,
        )
        d = original.to_dict()
        restored = LogEntry.from_dict(d)
        assert restored.category == original.category
        assert restored.message == original.message
        assert restored.duration_ms == original.duration_ms


class TestPerformanceRecord:
    """PerformanceRecord 测试"""

    def test_create(self):
        pr = PerformanceRecord(metric_name="latency", value=200.5, unit="ms")
        assert pr.metric_name == "latency"
        assert pr.value == 200.5
        assert pr.unit == "ms"
        assert pr.tags == {}

    def test_to_dict(self):
        pr = PerformanceRecord(metric_name="cpu", value=75.0, unit="%")
        d = pr.to_dict()
        assert d["metric_name"] == "cpu"
        assert "datetime" in d


class TestErrorRecord:
    """ErrorRecord 测试"""

    def test_create(self):
        er = ErrorRecord(message="出错了", severity="critical")
        assert er.message == "出错了"
        assert er.severity == "critical"
        assert er.resolved is False

    def test_to_dict(self):
        er = ErrorRecord(message="test error", source="module_x")
        d = er.to_dict()
        assert d["message"] == "test error"
        assert "datetime" in d


class TestBehaviorRecord:
    """BehaviorRecord 测试"""

    def test_create(self):
        br = BehaviorRecord(user_id="u1", action_type="click", session_id="s1")
        assert br.user_id == "u1"
        assert br.action_type == "click"

    def test_to_dict(self):
        br = BehaviorRecord(user_id="u1", action_type="search", payload={"q": "test"})
        d = br.to_dict()
        assert d["user_id"] == "u1"


class TestInsight:
    """Insight 测试"""

    def test_create(self):
        insight = Insight(type="pattern", summary="发现模式", confidence=0.85)
        assert insight.type == "pattern"
        assert insight.confidence == 0.85
        assert insight.tags == []

    def test_to_dict(self):
        i = Insight(type="anomaly", summary="异常检测", detail="详情")
        d = i.to_dict()
        assert "generated_at_iso" in d


class TestActionItem:
    """ActionItem 测试"""

    def test_create(self):
        ai = ActionItem(priority="high", category="performance", title="优化查询")
        assert ai.priority == "high"
        assert ai.status == "open"

    def test_to_dict(self):
        ai = ActionItem(priority="low", category="ux", title="改进界面")
        d = ai.to_dict()
        assert "created_at_iso" in d


class TestKnowledgeFinding:
    """KnowledgeFinding 测试"""

    def test_create(self):
        kf = KnowledgeFinding(domain="user_pattern", finding="用户经常搜索X")
        assert kf.domain == "user_pattern"
        assert kf.tags == []

    def test_to_dict(self):
        kf = KnowledgeFinding(domain="error_pattern", finding="某错误频繁出现")
        d = kf.to_dict()
        assert "created_at_iso" in d


class TestLogQuery:
    """LogQuery 测试"""

    def test_defaults(self):
        q = LogQuery()
        assert q.limit == 100
        assert q.offset == 0
        assert q.order_desc is True


class TestLogStats:
    """LogStats 测试"""

    def test_defaults(self):
        s = LogStats()
        assert s.total_count == 0
        assert s.by_category == {}
        assert s.by_level == {}
