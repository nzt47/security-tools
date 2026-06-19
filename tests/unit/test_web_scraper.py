"""Scraper 单元测试"""
import pytest
from unittest.mock import patch, MagicMock
from lxml import html as lxml_html

from agent.web.scraper import Scraper


class TestScraper:
    """测试抓取器"""

    def test_scraper_init(self):
        """测试初始化"""
        scraper = Scraper()
        
        assert scraper._http is None
        assert scraper._last_html == ""
        assert scraper._last_tree is None

    def test_scraper_set_http_client(self):
        """测试设置 HTTP 客户端"""
        scraper = Scraper()
        mock_client = MagicMock()
        
        scraper.set_http_client(mock_client)
        
        assert scraper._http is mock_client

    def test_scraper_parse_basic(self):
        """测试解析 HTML"""
        html_content = "<html><head><title>Test Page</title></head><body><h1>Hello</h1><p>Content</p></body></html>"
        
        scraper = Scraper()
        result = scraper.parse(html_content, url="https://example.com")
        
        assert result["ok"] is True
        assert result["title"] == "Test Page"
        assert "Hello" in result["text"]
        assert "Content" in result["text"]

    def test_scraper_parse_empty(self):
        """测试解析空内容"""
        scraper = Scraper()
        result = scraper.parse("")
        
        assert result["ok"] is False
        assert "HTML 内容为空" in result["error"]

    def test_scraper_xpath_extraction(self):
        """测试 XPath 提取"""
        html_content = "<html><body><div class='content'>Hello World</div></body></html>"
        
        scraper = Scraper()
        scraper.parse(html_content)
        
        result = scraper.xpath("//div[@class='content']/text()")
        
        assert len(result) == 1
        assert result[0] == "Hello World"

    def test_scraper_css_extraction(self):
        """测试 CSS 选择器提取"""
        html_content = "<html><body><div class='content'>CSS Test</div></body></html>"
        
        scraper = Scraper()
        scraper.parse(html_content)
        
        result = scraper.css("div.content")
        
        assert len(result) == 1
        assert result[0] == "CSS Test"

    def test_scraper_css_extraction_with_attr(self):
        """测试提取属性"""
        html_content = '<html><body><a href="https://example.com">Link</a></body></html>'
        
        scraper = Scraper()
        scraper.parse(html_content)
        
        result = scraper.css("a", attr="href")
        
        assert len(result) == 1
        assert result[0] == "https://example.com"

    def test_scraper_extract_links(self):
        """测试提取链接"""
        html_content = """<html>
        <body>
            <a href="https://example.com/page1">Page 1</a>
            <a href="/page2">Page 2</a>
        </body>
        </html>"""
        
        scraper = Scraper()
        result = scraper.parse(html_content, url="https://example.com")
        
        links = result["links"]
        
        assert len(links) == 2
        assert links[0]["url"] == "https://example.com/page1"
        assert links[1]["url"] == "https://example.com/page2"

    def test_scraper_extract_images(self):
        """测试提取图片"""
        html_content = """<html>
        <body>
            <img src="https://example.com/img1.jpg" alt="Image 1">
            <img src="/img2.png">
        </body>
        </html>"""
        
        scraper = Scraper()
        result = scraper.parse(html_content, url="https://example.com")
        
        images = result["images"]
        
        assert len(images) == 2
        assert images[0]["url"] == "https://example.com/img1.jpg"
        assert images[0]["alt"] == "Image 1"

    def test_scraper_extract_meta(self):
        """测试提取 meta 信息"""
        html_content = '<html><head><meta name="description" content="Test description"></head></html>'
        
        scraper = Scraper()
        result = scraper.parse(html_content)
        
        meta = result["meta"]
        
        assert "description" in meta
        assert meta["description"] == "Test description"

    @patch("agent.web.scraper.Scraper.fetch")
    def test_scraper_extract(self, mock_fetch):
        """测试结构化提取"""
        mock_fetch.return_value = {
            "ok": True,
            "url": "https://example.com"
        }
        
        scraper = Scraper()
        result = scraper.extract("https://example.com", {"title": "//h1/text()"})
        
        assert result["ok"] is True
        mock_fetch.assert_called_once()

    def test_scraper_clean_html(self):
        """测试清洗 HTML"""
        html_content = """<html>
        <head><script>var x = 1;</script><style>body { color: red; }</style></head>
        <body><p>Clean text</p></body>
        </html>"""
        
        cleaned = Scraper.clean_html(html_content)
        
        assert "<script>" not in cleaned
        assert "<style>" not in cleaned
        assert "Clean text" in cleaned

    def test_scraper_extract_text_from_html(self):
        """测试从 HTML 提取文本"""
        html_content = "<html><body><h1>Hello</h1><p>World</p></body></html>"
        
        text = Scraper.extract_text_from_html(html_content)
        
        assert "Hello" in text
        assert "World" in text

    def test_scraper_fetch_without_http(self):
        """测试没有 HTTP 客户端时的抓取"""
        scraper = Scraper()
        result = scraper.fetch("https://example.com")
        
        assert result["ok"] is False
        assert "HTTP 客户端未配置" in result["error"]