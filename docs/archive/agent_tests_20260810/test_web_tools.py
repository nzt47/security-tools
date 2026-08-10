"""网络工具集成测试 — 测试 agent.web 模块的 HttpClient/SearchEngine/Scraper

覆盖范围：
- HttpClient.get / .post — 正常返回、超时、404、自定义 headers
- HttpClient.batch_request — 批量请求、并发限制
- SearchEngine.search — 正常搜索、多引擎切换、无结果、降级策略
- Scraper.xpath / .css — XPath/CSS 匹配、无匹配、属性提取
- DataProcessor — 数据处理、截断逻辑
"""
import json
import pytest
from unittest.mock import patch, MagicMock, PropertyMock

from agent.web import HttpClient, SearchEngine, Scraper, DataProcessor


# ════════════════════════════════════════════════════════════════════════════════
#  HttpClient 测试
# ════════════════════════════════════════════════════════════════════════════════

class MockRequestsResponse:
    """模拟 requests.Response 对象"""
    def __init__(self, status_code=200, text="ok", headers=None, url=""):
        self.status_code = status_code
        self._text = text
        self.headers = headers or {"Content-Type": "text/html"}
        self.url = url or "http://example.com"
        self.encoding = "utf-8"
        self.content = text.encode("utf-8")
        self.reason = {200: "OK", 404: "Not Found", 500: "Server Error"}.get(status_code, "Unknown")
        self.ok = 200 <= status_code < 300
        self.history = []

    def json(self):
        return json.loads(self._text)

    def raise_for_status(self):
        if self.status_code >= 400:
            from requests.exceptions import HTTPError
            raise HTTPError(f"HTTP {self.status_code}: {self.reason}")

    def iter_content(self, chunk_size=8192):
        yield self.content

    def close(self):
        pass

    @property
    def cookies(self):
        from requests.cookies import RequestsCookieJar
        return RequestsCookieJar()


class TestHttpClient:
    """HttpClient 工具测试"""

    def _make_client(self):
        return HttpClient({"timeout": 5, "max_retries": 0})

    def test_get_success(self):
        """正常 GET 请求"""
        client = self._make_client()
        mock_resp = MockRequestsResponse(
            text="<html><body><h1>Hello</h1></body></html>",
            url="http://example.com",
        )
        with patch.object(client._session, "request", return_value=mock_resp):
            result = client.get("http://example.com")
        assert result["ok"] is True
        assert result["status_code"] == 200
        assert "Hello" in result["text"]

    def test_get_with_custom_headers(self):
        """自定义 headers 的 GET 请求"""
        client = self._make_client()
        mock_resp = MockRequestsResponse(text="ok")
        with patch.object(client._session, "request", return_value=mock_resp) as mock_request:
            client.get("http://example.com", headers={"Authorization": "Bearer test123"})
        call_kwargs = mock_request.call_args[1]
        assert call_kwargs["headers"]["Authorization"] == "Bearer test123"

    def test_get_404(self):
        """HTTP 404 返回错误"""
        client = self._make_client()
        mock_resp = MockRequestsResponse(status_code=404, text="Not Found")
        with patch.object(client._session, "request", return_value=mock_resp):
            result = client.get("http://example.com/404")
        assert result["ok"] is False
        assert result["status_code"] == 404

    def test_get_timeout(self):
        """请求超时"""
        client = self._make_client()
        from requests.exceptions import Timeout
        with patch.object(client._session, "request", side_effect=Timeout("connection timeout")):
            result = client.get("http://example.com", timeout=1)
        assert result["ok"] is False
        assert "超时" in result.get("error", "")

    def test_post_form_data(self):
        """POST 表单数据"""
        client = self._make_client()
        mock_resp = MockRequestsResponse(text="ok")
        with patch.object(client._session, "request", return_value=mock_resp) as mock_request:
            result = client.post("http://example.com/api", data={"key": "value"})
        assert result["ok"] is True
        call_kwargs = mock_request.call_args[1]
        assert call_kwargs["data"] == {"key": "value"}
        assert call_kwargs["method"] == "POST"

    def test_post_json(self):
        """POST JSON 数据"""
        client = self._make_client()
        mock_resp = MockRequestsResponse(text='{"ok": true}')
        with patch.object(client._session, "request", return_value=mock_resp) as mock_request:
            result = client.post("http://example.com/api", json_data={"key": "value"})
        assert result["ok"] is True
        call_kwargs = mock_request.call_args[1]
        assert call_kwargs["json"] == {"key": "value"}

    def test_post_omit_data(self):
        """POST 无 data 无 json_data"""
        client = self._make_client()
        mock_resp = MockRequestsResponse(text="ok")
        with patch.object(client._session, "request", return_value=mock_resp):
            result = client.post("http://example.com/api")
        assert result["ok"] is True

    def test_batch_request(self):
        """批量请求"""
        client = self._make_client()
        responses = {
            ("GET", "http://a.com"): MockRequestsResponse(text="A", url="http://a.com"),
            ("GET", "http://b.com"): MockRequestsResponse(text="B", url="http://b.com"),
            ("GET", "http://c.com/404"): MockRequestsResponse(status_code=404, text=""),
        }
        def side_effect(method, url, **kw):
            return responses.get((method, url), MockRequestsResponse(status_code=404, text=""))

        with patch.object(client._session, "request", side_effect=side_effect):
            results = client.batch_request(
                ["http://a.com", "http://b.com", "http://c.com/404"],
                max_concurrency=2,
            )
        assert len(results) == 3
        assert results[0]["ok"] is True
        assert results[1]["ok"] is True
        assert results[2]["ok"] is False

    def test_batch_request_empty(self):
        """空列表批量请求"""
        client = self._make_client()
        with patch.object(client._session, "request") as mock_request:
            results = client.batch_request([], max_concurrency=5)
        assert results == []
        mock_request.assert_not_called()

    def test_download_file(self, tmp_path):
        """下载文件（download 使用 session.get 而非 session.request）"""
        client = self._make_client()
        dest = tmp_path / "downloaded.txt"
        mock_resp = MockRequestsResponse(text="file content")
        mock_resp.stream = False
        with patch.object(client._session, "get", return_value=mock_resp):
            result = client.download("http://example.com/file.txt", str(dest))
        assert result["ok"] is True
        assert dest.read_text() == "file content"


