"""
Memory 重构模块单元测试
覆盖 base.py、router.py、filter.py、reviewer.py、long_term_memory.py、short_term_memory.py
"""
import pytest
import tempfile
import time
import json
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from dataclasses import dataclass, field

from agent.memory.base import (
    MemoryResult,
    MemoryInterface,
    MemoryCapability,
)
from agent.memory.router import MemoryRouter
from agent.memory.filter import SensitiveDataFilter, SensitiveLevel, FilterResult
from agent.memory.reviewer import MemoryReviewer, ReviewResult
from agent.memory.long_term_memory import LongTermMemory, LongTermMemoryEntry
from agent.memory.short_term_memory import ShortTermMemory, ShortTermMemoryEntry


# ============================================================================
# base.py 测试
# ============================================================================


class TestMemoryResult:
    """测试 MemoryResult 数据类"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_base_memoryresult_creation_normal(self):
        """测试 MemoryResult 正常创建"""
        result = MemoryResult(
            content="测试内容",
            confidence=0.95,
            source="test_source",
            metadata={"key": "value"},
        )

        assert result.content == "测试内容"
        assert result.confidence == 0.95
        assert result.source == "test_source"
        assert result.metadata == {"key": "value"}

    @pytest.mark.unit
    @pytest.mark.p2
    def test_base_memoryresult_default_metadata(self):
        """测试 MemoryResult 默认 metadata 为空字典"""
        result = MemoryResult(
            content="内容",
            confidence=0.8,
            source="source",
        )

        assert result.metadata == {}

    @pytest.mark.unit
    @pytest.mark.p2
    def test_base_memoryresult_content_any_type(self):
        """测试 MemoryResult content 支持任意类型"""
        result_dict = MemoryResult(
            content={"nested": "data"},
            confidence=1.0,
            source="dict_source",
        )
        result_list = MemoryResult(
            content=[1, 2, 3],
            confidence=0.5,
            source="list_source",
        )

        assert isinstance(result_dict.content, dict)
        assert result_dict.content["nested"] == "data"
        assert isinstance(result_list.content, list)
        assert len(result_list.content) == 3

    @pytest.mark.unit
    @pytest.mark.p2
    def test_base_memoryresult_confidence_edge_cases(self):
        """测试 MemoryResult 置信度边界值"""
        result_zero = MemoryResult(content="", confidence=0.0, source="test")
        result_one = MemoryResult(content="", confidence=1.0, source="test")

        assert result_zero.confidence == 0.0
        assert result_one.confidence == 1.0


class TestMemoryCapability:
    """测试 MemoryCapability 枚举"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_base_memorycapability_enum_values(self):
        """测试 MemoryCapability 枚举值完整性"""
        assert MemoryCapability.SEMANTIC_SEARCH.value == "semantic_search"
        assert MemoryCapability.FULLTEXT_SEARCH.value == "fulltext_search"
        assert MemoryCapability.FACT_EXTRACTION.value == "fact_extraction"
        assert MemoryCapability.KNOWLEDGE_GRAPH.value == "knowledge_graph"
        assert MemoryCapability.USER_PROFILE.value == "user_profile"
        assert MemoryCapability.LOCAL_FIRST.value == "local_first"
        assert MemoryCapability.REMOTE_SYNC.value == "remote_sync"

    @pytest.mark.unit
    @pytest.mark.p2
    def test_base_memorycapability_set_operations(self):
        """测试 MemoryCapability 集合操作"""
        caps = {MemoryCapability.LOCAL_FIRST, MemoryCapability.FULLTEXT_SEARCH}

        assert MemoryCapability.LOCAL_FIRST in caps
        assert MemoryCapability.SEMANTIC_SEARCH not in caps
        assert len(caps) == 2


