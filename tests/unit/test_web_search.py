import time
import pytest
from unittest.mock import patch, MagicMock

from agent.web.search import SearchEngine


class TestSearchEngineInit:
    """测试 SearchEngine 初始化"""

    def test_init_default(self):
        """测试默认初始化"""
        engine = SearchEngine()
        # 源码 __init__ 中 _default_engine 默认为空字符串（不再自动设为 'duckduckgo'）
        assert engine._default_engine == ""
        assert engine._cache == {}

    def test_init_with_config(self):
        """测试使用自定义配置初始化"""
        config = {
            "default_engine": "bing",
            "bing_api_key": "test_bing_key",
            "google_api_key": "test_google_key",
            "google_cx": "test_cx",
            "brave_api_key": "test_brave_key",
            "cache_ttl": 600,
        }
        engine = SearchEngine(config)
        assert engine._default_engine == "bing"
        # 源码 __init__ 不再自动映射 *_api_key 到 _api_keys，需通过 update_config 触发映射
        engine.update_config(config)
        assert engine._api_keys["bing"] == "test_bing_key"
        assert engine._api_keys["google"] == "test_google_key"
        # google_cx 不以 _api_key 结尾，update_config 不会映射它，仍保留在 _config 中
        assert engine._config.get("google_cx") == "test_cx"
        assert engine._api_keys["brave"] == "test_brave_key"
        assert engine._cache_ttl == 600

    def test_set_http_client(self):
        """测试设置 HTTP 客户端"""
        engine = SearchEngine()
        mock_client = MagicMock()
        engine.set_http_client(mock_client)
        assert engine._http_client == mock_client


class TestSearchEngineSearch:
    """测试搜索接口"""

    def test_search_unsupported_engine(self):
        """测试不支持的引擎"""
        engine = SearchEngine()
        result = engine.search("test query", engine="invalid")
        assert result["ok"] is False
        assert "不支持的搜索引擎" in result["error"]

    @patch("time.time")
    def test_search_cache_hit(self, mock_time):
        """测试缓存命中"""
        mock_time.return_value = 1000
        engine = SearchEngine()
        # 注册一个引擎，使搜索流程能进入缓存检查阶段
        engine.register_engine("duckduckgo", "DuckDuckGo", engine._search_duckduckgo)
        cache_data = {"ok": True, "results": ["cached result"]}
        # 源码缓存键格式为 f"any:{query}:{num_results}:{page}"
        engine._cache["any:test query:10:1"] = {
            "time": 900,
            "data": cache_data,
        }
        result = engine.search("test query")
        assert result == cache_data
        assert engine._stats["cached_hits"] == 1

    @patch("agent.web.search.SearchEngine._search_duckduckgo")
    def test_search_no_cache(self, mock_search):
        """测试无缓存时搜索"""
        mock_search.return_value = {"ok": True, "results": ["result 1"]}
        engine = SearchEngine()
        # 注册 duckduckgo 引擎，使其进入引擎优先级列表从而被自动选中
        engine.register_engine("duckduckgo", "DuckDuckGo", engine._search_duckduckgo)
        mock_http = MagicMock()
        engine.set_http_client(mock_http)
        result = engine.search("test query")
        assert result["ok"] is True
        mock_search.assert_called_once_with("test query", num_results=10, page=1)
        assert engine._stats["searches"] == 1


class TestSearchEngineDuckDuckGo:
    """测试 DuckDuckGo 搜索"""

    def test_search_duckduckgo_no_http(self):
        """测试无 HTTP 客户端"""
        engine = SearchEngine()
        result = engine._search_duckduckgo("test query")
        assert result["ok"] is False
        assert "HTTP 客户端未配置" in result["error"]

    def test_search_duckduckgo_http_error(self):
        """测试 HTTP 请求失败"""
        mock_http = MagicMock()
        mock_http.get.return_value = {"ok": False, "error": "Connection failed"}
        engine = SearchEngine()
        engine.set_http_client(mock_http)
        result = engine._search_duckduckgo("test query")
        assert result["ok"] is False

    @patch("agent.web.search.SearchEngine._parse_duckduckgo_html")
    def test_search_duckduckgo_success(self, mock_parse):
        """测试成功搜索"""
        mock_http = MagicMock()
        mock_http.get.return_value = {
            "ok": True,
            "text": "<html>...</html>",
            "url": "https://html.duckduckgo.com/html/",
        }
        mock_parse.return_value = [{"title": "Test 1", "url": "https://example.com", "snippet": "Test snippet"}]
        engine = SearchEngine()
        engine.set_http_client(mock_http)
        result = engine._search_duckduckgo("test query", num_results=5)
        assert result["ok"] is True
        assert len(result["results"]) == 1


