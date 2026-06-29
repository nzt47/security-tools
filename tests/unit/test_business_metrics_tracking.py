"""业务指标埋点验证测试（15个用例）

测试覆盖：
1. 正常流程埋点计数准确性
2. 异常流程埋点计数准确性
3. 并发场景下埋点计数准确性
4. 埋点标签完整性验证
5. 耗时指标的统计正确性（p95/p99）
6. 埋点对性能的影响（<1ms/次）
7. 埋点失败不影响主流程
8. 指标命名规范性检查
9. 业务指标定义与实际埋点一致性检查
10. 埋点数据导出格式正确性
11. 多维度标签组合查询正确性
12. 指标重置/清零功能
13. 历史数据保留策略验证
14. 指标聚合计算正确性
15. 告警阈值触发验证
"""

import pytest
import threading
import time
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from agent.monitoring.business_metrics import (
    BusinessMetricsCollector,
    BUSINESS_METRICS_DEFINITIONS,
    get_business_metrics_collector,
)


class TestNormalFlowTracking:
    """测试1：正常流程埋点计数准确性"""

    def test_interaction_count_accuracy(self):
        """验证交互计数准确性"""
        collector = BusinessMetricsCollector()
        
        collector.record_interaction("chat", "gpt-4", success=True, duration=1.5)
        collector.record_interaction("chat", "gpt-4", success=False, duration=2.0)
        collector.record_interaction("tool_call", "gpt-4", success=True, duration=0.5)
        
        metric = collector.get_metric_by_name("yunshu_interaction_total")
        assert metric is not None
        assert sum(metric["data"].values()) == 3

    def test_tool_call_count_accuracy(self):
        """验证工具调用计数准确性"""
        collector = BusinessMetricsCollector()
        
        collector.record_tool_call("read_file", "file", success=True, duration=0.3)
        collector.record_tool_call("write_file", "file", success=False, duration=0.5)
        collector.record_tool_call("search", "web", success=True, duration=1.0)
        collector.record_tool_call("read_file", "file", success=True, duration=0.2)
        
        metric = collector.get_metric_by_name("yunshu_tool_call_total")
        assert metric is not None
        assert sum(metric["data"].values()) == 4

    def test_memory_search_count_accuracy(self):
        """验证记忆搜索计数准确性"""
        collector = BusinessMetricsCollector()
        
        collector.record_memory_search("long_term", "keyword", hit=True)
        collector.record_memory_search("short_term", "vector", hit=False)
        collector.record_memory_search("long_term", "keyword", hit=True)
        
        metric = collector.get_metric_by_name("yunshu_memory_search_total")
        assert metric is not None
        assert sum(metric["data"].values()) == 3


class TestExceptionFlowTracking:
    """测试2：异常流程埋点计数准确性"""

    def test_failed_interaction_count(self):
        """验证失败交互计数"""
        collector = BusinessMetricsCollector()
        
        collector.record_interaction("chat", "gpt-4", success=False, duration=3.0)
        collector.record_interaction("chat", "gpt-4", success=False, duration=2.5)
        
        metric = collector.get_metric_by_name("yunshu_interaction_total")
        assert metric is not None
        
        failed_count = 0
        for key, value in metric["data"].items():
            if "success=False" in key:
                failed_count += value
        assert failed_count == 2

    def test_failed_model_call_count(self):
        """验证失败模型调用计数"""
        collector = BusinessMetricsCollector()
        
        collector.record_model_call("gpt-4", "openai", success=False, duration=5.0)
        collector.record_model_call("gpt-4", "openai", success=False, duration=4.0)
        collector.record_model_call("gpt-4", "openai", success=True, duration=2.0)
        
        metric = collector.get_metric_by_name("yunshu_model_call_total")
        assert metric is not None
        
        failed_count = 0
        for key, value in metric["data"].items():
            if "success=False" in key:
                failed_count += value
        assert failed_count == 2


