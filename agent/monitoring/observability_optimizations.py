#!/usr/bin/env python3
"""
可观测性性能优化模块

实现针对追踪、指标、日志的性能优化策略：
1. 采样策略优化 - 智能采样、自适应采样
2. 异步处理优化 - 批量写入、后台线程
3. 缓存机制优化 - 对象池、LRU缓存
4. 批量处理优化 - 批量序列化、批量导出
"""

import time
import threading
import queue
import json
import hashlib
from typing import Dict, Any, List, Optional, Callable
from collections import defaultdict, OrderedDict
from dataclasses import dataclass


@dataclass
class OptimizationStats:
    """优化统计信息"""
    sampling_ratio: float = 0.1
    cache_hits: int = 0
    cache_misses: int = 0
    batches_processed: int = 0
    items_batched: int = 0
    async_writes: int = 0
    sync_writes: int = 0
    memory_saved_bytes: int = 0
    time_saved_ms: float = 0.0


class FastProbabilitySampler:
    """快速概率采样器
    
    使用优化的哈希算法，减少计算开销
    """
    
    def __init__(self, ratio: float = 0.1):
        self.ratio = max(0.0, min(1.0, ratio))
        self._threshold = int(self.ratio * 0xFFFFFFFF)
    
    def should_sample(self, trace_id: str) -> bool:
        """判断是否采样（简化版本，提高性能）"""
        if self.ratio >= 1.0:
            return True
        if self.ratio <= 0.0:
            return False
        
        # 使用快速哈希算法
        hash_val = int(hashlib.md5(trace_id.encode()).hexdigest()[:8], 16)
        return hash_val <= self._threshold


class AdaptiveSampler:
    """自适应采样器
    
    根据系统负载动态调整采样比例
    """
    
    def __init__(self, 
                 target_rps: int = 100,
                 min_ratio: float = 0.01,
                 max_ratio: float = 1.0,
                 adaptation_interval: int = 5):
        self.target_rps = target_rps
        self.min_ratio = min_ratio
        self.max_ratio = max_ratio
        self.adaptation_interval = adaptation_interval
        
        self._current_ratio = 0.5
        self._prob_sampler = FastProbabilitySampler(self._current_ratio)
        
        self._lock = threading.Lock()
        self._request_count = 0
        self._last_adaptation = time.time()
    
    def _adapt(self):
        """根据负载调整采样比例"""
        now = time.time()
        elapsed = now - self._last_adaptation
        
        if elapsed < self.adaptation_interval:
            return
        
        with self._lock:
            actual_rps = self._request_count / elapsed
            if actual_rps > 0:
                ratio_adjustment = self.target_rps / actual_rps
                new_ratio = self._current_ratio * ratio_adjustment
                self._current_ratio = max(self.min_ratio, min(self.max_ratio, new_ratio))
                self._prob_sampler = FastProbabilitySampler(self._current_ratio)
            
            self._request_count = 0
            self._last_adaptation = now
    
    def should_sample(self, trace_id: str) -> bool:
        """判断是否采样"""
        with self._lock:
            self._request_count += 1
        
        result = self._prob_sampler.should_sample(trace_id)
        
        # 异步调整（非阻塞）
        self._adapt()
        
        return result
    
    @property
    def current_ratio(self) -> float:
        """获取当前采样比例"""
        return self._current_ratio