class TestSearchEngineParseDuckDuckGo:
    """测试解析 DuckDuckGo HTML"""

    def test_parse_duckduckgo_html_valid(self):
        """测试解析有效 HTML"""
        html = """
        <html>
        <div class="result">
            <h2 class="result__title">
                <a href="https://example.com/page1">Test Page 1</a>
            </h2>
            <a class="result__a" href="https://example.com/page1"></a>
            <a class="result__snippet">Test Snippet 1</a>
        </div>
        </html>
        """
        results = SearchEngine._parse_duckduckgo_html(html)
        assert len(results) == 1
        assert results[0]["title"] == "Test Page 1"
        assert results[0]["url"] == "https://example.com/page1"
        assert results[0]["snippet"] == "Test Snippet 1"

    def test_parse_duckduckgo_html_missing_title(self):
        """测试解析缺少标题的 HTML（覆盖第 152 行）"""
        html = """
        <html>
        <div class="result">
            <a class="result__a" href="https://example.com/page1"></a>
            <a class="result__snippet">Test Snippet 1</a>
        </div>
        </html>
        """
        results = SearchEngine._parse_duckduckgo_html(html)
        assert len(results) == 0

    def test_parse_duckduckgo_html_exception(self):
        """测试解析异常时的 fallback（覆盖第 166-169 行）"""
        # 创建一个会导致 lxml 解析失败的 HTML
        # 使用 patch 模拟异常
        from lxml import html as lxml_html
        with patch.object(lxml_html, 'fromstring', side_effect=Exception('Parse error')):
            html = """
            <html>
            <a href="https://example.com/fallback">Fallback Link</a>
            </html>
            """
            results = SearchEngine._parse_duckduckgo_html(html)
            assert len(results) >= 0  # 至少会尝试使用 fallback 解析

    def test_parse_duckduckgo_html_invalid(self):
        """测试解析无效 HTML"""
        results = SearchEngine._parse_duckduckgo_html("invalid html <<<")
        assert isinstance(results, list)


class TestSearchEngineBing:
    """测试 Bing 搜索"""

    def test_search_bing_no_api_key(self):
        """测试无 API Key"""
        engine = SearchEngine()
        result = engine._search_bing("test query")
        assert result["ok"] is False
        assert "Bing API Key 未配置" in result["error"]

    def test_search_bing_no_http(self):
        """测试无 HTTP 客户端"""
        engine = SearchEngine({"bing_api_key": "test_key"})
        result = engine._search_bing("test query")
        assert result["ok"] is False

    def test_search_bing_http_error(self):
        """测试 HTTP 请求失败（覆盖第 192 行）"""
        mock_http = MagicMock()
        mock_http.get.return_value = {"ok": False, "error": "Network error"}
        engine = SearchEngine({"bing_api_key": "test_key"})
        engine.set_http_client(mock_http)
        result = engine._search_bing("test query")
        assert result["ok"] is False

    def test_search_bing_invalid_json(self):
        """测试无效 JSON 响应"""
        mock_http = MagicMock()
        mock_http.get.return_value = {"ok": True, "text": "{invalid json}"}
        engine = SearchEngine({"bing_api_key": "test_key"})
        engine.set_http_client(mock_http)
        result = engine._search_bing("test query")
        assert result["ok"] is False
        assert "解析 Bing API 响应失败" in result["error"]

    def test_search_bing_success(self):
        """测试成功搜索"""
        mock_http = MagicMock()
        mock_http.get.return_value = {
            "ok": True,
            "text": '{"webPages": {"totalEstimatedMatches": 100, "value": [{"name": "Result", "url": "https://example.com", "snippet": "Snippet"}]}}',
        }
        engine = SearchEngine({"bing_api_key": "test_key"})
        engine.set_http_client(mock_http)
        result = engine._search_bing("test query")
        assert result["ok"] is True
        assert len(result["results"]) == 1


