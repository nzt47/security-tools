"""agent/monitoring/business_metrics.py 全面单元测试

测试目标：覆盖 BusinessMetricsCollector 的所有公开 API
覆盖维度：
1. BusinessMetricDefinition 数据类
2. 指标定义表 BUSINESS_METRICS_DEFINITIONS 完整性
3. Counter 指标：record_interaction / record_tool_call / record_task 等
4. Gauge 指标：update_task_completion_rate / update_memory_hit_rate 等
5. Histogram 指标：record_interaction(duration) / record_tool_call(duration) 等
6. 数据查询：get_dashboard_data / get_metric_by_name
7. 导出：export_prometheus
8. 重置与全局单例

状态同步说明：每个用例使用独立 BusinessMetricsCollector 实例避免全局污染。
"""
import threading

import pytest

from agent.monitoring.business_metrics import (
    BUSINESS_METRICS_DEFINITIONS,
    BusinessMetricDefinition,
    BusinessMetricsCollector,
    get_business_metrics_collector,
)


@pytest.fixture
def collector():
    """独立的业务指标收集器"""
    return BusinessMetricsCollector()


# ── 1. BusinessMetricDefinition 数据类 ───────────────────


class TestBusinessMetricDefinition:
    def test_default_values(self):
        d = BusinessMetricDefinition(
            name="test", description="desc", metric_type="counter"
        )
        assert d.name == "test"
        assert d.description == "desc"
        assert d.metric_type == "counter"
        assert d.labels == []
        assert d.unit == "次"
        assert d.category == "business"
        assert d.aggregation == "sum"
        assert d.retention_days == 30

    def test_custom_values(self):
        d = BusinessMetricDefinition(
            name="x",
            description="y",
            metric_type="gauge",
            labels=["a", "b"],
            unit="%",
            category="interaction",
            business_value="value",
            aggregation="avg",
            retention_days=7,
        )
        assert d.labels == ["a", "b"]
        assert d.unit == "%"
        assert d.category == "interaction"
        assert d.business_value == "value"
        assert d.aggregation == "avg"
        assert d.retention_days == 7


# ── 2. 指标定义表完整性 ─────────────────────────────────


class TestMetricDefinitions:
    def test_definitions_not_empty(self):
        assert len(BUSINESS_METRICS_DEFINITIONS) > 0

    def test_all_definitions_are_metric_objects(self):
        for name, defn in BUSINESS_METRICS_DEFINITIONS.items():
            assert isinstance(defn, BusinessMetricDefinition)
            assert defn.name == name

    def test_interaction_metrics_exist(self):
        assert "yunshu_interaction_total" in BUSINESS_METRICS_DEFINITIONS
        assert "yunshu_tool_call_total" in BUSINESS_METRICS_DEFINITIONS
        assert "yunshu_message_type_total" in BUSINESS_METRICS_DEFINITIONS

    def test_task_metrics_exist(self):
        assert "yunshu_task_total" in BUSINESS_METRICS_DEFINITIONS
        assert "yunshu_task_completion_rate" in BUSINESS_METRICS_DEFINITIONS
        assert "yunshu_planning_task_success" in BUSINESS_METRICS_DEFINITIONS
        assert "yunshu_async_task_success" in BUSINESS_METRICS_DEFINITIONS

    def test_memory_metrics_exist(self):
        assert "yunshu_memory_search_total" in BUSINESS_METRICS_DEFINITIONS
        assert "yunshu_memory_search_hit_rate" in BUSINESS_METRICS_DEFINITIONS
        assert "yunshu_memory_access_total" in BUSINESS_METRICS_DEFINITIONS
        assert "yunshu_memory_storage_total" in BUSINESS_METRICS_DEFINITIONS

    def test_extension_metrics_exist(self):
        assert "yunshu_extension_install_total" in BUSINESS_METRICS_DEFINITIONS
        assert "yunshu_extension_uninstall_total" in BUSINESS_METRICS_DEFINITIONS
        assert "yunshu_extension_enabled_count" in BUSINESS_METRICS_DEFINITIONS
        assert "yunshu_mcp_connection_total" in BUSINESS_METRICS_DEFINITIONS

    def test_model_router_metrics_exist(self):
        assert "yunshu_model_call_total" in BUSINESS_METRICS_DEFINITIONS
        assert "yunshu_model_call_duration_seconds" in BUSINESS_METRICS_DEFINITIONS
        assert "yunshu_model_switch_total" in BUSINESS_METRICS_DEFINITIONS
        assert "yunshu_model_success_rate" in BUSINESS_METRICS_DEFINITIONS

    def test_stability_metrics_exist(self):
        assert "yunshu_circuit_breaker_trigger_total" in BUSINESS_METRICS_DEFINITIONS
        assert "yunshu_circuit_breaker_state" in BUSINESS_METRICS_DEFINITIONS
        assert "yunshu_rate_limit_trigger_total" in BUSINESS_METRICS_DEFINITIONS
        assert "yunshu_degrade_trigger_total" in BUSINESS_METRICS_DEFINITIONS
        assert "yunshu_disaster_recovery_total" in BUSINESS_METRICS_DEFINITIONS
        assert "yunshu_backup_total" in BUSINESS_METRICS_DEFINITIONS

    def test_metric_types_valid(self):
        valid_types = {"counter", "gauge", "histogram"}
        for defn in BUSINESS_METRICS_DEFINITIONS.values():
            assert defn.metric_type in valid_types

    def test_categories_valid(self):
        valid_categories = {
            "interaction", "task", "knowledge", "extension",
            "model_router", "stability", "business",
        }
        for defn in BUSINESS_METRICS_DEFINITIONS.values():
            assert defn.category in valid_categories