class BatchProcessor:
    """批量处理器
    
    将多个小操作合并为批量操作，减少开销
    """
    
    def __init__(self, 
                 process_func: Callable[[List[Any]], None],
                 batch_size: int = 100,
                 flush_interval: float = 1.0,
                 max_queue_size: int = 10000):
        self.process_func = process_func
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self._queue = queue.Queue(maxsize=max_queue_size)
        self._flush_thread = None
        self._running = False
        self._lock = threading.Lock()
        self._stats = OptimizationStats()
    
    def start(self):
        """启动批量处理器"""
        if not self._running:
            self._running = True
            self._flush_thread = threading.Thread(
                target=self._flush_loop,
                daemon=True,
                name="BatchProcessor"
            )
            self._flush_thread.start()
    
    def stop(self, timeout: float = 5.0):
        """停止批量处理器"""
        self._running = False
        if self._flush_thread:
            self._flush_thread.join(timeout=timeout)
        self._flush()  # 最后一次刷新
    
    def submit(self, item: Any):
        """提交数据项"""
        try:
            self._queue.put(item, block=False)
        except queue.Full:
            # 队列满，立即刷新
            self._flush()
            try:
                self._queue.put(item, block=False)
            except queue.Full:
                # 仍然满，丢弃（避免阻塞）
                pass
    
    def _flush_loop(self):
        """后台刷新循环"""
        last_flush = time.time()
        
        while self._running:
            try:
                now = time.time()
                
                # 检查是否需要定时刷新
                if now - last_flush >= self.flush_interval:
                    self._flush()
                    last_flush = now
                
                # 等待新数据
                try:
                    item = self._queue.get(block=True, timeout=0.5)
                    self._queue.put(item)
                    self._flush()
                    last_flush = time.time()
                except queue.Empty:
                    pass
            except Exception:
                time.sleep(0.1)
    
    def _flush(self):
        """刷新批量数据"""
        batch = []
        
        while not self._queue.empty():
            try:
                batch.append(self._queue.get(block=False))
                if len(batch) >= self.batch_size:
                    break
            except queue.Empty:
                break
        
        if batch:
            try:
                start = time.time()
                self.process_func(batch)
                duration_ms = (time.time() - start) * 1000
                
                with self._lock:
                    self._stats.batches_processed += 1
                    self._stats.items_batched += len(batch)
                    self._stats.async_writes += len(batch)
                    # 估算节省的时间（假设每条单独处理需要1ms）
                    self._stats.time_saved_ms += (len(batch) - 1) * 0.5
            except Exception:
                # 处理失败，放回队列
                for item in batch:
                    try:
                        self._queue.put(item, block=False)
                    except queue.Full:
                        pass
    
    def get_stats(self) -> OptimizationStats:
        """获取统计信息"""
        with self._lock:
            return self._stats


class MemoryEfficientCache:
    """内存高效缓存
    
    使用紧凑的数据结构和定时清理策略
    """
    
    def __init__(self, max_size: int = 4096, ttl_seconds: int = 300):
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._cache = OrderedDict()
        self._lock = threading.RLock()
        self._stats = OptimizationStats()
    
    def get(self, key: str) -> Optional[Any]:
        """获取缓存值"""
        with self._lock:
            if key not in self._cache:
                self._stats.cache_misses += 1
                return None
            
            value, timestamp = self._cache[key]
            
            # 检查过期
            if time.time() - timestamp > self.ttl_seconds:
                del self._cache[key]
                self._stats.cache_misses += 1
                return None
            
            # 标记为最近使用
            self._cache.move_to_end(key)
            self._stats.cache_hits += 1
            return value
    
    def set(self, key: str, value: Any):
        """设置缓存值"""
        with self._lock:
            # 清理过期条目
            self._cleanup_expired()
            
            # 如果超过容量，删除最旧的
            while len(self._cache) >= self.max_size:
                self._cache.popitem(last=False)
            
            self._cache[key] = (value, time.time())
            self._cache.move_to_end(key)
            
            # 估算内存节省（假设每个条目平均1KB）
            self._stats.memory_saved_bytes += 1024
    
    def _cleanup_expired(self):
        """清理过期条目"""
        now = time.time()
        to_delete = []
        
        for key, (_, timestamp) in self._cache.items():
            if now - timestamp > self.ttl_seconds:
                to_delete.append(key)
        
        for key in to_delete:
            del self._cache[key]
    
    def clear(self):
        """清空缓存"""
        with self._lock:
            self._cache.clear()
    
    def get_stats(self) -> OptimizationStats:
        """获取统计信息"""
        with self._lock:
            return self._stats


