"""TracingCache 缓存隔离测试（出口隔离契约守护）

验证 monitoring/tracing_cache.py 的 LRUCache.get() 返回 deepcopy 副本，
调用方修改不污染缓存。
"""
import pytest
import time

from agent.monitoring.tracing_cache import LRUCache


class TestTracingCacheIsolation:
    """验证 LRUCache.get() 返回 deepcopy 副本，调用方修改不污染缓存"""

    @pytest.fixture
    def cache(self):
        """LRUCache 实例"""
        return LRUCache(max_size=100, ttl_seconds=300)

    def test_get_returns_independent_copy(self, cache):
        """get() 返回的 dict 修改后不影响缓存"""
        cache.set("key1", {"name": "original", "value": 42})

        result = cache.get("key1")
        assert result is not None
        original_name = result["name"]

        # 修改返回值
        result["name"] = "HACKED"
        result["value"] = 999

        # 再次获取，缓存应未被污染
        result2 = cache.get("key1")
        assert result2["name"] == original_name
        assert result2["value"] == 42

    def test_get_multiple_calls_return_different_objects(self, cache):
        """多次 get() 返回不同的对象"""
        cache.set("key1", {"items": [1, 2, 3]})

        result1 = cache.get("key1")
        result2 = cache.get("key1")

        assert result1 is not result2
        assert result1["items"] is not result2["items"]

    def test_get_nested_dict_not_shared(self, cache):
        """嵌套 dict 修改后不影响缓存"""
        cache.set("key1", {
            "config": {"timeout": 60, "options": {"retry": 3}}
        })

        result = cache.get("key1")
        original_timeout = result["config"]["timeout"]
        original_retry = result["config"]["options"]["retry"]

        # 修改嵌套 dict
        result["config"]["timeout"] = 999
        result["config"]["options"]["retry"] = 999

        # 缓存应未被污染
        result2 = cache.get("key1")
        assert result2["config"]["timeout"] == original_timeout
        assert result2["config"]["options"]["retry"] == original_retry

    def test_get_nested_list_modifications_not_shared(self, cache):
        """嵌套 list 通过 append/clear/[0]= 修改不影响缓存"""
        cache.set("key1", {
            "tags": ["a", "b", "c"],
            "items": [{"id": 1}, {"id": 2}],
        })

        # 场景 1: append
        result = cache.get("key1")
        result["tags"].append("HACKED")
        result2 = cache.get("key1")
        assert "HACKED" not in result2["tags"]
        assert len(result2["tags"]) == 3

        # 场景 2: clear
        result = cache.get("key1")
        result["items"].clear()
        result2 = cache.get("key1")
        assert len(result2["items"]) == 2

        # 场景 3: [0] = 替换
        result = cache.get("key1")
        result["tags"][0] = "HACKED"
        result2 = cache.get("key1")
        assert result2["tags"][0] == "a"

    def test_get_returns_none_for_missing_key(self, cache):
        """不存在的 key 返回 None"""
        assert cache.get("nonexistent_key") is None

    def test_get_returns_none_for_expired(self, cache):
        """过期的 key 返回 None"""
        # 设置一个已过期的缓存条目（直接操作内部结构）
        with cache._lock:
            cache._cache["expired_key"] = ({"data": "old"}, time.time() - 400)

        result = cache.get("expired_key")
        assert result is None
