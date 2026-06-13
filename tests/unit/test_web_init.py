"""
Web 模块初始化测试
"""

import pytest

from agent.web import (
    HttpClient,
    Scraper,
    SearchEngine,
    DataProcessor,
    CrawlerController,
    BrowserAgent,
)


class TestWebModuleInit:
    """测试 Web 模块导入和初始化"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_import_http_client(self):
        """测试导入 HttpClient"""
        assert HttpClient is not None

    @pytest.mark.unit
    @pytest.mark.p1
    def test_import_scraper(self):
        """测试导入 Scraper"""
        assert Scraper is not None

    @pytest.mark.unit
    @pytest.mark.p1
    def test_import_search_engine(self):
        """测试导入 SearchEngine"""
        assert SearchEngine is not None

    @pytest.mark.unit
    @pytest.mark.p1
    def test_import_data_processor(self):
        """测试导入 DataProcessor"""
        assert DataProcessor is not None

    @pytest.mark.unit
    @pytest.mark.p1
    def test_import_crawler_controller(self):
        """测试导入 CrawlerController"""
        assert CrawlerController is not None

    @pytest.mark.unit
    @pytest.mark.p1
    def test_import_browser_agent(self):
        """测试导入 BrowserAgent"""
        assert BrowserAgent is not None

    @pytest.mark.unit
    @pytest.mark.p1
    def test_all_exports(self):
        """测试 __all__ 导出"""
        from agent.web import __all__
        
        expected = [
            "HttpClient",
            "Scraper",
            "SearchEngine",
            "DataProcessor",
            "CrawlerController",
            "BrowserAgent",
        ]
        
        for name in expected:
            assert name in __all__