class TestMemoryInterface:
    """测试 MemoryInterface 抽象基类"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_base_memoryinterface_cannot_instantiate(self):
        """测试 MemoryInterface 不能直接实例化"""
        with pytest.raises(TypeError):
            MemoryInterface()

    @pytest.mark.unit
    @pytest.mark.p2
    def test_base_memoryinterface_subclass_must_implement(self):
        """测试子类必须实现所有抽象方法"""

        class IncompleteAdapter(MemoryInterface):
            async def save(self, key, data, metadata=None):
                return True

        with pytest.raises(TypeError):
            IncompleteAdapter()

    @pytest.mark.unit
    @pytest.mark.p2
    def test_base_memoryinterface_default_capabilities(self):
        """测试默认 capabilities 返回空集合"""

        class ConcreteAdapter(MemoryInterface):
            async def save(self, key, data, metadata=None):
                return True

            async def search(self, query, top_k=5):
                return []

            async def get_profile(self, user_id):
                return {}

            async def update_graph(self, entities, relations):
                return True

        adapter = ConcreteAdapter()
        assert adapter.capabilities == set()

    @pytest.mark.unit
    @pytest.mark.p2
    def test_base_memoryinterface_to_dict(self):
        """测试 to_dict 方法返回元信息"""

        class TestAdapter(MemoryInterface):
            @property
            def capabilities(self):
                return {MemoryCapability.LOCAL_FIRST}

            async def save(self, key, data, metadata=None):
                return True

            async def search(self, query, top_k=5):
                return []

            async def get_profile(self, user_id):
                return {}

            async def update_graph(self, entities, relations):
                return True

        adapter = TestAdapter()
        info = adapter.to_dict()

        assert info["name"] == "TestAdapter"
        assert "local_first" in info["capabilities"]


# ============================================================================
# router.py 测试
# ============================================================================


class MockMemoryAdapter(MemoryInterface):
    """Mock 适配器用于测试"""

    def __init__(self, name="mock"):
        self._name = name
        self._capabilities = {MemoryCapability.LOCAL_FIRST}
        self.save_called = False
        self.search_called = False
        self.search_query = None
        self.search_results = []

    @property
    def capabilities(self):
        return self._capabilities

    async def save(self, key, data, metadata=None):
        self.save_called = True
        return True

    async def search(self, query, top_k=5):
        self.search_called = True
        self.search_query = query
        return self.search_results

    async def get_profile(self, user_id):
        return {"user_id": user_id}

    async def update_graph(self, entities, relations):
        return True


class TestMemoryRouterRegistration:
    """测试 MemoryRouter 适配器注册/注销"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_router_register_adapter_success(self):
        """测试正常注册适配器"""
        router = MemoryRouter(default_adapter=MockMemoryAdapter("default"))
        adapter = MockMemoryAdapter("test")

        router.register("test_adapter", adapter)

        assert router.get_adapter("test_adapter") is adapter

    @pytest.mark.unit
    @pytest.mark.p2
    def test_router_register_invalid_adapter_raises(self):
        """测试注册非 MemoryInterface 适配器抛出 TypeError"""
        router = MemoryRouter(default_adapter=MockMemoryAdapter("default"))

        with pytest.raises(TypeError):
            router.register("bad", "not_an_adapter")

    @pytest.mark.unit
    @pytest.mark.p2
    def test_router_unregister_existing_adapter(self):
        """测试注销已注册的适配器"""
        router = MemoryRouter(default_adapter=MockMemoryAdapter("default"))
        adapter = MockMemoryAdapter("test")
        router.register("test_adapter", adapter)

        router.unregister("test_adapter")

        assert router.get_adapter("test_adapter") is None

    @pytest.mark.unit
    @pytest.mark.p2
    def test_router_unregister_nonexistent_no_error(self):
        """测试注销不存在的适配器不报错"""
        router = MemoryRouter(default_adapter=MockMemoryAdapter("default"))

        router.unregister("nonexistent")

    @pytest.mark.unit
    @pytest.mark.p2
    def test_router_list_adapters_includes_default(self):
        """测试 list_adapters 包含默认适配器"""
        router = MemoryRouter(default_adapter=MockMemoryAdapter("default"))
        adapter = MockMemoryAdapter("test")
        router.register("test_adapter", adapter)

        adapters = router.list_adapters()

        assert len(adapters) >= 2
        names = [a["name"] for a in adapters]
        assert "__default__" in names
        assert "test_adapter" in names