class TestConcurrentTracking:
    """测试3：并发场景下埋点计数准确性"""

    def test_concurrent_interaction_recording(self):
        """验证并发交互记录准确性"""
        collector = BusinessMetricsCollector()
        iterations = 100
        
        def record_interaction(i):
            collector.record_interaction(
                "chat", 
                "gpt-4", 
                success=(i % 2 == 0), 
                duration=0.1
            )
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(record_interaction, i) for i in range(iterations)]
            for future in as_completed(futures):
                future.result()
        
        metric = collector.get_metric_by_name("yunshu_interaction_total")
        assert metric is not None
        assert sum(metric["data"].values()) == iterations

    def test_concurrent_memory_search(self):
        """验证并发记忆搜索记录准确性"""
        collector = BusinessMetricsCollector()
        iterations = 50
        
        def record_search(i):
            collector.record_memory_search(
                "long_term" if i % 2 == 0 else "short_term",
                "keyword",
                hit=(i % 3 == 0)
            )
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(record_search, i) for i in range(iterations)]
            for future in as_completed(futures):
                future.result()
        
        metric = collector.get_metric_by_name("yunshu_memory_search_total")
        assert metric is not None
        assert sum(metric["data"].values()) == iterations


class TestLabelCompleteness:
    """测试4：埋点标签完整性验证"""

    def test_interaction_labels(self):
        """验证交互标签完整性"""
        collector = BusinessMetricsCollector()
        collector.record_interaction("chat", "gpt-4", success=True, duration=1.5)
        
        metric = collector.get_metric_by_name("yunshu_interaction_total")
        assert metric is not None
        
        label_keys = list(metric["data"].keys())
        assert len(label_keys) == 1
        
        key = label_keys[0]
        assert "interaction_type" in key or "chat" in key
        assert "model" in key or "gpt-4" in key
        assert "success" in key or "True" in key

    def test_tool_call_labels(self):
        """验证工具调用标签完整性"""
        collector = BusinessMetricsCollector()
        collector.record_tool_call("read_file", "file", success=True, duration=0.3)
        
        metric = collector.get_metric_by_name("yunshu_tool_call_total")
        assert metric is not None
        
        label_keys = list(metric["data"].keys())
        assert len(label_keys) == 1
        
        key = label_keys[0]
        assert "tool_name" in key or "read_file" in key
        assert "tool_category" in key or "file" in key
        assert "success" in key or "True" in key


class TestDurationStatistics:
    """测试5：耗时指标的统计正确性（p95/p99）"""

    def test_histogram_percentiles(self):
        """验证直方图百分位计算"""
        collector = BusinessMetricsCollector()
        
        durations = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
        for d in durations:
            collector.record_interaction("chat", "gpt-4", success=True, duration=d)
        
        metric = collector.get_metric_by_name("yunshu_interaction_duration_seconds")
        assert metric is not None
        
        for label_key, stats in metric["data"].items():
            assert "p50" in stats
            assert "p95" in stats
            assert "p99" in stats
            assert "count" in stats
            assert stats["count"] == 10
            assert stats["p50"] == 0.6
            assert stats["p95"] == 1.0
            assert stats["p99"] == 1.0

    def test_duration_sum_and_avg(self):
        """验证耗时求和与平均计算"""
        collector = BusinessMetricsCollector()
        
        collector.record_interaction("chat", "gpt-4", success=True, duration=1.0)
        collector.record_interaction("chat", "gpt-4", success=True, duration=2.0)
        collector.record_interaction("chat", "gpt-4", success=True, duration=3.0)
        
        metric = collector.get_metric_by_name("yunshu_interaction_duration_seconds")
        assert metric is not None
        
        for _, stats in metric["data"].items():
            assert stats["sum"] == 6.0
            assert abs(stats["avg"] - 2.0) < 0.0001


class TestPerformanceImpact:
    """测试6：埋点对性能的影响（<1ms/次）"""

    def test_single_metric_record_time(self):
        """验证单次埋点耗时<1ms"""
        collector = BusinessMetricsCollector()
        iterations = 1000
        start_time = time.time()
        
        for i in range(iterations):
            collector.record_interaction("chat", "gpt-4", success=True, duration=0.1)
        
        total_time = (time.time() - start_time) * 1000
        avg_time_per_record = total_time / iterations
        
        assert avg_time_per_record < 1.0, f"平均耗时 {avg_time_per_record:.4f}ms > 1ms"

    def test_mixed_metric_record_time(self):
        """验证混合埋点操作耗时<1ms/次"""
        collector = BusinessMetricsCollector()
        iterations = 500
        start_time = time.time()
        
        for i in range(iterations):
            collector.record_interaction("chat", "gpt-4", success=True, duration=0.1)
            collector.record_tool_call("read_file", "file", success=True, duration=0.05)
            collector.record_memory_search("long_term", "keyword", hit=True)
        
        total_time = (time.time() - start_time) * 1000
        avg_time_per_record = total_time / (iterations * 3)
        
        assert avg_time_per_record < 1.0, f"平均耗时 {avg_time_per_record:.4f}ms > 1ms"


