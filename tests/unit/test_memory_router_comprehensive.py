#!/usr/bin/env python3
"""MemoryRouter 综合单元测试

【生成日志摘要】
- 生成时间戳: 2026-07-02
- 内容描述: memory_router 模块全量单元测试
- 生成参数: 覆盖 ROUTE_MAP/适配器管理/路由逻辑/缓存层/敏感过滤/便捷方法/to_dict
- 模型配置: GLM-5.2
- 关键状态变化: 新增 ~50 个测试，目标覆盖率 90%+
"""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from agent.memory.base import MemoryInterface, MemoryResult, MemoryCapability
from agent.memory.router import MemoryRouter


# ═══════════════════════════════════════════════════════════════
# 测试辅助：MockAdapter 实现 MemoryInterface
# ═══════════════════════════════════════════════════════════════


class MockAdapter(MemoryInterface):
    """测试用的 Mock 适配器"""

    def __init__(self, name: str = "MockAdapter", capabilities: set = None):
        self._name = name
        self._capabilities = capabilities or set()
        self.save_called = 0
        self.search_called = 0
        self.last_save_args = None

    async def save(self, key, data, metadata=None):
        self.save_called += 1
        self.last_save_args = (key, data, metadata)
        return True

    async def search(self, query, top_k=5):
        self.search_called += 1
        return [MemoryResult(content="result", confidence=0.9, source=self._name)]

    async def get_profile(self, user_id):
        return {"user_id": user_id, "name": "test"}

    async def update_graph(self, entities, relations):
        return True

    @property
    def capabilities(self):
        return self._capabilities

    def to_dict(self):
        return {"name": self._name, "capabilities": [c.value for c in self._capabilities]}


