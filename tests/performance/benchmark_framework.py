#!/usr/bin/env python3
"""
性能基准测试框架

提供可重复执行的性能基准测试能力，支持：
- 请求响应时间测量
- 内存使用测量
- CPU 使用测量
- 吞吐量测量
- 多场景对比测试
"""

import time
import psutil
import json
import uuid
import threading
import concurrent.futures
from typing import Dict, Any, List, Callable, Optional
from dataclasses import dataclass, field
from collections import defaultdict
from datetime import datetime


@dataclass
class PerformanceMetrics:
    """性能指标数据类"""
    request_count: int = 0
    total_duration_ms: float = 0.0
    avg_duration_ms: float = 0.0
    p50_duration_ms: float = 0.0
    p90_duration_ms: float = 0.0
    p95_duration_ms: float = 0.0
    p99_duration_ms: float = 0.0
    min_duration_ms: float = 0.0
    max_duration_ms: float = 0.0
    throughput: float = 0.0  # requests per second
    avg_memory_mb: float = 0.0
    max_memory_mb: float = 0.0
    avg_cpu_percent: float = 0.0
    max_cpu_percent: float = 0.0


@dataclass
class TestScenario:
    """测试场景定义"""
    name: str
    description: str
    enabled_features: List[str]  # ['tracing', 'metrics', 'logging', 'full']
    iterations: int = 1000
    concurrent_workers: int = 10
    warmup_iterations: int = 100


@dataclass
class BenchmarkResult:
    """基准测试结果"""
    scenario_name: str
    timestamp: float
    metrics: PerformanceMetrics
    baseline_comparison: Dict[str, float] = field(default_factory=dict)
    overhead_percent: float = 0.0


class PerformanceProfiler:
    """性能测量工具类"""
    
    def __init__(self):
        self._latencies: List[float] = []
        self._memory_samples: List[float] = []
        self._cpu_samples: List[float] = []
        self._lock = threading.Lock()
        self._process = psutil.Process()
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_running = False
    
    def start_monitoring(self, interval_ms: float = 50):
        """启动资源监控"""
        self._monitor_running = True
        
        def monitor():
            while self._monitor_running:
                try:
                    memory_mb = self._process.memory_info().rss / (1024 * 1024)
                    cpu_percent = self._process.cpu_percent(interval=0.01)
                    
                    with self._lock:
                        self._memory_samples.append(memory_mb)
                        self._cpu_samples.append(cpu_percent)
                except Exception:
                    pass
                time.sleep(interval_ms / 1000)
        
        self._monitor_thread = threading.Thread(target=monitor, daemon=True)
        self._monitor_thread.start()
    
    def stop_monitoring(self):
        """停止资源监控"""
        self._monitor_running = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=1)
    
    def record_latency(self, duration_ms: float):
        """记录单次请求延迟"""
        with self._lock:
            self._latencies.append(duration_ms)
    
    def get_metrics(self, total_requests: int, total_duration_ms: float) -> PerformanceMetrics:
        """计算汇总性能指标"""
        with self._lock:
            latencies = sorted(self._latencies.copy())
        
        if not latencies:
            return PerformanceMetrics()
        
        n = len(latencies)
        metrics = PerformanceMetrics()
        
        # 延迟指标
        metrics.request_count = total_requests
        metrics.total_duration_ms = total_duration_ms
        metrics.avg_duration_ms = sum(latencies) / n
        metrics.min_duration_ms = latencies[0]
        metrics.max_duration_ms = latencies[-1]
        metrics.p50_duration_ms = latencies[int(n * 0.50)] if n > 0 else 0
        metrics.p90_duration_ms = latencies[int(n * 0.90)] if n > 0 else 0
        metrics.p95_duration_ms = latencies[int(n * 0.95)] if n > 0 else 0
        metrics.p99_duration_ms = latencies[int(n * 0.99)] if n > 0 else 0
        
        # 吞吐量
        metrics.throughput = total_requests / (total_duration_ms / 1000) if total_duration_ms > 0 else 0
        
        # 内存指标
        if self._memory_samples:
            metrics.avg_memory_mb = sum(self._memory_samples) / len(self._memory_samples)
            metrics.max_memory_mb = max(self._memory_samples)
        
        # CPU 指标
        if self._cpu_samples:
            metrics.avg_cpu_percent = sum(self._cpu_samples) / len(self._cpu_samples)
            metrics.max_cpu_percent = max(self._cpu_samples)
        
        return metrics
    
    def reset(self):
        """重置所有测量数据"""
        with self._lock:
            self._latencies.clear()
            self._memory_samples.clear()
            self._cpu_samples.clear()


