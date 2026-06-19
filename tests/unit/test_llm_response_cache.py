"""
LLM响应缓存模块测试 - 覆盖缓存过期、逐出策略、异步保存监控
"""

import pytest
import time
import threading

from agent.llm_response_cache import (
    CacheEntry,
    AsyncSaveRecord,
    LLMResponseCache,
    AsyncSaveMonitor,
    PerformanceLogger,
    llm_cache,
    async_save_monitor,
    perf_logger,
)


class TestCacheEntry:
    """测试缓存条目类"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cache_entry_init(self):
        """测试缓存条目初始化"""
        entry = CacheEntry(
            prompt_hash="abc123",
            response="test response",
            timestamp=time.time(),
            ttl_seconds=3600
        )
        assert entry.prompt_hash == "abc123"
        assert entry.response == "test response"
        assert not entry.is_expired()
        assert entry.hit_count == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cache_entry_is_expired(self):
        """测试过期检查"""
        entry = CacheEntry(
            prompt_hash="abc123",
            response="test response",
            timestamp=time.time() - 4000,
            ttl_seconds=3600
        )
        assert entry.is_expired()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cache_entry_not_expired(self):
        """测试未过期"""
        entry = CacheEntry(
            prompt_hash="abc123",
            response="test response",
            timestamp=time.time() - 1000,
            ttl_seconds=3600
        )
        assert not entry.is_expired()


class TestAsyncSaveRecord:
    """测试异步保存记录类"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_async_save_record_init(self):
        """测试异步保存记录初始化"""
        record = AsyncSaveRecord(
            task_id="task123",
            task_type="llm_response",
            start_time=time.time()
        )
        assert record.task_id == "task123"
        assert record.task_type == "llm_response"
        assert record.success is True
        assert record.end_time is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_async_save_record_completed(self):
        """测试完成的记录"""
        start = time.time()
        record = AsyncSaveRecord(
            task_id="task123",
            task_type="llm_response",
            start_time=start
        )
        record.end_time = start + 0.1
        record.elapsed_ms = 100.0
        record.success = True

        assert record.end_time is not None
        assert record.elapsed_ms == 100.0
        assert record.success is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_async_save_record_failed(self):
        """测试失败的记录"""
        record = AsyncSaveRecord(
            task_id="task123",
            task_type="llm_response",
            start_time=time.time()
        )
        record.success = False
        record.error = "test error"

        assert record.success is False
        assert record.error == "test error"


