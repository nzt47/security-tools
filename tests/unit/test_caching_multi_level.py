"""Multi-Level Cache 单元测试"""
import pytest
import time
import threading
from pathlib import Path

from agent.caching.multi_level_cache import (
    CacheEntry,
    CacheStats,
    LRUCache,
    DiskCache,
    MultiLevelCache,
    CacheManager,
    default_cache,
    cache_manager,
)


class TestCacheEntry:
    """测试缓存条目"""

    def test_cache_entry_creation(self):
        """测试缓存条目创建"""
        entry = CacheEntry(
            key="test_key",
            value="test_value",
            timestamp=1234567890.0,
            ttl_seconds=300,
            hit_count=5,
            generation_time_ms=10.0,
            level=1
        )
        
        assert entry.key == "test_key"
        assert entry.value == "test_value"
        assert entry.ttl_seconds == 300
        assert entry.hit_count == 5

    def test_cache_entry_is_expired(self):
        """测试过期检查"""
        entry = CacheEntry(
            key="test",
            value="value",
            timestamp=time.time() - 400,  # 400秒前
            ttl_seconds=300
        )
        assert entry.is_expired() is True
        
        entry2 = CacheEntry(
            key="test2",
            value="value2",
            timestamp=time.time(),
            ttl_seconds=300
        )
        assert entry2.is_expired() is False

    def test_cache_entry_to_dict(self):
        """测试转换为字典"""
        entry = CacheEntry(
            key="test",
            value="value",
            timestamp=1234567890.0,
            ttl_seconds=300
        )
        d = entry.to_dict()
        
        assert d["key"] == "test"
        assert d["value"] == "value"
        assert d["ttl_seconds"] == 300

    def test_cache_entry_from_dict(self):
        """测试从字典创建"""
        data = {
            "key": "test",
            "value": "value",
            "timestamp": 1234567890.0,
            "ttl_seconds": 300,
            "hit_count": 5,
            "generation_time_ms": 10.0,
            "level": 2
        }
        entry = CacheEntry.from_dict(data)
        
        assert entry.key == "test"
        assert entry.hit_count == 5
        assert entry.level == 2


class TestCacheStats:
    """测试缓存统计"""

    def test_cache_stats_init(self):
        """测试统计初始化"""
        stats = CacheStats()
        
        assert stats.total_hits == 0
        assert stats.total_misses == 0
        assert stats.total_puts == 0

    def test_record_hit(self):
        """测试记录命中"""
        stats = CacheStats()
        stats.record_hit(1, 5.0)
        
        assert stats.total_hits == 1
        assert stats.l1_hits == 1
        assert stats.total_hit_time_ms == 5.0

    def test_record_miss(self):
        """测试记录未命中"""
        stats = CacheStats()
        stats.record_miss(2)
        
        assert stats.total_misses == 1
        assert stats.l2_misses == 1

    def test_get_hit_rate(self):
        """测试获取命中率"""
        stats = CacheStats()
        stats.record_hit(1, 1.0)
        stats.record_hit(1, 1.0)
        stats.record_miss(1)
        
        assert abs(stats.get_hit_rate() - 66.666) < 0.01

    def test_to_dict(self):
        """测试转换为字典"""
        stats = CacheStats()
        stats.record_hit(1, 1.0)
        stats.record_miss(1)
        
        d = stats.to_dict()
        assert "hit_rate" in d
        assert "l1_hit_rate" in d


class TestLRUCache:
    """测试 LRU 缓存"""

    def test_lru_cache_init(self):
        """测试初始化"""
        cache = LRUCache(max_size=100, ttl_seconds=300)
        
        assert cache.max_size == 100
        assert cache.default_ttl == 300

    def test_lru_cache_get_set(self):
        """测试基本读写"""
        cache = LRUCache(max_size=10)
        cache.set("key1", "value1")
        
        result = cache.get("key1")
        assert result == "value1"

    def test_lru_cache_not_found(self):
        """测试获取不存在的键"""
        cache = LRUCache()
        result = cache.get("nonexistent")
        assert result is None

    def test_lru_cache_eviction(self):
        """测试 LRU 淘汰"""
        cache = LRUCache(max_size=3)
        
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.set("key3", "value3")
        
        # 访问 key1，使其成为最近使用
        cache.get("key1")
        
        # 添加第四个，应该淘汰 key2（最久未使用）
        cache.set("key4", "value4")
        
        assert cache.get("key1") == "value1"
        assert cache.get("key2") is None
        assert cache.get("key3") == "value3"
        assert cache.get("key4") == "value4"

    def test_lru_cache_ttl_expiration(self):
        """测试 TTL 过期"""
        cache = LRUCache(max_size=10, ttl_seconds=0.1)
        cache.set("key1", "value1")
        
        assert cache.get("key1") == "value1"
        
        time.sleep(0.2)
        
        assert cache.get("key1") is None

    def test_lru_cache_delete(self):
        """测试删除"""
        cache = LRUCache()
        cache.set("key1", "value1")
        cache.delete("key1")
        
        assert cache.get("key1") is None

    def test_lru_cache_clear(self):
        """测试清空"""
        cache = LRUCache()
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        
        cache.clear()
        
        assert cache.get_size() == 0


