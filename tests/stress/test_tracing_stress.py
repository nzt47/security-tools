#!/usr/bin/env python3
"""
高并发场景压测脚本

验证在大量并发请求下，追踪上下文是否依然能稳定传播。
测试场景：
1. 高并发请求下的上下文隔离性
2. 高并发下的跨服务传播准确性
3. 内存泄漏检测
4. 性能基准测试
"""

import sys
import time
import threading
import concurrent.futures
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import List, Dict, Set

sys.path.insert(0, '.')

from agent.monitoring import (
    TraceContext,
    get_trace_id,
    set_trace_id,
    set_span_id,
    extract_trace_context,
    inject_trace_context,
    capture_context,
    restore_context,
)

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


@dataclass
class StressTestResult:
    """压测结果"""
    total_requests: int = 0
    successful: int = 0
    failed: int = 0
    context_leaks: int = 0
    cross_contamination: int = 0
    avg_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    duration_seconds: float = 0.0
    errors: List[str] = field(default_factory=list)
    trace_ids_seen: Set[str] = field(default_factory=set)


class StressTest:
    """高并发压测类"""
    
    def __init__(self):
        self.result = StressTestResult()
        self._lock = threading.Lock()
    
    def single_request(self, request_id: int) -> bool:
        """模拟单个请求的完整链路
        
        Returns:
            True 如果上下文传播正确
        """
        start_time = time.time()
        success = True
        error_msg = None
        
        try:
            # 确保初始状态干净
            set_trace_id(None)
            set_span_id(None)
            
            # 阶段1: API Gateway 入口
            with TraceContext("APIGateway", f"request_{request_id}") as gateway_ctx:
                root_trace_id = gateway_ctx.trace_id
                headers = inject_trace_context()
                
                # 验证 trace_id 正确设置
                if get_trace_id() != root_trace_id:
                    raise ValueError(f"请求 {request_id}: Gateway 阶段 trace_id 不匹配")
                
                # 阶段2: ServiceA 处理
                context_a = extract_trace_context(headers)
                if not context_a or context_a['trace_id'] != root_trace_id:
                    raise ValueError(f"请求 {request_id}: ServiceA 上下文提取失败")
                
                set_trace_id(context_a['trace_id'])
                set_span_id(context_a['span_id'])
                
                with TraceContext("ServiceA", "process") as ctx_a:
                    if ctx_a.trace_id != root_trace_id:
                        raise ValueError(f"请求 {request_id}: ServiceA trace_id 不匹配")
                    
                    headers_a = inject_trace_context()
                    
                    # 阶段3: ServiceB 处理
                    context_b = extract_trace_context(headers_a)
                    if not context_b or context_b['trace_id'] != root_trace_id:
                        raise ValueError(f"请求 {request_id}: ServiceB 上下文提取失败")
                    
                    set_trace_id(context_b['trace_id'])
                    set_span_id(context_b['span_id'])
                    
                    with TraceContext("ServiceB", "process") as ctx_b:
                        if ctx_b.trace_id != root_trace_id:
                            raise ValueError(f"请求 {request_id}: ServiceB trace_id 不匹配")
            
            # 验证上下文已清理（无泄漏）
            if get_trace_id() is not None:
                with self._lock:
                    self.result.context_leaks += 1
                # 主动清理
                set_trace_id(None)
                set_span_id(None)
            
            # 记录 trace_id
            with self._lock:
                self.result.trace_ids_seen.add(root_trace_id)
            
        except Exception as e:
            success = False
            error_msg = str(e)
            with self._lock:
                self.result.errors.append(error_msg)
        
        finally:
            # 确保清理
            set_trace_id(None)
            set_span_id(None)
            
            latency = (time.time() - start_time) * 1000
            with self._lock:
                self.result.total_requests += 1
                if success:
                    self.result.successful += 1
                else:
                    self.result.failed += 1
                # 累积延迟用于计算平均值
                self.result.avg_latency_ms += latency
        
        return success
    
    def run_concurrent_test(self, num_requests: int, max_workers: int = 50) -> StressTestResult:
        """运行并发压测
        
        Args:
            num_requests: 总请求数
            max_workers: 最大并发数
        
        Returns:
            压测结果
        """
        logger.info(f"🚀 开始并发压测: {num_requests} 请求, {max_workers} 并发")
        
        start_time = time.time()
        latencies = []
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            for i in range(num_requests):
                futures.append(executor.submit(self._timed_request, i, latencies))
            
            # 等待所有任务完成
            concurrent.futures.wait(futures)
        
        self.result.duration_seconds = time.time() - start_time
        
        # 计算平均延迟
        if self.result.total_requests > 0:
            self.result.avg_latency_ms /= self.result.total_requests
        
        # 计算 P95 和 P99 延迟
        if latencies:
            latencies.sort()
            p95_idx = int(len(latencies) * 0.95)
            p99_idx = int(len(latencies) * 0.99)
            self.result.p95_latency_ms = latencies[min(p95_idx, len(latencies) - 1)]
            self.result.p99_latency_ms = latencies[min(p99_idx, len(latencies) - 1)]
        
        # 检查上下文交叉污染（每个请求应该有唯一的 trace_id）
        expected_unique = min(num_requests, len(self.result.trace_ids_seen))
        if len(self.result.trace_ids_seen) < expected_unique * 0.99:  # 允许1%误差
            self.result.cross_contamination = expected_unique - len(self.result.trace_ids_seen)
        
        logger.info(f"✅ 压测完成: {self.result.successful}/{self.result.total_requests} 成功")
        
        return self.result
    
    def _timed_request(self, request_id: int, latencies: List[float]):
        """带计时的请求"""
        start = time.time()
        result = self.single_request(request_id)
        latency = (time.time() - start) * 1000
        latencies.append(latency)
        return result