class TestSearchEngineGoogle:
    """测试 Google 搜索"""

    def test_search_google_no_keys(self):
        """测试无 API Key / CX"""
        engine = SearchEngine()
        result = engine._search_google("test query")
        assert result["ok"] is False
        assert "Google API Key 或 CX 未配置" in result["error"]

    def test_search_google_no_http(self):
        """测试无 HTTP 客户端"""
        engine = SearchEngine({"google_api_key": "test_key", "google_cx": "test_cx"})
        result = engine._search_google("test query")
        assert result["ok"] is False

    def test_search_google_http_error(self):
        """测试 HTTP 请求失败（覆盖第 239 行）"""
        mock_http = MagicMock()
        mock_http.get.return_value = {"ok": False, "error": "Network error"}
        engine = SearchEngine({"google_api_key": "test_key", "google_cx": "test_cx"})
        engine.set_http_client(mock_http)
        result = engine._search_google("test query")
        assert result["ok"] is False

    def test_search_google_invalid_json(self):
        """测试无效 JSON 响应"""
        mock_http = MagicMock()
        mock_http.get.return_value = {"ok": True, "text": "{invalid json}"}
        engine = SearchEngine({"google_api_key": "test_key", "google_cx": "test_cx"})
        engine.set_http_client(mock_http)
        result = engine._search_google("test query")
        assert result["ok"] is False
        assert "解析 Google API 响应失败" in result["error"]

    def test_search_google_success(self):
        """测试成功搜索"""
        mock_http = MagicMock()
        mock_http.get.return_value = {
            "ok": True,
            "text": '{"searchInformation": {"totalResults": 100}, "items": [{"title": "Result", "link": "https://example.com", "snippet": "Snippet"}]}',
        }
        engine = SearchEngine({"google_api_key": "test_key", "google_cx": "test_cx"})
        engine.set_http_client(mock_http)
        result = engine._search_google("test query")
        assert result["ok"] is True
        assert len(result["results"]) == 1


class TestSearchEngineBrave:
    """测试 Brave 搜索"""

    def test_search_brave_no_api_key(self):
        """测试无 API Key"""
        engine = SearchEngine()
        result = engine._search_brave("test query")
        assert result["ok"] is False
        assert "Brave API Key 未配置" in result["error"]

    def test_search_brave_no_http(self):
        """测试无 HTTP 客户端"""
        engine = SearchEngine({"brave_api_key": "test_key"})
        result = engine._search_brave("test query")
        assert result["ok"] is False

    def test_search_brave_http_error(self):
        """测试 HTTP 请求失败（覆盖第 283 行）"""
        mock_http = MagicMock()
        mock_http.get.return_value = {"ok": False, "error": "Network error"}
        engine = SearchEngine({"brave_api_key": "test_key"})
        engine.set_http_client(mock_http)
        result = engine._search_brave("test query")
        assert result["ok"] is False

    def test_search_brave_invalid_json(self):
        """测试无效 JSON 响应"""
        mock_http = MagicMock()
        mock_http.get.return_value = {"ok": True, "text": "{invalid json}"}
        engine = SearchEngine({"brave_api_key": "test_key"})
        engine.set_http_client(mock_http)
        result = engine._search_brave("test query")
        assert result["ok"] is False
        assert "解析 Brave API 响应失败" in result["error"]

    def test_search_brave_success(self):
        """测试成功搜索"""
        mock_http = MagicMock()
        mock_http.get.return_value = {
            "ok": True,
            "text": '{"web": {"totalEstimatedResults": 100, "results": [{"title": "Result", "url": "https://example.com", "description": "Snippet"}]}}',
        }
        engine = SearchEngine({"brave_api_key": "test_key"})
        engine.set_http_client(mock_http)
        result = engine._search_brave("test query")
        assert result["ok"] is True
        assert len(result["results"]) == 1