class TestDiskCache:
    """测试磁盘缓存"""

    def test_disk_cache_init(self, tmp_path):
        """测试初始化"""
        cache = DiskCache(cache_dir=str(tmp_path / "cache"), max_size_bytes=1024)
        
        assert cache.cache_dir == tmp_path / "cache"
        assert cache.cache_dir.exists()

    def test_disk_cache_get_set(self, tmp_path):
        """测试基本读写"""
        cache = DiskCache(cache_dir=str(tmp_path / "cache"))
        cache.set("key1", "value1", ttl_seconds=300)
        
        result = cache.get("key1")
        assert result == "value1"

    def test_disk_cache_not_found(self, tmp_path):
        """测试获取不存在的键"""
        cache = DiskCache(cache_dir=str(tmp_path / "cache"))
        result = cache.get("nonexistent")
        assert result is None

    def test_disk_cache_expiration(self, tmp_path):
        """测试 TTL 过期"""
        cache = DiskCache(cache_dir=str(tmp_path / "cache"))
        cache.set("key1", "value1", ttl_seconds=0.1)
        
        assert cache.get("key1") == "value1"
        
        time.sleep(0.2)
        
        assert cache.get("key1") is None

    def test_disk_cache_delete(self, tmp_path):
        """测试删除"""
        cache = DiskCache(cache_dir=str(tmp_path / "cache"))
        cache.set("key1", "value1")
        cache.delete("key1")
        
        assert cache.get("key1") is None

    def test_disk_cache_clear(self, tmp_path):
        """测试清空"""
        cache = DiskCache(cache_dir=str(tmp_path / "cache"))
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        
        cache.clear()
        
        assert cache.get_size() == 0


class TestMultiLevelCache:
    """测试多级缓存"""

    def test_multi_level_cache_init(self, tmp_path):
        """测试初始化"""
        cache = MultiLevelCache(
            l1_max_size=100,
            l1_ttl=300,
            l2_enabled=True,
            l2_dir=str(tmp_path / "cache"),
            l2_max_size_mb=10
        )
        
        assert cache._l1_cache is not None
        assert cache._l2_cache is not None
        assert cache._l2_enabled is True

    def test_multi_level_cache_l1_hit(self, tmp_path):
        """测试 L1 命中"""
        cache = MultiLevelCache(
            l1_max_size=10,
            l2_enabled=True,
            l2_dir=str(tmp_path / "cache")
        )
        
        cache.set("key1", "value1")
        result = cache.get("key1")
        
        assert result == "value1"
        
        stats = cache.get_stats()
        assert stats["l1_hits"] == 1
        assert stats["l2_hits"] == 0

    def test_multi_level_cache_l2_hit(self, tmp_path):
        """测试 L2 命中（L1 未命中）"""
        cache = MultiLevelCache(
            l1_max_size=1,
            l2_enabled=True,
            l2_dir=str(tmp_path / "cache")
        )
        
        # 设置两个键，L1 会淘汰第一个
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        
        # key1 应该在 L2 中
        result = cache.get("key1")
        assert result == "value1"
        
        stats = cache.get_stats()
        assert stats["l2_hits"] >= 1

    def test_multi_level_cache_miss(self, tmp_path):
        """测试缓存未命中"""
        cache = MultiLevelCache(
            l2_enabled=True,
            l2_dir=str(tmp_path / "cache")
        )
        
        result = cache.get("nonexistent")
        assert result is None
        
        stats = cache.get_stats()
        assert stats["total_misses"] >= 1

    def test_multi_level_cache_delete(self, tmp_path):
        """测试删除"""
        cache = MultiLevelCache(
            l2_enabled=True,
            l2_dir=str(tmp_path / "cache")
        )
        
        cache.set("key1", "value1")
        cache.delete("key1")
        
        assert cache.get("key1") is None

    def test_multi_level_cache_clear(self, tmp_path):
        """测试清空"""
        cache = MultiLevelCache(
            l2_enabled=True,
            l2_dir=str(tmp_path / "cache")
        )
        
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        
        cache.clear()
        
        assert cache.get("key1") is None
        assert cache.get("key2") is None

    def test_multi_level_cache_stats(self, tmp_path):
        """测试统计信息"""
        cache = MultiLevelCache(
            l2_enabled=True,
            l2_dir=str(tmp_path / "cache")
        )
        
        cache.set("key1", "value1")
        cache.get("key1")
        
        stats = cache.get_stats()
        
        assert "total_hits" in stats
        assert "total_misses" in stats
        assert "hit_rate" in stats
        assert "l1_size" in stats

    def test_multi_level_cache_l2_disabled(self):
        """测试禁用 L2"""
        cache = MultiLevelCache(l2_enabled=False)
        
        cache.set("key1", "value1")
        result = cache.get("key1")
        
        assert result == "value1"
        assert cache._l2_cache is None

    def test_multi_level_cache_warmup(self, tmp_path):
        """测试缓存预热"""
        def warmup_callback():
            return {"key1": "value1", "key2": "value2"}
        
        cache = MultiLevelCache(
            l2_enabled=True,
            l2_dir=str(tmp_path / "cache"),
            warmup_enabled=True,
            warmup_callback=warmup_callback
        )
        
        cache.warmup()
        
        assert cache.get("key1") == "value1"
        assert cache.get("key2") == "value2"


