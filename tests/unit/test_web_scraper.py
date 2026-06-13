import pytest
from unittest.mock import patch, MagicMock
from agent.web.scraper import Scraper


class TestScraperInit:
    """测试 Scraper 初始化与配置"""

    def test_init_default(self):
        """测试默认初始化"""
        scraper = Scraper()
        assert scraper is not None
        assert scraper._http is None

    def test_init_with_http_client(self):
        """测试带 HTTP 客户端初始化"""
        mock_http = MagicMock()
        scraper = Scraper(http_client=mock_http)
        assert scraper._http is mock_http

    def test_set_http_client(self):
        """测试设置 HTTP 客户端"""
        scraper = Scraper()
        mock_http = MagicMock()
        scraper.set_http_client(mock_http)
        assert scraper._http is mock_http


class TestScraperFetch:
    """测试抓取功能"""

    def test_fetch_no_http_client(self):
        """测试没有配置 HTTP 客户端时的抓取"""
        scraper = Scraper()
        result = scraper.fetch("http://example.com")
        assert result["ok"] is False
        assert "HTTP 客户端未配置" in result["error"]

    @patch("agent.web.scraper.Scraper.parse")
    def test_fetch_success(self, mock_parse):
        """测试成功抓取"""
        mock_http = MagicMock()
        mock_http.get.return_value = {
            "ok": True,
            "text": "<html><body>test</body></html>",
            "url": "http://example.com"
        }
        mock_parse.return_value = {"ok": True, "title": "test"}

        scraper = Scraper(http_client=mock_http)
        result = scraper.fetch("http://example.com")

        assert result["ok"] is True
        mock_http.get.assert_called_once_with("http://example.com")
        mock_parse.assert_called_once_with("<html><body>test</body></html>", url="http://example.com")

    @patch("agent.web.scraper.Scraper.parse")
    def test_fetch_failure(self, mock_parse):
        """测试抓取失败"""
        mock_http = MagicMock()
        mock_http.get.return_value = {"ok": False, "error": "Network error"}

        scraper = Scraper(http_client=mock_http)
        result = scraper.fetch("http://example.com")

        assert result["ok"] is False
        assert result["error"] == "Network error"
        mock_parse.assert_not_called()


class TestScraperParse:
    """测试解析功能"""

    def test_parse_empty_html(self):
        """测试解析空 HTML"""
        scraper = Scraper()
        result = scraper.parse("", url="http://example.com")
        assert result["ok"] is False
        assert "HTML 内容为空" in result["error"]



    def test_parse_valid_html(self):
        """测试解析有效 HTML"""
        html = """
        <html>
            <head>
                <title>Test Title</title>
                <meta name="description" content="Test description">
                <meta property="og:title" content="OG Title">
            </head>
            <body>
                <h1>Hello World</h1>
                <p>Test paragraph</p>
                <a href="http://example.com/page1">Link 1</a>
                <a href="http://example.com/page2">Link 2</a>
                <img src="http://example.com/image.jpg" alt="Test image">
            </body>
        </html>
        """
        scraper = Scraper()
        result = scraper.parse(html, url="http://example.com")

        assert result["ok"] is True
        assert result["title"] == "Test Title"
        assert "Hello World" in result["text"]
        assert "Test paragraph" in result["text"]
        assert len(result["links"]) == 2
        assert len(result["images"]) == 1
        assert "description" in result["meta"]
        assert "h1" in result["headings"]

    def test_parse_without_title(self):
        """测试解析没有 title 标签的 HTML（有 h1）"""
        html = """
        <html>
            <head>
                <meta property="og:title" content="OG Title Only">
            </head>
            <body>
                <h1>Fallback Title</h1>
            </body>
        </html>
        """
        scraper = Scraper()
        result = scraper.parse(html, url="http://example.com")
        # h1 会优先于 og:title 被提取
        assert result["title"] == "Fallback Title"

    def test_parse_without_title_and_h1(self):
        """测试解析没有 title 标签和 h1 的 HTML"""
        html = """
        <html>
            <head>
                <meta property="og:title" content="OG Title Only">
            </head>
            <body>
                <p>Just text</p>
            </body>
        </html>
        """
        scraper = Scraper()
        result = scraper.parse(html, url="http://example.com")
        assert result["title"] == "OG Title Only"

    def test_parse_with_only_h1(self):
        """测试解析只有 h1 作为标题的 HTML"""
        html = """
        <html>
            <body>
                <h1>H1 as Title</h1>
            </body>
        </html>
        """
        scraper = Scraper()
        result = scraper.parse(html, url="http://example.com")
        assert result["title"] == "H1 as Title"

    def test_parse_with_dynamic_content(self):
        """测试解析有动态内容的 HTML"""
        html = """
        <html>
            <head>
                <title>React Page</title>
            </head>
            <body>
                <div id="root">Loading...</div>
            </body>
        </html>
        """
        scraper = Scraper()
        result = scraper.parse(html, url="http://example.com")
        assert result["needs_javascript"] is True


