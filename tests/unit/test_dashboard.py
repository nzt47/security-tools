#!/usr/bin/env python3
"""
仪表盘 API 单元测试

测试覆盖：
1. 质量监控端点 (/api/dashboard/quality)
2. 链路追踪端点 (/api/dashboard/traces)
3. Memory 使用端点 (/api/dashboard/memory)
4. 健康检查端点 (/api/dashboard/health)
"""

import pytest
import json
import time
from unittest.mock import patch, MagicMock


class TestDashboardHealth:
    """仪表盘健康检查测试"""
    
    def test_health_endpoint_returns_healthy(self):
        """测试健康检查端点返回健康状态"""
        from agent.server_routes.routes_dashboard import _get_dashboard_health
        
        data = _get_dashboard_health()
        
        assert data["status"] == "healthy"
        assert data["services"]["tracing"] is True
        assert data["services"]["metrics"] is True
        assert data["services"]["memory"] is True
        assert data["services"]["failure_analysis"] is True
        assert "timestamp" in data
    
    def test_health_endpoint_has_timestamp(self):
        """测试健康检查端点包含时间戳"""
        from agent.server_routes.routes_dashboard import _get_dashboard_health
        
        data = _get_dashboard_health()
        
        assert isinstance(data["timestamp"], float)
        assert data["timestamp"] > 0


class TestQualityMetrics:
    """质量监控指标测试"""
    
    def test_parse_time_range_today(self):
        """测试解析今日时间范围"""
        from agent.server_routes.routes_dashboard import _parse_time_range
        from datetime import datetime
        
        start_time, end_time = _parse_time_range("today")
        now = datetime.now()
        
        # 开始时间应该是今天零点
        start_dt = datetime.fromtimestamp(start_time)
        assert start_dt.hour == 0
        assert start_dt.minute == 0
        assert start_dt.second == 0
        
        # 结束时间应该接近当前时间
        assert end_time > start_time
    
    def test_parse_time_range_week(self):
        """测试解析本周时间范围"""
        from agent.server_routes.routes_dashboard import _parse_time_range
        from datetime import datetime
        
        start_time, end_time = _parse_time_range("week")
        start_dt = datetime.fromtimestamp(start_time)
        
        # 开始时间应该是本周一零点
        assert start_dt.weekday() == 0  # Monday
        assert start_dt.hour == 0
    
    def test_parse_time_range_month(self):
        """测试解析本月时间范围"""
        from agent.server_routes.routes_dashboard import _parse_time_range
        from datetime import datetime
        
        start_time, end_time = _parse_time_range("month")
        start_dt = datetime.fromtimestamp(start_time)
        
        # 开始时间应该是本月一号零点
        assert start_dt.day == 1
        assert start_dt.hour == 0
    
    def test_get_schema_validation_stats(self):
        """测试获取 Schema 验证统计"""
        from agent.server_routes.routes_dashboard import _get_schema_validation_stats
        
        stats = _get_schema_validation_stats(0, time.time())
        
        assert "total_validations" in stats
        assert "successful_validations" in stats
        assert "failed_validations" in stats
        assert "pass_rate" in stats
        assert "trend" in stats
        
        assert stats["total_validations"] >= 0
        assert stats["successful_validations"] >= 0
        assert stats["failed_validations"] >= 0
        assert 0 <= stats["pass_rate"] <= 100
        assert len(stats["trend"]) > 0
    
    def test_get_critic_stats(self):
        """测试获取 Critic 评分统计"""
        from agent.server_routes.routes_dashboard import _get_critic_stats
        
        stats = _get_critic_stats(0, time.time())
        
        assert "total_evaluations" in stats
        assert "average_score" in stats
        assert "score_distribution" in stats
        assert "trend" in stats
        
        assert stats["total_evaluations"] >= 0
        assert 0 <= stats["average_score"] <= 100
        assert len(stats["trend"]) > 0
    
    def test_get_failure_distribution(self):
        """测试获取失败模式分布"""
        from agent.server_routes.routes_dashboard import _get_failure_distribution
        
        distribution = _get_failure_distribution(0, time.time())
        
        assert "total_failures" in distribution
        assert "distribution" in distribution
        assert "top_errors" in distribution
        assert isinstance(distribution["total_failures"], int)


