#!/usr/bin/env python3
"""
追踪上下文缓存模块

实现高效的追踪上下文缓存机制：
- LRU缓存实现
- 异步写入机制
- 批量序列化优化
- 对象池模式
"""

import time
import json
import threading
import queue
from typing import Dict, Any, Optional, List, Tuple
from collections import OrderedDict
from abc import ABC, abstractmethod

# ==================== LRU缓存实现 ====================

class LRUCache:
    """
    线程安全的LRU缓存实现
    
    使用OrderedDict实现最近最少使用策略
    """
    
    def __init__(self, max_size: int = 1024, ttl_seconds: int = 300):
        """
        Args:
            max_size: 最大缓存条目数
            ttl_seconds: 缓存过期时间（秒）
        """
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._cache = OrderedDict()
        self._lock = threading.RLock()
    
    def get(self, key: str) -> Optional[Any]:
        """获取缓存值"""
        with self._lock:
            if key not in self._cache:
                return None
            
            # 检查是否过期
            value, timestamp = self._cache[key]
            if time.time() - timestamp > self.ttl_seconds:
                del self._cache[key]
                return None
            
            # 标记为最近使用
            self._cache.move_to_end(key)
            return value
    
    def set(self, key: str, value: Any):
        """设置缓存值"""
        with self._lock:
            # 检查是否超过最大容量
            while len(self._cache) >= self.max_size:
                self._cache.popitem(last=False)
            
            self._cache[key] = (value, time.time())
            self._cache.move_to_end(key)
    
    def delete(self, key: str) -> bool:
        """删除缓存值"""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False
    
    def clear(self):
        """清空缓存"""
        with self._lock:
            self._cache.clear()
    
    def get_size(self) -> int:
        """获取缓存大小"""
        with self._lock:
            return len(self._cache)
    
    def keys(self) -> List[str]:
        """获取所有键"""
        with self._lock:
            return list(self._cache.keys())


# ==================== 对象池模式 ====================

class ObjectPool(ABC):
    """对象池基类"""
    
    @abstractmethod
    def acquire(self) -> Any:
        """获取对象"""
        pass
    
    @abstractmethod
    def release(self, obj: Any):
        """释放对象回池"""
        pass