class TestScraperXPath:
    """测试 XPath 提取"""

    def test_xpath_valid_expression(self):
        """测试有效的 XPath 表达式"""
        html = """
        <html>
            <body>
                <div class="content">Test 1</div>
                <div class="content">Test 2</div>
            </body>
        </html>
        """
        scraper = Scraper()
        scraper.parse(html, url="http://example.com")
        
        results = scraper.xpath("//div[@class='content']/text()")
        assert len(results) == 2
        assert "Test 1" in results
        assert "Test 2" in results

    def test_xpath_invalid_expression(self):
        """测试无效的 XPath 表达式"""
        scraper = Scraper()
        results = scraper.xpath("invalid xpath")
        assert results == []

    def test_xpath_no_tree(self):
        """测试没有解析树时的 XPath 提取"""
        scraper = Scraper()
        results = scraper.xpath("//div/text()")
        assert results == []

    def test_xpath_with_html_param(self):
        """测试使用自定义 HTML 的 XPath 提取"""
        html = """
        <html>
            <body>
                <span>Custom HTML</span>
            </body>
        </html>
        """
        scraper = Scraper()
        results = scraper.xpath("//span/text()", html=html)
        assert results == ["Custom HTML"]


class TestScraperCSS:
    """测试 CSS 选择器提取"""

    def test_css_extract_text(self):
        """测试使用 CSS 选择器提取文本"""
        html = """
        <html>
            <body>
                <p class="para">Paragraph 1</p>
                <p class="para">Paragraph 2</p>
            </body>
        </html>
        """
        scraper = Scraper()
        scraper.parse(html, url="http://example.com")
        
        results = scraper.css("p.para")
        assert len(results) == 2
        assert "Paragraph 1" in results
        assert "Paragraph 2" in results

    def test_css_extract_attribute(self):
        """测试使用 CSS 选择器提取属性"""
        html = """
        <html>
            <body>
                <a href="http://link1.com">Link 1</a>
                <a href="http://link2.com">Link 2</a>
            </body>
        </html>
        """
        scraper = Scraper()
        scraper.parse(html, url="http://example.com")
        
        results = scraper.css("a", attr="href")
        assert len(results) == 2
        assert "http://link1.com" in results
        assert "http://link2.com" in results

    def test_css_invalid_selector(self):
        """测试无效的 CSS 选择器"""
        scraper = Scraper()
        results = scraper.css("invalid-selector")
        assert results == []

    def test_css_no_tree(self):
        """测试没有解析树时的 CSS 提取"""
        scraper = Scraper()
        results = scraper.css("p")
        assert results == []

    def test_css_with_html_param(self):
        """测试使用自定义 HTML 的 CSS 提取"""
        html = """
        <html>
            <body>
                <h2>Custom CSS</h2>
            </body>
        </html>
        """
        scraper = Scraper()
        results = scraper.css("h2", html=html)
        assert results == ["Custom CSS"]


