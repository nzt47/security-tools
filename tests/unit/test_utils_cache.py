"""Cache Utilities 单元测试 — 基于 caching.multi_level_cache"""

import pytest
import time

from agent.caching.multi_level_cache import (
    MultiLevelCache,
    lru_cache_decorator,
    QueryCache,
)


class TestMultiLevelCache:
    """测试 MultiLevelCache 基本缓存行为"""

    def test_init(self):
        """测试初始化"""
        cache = MultiLevelCache(l1_max_size=100, l1_ttl=300)
        assert cache._l1_cache.max_size == 100
        assert cache._l1_cache.default_ttl == 300

    def test_get_set(self):
        """测试基本读写"""
        cache = MultiLevelCache(l1_max_size=10, l2_enabled=False)
        cache.set("key1", "value1")

        result = cache.get("key1")
        assert result == "value1"

    def test_not_found(self):
        """测试获取不存在的键"""
        cache = MultiLevelCache(l2_enabled=False)
        result = cache.get("nonexistent")
        assert result is None

    def test_eviction(self):
        """测试 LRU 淘汰"""
        cache = MultiLevelCache(l1_max_size=3, l2_enabled=False)

        cache.set("key1", "v1")
        cache.set("key2", "v2")
        cache.set("key3", "v3")

        # 访问 key1 提升其优先级
        cache.get("key1")

        # 添加第4个，应淘汰最旧的（key2）
        cache.set("key4", "v4")

        assert cache.get("key1") == "v1"
        assert cache.get("key2") is None   # 被淘汰
        assert cache.get("key3") == "v3"
        assert cache.get("key4") == "v4"

    def test_ttl_expiration(self):
        """测试 TTL 过期"""
        cache = MultiLevelCache(l1_max_size=10, l1_ttl=0.1, l2_enabled=False)
        cache.set("key1", "value1")

        assert cache.get("key1") == "value1"

        time.sleep(0.2)

        assert cache.get("key1") is None

    def test_clear(self):
        """测试清空"""
        cache = MultiLevelCache(l2_enabled=False)
        cache.set("key1", "value1")
        cache.set("key2", "value2")

        cache.clear()

        assert cache.get("key1") is None
        assert cache.get("key2") is None

    def test_get_stats(self):
        """测试获取统计信息"""
        cache = MultiLevelCache(l2_enabled=False)
        cache.set("key1", "value1")
        cache.get("key1")
        cache.get("key2")

        stats = cache.get_stats()

        assert stats["total_hits"] == 1
        assert stats["total_misses"] == 1
        assert "l1_size" in stats


class TestLruCacheDecorator:
    """测试 LRU 缓存装饰器"""

    def test_basic(self):
        """测试基本缓存功能"""
        call_count = [0]

        @lru_cache_decorator(max_size=10, ttl_seconds=300)
        def expensive_function(x):
            call_count[0] += 1
            return x * 2

        assert expensive_function(5) == 10
        assert expensive_function(5) == 10
        assert expensive_function(10) == 20
        assert call_count[0] == 2

    def test_with_kwargs(self):
        """测试带关键字参数的缓存"""
        call_count = [0]

        @lru_cache_decorator(max_size=10)
        def function_with_kwargs(a, b, c=10):
            call_count[0] += 1
            return a + b + c

        assert function_with_kwargs(1, 2, c=3) == 6
        assert function_with_kwargs(1, 2, c=3) == 6
        assert function_with_kwargs(1, 2, c=4) == 7
        assert call_count[0] == 2

    def test_ttl(self):
        """测试 TTL 过期"""
        call_count = [0]

        @lru_cache_decorator(max_size=10, ttl_seconds=0.1)
        def timed_function(x):
            call_count[0] += 1
            return x * 2

        timed_function(5)
        timed_function(5)
        assert call_count[0] == 1

        time.sleep(0.2)

        timed_function(5)
        assert call_count[0] == 2


class TestQueryCache:
    """测试查询缓存管理器"""

    def test_init(self):
        """测试初始化"""
        query_cache = QueryCache(max_size=100, ttl_seconds=300)
        assert query_cache._search_cache is not None
        assert query_cache._recent_cache is not None

    def test_clear_all(self):
        """测试清空所有缓存"""
        query_cache = QueryCache()

        query_cache.set_search("key1", "value1")
        query_cache.set_recent("key2", "value2")

        query_cache.clear_all()

        assert query_cache.get_search("key1") is None
        assert query_cache.get_recent("key2") is None

    def test_get_stats(self):
        """测试获取统计信息"""
        query_cache = QueryCache()

        query_cache.set_search("key1", "value1")
        query_cache.get_search("key1")

        stats = query_cache.get_stats()

        assert "search" in stats
        assert "recent" in stats
        assert "total_hits" in stats["search"]