class SpanDataPool(ObjectPool):
    """Span数据对象池
    
    复用Span数据字典对象，减少内存分配开销
    """
    
    def __init__(self, pool_size: int = 100):
        """
        Args:
            pool_size: 池大小
        """
        self.pool_size = pool_size
        self._pool = queue.Queue(maxsize=pool_size)
        self._lock = threading.Lock()
        self._created_count = 0
        
        # 预填充池
        for _ in range(pool_size // 2):
            self._pool.put(self._create_empty_span())
    
    def _create_empty_span(self) -> Dict:
        """创建空的Span数据字典"""
        return {
            'trace_id': '',
            'span_id': '',
            'parent_span_id': '',
            'service': '',
            'operation': '',
            'span_kind': '',
            'start_time': 0.0,
            'end_time': 0.0,
            'duration_ms': 0.0,
            'status': 'success',
            'attributes': {},
            'events': []
        }
    
    def _reset_span(self, span: Dict) -> Dict:
        """重置Span对象为初始状态"""
        span['trace_id'] = ''
        span['span_id'] = ''
        span['parent_span_id'] = ''
        span['service'] = ''
        span['operation'] = ''
        span['span_kind'] = ''
        span['start_time'] = 0.0
        span['end_time'] = 0.0
        span['duration_ms'] = 0.0
        span['status'] = 'success'
        span['attributes'].clear()
        span['events'].clear()
        return span
    
    def acquire(self) -> Dict:
        """获取Span对象"""
        try:
            span = self._pool.get(block=False)
            return self._reset_span(span)
        except queue.Empty:
            # 池为空，创建新对象
            with self._lock:
                if self._created_count < self.pool_size * 2:
                    self._created_count += 1
                    return self._create_empty_span()
                # 超过限制，等待
                return self._pool.get(block=True, timeout=1.0)
    
    def release(self, span: Dict):
        """释放Span对象回池"""
        try:
            self._pool.put(span, block=False)
        except queue.Full:
            # 池已满，丢弃对象（让GC处理）
            pass


# ==================== 异步写入器 ====================

class AsyncWriter:
    """异步写入器
    
    将数据写入操作异步化，避免阻塞主线程
    """
    
    def __init__(self, 
                 write_func: callable,
                 batch_size: int = 100,
                 flush_interval: float = 1.0,
                 max_queue_size: int = 10000):
        """
        Args:
            write_func: 实际写入函数
            batch_size: 批量写入大小
            flush_interval: 自动刷新间隔（秒）
            max_queue_size: 最大队列大小
        """
        self.write_func = write_func
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self._queue = queue.Queue(maxsize=max_queue_size)
        self._flush_thread = None
        self._running = False
        self._last_flush = time.time()
    
    def start(self):
        """启动异步写入线程"""
        if not self._running:
            self._running = True
            self._flush_thread = threading.Thread(
                target=self._flush_loop,
                daemon=True,
                name="AsyncWriter"
            )
            self._flush_thread.start()
    
    def stop(self, timeout: float = 5.0):
        """停止异步写入线程"""
        self._running = False
        if self._flush_thread:
            self._flush_thread.join(timeout=timeout)
    
    def write(self, data: Any):
        """异步写入数据"""
        try:
            self._queue.put(data, block=False)
        except queue.Full:
            # 队列满了，同步写入
            self._flush()
    
    def _flush_loop(self):
        """后台刷新循环"""
        while self._running:
            try:
                # 检查是否需要刷新
                now = time.time()
                if now - self._last_flush >= self.flush_interval:
                    self._flush()
                
                # 等待新数据或超时
                try:
                    self._queue.get(block=True, timeout=0.5)
                    self._queue.put(data)  # 放回
                    self._flush()
                except queue.Empty:
                    pass
            except Exception:
                # 忽略异常，继续运行
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
                self.write_func(batch)
            except Exception as e:
                # 写入失败，放回队列
                for item in batch:
                    try:
                        self._queue.put(item, block=False)
                    except queue.Full:
                        pass
        
        self._last_flush = time.time()


# ==================== 批量序列化器 ====================

class BatchSerializer:
    """批量序列化器
    
    优化大量数据的JSON序列化性能
    """
    
    @staticmethod
    def serialize_batch(items: List[Dict]) -> List[str]:
        """批量序列化多个对象"""
        return [json.dumps(item, separators=(',', ':')) for item in items]
    
    @staticmethod
    def serialize_compact(item: Dict) -> str:
        """紧凑序列化（无空格）"""
        return json.dumps(item, separators=(',', ':'))
    
    @staticmethod
    def serialize_pretty(item: Dict) -> str:
        """格式化序列化（带缩进）"""
        return json.dumps(item, ensure_ascii=False, indent=2)


# ==================== 追踪上下文缓存管理器 ====================

class TraceContextCache:
    """追踪上下文缓存管理器
    
    提供追踪上下文的高效缓存和检索
    """
    
    def __init__(self):
        # 上下文缓存（trace_id -> context）
        self._context_cache = LRUCache(max_size=4096, ttl_seconds=600)
        
        # Span数据缓存（trace_id -> List[span_data]）
        self._span_cache = LRUCache(max_size=2048, ttl_seconds=300)
        
        # Span对象池
        self._span_pool = SpanDataPool(pool_size=500)
        
        # 异步写入器
        self._async_writer = None
        
        # 统计信息
        self._stats_lock = threading.Lock()
        self._stats = {
            'cache_hits': 0,
            'cache_misses': 0,
            'objects_acquired': 0,
            'objects_released': 0,
            'async_writes': 0,
            'sync_writes': 0
        }
    
    def set_context(self, trace_id: str, context: Dict):
        """设置追踪上下文"""
        self._context_cache.set(trace_id, context)
    
    def get_context(self, trace_id: str) -> Optional[Dict]:
        """获取追踪上下文"""
        result = self._context_cache.get(trace_id)
        with self._stats_lock:
            if result:
                self._stats['cache_hits'] += 1
            else:
                self._stats['cache_misses'] += 1
        return result
    
    def delete_context(self, trace_id: str):
        """删除追踪上下文"""
        self._context_cache.delete(trace_id)
        self._span_cache.delete(trace_id)
    
    def add_span(self, trace_id: str, span_data: Dict):
        """添加Span到缓存"""
        spans = self._span_cache.get(trace_id) or []
        spans.append(span_data)
        self._span_cache.set(trace_id, spans)
    
    def get_spans(self, trace_id: str) -> Optional[List[Dict]]:
        """获取Trace的所有Span"""
        result = self._span_cache.get(trace_id)
        with self._stats_lock:
            if result:
                self._stats['cache_hits'] += 1
            else:
                self._stats['cache_misses'] += 1
        return result
    
    def acquire_span(self) -> Dict:
        """从对象池获取Span对象"""
        span = self._span_pool.acquire()
        with self._stats_lock:
            self._stats['objects_acquired'] += 1
        return span
    
    def release_span(self, span: Dict):
        """释放Span对象回池"""
        self._span_pool.release(span)
        with self._stats_lock:
            self._stats['objects_released'] += 1
    
    def init_async_writer(self, write_func: callable):
        """初始化异步写入器"""
        if self._async_writer is None:
            self._async_writer = AsyncWriter(write_func)
            self._async_writer.start()
    
    def async_write(self, data: Any):
        """异步写入数据"""
        if self._async_writer:
            self._async_writer.write(data)
            with self._stats_lock:
                self._stats['async_writes'] += 1
        else:
            # 未初始化异步写入器，同步写入
            self._sync_write(data)
    
    def _sync_write(self, data: Any):
        """同步写入数据"""
        with self._stats_lock:
            self._stats['sync_writes'] += 1
    
    def stop(self):
        """停止缓存管理器"""
        if self._async_writer:
            self._async_writer.stop()
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        with self._stats_lock:
            return {
                **self._stats,
                'context_cache_size': self._context_cache.get_size(),
                'span_cache_size': self._span_cache.get_size()
            }
    
    def clear_stats(self):
        """清除统计信息"""
        with self._stats_lock:
            self._stats = {
                'cache_hits': 0,
                'cache_misses': 0,
                'objects_acquired': 0,
                'objects_released': 0,
                'async_writes': 0,
                'sync_writes': 0
            }


# ==================== 全局缓存实例 ====================

_global_trace_cache = None

def get_trace_cache() -> TraceContextCache:
    """获取全局追踪缓存实例"""
    global _global_trace_cache
    if _global_trace_cache is None:
        _global_trace_cache = TraceContextCache()
    return _global_trace_cache


# ==================== 性能优化装饰器 ====================

def cached_context(func):
    """
    上下文缓存装饰器
    
    缓存函数返回的上下文，避免重复计算
    """
    def wrapper(trace_id: str, *args, **kwargs):
        cache = get_trace_cache()
        cached = cache.get_context(trace_id)
        
        if cached is not None:
            return cached
        
        result = func(trace_id, *args, **kwargs)
        
        if result is not None:
            cache.set_context(trace_id, result)
        
        return result
    
    return wrapper


__all__ = [
    'LRUCache',
    'SpanDataPool',
    'AsyncWriter',
    'BatchSerializer',
    'TraceContextCache',
    'get_trace_cache',
    'cached_context'
]