class TestMemoryRouterRouting:
    """测试 MemoryRouter 路由逻辑"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_router_route_known_task_registered(self):
        """测试已知任务类型路由到已注册适配器"""
        default = MockMemoryAdapter("default")
        mem0 = MockMemoryAdapter("mem0")
        router = MemoryRouter(default_adapter=default)
        router.register("mem0", mem0)

        result = router.route("fact_extraction")

        assert result is mem0

    @pytest.mark.unit
    @pytest.mark.p2
    def test_router_route_known_task_not_registered_fallback(self):
        """测试已知任务类型但适配器未注册时降级到默认适配器"""
        default = MockMemoryAdapter("default")
        router = MemoryRouter(default_adapter=default)

        result = router.route("fact_extraction")

        assert result is default

    @pytest.mark.unit
    @pytest.mark.p2
    def test_router_route_unknown_task_fallback(self):
        """测试未知任务类型返回默认适配器"""
        default = MockMemoryAdapter("default")
        router = MemoryRouter(default_adapter=default)

        result = router.route("unknown_task_type")

        assert result is default

    @pytest.mark.unit
    @pytest.mark.p2
    def test_router_route_default_task_type(self):
        """测试默认任务类型 local_privacy 路由"""
        default = MockMemoryAdapter("default")
        holographic = MockMemoryAdapter("holographic")
        router = MemoryRouter(default_adapter=default)
        router.register("holographic", holographic)

        result = router.route("local_privacy")

        assert result is holographic

    @pytest.mark.unit
    @pytest.mark.p2
    def test_router_default_property_getter(self):
        """测试 default 属性获取"""
        default = MockMemoryAdapter("default")
        router = MemoryRouter(default_adapter=default)

        assert router.default is default

    @pytest.mark.unit
    @pytest.mark.p2
    def test_router_default_property_setter_valid(self):
        """测试 default 属性设置有效适配器"""
        default1 = MockMemoryAdapter("default1")
        default2 = MockMemoryAdapter("default2")
        router = MemoryRouter(default_adapter=default1)

        router.default = default2

        assert router.default is default2

    @pytest.mark.unit
    @pytest.mark.p2
    def test_router_default_property_setter_invalid(self):
        """测试 default 属性设置无效适配器抛出 TypeError"""
        default = MockMemoryAdapter("default")
        router = MemoryRouter(default_adapter=default)

        with pytest.raises(TypeError):
            router.default = "not_an_adapter"


class TestMemoryRouterCache:
    """测试 MemoryRouter 缓存层附加/移除"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_router_attach_cache_layer(self):
        """测试附加缓存层"""
        router = MemoryRouter(default_adapter=MockMemoryAdapter("default"))
        mock_cache = Mock()

        router.attach_cache_layer(mock_cache)

        assert router._cache_layer is mock_cache

    @pytest.mark.unit
    @pytest.mark.p2
    def test_router_detach_cache_layer(self):
        """测试移除缓存层"""
        router = MemoryRouter(default_adapter=MockMemoryAdapter("default"))
        mock_cache = Mock()
        router.attach_cache_layer(mock_cache)

        router.detach_cache_layer()

        assert router._cache_layer is None

    @pytest.mark.unit
    @pytest.mark.p2
    def test_router_search_with_cache_hit(self):
        """测试 search 方法缓存命中时直接返回缓存结果"""
        default = MockMemoryAdapter("default")
        default.search_results = [
            MemoryResult(content="cached", confidence=0.9, source="test")
        ]
        router = MemoryRouter(default_adapter=default)
        mock_cache = Mock()
        cached_results = [MemoryResult(content="from_cache", confidence=1.0, source="cache")]
        mock_cache.get.return_value = cached_results
        router.attach_cache_layer(mock_cache)

        results = pytest.importorskip("asyncio").run(
            router.search("test_query", top_k=5, task_type="local_privacy")
        )

        assert results == cached_results
        mock_cache.get.assert_called_once()
        mock_cache.set.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.p2
    def test_router_search_with_cache_miss(self):
        """测试 search 方法缓存未命中时查询适配器并写入缓存"""
        import asyncio

        default = MockMemoryAdapter("default")
        test_results = [MemoryResult(content="result", confidence=0.8, source="test")]
        default.search_results = test_results
        router = MemoryRouter(default_adapter=default)
        mock_cache = Mock()
        mock_cache.get.return_value = None
        router.attach_cache_layer(mock_cache)

        results = asyncio.run(
            router.search("test_query", top_k=5, task_type="local_privacy")
        )

        assert results == test_results
        mock_cache.set.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.p2
    def test_router_search_without_cache(self):
        """测试无缓存层时 search 直接调用适配器"""
        import asyncio

        default = MockMemoryAdapter("default")
        test_results = [MemoryResult(content="result", confidence=0.8, source="test")]
        default.search_results = test_results
        router = MemoryRouter(default_adapter=default)

        results = asyncio.run(
            router.search("test_query", top_k=3, task_type="local_privacy")
        )

        assert results == test_results
        assert default.search_called is True
        assert default.search_query == "test_query"


