"""
网页内容爬取与解析引擎 — HTML 解析、XPath/CSS 选择器、动态内容

基于 lxml + BeautifulSoup4，支持结构化数据提取。
"""

import re
import json
import logging
from typing import Optional, List, Dict, Any, Union
from urllib.parse import urljoin, urlparse
from html import unescape

from lxml import etree, html as lxml_html

logger = logging.getLogger(__name__)

# 常见爬虫 User-Agent 黑名单特征
SPIDER_UA_PATTERNS = re.compile(
    r"(bot|crawl|spider|scrape|scrapy|curl|wget|python-requests|httpx|java|perl|ruby|php)",
    re.IGNORECASE,
)


class Scraper:
    """网页解析引擎 — HTML 抓取、解析、数据提取

    功能：
    - 自动抓取 URL 内容
    - HTML 解析（lxml 加速）
    - XPath / CSS 选择器提取
    - 链接提取与清洗
    - meta 信息提取
    - 正文提取（Readability 风格）
    - 动态内容检测
    """

    def __init__(self, http_client=None):
        self._http = http_client
        self._last_html = ""
        self._last_tree = None
        self._last_url = ""
        logger.info("Scraper 引擎已初始化")

    def set_http_client(self, http_client):
        """设置或更换 HTTP 客户端"""
        self._http = http_client

    # ── 抓取与解析 ────────────────────────────────────────────────

    def fetch(self, url: str, **kwargs) -> dict:
        """抓取并解析网页

        Args:
            url: 目标 URL
            **kwargs: 传递给 HttpClient.request 的参数

        Returns:
            dict: {ok, url, title, text, html, links, meta, ...}
        """
        if not self._http:
            return {"ok": False, "error": "HTTP 客户端未配置，请先调用 set_http_client()"}

        result = self._http.get(url, **kwargs)
        if not result.get("ok"):
            return result

        html_content = result.get("text", "")
        return self.parse(html_content, url=result.get("url", url))

    def parse(self, html_content: str, url: str = "") -> dict:
        """解析 HTML 内容

        Args:
            html_content: HTML 源码
            url: 来源 URL（用于链接补全）

        Returns:
            dict: {ok, url, title, text, html, links, images, meta, head, ...}
        """
        self._last_html = html_content
        self._last_url = url

        if not html_content or not html_content.strip():
            return {"ok": False, "error": "HTML 内容为空"}

        try:
            tree = lxml_html.fromstring(html_content)
            self._last_tree = tree
        except Exception as e:
            return {"ok": False, "error": f"HTML 解析失败: {e}"}

        result = {
            "ok": True,
            "url": url,
            "title": self._extract_title(tree),
            "text": self._extract_text(tree),
            "html": html_content,
            "links": self._extract_links(tree, url),
            "images": self._extract_images(tree, url),
            "meta": self._extract_meta(tree),
            "headings": self._extract_headings(tree),
        }

        # 检测是否需要动态渲染
        result["needs_javascript"] = self._detect_dynamic_content(tree)

        return result

    # ── 选择器提取 ────────────────────────────────────────────────

    def xpath(self, expression: str, html: Optional[str] = None) -> List[str]:
        """XPath 提取 — 使用 XPath 表达式提取文本

        Args:
            expression: XPath 表达式
            html: 可选，指定 HTML 源码，不传则使用最近解析的

        Returns:
            List[str]: 提取的文本列表
        """
        tree = self._get_tree(html)
        if tree is None:
            return []
        try:
            elements = tree.xpath(expression)
            return [
                e.text_content().strip() if hasattr(e, "text_content") else str(e).strip()
                for e in elements
                if e is not None
            ]
        except Exception as e:
            logger.warning("XPath 提取失败 (%s): %s", expression, e)
            return []

    def css(self, selector: str, html: Optional[str] = None, attr: Optional[str] = None) -> List[str]:
        """CSS 选择器提取

        Args:
            selector: CSS 选择器（如 div.content, h1.title）
            html: 可选，指定 HTML 源码
            attr: 可选，提取的属性名（如 href, src），不传则提取文本

        Returns:
            List[str]: 提取结果列表
        """
        tree = self._get_tree(html)
        if tree is None:
            return []
        try:
            if attr:
                elements = tree.cssselect(selector)
                return [
                    e.get(attr, "").strip()
                    for e in elements
                    if e is not None
                ]
            else:
                elements = tree.cssselect(selector)
                return [
                    e.text_content().strip()
                    for e in elements
                    if e is not None
                ]
        except Exception as e:
            logger.warning("CSS 选择器提取失败 (%s): %s", selector, e)
            return []

    def extract(self, url: str, xpath_map: Dict[str, str], **kwargs) -> dict:
        """结构化提取 — 一次性提取多个字段

        Args:
            url: 目标 URL
            xpath_map: 字段名 → XPath 表达式的映射
                       {'title': '//h1/text()', 'price': '//span[@class="price"]/text()'}
            **kwargs: 传递给 fetch 的参数

        Returns:
            dict: 提取结果，字段名 → 值列表
        """
        result = self.fetch(url, **kwargs)
        if not result.get("ok"):
            return result

        extracted = {}
        for field, expr in xpath_map.items():
            values = self.xpath(expr)
            extracted[field] = values

        result["extracted"] = extracted
        return result

    # ── 动态内容检测 ──────────────────────────────────────────────

    @staticmethod
    def _detect_dynamic_content(tree) -> bool:
        """检测页面是否需要 JavaScript 渲染"""
        indicators = [
            '//script[contains(text(), "react") or contains(text(), "vue") or contains(text(), "angular")]',
            '//div[@id="app" or @id="root" or @id="__next"]',
            '//div[contains(@class, "react")]',
            '//noscript[contains(text(), "JavaScript")]',
        ]
        try:
            for expr in indicators:
                if tree.xpath(expr):
                    return True
        except Exception:
            pass
        return False

    # ── 内部提取方法 ──────────────────────────────────────────────

    @staticmethod
    def _extract_title(tree) -> str:
        """提取页面标题"""
        try:
            title_el = tree.xpath("//title/text()")
            if title_el:
                return title_el[0].strip()

            h1 = tree.xpath("//h1/text()")
            if h1:
                return h1[0].strip()

            og_title = tree.xpath('//meta[@property="og:title"]/@content')
            if og_title:
                return og_title[0].strip()
        except Exception:
            pass
        return ""

    @staticmethod
    def _extract_text(tree) -> str:
        """提取页面正文文本"""
        try:
            # 移除脚本和样式
            for elem in tree.xpath("//script | //style | //noscript | //iframe"):
                elem.getparent().remove(elem)
        except Exception:
            pass

        text = tree.text_content() if hasattr(tree, "text_content") else ""
        # 清理空白
        text = re.sub(r"\s+", " ", text).strip()
        return text[:50000]  # 限制最大 5 万字

    @staticmethod
    def _extract_links(tree, base_url: str) -> List[Dict[str, str]]:
        """提取页面链接"""
        links = []
        seen = set()
        try:
            for a in tree.xpath("//a[@href]"):
                href = a.get("href", "").strip()
                if not href or href.startswith(("javascript:", "#", "mailto:", "tel:")):
                    continue

                # 补全相对链接
                abs_url = urljoin(base_url, href) if base_url else href
                abs_url = abs_url.split("#")[0]  # 去锚点

                if abs_url in seen or not abs_url.startswith(("http://", "https://")):
                    continue
                seen.add(abs_url)

                links.append({
                    "url": abs_url,
                    "text": (a.text_content() or "").strip()[:100],
                    "nofollow": "nofollow" in (a.get("rel", "") or ""),
                })
        except Exception:
            pass

        return links[:500]  # 最多 500 个链接

    @staticmethod
    def _extract_images(tree, base_url: str) -> List[Dict[str, str]]:
        """提取页面图片"""
        images = []
        try:
            for img in tree.xpath("//img[@src]"):
                src = img.get("src", "").strip()
                if not src:
                    continue
                abs_src = urljoin(base_url, src) if base_url else src
                alt = img.get("alt", "").strip()
                images.append({"url": abs_src, "alt": alt})
        except Exception:
            pass
        return images[:100]

    @staticmethod
    def _extract_meta(tree) -> Dict[str, str]:
        """提取 meta 信息"""
        meta = {}
        try:
            for m in tree.xpath("//meta"):
                name = m.get("name", m.get("property", "")).strip()
                content = m.get("content", "").strip()
                if name and content:
                    meta[name.lower()] = content[:500]
        except Exception:
            pass
        return meta

    @staticmethod
    def _extract_headings(tree) -> Dict[str, List[str]]:
        """提取标题层级结构"""
        headings = {}
        for level in range(1, 7):
            tag = f"h{level}"
            try:
                texts = [
                    (e.text_content() or "").strip()
                    for e in tree.xpath(f"//{tag}")
                    if (e.text_content() or "").strip()
                ]
                if texts:
                    headings[tag] = texts
            except Exception:
                pass
        return headings

    def _get_tree(self, html: Optional[str] = None):
        """获取 lxml 解析树"""
        if html is not None:
            try:
                return lxml_html.fromstring(html)
            except Exception:
                return None
        return self._last_tree

    # ── 工具方法 ──────────────────────────────────────────────────

    @staticmethod
    def clean_html(html_content: str) -> str:
        """清洗 HTML（移除脚本、样式、注释）"""
        try:
            tree = lxml_html.fromstring(html_content)
            for elem in tree.xpath("//script | //style | //noscript | //iframe | //comment()"):
                elem.getparent().remove(elem)
            return lxml_html.tostring(tree, encoding="unicode", method="html")
        except Exception:
            return html_content

    @staticmethod
    def extract_text_from_html(html_content: str, max_length: int = 50000) -> str:
        """快速从 HTML 中提取纯文本"""
        try:
            tree = lxml_html.fromstring(html_content)
            text = tree.text_content() if hasattr(tree, "text_content") else ""
            text = re.sub(r"\s+", " ", text).strip()
            return text[:max_length]
        except Exception:
            # fallback: 简单正则
            text = re.sub(r"<[^>]+>", " ", html_content)
            text = re.sub(r"\s+", " ", text).strip()
            return unescape(text[:max_length])

    def get_stats(self) -> dict:
        """获取解析统计"""
        return {
            "last_url": self._last_url,
            "last_html_length": len(self._last_html),
        }