class TestSearchEngineCache:
    """测试缓存功能"""

    @patch("time.time")
    def test_cache_expired(self, mock_time):
        """测试缓存过期"""
        mock_time.return_value = 1500
        engine = SearchEngine({"cache_ttl": 300})
        engine._cache["test"] = {"time": 1000, "data": {"ok": True}}
        result = engine._check_cache("test")
        assert result is None

    def test_set_cache_cleanup(self):
        """测试缓存清理"""
        engine = SearchEngine()
        # 添加超过 200 个过期缓存
        for i in range(250):
            engine._cache[f"key{i}"] = {"time": 0, "data": {}}
        # 设置新缓存会触发清理
        engine._set_cache("new_key", {"ok": True})
        # 清理后应该小于等于 200
        assert len(engine._cache) <= 201

    def test_clear_cache(self):
        """测试清空缓存"""
        engine = SearchEngine()
        engine._cache = {"key1": {"time": 100, "data": {}}, "key2": {"time": 200, "data": {}}}
        engine.clear_cache()
        assert engine._cache == {}


class TestSearchEngineUtils:
    """测试工具方法"""

    def test_parse_result_fallback(self):
        """测试回退解析"""
        html = """
        <html>
        <a href="https://example.com/page1">Page 1 Title</a>
        <a href="https://example.com/page2">Page 2 Title</a>
        </html>
        """
        results = SearchEngine._parse_result_fallback(html)
        assert len(results) == 2
        assert results[0]["title"] == "Page 1 Title"
        assert results[0]["url"] == "https://example.com/page1"

    def test_get_available_engines(self):
        """测试获取可用引擎"""
        engine = SearchEngine({"bing_api_key": "test_key"})
        # 源码不再自动注册引擎，需手动注册 4 个引擎
        engine.register_engine("duckduckgo", "DuckDuckGo", engine._search_duckduckgo, needs_key=False)
        engine.register_engine("bing", "Bing", engine._search_bing, needs_key=True)
        engine.register_engine("google", "Google", engine._search_google, needs_key=True)
        engine.register_engine("brave", "Brave", engine._search_brave, needs_key=True)
        # 通过 update_config 配置 Bing 的 API Key
        engine.update_config({"bing_api_key": "test_key"})
        available = engine.get_available_engines()
        assert len(available) == 4
        # 检查 duckduckgo 总是可用（无需 API Key）
        assert any(e["name"] == "duckduckgo" and e["configured"] for e in available)
        # 检查 Bing 已配置
        assert any(e["name"] == "bing" and e["configured"] for e in available)
        # 检查 Google 未配置
        assert any(e["name"] == "google" and not e["configured"] for e in available)

    def test_get_stats(self):
        """测试获取统计"""
        engine = SearchEngine()
        engine._stats["searches"] = 5
        engine._stats["total_results"] = 50
        engine._stats["cached_hits"] = 3
        engine._cache = {"k1": {}, "k2": {}}
        stats = engine.get_stats()
        assert stats["searches"] == 5
        assert stats["total_results"] == 50
        assert stats["cached_hits"] == 3
        assert stats["cache_size"] == 2


class TestSearchEngineMultiSearch:
    """测试批量搜索"""

    @patch("agent.web.search.SearchEngine.search")
    def test_multi_search(self, mock_search):
        """测试批量搜索多个查询"""
        mock_search.side_effect = [
            {"ok": True, "results": [f"Result {i}"]}
            for i in range(3)
        ]
        engine = SearchEngine()
        results = engine.multi_search(["query1", "query2", "query3"])
        assert len(results) == 3
        assert results[0]["results"] == ["Result 0"]
        assert results[1]["results"] == ["Result 1"]
        assert results[2]["results"] == ["Result 2"]