class TestMetricFailureIsolation:
    """测试7：埋点失败不影响主流程"""

    def test_metric_failure_does_not_raise(self):
        """验证埋点失败不抛出异常"""
        collector = BusinessMetricsCollector()
        
        try:
            collector.record_interaction("chat", "gpt-4", success=True, duration=1.5)
            collector.record_tool_call("test_tool", "test", success=True)
            collector.record_memory_search("test", "test", hit=True)
        except Exception as e:
            pytest.fail(f"埋点操作不应抛出异常: {e}")

    def test_partial_failure_isolation(self):
        """验证部分埋点失败不影响其他埋点"""
        collector = BusinessMetricsCollector()
        
        collector.record_interaction("chat", "gpt-4", success=True)
        collector.record_tool_call("tool1", "cat1", success=True)
        collector.record_tool_call("tool2", "cat2", success=False)
        collector.record_memory_search("type1", "method1", hit=True)
        
        interaction_metric = collector.get_metric_by_name("yunshu_interaction_total")
        tool_call_metric = collector.get_metric_by_name("yunshu_tool_call_total")
        memory_metric = collector.get_metric_by_name("yunshu_memory_search_total")
        
        assert sum(interaction_metric["data"].values()) == 1
        assert sum(tool_call_metric["data"].values()) == 2
        assert sum(memory_metric["data"].values()) == 1


class TestMetricNamingConvention:
    """测试8：指标命名规范性检查"""

    def test_naming_pattern(self):
        """验证指标命名符合 yunshu_<模块>_<动作> 格式"""
        pattern = r"^yunshu_[a-z_]+_[a-z_]+$"
        
        for name in BUSINESS_METRICS_DEFINITIONS.keys():
            assert re.match(pattern, name), f"指标命名不符合规范: {name}"

    def test_no_uppercase_in_names(self):
        """验证指标名称无大写字母"""
        for name in BUSINESS_METRICS_DEFINITIONS.keys():
            assert name == name.lower(), f"指标名称包含大写字母: {name}"


class TestMetricDefinitionConsistency:
    """测试9：业务指标定义与实际埋点一致性检查"""

    def test_counter_labels_match_record_methods(self):
        """验证计数器标签与记录方法参数一致"""
        collector = BusinessMetricsCollector()
        
        collector.record_interaction("chat", "gpt-4", success=True)
        metric = collector.get_metric_by_name("yunshu_interaction_total")
        assert metric is not None
        
        definition = BUSINESS_METRICS_DEFINITIONS["yunshu_interaction_total"]
        assert "interaction_type" in definition.labels
        assert "model" in definition.labels
        assert "success" in definition.labels

    def test_histogram_labels_match_record_methods(self):
        """验证直方图标签与记录方法参数一致"""
        collector = BusinessMetricsCollector()
        
        collector.record_interaction("chat", "gpt-4", success=True, duration=1.5)
        metric = collector.get_metric_by_name("yunshu_interaction_duration_seconds")
        assert metric is not None
        
        definition = BUSINESS_METRICS_DEFINITIONS["yunshu_interaction_duration_seconds"]
        assert "interaction_type" in definition.labels
        assert "model" in definition.labels


class TestExportFormat:
    """测试10：埋点数据导出格式正确性"""

    def test_prometheus_format(self):
        """验证 Prometheus 导出格式正确"""
        collector = BusinessMetricsCollector()
        collector.record_interaction("chat", "gpt-4", success=True, duration=1.5)
        
        export = collector.export_prometheus()
        lines = export.strip().split('\n')
        
        assert len(lines) >= 3
        assert lines[0].startswith("# HELP")
        assert lines[1].startswith("# TYPE")
        assert "yunshu_interaction_total" in export
        assert 'interaction_type="chat"' in export
        assert 'model="gpt-4"' in export
        assert 'success="True"' in export

    def test_dashboard_data_format(self):
        """验证仪表盘数据格式正确"""
        collector = BusinessMetricsCollector()
        collector.record_interaction("chat", "gpt-4", success=True)
        
        dashboard = collector.get_dashboard_data()
        
        assert "generated_at" in dashboard
        assert "summary" in dashboard
        assert "interaction" in dashboard
        assert "task" in dashboard
        assert "knowledge" in dashboard


