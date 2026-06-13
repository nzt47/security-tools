"""
LLM 响应缓存与性能日志测试
"""

import pytest
import time
from unittest.mock import Mock, patch

from agent.performance_logging import (
    CacheEntry,
    LLMCacheStats,
)


class TestCacheEntry:
    """测试缓存条目"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_create_cache_entry(self):
        """测试创建缓存条目"""
        entry = CacheEntry(
            prompt_hash="abc123",
            response="Test response",
            timestamp=time.time(),
            ttl_seconds=3600,
        )
        
        assert entry.prompt_hash == "abc123"
        assert entry.response == "Test response"
        assert entry.hit_count == 0

    @pytest.mark.unit
    @pytest.mark.p2
    def test_cache_entry_default_hit_count(self):
        """测试缓存条目默认命中次数"""
        entry = CacheEntry(
            prompt_hash="test",
            response="response",
            timestamp=time.time(),
            ttl_seconds=60,
        )
        
        assert entry.hit_count == 0
        assert entry.generation_time_ms == 0.0

    @pytest.mark.unit
    @pytest.mark.p2
    def test_is_expired_false(self):
        """测试未过期缓存"""
        entry = CacheEntry(
            prompt_hash="test",
            response="response",
            timestamp=time.time(),
            ttl_seconds=3600,
        )
        
        assert not entry.is_expired()

    @pytest.mark.unit
    @pytest.mark.p2
    def test_is_expired_true(self):
        """测试过期缓存"""
        entry = CacheEntry(
            prompt_hash="test",
            response="response",
            timestamp=time.time() - 7200,  # 2 hours ago
            ttl_seconds=3600,  # 1 hour TTL
        )
        
        assert entry.is_expired()


class TestLLMCacheStats:
    """测试 LLM 缓存统计"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_init(self):
        """测试初始化"""
        stats = LLMCacheStats()
        
        assert stats.hits == 0
        assert stats.misses == 0
        assert stats.evictions == 0

    @pytest.mark.unit
    @pytest.mark.p2
    def test_record_hit(self):
        """测试记录命中"""
        stats = LLMCacheStats()
        
        stats.record_hit(10.0)
        
        assert stats.hits == 1
        assert stats.total_hit_time_ms == 10.0

    @pytest.mark.unit
    @pytest.mark.p2
    def test_record_miss(self):
        """测试记录未命中"""
        stats = LLMCacheStats()
        
        stats.record_miss()
        
        assert stats.misses == 1

    @pytest.mark.unit
    @pytest.mark.p2
    def test_record_save(self):
        """测试记录保存"""
        stats = LLMCacheStats()
        
        stats.record_save(50.0)
        
        assert stats.total_save_time_ms == 50.0

    @pytest.mark.unit
    @pytest.mark.p2
    def test_record_eviction(self):
        """测试记录淘汰"""
        stats = LLMCacheStats()
        
        stats.record_eviction()
        
        assert stats.evictions == 1

    @pytest.mark.unit
    @pytest.mark.p2
    def test_get_hit_rate_no_data(self):
        """测试无数据时命中率"""
        stats = LLMCacheStats()
        
        rate = stats.get_hit_rate()
        
        assert rate == 0.0

    @pytest.mark.unit
    @pytest.mark.p2
    def test_get_hit_rate_with_data(self):
        """测试有数据时命中率"""
        stats = LLMCacheStats()
        
        stats.hits = 8
        stats.misses = 2
        
        rate = stats.get_hit_rate()
        
        assert rate == 0.8

    @pytest.mark.unit
    @pytest.mark.p2
    def test_get_avg_hit_time_no_hits(self):
        """测试无命中时平均时间"""
        stats = LLMCacheStats()
        
        avg_time = stats.get_avg_hit_time_ms()
        
        assert avg_time == 0.0

    @pytest.mark.unit
    @pytest.mark.p2
    def test_get_avg_hit_time_with_hits(self):
        """测试有命中时平均时间"""
        stats = LLMCacheStats()
        
        stats.record_hit(10.0)
        stats.record_hit(20.0)
        
        avg_time = stats.get_avg_hit_time_ms()
        
        assert avg_time == 15.0

    @pytest.mark.unit
    @pytest.mark.p2
    def test_get_avg_save_time(self):
        """测试平均保存时间"""
        stats = LLMCacheStats()
        
        stats.record_save(30.0)
        stats.record_save(50.0)
        stats.record_miss()
        stats.record_hit(10.0)
        
        # 平均保存时间基于命中和未命中总数
        avg_save = stats.get_avg_save_time_ms()
        
        assert avg_save == 40.0

    @pytest.mark.unit
    @pytest.mark.p2
    def test_to_dict(self):
        """测试转换为字典"""
        stats = LLMCacheStats()
        stats.hits = 5
        stats.misses = 5
        
        result = stats.to_dict()
        
        assert result['hits'] == 5
        assert result['misses'] == 5
        assert 'hit_rate' in result