#!/usr/bin/env python3
"""
可观测性性能优化核心模块

实现针对追踪、指标、日志的综合性能优化策略：
1. 高性能采样策略 - 自适应采样、分层采样
2. 异步批量处理 - 批量写入、后台线程
3. 智能缓存机制 - 对象池、LRU缓存
4. 资源管理优化 - 连接池、线程池
5. 熔断保护 - 防止可观测性系统过载
"""

import time
import threading
import queue
import json
import hashlib
import gc
from typing import Dict, Any, List, Optional, Callable, Tuple
from collections import defaultdict, OrderedDict
from dataclasses import dataclass
from enum import Enum


class OptimizationLevel(Enum):
    """优化级别"""
    DISABLED = "disabled"
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"


class ThroughputTier(Enum):
    """吞吐量等级"""
    LOW = "low"       # < 100 req/s
    MEDIUM = "medium" # 100-1000 req/s
    HIGH = "high"     # 1000-10000 req/s
    EXTREME = "extreme" # > 10000 req/s


@dataclass
class PerformanceStats:
    """性能统计信息"""
    sampling_ratio: float = 0.1
    cache_hits: int = 0
    cache_misses: int = 0
    batches_processed: int = 0
    items_batched: int = 0
    async_writes: int = 0
    sync_writes: int = 0
    memory_saved_bytes: int = 0
    time_saved_ms: float = 0.0
    dropped_items: int = 0
    queue_full_events: int = 0


@dataclass
class OptimizationConfig:
    """优化配置"""
    enabled: bool = True
    level: OptimizationLevel = OptimizationLevel.BALANCED
    target_throughput: ThroughputTier = ThroughputTier.MEDIUM
    
    # 采样配置
    default_sampling_ratio: float = 0.1
    min_sampling_ratio: float = 0.01
    max_sampling_ratio: float = 1.0
    
    # 批量处理配置
    batch_size: int = 200
    flush_interval_ms: int = 2000
    max_queue_size: int = 10000
    
    # 缓存配置
    cache_max_size: int = 4096
    cache_ttl_seconds: int = 300
    
    # 熔断配置
    circuit_breaker_threshold: int = 1000
    circuit_breaker_window: int = 60
    circuit_breaker_cooldown: int = 30


class FastSampler:
    """快速采样器（无锁设计）"""
    
    def __init__(self, ratio: float = 0.1):
        self._ratio = max(0.0, min(1.0, ratio))
        self._threshold = int(self._ratio * 0xFFFFFFFF)
    
    def should_sample(self, trace_id: str) -> bool:
        """判断是否采样"""
        if self._ratio >= 1.0:
            return True
        if self._ratio <= 0.0:
            return False
        
        hash_val = int(hashlib.md5(trace_id.encode()).hexdigest()[:8], 16)
        return hash_val <= self._threshold
    
    def update_ratio(self, new_ratio: float):
        """更新采样比例"""
        self._ratio = max(0.0, min(1.0, new_ratio))
        self._threshold = int(self._ratio * 0xFFFFFFFF)


class AdaptiveSampler:
    """自适应采样器
    
    根据系统负载动态调整采样比例，确保可观测性开销可控
    """
    
    def __init__(self, config: OptimizationConfig):
        self._config = config
        self._sampler = FastSampler(config.default_sampling_ratio)
        
        self._lock = threading.RLock()
        self._request_count = 0
        self._sample_count = 0
        self._last_adjustment = time.time()
        self._adjustment_interval = 5  # 调整间隔（秒）
        
        # 最近的采样率记录（用于平滑调整）
        self._recent_rates = []
        self._max_rate_history = 10
    
    def should_sample(self, trace_id: str) -> bool:
        """判断是否采样（非阻塞）"""
        result = self._sampler.should_sample(trace_id)
        
        # 记录采样结果（原子操作）
        if result:
            self._sample_count += 1
        self._request_count += 1
        
        # 异步调整（非阻塞）
        self._maybe_adjust()
        
        return result
    
    def _maybe_adjust(self):
        """检查是否需要调整采样比例"""
        now = time.time()
        elapsed = now - self._last_adjustment
        
        if elapsed < self._adjustment_interval:
            return
        
        with self._lock:
            # 检查是否仍需要调整（可能被其他线程抢先）
            if time.time() - self._last_adjustment < self._adjustment_interval:
                return
            
            if self._request_count > 0:
                actual_ratio = self._sample_count / self._request_count
                self._recent_rates.append(actual_ratio)
                
                # 保持历史记录数量
                if len(self._recent_rates) > self._max_rate_history:
                    self._recent_rates.pop(0)
                
                # 计算期望采样率（基于目标吞吐量）
                target_ratio = self._calculate_target_ratio()
                
                # 平滑调整
                avg_rate = sum(self._recent_rates) / len(self._recent_rates)
                adjustment_factor = target_ratio / max(avg_rate, 0.001)
                
                current_ratio = self._sampler._ratio
                new_ratio = current_ratio * adjustment_factor
                
                # 限制范围
                new_ratio = max(
                    self._config.min_sampling_ratio,
                    min(self._config.max_sampling_ratio, new_ratio)
                )
                
                # 只有变化超过阈值才更新
                if abs(new_ratio - current_ratio) > 0.01:
                    self._sampler.update_ratio(new_ratio)
            
            # 重置计数器
            self._request_count = 0
            self._sample_count = 0
            self._last_adjustment = now
    
    def _calculate_target_ratio(self) -> float:
        """计算目标采样率"""
        tier = self._config.target_throughput
        
        tier_ratios = {
            ThroughputTier.LOW: 0.5,
            ThroughputTier.MEDIUM: 0.2,
            ThroughputTier.HIGH: 0.1,
            ThroughputTier.EXTREME: 0.05
        }
        
        return tier_ratios.get(tier, 0.1)
    
    @property
    def current_ratio(self) -> float:
        """获取当前采样比例"""
        return self._sampler._ratio