# ── 3. Counter 指标 ─────────────────────────────────────


class TestCounterMetrics:
    def test_record_interaction(self, collector):
        collector.record_interaction("chat", "gpt-4", success=True)
        data = collector.get_metric_by_name("yunshu_interaction_total")
        assert data is not None
        assert sum(data["data"].values()) == 1

    def test_record_interaction_with_duration(self, collector):
        collector.record_interaction("chat", "gpt-4", success=True, duration=1.5)
        # counter 应记录
        c_data = collector.get_metric_by_name("yunshu_interaction_total")
        assert sum(c_data["data"].values()) == 1
        # histogram 也应记录
        h_data = collector.get_metric_by_name("yunshu_interaction_duration_seconds")
        assert h_data["data"]

    def test_record_message_type(self, collector):
        collector.record_message_type("simple_query", "greeting")
        data = collector.get_metric_by_name("yunshu_message_type_total")
        assert sum(data["data"].values()) == 1

    def test_record_tool_call(self, collector):
        collector.record_tool_call("read_file", "file", success=True, duration=0.3)
        c_data = collector.get_metric_by_name("yunshu_tool_call_total")
        assert sum(c_data["data"].values()) == 1
        h_data = collector.get_metric_by_name("yunshu_tool_call_duration_seconds")
        assert h_data["data"]

    def test_record_task(self, collector):
        collector.record_task("planning", "complex", "success", duration=10.0)
        c_data = collector.get_metric_by_name("yunshu_task_total")
        assert sum(c_data["data"].values()) == 1

    def test_record_planning_task(self, collector):
        collector.record_planning_task("react", 5, True)
        data = collector.get_metric_by_name("yunshu_planning_task_success")
        assert sum(data["data"].values()) == 1

    def test_record_async_task(self, collector):
        collector.record_async_task("background", "queue1", True)
        data = collector.get_metric_by_name("yunshu_async_task_success")
        assert sum(data["data"].values()) == 1

    def test_record_memory_search(self, collector):
        collector.record_memory_search("long_term", "keyword", True)
        data = collector.get_metric_by_name("yunshu_memory_search_total")
        assert sum(data["data"].values()) == 1

    def test_record_memory_search_with_duration(self, collector):
        collector.record_memory_search("long_term", "vector", True, duration=0.05)
        # memory_search 不记录 histogram（实现中没有）
        data = collector.get_metric_by_name("yunshu_memory_search_total")
        assert sum(data["data"].values()) == 1

    def test_record_memory_access(self, collector):
        collector.record_memory_access("key1", 5)
        data = collector.get_metric_by_name("yunshu_memory_access_total")
        assert sum(data["data"].values()) == 1

    def test_record_memory_storage(self, collector):
        collector.record_memory_storage("long_term", 3, True)
        data = collector.get_metric_by_name("yunshu_memory_storage_total")
        assert sum(data["data"].values()) == 1

    def test_record_memory_compression(self, collector):
        collector.record_memory_compression("summarize", True)
        data = collector.get_metric_by_name("yunshu_memory_compression_total")
        assert sum(data["data"].values()) == 1

    def test_record_memory_deletion(self, collector):
        collector.record_memory_deletion("short_term", True)
        data = collector.get_metric_by_name("yunshu_memory_deletion_total")
        assert sum(data["data"].values()) == 1

    def test_record_memory_operation(self, collector):
        collector.record_memory_operation("search", "long_term", 0.1)
        data = collector.get_metric_by_name("yunshu_memory_operation_duration_seconds")
        assert data["data"]

    def test_record_extension_install(self, collector):
        collector.record_extension_install("skill", "github", True)
        data = collector.get_metric_by_name("yunshu_extension_install_total")
        assert sum(data["data"].values()) == 1

    def test_record_extension_uninstall(self, collector):
        collector.record_extension_uninstall("skill", "skill_id_1")
        data = collector.get_metric_by_name("yunshu_extension_uninstall_total")
        assert sum(data["data"].values()) == 1

    def test_record_mcp_connection(self, collector):
        collector.record_mcp_connection("stdio", "service1", True)
        data = collector.get_metric_by_name("yunshu_mcp_connection_total")
        assert sum(data["data"].values()) == 1

    def test_record_skill_usage(self, collector):
        collector.record_skill_usage("skill_id", "category", True)
        data = collector.get_metric_by_name("yunshu_skill_usage_total")
        assert sum(data["data"].values()) == 1

    def test_record_market_search(self, collector):
        collector.record_market_search("tools", 10)
        data = collector.get_metric_by_name("yunshu_market_search_total")
        assert sum(data["data"].values()) == 1

    def test_record_model_call(self, collector):
        collector.record_model_call("gpt-4", "openai", True, duration=2.0)
        c_data = collector.get_metric_by_name("yunshu_model_call_total")
        assert sum(c_data["data"].values()) == 1
        h_data = collector.get_metric_by_name("yunshu_model_call_duration_seconds")
        assert h_data["data"]

    def test_record_model_switch(self, collector):
        collector.record_model_switch("gpt-4", "claude", "cost")
        data = collector.get_metric_by_name("yunshu_model_switch_total")
        assert sum(data["data"].values()) == 1

    def test_record_circuit_breaker_trigger(self, collector):
        collector.record_circuit_breaker_trigger(
            "tool_calling", "closed", "open", "high_error_rate"
        )
        data = collector.get_metric_by_name("yunshu_circuit_breaker_trigger_total")
        assert sum(data["data"].values()) == 1

    def test_record_rate_limit_trigger(self, collector):
        collector.record_rate_limit_trigger("global", "/api/chat", "user1", "exceeded")
        data = collector.get_metric_by_name("yunshu_rate_limit_trigger_total")
        assert sum(data["data"].values()) == 1

    def test_record_degrade_trigger(self, collector):
        collector.record_degrade_trigger("schema", "text_only", "validation_failed")
        data = collector.get_metric_by_name("yunshu_degrade_trigger_total")
        assert sum(data["data"].values()) == 1

    def test_record_disaster_recovery(self, collector):
        collector.record_disaster_recovery("auto", "completed", "backup_001")
        data = collector.get_metric_by_name("yunshu_disaster_recovery_total")
        assert sum(data["data"].values()) == 1

    def test_record_backup(self, collector):
        collector.record_backup("full", True)
        data = collector.get_metric_by_name("yunshu_backup_total")
        assert sum(data["data"].values()) == 1

    def test_multiple_increments_same_labels(self, collector):
        for _ in range(5):
            collector.record_interaction("chat", "gpt-4", success=True)
        data = collector.get_metric_by_name("yunshu_interaction_total")
        assert sum(data["data"].values()) == 5

    def test_multiple_increments_different_labels(self, collector):
        collector.record_interaction("chat", "gpt-4", success=True)
        collector.record_interaction("chat", "claude", success=True)
        collector.record_interaction("tool_call", "gpt-4", success=False)
        data = collector.get_metric_by_name("yunshu_interaction_total")
        assert sum(data["data"].values()) == 3
        # 应有 3 个不同的标签组合
        assert len(data["data"]) == 3


