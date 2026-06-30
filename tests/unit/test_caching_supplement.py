"""Multi-Level Cache 补充测试"""
from agent.caching.multi_level_cache import MultiLevelCache


class TestMultiLevelCache:
    """多级缓存补充测试"""

    def test_init(self):
        cache = MultiLevelCache()
        assert cache is not None

    def test_set_and_get(self):
        cache = MultiLevelCache()
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_get_missing(self):
        cache = MultiLevelCache()
        assert cache.get("nonexistent") is None

    def test_delete(self):
        cache = MultiLevelCache()
        cache.set("k", "v")
        cache.delete("k")
        assert cache.get("k") is None

    def test_clear(self):
        cache = MultiLevelCache()
        cache.set("a", 1)
        cache.set("b", 2)
        cache.clear()
        assert cache.get("a") is None
        assert cache.get("b") is None

    def test_contains(self):
        cache = MultiLevelCache()
        cache.set("x", "y")
        assert cache.get("x") is not None

    def test_not_contains(self):
        cache = MultiLevelCache()
        assert cache.get("z") is None