class BenchmarkRunner:
    """基准测试运行器"""
    
    def __init__(self):
        self._results: List[BenchmarkResult] = []
        self._baseline: Optional[PerformanceMetrics] = None
    
    def _run_test_scenario(self, scenario: TestScenario, test_func: Callable) -> BenchmarkResult:
        """运行单个测试场景"""
        print(f"\n🚀 运行测试场景: {scenario.name}")
        print(f"   描述: {scenario.description}")
        print(f"   启用特性: {', '.join(scenario.enabled_features)}")
        print(f"   迭代次数: {scenario.iterations}")
        print(f"   并发数: {scenario.concurrent_workers}")
        
        profiler = PerformanceProfiler()
        profiler.start_monitoring()
        
        # 预热阶段
        print("   预热中...")
        for _ in range(scenario.warmup_iterations):
            test_func()
        
        # 正式测试阶段
        print("   测试中...")
        start_time = time.perf_counter()
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=scenario.concurrent_workers) as executor:
            futures = []
            for _ in range(scenario.iterations):
                futures.append(executor.submit(self._timed_test, test_func, profiler))
            
            concurrent.futures.wait(futures)
        
        total_duration_ms = (time.perf_counter() - start_time) * 1000
        profiler.stop_monitoring()
        
        metrics = profiler.get_metrics(scenario.iterations, total_duration_ms)
        
        # 计算与基准的对比
        result = BenchmarkResult(
            scenario_name=scenario.name,
            timestamp=time.time(),
            metrics=metrics
        )
        
        if self._baseline:
            result.overhead_percent = ((metrics.avg_duration_ms - self._baseline.avg_duration_ms) / 
                                     self._baseline.avg_duration_ms * 100)
            result.baseline_comparison = {
                'latency_overhead_pct': result.overhead_percent,
                'throughput_ratio': metrics.throughput / self._baseline.throughput,
                'memory_increase_pct': ((metrics.avg_memory_mb - self._baseline.avg_memory_mb) / 
                                       self._baseline.avg_memory_mb * 100),
                'cpu_increase_pct': ((metrics.avg_cpu_percent - self._baseline.avg_cpu_percent) / 
                                     self._baseline.avg_cpu_percent * 100 if self._baseline.avg_cpu_percent > 0 else 0)
            }
        
        self._results.append(result)
        return result
    
    def _timed_test(self, test_func: Callable, profiler: PerformanceProfiler):
        """执行单个测试并记录延迟"""
        start = time.perf_counter()
        try:
            test_func()
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            profiler.record_latency(duration_ms)
    
    def run_all_scenarios(self, scenarios: List[TestScenario], test_func: Callable):
        """运行所有测试场景"""
        print("=" * 80)
        print("📊 开始性能基准测试套件")
        print("=" * 80)
        
        for i, scenario in enumerate(scenarios):
            result = self._run_test_scenario(scenario, test_func)
            
            # 设置基准（第一个场景作为基准）
            if i == 0:
                self._baseline = result.metrics
                print(f"\n   ✅ 已设置为基准场景")
            
            self._print_result(result)
        
        print("\n" + "=" * 80)
        print("✅ 所有测试场景完成")
        print("=" * 80)
    
    def _print_result(self, result: BenchmarkResult):
        """打印单个测试结果"""
        m = result.metrics
        print("\n   📈 测试结果:")
        print(f"      请求数: {m.request_count}")
        print(f"      总耗时: {m.total_duration_ms:.2f} ms")
        print(f"      平均延迟: {m.avg_duration_ms:.4f} ms")
        print(f"      P50延迟: {m.p50_duration_ms:.4f} ms")
        print(f"      P90延迟: {m.p90_duration_ms:.4f} ms")
        print(f"      P95延迟: {m.p95_duration_ms:.4f} ms")
        print(f"      P99延迟: {m.p99_duration_ms:.4f} ms")
        print(f"      最小延迟: {m.min_duration_ms:.4f} ms")
        print(f"      最大延迟: {m.max_duration_ms:.4f} ms")
        print(f"      吞吐量: {m.throughput:.2f} req/s")
        print(f"      平均内存: {m.avg_memory_mb:.2f} MB")
        print(f"      最大内存: {m.max_memory_mb:.2f} MB")
        print(f"      平均CPU: {m.avg_cpu_percent:.2f}%")
        print(f"      最大CPU: {m.max_cpu_percent:.2f}%")
        
        if result.baseline_comparison:
            print("\n      📊 与基准对比:")
            print(f"      延迟开销: {result.overhead_percent:+.2f}%")
            print(f"      吞吐量比率: {result.baseline_comparison['throughput_ratio']:.2f}x")
            print(f"      内存增加: {result.baseline_comparison['memory_increase_pct']:+.2f}%")
            print(f"      CPU增加: {result.baseline_comparison['cpu_increase_pct']:+.2f}%")
    
    def generate_report(self) -> Dict[str, Any]:
        """生成完整测试报告"""
        report = {
            'timestamp': time.time(),
            'generated_at': datetime.now().isoformat(),
            'scenarios': [],
            'summary': {}
        }
        
        for result in self._results:
            scenario_data = {
                'name': result.scenario_name,
                'timestamp': result.timestamp,
                'metrics': {
                    'request_count': result.metrics.request_count,
                    'total_duration_ms': result.metrics.total_duration_ms,
                    'avg_duration_ms': result.metrics.avg_duration_ms,
                    'p50_duration_ms': result.metrics.p50_duration_ms,
                    'p90_duration_ms': result.metrics.p90_duration_ms,
                    'p95_duration_ms': result.metrics.p95_duration_ms,
                    'p99_duration_ms': result.metrics.p99_duration_ms,
                    'min_duration_ms': result.metrics.min_duration_ms,
                    'max_duration_ms': result.metrics.max_duration_ms,
                    'throughput': result.metrics.throughput,
                    'avg_memory_mb': result.metrics.avg_memory_mb,
                    'max_memory_mb': result.metrics.max_memory_mb,
                    'avg_cpu_percent': result.metrics.avg_cpu_percent,
                    'max_cpu_percent': result.metrics.max_cpu_percent
                },
                'overhead_percent': result.overhead_percent,
                'baseline_comparison': result.baseline_comparison
            }
            report['scenarios'].append(scenario_data)
        
        # 添加摘要统计
        if self._results:
            baseline = self._results[0]
            full_observability = None
            
            for r in self._results:
                if 'full' in r.scenario_name.lower():
                    full_observability = r
                    break
            
            report['summary'] = {
                'baseline_latency_ms': baseline.metrics.avg_duration_ms,
                'full_observability_latency_ms': full_observability.metrics.avg_duration_ms if full_observability else None,
                'max_overhead_percent': max(r.overhead_percent for r in self._results),
                'recommendation': self._generate_recommendation()
            }
        
        return report
    
    def _generate_recommendation(self) -> str:
        """生成优化建议"""
        if not self._results or not self._baseline:
            return "无法生成建议：缺少基准数据"
        
        full_overhead = None
        for r in self._results:
            if 'full' in r.scenario_name.lower():
                full_overhead = r.overhead_percent
                break
        
        if full_overhead is None:
            return "未找到全量可观测性测试结果"
        
        if full_overhead < 5:
            return "✅ 可观测性开销优秀 (<5%)，当前配置无需优化"
        elif full_overhead < 15:
            return "⚠️ 可观测性开销正常 (5-15%)，建议关注高频场景"
        elif full_overhead < 30:
            return "🔍 可观测性开销较高 (15-30%)，建议优化采样策略"
        else:
            return "❌ 可观测性开销过高 (>30%)，需要立即优化"
    
    def save_report(self, filename: Optional[str] = None):
        """保存测试报告到文件"""
        if filename is None:
            filename = f"performance_benchmark_{int(time.time())}.json"
        
        report = self.generate_report()
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        
        print(f"\n📁 测试报告已保存到: {filename}")
        return filename