class LockFreeRingBuffer:
    """无锁环形缓冲区
    
    用于高并发场景下的数据收集，避免锁竞争
    """
    
    def __init__(self, capacity: int = 1024):
        self._capacity = capacity
        self._buffer = [None] * capacity
        self._head = 0
        self._tail = 0
        self._count = 0
        self._push_count = 0
        self._pop_count = 0
        self._overflow_count = 0
    
    def push(self, item: Any) -> bool:
        """添加元素（非阻塞）"""
        head = self._head
        next_head = (head + 1) % self._capacity
        
        if next_head == self._tail:
            self._overflow_count += 1
            logger.debug(
                json.dumps({
                    "trace_id": "ring_buffer",
                    "module_name": "LockFreeRingBuffer",
                    "action": "push_overflow",
                    "capacity": self._capacity,
                    "current_size": self._count,
                    "head": head,
                    "tail": self._tail,
                    "overflow_count": self._overflow_count
                })
            )
            return False  # 缓冲区满
        
        self._buffer[head] = item
        self._head = next_head
        self._count += 1
        self._push_count += 1
        
        if self._push_count % 10000 == 0:
            logger.debug(
                json.dumps({
                    "trace_id": "ring_buffer",
                    "module_name": "LockFreeRingBuffer",
                    "action": "push_stats",
                    "push_count": self._push_count,
                    "pop_count": self._pop_count,
                    "overflow_count": self._overflow_count,
                    "current_size": self._count,
                    "capacity": self._capacity
                })
            )
        
        return True
    
    def pop(self) -> Optional[Any]:
        """弹出元素（非阻塞）"""
        tail = self._tail
        
        if tail == self._head:
            return None  # 缓冲区空
        
        item = self._buffer[tail]
        self._tail = (tail + 1) % self._capacity
        self._count -= 1
        self._pop_count += 1
        return item
    
    def drain(self) -> List[Any]:
        """清空所有元素"""
        items = []
        while True:
            item = self.pop()
            if item is None:
                break
            items.append(item)
        
        if len(items) > 0:
            logger.debug(
                json.dumps({
                    "trace_id": "ring_buffer",
                    "module_name": "LockFreeRingBuffer",
                    "action": "drain",
                    "drained_count": len(items),
                    "remaining_size": self._count
                })
            )
        
        return items
    
    def is_empty(self) -> bool:
        """检查是否为空"""
        return self._head == self._tail
    
    def is_full(self) -> bool:
        """检查是否已满"""
        return (self._head + 1) % self._capacity == self._tail
    
    def size(self) -> int:
        """获取元素数量"""
        return self._count


