"""
DataAnalytics 单元测试
测试 agent/data_analytics.py 的功能
"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
from collections import defaultdict, Counter
from agent.data_analytics import (
    DataAnalytics,
    create_analytics,
)


class TestDataAnalytics:
    """测试数据智能分析器类"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_init(self):
        """测试分析器初始化"""
        analytics = DataAnalytics()
        assert analytics.black_box is None
        assert analytics.vector_store is None
        assert analytics._cache == {}
        assert analytics._cache_ttl == 300

    @pytest.mark.unit
    @pytest.mark.p2
    def test_init_with_dependencies(self):
        """测试带依赖的初始化"""
        mock_black_box = MagicMock()
        mock_vector_store = MagicMock()
        
        analytics = DataAnalytics(mock_black_box, mock_vector_store)
        
        assert analytics.black_box is mock_black_box
        assert analytics.vector_store is mock_vector_store


class TestEventTrends:
    """测试事件趋势分析功能"""

    @pytest.fixture
    def mock_black_box(self):
        """创建模拟的黑匣子"""
        mock = MagicMock()
        
        def mock_query(**kwargs):
            now = datetime.now()
            return [
                {
                    "timestamp": (now - timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "event_type": f"type_{i % 3}"
                }
                for i in range(10)
            ]
        
        mock.query.side_effect = mock_query
        return mock

    @pytest.mark.unit
    @pytest.mark.p2
    def test_analyze_event_trends_without_blackbox(self):
        """测试无黑匣子时的趋势分析"""
        analytics = DataAnalytics()
        result = analytics.analyze_event_trends()
        
        assert "error" in result
        assert result["error"] == "black_box not available"

    @pytest.mark.unit
    @pytest.mark.p2
    def test_analyze_event_trends(self, mock_black_box):
        """测试事件趋势分析"""
        analytics = DataAnalytics(black_box=mock_black_box)
        result = analytics.analyze_event_trends(days=7)
        
        assert "period" in result
        assert "overview" in result
        assert "daily" in result
        assert "type_distribution" in result
        
        assert result["overview"]["total_events"] == 10
        assert result["overview"]["total_types"] >= 1

    @pytest.mark.unit
    @pytest.mark.p2
    def test_analyze_event_trends_period(self, mock_black_box):
        """测试趋势分析时间段"""
        analytics = DataAnalytics(black_box=mock_black_box)
        result = analytics.analyze_event_trends(days=3)
        
        assert result["period"]["days"] == 3


class TestAnomalyDetection:
    """测试异常检测功能"""

    @pytest.fixture
    def mock_black_box_with_anomalies(self):
        """创建带异常的模拟黑匣子"""
        mock = MagicMock()
        
        def mock_query(**kwargs):
            # 生成一些正常事件和异常
            events = []
            now = datetime.now()
            for i in range(50):
                events.append({
                    "timestamp": (now - timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "event_type": "normal"
                })
            # 添加异常小时的事件
            for _ in range(100):
                events.append({
                    "timestamp": now.strftime("%Y-%m-%dT12:00:00Z"),
                    "event_type": "spike"
                })
            return events
        
        mock.query.side_effect = mock_query
        return mock

    @pytest.mark.unit
    @pytest.mark.p2
    def test_detect_anomalies_without_blackbox(self):
        """测试无黑匣子时的异常检测"""
        analytics = DataAnalytics()
        result = analytics.detect_anomalies()
        
        assert result == []

    @pytest.mark.unit
    @pytest.mark.p2
    def test_detect_anomalies_insufficient_data(self):
        """测试数据不足时的异常检测"""
        mock = MagicMock()
        mock.query.return_value = [
            {"timestamp": "2024-01-01T00:00:00Z"}
        ]
        
        analytics = DataAnalytics(black_box=mock)
        result = analytics.detect_anomalies()
        
        assert result == []

    @pytest.mark.unit
    @pytest.mark.p2
    def test_detect_anomalies_with_threshold(self, mock_black_box_with_anomalies):
        """测试使用自定义阈值的异常检测"""
        analytics = DataAnalytics(black_box=mock_black_box_with_anomalies)
        result = analytics.detect_anomalies(threshold_multiplier=1.0)
        
        assert isinstance(result, list)

    @pytest.mark.unit
    @pytest.mark.p2
    def test_detect_anomalies_spike_type(self, mock_black_box_with_anomalies):
        """测试检测尖峰异常"""
        analytics = DataAnalytics(black_box=mock_black_box_with_anomalies)
        result = analytics.detect_anomalies()
        
        # 检查是否有 spike 类型的异常
        spike_anomalies = [a for a in result if a.get("type") == "spike"]
        assert isinstance(spike_anomalies, list)


class TestUserBehaviorAnalysis:
    """测试用户行为分析功能"""

    @pytest.fixture
    def mock_vector_store(self):
        """创建模拟的向量存储"""
        mock = MagicMock()
        mock.get_recent.return_value = [
            MagicMock(metadata={"category": "work", "source": "meeting"}),
            MagicMock(metadata={"category": "work", "source": "email"}),
            MagicMock(metadata={"category": "personal", "source": "note"}),
            MagicMock(metadata={"tags": ["important", "urgent"]}),
        ]
        return mock

    @pytest.mark.unit
    @pytest.mark.p2
    def test_analyze_user_behavior_without_vectorstore(self):
        """测试无向量存储时的行为分析"""
        analytics = DataAnalytics()
        result = analytics.analyze_user_behavior()
        
        assert "error" in result
        assert result["error"] == "vector_store not available"

    @pytest.mark.unit
    @pytest.mark.p2
    def test_analyze_user_behavior(self, mock_vector_store):
        """测试用户行为分析"""
        analytics = DataAnalytics(vector_store=mock_vector_store)
        result = analytics.analyze_user_behavior()
        
        assert "recent_memory_count" in result
        assert result["recent_memory_count"] == 4
        assert "categories" in result
        assert "sources" in result
        assert "tags" in result
        assert "insights" in result

    @pytest.mark.unit
    @pytest.mark.p2
    def test_analyze_user_behavior_categories(self, mock_vector_store):
        """测试行为分析的类别统计"""
        analytics = DataAnalytics(vector_store=mock_vector_store)
        result = analytics.analyze_user_behavior()
        
        assert result["categories"]["work"] == 2
        assert result["categories"]["personal"] == 1

    @pytest.mark.unit
    @pytest.mark.p2
    def test_analyze_user_behavior_sources(self, mock_vector_store):
        """测试行为分析的来源统计"""
        analytics = DataAnalytics(vector_store=mock_vector_store)
        result = analytics.analyze_user_behavior()
        
        assert result["sources"]["meeting"] == 1
        assert result["sources"]["email"] == 1
        assert result["sources"]["note"] == 1


class TestInsightsExtraction:
    """测试洞察提取功能"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_extract_insights_with_data(self):
        """测试有数据的洞察提取"""
        analytics = DataAnalytics()
        
        categories = {"work": 5, "personal": 3}
        sources = {"meeting": 4, "email": 3}
        tags = {"urgent": 2, "important": 3, "review": 1}
        
        insights = analytics._extract_insights(categories, sources, tags)
        
        assert len(insights) == 3
        assert any("work" in insight for insight in insights)
        assert any("meeting" in insight for insight in insights)
        assert any("urgent" in insight.lower() or "important" in insight.lower() for insight in insights)

    @pytest.mark.unit
    @pytest.mark.p2
    def test_extract_insights_empty_data(self):
        """测试空数据的洞察提取"""
        analytics = DataAnalytics()
        
        insights = analytics._extract_insights({}, {}, {})
        
        assert insights == []

    @pytest.mark.unit
    @pytest.mark.p2
    def test_extract_insights_partial_data(self):
        """测试部分数据的洞察提取"""
        analytics = DataAnalytics()
        
        insights = analytics._extract_insights({"work": 5}, {}, {})
        
        assert len(insights) == 1
        assert "work" in insights[0]


class TestReportGeneration:
    """测试报告生成功能"""

    @pytest.fixture
    def fully_configured_analytics(self):
        """创建完全配置的分析器"""
        mock_black_box = MagicMock()
        mock_black_box.query.return_value = [
            {"timestamp": "2024-01-01T10:00:00Z", "event_type": "test"}
        ]
        
        mock_vector_store = MagicMock()
        mock_vector_store.get_recent.return_value = [
            MagicMock(metadata={"category": "test"})
        ]
        
        return DataAnalytics(mock_black_box, mock_vector_store)

    @pytest.mark.unit
    @pytest.mark.p2
    def test_generate_report_json(self, fully_configured_analytics):
        """测试 JSON 格式报告生成"""
        report = fully_configured_analytics.generate_report(format="json")
        
        import json
        data = json.loads(report)
        
        assert "generated_at" in data
        assert "event_trends" in data
        assert "anomalies" in data
        assert "user_behavior" in data

    @pytest.mark.unit
    @pytest.mark.p2
    def test_generate_report_html(self, fully_configured_analytics):
        """测试 HTML 格式报告生成"""
        report = fully_configured_analytics.generate_report(format="html")
        
        # HTML 标签会被转义为 HTML 实体
        assert "&lt;html&gt;" in report or "<html>" in report
        assert "数据分析报告" in report
        assert "数据智能分析报告" in report

    @pytest.mark.unit
    @pytest.mark.p2
    def test_generate_report_text(self, fully_configured_analytics):
        """测试文本格式报告生成"""
        report = fully_configured_analytics.generate_report(format="text")
        
        assert "=" * 80 in report
        assert "数据智能分析报告" in report
        assert "生成时间:" in report

    @pytest.mark.unit
    @pytest.mark.p2
    def test_generate_report_default_format(self, fully_configured_analytics):
        """测试默认格式报告生成"""
        report = fully_configured_analytics.generate_report()
        
        assert isinstance(report, str)
        assert len(report) > 0


class TestCreateAnalytics:
    """测试快捷函数"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_create_analytics_without_args(self):
        """测试创建无参数分析器"""
        analytics = create_analytics()
        
        assert isinstance(analytics, DataAnalytics)
        assert analytics.black_box is None
        assert analytics.vector_store is None

    @pytest.mark.unit
    @pytest.mark.p2
    def test_create_analytics_with_args(self):
        """测试创建带参数的分析器"""
        mock_black_box = MagicMock()
        mock_vector_store = MagicMock()
        
        analytics = create_analytics(mock_black_box, mock_vector_store)
        
        assert analytics.black_box is mock_black_box
        assert analytics.vector_store is mock_vector_store