# 预定义测试场景
DEFAULT_SCENARIOS = [
    TestScenario(
        name="基准测试（无可观测性）",
        description="禁用所有可观测性功能，作为性能基准",
        enabled_features=[],
        iterations=1000,
        concurrent_workers=10
    ),
    TestScenario(
        name="追踪启用",
        description="仅启用分布式追踪功能",
        enabled_features=['tracing'],
        iterations=1000,
        concurrent_workers=10
    ),
    TestScenario(
        name="指标导出",
        description="仅启用指标收集和导出",
        enabled_features=['metrics'],
        iterations=1000,
        concurrent_workers=10
    ),
    TestScenario(
        name="日志关联",
        description="仅启用日志与追踪关联",
        enabled_features=['logging'],
        iterations=1000,
        concurrent_workers=10
    ),
    TestScenario(
        name="全量可观测性",
        description="启用所有可观测性功能（追踪+指标+日志）",
        enabled_features=['tracing', 'metrics', 'logging', 'full'],
        iterations=1000,
        concurrent_workers=10
    )
]


def run_benchmark(test_func: Callable, scenarios: List[TestScenario] = None):
    """运行基准测试的便捷函数"""
    if scenarios is None:
        scenarios = DEFAULT_SCENARIOS
    
    runner = BenchmarkRunner()
    runner.run_all_scenarios(scenarios, test_func)
    return runner.generate_report()
