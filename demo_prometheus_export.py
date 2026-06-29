#!/usr/bin/env python3
"""埋点数据导出示例 — Prometheus 格式展示

运行此脚本可查看业务指标在 Prometheus 格式下的实际输出样子。
"""

from agent.monitoring.business_metrics import BusinessMetricsCollector, BUSINESS_METRICS_DEFINITIONS


def demo_prometheus_export():
    """演示 Prometheus 格式的埋点数据导出"""
    collector = BusinessMetricsCollector()

    # 模拟对话交互
    collector.record_interaction("chat", "gpt-4", success=True, duration=1.5)
    collector.record_interaction("chat", "gpt-4", success=False, duration=2.0)
    collector.record_interaction("chat", "claude-3-sonnet", success=True, duration=1.2)
    collector.record_interaction("tool_call", "gpt-4", success=True, duration=0.5)
    collector.record_interaction("tool_call", "claude-3-sonnet", success=False, duration=0.8)

    # 模拟工具调用
    collector.record_tool_call("read_file", "file", success=True, duration=0.3)
    collector.record_tool_call("write_file", "file", success=False, duration=0.5)
    collector.record_tool_call("search", "web", success=True, duration=1.0)
    collector.record_tool_call("read_file", "file", success=True, duration=0.2)

    # 模拟记忆搜索
    collector.record_memory_search("long_term", "keyword", hit=True)
    collector.record_memory_search("short_term", "vector", hit=False)
    collector.record_memory_search("long_term", "keyword", hit=True)

    # 模拟记忆存储
    collector.record_memory_storage("long_term", importance=1)
    collector.record_memory_storage("short_term", importance=0)

    # 模拟模型调用
    collector.record_model_call("gpt-4", "openai", success=True, duration=2.0)
    collector.record_model_call("claude-3-sonnet", "claude", success=True, duration=3.0)
    collector.record_model_call("gpt-4", "openai", success=False, duration=5.0)

    # 模拟模型切换
    collector.record_model_switch("gpt-3.5", "gpt-4", "performance")
    collector.record_model_switch("gpt-4", "claude", "error_recovery")

    # 模拟熔断器触发
    collector.record_circuit_breaker_trigger("api", "closed", "open", "high_error_rate")
    collector.record_circuit_breaker_trigger("tool_calling", "open", "half_open", "timeout")

    # 模拟限流触发
    collector.record_rate_limit_trigger("global", "/api/chat", "user1", "rate_limit_exceeded")
    collector.record_rate_limit_trigger("endpoint", "/api/chat", "user2", "endpoint_limit_exceeded")

    # 模拟降级触发
    collector.record_degrade_trigger("schema", "text_only", "validation_failed")
    collector.record_degrade_trigger("memory", "cache_only", "timeout")

    # 模拟容灾恢复
    collector.record_disaster_recovery("backup_restore", "completed", "backup_001")
    collector.record_disaster_recovery("database_repair", "failed", "")

    print("=" * 80)
    print("📊 Prometheus 格式导出示例")
    print("=" * 80)
    print()

    # 导出 Prometheus 格式
    prometheus_output = collector.export_prometheus()
    print(prometheus_output)

    print()
    print("=" * 80)
    print("📈 Dashboard 数据格式示例")
    print("=" * 80)
    print()

    # 导出 Dashboard 格式
    dashboard = collector.get_dashboard_data()

    import json
    print(json.dumps(dashboard, indent=2, ensure_ascii=False))

    print()
    print("=" * 80)
    print("🔍 关键埋点指标说明")
    print("=" * 80)
    print()

    # 输出关键指标说明
    key_metrics = [
        "yunshu_interaction_total",
        "yunshu_tool_call_total",
        "yunshu_memory_search_total",
        "yunshu_memory_storage_total",
        "yunshu_model_call_total",
        "yunshu_model_switch_total",
        "yunshu_circuit_breaker_trigger_total",
        "yunshu_rate_limit_trigger_total",
        "yunshu_degrade_trigger_total",
        "yunshu_disaster_recovery_total",
    ]

    for name in key_metrics:
        if name in BUSINESS_METRICS_DEFINITIONS:
            defn = BUSINESS_METRICS_DEFINITIONS[name]
            print(f"【{name}】")
            print(f"  描述: {defn.description}")
            print(f"  类型: {defn.metric_type}")
            print(f"  标签: {defn.labels}")
            print(f"  单位: {defn.unit}")
            print(f"  分类: {defn.category}")
            print()


def demo_performance_metrics():
    """演示性能指标验证"""
    import time

    print("=" * 80)
    print("⚡ 性能指标验证")
    print("=" * 80)
    print()

    collector = BusinessMetricsCollector()
    iterations = 1000

    # 测试单次埋点性能
    start = time.time()
    for i in range(iterations):
        collector.record_interaction("chat", "gpt-4", success=True, duration=0.1)
    elapsed = (time.time() - start) * 1000

    print(f"总迭代次数: {iterations}")
    print(f"总耗时: {elapsed:.2f}ms")
    print(f"平均单次埋点耗时: {elapsed/iterations:.4f}ms")
    print()

    # 验证是否小于1ms
    avg_time = elapsed / iterations
    if avg_time < 1.0:
        print(f"✅ 性能验证通过！平均耗时 {avg_time:.4f}ms < 1ms")
    else:
        print(f"❌ 性能验证失败！平均耗时 {avg_time:.4f}ms >= 1ms")


if __name__ == "__main__":
    demo_prometheus_export()
    print()
    demo_performance_metrics()