class TestScraperExtract:
    """测试结构化提取"""

    @patch("agent.web.scraper.Scraper.fetch")
    def test_extract_multiple_fields(self, mock_fetch):
        """测试提取多个字段"""
        mock_fetch.return_value = {
            "ok": True,
            "url": "http://example.com"
        }
        
        html = """
        <html>
            <body>
                <h1 class="title">Product Title</h1>
                <span class="price">$19.99</span>
                <p class="desc">Product description</p>
            </body>
        </html>
        """
        scraper = Scraper()
        scraper.parse(html, url="http://example.com")
        
        xpath_map = {
            "title": "//h1[@class='title']/text()",
            "price": "//span[@class='price']/text()",
            "desc": "//p[@class='desc']/text()"
        }
        
        result = scraper.extract("http://example.com", xpath_map)
        assert result["ok"] is True
        assert "extracted" in result
        assert result["extracted"]["title"] == ["Product Title"]
        assert result["extracted"]["price"] == ["$19.99"]
        assert result["extracted"]["desc"] == ["Product description"]

    @patch("agent.web.scraper.Scraper.fetch")
    def test_extract_fetch_failed(self, mock_fetch):
        """测试提取失败（抓取失败）"""
        mock_fetch.return_value = {"ok": False, "error": "Failed to fetch"}
        
        scraper = Scraper()
        result = scraper.extract("http://example.com", {})
        assert result["ok"] is False
        assert result["error"] == "Failed to fetch"


class TestScraperInternalMethods:
    """测试内部提取方法"""

    def test_extract_title(self):
        """测试提取标题"""
        html = """
        <html>
            <head>
                <title>Test Page Title</title>
                <meta property="og:title" content="OG Title">
            </head>
            <body>
                <h1>H1 Title</h1>
            </body>
        </html>
        """
        from lxml import html as lxml_html
        tree = lxml_html.fromstring(html)
        title = Scraper._extract_title(tree)
        assert title == "Test Page Title"

    def test_extract_text(self):
        """测试提取正文文本"""
        html = """
        <html>
            <body>
                <script>alert('test')</script>
                <style>body { color: red; }</style>
                <div>Hello World</div>
                <p>Test Paragraph</p>
            </body>
        </html>
        """
        from lxml import html as lxml_html
        tree = lxml_html.fromstring(html)
        text = Scraper._extract_text(tree)
        assert "Hello World" in text
        assert "Test Paragraph" in text
        assert "alert" not in text

    def test_extract_links(self):
        """测试提取链接"""
        html = """
        <html>
            <body>
                <a href="http://example.com/link1">Link 1</a>
                <a href="http://example.com/link2">Link 2</a>
                <a href="mailto:test@example.com">Email</a>
                <a href="javascript:void(0)">JS</a>
                <a href="#section1">Anchor</a>
            </body>
        </html>
        """
        from lxml import html as lxml_html
        tree = lxml_html.fromstring(html)
        links = Scraper._extract_links(tree, "http://example.com")
        assert len(links) == 2
        assert any(l["url"] == "http://example.com/link1" for l in links)
        assert any(l["url"] == "http://example.com/link2" for l in links)

    def test_extract_images(self):
        """测试提取图片"""
        html = """
        <html>
            <body>
                <img src="image1.jpg" alt="Image 1">
                <img src="image2.jpg" alt="Image 2">
            </body>
        </html>
        """
        from lxml import html as lxml_html
        tree = lxml_html.fromstring(html)
        images = Scraper._extract_images(tree, "http://example.com")
        assert len(images) == 2
        assert images[0]["url"] == "http://example.com/image1.jpg"
        assert images[0]["alt"] == "Image 1"

    def test_extract_meta(self):
        """测试提取 meta 信息"""
        html = """
        <html>
            <head>
                <meta name="description" content="Test description">
                <meta property="og:type" content="website">
            </head>
        </html>
        """
        from lxml import html as lxml_html
        tree = lxml_html.fromstring(html)
        meta = Scraper._extract_meta(tree)
        assert "description" in meta
        assert "og:type" in meta
        assert meta["description"] == "Test description"

    def test_extract_headings(self):
        """测试提取标题层级"""
        html = """
        <html>
            <body>
                <h1>Heading 1</h1>
                <h2>Heading 2</h2>
                <h2>Another Heading 2</h2>
                <h3>Heading 3</h3>
            </body>
        </html>
        """
        from lxml import html as lxml_html
        tree = lxml_html.fromstring(html)
        headings = Scraper._extract_headings(tree)
        assert "h1" in headings
        assert "h2" in headings
        assert "h3" in headings
        assert headings["h1"] == ["Heading 1"]
        assert len(headings["h2"]) == 2