# ── 4. Gauge 指标 ───────────────────────────────────────


class TestGaugeMetrics:
    def test_update_task_completion_rate(self, collector):
        collector.update_task_completion_rate("planning", "complex", 85.5)
        data = collector.get_metric_by_name("yunshu_task_completion_rate")
        assert 85.5 in data["data"].values()

    def test_update_memory_hit_rate(self, collector):
        collector.update_memory_hit_rate("long_term", "keyword", 72.3)
        data = collector.get_metric_by_name("yunshu_memory_search_hit_rate")
        assert 72.3 in data["data"].values()

    def test_update_vector_hit_rate(self, collector):
        collector.update_vector_hit_rate("chromadb", "similarity", 90.0)
        data = collector.get_metric_by_name("yunshu_vector_query_hit_rate")
        assert 90.0 in data["data"].values()

    def test_update_extension_enabled_count(self, collector):
        collector.update_extension_enabled_count("skill", 5)
        data = collector.get_metric_by_name("yunshu_extension_enabled_count")
        assert 5 in data["data"].values()

    def test_update_mcp_active_connections(self, collector):
        collector.update_mcp_active_connections("stdio", 3)
        data = collector.get_metric_by_name("yunshu_mcp_active_connection_count")
        assert 3 in data["data"].values()

    def test_update_model_success_rate(self, collector):
        collector.update_model_success_rate("gpt-4", "openai", 99.5)
        data = collector.get_metric_by_name("yunshu_model_success_rate")
        assert 99.5 in data["data"].values()

    def test_update_circuit_breaker_state(self, collector):
        collector.update_circuit_breaker_state("cb1", "open", 1.0)
        data = collector.get_metric_by_name("yunshu_circuit_breaker_state")
        assert 1.0 in data["data"].values()

    def test_gauge_overwrites_value(self, collector):
        collector.update_task_completion_rate("planning", "complex", 50.0)
        collector.update_task_completion_rate("planning", "complex", 90.0)
        data = collector.get_metric_by_name("yunshu_task_completion_rate")
        assert sum(data["data"].values()) == 90.0


