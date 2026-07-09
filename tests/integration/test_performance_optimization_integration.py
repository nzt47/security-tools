#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""performance_optimization 集成测试

覆盖 monitoring/performance_optimization.py 的性能优化组件：
- FastSampler: 基于MD5哈希的快速采样
- AdaptiveSampler: 自适应采样率调整
- LockFreeRingBuffer: 无锁环形缓冲区
- BatchProcessor: 批量处理器
- MemoryEfficientCache: LRU+TTL缓存
- CircuitBreaker: 熔断器状态机
- OptimizedObservabilityManager: 统一管理器
- optimized_trace: 装饰器
"""

import time
import threading
from unittest.mock import patch, MagicMock
from collections import OrderedDict

import pytest

from agent.monitoring.performance_optimization import (
    OptimizationLevel,
    ThroughputTier,
    PerformanceStats,
    OptimizationConfig,
    FastSampler,
    AdaptiveSampler,
    LockFreeRingBuffer,
    BatchProcessor,
    MemoryEfficientCache,
    CircuitBreaker,
    OptimizedObservabilityManager,
    get_optimization_manager,
    optimized_trace,
)


# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def config():
    """默认优化配置"""
    return OptimizationConfig()


@pytest.fixture
def small_config():
    """小容量配置（便于测试边界）"""
    return OptimizationConfig(
        batch_size=3,
        max_queue_size=8,
        cache_max_size=3,
        cache_ttl_seconds=2,
        circuit_breaker_threshold=3,
        circuit_breaker_window=60,
        circuit_breaker_cooldown=1,
    )


@pytest.fixture
def reset_manager():
    """重置全局管理器"""
    import agent.monitoring.performance_optimization as module
    old = module._global_optimization_manager
    module._global_optimization_manager = None
    yield
    module._global_optimization_manager = old


# ═══════════════════════════════════════════════════════════════
# 枚举与 Dataclass
# ═══════════════════════════════════════════════════════════════

class TestEnums:
    def test_optimization_level_values(self):
        assert OptimizationLevel.DISABLED.value == "disabled"
        assert OptimizationLevel.CONSERVATIVE.value == "conservative"
        assert OptimizationLevel.BALANCED.value == "balanced"
        assert OptimizationLevel.AGGRESSIVE.value == "aggressive"

    def test_throughput_tier_values(self):
        assert ThroughputTier.LOW.value == "low"
        assert ThroughputTier.MEDIUM.value == "medium"
        assert ThroughputTier.HIGH.value == "high"
        assert ThroughputTier.EXTREME.value == "extreme"


class TestDataclasses:
    def test_performance_stats_defaults(self):
        stats = PerformanceStats()
        assert stats.sampling_ratio == 0.1
        assert stats.cache_hits == 0
        assert stats.cache_misses == 0
        assert stats.batches_processed == 0
        assert stats.dropped_items == 0

    def test_optimization_config_defaults(self):
        cfg = OptimizationConfig()
        assert cfg.enabled is True
        assert cfg.level == OptimizationLevel.BALANCED
        assert cfg.target_throughput == ThroughputTier.MEDIUM
        assert cfg.default_sampling_ratio == 0.1
        assert cfg.batch_size == 200
        assert cfg.cache_max_size == 4096
        assert cfg.circuit_breaker_threshold == 1000


# ═══════════════════════════════════════════════════════════════
# FastSampler
# ═══════════════════════════════════════════════════════════════

class TestFastSampler:
    def test_ratio_1_always_samples(self):
        sampler = FastSampler(ratio=1.0)
        assert sampler.should_sample("trace1") is True
        assert sampler.should_sample("trace2") is True

    def test_ratio_0_never_samples(self):
        sampler = FastSampler(ratio=0.0)
        assert sampler.should_sample("trace1") is False
        assert sampler.should_sample("trace2") is False

    def test_deterministic_sampling(self):
        """同一 trace_id 采样结果确定"""
        sampler = FastSampler(ratio=0.5)
        result1 = sampler.should_sample("trace-abc")
        result2 = sampler.should_sample("trace-abc")
        assert result1 == result2

    def test_ratio_clamped_below_zero(self):
        sampler = FastSampler(ratio=-0.5)
        assert sampler.should_sample("trace1") is False

    def test_ratio_clamped_above_one(self):
        sampler = FastSampler(ratio=2.0)
        assert sampler.should_sample("trace1") is True

    def test_update_ratio(self):
        sampler = FastSampler(ratio=0.0)
        assert sampler.should_sample("trace1") is False
        sampler.update_ratio(1.0)
        assert sampler.should_sample("trace1") is True

    def test_update_ratio_clamped(self):
        sampler = FastSampler(ratio=0.5)
        sampler.update_ratio(-1.0)
        assert sampler.should_sample("trace1") is False
        sampler.update_ratio(5.0)
        assert sampler.should_sample("trace1") is True

    def test_partial_sampling_returns_bool(self):
        sampler = FastSampler(ratio=0.3)
        results = [sampler.should_sample(f"trace-{i}") for i in range(100)]
        assert all(isinstance(r, bool) for r in results)
        # 30% 采样率下 100 个请求中应有一些被采样
        assert sum(results) > 0
        assert sum(results) < 100


# ═══════════════════════════════════════════════════════════════
# AdaptiveSampler
# ═══════════════════════════════════════════════════════════════

class TestAdaptiveSampler:
    def test_should_sample_returns_bool(self, config):
        sampler = AdaptiveSampler(config)
        result = sampler.should_sample("trace1")
        assert isinstance(result, bool)

    def test_request_count_increments(self, config):
        sampler = AdaptiveSampler(config)
        sampler.should_sample("trace1")
        sampler.should_sample("trace2")
        assert sampler._request_count == 2

    def test_current_ratio(self, config):
        sampler = AdaptiveSampler(config)
        assert sampler.current_ratio == config.default_sampling_ratio

    def test_calculate_target_ratio_low(self):
        cfg = OptimizationConfig(target_throughput=ThroughputTier.LOW)
        sampler = AdaptiveSampler(cfg)
        assert sampler._calculate_target_ratio() == 0.5

    def test_calculate_target_ratio_medium(self):
        cfg = OptimizationConfig(target_throughput=ThroughputTier.MEDIUM)
        sampler = AdaptiveSampler(cfg)
        assert sampler._calculate_target_ratio() == 0.2

    def test_calculate_target_ratio_high(self):
        cfg = OptimizationConfig(target_throughput=ThroughputTier.HIGH)
        sampler = AdaptiveSampler(cfg)
        assert sampler._calculate_target_ratio() == 0.1

    def test_calculate_target_ratio_extreme(self):
        cfg = OptimizationConfig(target_throughput=ThroughputTier.EXTREME)
        sampler = AdaptiveSampler(cfg)
        assert sampler._calculate_target_ratio() == 0.05

    def test_maybe_adjust_no_adjustment_within_interval(self, config):
        """调整间隔内不调整"""
        sampler = AdaptiveSampler(config)
        original_ratio = sampler.current_ratio
        sampler.should_sample("trace1")
        # 间隔内不调整
        assert sampler.current_ratio == original_ratio

    def test_maybe_adjust_triggers_after_interval(self, config):
        """超过调整间隔后触发调整"""
        sampler = AdaptiveSampler(config)
        # 模拟上次调整在 6 秒前
        sampler._last_adjustment = time.time() - 6
        # 发送一些请求
        for i in range(100):
            sampler.should_sample(f"trace-{i}")
        # 触发调整
        sampler._maybe_adjust()
        # 调整后 _last_adjustment 应更新
        assert time.time() - sampler._last_adjustment < 1

    def test_maybe_adjust_resets_counters(self, config):
        """调整后重置计数器"""
        sampler = AdaptiveSampler(config)
        for i in range(50):
            sampler.should_sample(f"trace-{i}")
        assert sampler._request_count == 50
        # 模拟超过调整间隔
        sampler._last_adjustment = time.time() - 6
        sampler._maybe_adjust()
        assert sampler._request_count == 0
        assert sampler._sample_count == 0


# ═══════════════════════════════════════════════════════════════
# LockFreeRingBuffer
# ═══════════════════════════════════════════════════════════════

class TestLockFreeRingBuffer:
    def test_push_pop_single(self):
        buf = LockFreeRingBuffer(capacity=8)
        assert buf.push("item1") is True
        assert buf.pop() == "item1"

    def test_push_pop_multiple(self):
        buf = LockFreeRingBuffer(capacity=8)
        buf.push("a")
        buf.push("b")
        buf.push("c")
        assert buf.pop() == "a"
        assert buf.pop() == "b"
        assert buf.pop() == "c"

    def test_pop_empty_returns_none(self):
        buf = LockFreeRingBuffer(capacity=8)
        assert buf.pop() is None

    def test_is_empty(self):
        buf = LockFreeRingBuffer(capacity=8)
        assert buf.is_empty() is True
        buf.push("x")
        assert buf.is_empty() is False

    def test_is_full(self):
        buf = LockFreeRingBuffer(capacity=4)
        # capacity=4 → 实际可存 3 个
        buf.push("a")
        buf.push("b")
        buf.push("c")
        assert buf.is_full() is True

    def test_push_full_returns_false(self):
        buf = LockFreeRingBuffer(capacity=4)
        buf.push("a")
        buf.push("b")
        buf.push("c")
        assert buf.push("d") is False
        assert buf._overflow_count == 1

    def test_size(self):
        buf = LockFreeRingBuffer(capacity=8)
        assert buf.size() == 0
        buf.push("a")
        assert buf.size() == 1
        buf.push("b")
        assert buf.size() == 2
        buf.pop()
        assert buf.size() == 1

    def test_drain(self):
        buf = LockFreeRingBuffer(capacity=8)
        buf.push("a")
        buf.push("b")
        buf.push("c")
        items = buf.drain()
        assert items == ["a", "b", "c"]
        assert buf.is_empty() is True

    def test_drain_empty(self):
        buf = LockFreeRingBuffer(capacity=8)
        assert buf.drain() == []

    def test_fifo_order_after_wraparound(self):
        """环绕后仍保持 FIFO"""
        buf = LockFreeRingBuffer(capacity=4)
        # 填满后清空，再填入
        buf.push("a")
        buf.push("b")
        buf.push("c")
        buf.pop()  # 移除 a
        buf.pop()  # 移除 b
        buf.push("d")
        buf.push("e")
        # 现在 buffer 中有 c, d, e
        assert buf.pop() == "c"
        assert buf.pop() == "d"
        assert buf.pop() == "e"


# ═══════════════════════════════════════════════════════════════
# BatchProcessor
# ═══════════════════════════════════════════════════════════════

class TestBatchProcessor:
    def test_submit_and_flush(self, small_config):
        processed = []
        bp = BatchProcessor(processed.extend, small_config)
        bp.submit("item1")
        bp.submit("item2")
        bp._flush()
        assert processed == ["item1", "item2"]

    def test_submit_triggers_flush_at_batch_size(self, small_config):
        """达到 batch_size 时自动 flush"""
        processed = []
        bp = BatchProcessor(processed.extend, small_config)
        bp.submit("a")
        bp.submit("b")
        bp.submit("c")  # batch_size=3 → 触发 flush
        assert len(processed) == 3

    def test_submit_to_full_queue_returns_false(self):
        """队列满时 submit 返回 False（用大 batch_size 避免自动 flush）"""
        cfg = OptimizationConfig(batch_size=1000, max_queue_size=8)
        bp = BatchProcessor(lambda x: None, cfg)
        # max_queue_size=8 → 实际可存 7 个
        for i in range(7):
            assert bp.submit(i) is True
        # 第 8 个失败
        assert bp.submit("overflow") is False
        stats = bp.get_stats()
        assert stats.dropped_items == 1
        assert stats.queue_full_events == 1

    def test_get_stats(self, small_config):
        bp = BatchProcessor(lambda x: None, small_config)
        bp.submit("a")
        bp._flush()
        stats = bp.get_stats()
        assert stats.batches_processed == 1
        assert stats.items_batched == 1

    def test_start_stop(self, small_config):
        bp = BatchProcessor(lambda x: None, small_config)
        bp.start()
        assert bp._running is True
        bp.stop(timeout=1.0)
        assert bp._running is False

    def test_start_double_start_noop(self, small_config):
        bp = BatchProcessor(lambda x: None, small_config)
        bp.start()
        thread1 = bp._flush_thread
        bp.start()
        assert bp._flush_thread is thread1
        bp.stop(timeout=1.0)

    def test_flush_error_retries_items(self, small_config):
        """process_func 抛异常时部分恢复"""
        call_count = [0]
        def failing_func(batch):
            call_count[0] += 1
            raise RuntimeError("fail")

        bp = BatchProcessor(failing_func, small_config)
        bp.submit("a")
        bp.submit("b")
        bp._flush()
        # 失败后部分项放回队列
        assert bp._queue.size() > 0

    def test_flush_empty_noop(self, small_config):
        bp = BatchProcessor(lambda x: None, small_config)
        bp._flush()  # 空队列不调用 process_func


# ═══════════════════════════════════════════════════════════════
# MemoryEfficientCache
# ═══════════════════════════════════════════════════════════════

class TestMemoryEfficientCache:
    def test_set_get(self, small_config):
        cache = MemoryEfficientCache(small_config)
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_get_missing_returns_none(self, small_config):
        cache = MemoryEfficientCache(small_config)
        assert cache.get("nonexistent") is None

    def test_cache_hit_miss_stats(self, small_config):
        cache = MemoryEfficientCache(small_config)
        cache.set("key1", "value1")
        cache.get("key1")  # hit
        cache.get("key2")  # miss
        stats = cache.get_stats()
        assert stats.cache_hits == 1
        assert stats.cache_misses == 1

    def test_ttl_expiry(self, small_config):
        """TTL 过期返回 None"""
        cache = MemoryEfficientCache(small_config)
        cache.set("key1", "value1")
        # 手动修改 timestamp 为过期
        with cache._lock:
            value, _ = cache._cache["key1"]
            cache._cache["key1"] = (value, time.time() - 10)
        assert cache.get("key1") is None

    def test_lru_eviction(self, small_config):
        """超过 cache_max_size 时淘汰最旧"""
        cache = MemoryEfficientCache(small_config)
        # cache_max_size=3
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        cache.set("d", 4)  # "a" 被淘汰
        assert cache.get("a") is None
        assert cache.get("d") == 4

    def test_lru_move_to_end_on_get(self, small_config):
        """get 后标记为最近使用，不被淘汰"""
        cache = MemoryEfficientCache(small_config)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        cache.get("a")  # a 移到末尾
        cache.set("d", 4)  # b 被淘汰（最旧）
        assert cache.get("a") == 1
        assert cache.get("b") is None

    def test_clear(self, small_config):
        cache = MemoryEfficientCache(small_config)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.clear()
        assert cache.get("a") is None
        assert cache.get("b") is None

    def test_memory_saved_increments(self, small_config):
        cache = MemoryEfficientCache(small_config)
        cache.set("a", 1)
        stats = cache.get_stats()
        assert stats.memory_saved_bytes > 0

    def test_cleanup_expired(self, small_config):
        """_cleanup_expired 清理过期项"""
        cache = MemoryEfficientCache(small_config)
        cache.set("a", 1)
        # 手动过期
        with cache._lock:
            value, _ = cache._cache["a"]
            cache._cache["a"] = (value, time.time() - 10)
        deleted = cache._cleanup_expired(limit=10)
        assert deleted == 1


# ═══════════════════════════════════════════════════════════════
# CircuitBreaker
# ═══════════════════════════════════════════════════════════════

class TestCircuitBreaker:
    def test_initial_state_closed(self, small_config):
        cb = CircuitBreaker(small_config)
        assert cb.get_state() == "closed"

    def test_closed_allows_request(self, small_config):
        cb = CircuitBreaker(small_config)
        assert cb.allow_request() is True

    def test_record_failure_increments(self, small_config):
        cb = CircuitBreaker(small_config)
        cb.record_failure()
        cb.record_failure()
        assert cb._failure_count == 2

    def test_open_after_threshold(self, small_config):
        """失败达阈值 → OPEN"""
        cb = CircuitBreaker(small_config)  # threshold=3
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.allow_request() is False
        assert cb.get_state() == "open"

    def test_open_blocks_request(self, small_config):
        cb = CircuitBreaker(small_config)
        for _ in range(3):
            cb.record_failure()
        cb.allow_request()  # 触发 OPEN
        assert cb.allow_request() is False  # 仍然 OPEN

    def test_half_open_after_cooldown(self, small_config):
        """冷却后 → HALF_OPEN"""
        cfg = OptimizationConfig(
            circuit_breaker_threshold=3,
            circuit_breaker_cooldown=0,
        )
        cb = CircuitBreaker(cfg)
        for _ in range(3):
            cb.record_failure()
        assert cb.allow_request() is False  # OPEN
        # cooldown=0 → 下一次转 HALF_OPEN
        assert cb.allow_request() is True
        assert cb.get_state() == "half_open"

    def test_closed_after_success_in_half_open(self):
        """HALF_OPEN 状态 3 次成功 → CLOSED"""
        cfg = OptimizationConfig(
            circuit_breaker_threshold=3,
            circuit_breaker_cooldown=0,
        )
        cb = CircuitBreaker(cfg)
        for _ in range(3):
            cb.record_failure()
        cb.allow_request()  # OPEN
        cb.allow_request()  # HALF_OPEN
        # 3 次成功
        cb.record_success()
        cb.record_success()
        cb.record_success()
        cb.allow_request()  # → CLOSED
        assert cb.get_state() == "closed"

    def test_record_success_decrements_failure(self, small_config):
        cb = CircuitBreaker(small_config)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb._failure_count == 1

    def test_state_enum_values(self):
        assert CircuitBreaker.State.CLOSED.value == "closed"
        assert CircuitBreaker.State.OPEN.value == "open"
        assert CircuitBreaker.State.HALF_OPEN.value == "half_open"


# ═══════════════════════════════════════════════════════════════
# OptimizedObservabilityManager
# ═══════════════════════════════════════════════════════════════

class TestOptimizedObservabilityManager:
    def test_should_sample_disabled_config(self):
        """config.enabled=False → 总是采样"""
        cfg = OptimizationConfig(enabled=False)
        mgr = OptimizedObservabilityManager(cfg)
        assert mgr.should_sample("trace1") is True

    def test_should_sample_enabled(self, config):
        mgr = OptimizedObservabilityManager(config)
        result = mgr.should_sample("trace1")
        assert isinstance(result, bool)

    def test_cache_context(self, config):
        mgr = OptimizedObservabilityManager(config)
        mgr.cache_context("trace1", {"key": "value"})
        assert mgr.get_cached_context("trace1") == {"key": "value"}

    def test_get_cached_context_disabled(self):
        cfg = OptimizationConfig(enabled=False)
        mgr = OptimizedObservabilityManager(cfg)
        assert mgr.get_cached_context("trace1") is None

    def test_cache_context_disabled_no_op(self):
        cfg = OptimizationConfig(enabled=False)
        mgr = OptimizedObservabilityManager(cfg)
        mgr.cache_context("trace1", {"key": "value"})
        assert mgr.get_cached_context("trace1") is None

    def test_submit_for_processing_no_batch_processor(self, config):
        """无 batch_processor 时回退同步处理"""
        processed = []
        mgr = OptimizedObservabilityManager(config)
        mgr.init_batch_processor(processed.extend)
        mgr.submit_for_processing({"data": 1})
        mgr._batch_processor._flush()
        assert len(processed) == 1

    def test_submit_for_processing_disabled(self):
        cfg = OptimizationConfig(enabled=False)
        mgr = OptimizedObservabilityManager(cfg)
        mgr.submit_for_processing({"data": 1})  # 无操作

    def test_get_stats(self, config):
        mgr = OptimizedObservabilityManager(config)
        mgr.cache_context("t1", {"v": 1})
        mgr.get_cached_context("t1")  # hit
        mgr.get_cached_context("t2")  # miss
        stats = mgr.get_stats()
        assert "sampler_ratio" in stats
        assert "circuit_breaker_state" in stats
        assert "cache_stats" in stats
        assert "batch_stats" in stats
        assert "global" in stats
        assert stats["cache_stats"]["hits"] == 1
        assert stats["cache_stats"]["misses"] == 1

    def test_get_stats_hit_rate(self, config):
        mgr = OptimizedObservabilityManager(config)
        mgr.cache_context("t1", {"v": 1})
        mgr.get_cached_context("t1")  # hit
        mgr.get_cached_context("t2")  # miss
        stats = mgr.get_stats()
        assert stats["cache_stats"]["hit_rate"] == 0.5

    def test_get_stats_hit_rate_zero(self, config):
        mgr = OptimizedObservabilityManager(config)
        stats = mgr.get_stats()
        assert stats["cache_stats"]["hit_rate"] == 0.0

    def test_start_stop(self, config):
        mgr = OptimizedObservabilityManager(config)
        mgr.init_batch_processor(lambda x: None)
        mgr.start()
        assert mgr._started is True
        mgr.stop()
        assert mgr._started is False

    def test_start_double_start_noop(self, config):
        mgr = OptimizedObservabilityManager(config)
        mgr.start()
        mgr.start()
        assert mgr._started is True
        mgr.stop()

    def test_init_batch_processor(self, config):
        mgr = OptimizedObservabilityManager(config)
        assert mgr._batch_processor is None
        mgr.init_batch_processor(lambda x: None)
        assert mgr._batch_processor is not None

    def test_init_batch_processor_starts_if_started(self, config):
        mgr = OptimizedObservabilityManager(config)
        mgr.start()
        mgr.init_batch_processor(lambda x: None)
        assert mgr._batch_processor._running is True
        mgr.stop()

    def test_calculate_hit_rate(self, config):
        mgr = OptimizedObservabilityManager(config)
        assert mgr._calculate_hit_rate() == 0.0
        mgr.cache_context("t1", {"v": 1})
        mgr.get_cached_context("t1")
        assert mgr._calculate_hit_rate() == 1.0

    def test_submit_with_circuit_breaker_open(self):
        """熔断器 OPEN 时不提交"""
        cfg = OptimizationConfig(
            circuit_breaker_threshold=1,
            circuit_breaker_cooldown=60,
        )
        mgr = OptimizedObservabilityManager(cfg)
        mgr.init_batch_processor(lambda x: None)
        mgr._circuit_breaker.record_failure()
        # 熔断器打开
        assert mgr._circuit_breaker.allow_request() is False
        # submit 不处理
        mgr.submit_for_processing({"data": 1})


# ═══════════════════════════════════════════════════════════════
# 全局单例与装饰器
# ═══════════════════════════════════════════════════════════════

class TestGlobalManager:
    def test_get_optimization_manager_singleton(self, reset_manager):
        m1 = get_optimization_manager()
        m2 = get_optimization_manager()
        assert m1 is m2

    def test_get_optimization_manager_with_config(self, reset_manager):
        cfg = OptimizationConfig(enabled=True)
        mgr = get_optimization_manager(cfg)
        assert mgr._config.enabled is True

    def test_singleton_reset(self, reset_manager):
        m1 = get_optimization_manager()
        import agent.monitoring.performance_optimization as module
        module._global_optimization_manager = None
        m2 = get_optimization_manager()
        assert m1 is not m2


class TestOptimizedTraceDecorator:
    def test_decorator_calls_function(self, reset_manager):
        @optimized_trace("test_service", "test_op")
        def my_func(x):
            return x * 2

        assert my_func(5) == 10

    def test_decorator_preserves_return_value(self, reset_manager):
        @optimized_trace("svc", "op")
        def returns_dict():
            return {"key": "value"}

        assert returns_dict() == {"key": "value"}

    def test_decorator_with_args(self, reset_manager):
        @optimized_trace("svc", "op")
        def add(a, b):
            return a + b

        assert add(3, 4) == 7

    def test_decorator_with_kwargs(self, reset_manager):
        @optimized_trace("svc", "op")
        def greet(name, greeting="Hello"):
            return f"{greeting}, {name}"

        assert greet("World") == "Hello, World"
        assert greet("World", greeting="Hi") == "Hi, World"