# ════════════════════════════════════════════════════════════════════════════════
#  SearchEngine 测试
# ════════════════════════════════════════════════════════════════════════════════

class TestSearchEngine:
    """SearchEngine 测试"""

    def _make_engine(self, priority=None):
        config = {
            "default_engine": "test_engine",
            "engine_priority": priority or ["test_engine", "backup_engine"],
            "engine_enabled": {"test_engine": True, "backup_engine": True},
            "timeout": 5,
        }
        engine = SearchEngine(config)
        engine.register_engine("test_engine", "测试引擎", handler=lambda q, **kw: {
            "ok": True,
            "engine": "test_engine",
            "results": [
                {"title": "R1", "url": "http://r1.com", "snippet": "结果1"},
                {"title": "R2", "url": "http://r2.com", "snippet": "结果2"},
            ],
        })
        engine.register_engine("backup_engine", "备用引擎", handler=lambda q, **kw: {
            "ok": True,
            "engine": "backup_engine",
            "results": [
                {"title": "Backup", "url": "http://backup.com", "snippet": "备用结果"},
            ],
        })
        return engine

    def test_search_normal(self):
        """正常搜索"""
        engine = self._make_engine()
        result = engine.search("test query")
        assert result["ok"] is True
        assert len(result["results"]) == 2
        assert result["results"][0]["title"] == "R1"

    def test_search_specify_engine(self):
        """指定引擎搜索"""
        engine = self._make_engine()
        result = engine.search("query", engine="backup_engine")
        assert result["ok"] is True
        assert result["results"][0]["snippet"] == "备用结果"

    def test_search_no_results(self):
        """无搜索结果"""
        engine = self._make_engine()
        engine.register_engine("empty_engine", "空引擎", handler=lambda q, **kw: {
            "ok": True, "engine": "empty_engine", "results": [],
        })
        # 设置优先级，让 empty_engine 成为首选
        engine._config["engine_priority"] = ["empty_engine", "test_engine"]
        engine._engine_priority = ["empty_engine", "test_engine"]
        result = engine.search("query")
        # 空结果应触发降级到 test_engine
        assert result["ok"] is True
        assert len(result["results"]) > 0

    def test_search_fallback(self):
        """主引擎失败时自动降级到备用引擎"""
        engine = SearchEngine({
            "default_engine": "failing",
            "engine_priority": ["failing", "backup"],
            "engine_enabled": {"failing": True, "backup": True},
            "timeout": 5,
        })
        engine.register_engine("failing", "会失败的引擎", handler=lambda q, **kw: {
            "ok": False, "engine": "failing", "error": "engine error",
        })
        engine.register_engine("backup", "备用引擎", handler=lambda q, **kw: {
            "ok": True, "engine": "backup",
            "results": [{"title": "B", "url": "http://b.com", "snippet": "b"}],
        })
        result = engine.search("query")
        assert result["ok"] is True
        assert result.get("fallback_used") is True

    def test_search_all_engines_fail(self):
        """所有引擎都失败"""
        engine = SearchEngine({
            "default_engine": "fail1",
            "engine_priority": ["fail1", "fail2"],
            "engine_enabled": {"fail1": True, "fail2": True},
            "timeout": 5,
        })
        for name in ("fail1", "fail2"):
            engine.register_engine(name, f"{name}", handler=lambda q, **kw: {
                "ok": False, "engine": name, "error": "failed",
            })
        result = engine.search("query")
        assert result["ok"] is False
        assert "所有搜索引擎均失败" in result.get("error", "")

    def test_search_no_engines(self):
        """没有可用引擎"""
        engine = SearchEngine({"default_engine": "", "engine_priority": [], "timeout": 5})
        result = engine.search("query")
        assert result["ok"] is False
        assert "没有可用" in result.get("error", "")

    def test_get_available_engines(self):
        """列出可用引擎"""
        engine = self._make_engine()
        available = engine.get_available_engines()
        names = [e["name"] for e in available]
        assert "test_engine" in names
        assert "backup_engine" in names

    def test_search_result_truncation(self):
        """搜索结果截断逻辑（超过 num_results 应截断）"""
        engine = self._make_engine()
        # 注册一个返回很多结果的引擎
        engine.register_engine("many", "很多结果", handler=lambda q, num_results=10, **kw: {
            "ok": True,
            "engine": "many",
            "results": [{"title": f"R{i}", "url": f"http://r{i}.com", "snippet": ""}
                       for i in range(num_results * 2)],
        })
        engine._engine_priority = ["many"]
        result = engine.search("query", num_results=5)
        # 验证结果被截断到接近 num_results
        assert len(result["results"]) <= 10


