"""
多级缓存模块测试
"""

import pytest
import tempfile
import time

from agent.caching.multi_level_cache import (
    CacheEntry,
    CacheStats,
    MultiLevelCache,
)


class TestCacheEntry:
    """测试缓存条目类"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cache_entry_init(self):
        """测试缓存条目初始化"""
        entry = CacheEntry(
            key="test_key",
            value="test_value",
            timestamp=time.time(),
            ttl_seconds=3600
        )
        assert entry.key == "test_key"
        assert entry.value == "test_value"
        assert not entry.is_expired()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cache_entry_is_expired(self):
        """测试过期检查"""
        entry = CacheEntry(
            key="test_key",
            value="test_value",
            timestamp=time.time() - 4000,  # 已过期
            ttl_seconds=3600
        )
        assert entry.is_expired()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cache_entry_to_from_dict(self):
        """测试序列化和反序列化"""
        entry = CacheEntry(
            key="test_key",
            value={"nested": "value"},
            timestamp=1234567890.0,
            ttl_seconds=3600,
            hit_count=5,
            generation_time_ms=123.456,
            level=2
        )
        
        data = entry.to_dict()
        assert data["key"] == "test_key"
        assert data["hit_count"] == 5
        
        restored = CacheEntry.from_dict(data)
        assert restored.key == "test_key"
        assert restored.hit_count == 5
        assert restored.level == 2


class TestMultiLevelCache:
    """测试多级缓存类"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cache_set_get(self):
        """测试基本的设置和获取"""
        cache = MultiLevelCache(l1_max_size=100, l1_ttl=300, l2_enabled=False)
        
        cache.set("key1", "value1")
        result = cache.get("key1")
        
        assert result == "value1"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cache_get_nonexistent(self):
        """测试获取不存在的键"""
        cache = MultiLevelCache(l1_max_size=100, l1_ttl=300, l2_enabled=False)
        
        result = cache.get("nonexistent_key")
        assert result is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cache_ttl_expiration(self):
        """测试TTL过期"""
        cache = MultiLevelCache(l1_max_size=100, l1_ttl=1, l2_enabled=False)
        
        cache.set("key1", "value1", ttl_seconds=1)
        assert cache.get("key1") == "value1"
        
        time.sleep(1.1)  # 等待过期
        
        result = cache.get("key1")
        assert result is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cache_lru_eviction(self):
        """测试LRU淘汰"""
        cache = MultiLevelCache(l1_max_size=3, l1_ttl=300, l2_enabled=False)
        
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.set("key3", "value3")
        
        # 访问 key1 使其成为最新
        cache.get("key1")
        
        # 添加第4个键，应该淘汰 key2（最久未访问）
        cache.set("key4", "value4")
        
        assert cache.get("key1") == "value1"
        assert cache.get("key4") == "value4"
        assert cache.get("key2") is None  # key2 被淘汰

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cache_delete(self):
        """测试删除"""
        cache = MultiLevelCache(l1_max_size=100, l1_ttl=300, l2_enabled=False)
        
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"
        
        cache.delete("key1")
        assert cache.get("key1") is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cache_clear(self):
        """测试清空"""
        cache = MultiLevelCache(l1_max_size=100, l1_ttl=300, l2_enabled=False)
        
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        
        cache.clear()
        
        assert cache.get("key1") is None
        assert cache.get("key2") is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cache_get_stats(self):
        """测试获取统计信息"""
        cache = MultiLevelCache(l1_max_size=100, l1_ttl=300, l2_enabled=False)
        
        cache.set("key1", "value1")
        cache.get("key1")  # hit
        cache.get("key2")  # miss
        
        stats = cache.get_stats()
        assert isinstance(stats, dict)
        assert stats["l1_hits"] >= 1
        assert stats["total_misses"] >= 1

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cache_with_l2(self):
        """测试L2磁盘缓存"""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = MultiLevelCache(
                l1_max_size=10,
                l1_ttl=1,
                l2_enabled=True,
                l2_dir=tmpdir
            )
            
            cache.set("key1", "value1", ttl_seconds=1)
            assert cache.get("key1") == "value1"
            
            # 等待L1过期
            time.sleep(1.1)
            
            # 应该从L2获取
            result = cache.get("key1")
            assert result == "value1"

class TestCacheStats:
    """测试缓存统计类"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cache_stats_init(self):
        """测试统计初始化"""
        stats = CacheStats()
        assert stats.total_hits == 0
        assert stats.total_misses == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cache_stats_update(self):
        """测试统计更新"""
        stats = CacheStats()
        stats.total_hits += 1
        stats.total_misses += 1
        stats.total_puts += 1
        
        assert stats.total_hits == 1
        assert stats.total_misses == 1
        assert stats.total_puts == 1