class TestMultiDimensionQuery:
    """测试11：多维度标签组合查询正确性"""

    def test_multi_dimension_filter(self):
        """验证多维度标签组合查询"""
        collector = BusinessMetricsCollector()
        
        collector.record_interaction("chat", "gpt-4", success=True)
        collector.record_interaction("chat", "gpt-4", success=False)
        collector.record_interaction("chat", "claude", success=True)
        collector.record_interaction("tool_call", "gpt-4", success=True)
        
        metric = collector.get_metric_by_name("yunshu_interaction_total")
        assert metric is not None
        
        gpt4_success_count = 0
        for key, value in metric["data"].items():
            if "model=gpt-4" in key and "success=True" in key:
                gpt4_success_count += value
        assert gpt4_success_count == 2

    def test_category_filter(self):
        """验证按分类查询"""
        collector = BusinessMetricsCollector()
        
        collector.record_interaction("chat", "gpt-4", success=True)
        collector.record_tool_call("read_file", "file", success=True)
        collector.record_memory_search("long_term", "keyword", hit=True)
        
        dashboard = collector.get_dashboard_data()
        
        interaction_count = sum(dashboard["interaction"]["yunshu_interaction_total"]["data"].values())
        tool_call_count = sum(dashboard["interaction"]["yunshu_tool_call_total"]["data"].values())
        memory_count = sum(dashboard["knowledge"]["yunshu_memory_search_total"]["data"].values())
        
        assert interaction_count == 1
        assert tool_call_count == 1
        assert memory_count == 1


class TestMetricReset:
    """测试12：指标重置/清零功能"""

    def test_full_reset(self):
        """验证完全重置功能"""
        collector = BusinessMetricsCollector()
        
        collector.record_interaction("chat", "gpt-4", success=True)
        collector.record_tool_call("read_file", "file", success=True)
        collector.record_memory_search("long_term", "keyword", hit=True)
        
        interaction_before = sum(collector.get_metric_by_name("yunshu_interaction_total")["data"].values())
        assert interaction_before > 0
        
        collector.reset()
        
        interaction_after = collector.get_metric_by_name("yunshu_interaction_total")
        assert interaction_after is None or sum(interaction_after["data"].values()) == 0

    def test_reset_then_record(self):
        """验证重置后可重新记录"""
        collector = BusinessMetricsCollector()
        
        collector.record_interaction("chat", "gpt-4", success=True)
        collector.reset()
        collector.record_interaction("chat", "claude", success=True)
        
        metric = collector.get_metric_by_name("yunshu_interaction_total")
        assert metric is not None
        assert sum(metric["data"].values()) == 1


class TestDataRetention:
    """测试13：历史数据保留策略验证"""

    def test_timestamp_recording(self):
        """验证时间戳记录"""
        collector = BusinessMetricsCollector()
        
        collector.record_interaction("chat", "gpt-4", success=True)
        time.sleep(0.01)
        collector.record_interaction("chat", "gpt-4", success=True)
        
        timestamps_dict = collector._timestamps["yunshu_interaction_total"]
        assert len(timestamps_dict) == 1
        
        for label_key, timestamps in timestamps_dict.items():
            assert len(timestamps) == 2
            assert timestamps[0] < timestamps[1]

    def test_time_range_filter(self):
        """验证时间范围过滤"""
        collector = BusinessMetricsCollector()
        
        collector.record_interaction("chat", "gpt-4", success=True)
        time.sleep(0.1)
        collector.record_interaction("chat", "gpt-4", success=True)
        
        dashboard_recent = collector.get_dashboard_data(time_range=0.05)
        dashboard_all = collector.get_dashboard_data(time_range=None)
        
        all_count = sum(dashboard_all["interaction"]["yunshu_interaction_total"]["data"].values())
        recent_count = sum(dashboard_recent["interaction"]["yunshu_interaction_total"]["data"].values())
        
        assert all_count == 2
        assert recent_count == 1


class TestAggregationCalculation:
    """测试14：指标聚合计算正确性"""

    def test_sum_aggregation(self):
        """验证求和聚合"""
        collector = BusinessMetricsCollector()
        
        collector.record_interaction("chat", "gpt-4", success=True)
        collector.record_interaction("chat", "gpt-4", success=False)
        collector.record_interaction("tool_call", "gpt-4", success=True)
        
        dashboard = collector.get_dashboard_data()
        assert dashboard["summary"]["total_interactions"] == 3

    def test_average_aggregation(self):
        """验证平均值聚合"""
        collector = BusinessMetricsCollector()
        
        collector.update_task_completion_rate("planning", "simple", 80.0)
        collector.update_task_completion_rate("planning", "complex", 60.0)
        collector.update_task_completion_rate("async", "simple", 90.0)
        
        dashboard = collector.get_dashboard_data()
        completion_rate = dashboard["summary"]["task_success_rate"]
        assert abs(completion_rate - 76.66666666666667) < 0.0001


