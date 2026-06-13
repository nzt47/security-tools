"""
LLM响应缓存模块测试
"""

import pytest
import time

from agent.llm_response_cache import (
    CacheEntry,
    AsyncSaveRecord,
    LLMResponseCache,
    llm_cache,
    async_save_monitor,
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
        
        assert record.end_time is not None
        assert record.elapsed_ms == 100.0


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

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cache_lru_eviction(self):
        """测试LRU淘汰"""
        cache = LLMResponseCache(max_size=3, ttl_seconds=3600)
        
        cache.put("prompt1", "response1")
        cache.put("prompt2", "response2")
        cache.put("prompt3", "response3")
        
        # 访问prompt1使其成为最新
        cache.get("prompt1")
        
        # 添加第4个，应该淘汰prompt2
        cache.put("prompt4", "response4")
        
        assert cache.get("prompt1") == "response1"
        assert cache.get("prompt4") == "response4"
        assert cache.get("prompt2") is None

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

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cache_stats_with_hit_rate(self):
        """测试命中率计算"""
        cache = LLMResponseCache(max_size=100, ttl_seconds=3600)
        
        # 添加一些缓存条目
        cache.put("prompt1", "response1")
        cache.put("prompt2", "response2")
        
        # 多次命中
        for _ in range(5):
            cache.get("prompt1")
            cache.get("prompt2")
        
        stats = cache.get_stats()
        assert stats["total_hits"] == 10
        assert stats["total_misses"] == 0


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