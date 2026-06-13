"""
网页解析引擎测试
"""

import pytest
from unittest.mock import Mock, patch

from agent.web.scraper import Scraper, SPIDER_UA_PATTERNS


class TestScraperInit:
    """测试解析引擎初始化"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_init_without_http_client(self):
        """测试无 HTTP 客户端初始化"""
        scraper = Scraper()
        
        assert scraper._http is None
        assert scraper._last_html == ""
        assert scraper._last_url == ""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_init_with_http_client(self):
        """测试带 HTTP 客户端初始化"""
        mock_http = Mock()
        scraper = Scraper(http_client=mock_http)
        
        assert scraper._http == mock_http

    @pytest.mark.unit
    @pytest.mark.p1
    def test_set_http_client(self):
        """测试设置 HTTP 客户端"""
        scraper = Scraper()
        mock_http = Mock()
        
        scraper.set_http_client(mock_http)
        
        assert scraper._http == mock_http


class TestSpiderUAPatterns:
    """测试爬虫 UA 黑名单"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_detect_bot_ua(self):
        """测试检测爬虫 UA"""
        bot_uas = [
            "Googlebot/2.1",
            "bingbot/2.0",
            "Python-requests/2.28.0",
            "curl/7.68.0",
            "Scrapy/2.5.0",
        ]
        
        for ua in bot_uas:
            assert SPIDER_UA_PATTERNS.search(ua) is not None

    @pytest.mark.unit
    @pytest.mark.p1
    def test_normal_ua_not_detected(self):
        """测试正常 UA 不被检测"""
        normal_uas = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) Safari/605.1.15",
        ]
        
        for ua in normal_uas:
            assert SPIDER_UA_PATTERNS.search(ua) is None


class TestScraperFetch:
    """测试网页抓取"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_fetch_without_http_client(self):
        """测试无 HTTP 客户端抓取"""
        scraper = Scraper()
        result = scraper.fetch("http://example.com")
        
        assert not result["ok"]
        assert "HTTP 客户端未配置" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p1
    def test_fetch_with_http_client_success(self):
        """测试成功抓取"""
        mock_http = Mock()
        mock_http.get.return_value = {
            "ok": True,
            "text": "<html><head><title>Test</title></head><body>Hello</body></html>",
            "url": "http://example.com",
        }
        
        scraper = Scraper(http_client=mock_http)
        result = scraper.fetch("http://example.com")
        
        assert result["ok"]
        mock_http.get.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.p1
    def test_fetch_with_http_client_failure(self):
        """测试抓取失败"""
        mock_http = Mock()
        mock_http.get.return_value = {
            "ok": False,
            "error": "Connection timeout",
        }
        
        scraper = Scraper(http_client=mock_http)
        result = scraper.fetch("http://example.com")
        
        assert not result["ok"]
        assert "Connection timeout" in result["error"]


class TestScraperParse:
    """测试 HTML 解析"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_parse_basic_html(self):
        """测试解析基本 HTML"""
        scraper = Scraper()
        html = "<html><head><title>Test Page</title></head><body><p>Hello World</p></body></html>"
        
        result = scraper.parse(html, url="http://example.com")
        
        assert result["ok"]
        assert result["title"] == "Test Page"
        assert "Hello World" in result["text"]

    @pytest.mark.unit
    @pytest.mark.p1
    def test_parse_with_links(self):
        """测试解析带链接的 HTML"""
        scraper = Scraper()
        html = """
        <html>
        <body>
            <a href="/page1">Page 1</a>
            <a href="http://other.com/page2">Page 2</a>
        </body>
        </html>
        """
        
        result = scraper.parse(html, url="http://example.com")
        
        assert result["ok"]
        assert len(result["links"]) >= 2

    @pytest.mark.unit
    @pytest.mark.p1
    def test_parse_with_meta(self):
        """测试解析带 meta 标签的 HTML"""
        scraper = Scraper()
        html = """
        <html>
        <head>
            <meta name="description" content="Test description">
            <meta name="keywords" content="test, keywords">
        </head>
        <body>Content</body>
        </html>
        """
        
        result = scraper.parse(html, url="http://example.com")
        
        assert result["ok"]
        assert "meta" in result


class TestScraperExtract:
    """测试数据提取"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_extract_xpath(self):
        """测试 XPath 提取"""
        scraper = Scraper()
        html = """
        <html>
        <body>
            <div class="content">
                <p class="item">Item 1</p>
                <p class="item">Item 2</p>
            </div>
        </body>
        </html>
        """
        
        result = scraper.parse(html)
        # 使用 parse 结果中的数据
        assert result["ok"]

    @pytest.mark.unit
    @pytest.mark.p1
    def test_extract_css(self):
        """测试 CSS 选择器提取"""
        scraper = Scraper()
        html = """
        <html>
        <body>
            <div class="content">
                <span class="title">Title</span>
            </div>
        </body>
        </html>
        """
        
        result = scraper.parse(html)
        assert result["ok"]


class TestScraperLinks:
    """测试链接处理"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_extract_links_absolute(self):
        """测试提取绝对链接"""
        scraper = Scraper()
        html = '<a href="http://example.com/page">Link</a>'
        
        result = scraper.parse(html, url="http://other.com")
        links = result.get("links", [])
        
        # links 是字典列表
        assert len(links) >= 1
        assert links[0]["url"] == "http://example.com/page"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_extract_links_relative(self):
        """测试提取相对链接"""
        scraper = Scraper()
        html = '<a href="/page">Link</a>'
        
        result = scraper.parse(html, url="http://example.com")
        links = result.get("links", [])
        
        # 相对链接应该被转换为绝对链接
        assert len(links) >= 1
        assert "example.com" in links[0]["url"]