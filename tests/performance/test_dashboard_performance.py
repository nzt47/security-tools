#!/usr/bin/env python3
"""
仪表盘性能测试

测试目标：确保仪表盘各个端点加载时间 < 2秒

测试覆盖：
1. /api/dashboard/health - 健康检查
2. /api/dashboard/quality - 质量监控数据
3. /api/dashboard/traces - 追踪数据列表
4. /api/dashboard/memory - Memory使用统计
"""

import time
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from agent.server_routes.routes_dashboard import (
    _get_dashboard_health,
    _get_quality_metrics,
    _get_trace_list,
    _get_memory_stats,
    _parse_time_range
)


class PerformanceTestResult:
    """性能测试结果"""
    def __init__(self, name, duration_ms, passed, threshold_ms=2000):
        self.name = name
        self.duration_ms = duration_ms
        self.threshold_ms = threshold_ms
        self.passed = passed
    
    def __str__(self):
        status = "✅ PASS" if self.passed else "❌ FAIL"
        return f"{status} | {self.name}: {self.duration_ms:.2f}ms (阈值: {self.threshold_ms}ms)"


def run_performance_tests():
    """运行性能测试"""
    results = []
    threshold_ms = 2000  # 2秒
    
    print("=" * 70)
    print("仪表盘性能测试")
    print(f"阈值要求: < {threshold_ms}ms")
    print("=" * 70)
    
    # 测试1: 健康检查
    start = time.time()
    _get_dashboard_health()
    duration = (time.time() - start) * 1000
    results.append(PerformanceTestResult(
        "/api/dashboard/health",
        duration,
        duration < threshold_ms
    ))
    print(results[-1])
    
    # 测试2: 质量监控指标
    start = time.time()
    start_time, end_time = _parse_time_range("today")
    _get_quality_metrics(start_time, end_time)
    duration = (time.time() - start) * 1000
    results.append(PerformanceTestResult(
        "/api/dashboard/quality",
        duration,
        duration < threshold_ms
    ))
    print(results[-1])
    
    # 测试3: 追踪数据列表
    start = time.time()
    _get_trace_list(limit=20)
    duration = (time.time() - start) * 1000
    results.append(PerformanceTestResult(
        "/api/dashboard/traces",
        duration,
        duration < threshold_ms
    ))
    print(results[-1])
    
    # 测试4: Memory使用统计
    start = time.time()
    _get_memory_stats()
    duration = (time.time() - start) * 1000
    results.append(PerformanceTestResult(
        "/api/dashboard/memory",
        duration,
        duration < threshold_ms
    ))
    print(results[-1])
    
    # 汇总结果
    print("=" * 70)
    passed_count = sum(1 for r in results if r.passed)
    total_count = len(results)
    
    print(f"测试结果: {passed_count}/{total_count} 通过")
    
    # 计算平均响应时间
    avg_duration = sum(r.duration_ms for r in results) / total_count
    print(f"平均响应时间: {avg_duration:.2f}ms")
    
    # 计算总加载时间（模拟页面同时加载所有数据）
    total_duration = sum(r.duration_ms for r in results)
    print(f"总加载时间(串行): {total_duration:.2f}ms")
    
    # 找出最慢的端点
    slowest = max(results, key=lambda r: r.duration_ms)
    print(f"最慢端点: {slowest.name} ({slowest.duration_ms:.2f}ms)")
    
    print("=" * 70)
    
    if passed_count == total_count:
        print("✅ 所有性能测试通过！")
        return 0
    else:
        print("❌ 部分性能测试未通过！")
        print("\n未通过的测试:")
        for r in results:
            if not r.passed:
                print(f"  - {r.name}: {r.duration_ms:.2f}ms (超过阈值 {r.threshold_ms}ms)")
        return 1


if __name__ == "__main__":
    sys.exit(run_performance_tests())
