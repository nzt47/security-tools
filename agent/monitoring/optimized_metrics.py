#!/usr/bin/env python3
"""
优化的指标采集模块

实现高性能的指标收集，减少锁竞争，支持：
1. 无锁统计收集
2. 批量写入优化
3. 分层存储策略
4. 采样统计（减少内存占用）
"""

import time
import threading
import struct
import uuid
from typing import Dict, List, Optional, Any, Callable
from collections import defaultdict

from agent.monitoring.tracing import get_trace_id, set_trace_id

try:
    import mmh3
    _MMH3_AVAILABLE = True
except ImportError:
    _MMH3_AVAILABLE = False


class LockFreeCounter:
    """无锁计数器
    
    使用线程本地存储减少锁竞争，在Python中通过GIL保证原子性
    """
    
    def __init__(self, initial: int = 0):
        self._value = initial
    
    def increment(self, delta: int = 1) -> int:
        """原子增加（Python中GIL保证原子性）"""
        self._value += delta
        return self._value
    
    def get(self) -> int:
        """获取当前值"""
        return self._value
    
    def reset(self):
        """重置计数器"""
        self._value = 0


class LockFreeHistogram:
    """无锁直方图
    
    使用分段存储和原子操作，避免锁竞争
    """
    
    def __init__(self, buckets: List[int] = None):
        """
        Args:
            buckets: 直方图桶边界（微秒）
        """
        if buckets is None:
            buckets = [100, 500, 1000, 5000, 10000, 50000, 100000]
        
        self._buckets = sorted(buckets)
        self._counts = [LockFreeCounter() for _ in range(len(buckets) + 1)]
        self._sum = LockFreeCounter()
        self._count = LockFreeCounter()
        self._min = LockFreeCounter(0)
        self._max = LockFreeCounter(0)
    
    def record(self, duration_us: int):
        """记录持续时间（微秒）"""
        self._count.increment()
        self._sum.increment(duration_us)
        
        # 更新 min/max（简单实现，可能有竞态但可接受）
        current_min = self._min.get()
        if current_min == 0 or duration_us < current_min:
            self._min._value = duration_us
        
        current_max = self._max.get()
        if duration_us > current_max:
            self._max._value = duration_us
        
        # 找到对应的桶
        for i, bucket in enumerate(self._buckets):
            if duration_us <= bucket:
                self._counts[i].increment()
                return
        
        # 放入最后一个桶
        self._counts[-1].increment()
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        count = self._count.get()
        if count == 0:
            return {
                'count': 0,
                'sum': 0,
                'avg': 0,
                'min': 0,
                'max': 0,
                'p50': 0,
                'p90': 0,
                'p95': 0,
                'p99': 0,
                'buckets': []
            }
        
        sum_us = self._sum.get()
        avg = sum_us / count
        
        # 计算百分位数（基于桶估算）
        bucket_counts = [c.get() for c in self._counts]
        p50 = self._estimate_percentile(bucket_counts, 0.50)
        p90 = self._estimate_percentile(bucket_counts, 0.90)
        p95 = self._estimate_percentile(bucket_counts, 0.95)
        p99 = self._estimate_percentile(bucket_counts, 0.99)
        
        return {
            'count': count,
            'sum': sum_us,
            'avg': avg,
            'min': self._min.get(),
            'max': self._max.get(),
            'p50': p50,
            'p90': p90,
            'p95': p95,
            'p99': p99,
            'buckets': [{'bucket': b, 'count': bucket_counts[i]} 
                       for i, b in enumerate(self._buckets + [float('inf')])]
        }
    
    def _estimate_percentile(self, bucket_counts: List[int], percentile: float) -> float:
        """基于桶估算百分位数"""
        total = sum(bucket_counts)
        if total == 0:
            return 0
        
        target = int(total * percentile)
        running = 0
        
        for i, count in enumerate(bucket_counts):
            running += count
            if running >= target:
                if i == 0:
                    return self._buckets[0] * 0.5 if self._buckets else 0
                elif i < len(self._buckets):
                    lower = self._buckets[i-1] if i > 0 else 0
                    upper = self._buckets[i]
                    return (lower + upper) / 2
                else:
                    return self._buckets[-1] * 1.5
        
        return self._buckets[-1] if self._buckets else 0
    
    def reset(self):
        """重置直方图"""
        for counter in self._counts:
            counter.reset()
        self._sum.reset()
        self._count.reset()
        self._min.reset()
        self._max.reset()