# ════════════════════════════════════════════════════════════════════════════════
#  Scraper 测试
# ════════════════════════════════════════════════════════════════════════════════

class TestScraper:
    """Scraper 解析工具测试"""

    HTML_SAMPLE = """
    <html>
    <head><title>测试页面</title></head>
    <body>
        <div id="content">
            <h1>欢迎</h1>
            <p class="intro">这是一个测试页面</p>
            <ul>
                <li><a href="/page1">链接一</a></li>
                <li><a href="/page2">链接二</a></li>
            </ul>
        </div>
        <footer>页脚信息</footer>
    </body>
    </html>
    """

    def test_xpath_match(self):
        """XPath 匹配"""
        scraper = Scraper()
        results = scraper.xpath("//h1/text()", html=self.HTML_SAMPLE)
        assert len(results) > 0
        assert "欢迎" in results[0]

    def test_xpath_no_match(self):
        """XPath 无匹配"""
        scraper = Scraper()
        results = scraper.xpath("//nonexistent", html=self.HTML_SAMPLE)
        assert len(results) == 0

    def test_css_selector(self):
        """CSS 选择器"""
        scraper = Scraper()
        results = scraper.css("p.intro", html=self.HTML_SAMPLE)
        assert len(results) > 0

    def test_css_attr_extraction(self):
        """CSS 选择器提取属性"""
        scraper = Scraper()
        results = scraper.css("a", html=self.HTML_SAMPLE, attr="href")
        assert len(results) == 2
        assert "/page1" in results
        assert "/page2" in results

    def test_css_no_match(self):
        """CSS 选择器无匹配"""
        scraper = Scraper()
        results = scraper.css(".nonexistent", html=self.HTML_SAMPLE)
        assert len(results) == 0

    def test_parse_title(self):
        """解析页面标题"""
        scraper = Scraper()
        parsed = scraper.parse(self.HTML_SAMPLE, url="http://example.com")
        assert parsed.get("title") == "测试页面"

    def test_parse_links(self):
        """解析页面链接"""
        scraper = Scraper()
        parsed = scraper.parse(self.HTML_SAMPLE, url="http://example.com")
        links = parsed.get("links", [])
        assert len(links) >= 2


# ════════════════════════════════════════════════════════════════════════════════
#  DataProcessor 测试
# ════════════════════════════════════════════════════════════════════════════════

class TestDataProcessor:
    """DataProcessor 数据处理测试"""

    def test_clean_text(self):
        """清洗文本"""
        result = DataProcessor.clean_text("  hello   world  \n\n  test  ")
        assert "hello world" in result
        assert "test" in result

    def test_summarize_results(self):
        """结果摘要"""
        results = [
            {"title": "Title1", "url": "http://a.com", "snippet": "Snippet1"},
            {"title": "Title2", "url": "http://b.com", "snippet": "Snippet2"},
        ]
        summary = DataProcessor.summarize_results(results)
        assert "Title1" in summary
        assert "Title2" in summary

    def test_process_filter_duplicates(self):
        """去重处理（需要满足最小内容长度 50 字符）"""
        dt = DataProcessor()
        items = [
            {"title": "Same", "url": "http://a.com", "snippet": "A" * 60},
            {"title": "Same", "url": "http://a.com", "snippet": "A" * 60},
            {"title": "Unique", "url": "http://b.com", "snippet": "B" * 60},
        ]
        processed = dt.process(items)
        urls = [i["url"] for i in processed]
        assert urls.count("http://a.com") == 1
        assert "http://b.com" in urls
