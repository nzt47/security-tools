"""data_analytics 边界测试

覆盖场景：empty / invalid / null / extreme
被测模块：agent.data_analytics (DataAnalytics)

【生成日志摘要】
- 生成时间：2026-07-01
- 版本：v1.0.0
- 内容：BT-012 data_analytics 边界测试，覆盖 4 类边界场景，10 个测试用例
- 关键场景：black_box/vector_store 缺失、空事件、无效参数、None 输入、极大 days
"""
import pytest
from datetime import datetime, timedelta
from collections import defaultdict

from agent.data_analytics import DataAnalytics, create_analytics, _safe_call


# ═══════════════════════════════════════════════════════════════
#  辅助 Mock 对象
# ═══════════════════════════════════════════════════════════════

class MockBlackBox:
    """模拟黑匣子日志系统

    支持 query() 方法返回事件列表。
    """

    def __init__(self, events=None):
        self._events = events or []

    def query(self, start=None, end=None, limit=10000):
        """查询事件（简化版：按 limit 截断）"""
        result = self._events[:limit]
        if start and end:
            # 简化的时间过滤（实际只检查 timestamp 字段前缀）
            result = [
                e for e in result
                if start <= e.get("timestamp", "") <= end
            ]
        return result


class MockVectorStore:
    """模拟向量存储"""

    def __init__(self, items=None):
        self._items = items or []

    def get_recent(self, limit=100):
        """获取最近的记忆项"""
        return self._items[:limit]


def _make_event(event_type="user_action", timestamp=None, **extra):
    """构造事件字典"""
    if timestamp is None:
        timestamp = datetime.now().strftime("%Y-%m-%dT%H:00:00Z")
    event = {"timestamp": timestamp, "event_type": event_type}
    event.update(extra)
    return event


def _make_memory_item(category=None, source=None, tags=None):
    """构造记忆项"""
    md = {}
    if category is not None:
        md["category"] = category
    if source is not None:
        md["source"] = source
    if tags is not None:
        md["tags"] = tags
    return {"metadata": md, "content": "test content"}


# ═══════════════════════════════════════════════════════════════
#  TestEmptyBoundary — 空值/空容器边界
# ═══════════════════════════════════════════════════════════════

class TestEmptyBoundary:
    """空值边界测试"""

    def test_empty_black_box_analyze_trends_returns_error(self):
        """black_box=None 时 analyze_event_trends 返回错误字典"""
        analytics = DataAnalytics(black_box=None, vector_store=None)
        result = analytics.analyze_event_trends()
        assert result == {"error": "black_box not available"}

    def test_empty_vector_store_analyze_behavior_returns_error(self):
        """vector_store=None 时 analyze_user_behavior 返回错误字典"""
        analytics = DataAnalytics(black_box=None, vector_store=None)
        result = analytics.analyze_user_behavior()
        assert result == {"error": "vector_store not available"}

    def test_empty_events_detect_anomalies_returns_empty(self):
        """空事件列表时 detect_anomalies 返回空列表

        注：black_box.query 返回空列表 → hourly_counts 为空 →
        len(hourly_counts) < 2 → 返回 []
        """
        empty_black_box = MockBlackBox(events=[])
        analytics = DataAnalytics(black_box=empty_black_box, vector_store=None)
        result = analytics.detect_anomalies()
        assert result == []

    def test_empty_events_analyze_trends_returns_zero_overview(self):
        """空事件列表时 analyze_event_trends 返回 total_events=0"""
        empty_black_box = MockBlackBox(events=[])
        analytics = DataAnalytics(black_box=empty_black_box, vector_store=None)
        result = analytics.analyze_event_trends()
        assert result["overview"]["total_events"] == 0
        assert result["overview"]["total_types"] == 0


# ═══════════════════════════════════════════════════════════════
#  TestInvalidBoundary — 非法输入边界
# ═══════════════════════════════════════════════════════════════

