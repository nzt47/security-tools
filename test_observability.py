#!/usr/bin/env python3
"""可观测性系统综合测试"""
import sys
import os
import importlib.util

print("=== 可观测性系统综合测试 ===\n")

# 1. 测试指标收集器
print("1. 测试 Prometheus 指标收集器")
try:
    spec = importlib.util.spec_from_file_location("metrics", "agent/monitoring/metrics.py")
    metrics_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(metrics_module)
    
    collector = metrics_module.get_metrics_collector()
    collector.record_latency('test.api.latency', 0.3)
    collector.record_latency('test.api.latency', 0.7)
    collector.increment_counter('test.count.requests', 5)
    
    prom_output = collector.export_prometheus()
    assert 'test_api_latency' in prom_output, "指标导出格式错误"
    assert 'test_count_requests' in prom_output, "计数器导出失败"
    print("   ✓ 指标收集器测试通过")
except Exception as e:
    print(f"   ✗ 指标收集器测试失败: {e}")

# 2. 测试 Loki 日志系统
print("\n2. 测试 Loki 日志系统")
try:
    spec = importlib.util.spec_from_file_location("loki", "agent/monitoring/loki.py")
    loki_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loki_module)
    
    client = loki_module.get_loki_client()
    
    # 测试推送日志
    client.push_log(
        labels={'service': 'test', 'level': 'INFO'},
        message='测试日志消息'
    )
    print("   ✓ 日志推送测试通过")
    
    # 测试查询日志
    logs = client.query_logs('测试')
    assert isinstance(logs, list), "日志查询返回格式错误"
    print("   ✓ 日志查询测试通过")
    
    # 测试获取标签
    labels = client.get_labels()
    assert isinstance(labels, dict), "标签获取返回格式错误"
    print("   ✓ 标签获取测试通过")
    
except Exception as e:
    print(f"   ✗ Loki 测试失败: {e}")

# 3. 测试追踪功能
print("\n3. 测试追踪功能")
try:
    spec = importlib.util.spec_from_file_location("tracing", "agent/monitoring/tracing.py")
    tracing_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tracing_module)
    
    # 测试 TraceContext
    with tracing_module.TraceContext('test-service', 'test-operation') as ctx:
        assert ctx.trace_id is not None, "Trace ID 应为非空"
        assert ctx.span_id is not None, "Span ID 应为非空"
    print("   ✓ TraceContext 测试通过")
    
    # 测试追踪查询
    traces = tracing_module.get_recent_traces(5)
    assert isinstance(traces, list), "追踪查询返回格式错误"
    print("   ✓ 追踪查询测试通过")
    
except Exception as e:
    print(f"   ✗ 追踪测试失败: {e}")

# 4. 测试告警规则配置
print("\n4. 测试告警规则配置")
try:
    import yaml
    
    alert_file = 'monitoring/alerts.yml'
    if os.path.exists(alert_file):
        with open(alert_file, 'r', encoding='utf-8') as f:
            rules = yaml.safe_load(f)
        
        assert 'groups' in rules, "告警规则格式错误"
        print("   ✓ 告警规则文件格式正确")
        
        # 检查是否有规则
        if rules['groups']:
            for group in rules['groups']:
                assert 'name' in group, "缺少 group name"
                assert 'rules' in group, "缺少 rules"
            print("   ✓ 告警规则结构验证通过")
    else:
        print("   ⚠ 告警规则文件不存在")
        
except Exception as e:
    print(f"   ✗ 告警规则测试失败: {e}")

# 5. 测试路由文件导入
print("\n5. 测试路由文件")
try:
    # 检查路由文件是否存在
    assert os.path.exists('agent/server_routes/routes_logging.py'), "路由文件不存在"
    print("   ✓ 路由文件存在")
    
    # 检查关键端点是否存在
    with open('agent/server_routes/routes_logging.py', 'r', encoding='utf-8') as f:
        content = f.read()
        assert '@app.route("/metrics"' in content, "缺少 /metrics 端点"
        assert '@app.route("/dashboard"' in content, "缺少 /dashboard 端点"
        assert '@app.route("/api/observability/logs"' in content, "缺少日志端点"
        assert '@app.route("/api/observability/traces"' in content, "缺少追踪端点"
        assert '@app.route("/api/observability/alerts"' in content, "缺少告警端点"
    print("   ✓ 关键端点验证通过")
    
except Exception as e:
    print(f"   ✗ 路由测试失败: {e}")

# 6. 测试仪表盘模板
print("\n6. 测试仪表盘模板")
try:
    assert os.path.exists('templates/observability_dashboard.html'), "仪表盘模板不存在"
    
    with open('templates/observability_dashboard.html', 'r', encoding='utf-8') as f:
        content = f.read()
        assert 'metrics' in content, "缺少指标标签页"
        assert 'traces' in content, "缺少追踪标签页"
        assert 'logs' in content, "缺少日志标签页"
        assert 'alerts' in content, "缺少告警标签页"
    print("   ✓ 仪表盘模板验证通过")
    
except Exception as e:
    print(f"   ✗ 仪表盘模板测试失败: {e}")

print("\n=== 测试完成 ===")