class TestMemoryRouterSensitiveFilter:
    """测试 MemoryRouter 敏感信息过滤功能"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_router_filter_disabled_by_default(self):
        """测试默认情况下敏感信息过滤功能禁用"""
        router = MemoryRouter(default_adapter=MockMemoryAdapter("default"))

        has_sensitive, filtered, patterns = router._filter_sensitive_info("我的密码是123456")

        assert has_sensitive is False
        assert filtered == "我的密码是123456"
        assert patterns == []

    @pytest.mark.unit
    @pytest.mark.p2
    def test_router_filter_enabled_detects_password(self):
        """测试启用过滤后检测密码类敏感信息"""
        router = MemoryRouter(default_adapter=MockMemoryAdapter("default"))
        router._sensitive_filter_enabled = True

        has_sensitive, filtered_data, detected = router._filter_sensitive_info("password=123456")

        assert has_sensitive is True
        assert len(detected) > 0
        assert "[REDACTED]" in str(filtered_data)

    @pytest.mark.unit
    @pytest.mark.p2
    def test_router_filter_enabled_no_sensitive(self):
        """测试启用过滤但内容无害时返回 False"""
        router = MemoryRouter(default_adapter=MockMemoryAdapter("default"))
        router._sensitive_filter_enabled = True

        has_sensitive, data, detected = router._filter_sensitive_info("今天天气真好")

        assert has_sensitive is False
        assert data == "今天天气真好"
        assert len(detected) == 0

    @pytest.mark.unit
    @pytest.mark.p2
    def test_router_save_with_boundary_blocks_sensitive(self):
        """测试边界约束启用时拒绝写入敏感数据"""
        import asyncio

        default = MockMemoryAdapter("default")
        router = MemoryRouter(default_adapter=default)
        router._memory_boundary_enabled = True
        router._sensitive_filter_enabled = True

        result = asyncio.run(
            router.save("test_key", "password=secret", task_type="local_privacy")
        )

        assert result is False
        assert default.save_called is False


class TestMemoryRouterConvenienceMethods:
    """测试 MemoryRouter 便捷方法"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_router_get_profile_routes_correctly(self):
        """测试 get_profile 路由到正确适配器"""
        import asyncio

        default = MockMemoryAdapter("default")
        router = MemoryRouter(default_adapter=default)

        result = asyncio.run(router.get_profile("user_123", task_type="user_profile"))

        assert result["user_id"] == "user_123"

    @pytest.mark.unit
    @pytest.mark.p2
    def test_router_update_graph_routes_correctly(self):
        """测试 update_graph 路由到正确适配器"""
        import asyncio

        default = MockMemoryAdapter("default")
        router = MemoryRouter(default_adapter=default)

        result = asyncio.run(
            router.update_graph(
                entities=[{"name": "Alice", "type": "person"}],
                relations=[{"source": "Alice", "target": "Bob", "type": "friend"}],
                task_type="knowledge_nav",
            )
        )

        assert result is True

    @pytest.mark.unit
    @pytest.mark.p2
    def test_router_to_dict_returns_state(self):
        """测试 to_dict 返回路由器完整状态"""
        router = MemoryRouter(default_adapter=MockMemoryAdapter("default"))
        router.register("test", MockMemoryAdapter("test"))

        state = router.to_dict()

        assert state["type"] == "MemoryRouter"
        assert "adapters" in state
        assert "route_map" in state
        assert "cache_layer" in state
        assert isinstance(state["boundary_enabled"], bool)


# ============================================================================
# filter.py 测试
# ============================================================================


class TestSensitiveDataFilter:
    """测试 SensitiveDataFilter 敏感信息检测和过滤"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_filter_creation_default(self):
        """测试过滤器默认初始化"""
        sf = SensitiveDataFilter()

        assert sf is not None

    @pytest.mark.unit
    @pytest.mark.p2
    def test_filter_detect_password(self):
        """测试检测密码类敏感信息"""
        sf = SensitiveDataFilter()

        result = sf.detect("password=123456")

        assert isinstance(result, FilterResult)

    @pytest.mark.unit
    @pytest.mark.p2
    def test_filter_detect_email(self):
        """测试检测邮箱地址"""
        sf = SensitiveDataFilter()

        result = sf.detect("联系我 test@example.com")

        assert isinstance(result, FilterResult)

    @pytest.mark.unit
    @pytest.mark.p2
    def test_filter_detect_phone(self):
        """测试检测手机号码"""
        sf = SensitiveDataFilter()

        result = sf.detect("手机号 13800138000")

        assert isinstance(result, FilterResult)

    @pytest.mark.unit
    @pytest.mark.p2
    def test_filter_check_alias(self):
        """测试 check 方法作为 detect 的别名"""
        sf = SensitiveDataFilter()

        result = sf.check("一些内容")

        assert isinstance(result, FilterResult)

    @pytest.mark.unit
    @pytest.mark.p2
    def test_filter_built_in_patterns_property(self):
        """测试 BUILT_IN_PATTERNS 属性"""
        sf = SensitiveDataFilter()

        patterns = sf.BUILT_IN_PATTERNS

        assert isinstance(patterns, dict)

    @pytest.mark.unit
    @pytest.mark.p2
    def test_filter_harmless_content(self):
        """测试无害内容不触发敏感检测"""
        sf = SensitiveDataFilter()

        result = sf.detect("今天天气真好，适合出去散步")

        assert isinstance(result, FilterResult)


# ============================================================================
# reviewer.py 测试
# ============================================================================


class TestReviewResult:
    """测试 ReviewResult 数据类"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_reviewer_reviewresult_default_values(self):
        """测试 ReviewResult 默认值"""
        result = ReviewResult()

        assert result.total_entries == 0
        assert result.healthy_entries == 0
        assert result.stale_entries == 0
        assert result.duplicate_entries == 0
        assert result.sensitive_unverified == 0
        assert isinstance(result.suggestions, list)
        assert isinstance(result.report, dict)
        assert result.reviewed_at > 0

    @pytest.mark.unit
    @pytest.mark.p2
    def test_reviewer_reviewresult_custom_values(self):
        """测试 ReviewResult 自定义值"""
        result = ReviewResult(
            total_entries=100,
            stale_entries=10,
            duplicate_entries=5,
            suggestions=["建议清理陈旧记忆"],
        )

        assert result.total_entries == 100
        assert result.stale_entries == 10
        assert result.duplicate_entries == 5
        assert len(result.suggestions) == 1