# ── 5. Histogram 指标 ───────────────────────────────────


class TestHistogramMetrics:
    def test_interaction_duration_histogram(self, collector):
        for d in [0.1, 0.5, 1.0, 2.0, 5.0]:
            collector.record_interaction("chat", "gpt-4", success=True, duration=d)
        data = collector.get_metric_by_name("yunshu_interaction_duration_seconds")
        assert data["data"]
        # 检查统计值
        stats = list(data["data"].values())[0]
        assert stats["count"] == 5
        assert stats["min"] == 0.1
        assert stats["max"] == 5.0
        assert "p50" in stats
        assert "p95" in stats
        assert "p99" in stats

    def test_tool_call_duration_histogram(self, collector):
        collector.record_tool_call("read_file", "file", True, duration=0.3)
        data = collector.get_metric_by_name("yunshu_tool_call_duration_seconds")
        assert data["data"]

    def test_task_duration_histogram(self, collector):
        collector.record_task("planning", "complex", "success", duration=10.0)
        data = collector.get_metric_by_name("yunshu_task_duration_seconds")
        assert data["data"]

    def test_model_call_duration_histogram(self, collector):
        collector.record_model_call("gpt-4", "openai", True, duration=2.0)
        data = collector.get_metric_by_name("yunshu_model_call_duration_seconds")
        assert data["data"]

    def test_memory_operation_duration_histogram(self, collector):
        collector.record_memory_operation("search", "long_term", 0.1)
        data = collector.get_metric_by_name("yunshu_memory_operation_duration_seconds")
        assert data["data"]


# ── 6. 数据查询 ─────────────────────────────────────────