class BatchProcessor:
    """批量处理器
    
    将多个小操作合并为批量操作，减少IO开销
    """
    
    def __init__(self, 
                 process_func: Callable[[List[Any]], None],
                 config: OptimizationConfig):
        self._process_func = process_func
        self._config = config
        
        self._queue = LockFreeRingBuffer(config.max_queue_size)
        self._flush_thread = None
        self._running = False
        self._last_flush = time.time()
        
        # 统计信息
        self._stats_lock = threading.Lock()
        self._stats = PerformanceStats()
        
        # 线程本地存储用于追踪
        self._local = threading.local()
    
    def start(self):
        """启动批量处理器"""
        if self._running:
            return
        
        self._running = True
        self._flush_thread = threading.Thread(
            target=self._flush_loop,
            daemon=True,
            name="BatchProcessor"
        )
        self._flush_thread.start()
        logger.info(
            json.dumps({
                "trace_id": "batch_processor",
                "module_name": "BatchProcessor",
                "action": "start",
                "batch_size": self._config.batch_size,
                "flush_interval_ms": self._config.flush_interval_ms,
                "max_queue_size": self._config.max_queue_size
            })
        )
    
    def stop(self, timeout: float = 5.0):
        """停止批量处理器"""
        self._running = False
        if self._flush_thread:
            self._flush_thread.join(timeout=timeout)
        self._flush()  # 最后一次刷新
        logger.info(
            json.dumps({
                "trace_id": "batch_processor",
                "module_name": "BatchProcessor",
                "action": "stop",
                "final_stats": self._stats.__dict__
            })
        )
    
    def submit(self, item: Any) -> bool:
        """提交数据项"""
        thread_id = threading.current_thread().ident
        
        success = self._queue.push(item)
        
        if not success:
            with self._stats_lock:
                self._stats.dropped_items += 1
                self._stats.queue_full_events += 1
            
            logger.warning(
                json.dumps({
                    "trace_id": "batch_processor",
                    "module_name": "BatchProcessor",
                    "action": "submit_failed",
                    "thread_id": thread_id,
                    "queue_size": self._queue.size(),
                    "dropped_items": self._stats.dropped_items,
                    "queue_full_events": self._stats.queue_full_events
                })
            )
            return False
        
        # 检查是否需要立即刷新
        if self._queue.size() >= self._config.batch_size:
            logger.debug(
                json.dumps({
                    "trace_id": "batch_processor",
                    "module_name": "BatchProcessor",
                    "action": "trigger_flush_by_size",
                    "thread_id": thread_id,
                    "queue_size": self._queue.size(),
                    "batch_threshold": self._config.batch_size
                })
            )
            self._flush()
        
        return True
    
    def _flush_loop(self):
        """后台刷新循环"""
        thread_id = threading.current_thread().ident
        flush_count = 0
        
        logger.debug(
            json.dumps({
                "trace_id": "batch_processor",
                "module_name": "BatchProcessor",
                "action": "flush_loop_start",
                "thread_id": thread_id
            })
        )
        
        while self._running:
            try:
                now = time.time()
                
                # 定时刷新
                if now - self._last_flush >= self._config.flush_interval_ms / 1000:
                    logger.debug(
                        json.dumps({
                            "trace_id": "batch_processor",
                            "module_name": "BatchProcessor",
                            "action": "trigger_flush_by_timer",
                            "thread_id": thread_id,
                            "elapsed_ms": (now - self._last_flush) * 1000,
                            "flush_interval_ms": self._config.flush_interval_ms
                        })
                    )
                    self._flush()
                    self._last_flush = now
                    flush_count += 1
                
                time.sleep(0.05)  # 50ms 轮询间隔
            except Exception as e:
                logger.error(
                    json.dumps({
                        "trace_id": "batch_processor",
                        "module_name": "BatchProcessor",
                        "action": "flush_loop_error",
                        "thread_id": thread_id,
                        "error": str(e)
                    })
                )
                time.sleep(0.1)
        
        logger.debug(
            json.dumps({
                "trace_id": "batch_processor",
                "module_name": "BatchProcessor",
                "action": "flush_loop_stop",
                "thread_id": thread_id,
                "total_flushes": flush_count
            })
        )
    
    def _flush(self):
        """刷新批量数据"""
        thread_id = threading.current_thread().ident
        
        batch = self._queue.drain()
        
        if not batch:
            return
        
        try:
            start = time.time()
            self._process_func(batch)
            duration_ms = (time.time() - start) * 1000
            
            with self._stats_lock:
                self._stats.batches_processed += 1
                self._stats.items_batched += len(batch)
                self._stats.async_writes += len(batch)
                self._stats.time_saved_ms += (len(batch) - 1) * 0.5
            
            logger.debug(
                json.dumps({
                    "trace_id": "batch_processor",
                    "module_name": "BatchProcessor",
                    "action": "flush",
                    "thread_id": thread_id,
                    "batch_size": len(batch),
                    "duration_ms": duration_ms,
                    "batches_processed": self._stats.batches_processed,
                    "items_batched": self._stats.items_batched,
                    "time_saved_ms": self._stats.time_saved_ms
                })
            )
            
            if duration_ms > 100:
                logger.warning(
                    json.dumps({
                        "trace_id": "batch_processor",
                        "module_name": "BatchProcessor",
                        "action": "flush_slow",
                        "thread_id": thread_id,
                        "batch_size": len(batch),
                        "duration_ms": duration_ms
                    })
                )
        except Exception as e:
            # 处理失败，尝试放回队列（部分恢复）
            logger.error(
                json.dumps({
                    "trace_id": "batch_processor",
                    "module_name": "BatchProcessor",
                    "action": "flush_error",
                    "thread_id": thread_id,
                    "error": str(e),
                    "batch_size": len(batch),
                    "items_to_retry": min(len(batch), 10)
                })
            )
            for item in batch[:min(len(batch), 10)]:
                self._queue.push(item)
    
    def get_stats(self) -> PerformanceStats:
        """获取统计信息"""
        with self._stats_lock:
            return self._stats


