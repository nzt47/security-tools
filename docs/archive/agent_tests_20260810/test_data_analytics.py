"""DataAnalytics 单元测试"""
import pytest
from agent.data_analytics import DataAnalytics, create_analytics


class MockBlackBox:
    """模拟黑匣子日志系统"""
    def __init__(self, events=None):
        self.events = events or []
    
    def query(self, start=None, end=None, limit=1000):
        return self.events


class MemoryItem:
    """模拟记忆项对象"""
    def __init__(self, metadata):
        self.metadata = metadata


class MockVectorStore:
    """模拟向量存储"""
    def __init__(self, memories=None):
        self.memories = []
        for m in memories or []:
            self.memories.append(MemoryItem(m.get("metadata", {})))
    
    def get_recent(self, limit=100):
        return self.memories[:limit]


def test_analytics_initialization():
    """初始化数据分析器"""
    analytics = DataAnalytics()
    assert analytics.black_box is None
    assert analytics.vector_store is None


def test_analytics_with_mocks():
    """使用模拟对象初始化"""
    mock_bb = MockBlackBox()
    mock_vs = MockVectorStore()
    
    analytics = DataAnalytics(black_box=mock_bb, vector_store=mock_vs)
    
    assert analytics.black_box is mock_bb
    assert analytics.vector_store is mock_vs


def test_analyze_event_trends_no_black_box():
    """无黑匣子时返回错误"""
    analytics = DataAnalytics()
    result = analytics.analyze_event_trends()
    
    assert "error" in result
    assert result["error"] == "black_box not available"


def test_analyze_event_trends_with_data():
    """分析事件趋势"""
    events = [
        {"timestamp": "2024-01-01T10:00:00Z", "event_type": "task_start"},
        {"timestamp": "2024-01-01T11:00:00Z", "event_type": "task_end"},
        {"timestamp": "2024-01-02T09:00:00Z", "event_type": "task_start"},
    ]
    
    mock_bb = MockBlackBox(events=events)
    analytics = DataAnalytics(black_box=mock_bb)
    
    result = analytics.analyze_event_trends(days=7)
    
    assert "period" in result
    assert result["overview"]["total_events"] == 3
    assert len(result["type_distribution"]) == 2


def test_detect_anomalies_no_black_box():
    """无黑匣子时返回空列表"""
    analytics = DataAnalytics()
    result = analytics.detect_anomalies()
    
    assert result == []


def test_detect_anomalies_with_insufficient_data():
    """数据不足时返回空列表"""
    events = [{"timestamp": "2024-01-01T10:00:00Z"}]
    mock_bb = MockBlackBox(events=events)
    analytics = DataAnalytics(black_box=mock_bb)
    
    result = analytics.detect_anomalies()
    
    assert result == []


def test_detect_anomalies_with_data():
    """检测异常"""
    events = []
    for i in range(24):
        hour = f"2024-01-01T{i:02d}:00:00Z"
        # 创建一个峰值
        count = 100 if i == 12 else 10
        events.extend([{"timestamp": hour}] * count)
    
    mock_bb = MockBlackBox(events=events)
    analytics = DataAnalytics(black_box=mock_bb)
    
    result = analytics.detect_anomalies()
    
    assert isinstance(result, list)


def test_analyze_user_behavior_no_vector_store():
    """无向量存储时返回错误"""
    analytics = DataAnalytics()
    result = analytics.analyze_user_behavior()
    
    assert "error" in result
    assert result["error"] == "vector_store not available"


def test_analyze_user_behavior_with_data():
    """分析用户行为"""
    memories = [
        {"metadata": {"category": "work", "source": "email", "tags": ["urgent", "work"]}},
        {"metadata": {"category": "personal", "source": "chat", "tags": ["family"]}},
        {"metadata": {"category": "work", "source": "email", "tags": ["meeting"]}},
    ]
    
    mock_vs = MockVectorStore(memories=memories)
    analytics = DataAnalytics(vector_store=mock_vs)
    
    result = analytics.analyze_user_behavior()
    
    assert result["recent_memory_count"] == 3
    assert result["categories"]["work"] == 2
    assert "insights" in result


def test_generate_report_text():
    """生成文本格式报告"""
    analytics = DataAnalytics()
    report = analytics.generate_report(format="text")
    
    assert isinstance(report, str)
    assert "数据智能分析报告" in report


def test_generate_report_json():
    """生成JSON格式报告"""
    analytics = DataAnalytics()
    report = analytics.generate_report(format="json")
    
    import json
    result = json.loads(report)
    
    assert isinstance(result, dict)
    assert "generated_at" in result


def test_generate_report_html():
    """生成HTML格式报告"""
    analytics = DataAnalytics()
    report = analytics.generate_report(format="html")
    
    assert isinstance(report, str)
    # HTML内容被转义，所以检查转义后的形式
    assert "&lt;html&gt;" in report or "<html>" in report


def test_create_analytics():
    """创建数据分析实例（快捷函数）"""
    analytics = create_analytics()
    
    assert isinstance(analytics, DataAnalytics)


def test_extract_insights():
    """提取洞察"""
    categories = {"work": 10, "personal": 5}
    sources = {"email": 8, "chat": 7}
    tags = {"urgent": 6, "meeting": 4, "family": 3}
    
    analytics = DataAnalytics()
    insights = analytics._extract_insights(categories, sources, tags)
    
    assert isinstance(insights, list)
    assert len(insights) >= 1