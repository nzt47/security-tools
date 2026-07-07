#!/usr/bin/env python3
"""
性能指标收集模块

功能:
- 记录延迟指标（Histogram）
- 记录计数器（Counter）
- 计算统计值（avg, min, max, p95, p99）
- 线程安全的指标管理
"""

import time
import logging
import json
import uuid
import threading
from typing import Dict, List
from dataclasses import dataclass
from collections import defaultdict

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


@dataclass
class Metric:
    """指标数据
    
    Attributes:
        name: 指标名称
        value: 指标值
        timestamp: 时间戳
        labels: 标签字典
    """
    name: str
    value: float
    timestamp: float
    labels: Dict[str, str]

class MetricsCollector:
    """轻量级指标收集器
    
    功能:
    - 记录延迟指标（Histogram）
    - 记录计数器（Counter）
    - 计算统计值（avg, min, max, p95, p99）
    
    使用示例:
        collector = MetricsCollector()
        
        # 记录延迟
        collector.record_latency("latency.digital_life.chat", 0.5)
        
        # 增加计数
        collector.increment_counter("count.chat.total")
        
        # 获取统计
        stats = collector.get_stats("latency.digital_life.chat")
        print(stats)
        # {
        #     'count': 100,
        #     'sum': 50.0,
        #     'avg': 0.5,
        #     'min': 0.1,
        #     'max': 1.2,
        #     'p50': 0.45,
        #     'p95': 0.95,
        #     'p99': 1.1
        # }
    """
    
    def __init__(self):
        """初始化指标收集器"""
        self._histograms: Dict[str, List[float]] = defaultdict(list)
        self._counters: Dict[str, int] = defaultdict(int)
        # 使用 RLock（可重入锁）避免 get_all_metrics → get_stats 重入死锁
        # 历史问题：threading.Lock 不可重入，若在持锁时调用 get_stats 会永久阻塞
        self._lock = threading.RLock()
        
        logger.info("[Metrics] 指标收集器已初始化")
    
    def record_latency(self, metric_name: str, duration: float):
        """记录延迟指标
        
        Args:
            metric_name: 指标名称 (如: latency.digital_life.chat)
            duration: 持续时间（秒）
        """
        with self._lock:
            self._histograms[metric_name].append(duration)
        
        logger.debug(
            f"[Metrics] {metric_name}: {duration*1000:.2f}ms",
            extra={'metric': metric_name, 'value': duration, 'unit': 'ms'}
        )
    
    def increment_counter(self, counter_name: str, value: int = 1):
        """增加计数器
        
        Args:
            counter_name: 计数器名称
            value: 增加的值
        """
        with self._lock:
            self._counters[counter_name] += value
        
        logger.debug(
            f"[Metrics] Counter {counter_name}: {self._counters[counter_name]} (+{value})",
            extra={'counter': counter_name, 'value': self._counters[counter_name]}
        )
    
    def get_stats(self, metric_name: str) -> Dict:
        """获取指标统计
        
        Args:
            metric_name: 指标名称
        
        Returns:
            统计字典，包含:
            - count: 样本数
            - sum: 总和
            - avg: 平均值
            - min: 最小值
            - max: 最大值
            - p50: 50分位数
            - p95: 95分位数
            - p99: 99分位数
        """
        with self._lock:
            values = list(self._histograms.get(metric_name, []))
        
        if not values:
            return {
                'count': 0,
                'sum': 0,
                'avg': 0,
                'min': 0,
                'max': 0,
                'p50': 0,
                'p95': 0,
                'p99': 0
            }
        
        sorted_values = sorted(values)
        n = len(sorted_values)
        
        return {
            'count': n,
            'sum': sum(values),
            'avg': sum(values) / n,
            'min': min(values),
            'max': max(values),
            'p50': sorted_values[int(n * 0.50)],
            'p95': sorted_values[min(int(n * 0.95), n-1)],
            'p99': sorted_values[min(int(n * 0.99), n-1)]
        }
    
    def get_all_metrics(self) -> Dict:
        """获取所有指标

        Returns:
            包含所有指标的字典:
            {
                'histograms': {
                    'metric_name': {...stats...},
                    ...
                },
                'counters': {
                    'counter_name': value,
                    ...
                },
                'generated_at': timestamp
            }
        """
        # 使用 RLock 后理论上可在持锁时调用 get_stats（可重入），但为减少锁持有时间
        # 仍采用「锁内复制快照 → 锁外计算」的模式，降低锁竞争
        with self._lock:
            histogram_names = list(self._histograms.keys())
            counters = dict(self._counters)

        histograms = {
            name: self.get_stats(name)
            for name in histogram_names
        }

        return {
            'histograms': histograms,
            'counters': counters,
            'generated_at': time.time()
        }
    
    def reset(self):
        """重置所有指标"""
        with self._lock:
            self._histograms.clear()
            self._counters.clear()
        logger.info("[Metrics] 指标已重置")
    
    def get_metric_names(self) -> List[str]:
        """获取所有指标名称
        
        Returns:
            指标名称列表
        """
        with self._lock:
            return list(self._histograms.keys())
    
    def get_counter_names(self) -> List[str]:
        """获取所有计数器名称
        
        Returns:
            计数器名称列表
        """
        with self._lock:
            return list(self._counters.keys())
    
    def export_prometheus(self) -> str:
        """导出 Prometheus 格式的指标
        
        Returns:
            Prometheus 格式的文本
        """
        lines = []
        
        # 导出 Histograms
        for name, stats in self.get_all_metrics()['histograms'].items():
            if stats['count'] > 0:
                metric_name = name.replace('.', '_')
                lines.append(f"# HELP {metric_name} Latency metric")
                lines.append(f"# TYPE {metric_name} summary")
                lines.append(f'{metric_name}_sum {{service="{metric_name}"}} {stats["sum"]}')
                lines.append(f'{metric_name}_count {{service="{metric_name}"}} {stats["count"]}')
                lines.append(f'{metric_name} {{service="{metric_name}",quantile="0.95"}} {stats["p95"]}')
        
        # 导出 Counters
        for name, value in self.get_all_metrics()['counters'].items():
            metric_name = name.replace('.', '_')
            lines.append(f"# HELP {metric_name} Counter metric")
            lines.append(f"# TYPE {metric_name} counter")
            lines.append(f'{metric_name} {{service="{metric_name}"}} {value}')
        
        return '\n'.join(lines)

# 全局单例
_global_collector = MetricsCollector()

def get_metrics_collector() -> MetricsCollector:
    """获取全局指标收集器
    
    Returns:
        全局 MetricsCollector 实例
    """
    return _global_collector

def record_latency(metric_name: str, duration: float):
    """快捷函数：记录延迟指标"""
    get_metrics_collector().record_latency(metric_name, duration)

def increment_counter(counter_name: str, value: int = 1):
    """快捷函数：增加计数器"""
    get_metrics_collector().increment_counter(counter_name, value)

def get_all_metrics() -> Dict:
    """快捷函数：获取所有指标"""
    return get_metrics_collector().get_all_metrics()


def _safe_call(func, *args, action="safe_call", **kwargs):
    """安全调用包装器——捕获异常并记录结构化日志后重新抛出

    用于边界显性化：可能失败的操作应通过此包装器调用，
    确保异常被记录后再向上传播，而非静默吞掉。
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.error(json.dumps({
            "trace_id": _trace_id(),
            "module_name": "metrics",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
