#!/usr/bin/env python3
"""
可观测性开销基准测试

测试不同可观测性功能对系统性能的影响：
1. 基准测试（无可观测性）
2. 仅追踪
3. 仅指标
4. 仅日志关联
5. 全量可观测性
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import time
import json
from typing import Dict, Any

# 全局状态控制
_current_scenario = ""
_tracing_enabled = False
_metrics_enabled = False
_logging_enabled = False


def set_scenario(scenario_name: str, features: list):
    """设置当前测试场景的功能开关"""
    global _current_scenario, _tracing_enabled, _metrics_enabled, _logging_enabled
    _current_scenario = scenario_name
    _tracing_enabled = 'tracing' in features
    _metrics_enabled = 'metrics' in features
    _logging_enabled = 'logging' in features


def get_test_function():
    """获取当前场景的测试函数"""
    def test_with_observability():
        """模拟一个典型的请求处理流程"""
        # 模拟业务逻辑
        result = {"data": "processed"}
        
        # 根据场景决定是否启用可观测性功能
        if _tracing_enabled:
            from agent.monitoring.tracing import TraceContext
            with TraceContext("TestService", "test_operation") as ctx:
                ctx.set_attribute("test_key", "test_value")
                ctx.add_event("test_event", {"status": "completed"})
        
        if _metrics_enabled:
            from agent.monitoring.metrics import record_latency, increment_counter
            record_latency("latency.test.request", 0.001)
            increment_counter("count.test.requests")
        
        if _logging_enabled:
            import logging
            logger = logging.getLogger(__name__)
            trace_id = ""
            if _tracing_enabled:
                from agent.monitoring.tracing import get_trace_id
                trace_id = get_trace_id() or "no-trace"
            logger.info(f"[TRACE:{trace_id}] 请求处理完成")
        
        return result
    
    return test_with_observability


def run_observability_benchmark():
    """运行可观测性开销基准测试"""
    from benchmark_framework import BenchmarkRunner, DEFAULT_SCENARIOS
    
    runner = BenchmarkRunner()
    
    global _current_scenario, _tracing_enabled, _metrics_enabled, _logging_enabled
    
    def test_function():
        """测试函数 - 根据当前全局状态执行测试"""
        result = {"data": "processed"}
        
        if _tracing_enabled:
            from agent.monitoring.tracing import TraceContext
            with TraceContext("TestService", "test_operation") as ctx:
                ctx.set_attribute("test_key", "test_value")
                ctx.add_event("test_event", {"status": "completed"})
        
        if _metrics_enabled:
            from agent.monitoring.metrics import record_latency, increment_counter
            record_latency("latency.test.request", 0.001)
            increment_counter("count.test.requests")
        
        if _logging_enabled:
            import logging
            logger = logging.getLogger(__name__)
            trace_id = ""
            if _tracing_enabled:
                from agent.monitoring.tracing import get_trace_id
                trace_id = get_trace_id() or "no-trace"
            logger.info(f"[TRACE:{trace_id}] 请求处理完成")
        
        return result
    
    # 逐个运行场景
    for i, scenario in enumerate(DEFAULT_SCENARIOS):
        # 设置场景
        _current_scenario = scenario.name
        _tracing_enabled = 'tracing' in scenario.enabled_features
        _metrics_enabled = 'metrics' in scenario.enabled_features
        _logging_enabled = 'logging' in scenario.enabled_features
        
        print(f"\n🚀 运行测试场景: {scenario.name}")
        print(f"   描述: {scenario.description}")
        print(f"   启用特性: {', '.join(scenario.enabled_features)}")
        print(f"   迭代次数: {scenario.iterations}")
        print(f"   并发数: {scenario.concurrent_workers}")
        
        result = runner._run_test_scenario(scenario, test_function)
        
        # 设置基准（第一个场景作为基准）
        if i == 0:
            runner._baseline = result.metrics
            print(f"\n   ✅ 已设置为基准场景")
        
        runner._print_result(result)
    
    # 保存报告
    report = runner.generate_report()
    filename = runner.save_report()
    
    # 输出分析摘要
    print_analysis_summary(report)
    
    return report, filename


def print_analysis_summary(report: Dict[str, Any]):
    """打印分析摘要"""
    print("\n" + "=" * 80)
    print("🔍 可观测性开销分析报告")
    print("=" * 80)
    
    scenarios = report.get('scenarios', [])
    if not scenarios:
        print("❌ 没有测试数据")
        return
    
    # 提取基准场景
    baseline = None
    tracing_result = None
    metrics_result = None
    logging_result = None
    full_result = None
    
    for s in scenarios:
        name = s['name']
        if '基准' in name:
            baseline = s
        elif '追踪' in name:
            tracing_result = s
        elif '指标' in name:
            metrics_result = s
        elif '日志' in name:
            logging_result = s
        elif '全量' in name:
            full_result = s
    
    if baseline:
        baseline_latency = baseline['metrics']['avg_duration_ms']
        print(f"\n📋 基准性能（无可观测性）:")
        print(f"   平均延迟: {baseline_latency:.4f} ms")
        print(f"   吞吐量: {baseline['metrics']['throughput']:.2f} req/s")
        
        print("\n📊 各功能开销对比:")
        
        if tracing_result:
            tracing_overhead = tracing_result.get('overhead_percent', 0)
            print(f"   🎯 追踪功能: {tracing_overhead:+.2f}%")
        
        if metrics_result:
            metrics_overhead = metrics_result.get('overhead_percent', 0)
            print(f"   📈 指标功能: {metrics_overhead:+.2f}%")
        
        if logging_result:
            logging_overhead = logging_result.get('overhead_percent', 0)
            print(f"   📝 日志关联: {logging_overhead:+.2f}%")
        
        if full_result:
            full_overhead = full_result.get('overhead_percent', 0)
            print(f"\n   🔮 全量可观测性: {full_overhead:+.2f}%")
    
    # 输出优化建议
    summary = report.get('summary', {})
    recommendation = summary.get('recommendation', '')
    if recommendation:
        print(f"\n💡 {recommendation}")
    
    print("\n" + "=" * 80)


def main():
    """主函数"""
    print("🚀 开始可观测性开销基准测试")
    print("=" * 80)
    
    try:
        report, filename = run_observability_benchmark()
        
        print(f"\n✅ 测试完成！")
        print(f"📁 报告文件: {filename}")
        
        return 0
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
