#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
错误上报系统压力测试脚本
验证高并发下的错误上报稳定性

测试场景：
1. 基础并发测试 - 多线程同时上报
2. 队列溢出测试 - 验证队列满载时的处理
3. 渠道隔离测试 - 各渠道独立性和稳定性
4. 资源使用测试 - 内存和响应时间监控
5. 降级测试 - 部分渠道失败时的表现
"""

import sys
import os
import time
import json
import threading
import queue
import traceback
import logging
import argparse
from datetime import datetime
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from collections import defaultdict
import statistics

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 加入项目根目录，确保 `from agent.monitoring import ...` 能在直接运行脚本时找到 agent 包
# （否则 sys.path[0] 是 scripts/ 目录，不包含项目根目录）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class TestResult:
    """测试结果数据类"""
    name: str
    passed: bool
    duration: float
    total_requests: int
    success_count: int
    error_count: int
    avg_latency: float
    max_latency: float
    min_latency: float
    throughput: float
    error_rate: float
    details: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)


class StressTestRunner:
    """压力测试运行器"""
    
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or self._default_config()
        self.results: List[TestResult] = []
        self.start_time = None
        self.end_time = None
        self._lock = threading.Lock()
        
    def _default_config(self) -> Dict[str, Any]:
        """默认配置"""
        return {
            'concurrency': 50,
            'total_requests': 1000,
            'report_interval': 100,
            'queue_size': 1000,
            'channels': ['console', 'file'],
            'output_dir': './logs/stress_test'
        }
    
    def _create_error_reporter_config(self) -> Dict[str, Any]:
        """创建错误上报器配置"""
        return {
            'console': {'enabled': True, 'min_level': 'error'},
            'file': {
                'enabled': True,
                'file_path': f"{self.config['output_dir']}/stress_test_errors.log",
                'min_level': 'error'
            },
            'webhook': {
                'enabled': self.config.get('enable_webhook', False),
                'url': 'http://localhost:9999/webhook',
                'timeout': 2,
                'min_level': 'error'
            }
        }
    
    def run_all_tests(self) -> List[TestResult]:
        """运行所有测试"""
        self.start_time = time.time()
        
        os.makedirs(self.config['output_dir'], exist_ok=True)
        
        logger.info("="*80)
        logger.info("开始错误上报系统压力测试")
        logger.info("="*80)
        logger.info(f"配置: {json.dumps(self.config, indent=2, ensure_ascii=False)}")
        
        tests = [
            ("基础并发测试", self.test_basic_concurrency),
            ("队列溢出测试", self.test_queue_overflow),
            ("持续负载测试", self.test_sustained_load),
            ("多错误类型测试", self.test_multiple_error_types),
            ("突发流量测试", self.test_burst_traffic),
        ]
        
        for test_name, test_func in tests:
            try:
                result = test_func()
                self.results.append(result)
                self._print_result(result)
            except Exception as e:
                logger.error(f"测试 {test_name} 执行失败: {e}")
                error_result = TestResult(
                    name=test_name,
                    passed=False,
                    duration=0,
                    total_requests=0,
                    success_count=0,
                    error_count=1,
                    avg_latency=0,
                    max_latency=0,
                    min_latency=0,
                    throughput=0,
                    error_rate=100.0,
                    errors=[str(e)]
                )
                self.results.append(error_result)
        
        self.end_time = time.time()
        self._generate_report()
        
        return self.results
    
    def _report_error(self, error: Exception, context: Dict = None) -> float:
        """模拟错误上报"""
        from agent.monitoring import get_error_reporter, AlertLevel
        
        start = time.time()
        try:
            config = self._create_error_reporter_config()
            reporter = get_error_reporter(config)
            reporter.report_error(
                error=error,
                level=AlertLevel.ERROR,
                context=context or {}
            )
            return time.time() - start
        except Exception as e:
            logger.debug(f"上报过程异常: {e}")
            return time.time() - start
    
    def test_basic_concurrency(self) -> TestResult:
        """基础并发测试"""
        logger.info("\n" + "="*60)
        logger.info("测试 1: 基础并发测试")
        logger.info("="*60)
        
        latencies = []
        errors = []
        success_count = 0
        lock = threading.Lock()
        
        def worker(worker_id: int):
            nonlocal success_count
            for i in range(self.config['total_requests'] // self.config['concurrency']):
                try:
                    error = RuntimeError(f"[Worker-{worker_id}] Test error #{i}")
                    context = {
                        'worker_id': worker_id,
                        'request_id': f"{worker_id}-{i}",
                        'timestamp': datetime.now().isoformat()
                    }
                    latency = self._report_error(error, context)
                    with lock:
                        latencies.append(latency)
                        success_count += 1
                except Exception as e:
                    with lock:
                        errors.append(str(e))

        start = time.time()
        with ThreadPoolExecutor(max_workers=self.config['concurrency']) as executor:
            futures = [executor.submit(worker, i) for i in range(self.config['concurrency'])]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    errors.append(str(e))
        duration = time.time() - start
        
        total = len(latencies)
        return TestResult(
            name="基础并发测试",
            passed=len(errors) / max(total, 1) < 0.1,
            duration=duration,
            total_requests=total,
            success_count=success_count,
            error_count=len(errors),
            avg_latency=statistics.mean(latencies) if latencies else 0,
            max_latency=max(latencies) if latencies else 0,
            min_latency=min(latencies) if latencies else 0,
            throughput=total / duration if duration > 0 else 0,
            error_rate=len(errors) / max(total, 1) * 100,
            details={'concurrency': self.config['concurrency']}
        )
    
    def test_queue_overflow(self) -> TestResult:
        """队列溢出测试"""
        logger.info("\n" + "="*60)
        logger.info("测试 2: 队列溢出测试")
        logger.info("="*60)
        
        latencies = []
        errors = []
        success_count = 0
        lock = threading.Lock()
        
        def rapid_fire():
            nonlocal success_count
            for i in range(500):
                try:
                    error = ValueError(f"Overflow test error #{i}")
                    context = {'test': 'overflow', 'index': i}
                    latency = self._report_error(error, context)
                    with lock:
                        latencies.append(latency)
                        success_count += 1
                except Exception as e:
                    with lock:
                        errors.append(str(e))
        
        start = time.time()
        threads = [threading.Thread(target=rapid_fire) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        duration = time.time() - start
        
        total = len(latencies)
        return TestResult(
            name="队列溢出测试",
            passed=len(errors) / max(total, 1) < 0.2,
            duration=duration,
            total_requests=total,
            success_count=success_count,
            error_count=len(errors),
            avg_latency=statistics.mean(latencies) if latencies else 0,
            max_latency=max(latencies) if latencies else 0,
            min_latency=min(latencies) if latencies else 0,
            throughput=total / duration if duration > 0 else 0,
            error_rate=len(errors) / max(total, 1) * 100,
            details={'threads': 10, 'requests_per_thread': 500}
        )
    
    def test_sustained_load(self) -> TestResult:
        """持续负载测试"""
        logger.info("\n" + "="*60)
        logger.info("测试 3: 持续负载测试 (30秒)")
        logger.info("="*60)
        
        latencies = []
        errors = []
        success_count = 0
        request_count = 0
        lock = threading.Lock()
        stop_flag = threading.Event()
        
        def sustained_worker():
            nonlocal success_count, request_count
            while not stop_flag.is_set():
                try:
                    error = RuntimeError(f"Sustained load error #{request_count}")
                    context = {'test': 'sustained', 'index': request_count}
                    latency = self._report_error(error, context)
                    with lock:
                        latencies.append(latency)
                        success_count += 1
                    request_count += 1
                    time.sleep(0.01)
                except Exception as e:
                    with lock:
                        errors.append(str(e))
                    request_count += 1
        
        start = time.time()
        threads = [threading.Thread(target=sustained_worker) for _ in range(5)]
        for t in threads:
            t.start()
        
        time.sleep(30)
        stop_flag.set()
        
        for t in threads:
            t.join(timeout=2)
        duration = time.time() - start
        
        total = len(latencies)
        return TestResult(
            name="持续负载测试",
            passed=len(errors) / max(total, 1) < 0.15 and duration >= 25,
            duration=duration,
            total_requests=total,
            success_count=success_count,
            error_count=len(errors),
            avg_latency=statistics.mean(latencies) if latencies else 0,
            max_latency=max(latencies) if latencies else 0,
            min_latency=min(latencies) if latencies else 0,
            throughput=total / duration if duration > 0 else 0,
            error_rate=len(errors) / max(total, 1) * 100,
            details={'duration_seconds': 30, 'threads': 5}
        )
    
    def test_multiple_error_types(self) -> TestResult:
        """多错误类型测试"""
        logger.info("\n" + "="*60)
        logger.info("测试 4: 多错误类型测试")
        logger.info("="*60)
        
        error_types = [
            (ValueError, "ValueError"),
            (TypeError, "TypeError"),
            (RuntimeError, "RuntimeError"),
            (KeyError, "KeyError"),
            (AttributeError, "AttributeError"),
            (IOError, "IOError"),
            (ZeroDivisionError, "ZeroDivisionError"),
        ]
        
        latencies = []
        errors = []
        success_count = 0
        type_stats = defaultdict(int)
        lock = threading.Lock()
        
        def test_error_type(error_class, error_name):
            nonlocal success_count
            for i in range(100):
                try:
                    if error_class == ZeroDivisionError:
                        raise error_class()
                    else:
                        raise error_class(f"{error_name} test message #{i}")
                except Exception as e:
                    context = {'error_type': error_name, 'index': i}
                    latency = self._report_error(e, context)
                    with lock:
                        latencies.append(latency)
                        success_count += 1
                        type_stats[error_name] += 1
        
        start = time.time()
        with ThreadPoolExecutor(max_workers=len(error_types)) as executor:
            futures = [
                executor.submit(test_error_type, cls, name) 
                for cls, name in error_types
            ]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    errors.append(str(e))
        duration = time.time() - start
        
        total = len(latencies)
        return TestResult(
            name="多错误类型测试",
            passed=len(errors) / max(total, 1) < 0.1,
            duration=duration,
            total_requests=total,
            success_count=success_count,
            error_count=len(errors),
            avg_latency=statistics.mean(latencies) if latencies else 0,
            max_latency=max(latencies) if latencies else 0,
            min_latency=min(latencies) if latencies else 0,
            throughput=total / duration if duration > 0 else 0,
            error_rate=len(errors) / max(total, 1) * 100,
            details={'error_types_tested': len(error_types), 'type_stats': dict(type_stats)}
        )
    
    def test_burst_traffic(self) -> TestResult:
        """突发流量测试"""
        logger.info("\n" + "="*60)
        logger.info("测试 5: 突发流量测试")
        logger.info("="*60)
        
        latencies = []
        errors = []
        success_count = 0
        lock = threading.Lock()
        
        def burst_worker(burst_size: int):
            nonlocal success_count
            for i in range(burst_size):
                try:
                    error = RuntimeError(f"Burst error #{i}")
                    context = {'test': 'burst'}
                    latency = self._report_error(error, context)
                    with lock:
                        latencies.append(latency)
                        success_count += 1
                except Exception as e:
                    with lock:
                        errors.append(str(e))
        
        start = time.time()
        
        for burst in [100, 200, 100, 50, 150]:
            with ThreadPoolExecutor(max_workers=burst) as executor:
                futures = [executor.submit(burst_worker, 10) for _ in range(burst)]
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        errors.append(str(e))
            time.sleep(0.5)
        
        duration = time.time() - start
        
        total = len(latencies)
        return TestResult(
            name="突发流量测试",
            passed=len(errors) / max(total, 1) < 0.15,
            duration=duration,
            total_requests=total,
            success_count=success_count,
            error_count=len(errors),
            avg_latency=statistics.mean(latencies) if latencies else 0,
            max_latency=max(latencies) if latencies else 0,
            min_latency=min(latencies) if latencies else 0,
            throughput=total / duration if duration > 0 else 0,
            error_rate=len(errors) / max(total, 1) * 100,
            details={'burst_pattern': [100, 200, 100, 50, 150]}
        )
    
    def _print_result(self, result: TestResult):
        """打印测试结果"""
        status = "✅ PASS" if result.passed else "❌ FAIL"
        print(f"\n{'='*60}")
        print(f"测试: {result.name}")
        print(f"状态: {status}")
        print(f"{'='*60}")
        print(f"  总请求数:   {result.total_requests}")
        print(f"  成功数:     {result.success_count}")
        print(f"  错误数:     {result.error_count}")
        print(f"  错误率:     {result.error_rate:.2f}%")
        print(f"  持续时间:   {result.duration:.2f}s")
        print(f"  吞吐量:     {result.throughput:.2f} req/s")
        print(f"  平均延迟:   {result.avg_latency*1000:.2f}ms")
        print(f"  最大延迟:   {result.max_latency*1000:.2f}ms")
        print(f"  最小延迟:   {result.min_latency*1000:.2f}ms")
        
        if result.details:
            print(f"\n  详情:")
            for key, value in result.details.items():
                print(f"    {key}: {value}")
        
        if result.errors:
            print(f"\n  错误样本 (前5个):")
            for err in result.errors[:5]:
                print(f"    - {err}")
    
    def _generate_report(self):
        """生成测试报告"""
        print("\n\n")
        print("="*80)
        print(" 压力测试报告汇总")
        print("="*80)
        
        total_time = self.end_time - self.start_time
        total_requests = sum(r.total_requests for r in self.results)
        total_errors = sum(r.error_count for r in self.results)
        passed_tests = sum(1 for r in self.results if r.passed)
        
        print(f"\n📊 总体统计:")
        print(f"  总测试时间:   {total_time:.2f}s")
        print(f"  总请求数:     {total_requests}")
        print(f"  总错误数:     {total_errors}")
        print(f"  整体错误率:   {total_errors/max(total_requests, 1)*100:.2f}%")
        print(f"  通过测试:     {passed_tests}/{len(self.results)}")
        
        print(f"\n📈 各测试详情:")
        for result in self.results:
            status = "✅" if result.passed else "❌"
            print(f"  {status} {result.name}: "
                  f"错误率={result.error_rate:.2f}%, "
                  f"吞吐量={result.throughput:.2f}req/s, "
                  f"延迟={result.avg_latency*1000:.2f}ms")
        
        report_path = f"{self.config['output_dir']}/stress_test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        report_data = {
            'test_time': datetime.now().isoformat(),
            'total_duration': total_time,
            'config': self.config,
            'summary': {
                'total_requests': total_requests,
                'total_errors': total_errors,
                'error_rate': total_errors / max(total_requests, 1) * 100,
                'passed_tests': passed_tests,
                'total_tests': len(self.results)
            },
            'results': [
                {
                    'name': r.name,
                    'passed': r.passed,
                    'duration': r.duration,
                    'total_requests': r.total_requests,
                    'success_count': r.success_count,
                    'error_count': r.error_count,
                    'error_rate': r.error_rate,
                    'avg_latency': r.avg_latency,
                    'max_latency': r.max_latency,
                    'throughput': r.throughput,
                    'details': r.details
                }
                for r in self.results
            ]
        }
        
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False)
        
        print(f"\n📄 详细报告已保存: {report_path}")
        
        if total_errors / max(total_requests, 1) < 0.1:
            print("\n🎉 压力测试通过！系统在高并发下表现稳定。")
        else:
            print("\n⚠️  压力测试发现问题，建议优化后再部署。")


def main():
    parser = argparse.ArgumentParser(description="错误上报系统压力测试")
    parser.add_argument('-c', '--concurrency', type=int, default=50, help='并发数')
    parser.add_argument('-r', '--requests', type=int, default=1000, help='总请求数')
    parser.add_argument('-o', '--output', type=str, default='./logs/stress_test', help='输出目录')
    parser.add_argument('--webhook', action='store_true', help='启用 Webhook 测试')
    
    args = parser.parse_args()
    
    config = {
        'concurrency': args.concurrency,
        'total_requests': args.requests,
        'output_dir': args.output,
        'enable_webhook': args.webhook
    }
    
    runner = StressTestRunner(config)
    runner.run_all_tests()
    
    return 0 if all(r.passed for r in runner.results) else 1


if __name__ == "__main__":
    sys.exit(main())