class TestCacheManager:
    """测试缓存管理器"""

    def test_cache_manager_singleton(self):
        """测试单例模式"""
        manager1 = CacheManager.get_instance()
        manager2 = CacheManager.get_instance()
        
        assert manager1 is manager2

    def test_cache_manager_get_cache(self, tmp_path):
        """测试获取或创建缓存"""
        manager = CacheManager.get_instance()
        
        cache1 = manager.get_cache("test_cache", l2_dir=str(tmp_path / "cache"))
        cache2 = manager.get_cache("test_cache")
        
        assert cache1 is cache2

    def test_cache_manager_remove_cache(self, tmp_path):
        """测试删除缓存"""
        manager = CacheManager.get_instance()
        
        manager.get_cache("test_cache", l2_dir=str(tmp_path / "cache"))
        assert "test_cache" in manager._caches
        
        manager.remove_cache("test_cache")
        assert "test_cache" not in manager._caches

    def test_cache_manager_clear_all(self, tmp_path):
        """测试清空所有缓存"""
        manager = CacheManager.get_instance()
        
        manager.get_cache("cache1", l2_dir=str(tmp_path / "cache1"))
        manager.get_cache("cache2", l2_dir=str(tmp_path / "cache2"))
        
        manager.clear_all()
        
        # 缓存实例仍然存在，但内容被清空
        cache1 = manager.get_cache("cache1")
        assert cache1.get("any_key") is None


class TestThreadSafety:
    """测试线程安全性"""

    def test_lru_cache_thread_safety(self):
        """测试 LRU 缓存线程安全"""
        cache = LRUCache(max_size=100)
        
        def worker():
            for i in range(100):
                cache.set(f"key_{i}", f"value_{i}")
                cache.get(f"key_{i}")
        
        threads = []
        for _ in range(10):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()
        
        # 验证缓存大小合理
        assert cache.get_size() <= 100

    def test_multi_level_cache_thread_safety(self, tmp_path):
        """测试多级缓存线程安全"""
        cache = MultiLevelCache(
            l1_max_size=100,
            l2_enabled=True,
            l2_dir=str(tmp_path / "cache")
        )
        
        def worker():
            for i in range(50):
                cache.set(f"key_{i}", f"value_{i}")
                cache.get(f"key_{i}")
        
        threads = []
        for _ in range(5):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()
        
        stats = cache.get_stats()
        assert stats["total_hits"] > 0


class TestDefaultCache:
    """测试默认缓存实例"""

    def test_default_cache(self):
        """测试默认缓存"""
        default_cache.set("test_key", "test_value")
        result = default_cache.get("test_key")
        
        assert result == "test_value"
        
        # 清理
        default_cache.clear()