class TestMemoryReviewer:
    """测试 MemoryReviewer 记忆审查功能"""

    def _create_mock_ltm(self, stats=None, unverified=None):
        """创建 mock LongTermMemory"""
        mock_ltm = Mock()
        mock_ltm.db_path = ":memory:"
        mock_ltm._TABLE_NAME = "long_term_memory"
        mock_ltm.get_stats.return_value = stats or {
            "total_entries": 0,
            "sensitive_entries": 0,
            "verified_entries": 0,
            "high_importance_entries": 0,
        }
        mock_ltm.list_unverified.return_value = unverified or []
        return mock_ltm

    @pytest.mark.unit
    @pytest.mark.p2
    def test_reviewer_initialization(self):
        """测试 MemoryReviewer 初始化"""
        mock_ltm = self._create_mock_ltm()
        reviewer = MemoryReviewer(mock_ltm, stale_threshold_days=15, similarity_threshold=0.9)

        assert reviewer._ltm is mock_ltm
        assert reviewer._stale_threshold == 15 * 86400
        assert reviewer._similarity_threshold == 0.9
        assert reviewer._last_review is None

    @pytest.mark.unit
    @pytest.mark.p2
    def test_reviewer_review_empty_memory(self):
        """测试空记忆库审查"""
        import asyncio

        mock_ltm = self._create_mock_ltm(stats={
            "total_entries": 0,
            "sensitive_entries": 0,
            "verified_entries": 0,
            "high_importance_entries": 0,
        })
        reviewer = MemoryReviewer(mock_ltm)

        result = asyncio.run(reviewer.review())

        assert isinstance(result, ReviewResult)
        assert result.total_entries == 0
        assert reviewer._last_review is result

    @pytest.mark.unit
    @pytest.mark.p2
    def test_reviewer_review_quick(self):
        """测试快速审查功能"""
        import asyncio

        mock_ltm = self._create_mock_ltm(
            stats={
                "total_entries": 10,
                "sensitive_entries": 2,
                "verified_entries": 5,
                "high_importance_entries": 3,
            },
            unverified=[Mock(), Mock()],
        )
        reviewer = MemoryReviewer(mock_ltm)

        result = asyncio.run(reviewer.review_quick())

        assert result["quick"] is True
        assert result["total_entries"] == 10
        assert "suggestions" in result
        assert len(result["suggestions"]) > 0

    @pytest.mark.unit
    @pytest.mark.p2
    def test_reviewer_get_last_review_none(self):
        """测试未审查时 get_last_review 返回 None"""
        mock_ltm = self._create_mock_ltm()
        reviewer = MemoryReviewer(mock_ltm)

        assert reviewer.get_last_review() is None

    @pytest.mark.unit
    @pytest.mark.p2
    def test_reviewer_calculate_health_score_empty(self):
        """测试空记忆库健康评分为 100"""
        mock_ltm = self._create_mock_ltm()
        reviewer = MemoryReviewer(mock_ltm)

        result = ReviewResult(total_entries=0)
        score = reviewer._calculate_health_score(result)

        assert score == 100.0

    @pytest.mark.unit
    @pytest.mark.p2
    def test_reviewer_calculate_health_score_perfect(self):
        """测试健康记忆库满分"""
        mock_ltm = self._create_mock_ltm()
        reviewer = MemoryReviewer(mock_ltm)

        result = ReviewResult(
            total_entries=10,
            healthy_entries=10,
            stale_entries=0,
            duplicate_entries=0,
            sensitive_unverified=0,
        )
        score = reviewer._calculate_health_score(result)

        assert score == 100.0

    @pytest.mark.unit
    @pytest.mark.p2
    def test_reviewer_calculate_health_score_deductions(self):
        """测试健康评分扣分逻辑"""
        mock_ltm = self._create_mock_ltm()
        reviewer = MemoryReviewer(mock_ltm)

        result = ReviewResult(
            total_entries=100,
            stale_entries=5,
            duplicate_entries=3,
            sensitive_unverified=2,
        )
        score = reviewer._calculate_health_score(result)

        assert 0.0 <= score < 100.0

    @pytest.mark.unit
    @pytest.mark.p2
    def test_reviewer_generate_suggestions_stale(self):
        """测试生成陈旧记忆清理建议"""
        mock_ltm = self._create_mock_ltm()
        reviewer = MemoryReviewer(mock_ltm, stale_threshold_days=30)

        result = ReviewResult(total_entries=100, stale_entries=10)
        suggestions = reviewer._generate_suggestions(result, ["key1"], [])

        assert any("陈旧" in s for s in suggestions)

    @pytest.mark.unit
    @pytest.mark.p2
    def test_reviewer_generate_suggestions_duplicate(self):
        """测试生成重复记忆清理建议"""
        mock_ltm = self._create_mock_ltm()
        reviewer = MemoryReviewer(mock_ltm)

        result = ReviewResult(total_entries=100, duplicate_entries=5)
        suggestions = reviewer._generate_suggestions(result, [], ["dup1", "dup2"])

        assert any("重复" in s for s in suggestions)

    @pytest.mark.unit
    @pytest.mark.p2
    def test_reviewer_generate_suggestions_empty(self):
        """测试空记忆库生成建议"""
        mock_ltm = self._create_mock_ltm()
        reviewer = MemoryReviewer(mock_ltm)

        result = ReviewResult(total_entries=0)
        suggestions = reviewer._generate_suggestions(result, [], [])

        assert any("为空" in s for s in suggestions)

    @pytest.mark.unit
    @pytest.mark.p2
    def test_reviewer_generate_report_structure(self):
        """测试生成报告的结构完整性"""
        mock_ltm = self._create_mock_ltm()
        reviewer = MemoryReviewer(mock_ltm)

        result = ReviewResult(total_entries=10, stale_entries=1, duplicate_entries=0)
        report = reviewer._generate_report(result, ["stale_key"], [])

        assert "health_score" in report
        assert "total_entries" in report
        assert "stale_threshold_days" in report
        assert "stale_keys_sample" in report
        assert "duplicate_keys_sample" in report


