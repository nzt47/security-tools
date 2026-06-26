#!/usr/bin/env python3
"""
追踪性能基准测试模块

用于评估追踪功能对系统性能的影响，包括：
- Span创建开销
- 上下文切换开销
- 序列化开销
- 存储开销
- 采样机制开销
"""

import time
import uuid
import json
import threading
import concurrent.futures
from typing import Dict, Any, List, Callable
from collections import defaultdict

# 性能统计类
class PerformanceStats:
    """性能统计收集器"""
    
    def __init__(self):
        self.timings = defaultdict(list)
        self.counters = defaultdict(int)
        self.start_time = time.time()
    
    def record(self, metric_name: str, duration_ms: float):
        """记录单次执行耗时"""
        self.timings[metric_name].append(duration_ms)
    
    def increment(self, counter_name: str, value: int = 1):
        """增加计数器"""
        self.counters[counter_name] += value
    
    def get_stats(self, metric_name: str) -> Dict[str, float]:
        """获取指标统计信息"""
        values = self.timings[metric_name]
        if not values:
            return {"count": 0, "mean": 0, "p50": 0, "p90": 0, "p99": 0, "min": 0, "max": 0}
        
        values.sort()
        n = len(values)
        return {
            "count": n,
            "mean": sum(values) / n,
            "p50": values[int(n * 0.50)] if n > 0 else 0,
            "p90": values[int(n * 0.90)] if n > 0 else 0,
            "p99": values[int(n * 0.99)] if n > 0 else 0,
            "min": values[0],
            "max": values[-1]
        }
    
    def get_report(self) -> Dict[str, Any]:
        """生成完整性能报告"""
        report = {
            "total_duration_ms": (time.time() - self.start_time) * 1000,
            "metrics": {},
            "counters": dict(self.counters)
        }
        
        for metric_name in self.timings:
            report["metrics"][metric_name] = self.get_stats(metric_name)
        
        return report


def measure_overhead(
    func: Callable,
    iterations: int = 10000,
    warmup: int = 1000,
    *args,
    **kwargs
) -> Dict[str, float]:
    """
    测量函数执行开销
    
    Args:
        func: 要测量的函数
        iterations: 迭代次数
        warmup: 预热次数（不计入统计）
        *args: 函数位置参数
        **kwargs: 函数关键字参数
    
    Returns:
        包含统计信息的字典
    """
    # 预热
    for _ in range(warmup):
        func(*args, **kwargs)
    
    # 正式测量
    start = time.perf_counter()
    for _ in range(iterations):
        func(*args, **kwargs)
    end = time.perf_counter()
    
    total_ms = (end - start) * 1000
    per_call_ms = total_ms / iterations
    
    return {
        "iterations": iterations,
        "total_ms": total_ms,
        "per_call_ms": per_call_ms,
        "calls_per_second": iterations / ((end - start) or 0.000001)
    }