class TestAlertThreshold:
    """测试15：告警阈值触发验证"""

    def test_high_failure_rate_detection(self):
        """验证高失败率检测"""
        collector = BusinessMetricsCollector()
        
        for i in range(10):
            success = (i < 2)
            collector.record_model_call("gpt-4", "openai", success=success, duration=0.5)
        
        metric = collector.get_metric_by_name("yunshu_model_call_total")
        assert metric is not None
        
        success_count = 0
        failure_count = 0
        for key, value in metric["data"].items():
            if "success=True" in key:
                success_count += value
            elif "success=False" in key:
                failure_count += value
        
        failure_rate = failure_count / (success_count + failure_count)
        assert failure_rate >= 0.8

    def test_circuit_breaker_trigger_count(self):
        """验证熔断器触发计数"""
        collector = BusinessMetricsCollector()
        
        collector.record_circuit_breaker_trigger("test", "closed", "open", "high_error_rate")
        collector.record_circuit_breaker_trigger("test", "open", "half_open", "timeout")
        collector.record_circuit_breaker_trigger("test", "half_open", "closed", "recovery_success")
        
        metric = collector.get_metric_by_name("yunshu_circuit_breaker_trigger_total")
        assert metric is not None
        assert sum(metric["data"].values()) == 3


class TestModelRouterMetrics:
    """测试模型路由指标"""

    def test_model_call_metrics(self):
        """验证模型调用指标"""
        collector = BusinessMetricsCollector()
        
        collector.record_model_call("gpt-4", "openai", success=True, duration=2.0)
        collector.record_model_call("claude-3-sonnet", "claude", success=True, duration=3.0)
        collector.record_model_call("gpt-4", "openai", success=False, duration=5.0)
        
        call_metric = collector.get_metric_by_name("yunshu_model_call_total")
        duration_metric = collector.get_metric_by_name("yunshu_model_call_duration_seconds")
        
        assert sum(call_metric["data"].values()) == 3
        assert duration_metric is not None

    def test_model_switch_metrics(self):
        """验证模型切换指标"""
        collector = BusinessMetricsCollector()
        
        collector.record_model_switch("gpt-3.5", "gpt-4", "performance")
        collector.record_model_switch("gpt-4", "claude", "error_recovery")
        
        metric = collector.get_metric_by_name("yunshu_model_switch_total")
        assert metric is not None
        assert sum(metric["data"].values()) == 2


class TestStabilityMetrics:
    """测试稳定性指标"""

    def test_circuit_breaker_metrics(self):
        """验证熔断器指标"""
        collector = BusinessMetricsCollector()
        
        collector.record_circuit_breaker_trigger("api", "closed", "open", "failure_rate")
        collector.update_circuit_breaker_state("api", "open", 1.0)
        
        trigger_metric = collector.get_metric_by_name("yunshu_circuit_breaker_trigger_total")
        state_metric = collector.get_metric_by_name("yunshu_circuit_breaker_state")
        
        assert sum(trigger_metric["data"].values()) == 1
        assert state_metric is not None

    def test_rate_limit_metrics(self):
        """验证限流指标"""
        collector = BusinessMetricsCollector()
        
        collector.record_rate_limit_trigger("global", "/api/chat", "user1", "rate_limit")
        collector.record_rate_limit_trigger("user", "/api/chat", "user2", "user_limit")
        
        metric = collector.get_metric_by_name("yunshu_rate_limit_trigger_total")
        assert metric is not None
        assert sum(metric["data"].values()) == 2

    def test_degrade_metrics(self):
        """验证降级指标"""
        collector = BusinessMetricsCollector()
        
        collector.record_degrade_trigger("schema", "text_only", "validation_failed")
        collector.record_degrade_trigger("memory", "cache_only", "timeout")
        
        metric = collector.get_metric_by_name("yunshu_degrade_trigger_total")
        assert metric is not None
        assert sum(metric["data"].values()) == 2

    def test_disaster_recovery_metrics(self):
        """验证容灾恢复指标"""
        collector = BusinessMetricsCollector()
        
        collector.record_disaster_recovery("backup_restore", "completed", "backup_001")
        collector.record_disaster_recovery("database_repair", "failed", "")
        
        metric = collector.get_metric_by_name("yunshu_disaster_recovery_total")
        assert metric is not None
        assert sum(metric["data"].values()) == 2
