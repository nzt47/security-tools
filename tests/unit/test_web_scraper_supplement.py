"""Scraper 补充测试 - 未覆盖分支"""
import pytest
from unittest.mock import patch, MagicMock
from lxml import html as lxml_html

from agent.web.scraper import Scraper


class TestScraperDynamicContentDetection:
    """测试动态内容检测"""

    def test_detect_dynamic_content_react(self):
        """测试检测 React 应用"""
        html_content = """
        <html>
        <body>
            <div id="root"></div>
            <script>ReactDOM.render(<App />, document.getElementById('root'));</script>
        </body>
        </html>
        """
        
        tree = lxml_html.fromstring(html_content)
        result = Scraper._detect_dynamic_content(tree)
        
        assert result is True

    def test_detect_dynamic_content_vue(self):
        """测试检测 Vue 应用"""
        html_content = """
        <html>
        <body>
            <div id="app"></div>
            <script>new Vue({el: '#app'});</script>
        </body>
        </html>
        """
        
        tree = lxml_html.fromstring(html_content)
        result = Scraper._detect_dynamic_content(tree)
        
        assert result is True

    def test_detect_dynamic_content_angular(self):
        """测试检测 Angular 应用"""
        html_content = """
        <html>
        <body>
            <script>angular.module('app', []);</script>
        </body>
        </html>
        """
        
        tree = lxml_html.fromstring(html_content)
        result = Scraper._detect_dynamic_content(tree)
        
        assert result is True

    def test_detect_dynamic_content_nextjs(self):
        """测试检测 Next.js 应用"""
        html_content = """
        <html>
        <body>
            <div id="__next"></div>
        </body>
        </html>
        """
        
        tree = lxml_html.fromstring(html_content)
        result = Scraper._detect_dynamic_content(tree)
        
        assert result is True

    def test_detect_dynamic_content_noscript(self):
        """测试检测 noscript 标签"""
        html_content = """
        <html>
        <body>
            <noscript>Please enable JavaScript to view this page.</noscript>
        </body>
        </html>
        """
        
        tree = lxml_html.fromstring(html_content)
        result = Scraper._detect_dynamic_content(tree)
        
        assert result is True

    def test_detect_dynamic_content_static(self):
        """测试静态页面不被误判"""
        html_content = """
        <html>
        <head><title>Static Page</title></head>
        <body>
            <h1>Hello World</h1>
            <p>This is a static HTML page.</p>
        </body>
        </html>
        """
        
        tree = lxml_html.fromstring(html_content)
        result = Scraper._detect_dynamic_content(tree)
        
        assert result is False

    def test_detect_dynamic_content_empty_tree(self):
        """测试空树的处理"""
        html_content = "<html></html>"
        
        tree = lxml_html.fromstring(html_content)
        result = Scraper._detect_dynamic_content(tree)
        
        assert result is False


class TestScraperLinkExtraction:
    """测试链接提取与去重"""

    def test_extract_links_duplicate_removal(self):
        """测试链接去重"""
        html_content = """
        <html>
        <body>
            <a href="https://example.com/page1">Link 1</a>
            <a href="https://example.com/page1">Link 1 (duplicate)</a>
            <a href="https://example.com/page2">Link 2</a>
        </body>
        </html>
        """
        
        scraper = Scraper()
        result = scraper.parse(html_content, url="https://example.com")
        
        links = result["links"]
        assert len(links) == 2
        assert links[0]["url"] == "https://example.com/page1"
        assert links[1]["url"] == "https://example.com/page2"

    def test_extract_links_anchor_removal(self):
        """测试锚点移除"""
        html_content = """
        <html>
        <body>
            <a href="https://example.com/page#section1">Section 1</a>
            <a href="https://example.com/page#section2">Section 2</a>
        </body>
        </html>
        """
        
        scraper = Scraper()
        result = scraper.parse(html_content, url="https://example.com")
        
        links = result["links"]
        assert len(links) == 1  # 两个链接指向同一页面，去重后只剩一个
        assert links[0]["url"] == "https://example.com/page"

    def test_extract_links_filter_javascript(self):
        """测试过滤 JavaScript 链接"""
        html_content = """
        <html>
        <body>
            <a href="javascript:void(0)">Click</a>
            <a href="https://example.com/valid">Valid</a>
        </body>
        </html>
        """
        
        scraper = Scraper()
        result = scraper.parse(html_content, url="https://example.com")
        
        links = result["links"]
        assert len(links) == 1
        assert links[0]["url"] == "https://example.com/valid"

    def test_extract_links_filter_mailto(self):
        """测试过滤 mailto 链接"""
        html_content = """
        <html>
        <body>
            <a href="mailto:test@example.com">Email</a>
            <a href="https://example.com/page">Page</a>
        </body>
        </html>
        """
        
        scraper = Scraper()
        result = scraper.parse(html_content, url="https://example.com")
        
        links = result["links"]
        assert len(links) == 1
        assert links[0]["url"] == "https://example.com/page"

    def test_extract_links_nofollow(self):
        """测试 nofollow 属性提取"""
        html_content = """
        <html>
        <body>
            <a href="https://example.com/nofollow" rel="nofollow">No Follow</a>
            <a href="https://example.com/follow">Follow</a>
        </body>
        </html>
        """
        
        scraper = Scraper()
        result = scraper.parse(html_content, url="https://example.com")
        
        links = result["links"]
        assert len(links) == 2
        assert links[0]["nofollow"] is True
        assert links[1]["nofollow"] is False


