#!/usr/bin/env python3
"""测试指标收集器"""
import sys
import importlib.util

# 直接加载模块，避免导入agent包
spec = importlib.util.spec_from_file_location("metrics", "agent/monitoring/metrics.py")
metrics_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metrics_module)

print("=== 测试指标收集器 ===")
collector = metrics_module.get_metrics_collector()
print("1. 收集器初始化成功")

# 记录一些指标
collector.record_latency('test.latency.api', 0.5)
collector.record_latency('test.latency.api', 1.2)
collector.record_latency('test.latency.db', 0.1)
collector.increment_counter('test.count.requests', 10)
collector.increment_counter('test.count.errors', 2)
print("2. 指标记录成功")

# 获取所有指标
metrics = collector.get_all_metrics()
print(f"3. 获取指标成功")
print(f"   - Histograms: {list(metrics['histograms'].keys())}")
print(f"   - Counters: {dict(metrics['counters'])}")

# 导出 Prometheus 格式
prom_output = collector.export_prometheus()
print("4. Prometheus 导出成功")
print("--- Prometheus Output ---")
print(prom_output)
print("------------------------")

print("\n=== 测试完成 ===")