class TraceOverheadBenchmark:
    """追踪开销基准测试"""
    
    def __init__(self):
        self.stats = PerformanceStats()
    
    def _generate_trace_id(self) -> str:
        """生成trace_id（模拟）"""
        return uuid.uuid4().hex[:16]
    
    def _generate_span_id(self) -> str:
        """生成span_id（模拟）"""
        return uuid.uuid4().hex[:16]
    
    def benchmark_uuid_generation(self, iterations: int = 10000):
        """基准测试UUID生成开销"""
        results = measure_overhead(
            lambda: uuid.uuid4().hex[:16],
            iterations=iterations
        )
        self.stats.record("uuid_generation", results["per_call_ms"])
        self.stats.increment("uuid_generation_total", iterations)
        return results
    
    def benchmark_json_serialization(self, iterations: int = 10000):
        """基准测试JSON序列化开销"""
        span_data = {
            "trace_id": "abc123def4567890",
            "span_id": "1234567890abcdef",
            "service": "test-service",
            "operation": "test-operation",
            "start_time": 1234567890.123456,
            "end_time": 1234567890.123456,
            "duration_ms": 150.5,
            "status": "success",
            "attributes": {"key1": "value1", "key2": ["a", "b", "c"]},
            "events": [{"name": "event1", "timestamp": 1234567890.123}]
        }
        
        results = measure_overhead(
            lambda: json.dumps(span_data),
            iterations=iterations
        )
        self.stats.record("json_serialization", results["per_call_ms"])
        self.stats.increment("json_serialization_total", iterations)
        return results
    
    def benchmark_context_var_access(self, iterations: int = 10000):
        """基准测试ContextVar访问开销"""
        from contextvars import ContextVar
        
        test_var = ContextVar('test_var', default=None)
        test_var.set("test_value")
        
        def access_context():
            val = test_var.get()
            return val
        
        results = measure_overhead(
            access_context,
            iterations=iterations
        )
        self.stats.record("context_var_access", results["per_call_ms"])
        self.stats.increment("context_var_access_total", iterations)
        return results
    
    def benchmark_context_switch(self, iterations: int = 10000):
        """基准测试上下文切换开销"""
        from contextvars import ContextVar
        
        trace_id_var = ContextVar('trace_id', default=None)
        span_id_var = ContextVar('span_id', default=None)
        
        def switch_context():
            # 保存当前上下文
            old_trace = trace_id_var.get()
            old_span = span_id_var.get()
            
            # 设置新上下文
            trace_id_var.set(uuid.uuid4().hex[:16])
            span_id_var.set(uuid.uuid4().hex[:16])
            
            # 恢复上下文
            trace_id_var.set(old_trace)
            span_id_var.set(old_span)
        
        results = measure_overhead(
            switch_context,
            iterations=iterations
        )
        self.stats.record("context_switch", results["per_call_ms"])
        self.stats.increment("context_switch_total", iterations)
        return results
    
    def benchmark_span_creation(self, iterations: int = 1000):
        """基准测试Span创建开销（使用真实TraceContext）"""
        # 延迟导入以避免影响其他测试
        from .tracing import TraceContext
        
        def create_span():
            with TraceContext("test-service", "test-operation") as ctx:
                ctx.add_event("test_event", {"key": "value"})
                ctx.set_attribute("test_attr", "test_value")
        
        results = measure_overhead(
            create_span,
            iterations=iterations,
            warmup=100
        )
        self.stats.record("span_creation", results["per_call_ms"])
        self.stats.increment("span_creation_total", iterations)
        return results
    
    def benchmark_parallel_spans(self, threads: int = 4, iterations: int = 1000):
        """基准测试多线程并行Span创建"""
        from .tracing import TraceContext
        
        def worker():
            results = []
            for _ in range(iterations):
                start = time.perf_counter()
                with TraceContext("parallel-service", "parallel-op") as ctx:
                    ctx.set_attribute("thread", threading.current_thread().name)
                end = time.perf_counter()
                results.append((end - start) * 1000)
            return results
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as executor:
            futures = [executor.submit(worker) for _ in range(threads)]
            
            all_results = []
            for future in concurrent.futures.as_completed(futures):
                all_results.extend(future.result())
        
        avg_ms = sum(all_results) / len(all_results)
        self.stats.record("parallel_span_creation", avg_ms)
        self.stats.increment("parallel_spans_total", len(all_results))
        
        return {
            "threads": threads,
            "iterations_per_thread": iterations,
            "total_iterations": len(all_results),
            "avg_per_call_ms": avg_ms,
            "min_ms": min(all_results),
            "max_ms": max(all_results)
        }
    
    def run_full_benchmark(self) -> Dict[str, Any]:
        """运行完整基准测试套件"""
        print("=" * 80)
        print("🚀 开始追踪性能基准测试")
        print("=" * 80)
        
        results = {}
        
        print("\n📊 1. UUID生成开销测试")
        results["uuid_generation"] = self.benchmark_uuid_generation()
        print(f"   结果: {results['uuid_generation']}")
        
        print("\n📊 2. JSON序列化开销测试")
        results["json_serialization"] = self.benchmark_json_serialization()
        print(f"   结果: {results['json_serialization']}")
        
        print("\n📊 3. ContextVar访问开销测试")
        results["context_var_access"] = self.benchmark_context_var_access()
        print(f"   结果: {results['context_var_access']}")
        
        print("\n📊 4. 上下文切换开销测试")
        results["context_switch"] = self.benchmark_context_switch()
        print(f"   结果: {results['context_switch']}")
        
        print("\n📊 5. Span创建开销测试")
        results["span_creation"] = self.benchmark_span_creation()
        print(f"   结果: {results['span_creation']}")
        
        print("\n📊 6. 多线程并行Span创建测试")
        results["parallel_spans"] = self.benchmark_parallel_spans()
        print(f"   结果: {results['parallel_spans']}")
        
        print("\n" + "=" * 80)
        print("📈 性能统计摘要")
        print("=" * 80)
        
        summary = self.stats.get_report()
        for metric_name, stats in summary["metrics"].items():
            print(f"\n{metric_name}:")
            print(f"  调用次数: {stats['count']}")
            print(f"  平均耗时: {stats['mean']:.6f} ms")
            print(f"  P50: {stats['p50']:.6f} ms")
            print(f"  P90: {stats['p90']:.6f} ms")
            print(f"  P99: {stats['p99']:.6f} ms")
            print(f"  最小: {stats['min']:.6f} ms")
            print(f"  最大: {stats['max']:.6f} ms")
        
        print("\n" + "=" * 80)
        print("💡 开销评估结论")
        print("=" * 80)
        
        span_creation_stats = summary["metrics"].get("span_creation", {})
        avg_span_ms = span_creation_stats.get("mean", 0)
        
        if avg_span_ms < 0.1:
            print(f"✅ Span创建开销极低 ({avg_span_ms:.4f}ms)，对性能影响可忽略")
        elif avg_span_ms < 1.0:
            print(f"⚠️ Span创建开销较低 ({avg_span_ms:.4f}ms)，建议关注高频场景")
        else:
            print(f"❌ Span创建开销较高 ({avg_span_ms:.4f}ms)，需要优化")
        
        return {
            "detailed_results": results,
            "summary": summary,
            "timestamp": time.time()
        }


def main():
    """主函数：运行基准测试"""
    benchmark = TraceOverheadBenchmark()
    results = benchmark.run_full_benchmark()
    
    # 保存结果到文件
    output_file = f"tracing_performance_report_{int(time.time())}.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f"\n📁 性能报告已保存到: {output_file}")


if __name__ == "__main__":
    main()