def run_async(coro):
    """同步运行异步协程"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ═══════════════════════════════════════════════════════════════
# ROUTE_MAP 测试
# ═══════════════════════════════════════════════════════════════


class TestRouteMap:
    def test_has_five_routes(self):
        assert len(MemoryRouter.ROUTE_MAP) == 5

    def test_deep_reasoning_routes_to_hindsight(self):
        assert MemoryRouter.ROUTE_MAP["deep_reasoning"] == "hindsight"

    def test_local_privacy_routes_to_holographic(self):
        assert MemoryRouter.ROUTE_MAP["local_privacy"] == "holographic"

    def test_user_profile_routes_to_honcho(self):
        assert MemoryRouter.ROUTE_MAP["user_profile"] == "honcho"

    def test_fact_extraction_routes_to_mem0(self):
        assert MemoryRouter.ROUTE_MAP["fact_extraction"] == "mem0"

    def test_knowledge_nav_routes_to_openviking(self):
        assert MemoryRouter.ROUTE_MAP["knowledge_nav"] == "openviking"


# ═══════════════════════════════════════════════════════════════
# 初始化测试
# ═══════════════════════════════════════════════════════════════


class TestInit:
    def test_default_adapter_is_holographic(self):
        router = MemoryRouter()
        assert router.default.__class__.__name__ == "HolographicAdapter"

    def test_custom_default_adapter(self):
        mock = MockAdapter("custom_default")
        router = MemoryRouter(default_adapter=mock)
        assert router.default is mock

    def test_initial_adapters_empty(self):
        router = MemoryRouter()
        # 只有 __default__
        adapters = router.list_adapters()
        assert len(adapters) == 1
        assert adapters[0]["name"] == "__default__"

    def test_initial_cache_layer_none(self):
        router = MemoryRouter()
        assert router._cache_layer is None

    def test_sensitive_filter_disabled_by_default(self):
        router = MemoryRouter()
        assert router._sensitive_filter_enabled is False
        assert router._memory_boundary_enabled is False
        assert router._sensitive_filter is None


# ═══════════════════════════════════════════════════════════════
# 适配器管理测试
# ═══════════════════════════════════════════════════════════════


class TestAdapterManagement:
    def test_register_adapter(self):
        router = MemoryRouter()
        mock = MockAdapter("test1")
        router.register("test", mock)
        assert router.get_adapter("test") is mock

    def test_register_multiple_adapters(self):
        router = MemoryRouter()
        for i in range(5):
            router.register(f"adapter{i}", MockAdapter(f"m{i}"))
        for i in range(5):
            assert router.get_adapter(f"adapter{i}") is not None

    def test_register_non_memory_interface_raises(self):
        router = MemoryRouter()
        with pytest.raises(TypeError):
            router.register("bad", "not an adapter")

    def test_unregister_adapter(self):
        router = MemoryRouter()
        mock = MockAdapter()
        router.register("test", mock)
        router.unregister("test")
        assert router.get_adapter("test") is None

    def test_unregister_nonexistent_no_error(self):
        router = MemoryRouter()
        router.unregister("not_exist")  # 不应抛异常

    def test_get_adapter_nonexistent_returns_none(self):
        router = MemoryRouter()
        assert router.get_adapter("not_exist") is None

    def test_list_adapters_includes_registered(self):
        router = MemoryRouter()
        router.register("hindsight", MockAdapter("HindsightMock"))
        router.register("mem0", MockAdapter("Mem0Mock"))
        adapters = router.list_adapters()
        names = [a["name"] for a in adapters]
        assert "hindsight" in names
        assert "mem0" in names
        assert "__default__" in names

    def test_list_adapters_count(self):
        router = MemoryRouter()
        router.register("a1", MockAdapter())
        router.register("a2", MockAdapter())
        # 2 个注册 + 1 个默认
        assert len(router.list_adapters()) == 3

    def test_list_adapters_includes_capabilities(self):
        router = MemoryRouter()
        caps = {MemoryCapability.SEMANTIC_SEARCH, MemoryCapability.LOCAL_FIRST}
        router.register("cap_adapter", MockAdapter(capabilities=caps))
        adapters = router.list_adapters()
        cap_adapter = next(a for a in adapters if a["name"] == "cap_adapter")
        assert "semantic_search" in cap_adapter["capabilities"]
        assert "local_first" in cap_adapter["capabilities"]


# ═══════════════════════════════════════════════════════════════
# 路由逻辑测试
# ═══════════════════════════════════════════════════════════════


class TestRouteLogic:
    def test_route_returns_registered_adapter(self):
        router = MemoryRouter()
        mock = MockAdapter("HindsightMock")
        router.register("hindsight", mock)
        assert router.route("deep_reasoning") is mock

    def test_route_returns_default_when_adapter_not_registered(self):
        router = MemoryRouter()
        # hindsight 未注册 → 返回默认适配器
        result = router.route("deep_reasoning")
        assert result is router.default

    def test_route_unknown_task_type_returns_default(self):
        router = MemoryRouter()
        result = router.route("unknown_type")
        assert result is router.default

    def test_route_default_task_type(self):
        router = MemoryRouter()
        result = router.route("local_privacy")
        # local_privacy → holographic，但未注册 holographic → 返回默认
        assert result is router.default

    def test_route_with_explicit_adapter_registered(self):
        router = MemoryRouter()
        mock = MockAdapter("Mem0Mock")
        router.register("mem0", mock)
        assert router.route("fact_extraction") is mock

    def test_route_returns_non_none_always(self):
        router = MemoryRouter()
        for task_type in ["deep_reasoning", "local_privacy", "user_profile",
                          "fact_extraction", "knowledge_nav", "unknown"]:
            assert router.route(task_type) is not None


# ═══════════════════════════════════════════════════════════════
# default 属性测试
# ═══════════════════════════════════════════════════════════════


class TestDefaultProperty:
    def test_get_default(self):
        router = MemoryRouter()
        assert router.default is not None

    def test_set_default(self):
        router = MemoryRouter()
        mock = MockAdapter("new_default")
        router.default = mock
        assert router.default is mock

    def test_set_default_non_interface_raises(self):
        router = MemoryRouter()
        with pytest.raises(TypeError):
            router.default = "not an adapter"

    def test_set_default_affects_route(self):
        router = MemoryRouter()
        mock = MockAdapter("new_default")
        router.default = mock
        # 未知 task_type → 返回默认
        assert router.route("unknown") is mock


# ═══════════════════════════════════════════════════════════════
# 缓存层测试
# ═══════════════════════════════════════════════════════════════


class TestCacheLayer:
    def test_attach_cache_layer(self):
        router = MemoryRouter()
        mock_cache = MagicMock()
        router.attach_cache_layer(mock_cache)
        assert router._cache_layer is mock_cache

    def test_detach_cache_layer(self):
        router = MemoryRouter()
        router.attach_cache_layer(MagicMock())
        router.detach_cache_layer()
        assert router._cache_layer is None

    def test_search_uses_cache_on_hit(self):
        router = MemoryRouter()
        mock_adapter = MockAdapter("test")
        router.register("holographic", mock_adapter)

        mock_cache = MagicMock()
        cached_results = [MemoryResult(content="cached", confidence=1.0, source="cache")]
        mock_cache.get.return_value = cached_results
        router.attach_cache_layer(mock_cache)

        results = run_async(router.search("query", task_type="local_privacy"))
        assert results == cached_results
        assert mock_adapter.search_called == 0  # 缓存命中，不调用适配器

    def test_search_writes_cache_on_miss(self):
        router = MemoryRouter()
        mock_adapter = MockAdapter("test")
        router.register("holographic", mock_adapter)

        mock_cache = MagicMock()
        mock_cache.get.return_value = None  # 缓存未命中
        router.attach_cache_layer(mock_cache)

        results = run_async(router.search("query", task_type="local_privacy"))
        assert len(results) == 1
        assert mock_adapter.search_called == 1
        # 验证缓存写入被调用
        mock_cache.set.assert_called_once()

    def test_search_without_cache_layer(self):
        router = MemoryRouter()
        mock_adapter = MockAdapter("test")
        router.register("holographic", mock_adapter)

        results = run_async(router.search("query", task_type="local_privacy"))
        assert len(results) == 1
        assert mock_adapter.search_called == 1


# ═══════════════════════════════════════════════════════════════
# 敏感信息过滤测试
# ═══════════════════════════════════════════════════════════════


class TestSensitiveFilter:
    def test_filter_disabled_returns_original(self):
        router = MemoryRouter()
        # 默认禁用
        has_sensitive, filtered, patterns = router._filter_sensitive_info("some content")
        assert has_sensitive is False
        assert filtered == "some content"
        assert patterns == []

    def test_filter_enabled_no_sensitive(self):
        router = MemoryRouter()
        router._sensitive_filter_enabled = True

        # Mock 敏感过滤器
        mock_filter = MagicMock()
        mock_result = MagicMock()
        mock_result.allowed = True
        mock_result.violations = []
        mock_filter.detect.return_value = mock_result
        router._sensitive_filter = mock_filter

        has_sensitive, filtered, patterns = router._filter_sensitive_info("normal content")
        assert has_sensitive is False
        assert patterns == []

    def test_filter_enabled_with_sensitive(self):
        router = MemoryRouter()
        router._sensitive_filter_enabled = True

        mock_filter = MagicMock()
        mock_result = MagicMock()
        mock_result.allowed = False
        mock_result.violations = [{"type": "password"}]
        mock_result.sanitized_content = "filtered [REDACTED] content"
        mock_filter.detect.return_value = mock_result
        router._sensitive_filter = mock_filter

        has_sensitive, filtered, patterns = router._filter_sensitive_info("password=123456")
        assert has_sensitive is True
        assert len(patterns) == 1
        assert "[REDACTED]" in filtered

    def test_filter_with_mask_fallback(self):
        router = MemoryRouter()
        router._sensitive_filter_enabled = True

        mock_filter = MagicMock()
        mock_result = MagicMock()
        mock_result.allowed = False
        mock_result.violations = [{"type": "token"}]
        mock_result.sanitized_content = None  # 没有 sanitized_content
        mock_filter.mask.return_value = "********"
        mock_filter.detect.return_value = mock_result
        router._sensitive_filter = mock_filter

        has_sensitive, filtered, patterns = router._filter_sensitive_info("token=abc")
        assert has_sensitive is True
        assert "[REDACTED]" in filtered


# ═══════════════════════════════════════════════════════════════
# 内存边界约束测试
# ═══════════════════════════════════════════════════════════════


class TestMemoryBoundary:
    def test_boundary_blocks_sensitive_save(self):
        router = MemoryRouter()
        router._memory_boundary_enabled = True
        router._sensitive_filter_enabled = True

        # Mock 敏感过滤器
        mock_filter = MagicMock()
        mock_result = MagicMock()
        mock_result.allowed = False
        mock_result.violations = [{"type": "password"}]
        mock_filter.detect.return_value = mock_result
        router._sensitive_filter = mock_filter

        result = run_async(router.save("key", "password=123", task_type="local_privacy"))
        assert result is False

    def test_boundary_allows_non_sensitive_save(self):
        router = MemoryRouter()
        router._memory_boundary_enabled = True
        router._sensitive_filter_enabled = True

        mock_adapter = MockAdapter()
        router.register("holographic", mock_adapter)

        # Mock 敏感过滤器（无敏感信息）
        mock_filter = MagicMock()
        mock_result = MagicMock()
        mock_result.allowed = True
        mock_result.violations = []
        mock_filter.detect.return_value = mock_result
        router._sensitive_filter = mock_filter

        result = run_async(router.save("key", "normal data", task_type="local_privacy"))
        assert result is True
        assert mock_adapter.save_called == 1

    def test_boundary_disabled_allows_save(self):
        router = MemoryRouter()
        # boundary 和 filter 都禁用
        mock_adapter = MockAdapter()
        router.register("holographic", mock_adapter)

        result = run_async(router.save("key", "password=123", task_type="local_privacy"))
        assert result is True
        assert mock_adapter.save_called == 1


# ═══════════════════════════════════════════════════════════════
# 便捷方法测试
# ═══════════════════════════════════════════════════════════════


class TestConvenienceMethods:
    def test_save_routes_to_adapter(self):
        router = MemoryRouter()
        mock_adapter = MockAdapter()
        router.register("holographic", mock_adapter)

        result = run_async(router.save("key1", "data1", task_type="local_privacy"))
        assert result is True
        assert mock_adapter.save_called == 1
        assert mock_adapter.last_save_args == ("key1", "data1", None)

    def test_save_with_metadata(self):
        router = MemoryRouter()
        mock_adapter = MockAdapter()
        router.register("holographic", mock_adapter)

        meta = {"category": "test"}
        run_async(router.save("key", "data", metadata=meta, task_type="local_privacy"))
        assert mock_adapter.last_save_args == ("key", "data", meta)

    def test_search_returns_results(self):
        router = MemoryRouter()
        mock_adapter = MockAdapter()
        router.register("holographic", mock_adapter)

        results = run_async(router.search("query", top_k=3, task_type="local_privacy"))
        assert len(results) == 1
        assert results[0].source == "MockAdapter"

    def test_get_profile_routes_to_adapter(self):
        router = MemoryRouter()
        mock_adapter = MockAdapter()
        router.register("honcho", mock_adapter)

        profile = run_async(router.get_profile("user123", task_type="user_profile"))
        assert profile["user_id"] == "user123"

    def test_update_graph_routes_to_adapter(self):
        router = MemoryRouter()
        mock_adapter = MockAdapter()
        router.register("openviking", mock_adapter)

        result = run_async(
            router.update_graph(
                entities=[{"name": "e1"}],
                relations=[{"source": "e1", "target": "e2"}],
                task_type="knowledge_nav",
            )
        )
        assert result is True


# ═══════════════════════════════════════════════════════════════
# to_dict 测试
# ═══════════════════════════════════════════════════════════════


class TestToDict:
    def test_to_dict_contains_required_fields(self):
        router = MemoryRouter()
        d = router.to_dict()
        assert "type" in d
        assert "adapters" in d
        assert "route_map" in d
        assert "cache_layer" in d
        assert "boundary_enabled" in d
        assert "sensitive_filter_enabled" in d

    def test_to_dict_type_is_class_name(self):
        router = MemoryRouter()
        assert router.to_dict()["type"] == "MemoryRouter"

    def test_to_dict_route_map_matches_route_map(self):
        router = MemoryRouter()
        assert router.to_dict()["route_map"] == dict(MemoryRouter.ROUTE_MAP)

    def test_to_dict_cache_layer_status(self):
        router = MemoryRouter()
        assert router.to_dict()["cache_layer"] is False

        router.attach_cache_layer(MagicMock())
        assert router.to_dict()["cache_layer"] is True

    def test_to_dict_includes_registered_adapters(self):
        router = MemoryRouter()
        router.register("hindsight", MockAdapter("HindsightMock"))
        d = router.to_dict()
        names = [a["name"] for a in d["adapters"]]
        assert "hindsight" in names
        assert "__default__" in names


# ═══════════════════════════════════════════════════════════════
# 集成测试
# ═══════════════════════════════════════════════════════════════


class TestIntegration:
    def test_full_save_search_flow(self):
        """完整流程：注册适配器 → save → search"""
        router = MemoryRouter()
        mock_adapter = MockAdapter("integration_test")
        router.register("holographic", mock_adapter)

        # save
        save_result = run_async(router.save("key1", "content1", task_type="local_privacy"))
        assert save_result is True

        # search
        search_results = run_async(router.search("content", task_type="local_privacy"))
        assert len(search_results) == 1

    def test_multiple_task_types_route_to_different_adapters(self):
        """不同任务类型路由到不同适配器"""
        router = MemoryRouter()
        hindsight_mock = MockAdapter("Hindsight")
        mem0_mock = MockAdapter("Mem0")
        router.register("hindsight", hindsight_mock)
        router.register("mem0", mem0_mock)

        # deep_reasoning → hindsight
        assert router.route("deep_reasoning") is hindsight_mock

        # fact_extraction → mem0
        assert router.route("fact_extraction") is mem0_mock

    def test_router_instances_independent(self):
        """路由器实例互相独立"""
        r1 = MemoryRouter()
        r2 = MemoryRouter()

        mock = MockAdapter()
        r1.register("hindsight", mock)

        assert r1.get_adapter("hindsight") is mock
        assert r2.get_adapter("hindsight") is None