class TestQueryData:
    def test_get_dashboard_data_empty(self, collector):
        dashboard = collector.get_dashboard_data()
        assert "generated_at" in dashboard
        assert "interaction" in dashboard
        assert "task" in dashboard
        assert "knowledge" in dashboard
        assert "extension" in dashboard
        assert "model_router" in dashboard
        assert "stability" in dashboard
        assert "summary" in dashboard

    def test_get_dashboard_data_with_metrics(self, collector):
        collector.record_interaction("chat", "gpt-4", success=True)
        collector.record_tool_call("read_file", "file", success=True)
        dashboard = collector.get_dashboard_data()
        assert dashboard["summary"]["total_interactions"] == 1
        assert dashboard["summary"]["total_tool_calls"] == 1

    def test_get_dashboard_data_with_time_range(self, collector):
        collector.record_interaction("chat", "gpt-4", success=True)
        # 时间范围 60 秒
        dashboard = collector.get_dashboard_data(time_range=60)
        assert dashboard["time_range_seconds"] == 60

    def test_get_metric_by_name_existing(self, collector):
        collector.record_interaction("chat", "gpt-4", success=True)
        result = collector.get_metric_by_name("yunshu_interaction_total")
        assert result is not None
        assert "definition" in result
        assert "data" in result
        assert result["definition"]["name"] == "yunshu_interaction_total"

    def test_get_metric_by_name_nonexistent(self, collector):
        assert collector.get_metric_by_name("nonexistent_metric") is None

    def test_summary_task_success_rate(self, collector):
        collector.update_task_completion_rate("planning", "complex", 80.0)
        collector.update_task_completion_rate("planning", "simple", 90.0)
        dashboard = collector.get_dashboard_data()
        # 平均值
        assert dashboard["summary"]["task_success_rate"] == 85.0

    def test_summary_memory_hit_rate(self, collector):
        collector.update_memory_hit_rate("long_term", "keyword", 60.0)
        collector.update_memory_hit_rate("short_term", "keyword", 80.0)
        dashboard = collector.get_dashboard_data()
        assert dashboard["summary"]["memory_hit_rate"] == 70.0

    def test_summary_active_extensions(self, collector):
        collector.update_extension_enabled_count("skill", 3)
        collector.update_extension_enabled_count("mcp", 2)
        dashboard = collector.get_dashboard_data()
        assert dashboard["summary"]["active_extensions"] == 5


# ── 7. 导出 Prometheus ──────────────────────────────────


class TestExportPrometheus:
    def test_export_empty(self, collector):
        result = collector.export_prometheus()
        assert isinstance(result, str)
        # 即使无数据，也应包含所有指标的 HELP 和 TYPE
        for name in BUSINESS_METRICS_DEFINITIONS:
            assert name.replace('.', '_') in result

    def test_export_with_counter_data(self, collector):
        collector.record_interaction("chat", "gpt-4", success=True)
        result = collector.export_prometheus()
        assert "yunshu_interaction_total" in result
        assert "# HELP" in result
        assert "# TYPE" in result

    def test_export_with_gauge_data(self, collector):
        collector.update_task_completion_rate("planning", "complex", 85.0)
        result = collector.export_prometheus()
        assert "yunshu_task_completion_rate" in result

    def test_export_with_histogram_data(self, collector):
        collector.record_interaction("chat", "gpt-4", success=True, duration=1.0)
        result = collector.export_prometheus()
        assert "yunshu_interaction_duration_seconds_sum" in result
        assert "yunshu_interaction_duration_seconds_count" in result
        assert 'quantile="0.5"' in result
        assert 'quantile="0.95"' in result
        assert 'quantile="0.99"' in result

    def test_export_contains_labels(self, collector):
        collector.record_interaction("chat", "gpt-4", success=True)
        result = collector.export_prometheus()
        # 应包含标签
        assert "interaction_type=" in result
        assert "model=" in result


# ── 8. 重置与全局单例 ───────────────────────────────────


class TestResetAndGlobal:
    def test_reset_clears_all(self, collector):
        collector.record_interaction("chat", "gpt-4", success=True)
        collector.update_task_completion_rate("planning", "complex", 80.0)
        collector.record_tool_call("read_file", "file", True, duration=0.5)
        collector.reset()
        dashboard = collector.get_dashboard_data()
        assert dashboard["summary"]["total_interactions"] == 0
        assert dashboard["summary"]["total_tool_calls"] == 0

    def test_get_business_metrics_collector_singleton(self):
        c1 = get_business_metrics_collector()
        c2 = get_business_metrics_collector()
        assert c1 is c2

    def test_global_collector_can_record(self):
        collector = get_business_metrics_collector()
        collector.record_interaction("test", "test_model", success=True)
        data = collector.get_metric_by_name("yunshu_interaction_total")
        assert sum(data["data"].values()) >= 1

    def test_concurrent_increments(self, collector):
        """并发增加计数器应线程安全"""
        barrier = threading.Barrier(10)

        def record():
            barrier.wait()
            collector.record_interaction("chat", "gpt-4", success=True)

        threads = [threading.Thread(target=record) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        data = collector.get_metric_by_name("yunshu_interaction_total")
        assert sum(data["data"].values()) == 10