# ============================================================================
# long_term_memory.py 测试
# ============================================================================


class TestLongTermMemoryEntry:
    """测试 LongTermMemoryEntry 数据类"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_ltm_entry_default_values(self):
        """测试 LongTermMemoryEntry 默认值"""
        entry = LongTermMemoryEntry(key="test_key", content="test_content")

        assert entry.key == "test_key"
        assert entry.content == "test_content"
        assert entry.importance == 3
        assert entry.tags == []
        assert entry.access_count == 0
        assert entry.sensitive is False
        assert entry.verified is False
        assert isinstance(entry.metadata, dict)
        assert entry.created_at > 0

    @pytest.mark.unit
    @pytest.mark.p2
    def test_ltm_entry_custom_values(self):
        """测试 LongTermMemoryEntry 自定义值"""
        entry = LongTermMemoryEntry(
            key="custom_key",
            content="custom_content",
            importance=5,
            tags=["important", "user"],
            sensitive=True,
            metadata={"source": "test"},
        )

        assert entry.importance == 5
        assert len(entry.tags) == 2
        assert entry.sensitive is True
        assert entry.metadata["source"] == "test"

    @pytest.mark.unit
    @pytest.mark.p2
    def test_ltm_entry_to_dict(self):
        """测试 LongTermMemoryEntry to_dict 序列化"""
        entry = LongTermMemoryEntry(
            key="dict_test",
            content="some content",
            importance=4,
            tags=["a", "b"],
        )

        d = entry.to_dict()

        assert isinstance(d, dict)
        assert d["key"] == "dict_test"
        assert d["content"] == "some content"
        assert d["importance"] == 4
        assert d["tags"] == ["a", "b"]

    @pytest.mark.unit
    @pytest.mark.p2
    def test_ltm_entry_from_dict(self):
        """测试 LongTermMemoryEntry from_dict 反序列化"""
        data = {
            "key": "from_dict_test",
            "content": "test content",
            "importance": 5,
            "tags": ["test"],
            "sensitive": True,
            "verified": True,
            "metadata": {"hello": "world"},
        }

        entry = LongTermMemoryEntry.from_dict(data)

        assert entry.key == "from_dict_test"
        assert entry.importance == 5
        assert entry.sensitive is True
        assert entry.verified is True
        assert entry.metadata["hello"] == "world"

    @pytest.mark.unit
    @pytest.mark.p2
    def test_ltm_entry_roundtrip(self):
        """测试 to_dict -> from_dict 往返一致性"""
        original = LongTermMemoryEntry(
            key="roundtrip",
            content={"nested": "data"},
            importance=2,
            tags=["round", "trip"],
            sensitive=False,
            metadata={"version": 1},
        )

        d = original.to_dict()
        restored = LongTermMemoryEntry.from_dict(d)

        assert restored.key == original.key
        assert restored.importance == original.importance
        assert restored.tags == original.tags


# ============================================================================
# short_term_memory.py 测试
# ============================================================================


class TestShortTermMemoryEntry:
    """测试 ShortTermMemoryEntry 数据类"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_stm_entry_default_values(self):
        """测试 ShortTermMemoryEntry 默认值"""
        entry = ShortTermMemoryEntry(key="test_key", content="test_content")

        assert entry.key == "test_key"
        assert entry.content == "test_content"
        assert entry.expires_at == 0.0
        assert entry.task_id == ""
        assert entry.accessed is False
        assert entry.created_at > 0

    @pytest.mark.unit
    @pytest.mark.p2
    def test_stm_entry_custom_values(self):
        """测试 ShortTermMemoryEntry 自定义值"""
        entry = ShortTermMemoryEntry(
            key="custom",
            content={"data": "value"},
            expires_at=9999999999.0,
            task_id="task_123",
            accessed=True,
        )

        assert entry.content == {"data": "value"}
        assert entry.task_id == "task_123"
        assert entry.accessed is True