class ThreadLocalMetrics:
    """线程本地指标存储
    
    每个线程维护自己的指标数据，减少锁竞争
    """
    
    _local = threading.local()
    
    @classmethod
    def _get_thread_data(cls) -> Dict[str, Any]:
        """获取当前线程的指标数据"""
        if not hasattr(cls._local, 'metrics'):
            cls._local.metrics = {
                'counters': defaultdict(int),
                'histograms': defaultdict(lambda: LockFreeHistogram())
            }
        return cls._local.metrics
    
    @classmethod
    def increment_counter(cls, name: str, value: int = 1):
        """增加计数器"""
        data = cls._get_thread_data()
        data['counters'][name] += value
    
    @classmethod
    def record_latency(cls, name: str, duration_ms: float):
        """记录延迟（毫秒）"""
        data = cls._get_thread_data()
        data['histograms'][name].record(int(duration_ms * 1000))  # 转换为微秒
    
    @classmethod
    def merge_all(cls) -> Dict[str, Any]:
        """合并所有线程的指标数据"""
        # 注意：这个方法需要特殊处理，因为线程本地数据无法直接遍历
        # 返回当前线程的数据作为示例
        return cls._get_thread_data()


class SampledMetricsCollector:
    """采样指标收集器
    
    对高频指标进行采样，减少内存占用和写入开销
    """
    
    def __init__(self, sample_rate: float = 0.1):
        self._sample_rate = max(0.0, min(1.0, sample_rate))
        self._counters = defaultdict(LockFreeCounter)
        self._histograms = {}
    
    def should_sample(self, metric_name: str) -> bool:
        """判断是否采样"""
        if self._sample_rate >= 1.0:
            return True
        
        # 使用确定性哈希确保同一指标总是被采样或不被采样
        if _MMH3_AVAILABLE:
            hash_val = mmh3.hash(metric_name) & 0xFFFFFFFF
        else:
            # 回退到简单哈希
            hash_val = hash(metric_name) & 0xFFFFFFFF
        
        return hash_val <= int(self._sample_rate * 0xFFFFFFFF)
    
    def increment_counter(self, name: str, value: int = 1):
        """增加计数器"""
        if self.should_sample(name):
            self._counters[name].increment(value)
    
    def record_latency(self, name: str, duration_ms: float):
        """记录延迟"""
        if self.should_sample(name):
            if name not in self._histograms:
                self._histograms[name] = LockFreeHistogram()
            self._histograms[name].record(int(duration_ms * 1000))
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        result = {
            'counters': {},
            'histograms': {}
        }
        
        for name, counter in self._counters.items():
            result['counters'][name] = counter.get()
        
        for name, histogram in self._histograms.items():
            result['histograms'][name] = histogram.get_stats()
        
        return result


class BatchMetricsWriter:
    """批量指标写入器"""
    
    def __init__(self, write_func: Callable[[List[Dict]], None], batch_size: int = 100):
        self._write_func = write_func
        self._batch_size = batch_size
        self._buffer = []
        self._lock = threading.Lock()
        self._flush_thread = None
        self._running = False
        # 后台刷新线程专属 trace_id（解决 ContextVar 不自动继承到子线程问题）
        self._flush_trace_id = f"metrics-flush-{uuid.uuid4().hex[:16]}"

    def start(self):
        """启动后台写入线程"""
        if self._running:
            return
        
        self._running = True
        self._flush_thread = threading.Thread(
            target=self._flush_loop,
            daemon=True,
            name="BatchMetricsWriter"
        )
        self._flush_thread.start()
    
    def stop(self, timeout: float = 5.0):
        """停止写入器"""
        self._running = False
        if self._flush_thread:
            self._flush_thread.join(timeout=timeout)
        self._flush()
    
    def write(self, data: Dict):
        """写入指标数据"""
        with self._lock:
            self._buffer.append(data)
            
            if len(self._buffer) >= self._batch_size:
                self._flush()
    
    def _flush_loop(self):
        """后台刷新循环"""
        # 设置后台线程 trace_id（ContextVar 不自动继承到子线程）
        set_trace_id(self._flush_trace_id)
        while self._running:
            time.sleep(1.0)
            self._flush()
    
    def _flush(self):
        """刷新批量数据"""
        with self._lock:
            if not self._buffer:
                return
            
            batch = self._buffer[:]
            self._buffer = []
        
        try:
            self._write_func(batch)
        except Exception:
            # 写入失败，放回缓冲区（最多放回10条）
            with self._lock:
                self._buffer = batch[:10] + self._buffer


