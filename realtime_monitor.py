#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
错误上报系统实时监控脚本
监控延迟和吞吐量指标
"""

import sys
import os
import time
import json
import threading
import statistics
import logging
from datetime import datetime
from dataclasses import dataclass, field
from typing import List, Dict, Any
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class MetricsSnapshot:
    """指标快照"""
    timestamp: float
    requests_count: int
    errors_count: int
    total_latency: float
    min_latency: float = float('inf')
    max_latency: float = 0
    latencies: List[float] = field(default_factory=list)


class RealTimeMonitor:
    """实时监控器"""
    
    def __init__(self, window_size: int = 60):
        self.window_size = window_size
        self.metrics_history: deque = deque(maxlen=window_size)
        self.total_requests = 0
        self.total_errors = 0
        self.all_latencies: List[float] = []
        self._lock = threading.Lock()
        self._running = False
        self._start_time = time.time()
        
    def record_request(self, latency: float, is_error: bool = False):
        """记录一次请求"""
        with self._lock:
            self.total_requests += 1
            if is_error:
                self.total_errors += 1
            self.all_latencies.append(latency)
            
            snapshot = MetricsSnapshot(
                timestamp=time.time(),
                requests_count=1,
                errors_count=1 if is_error else 0,
                total_latency=latency,
                min_latency=latency,
                max_latency=latency,
                latencies=[latency]
            )
            self.metrics_history.append(snapshot)
    
    def get_current_metrics(self) -> Dict[str, Any]:
        """获取当前指标"""
        with self._lock:
            if not self.all_latencies:
                return {
                    'uptime': time.time() - self._start_time,
                    'total_requests': 0,
                    'total_errors': 0,
                    'error_rate': 0.0,
                    'avg_latency_ms': 0.0,
                    'p50_latency_ms': 0.0,
                    'p95_latency_ms': 0.0,
                    'p99_latency_ms': 0.0,
                    'max_latency_ms': 0.0,
                    'min_latency_ms': 0.0,
                    'current_throughput': 0.0
                }
            
            sorted_latencies = sorted(self.all_latencies)
            n = len(sorted_latencies)
            
            current_window = list(self.metrics_history)[-10:] if self.metrics_history else []
            recent_throughput = sum(s.requests_count for s in current_window) / max(len(current_window), 1)
            
            return {
                'uptime': time.time() - self._start_time,
                'total_requests': self.total_requests,
                'total_errors': self.total_errors,
                'error_rate': self.total_errors / self.total_requests * 100,
                'avg_latency_ms': statistics.mean(self.all_latencies) * 1000,
                'p50_latency_ms': sorted_latencies[n // 2] * 1000,
                'p95_latency_ms': sorted_latencies[int(n * 0.95)] * 1000,
                'p99_latency_ms': sorted_latencies[int(n * 0.99)] * 1000,
                'max_latency_ms': max(self.all_latencies) * 1000,
                'min_latency_ms': min(self.all_latencies) * 1000,
                'current_throughput': recent_throughput
            }
    
    def print_metrics(self):
        """打印当前指标"""
        metrics = self.get_current_metrics()
        
        os.system('cls' if os.name == 'nt' else 'clear')
        
        print("=" * 80)
        print(f" Digital Life 错误上报系统 - 实时监控")
        print("=" * 80)
        print(f" 监控时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f" 运行时间: {metrics['uptime']:.1f}s")
        print()
        print(f" ┌{'─'*38}┬{'─'*38}┐")
        print(f" │ {'请求统计':^36} │ {'延迟统计 (ms)':^36} │")
        print(f" ├{'─'*38}┼{'─'*38}┤")
        print(f" │ 总请求数: {metrics['total_requests']:>15,} │ 平均延迟: {metrics['avg_latency_ms']:>16,.2f} │")
        print(f" │ 错误数:   {metrics['total_errors']:>15,} │ P50延迟:  {metrics['p50_latency_ms']:>16,.2f} │")
        print(f" │ 错误率:   {metrics['error_rate']:>14.2f}% │ P95延迟:  {metrics['p95_latency_ms']:>16,.2f} │")
        print(f" │          {' '*28} │ P99延迟:  {metrics['p99_latency_ms']:>16,.2f} │")
        print(f" │          {' '*28} │ 最大延迟: {metrics['max_latency_ms']:>16,.2f} │")
        print(f" │          {' '*28} │ 最小延迟: {metrics['min_latency_ms']:>16,.2f} │")
        print(f" ├{'─'*38}┼{'─'*38}┤")
        print(f" │ 吞吐量:   {metrics['current_throughput']:>15,.2f}/s │")
        print(f" └{'─'*38}┴{'─'*38}┘")
        print()
        print(" 按 Ctrl+C 停止监控")
        print("=" * 80)
    
    def start_monitoring(self, interval: float = 1.0):
        """开始监控"""
        self._running = True
        
        def monitor_loop():
            while self._running:
                self.print_metrics()
                time.sleep(interval)
        
        thread = threading.Thread(target=monitor_loop, daemon=True)
        thread.start()
        return thread
    
    def stop_monitoring(self):
        """停止监控"""
        self._running = False


def simulate_load(monitor: RealTimeMonitor, concurrency: int, requests_per_second: int, duration: int):
    """模拟负载"""
    from agent.monitoring import get_error_reporter, AlertLevel
    
    config = {
        'console': {'enabled': False, 'min_level': 'error'},
        'file': {'enabled': False, 'min_level': 'error'}
    }
    reporter = get_error_reporter(config)
    
    end_time = time.time() + duration
    request_interval = 1.0 / requests_per_second if requests_per_second > 0 else 0
    
    def send_request():
        while time.time() < end_time:
            try:
                error = RuntimeError(f"Monitor test error at {datetime.now().isoformat()}")
                start = time.time()
                reporter.report_error(
                    error=error,
                    level=AlertLevel.ERROR,
                    context={'test': 'realtime_monitor'}
                )
                latency = time.time() - start
                monitor.record_request(latency, is_error=False)
            except Exception as e:
                monitor.record_request(0, is_error=True)
            
            if request_interval > 0:
                time.sleep(request_interval)
    
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(send_request) for _ in range(concurrency)]
        for future in futures:
            try:
                future.result()
            except:
                pass


def main():
    parser = argparse.ArgumentParser(description="错误上报系统实时监控")
    parser.add_argument('-c', '--concurrency', type=int, default=10, help='并发数')
    parser.add_argument('-r', '--rate', type=int, default=50, help='每秒请求数')
    parser.add_argument('-d', '--duration', type=int, default=60, help='持续时间（秒）')
    parser.add_argument('-i', '--interval', type=float, default=1.0, help='刷新间隔（秒）')
    parser.add_argument('--simulate', action='store_true', help='模拟负载测试')
    
    args = parser.parse_args()
    
    monitor = RealTimeMonitor()
    
    if args.simulate:
        print(f"\n🚀 启动模拟负载测试")
        print(f"   并发: {args.concurrency}")
        print(f"   速率: {args.rate} req/s")
        print(f"   持续: {args.duration}s")
        print()
        
        monitor.start_monitoring(interval=args.interval)
        
        simulate_load(monitor, args.concurrency, args.rate, args.duration)
        
        time.sleep(2)
    else:
        print("\n📊 启动实时监控（无模拟负载）")
        print("   按 Ctrl+C 停止")
        print()
        monitor.start_monitoring(interval=args.interval)
        
        try:
            while True:
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("\n\n监控已停止")
    
    print("\n📄 最终指标:")
    print(json.dumps(monitor.get_current_metrics(), indent=2))


if __name__ == "__main__":
    main()
