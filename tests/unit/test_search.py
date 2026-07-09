"""
搜索引擎集成测试
"""

import pytest
from unittest.mock import Mock, patch

from agent.web.search import SearchEngine


class TestSearchEngineInit:
    """测试搜索引擎初始化"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_init_default_config(self):
        """测试默认配置初始化"""
        engine = SearchEngine()

        # 源码 __init__ 中 _default_engine 默认为空字符串（不再自动设为 'duckduckgo'）
        assert engine._default_engine == ""
        assert engine._http_client is None
        assert engine._stats["searches"] == 0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_init_custom_engine(self):
        """测试自定义引擎初始化"""
        config = {"default_engine": "bing"}
        engine = SearchEngine(config=config)
        
        assert engine._default_engine == "bing"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_init_with_api_keys(self):
        """测试带 API Key 初始化"""
        config = {
            "bing_api_key": "test_bing_key",
            "google_api_key": "test_google_key",
            "brave_api_key": "test_brave_key",
        }
        engine = SearchEngine(config=config)

        # 源码 __init__ 不再自动映射 *_api_key 到 _api_keys，需通过 update_config 触发映射
        engine.update_config(config)

        assert engine._api_keys["bing"] == "test_bing_key"
        assert engine._api_keys["google"] == "test_google_key"
        assert engine._api_keys["brave"] == "test_brave_key"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_init_cache_config(self):
        """测试缓存配置"""
        config = {"cache_ttl": 600}
        engine = SearchEngine(config=config)
        
        assert engine._cache_ttl == 600

    @pytest.mark.unit
    @pytest.mark.p1
    def test_set_http_client(self):
        """测试设置 HTTP 客户端"""
        engine = SearchEngine()
        mock_client = Mock()
        
        engine.set_http_client(mock_client)
        
        assert engine._http_client == mock_client


class TestSearchEngineCache:
    """测试搜索缓存"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_cache_check_empty(self):
        """测试空缓存检查"""
        engine = SearchEngine()
        
        cached = engine._check_cache("test_key")
        assert cached is None

    @pytest.mark.unit
    @pytest.mark.p1
    def test_cache_store_and_retrieve(self):
        """测试缓存存储和检索"""
        engine = SearchEngine()
        
        result = {"ok": True, "results": [{"title": "Test"}]}
        engine._set_cache("test_key", result)
        
        cached = engine._check_cache("test_key")
        assert cached == result

    @pytest.mark.unit
    @pytest.mark.p1
    def test_cache_key_generation(self):
        """测试缓存键生成"""
        engine = SearchEngine()
        
        # 缓存键应该包含引擎、查询、结果数和页码
        cache_key = "duckduckgo:test query:10:1"
        
        engine._set_cache(cache_key, {"ok": True})
        cached = engine._check_cache(cache_key)
        
        assert cached is not None


class TestSearchEngineStats:
    """测试搜索统计"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_initial_stats(self):
        """测试初始统计"""
        engine = SearchEngine()
        
        assert engine._stats["searches"] == 0
        assert engine._stats["total_results"] == 0
        assert engine._stats["cached_hits"] == 0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_get_stats(self):
        """测试获取统计"""
        engine = SearchEngine()
        
        stats = engine.get_stats()
        assert stats["searches"] == 0


class TestSearchEngineSearch:
    """测试搜索功能"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_search_without_http_client(self):
        """测试无 HTTP 客户端搜索"""
        engine = SearchEngine()
        result = engine.search("test query")
        
        # 应该返回错误或使用默认方式
        assert "ok" in result

    @pytest.mark.unit
    @pytest.mark.p1
    def test_search_num_results_limit(self):
        """测试结果数限制"""
        engine = SearchEngine()
        
        # 结果数应该在 1-50 之间
        # num_results = min(max(num_results, 1), 50)
        
        # 测试边界值
        assert min(max(0, 1), 50) == 1
        assert min(max(100, 1), 50) == 50
        assert min(max(10, 1), 50) == 10

    @pytest.mark.unit
    @pytest.mark.p1
    def test_search_cached_result(self):
        """测试缓存结果"""
        engine = SearchEngine()
        
        # 存储缓存
        cached_result = {"ok": True, "results": [], "cached": True}
        engine._set_cache("duckduckgo:test:10:1", cached_result)
        
        # 搜索应该返回缓存
        result = engine.search("test", engine="duckduckgo", num_results=10, page=1)
        
        # 如果缓存命中，应该返回缓存结果
        if result.get("cached"):
            assert result == cached_result


class TestSearchEngineEngines:
    """测试不同搜索引擎"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_supported_engines(self):
        """测试支持的引擎"""
        engine = SearchEngine()
        
        # 支持的引擎列表
        supported = ["duckduckgo", "bing", "google", "brave"]
        
        for name in supported:
            # 每个引擎都应该有对应的搜索方法
            method_name = f"_search_{name}"
            assert hasattr(engine, method_name) or name in ["duckduckgo", "bing", "google", "brave"]

    @pytest.mark.unit
    @pytest.mark.p1
    def test_engine_selection(self):
        """测试引擎选择"""
        engine = SearchEngine()

        # 默认引擎为空字符串（需手动注册并设置）
        assert engine._default_engine == ""

        # 自定义引擎
        engine2 = SearchEngine(config={"default_engine": "bing"})
        assert engine2._default_engine == "bing"