class TestScraperTools:
    """测试工具方法"""

    def test_clean_html(self):
        """测试清洗 HTML"""
        html = """
        <html>
            <head>
                <script>alert('test')</script>
                <style>body { color: red; }</style>
            </head>
            <body>
                <div>Hello World</div>
                <!-- This is a comment -->
            </body>
        </html>
        """
        cleaned = Scraper.clean_html(html)
        assert "script" not in cleaned.lower()
        assert "style" not in cleaned.lower()
        assert "comment" not in cleaned
        assert "Hello World" in cleaned

    def test_extract_text_from_html(self):
        """测试快速提取文本"""
        html = """
        <html>
            <body>
                <div>Hello</div>
                <p>World</p>
            </body>
        </html>
        """
        text = Scraper.extract_text_from_html(html)
        assert "Hello" in text
        assert "World" in text

    def test_extract_text_from_html_invalid(self):
        """测试解析无效 HTML 时的文本提取回退"""
        invalid_html = "<<< invalid html >>>"
        text = Scraper.extract_text_from_html(invalid_html)
        assert "invalid html" in text

    def test_get_stats(self):
        """测试获取统计信息"""
        scraper = Scraper()
        html = "<html><body>test</body></html>"
        scraper.parse(html, url="http://example.com")
        
        stats = scraper.get_stats()
        assert "last_url" in stats
        assert "last_html_length" in stats
        assert stats["last_url"] == "http://example.com"


class TestScraperEdgeCases:
    """测试边界条件和异常情况"""

    def test_xpath_with_non_text_nodes(self):
        """测试提取非文本节点的 XPath"""
        html = """
        <html>
            <body>
                <div class="container">
                    <p>Hello</p>
                </div>
            </body>
        </html>
        """
        scraper = Scraper()
        scraper.parse(html, url="http://example.com")
        
        # 提取非文本节点
        results = scraper.xpath("//div[@class='container']")
        assert len(results) == 1

    def test_css_extract_non_existent_attr(self):
        """测试提取不存在的属性"""
        html = """
        <html>
            <body>
                <a href="http://example.com">Link</a>
            </body>
        </html>
        """
        scraper = Scraper()
        scraper.parse(html, url="http://example.com")
        
        # 提取不存在的属性
        results = scraper.css("a", attr="non_existent_attr")
        assert len(results) == 1
        assert results[0] == ""

    def test_extract_links_with_invalid_url(self):
        """测试提取无效的 URL"""
        html = """
        <html>
            <body>
                <a href="javascript:alert(1)">JS Link</a>
                <a href="mailto:test@example.com">Email</a>
                <a href="#anchor">Anchor</a>
                <a href="/relative/path">Relative</a>
            </body>
        </html>
        """
        from lxml import html as lxml_html
        tree = lxml_html.fromstring(html)
        links = Scraper._extract_links(tree, "http://example.com")
        assert len(links) == 1
        assert links[0]["url"] == "http://example.com/relative/path"

    def test_extract_images_without_src(self):
        """测试提取没有 src 属性的图片"""
        html = """
        <html>
            <body>
                <img alt="No src">
            </body>
        </html>
        """
        from lxml import html as lxml_html
        tree = lxml_html.fromstring(html)
        images = Scraper._extract_images(tree, "http://example.com")
        assert len(images) == 0

    def test_extract_title_all_missing(self):
        """测试所有标题来源都缺失的情况"""
        html = """
        <html>
            <body>
                <p>Nothing here</p>
            </body>
        </html>
        """
        from lxml import html as lxml_html
        tree = lxml_html.fromstring(html)
        title = Scraper._extract_title(tree)
        assert title == ""

    def test_detect_dynamic_content_without_indicators(self):
        """测试检测没有动态内容标志的页面"""
        html = """
        <html>
            <body>
                <p>Static content</p>
            </body>
        </html>
        """
        from lxml import html as lxml_html
        tree = lxml_html.fromstring(html)
        needs_js = Scraper._detect_dynamic_content(tree)
        assert needs_js is False