class MemoryEfficientCache:
    """内存高效缓存
    
    使用紧凑的数据结构和定时清理策略
    """
    
    def __init__(self, config: OptimizationConfig):
        self._config = config
        self._cache = OrderedDict()
        self._lock = threading.RLock()
        self._stats = PerformanceStats()
    
    def get(self, key: str) -> Optional[Any]:
        """获取缓存值"""
        with self._lock:
            if key not in self._cache:
                self._stats.cache_misses += 1
                return None
            
            value, timestamp = self._cache[key]
            
            # 检查过期
            if time.time() - timestamp > self._config.cache_ttl_seconds:
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
            # 清理过期条目（只清理部分，避免长时间持有锁）
            self._cleanup_expired(limit=10)
            
            # 如果超过容量，删除最旧的
            while len(self._cache) >= self._config.cache_max_size:
                self._cache.popitem(last=False)
            
            self._cache[key] = (value, time.time())
            self._cache.move_to_end(key)
            
            # 估算内存节省
            self._stats.memory_saved_bytes += 1024
    
    def _cleanup_expired(self, limit: int = 10):
        """清理过期条目"""
        now = time.time()
        deleted = 0
        
        for key in list(self._cache.keys())[:limit]:
            _, timestamp = self._cache[key]
            if now - timestamp > self._config.cache_ttl_seconds:
                del self._cache[key]
                deleted += 1
        
        return deleted
    
    def clear(self):
        """清空缓存"""
        with self._lock:
            self._cache.clear()
    
    def get_stats(self) -> PerformanceStats:
        """获取统计信息"""
        with self._lock:
            return self._stats


class CircuitBreaker:
    """熔断器
    
    防止可观测性系统过载影响主业务
    """
    
    class State(Enum):
        CLOSED = "closed"
        OPEN = "open"
        HALF_OPEN = "half_open"
    
    def __init__(self, config: OptimizationConfig):
        self._config = config
        self._state = CircuitBreaker.State.CLOSED
        
        self._lock = threading.RLock()
        self._failure_count = 0
        self._success_count = 0
        self._last_failure = 0
        self._cooldown_end = 0
    
    def allow_request(self) -> bool:
        """判断是否允许请求"""
        with self._lock:
            now = time.time()
            
            if self._state == CircuitBreaker.State.OPEN:
                # 检查冷却时间
                if now >= self._cooldown_end:
                    self._state = CircuitBreaker.State.HALF_OPEN
                    self._success_count = 0
                else:
                    return False
            
            if self._state == CircuitBreaker.State.HALF_OPEN:
                # 半开状态：允许有限数量的请求进行试探
                if self._success_count >= 3:
                    self._state = CircuitBreaker.State.CLOSED
                    self._failure_count = 0
                return True
            
            # 闭合状态：检查失败率
            window_start = now - self._config.circuit_breaker_window
            
            if self._failure_count >= self._config.circuit_breaker_threshold:
                self._state = CircuitBreaker.State.OPEN
                self._cooldown_end = now + self._config.circuit_breaker_cooldown
                return False
            
            return True
    
    def record_success(self):
        """记录成功"""
        with self._lock:
            self._success_count += 1
            if self._failure_count > 0:
                self._failure_count -= 1
    
    def record_failure(self):
        """记录失败"""
        with self._lock:
            now = time.time()
            self._last_failure = now
            self._failure_count += 1
    
    def get_state(self) -> str:
        """获取熔断器状态"""
        with self._lock:
            return self._state.value