class TestInvalidBoundary:
    """非法输入边界测试"""

    def test_invalid_days_zero_analyze_trends(self):
        """days=0 时仍能执行（start_date == end_date，查询范围为零）"""
        black_box = MockBlackBox(events=[_make_event()])
        analytics = DataAnalytics(black_box=black_box, vector_store=None)
        # days=0 不应抛异常，返回正常结构
        result = analytics.analyze_event_trends(days=0)
        assert "period" in result
        assert result["period"]["days"] == 0

    def test_invalid_threshold_zero_detect_anomalies(self):
        """threshold_multiplier=0 时仍能执行（threshold = avg）"""
        # 构造足够多的事件以通过 len(hourly_counts) >= 2 检查
        now = datetime.now()
        events = [
            _make_event(timestamp=now.strftime("%Y-%m-%dT%H:00:00Z")),
            _make_event(timestamp=(now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:00:00Z")),
        ]
        black_box = MockBlackBox(events=events)
        analytics = DataAnalytics(black_box=black_box, vector_store=None)
        # threshold_multiplier=0 不应抛异常
        result = analytics.detect_anomalies(threshold_multiplier=0.0)
        assert isinstance(result, list)

    def test_invalid_format_generate_report_falls_back_to_text(self):
        """format="unknown" 时回退到文本格式（else 分支）"""
        black_box = MockBlackBox(events=[])
        analytics = DataAnalytics(black_box=black_box, vector_store=None)
        report = analytics.generate_report(format="unknown_format")
        assert isinstance(report, str)
        assert "数据智能分析报告" in report

    def test_invalid_format_empty_string_generate_report(self):
        """format="" 时回退到文本格式"""
        black_box = MockBlackBox(events=[])
        analytics = DataAnalytics(black_box=black_box, vector_store=None)
        report = analytics.generate_report(format="")
        assert isinstance(report, str)
        assert "数据智能分析报告" in report


# ═══════════════════════════════════════════════════════════════
#  TestNullBoundary — None 输入边界
# ═══════════════════════════════════════════════════════════════

class TestNullBoundary:
    """None 输入边界测试"""

    def test_null_black_box_all_methods_safe(self):
        """black_box=None 时所有方法安全返回（不抛异常）"""
        analytics = DataAnalytics(black_box=None, vector_store=None)
        # analyze_event_trends → 错误字典
        assert analytics.analyze_event_trends() == {"error": "black_box not available"}
        # detect_anomalies → 空列表
        assert analytics.detect_anomalies() == []
        # generate_report → 仍能生成报告（内部调用上述方法）
        report = analytics.generate_report()
        assert isinstance(report, str)

    def test_null_vector_store_analyze_behavior_safe(self):
        """vector_store=None 时 analyze_user_behavior 安全返回错误字典"""
        analytics = DataAnalytics(black_box=None, vector_store=None)
        result = analytics.analyze_user_behavior()
        assert result == {"error": "vector_store not available"}

    def test_create_analytics_with_null_args(self):
        """create_analytics(None, None) 应返回有效实例"""
        analytics = create_analytics(black_box=None, vector_store=None)
        assert analytics is not None
        assert analytics.black_box is None
        assert analytics.vector_store is None


# ═══════════════════════════════════════════════════════════════
#  TestExtremeBoundary — 极值边界
# ═══════════════════════════════════════════════════════════════

class TestExtremeBoundary:
    """极值边界测试"""

    def test_extreme_large_days_analyze_trends(self):
        """days=36500（100 年）时不报错

        注：源码 analyze_event_trends 用 timedelta(days=days) 计算 start_date，
        days 超过约 365000 时会触发 OverflowError（datetime 范围溢出）。
        此处用 36500（100 年）验证极大但合理的值能正常工作。
        已知缺陷：源码未对 days 上限做防御，后续应添加校验。
        """
        black_box = MockBlackBox(events=[_make_event()])
        analytics = DataAnalytics(black_box=black_box, vector_store=None)
        result = analytics.analyze_event_trends(days=36500)
        assert result["period"]["days"] == 36500
        # start_date 会是 100 年前，但仍能正常计算
        assert "start" in result["period"]

    def test_extreme_overflow_days_raises(self):
        """days=999999 触发 OverflowError（边界缺陷验证）

        验证源码边界缺陷：days 过大时 timedelta(days=days) 导致
        end_date - timedelta 超出 datetime 范围，抛出 OverflowError。
        此测试记录该缺陷，供后续修复时回归验证。
        """
        black_box = MockBlackBox(events=[_make_event()])
        analytics = DataAnalytics(black_box=black_box, vector_store=None)
        with pytest.raises(OverflowError):
            analytics.analyze_event_trends(days=999999)

    def test_extreme_large_threshold_detect_anomalies(self):
        """threshold_multiplier=999999 时几乎不会检测到异常"""
        now = datetime.now()
        events = [
            _make_event(timestamp=now.strftime("%Y-%m-%dT%H:00:00Z")),
            _make_event(timestamp=(now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:00:00Z")),
        ]
        black_box = MockBlackBox(events=events)
        analytics = DataAnalytics(black_box=black_box, vector_store=None)
        # 极大阈值 → threshold 极高 → 几乎无异常
        result = analytics.detect_anomalies(threshold_multiplier=999999.0)
        assert isinstance(result, list)
        # 大概率为空（因为 threshold 过高）
        # 但不强制断言长度，因为 lull 类型异常可能存在


# ═══════════════════════════════════════════════════════════════
#  TestSafeCallBoundary — _safe_call 辅助函数边界
# ═══════════════════════════════════════════════════════════════

class TestSafeCallBoundary:
    """_safe_call 辅助函数边界测试"""

    def test_safe_call_normal_return(self):
        """_safe_call 正常调用应返回函数结果"""
        def add(a, b):
            return a + b
        result = _safe_call(add, 1, 2, action="test_add")
        assert result == 3

    def test_safe_call_propagates_exception(self):
        """_safe_call 捕获异常后重新抛出（边界显性化原则）"""
        def fail():
            raise ValueError("测试失败")
        # _safe_call 应记录日志后重新抛出，而非静默吞掉
        with pytest.raises(ValueError, match="测试失败"):
            _safe_call(fail, action="test_fail")