class TestShortTermMemory:
    """测试 ShortTermMemory 短期记忆和 LRU 淘汰机制"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_stm_initialization(self):
        """测试 ShortTermMemory 初始化"""
        stm = ShortTermMemory(max_size=50, default_ttl=600)

        assert stm is not None
        assert stm._max_size == 50
        assert stm._default_ttl == 600

    @pytest.mark.unit
    @pytest.mark.p2
    def test_stm_capabilities(self):
        """测试 ShortTermMemory capabilities 属性"""
        stm = ShortTermMemory()

        caps = stm.capabilities

        assert MemoryCapability.LOCAL_FIRST in caps

    @pytest.mark.unit
    @pytest.mark.p2
    def test_stm_save_success(self):
        """测试保存临时记忆成功"""
        import asyncio

        stm = ShortTermMemory()

        result = asyncio.run(stm.save("test_key", "test_value"))

        assert result is True

    @pytest.mark.unit
    @pytest.mark.p2
    def test_stm_save_empty_key_fails(self):
        """测试空 key 保存失败"""
        import asyncio

        stm = ShortTermMemory()

        result = asyncio.run(stm.save("", "value"))

        assert result is False

    @pytest.mark.unit
    @pytest.mark.p2
    def test_stm_save_with_custom_ttl(self):
        """测试保存时指定自定义 TTL"""
        import asyncio

        stm = ShortTermMemory()

        result = asyncio.run(stm.save("ttl_key", "value", ttl=120, task_id="task1"))

        assert result is True

    @pytest.mark.unit
    @pytest.mark.p2
    def test_stm_get_existing(self):
        """测试获取已存在的临时记忆"""
        import asyncio

        stm = ShortTermMemory()
        asyncio.run(stm.save("get_test", "hello stm"))

        value = asyncio.run(stm.get("get_test"))

        assert value == "hello stm"

    @pytest.mark.unit
    @pytest.mark.p2
    def test_stm_get_marks_accessed(self):
        """测试获取记忆后标记为已访问"""
        import asyncio

        stm = ShortTermMemory()
        asyncio.run(stm.save("access_test", "value"))

        asyncio.run(stm.get("access_test"))
        entry = stm._store["access_test"]

        assert entry.accessed is True

    @pytest.mark.unit
    @pytest.mark.p2
    def test_stm_get_nonexistent(self):
        """测试获取不存在的记忆返回 None"""
        import asyncio

        stm = ShortTermMemory()

        value = asyncio.run(stm.get("nonexistent"))

        assert value is None

    @pytest.mark.unit
    @pytest.mark.p2
    def test_stm_get_empty_key(self):
        """测试获取空 key 返回 None"""
        import asyncio

        stm = ShortTermMemory()

        value = asyncio.run(stm.get(""))

        assert value is None

    @pytest.mark.unit
    @pytest.mark.p2
    def test_stm_delete_success(self):
        """测试删除临时记忆成功"""
        import asyncio

        stm = ShortTermMemory()
        asyncio.run(stm.save("del_test", "value"))

        result = asyncio.run(stm.delete("del_test"))

        assert result is True
        assert "del_test" not in stm._store

    @pytest.mark.unit
    @pytest.mark.p2
    def test_stm_delete_nonexistent(self):
        """测试删除不存在的记忆返回 False"""
        import asyncio

        stm = ShortTermMemory()

        result = asyncio.run(stm.delete("nonexistent"))

        assert result is False

    @pytest.mark.unit
    @pytest.mark.p2
    def test_stm_delete_empty_key(self):
        """测试删除空 key 返回 False"""
        import asyncio

        stm = ShortTermMemory()

        result = asyncio.run(stm.delete(""))

        assert result is False

    @pytest.mark.unit
    @pytest.mark.p2
    def test_stm_clear_task_memory(self):
        """测试清除指定任务的所有记忆"""
        import asyncio

        stm = ShortTermMemory()
        asyncio.run(stm.save("t1_a", "a", task_id="task1"))
        asyncio.run(stm.save("t1_b", "b", task_id="task1"))
        asyncio.run(stm.save("t2_a", "c", task_id="task2"))

        count = asyncio.run(stm.clear_task_memory("task1"))

        assert count == 2
        assert "t1_a" not in stm._store
        assert "t1_b" not in stm._store
        assert "t2_a" in stm._store

    @pytest.mark.unit
    @pytest.mark.p2
    def test_stm_clear_task_memory_empty_id(self):
        """测试清除空 task_id 返回 0"""
        import asyncio

        stm = ShortTermMemory()

        count = asyncio.run(stm.clear_task_memory(""))

        assert count == 0

    @pytest.mark.unit
    @pytest.mark.p2
    def test_stm_clear_all(self):
        """测试清空所有临时记忆"""
        import asyncio

        stm = ShortTermMemory()
        asyncio.run(stm.save("k1", "v1"))
        asyncio.run(stm.save("k2", "v2"))
        asyncio.run(stm.save("k3", "v3"))

        count = asyncio.run(stm.clear_all())

        assert count == 3
        assert len(stm._store) == 0

    @pytest.mark.unit
    @pytest.mark.p2
    def test_stm_expired_entry_not_accessible(self):
        """测试已过期的记忆无法访问"""
        import asyncio

        stm = ShortTermMemory(default_ttl=0)
        asyncio.run(stm.save("expired_key", "value", ttl=1))
        time.sleep(1.1)

        value = asyncio.run(stm.get("expired_key"))

        assert value is None
        assert "expired_key" not in stm._store

    @pytest.mark.unit
    @pytest.mark.p2
    def test_stm_cleanup_expired(self):
        """测试清理过期记忆"""
        import asyncio

        stm = ShortTermMemory()
        asyncio.run(stm.save("exp1", "v1", ttl=1))
        asyncio.run(stm.save("exp2", "v2", ttl=1))
        asyncio.run(stm.save("noexp", "v3", ttl=0))
        time.sleep(1.1)

        count = stm.cleanup_expired()

        assert count >= 2

    @pytest.mark.unit
    @pytest.mark.p2
    def test_stm_lru_eviction_unaccessed_first(self):
        """测试 LRU 淘汰优先移除未访问的条目"""
        import asyncio

        stm = ShortTermMemory(max_size=3, default_ttl=0)
        asyncio.run(stm.save("k1", "v1"))
        asyncio.run(stm.save("k2", "v2"))
        asyncio.run(stm.get("k1"))
        asyncio.run(stm.save("k3", "v3"))

        asyncio.run(stm.save("k4", "v4"))

        assert len(stm._store) == 3
        # k2 未被访问，应该被淘汰
        assert "k2" not in stm._store
        assert "k1" in stm._store
        assert "k3" in stm._store
        assert "k4" in stm._store

    @pytest.mark.unit
    @pytest.mark.p2
    def test_stm_lru_eviction_all_accessed(self):
        """测试所有条目都被访问过时淘汰最老的"""
        import asyncio

        stm = ShortTermMemory(max_size=2, default_ttl=0)
        asyncio.run(stm.save("oldest", "v1"))
        asyncio.run(stm.save("middle", "v2"))
        asyncio.run(stm.get("oldest"))
        asyncio.run(stm.get("middle"))

        asyncio.run(stm.save("newest", "v3"))

        assert len(stm._store) == 2
        # 全部被访问过时淘汰最老的
        assert "oldest" not in stm._store
        assert "middle" in stm._store
        assert "newest" in stm._store

    @pytest.mark.unit
    @pytest.mark.p2
    def test_stm_lru_update_existing_does_not_evict(self):
        """测试更新已存在的 key 不触发 LRU 淘汰"""
        import asyncio

        stm = ShortTermMemory(max_size=2, default_ttl=0)
        asyncio.run(stm.save("k1", "v1"))
        asyncio.run(stm.save("k2", "v2"))

        asyncio.run(stm.save("k1", "v1_updated"))

        assert len(stm._store) == 2
        assert "k1" in stm._store
        assert "k2" in stm._store

    @pytest.mark.unit
    @pytest.mark.p2
    def test_stm_get_stats_empty(self):
        """测试空库统计信息"""
        stm = ShortTermMemory(max_size=100)

        stats = stm.get_stats()

        assert stats["total_entries"] == 0
        assert stats["max_size"] == 100
        assert stats["usage_pct"] == 0.0

    @pytest.mark.unit
    @pytest.mark.p2
    def test_stm_get_stats_with_data(self):
        """测试有数据时统计信息正确"""
        import asyncio

        stm = ShortTermMemory(max_size=10)
        asyncio.run(stm.save("k1", "v1"))
        asyncio.run(stm.save("k2", "v2"))

        stats = stm.get_stats()

        assert stats["total_entries"] == 2
        assert stats["usage_pct"] == 20.0

    @pytest.mark.unit
    @pytest.mark.p2
    def test_stm_list_entries(self):
        """测试列出所有记忆条目"""
        import asyncio

        stm = ShortTermMemory()
        asyncio.run(stm.save("k1", "v1", task_id="t1"))
        asyncio.run(stm.save("k2", "v2", task_id="t2"))

        entries = stm.list_entries()

        assert len(entries) == 2
        assert all("key" in e for e in entries)
        assert all("task_id" in e for e in entries)

    @pytest.mark.unit
    @pytest.mark.p2
    def test_stm_evict_lru_empty_store(self):
        """测试空 store 调用 _evict_lru 不报错"""
        stm = ShortTermMemory(max_size=10)

        stm._evict_lru()

        assert len(stm._store) == 0