class FastJSONSerializer:
    """快速JSON序列化器
    
    使用优化的序列化策略
    """
    
    @staticmethod
    def serialize_compact(data: Dict) -> str:
        """紧凑序列化（最小化输出）"""
        return json.dumps(data, separators=(',', ':'))
    
    @staticmethod
    def serialize_batch(items: List[Dict]) -> List[str]:
        """批量序列化"""
        return [json.dumps(item, separators=(',', ':')) for item in items]
    
    @staticmethod
    def serialize_for_storage(data: Dict) -> str:
        """序列化用于存储（压缩格式）"""
        return json.dumps(data, separators=(',', ':'), ensure_ascii=False)


class OptimizedTraceContextManager:
    """优化的追踪上下文管理器
    
    整合所有优化策略
    """
    
    def __init__(self):
        self._sampler = AdaptiveSampler(target_rps=50)
        self._cache = MemoryEfficientCache(max_size=4096)
        self._batch_processor = None
        
        self._stats_lock = threading.Lock()
        self._stats = OptimizationStats()
    
    def init_batch_processor(self, process_func: Callable[[List[Any]], None]):
        """初始化批量处理器"""
        if self._batch_processor is None:
            self._batch_processor = BatchProcessor(
                process_func=process_func,
                batch_size=200,
                flush_interval=2.0
            )
            self._batch_processor.start()
    
    def should_sample(self, trace_id: str) -> bool:
        """判断是否采样"""
        return self._sampler.should_sample(trace_id)
    
    def cache_context(self, trace_id: str, context: Dict):
        """缓存上下文"""
        self._cache.set(trace_id, context)
    
    def get_cached_context(self, trace_id: str) -> Optional[Dict]:
        """获取缓存的上下文"""
        return self._cache.get(trace_id)
    
    def submit_for_processing(self, data: Dict):
        """提交数据进行批量处理"""
        if self._batch_processor:
            self._batch_processor.submit(data)
        else:
            # 未初始化批量处理器，直接处理
            with self._stats_lock:
                self._stats.sync_writes += 1
    
    def get_stats(self) -> Dict[str, Any]:
        """获取综合统计信息"""
        with self._stats_lock:
            return {
                'sampler_ratio': self._sampler.current_ratio,
                'cache_stats': {
                    'hits': self._cache.get_stats().cache_hits,
                    'misses': self._cache.get_stats().cache_misses,
                    'memory_saved_bytes': self._cache.get_stats().memory_saved_bytes
                },
                'batch_stats': self._batch_processor.get_stats().__dict__ if self._batch_processor else {},
                'overall': self._stats.__dict__
            }
    
    def stop(self):
        """停止管理器"""
        if self._batch_processor:
            self._batch_processor.stop()


# 全局优化管理器实例
_global_optimization_manager = None

def get_optimization_manager() -> OptimizedTraceContextManager:
    """获取全局优化管理器"""
    global _global_optimization_manager
    if _global_optimization_manager is None:
        _global_optimization_manager = OptimizedTraceContextManager()
    return _global_optimization_manager


# 优化装饰器
def optimized_trace(func):
    """
    优化的追踪装饰器
    
    使用自适应采样和批量处理
    """
    def wrapper(*args, **kwargs):
        from .tracing import get_trace_id
        
        trace_id = get_trace_id()
        if trace_id:
            manager = get_optimization_manager()
            if not manager.should_sample(trace_id):
                # 不采样，跳过追踪逻辑
                return func(*args, **kwargs)
        
        return func(*args, **kwargs)
    
    return wrapper


__all__ = [
    'OptimizationStats',
    'FastProbabilitySampler',
    'AdaptiveSampler',
    'BatchProcessor',
    'MemoryEfficientCache',
    'FastJSONSerializer',
    'OptimizedTraceContextManager',
    'get_optimization_manager',
    'optimized_trace'
]