class TestLLMResponseCache:
    """测试LLM响应缓存类"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cache_put_get(self):
        """测试基本的put和get"""
        cache = LLMResponseCache(max_size=100, ttl_seconds=3600)

        cache.put("test_prompt", "test_response")
        result = cache.get("test_prompt")

        assert result == "test_response"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cache_get_nonexistent(self):
        """测试获取不存在的提示词"""
        cache = LLMResponseCache(max_size=100, ttl_seconds=3600)

        result = cache.get("nonexistent_prompt")
        assert result is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cache_ttl_expiration(self):
        """测试TTL过期"""
        cache = LLMResponseCache(max_size=100, ttl_seconds=1)

        cache.put("test_prompt", "test_response")
        assert cache.get("test_prompt") == "test_response"

        time.sleep(1.1)

        result = cache.get("test_prompt")
        assert result is None
        assert cache.total_evictions == 1

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cache_lru_eviction(self):
        """测试LRU淘汰"""
        cache = LLMResponseCache(max_size=3, ttl_seconds=3600)

        cache.put("prompt1", "response1")
        cache.put("prompt2", "response2")
        cache.put("prompt3", "response3")

        cache.get("prompt1")

        cache.put("prompt4", "response4")

        assert cache.get("prompt1") == "response1"
        assert cache.get("prompt4") == "response4"
        assert cache.get("prompt2") is None
        assert cache.total_evictions == 1

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cache_clear(self):
        """测试清空"""
        cache = LLMResponseCache(max_size=100, ttl_seconds=3600)

        cache.put("prompt1", "response1")
        cache.put("prompt2", "response2")

        cache.clear()

        assert cache.get("prompt1") is None
        assert cache.get("prompt2") is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cache_get_stats(self):
        """测试获取统计信息"""
        cache = LLMResponseCache(max_size=100, ttl_seconds=3600)

        cache.put("prompt1", "response1")
        cache.get("prompt1")  # hit
        cache.get("prompt2")  # miss

        stats = cache.get_stats()
        assert isinstance(stats, dict)
        assert stats["total_hits"] >= 1
        assert stats["total_misses"] >= 1
        assert "hit_rate" in stats
        assert "cache_size" in stats

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cache_stats_with_hit_rate(self):
        """测试命中率计算"""
        cache = LLMResponseCache(max_size=100, ttl_seconds=3600)

        cache.put("prompt1", "response1")
        cache.put("prompt2", "response2")

        for _ in range(5):
            cache.get("prompt1")
            cache.get("prompt2")

        stats = cache.get_stats()
        assert stats["total_hits"] == 10
        assert stats["total_misses"] == 0
        assert float(stats["hit_rate"].rstrip('%')) == 100.0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_cache_update_existing(self):
        """测试更新已存在的缓存"""
        cache = LLMResponseCache(max_size=100, ttl_seconds=3600)

        cache.put("prompt1", "response1")
        cache.get("prompt1")  # hit_count = 1

        cache.put("prompt1", "response1_updated")

        result = cache.get("prompt1")
        assert result == "response1_updated"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_cache_prompt_classification(self):
        """测试提示词分类"""
        cache = LLMResponseCache(max_size=100, ttl_seconds=3600)

        cache.put("hello", "hi there")
        cache.get("hello")

        cache.put("what is your status", "I'm fine")
        cache.get("what is your status")

        cache.put("help me", "sure")
        cache.get("help me")

        stats = cache.get_stats()
        assert "greeting" in stats["hits_by_type"]
        assert "status_query" in stats["hits_by_type"]
        assert "help_request" in stats["hits_by_type"]

    @pytest.mark.unit
    @pytest.mark.p1
    def test_cache_concurrent_access(self):
        """测试并发访问缓存"""
        cache = LLMResponseCache(max_size=100, ttl_seconds=3600)
        results = []

        def writer():
            for i in range(20):
                cache.put(f"prompt{i}", f"response{i}")

        def reader():
            for i in range(20):
                result = cache.get(f"prompt{i}")
                results.append(result is not None)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=writer),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert cache.cache_size >= 0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_cache_max_size_edge_case(self):
        """测试最大缓存大小边界情况"""
        cache = LLMResponseCache(max_size=1, ttl_seconds=3600)

        cache.put("prompt1", "response1")
        cache.put("prompt2", "response2")

        assert cache.get("prompt1") is None
        assert cache.get("prompt2") == "response2"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_cache_custom_ttl(self):
        """测试自定义TTL"""
        cache = LLMResponseCache(max_size=100, ttl_seconds=3600)

        cache.put("short_ttl", "short_value", ttl_seconds=1)
        cache.put("long_ttl", "long_value", ttl_seconds=3600)

        time.sleep(1.1)

        assert cache.get("short_ttl") is None
        assert cache.get("long_ttl") == "long_value"

    @pytest.mark.unit
    @pytest.mark.p2
    def test_cache_empty_prompt(self):
        """测试空提示词"""
        cache = LLMResponseCache(max_size=100, ttl_seconds=3600)

        cache.put("", "empty_response")
        result = cache.get("")

        assert result == "empty_response"

    @pytest.mark.unit
    @pytest.mark.p2
    def test_cache_large_content(self):
        """测试大内容"""
        cache = LLMResponseCache(max_size=100, ttl_seconds=3600)

        large_response = "x" * 10000
        cache.put("large_prompt", large_response)

        result = cache.get("large_prompt")
        assert result == large_response


class TestAsyncSaveMonitor:
    """测试异步保存监控器"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_start_and_end_save(self):
        """测试开始和结束保存"""
        monitor = AsyncSaveMonitor(max_records=100)

        task_id = monitor.start_save("test_task")
        assert task_id is not None
        assert "test_task" in task_id

        monitor.end_save(task_id, success=True)

        stats = monitor.get_stats()
        assert stats["total_saves"] == 1
        assert stats["successful_saves"] == 1
        assert stats["failed_saves"] == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_failed_save(self):
        """测试失败的保存"""
        monitor = AsyncSaveMonitor(max_records=100)

        task_id = monitor.start_save("test_task")
        monitor.end_save(task_id, success=False, error="test error")

        stats = monitor.get_stats()
        assert stats["total_saves"] == 1
        assert stats["successful_saves"] == 0
        assert stats["failed_saves"] == 1

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_recent_records(self):
        """测试获取最近记录"""
        monitor = AsyncSaveMonitor(max_records=10)

        for i in range(15):
            task_id = monitor.start_save(f"task_{i}")
            monitor.end_save(task_id, success=True)

        records = monitor.get_recent_records(5)
        assert len(records) == 5

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_stats_empty(self):
        """测试空统计"""
        monitor = AsyncSaveMonitor(max_records=100)

        stats = monitor.get_stats()
        assert stats["total_saves"] == 0
        assert stats["success_rate"] == "0.0%"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_success_rate_calculation(self):
        """测试成功率计算"""
        monitor = AsyncSaveMonitor(max_records=100)

        for i in range(10):
            task_id = monitor.start_save("task")
            monitor.end_save(task_id, success=(i % 2 == 0))

        stats = monitor.get_stats()
        assert stats["total_saves"] == 10
        assert stats["successful_saves"] == 5
        assert stats["success_rate"] == "50.0%"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_record_not_found(self):
        """测试结束不存在的任务"""
        monitor = AsyncSaveMonitor(max_records=100)

        monitor.end_save("nonexistent_task", success=True)

        stats = monitor.get_stats()
        assert stats["total_saves"] == 0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_concurrent_saves(self):
        """测试并发保存"""
        monitor = AsyncSaveMonitor(max_records=100)
        results = []

        def save_task(idx):
            task_id = monitor.start_save(f"task_{idx}")
            time.sleep(0.01)
            monitor.end_save(task_id, success=True)
            results.append(True)

        threads = []
        for i in range(10):
            t = threading.Thread(target=save_task, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        stats = monitor.get_stats()
        assert stats["total_saves"] == 10
        assert stats["successful_saves"] == 10


class TestPerformanceLogger:
    """测试性能日志记录器"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_log_timing(self):
        """测试记录计时"""
        logger = PerformanceLogger()

        start_time = time.perf_counter()
        logger.log_timing("test_phase", start_time, metadata={"key": "value"})

        assert len(logger.timings) == 1
        assert logger.timings[0]["phase"] == "test_phase"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_get_summary(self):
        """测试获取摘要"""
        logger = PerformanceLogger()

        start = time.perf_counter()
        logger.log_timing("phase1", start)
        time.sleep(0.01)
        logger.log_timing("phase1", time.perf_counter())

        summary = logger.get_summary()
        assert "phase1" in summary

    @pytest.mark.unit
    @pytest.mark.p1
    def test_timing_limit(self):
        """测试计时记录限制"""
        logger = PerformanceLogger()

        for i in range(1100):
            logger.log_timing("phase", time.perf_counter())

        assert len(logger.timings) == 1000

    @pytest.mark.unit
    @pytest.mark.p2
    def test_get_summary_empty(self):
        """测试空计时记录时的汇总"""
        logger = PerformanceLogger()
        
        summary = logger.get_summary()
        assert summary == {}


class TestLLMResponseCacheEdgeCases:
    """测试LLM响应缓存边界情况"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_cache_expiration_with_zero_ttl(self):
        """测试TTL为0时的缓存过期"""
        cache = LLMResponseCache(max_size=100, ttl_seconds=0)
        
        cache.put("prompt1", "response1")
        result = cache.get("prompt1")
        
        assert result is None
        assert cache.total_misses == 1
        assert cache.total_evictions == 1

    @pytest.mark.unit
    @pytest.mark.p2
    def test_cache_eviction_order(self):
        """测试LRU淘汰顺序"""
        cache = LLMResponseCache(max_size=3, ttl_seconds=3600)
        
        cache.put("prompt1", "response1")
        cache.put("prompt2", "response2")
        cache.put("prompt3", "response3")
        
        cache.get("prompt1")
        cache.get("prompt2")
        
        cache.put("prompt4", "response4")
        
        assert cache.get("prompt1") == "response1"
        assert cache.get("prompt2") == "response2"
        assert cache.get("prompt3") is None
        assert cache.get("prompt4") == "response4"

    @pytest.mark.unit
    @pytest.mark.p2
    def test_cache_eviction_with_update(self):
        """测试更新缓存时的LRU行为"""
        cache = LLMResponseCache(max_size=3, ttl_seconds=3600)
        
        cache.put("prompt1", "response1")
        cache.put("prompt2", "response2")
        cache.put("prompt3", "response3")
        
        cache.put("prompt1", "response1_updated")
        
        cache.put("prompt4", "response4")
        
        assert cache.get("prompt1") == "response1_updated"
        assert cache.get("prompt2") is None
        assert cache.get("prompt4") == "response4"

    @pytest.mark.unit
    @pytest.mark.p2
    def test_cache_expiration_affects_stats(self):
        """测试缓存过期对统计的影响"""
        cache = LLMResponseCache(max_size=100, ttl_seconds=1)
        
        cache.put("prompt1", "response1")
        cache.get("prompt1")
        assert cache.total_hits == 1
        
        import time
        time.sleep(1.1)
        
        cache.get("prompt1")
        assert cache.total_hits == 1
        assert cache.total_misses == 1
        assert cache.total_evictions == 1

    @pytest.mark.unit
    @pytest.mark.p2
    def test_cache_prompt_classification_status_query(self):
        """测试状态查询分类"""
        cache = LLMResponseCache(max_size=100, ttl_seconds=3600)
        
        cache.put("what is your status", "I'm fine")
        cache.get("what is your status")
        
        stats = cache.get_stats()
        assert "status_query" in stats["hits_by_type"]

    @pytest.mark.unit
    @pytest.mark.p2
    def test_cache_prompt_classification_other(self):
        """测试其他类型分类"""
        cache = LLMResponseCache(max_size=100, ttl_seconds=3600)
        
        cache.put("explain quantum computing concepts in detail", "quantum computing involves qubits")
        cache.get("explain quantum computing concepts in detail")
        
        stats = cache.get_stats()
        assert "other" in stats["hits_by_type"]


class TestAsyncSaveMonitorEdgeCases:
    """测试异步保存监控器边界情况"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_async_save_end_not_found(self):
        """测试结束不存在的任务"""
        monitor = AsyncSaveMonitor()
        
        monitor.end_save("non_existent_task", success=True)

    @pytest.mark.unit
    @pytest.mark.p2
    def test_async_save_failure(self):
        """测试异步保存失败"""
        monitor = AsyncSaveMonitor()
        
        task_id = monitor.start_save("test")
        monitor.end_save(task_id, success=False, error="test error")
        
        stats = monitor.get_stats()
        assert stats["failed_saves"] == 1
        assert stats["success_rate"] == "0.0%"

    @pytest.mark.unit
    @pytest.mark.p2
    def test_async_save_record_limit(self):
        """测试记录数量限制"""
        monitor = AsyncSaveMonitor(max_records=5)
        
        for i in range(10):
            task_id = monitor.start_save("test")
            monitor.end_save(task_id)
        
        assert len(monitor.records) == 5

    @pytest.mark.unit
    @pytest.mark.p2
    def test_async_save_get_recent_records(self):
        """测试获取最近记录"""
        monitor = AsyncSaveMonitor(max_records=10)
        
        for i in range(3):
            task_id = monitor.start_save("test")
            monitor.end_save(task_id)
        
        recent = monitor.get_recent_records(2)
        assert len(recent) == 2


class TestGlobalInstances:
    """测试全局实例"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_llm_cache_instance(self):
        """测试全局LLM缓存实例"""
        assert llm_cache is not None
        assert isinstance(llm_cache, LLMResponseCache)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_async_save_monitor_exists(self):
        """测试异步保存监控器存在"""
        assert async_save_monitor is not None
        assert isinstance(async_save_monitor, AsyncSaveMonitor)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_perf_logger_exists(self):
        """测试性能日志记录器存在"""
        assert perf_logger is not None
        assert isinstance(perf_logger, PerformanceLogger)