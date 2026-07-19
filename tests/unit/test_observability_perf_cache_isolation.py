"""observability_optimizations 和 performance_optimization 的缓存隔离测试

验证两个模块中独立的 MemoryEfficientCache.get() 返回 deepcopy 副本，
调用方修改返回值不污染缓存（出口隔离契约）。

注意：这两个模块的 MemoryEfficientCache 是独立实现，不继承 multi_level_cache.LRUCache，
因此 P1 修复（multi_level_cache.LRUCache.get deepcopy）不会自动消除它们的风险，
必须单独修复并单独测试。
"""
import pytest
import time


# ── observability_optimizations.MemoryEfficientCache 隔离测试 ──


class TestObservabilityCacheIsolation:
    """验证 observability_optimizations.MemoryEfficientCache.get() 返回 deepcopy 副本"""

    @pytest.fixture
    def cache(self):
        from agent.monitoring.observability_optimizations import MemoryEfficientCache
        c = MemoryEfficientCache(max_size=100, ttl_seconds=300)
        return c

    def test_get_returns_independent_copy(self, cache):
        """get() 返回的 dict 修改后不影响缓存"""
        cache.set("key1", {"name": "original", "value": 42})

        result = cache.get("key1")
        assert result is not None

        # 修改返回值
        result["name"] = "HACKED"
        result["value"] = 999

        # 再次获取，缓存应未被污染
        result2 = cache.get("key1")
        assert result2["name"] == "original"
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
        cache.set("key1", {"data": "old"})
        # 手动设置过期时间
        with cache._lock:
            value, _ = cache._cache["key1"]
            cache._cache["key1"] = (value, time.time() - 400)
        result = cache.get("key1")
        assert result is None

    def test_trace_context_scenario_not_leaked(self, cache):
        """验证 trace 上下文缓存场景不泄漏"""
        trace_context = {
            "trace_id": "abc123",
            "spans": [
                {"span_id": "s1", "operation": "http_request", "duration_ms": 50.5},
                {"span_id": "s2", "operation": "db_query", "duration_ms": 12.3},
            ],
            "metadata": {"service": "api-gateway", "env": "prod"},
        }
        cache.set("trace_abc123", trace_context)

        # 获取并修改
        cached = cache.get("trace_abc123")
        cached["trace_id"] = "HACKED"
        cached["spans"].append({"span_id": "injected"})
        cached["spans"][0]["duration_ms"] = 999.0
        cached["metadata"]["env"] = "staging"

        # 再次获取，缓存应未被污染
        cached2 = cache.get("trace_abc123")
        assert cached2["trace_id"] == "abc123"
        assert len(cached2["spans"]) == 2
        assert cached2["spans"][0]["duration_ms"] == 50.5
        assert cached2["metadata"]["env"] == "prod"


# ── performance_optimization.MemoryEfficientCache 隔离测试 ──


class TestPerformanceCacheIsolation:
    """验证 performance_optimization.MemoryEfficientCache.get() 返回 deepcopy 副本"""

    @pytest.fixture
    def cache(self):
        from agent.monitoring.performance_optimization import (
            MemoryEfficientCache, OptimizationConfig,
        )
        config = OptimizationConfig()
        c = MemoryEfficientCache(config=config)
        return c

    def test_get_returns_independent_copy(self, cache):
        """get() 返回的 dict 修改后不影响缓存"""
        cache.set("key1", {"name": "original", "value": 42})

        result = cache.get("key1")
        assert result is not None

        # 修改返回值
        result["name"] = "HACKED"
        result["value"] = 999

        # 再次获取，缓存应未被污染
        result2 = cache.get("key1")
        assert result2["name"] == "original"
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

        # 修改嵌套 dict
        result["config"]["timeout"] = 999
        result["config"]["options"]["retry"] = 999

        # 缓存应未被污染
        result2 = cache.get("key1")
        assert result2["config"]["timeout"] == original_timeout
        assert result2["config"]["options"]["retry"] == 3

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
        cache.set("key1", {"data": "old"})
        # 手动设置过期时间
        with cache._lock:
            value, _ = cache._cache["key1"]
            cache._cache["key1"] = (value, time.time() - 99999)
        result = cache.get("key1")
        assert result is None

    def test_performance_metrics_scenario_not_leaked(self, cache):
        """验证性能指标缓存场景不泄漏"""
        metrics = {
            "operation": "llm_call",
            "latency_ms": 1250.5,
            "token_count": {"prompt": 150, "completion": 80},
            "tags": ["llm", "gpt-4"],
            "metadata": {"model": "gpt-4", "cached": False},
        }
        cache.set("metrics_llm_123", metrics)

        # 获取并修改
        cached = cache.get("metrics_llm_123")
        cached["latency_ms"] = 0.0
        cached["token_count"]["completion"] = 999
        cached["tags"].append("HACKED")
        cached["metadata"]["cached"] = True

        # 再次获取，缓存应未被污染
        cached2 = cache.get("metrics_llm_123")
        assert cached2["latency_ms"] == 1250.5
        assert cached2["token_count"]["completion"] == 80
        assert "HACKED" not in cached2["tags"]
        assert cached2["metadata"]["cached"] is False