class TestScraperExceptionHandling:
    """测试异常处理分支"""

    def test_parse_invalid_html(self, monkeypatch):
        """测试解析无效 HTML 时的异常处理"""
        def mock_fromstring(*args, **kwargs):
            raise ValueError("Mocked parsing error")
        
        monkeypatch.setattr("agent.web.scraper.lxml_html.fromstring", mock_fromstring)
        
        scraper = Scraper()
        result = scraper.parse("<html><body></body></html>", url="http://example.com")
        assert result["ok"] is False
        assert "HTML 解析失败" in result["error"]

    def test_xpath_expression_error(self):
        """测试 XPath 表达式错误时的异常处理"""
        html = "<html><body><div>test</div></body></html>"
        scraper = Scraper()
        scraper.parse(html, url="http://example.com")
        
        # 使用无效的 XPath 表达式
        results = scraper.xpath("//div[@invalid=]")
        assert results == []

    def test_css_selector_error(self):
        """测试 CSS 选择器错误时的异常处理"""
        html = "<html><body><div>test</div></body></html>"
        scraper = Scraper()
        scraper.parse(html, url="http://example.com")
        
        # 使用无效的 CSS 选择器
        results = scraper.css("div[invalid=")
        assert results == []

    def test_detect_dynamic_content_exception(self):
        """测试动态内容检测时的异常处理"""
        scraper = Scraper()
        # 传入非树对象触发异常
        result = scraper._detect_dynamic_content("invalid tree")
        assert result is False

    def test_extract_title_exception(self):
        """测试标题提取时的异常处理"""
        title = Scraper._extract_title("invalid tree")
        assert title == ""

    def test_extract_text_exception(self):
        """测试文本提取时的异常处理"""
        text = Scraper._extract_text("invalid tree")
        assert text == ""

    def test_extract_links_exception(self):
        """测试链接提取时的异常处理"""
        links = Scraper._extract_links("invalid tree", "http://example.com")
        assert links == []

    def test_extract_links_duplicate_url(self):
        """测试提取重复 URL 的处理（continue 分支）"""
        html = """
        <html>
            <body>
                <a href="http://example.com/same">Link 1</a>
                <a href="http://example.com/same">Link 2</a>
            </body>
        </html>
        """
        from lxml import html as lxml_html
        tree = lxml_html.fromstring(html)
        links = Scraper._extract_links(tree, "http://example.com")
        assert len(links) == 1
        assert links[0]["url"] == "http://example.com/same"

    def test_extract_images_exception(self):
        """测试图片提取时的异常处理"""
        images = Scraper._extract_images("invalid tree", "http://example.com")
        assert images == []

    def test_extract_images_empty_src(self):
        """测试提取空 src 属性的图片（continue 分支）"""
        html = """
        <html>
            <body>
                <img src="" alt="Empty">
                <img src="image.jpg" alt="Valid">
            </body>
        </html>
        """
        from lxml import html as lxml_html
        tree = lxml_html.fromstring(html)
        images = Scraper._extract_images(tree, "http://example.com")
        assert len(images) == 1
        assert images[0]["url"] == "http://example.com/image.jpg"

    def test_extract_meta_exception(self):
        """测试 meta 提取时的异常处理"""
        meta = Scraper._extract_meta("invalid tree")
        assert meta == {}

    def test_extract_headings_exception(self):
        """测试标题层级提取时的异常处理"""
        headings = Scraper._extract_headings("invalid tree")
        assert headings == {}

    def test_get_tree_invalid_html(self, monkeypatch):
        """测试获取解析树时的异常处理"""
        def mock_fromstring(*args, **kwargs):
            raise ValueError("Mocked parsing error")
        
        monkeypatch.setattr("agent.web.scraper.lxml_html.fromstring", mock_fromstring)
        
        scraper = Scraper()
        tree = scraper._get_tree("<html><body></body></html>")
        assert tree is None