class TestTraceEndpoints:
    """链路追踪端点测试"""
    
    def test_generate_mock_traces(self):
        """测试生成模拟追踪数据"""
        from agent.server_routes.routes_dashboard import _generate_mock_traces
        
        traces = _generate_mock_traces(5)
        
        assert len(traces) == 5
        for trace in traces:
            assert "trace_id" in trace
            assert "service" in trace
            assert "operation" in trace
            assert "status" in trace
            assert "duration_ms" in trace
            assert "timestamp" in trace
    
    def test_generate_mock_trace_detail(self):
        """测试生成模拟追踪详情"""
        from agent.server_routes.routes_dashboard import _generate_mock_trace_detail
        
        trace_id = "test1234567890abcd"
        detail = _generate_mock_trace_detail(trace_id)
        
        assert detail["trace_id"] == trace_id
        assert "spans" in detail
        assert "duration_ms" in detail
        assert len(detail["spans"]) > 0
        
        for span in detail["spans"]:
            assert "span_id" in span
            assert "name" in span
            assert "duration_ms" in span
            assert "status" in span


class TestMemoryEndpoints:
    """Memory 使用端点测试"""
    
    def test_get_long_term_memory_stats(self):
        """测试获取长期记忆统计"""
        from agent.server_routes.routes_dashboard import _get_long_term_memory_stats
        
        stats = _get_long_term_memory_stats()
        
        assert "total_count" in stats
        assert "total_size_mb" in stats
        assert "average_size_kb" in stats
        assert "growth_trend" in stats
        assert "last_updated" in stats
        
        assert isinstance(stats["total_count"], int)
        assert isinstance(stats["total_size_mb"], float)
        assert len(stats["growth_trend"]) == 8  # 7天 + 当前
    
    def test_get_short_term_memory_stats(self):
        """测试获取临时记忆统计"""
        from agent.server_routes.routes_dashboard import _get_short_term_memory_stats
        
        stats = _get_short_term_memory_stats()
        
        assert "total_count" in stats
        assert "active_sessions" in stats
        assert "average_age_minutes" in stats
        assert "growth_trend" in stats
        
        assert isinstance(stats["active_sessions"], int)
    
    def test_get_hit_rate_stats(self):
        """测试获取命中率统计"""
        from agent.server_routes.routes_dashboard import _get_hit_rate_stats
        
        stats = _get_hit_rate_stats()
        
        assert "overall_hit_rate" in stats
        assert "long_term_hit_rate" in stats
        assert "short_term_hit_rate" in stats
        assert "total_requests" in stats
        assert "cache_hits" in stats
        assert "trend" in stats
        
        assert 0 <= stats["overall_hit_rate"] <= 100
    
    def test_get_memory_category_distribution(self):
        """测试获取记忆分类分布"""
        from agent.server_routes.routes_dashboard import _get_memory_category_distribution
        
        categories = _get_memory_category_distribution()
        
        assert len(categories) == 5
        for cat in categories:
            assert "name" in cat
            assert "count" in cat
            assert "percentage" in cat
        
        # 百分比总和应该为 100
        total_percentage = sum(cat["percentage"] for cat in categories)
        assert total_percentage == 100
    
    def test_get_recent_memory_access(self):
        """测试获取最近访问记录"""
        from agent.server_routes.routes_dashboard import _get_recent_memory_access
        
        records = _get_recent_memory_access()
        
        assert len(records) == 10
        for record in records:
            assert "id" in record
            assert "type" in record
            assert "category" in record
            assert "content" in record
            assert "timestamp" in record
            assert "duration_ms" in record


class TestDashboardIntegration:
    """仪表盘集成测试"""
    
    def test_quality_metrics_has_required_fields(self):
        """测试质量指标包含所有必需字段"""
        from agent.server_routes.routes_dashboard import _get_quality_metrics
        
        result = _get_quality_metrics(0, time.time())
        
        assert "schema_validation" in result
        assert "critic_scores" in result
        assert "failure_distribution" in result
        assert "time_range" in result
        assert "generated_at" in result
    
    def test_memory_stats_has_required_fields(self):
        """测试内存统计包含所有必需字段"""
        from agent.server_routes.routes_dashboard import _get_memory_stats
        
        result = _get_memory_stats()
        
        assert "long_term" in result
        assert "short_term" in result
        assert "hit_rate" in result
        assert "category_distribution" in result
        assert "recent_access" in result
        assert "generated_at" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