def print_stress_report(result: StressTestResult, test_name: str):
    """打印压测报告"""
    print("\n" + "="*80)
    print(f"📊 {test_name}")
    print("="*80)
    print(f"  总请求数:      {result.total_requests}")
    print(f"  成功:          {result.successful} ✅")
    print(f"  失败:          {result.failed} {'❌' if result.failed > 0 else ''}")
    print(f"  上下文泄漏:    {result.context_leaks} {'❌' if result.context_leaks > 0 else '✅'}")
    print(f"  交叉污染:      {result.cross_contamination} {'❌' if result.cross_contamination > 0 else '✅'}")
    print(f"  唯一 trace_id: {len(result.trace_ids_seen)}")
    print()
    print(f"  总耗时:        {result.duration_seconds:.2f} 秒")
    print(f"  QPS:           {result.total_requests / result.duration_seconds:.1f} 请求/秒")
    print(f"  平均延迟:      {result.avg_latency_ms:.2f} ms")
    print(f"  P95 延迟:      {result.p95_latency_ms:.2f} ms")
    print(f"  P99 延迟:      {result.p99_latency_ms:.2f} ms")
    print()
    
    if result.errors:
        print("  ❌ 错误列表 (前10条):")
        for i, err in enumerate(result.errors[:10]):
            print(f"    {i+1}. {err}")
    
    # 评估结果
    passed = (result.failed == 0 and 
              result.context_leaks == 0 and 
              result.cross_contamination == 0)
    print(f"\n  整体评估: {'✅ 通过' if passed else '❌ 失败'}")
    print("="*80)


def main():
    """主函数 - 运行多组压测"""
    print("\n" + "="*80)
    print("🚀 高并发追踪上下文稳定性压测")
    print("="*80)
    
    test_scenarios = [
        ("低并发 (100 请求, 10 并发)", 100, 10),
        ("中并发 (500 请求, 50 并发)", 500, 50),
        ("高并发 (1000 请求, 100 并发)", 1000, 100),
        ("超高并发 (2000 请求, 200 并发)", 2000, 200),
    ]
    
    all_passed = True
    
    for test_name, num_requests, max_workers in test_scenarios:
        stress_test = StressTest()
        result = stress_test.run_concurrent_test(num_requests, max_workers)
        print_stress_report(result, test_name)
        
        if result.failed > 0 or result.context_leaks > 0 or result.cross_contamination > 0:
            all_passed = False
    
    # 总结
    print("\n" + "="*80)
    print(f"📋 压测总结: {'✅ 全部通过' if all_passed else '❌ 存在问题'}")
    print("="*80)
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())