class TestScraperHtmlCleaning:
    """测试 HTML 清洗"""

    def test_clean_html_remove_scripts(self):
        """测试移除脚本"""
        html_content = """
        <html>
        <head>
            <script>var x = 1;</script>
            <script src="test.js"></script>
        </head>
        <body><p>Content</p></body>
        </html>
        """
        
        cleaned = Scraper.clean_html(html_content)
        
        assert "<script>" not in cleaned
        assert "test.js" not in cleaned
        assert "Content" in cleaned

    def test_clean_html_remove_styles(self):
        """测试移除样式"""
        html_content = """
        <html>
        <head>
            <style>body { color: red; }</style>
            <link rel="stylesheet" href="style.css">
        </head>
        <body><p>Content</p></body>
        </html>
        """
        
        cleaned = Scraper.clean_html(html_content)
        
        assert "<style>" not in cleaned
        assert "style.css" not in cleaned

    def test_clean_html_remove_iframes(self):
        """测试移除 iframe"""
        html_content = """
        <html>
        <body>
            <iframe src="https://example.com"></iframe>
            <p>Content</p>
        </body>
        </html>
        """
        
        cleaned = Scraper.clean_html(html_content)
        
        assert "<iframe" not in cleaned
        assert "Content" in cleaned

    def test_clean_html_remove_comments(self):
        """测试移除注释"""
        html_content = """
        <html>
        <body>
            <!-- This is a comment -->
            <p>Content</p>
        </body>
        </html>
        """
        
        cleaned = Scraper.clean_html(html_content)
        
        assert "<!--" not in cleaned
        assert "-->" not in cleaned
        assert "Content" in cleaned

    def test_clean_html_invalid_html(self):
        """测试无效 HTML 的处理"""
        html_content = "<html><body><div>Invalid"
        
        cleaned = Scraper.clean_html(html_content)

        # lxml 会自动修复未闭合标签,验证内容保留即可
        assert "Invalid" in cleaned


class TestScraperTextExtraction:
    """测试文本提取"""

    def test_extract_text_empty_html(self):
        """测试空 HTML 的文本提取"""
        html_content = ""
        
        text = Scraper.extract_text_from_html(html_content)
        
        assert text == ""

    def test_extract_text_max_length(self):
        """测试文本长度限制"""
        long_text = "x" * 60000
        html_content = f"<html><body><p>{long_text}</p></body></html>"
        
        text = Scraper.extract_text_from_html(html_content, max_length=50000)
        
        assert len(text) == 50000

    def test_extract_text_entities(self):
        """测试 HTML 实体解码"""
        html_content = "<html><body><p>&amp; &lt; &gt; &quot;</p></body></html>"
        
        text = Scraper.extract_text_from_html(html_content)
        
        assert "&" in text
        assert "<" in text
        assert ">" in text
        assert '"' in text


class TestScraperHeadingsExtraction:
    """测试标题提取"""

    def test_extract_headings_all_levels(self):
        """测试提取所有级别的标题"""
        html_content = """
        <html>
        <body>
            <h1>Level 1</h1>
            <h2>Level 2</h2>
            <h3>Level 3</h3>
            <h4>Level 4</h4>
            <h5>Level 5</h5>
            <h6>Level 6</h6>
        </body>
        </html>
        """
        
        scraper = Scraper()
        result = scraper.parse(html_content)
        
        headings = result["headings"]
        
        assert "h1" in headings
        assert "h2" in headings
        assert "h3" in headings
        assert "h4" in headings
        assert "h5" in headings
        assert "h6" in headings
        assert headings["h1"] == ["Level 1"]
        assert headings["h2"] == ["Level 2"]

    def test_extract_headings_empty(self):
        """测试无标题页面"""
        html_content = "<html><body><p>Only text</p></body></html>"
        
        scraper = Scraper()
        result = scraper.parse(html_content)
        
        headings = result["headings"]
        
        assert headings == {}


class TestScraperParseErrors:
    """测试解析错误处理"""

    def test_parse_invalid_html(self):
        """测试无效 HTML 解析"""
        html_content = "<html><body><div>"  # 未闭合标签
        
        scraper = Scraper()
        result = scraper.parse(html_content)
        
        # lxml 会尝试修复无效 HTML
        assert result["ok"] is True

    def test_parse_empty_body(self):
        """测试空 body"""
        html_content = "<html><head><title>Test</title></head><body></body></html>"
        
        scraper = Scraper()
        result = scraper.parse(html_content)
        
        assert result["ok"] is True
        assert result["title"] == "Test"
        assert result["text"]