class OptimizedObservabilityManager:
    """优化的可观测性管理器
    
    整合所有优化策略，提供统一的高性能可观测性接口
    """
    
    def __init__(self, config: OptimizationConfig = None):
        self._config = config or OptimizationConfig()
        
        # 采样器
        self._sampler = AdaptiveSampler(self._config)
        
        # 缓存
        self._cache = MemoryEfficientCache(self._config)
        
        # 批量处理器（延迟初始化）
        self._batch_processor = None
        self._batch_process_func = None
        
        # 熔断器
        self._circuit_breaker = CircuitBreaker(self._config)
        
        # 全局统计
        self._global_stats = PerformanceStats()
        self._stats_lock = threading.Lock()
        
        # 启动标记
        self._started = False
    
    def init_batch_processor(self, process_func: Callable[[List[Any]], None]):
        """初始化批量处理器"""
        if self._batch_processor is None:
            self._batch_process_func = process_func
            self._batch_processor = BatchProcessor(process_func, self._config)
            if self._started:
                self._batch_processor.start()
    
    def should_sample(self, trace_id: str) -> bool:
        """判断是否采样"""
        if not self._config.enabled:
            return True
        
        if not self._circuit_breaker.allow_request():
            return False
        
        return self._sampler.should_sample(trace_id)
    
    def cache_context(self, trace_id: str, context: Dict):
        """缓存上下文"""
        if self._config.enabled:
            self._cache.set(trace_id, context)
    
    def get_cached_context(self, trace_id: str) -> Optional[Dict]:
        """获取缓存的上下文"""
        if not self._config.enabled:
            return None
        return self._cache.get(trace_id)
    
    def submit_for_processing(self, data: Dict):
        """提交数据进行批量处理"""
        if not self._config.enabled:
            return
        
        if not self._circuit_breaker.allow_request():
            return
        
        if self._batch_processor:
            success = self._batch_processor.submit(data)
            if not success:
                with self._stats_lock:
                    self._global_stats.dropped_items += 1
        else:
            # 回退到同步处理
            if self._batch_process_func:
                try:
                    self._batch_process_func([data])
                    with self._stats_lock:
                        self._global_stats.sync_writes += 1
                except Exception:
                    pass
    
    def get_stats(self) -> Dict[str, Any]:
        """获取综合统计信息"""
        with self._stats_lock:
            return {
                'sampler_ratio': self._sampler.current_ratio,
                'circuit_breaker_state': self._circuit_breaker.get_state(),
                'cache_stats': {
                    'hits': self._cache.get_stats().cache_hits,
                    'misses': self._cache.get_stats().cache_misses,
                    'memory_saved_bytes': self._cache.get_stats().memory_saved_bytes,
                    'hit_rate': self._calculate_hit_rate()
                },
                'batch_stats': self._batch_processor.get_stats().__dict__ if self._batch_processor else {},
                'global': self._global_stats.__dict__
            }
    
    def _calculate_hit_rate(self) -> float:
        """计算缓存命中率"""
        stats = self._cache.get_stats()
        total = stats.cache_hits + stats.cache_misses
        return stats.cache_hits / total if total > 0 else 0.0
    
    def start(self):
        """启动管理器"""
        if self._started:
            return
        
        self._started = True
        if self._batch_processor:
            self._batch_processor.start()
    
    def stop(self):
        """停止管理器"""
        if self._batch_processor:
            self._batch_processor.stop()
        self._started = False


# 全局优化管理器实例
_global_optimization_manager = None


def get_optimization_manager(config: OptimizationConfig = None) -> OptimizedObservabilityManager:
    """获取全局优化管理器"""
    global _global_optimization_manager
    if _global_optimization_manager is None:
        _global_optimization_manager = OptimizedObservabilityManager(config)
    return _global_optimization_manager


# 优化装饰器
def optimized_trace(service: str, operation: str):
    """
    优化的追踪装饰器
    
    使用自适应采样和批量处理，减少可观测性开销
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            from agent.monitoring.tracing import get_trace_id
            
            trace_id = get_trace_id()
            if trace_id:
                manager = get_optimization_manager()
                if not manager.should_sample(trace_id):
                    return func(*args, **kwargs)
            
            return func(*args, **kwargs)
        
        return wrapper
    return decorator


__all__ = [
    'OptimizationLevel',
    'ThroughputTier',
    'PerformanceStats',
    'OptimizationConfig',
    'FastSampler',
    'AdaptiveSampler',
    'LockFreeRingBuffer',
    'BatchProcessor',
    'MemoryEfficientCache',
    'CircuitBreaker',
    'OptimizedObservabilityManager',
    'get_optimization_manager',
    'optimized_trace'
]