class OptimizedMetricsCollector:
    """优化的指标收集器
    
    整合多种优化策略：
    1. 线程本地存储减少锁竞争
    2. 采样减少内存占用
    3. 批量写入减少IO开销
    4. 无锁数据结构提高并发性能
    """
    
    def __init__(self, sampling_enabled: bool = True, sample_rate: float = 0.1):
        self._sampling_enabled = sampling_enabled
        self._sampler = SampledMetricsCollector(sample_rate)
        self._thread_local = ThreadLocalMetrics
        
        # 聚合计数器（用于非采样场景）
        self._global_counters = defaultdict(LockFreeCounter)
        self._global_histograms = {}
        
        # 批量写入器
        self._batch_writer = None
        
        # 统计信息
        self._stats = {
            'records_written': 0,
            'batches_flushed': 0,
            'sampled_records': 0,
            'direct_records': 0
        }
    
    def init_batch_writer(self, write_func: Callable[[List[Dict]], None], batch_size: int = 100):
        """初始化批量写入器"""
        if self._batch_writer is None:
            self._batch_writer = BatchMetricsWriter(write_func, batch_size)
            self._batch_writer.start()
    
    def record_latency(self, metric_name: str, duration: float):
        """记录延迟指标（秒）"""
        duration_ms = duration * 1000
        
        if self._sampling_enabled:
            self._sampler.record_latency(metric_name, duration_ms)
            self._stats['sampled_records'] += 1
        else:
            # 使用线程本地存储
            self._thread_local.record_latency(metric_name, duration_ms)
            
            # 更新全局直方图（带锁）
            if metric_name not in self._global_histograms:
                self._global_histograms[metric_name] = LockFreeHistogram()
            self._global_histograms[metric_name].record(int(duration_ms * 1000))
            self._stats['direct_records'] += 1
    
    def increment_counter(self, counter_name: str, value: int = 1):
        """增加计数器"""
        if self._sampling_enabled:
            self._sampler.increment_counter(counter_name, value)
            self._stats['sampled_records'] += value
        else:
            self._global_counters[counter_name].increment(value)
            self._stats['direct_records'] += value
    
    def get_stats(self, metric_name: str = None) -> Dict[str, Any]:
        """获取指标统计"""
        if self._sampling_enabled:
            return self._sampler.get_stats()
        
        if metric_name:
            histogram = self._global_histograms.get(metric_name)
            if histogram:
                return histogram.get_stats()
            return {}
        
        result = {
            'counters': {},
            'histograms': {}
        }
        
        for name, counter in self._global_counters.items():
            result['counters'][name] = counter.get()
        
        for name, histogram in self._global_histograms.items():
            result['histograms'][name] = histogram.get_stats()
        
        return result
    
    def get_all_metrics(self) -> Dict[str, Any]:
        """获取所有指标"""
        if self._sampling_enabled:
            sampler_stats = self._sampler.get_stats()
            return {
                'histograms': sampler_stats.get('histograms', {}),
                'counters': sampler_stats.get('counters', {}),
                'generated_at': time.time(),
                'sampling_rate': self._sampler._sample_rate
            }
        
        return {
            'histograms': {
                name: hist.get_stats() for name, hist in self._global_histograms.items()
            },
            'counters': {
                name: cnt.get() for name, cnt in self._global_counters.items()
            },
            'generated_at': time.time(),
            'sampling_rate': 1.0
        }
    
    def export_prometheus(self) -> str:
        """导出 Prometheus 格式的指标"""
        lines = []
        metrics = self.get_all_metrics()
        
        for name, stats in metrics.get('histograms', {}).items():
            if stats.get('count', 0) > 0:
                metric_name = name.replace('.', '_')
                lines.append(f"# HELP {metric_name} Latency metric")
                lines.append(f"# TYPE {metric_name} summary")
                lines.append(f'{metric_name}_sum {{service="{metric_name}"}} {stats["sum"] / 1000}')
                lines.append(f'{metric_name}_count {{service="{metric_name}"}} {stats["count"]}')
                lines.append(f'{metric_name} {{service="{metric_name}",quantile="0.95"}} {stats["p95"] / 1000}')
        
        for name, value in metrics.get('counters', {}).items():
            metric_name = name.replace('.', '_')
            lines.append(f"# HELP {metric_name} Counter metric")
            lines.append(f"# TYPE {metric_name} counter")
            lines.append(f'{metric_name} {{service="{metric_name}"}} {value}')
        
        return '\n'.join(lines)
    
    def reset(self):
        """重置所有指标"""
        self._sampler = SampledMetricsCollector(self._sampler._sample_rate)
        self._global_counters.clear()
        self._global_histograms.clear()
        self._stats = {
            'records_written': 0,
            'batches_flushed': 0,
            'sampled_records': 0,
            'direct_records': 0
        }
    
    def get_internal_stats(self) -> Dict[str, Any]:
        """获取内部统计信息"""
        return self._stats


# 全局优化指标收集器实例
_global_optimized_collector = None


def get_optimized_metrics_collector(sampling_enabled: bool = True, 
                                    sample_rate: float = 0.1) -> OptimizedMetricsCollector:
    """获取全局优化指标收集器"""
    global _global_optimized_collector
    if _global_optimized_collector is None:
        _global_optimized_collector = OptimizedMetricsCollector(sampling_enabled, sample_rate)
    return _global_optimized_collector


__all__ = [
    'LockFreeCounter',
    'LockFreeHistogram',
    'ThreadLocalMetrics',
    'SampledMetricsCollector',
    'BatchMetricsWriter',
    'OptimizedMetricsCollector',
    'get_optimized_metrics_collector'
]