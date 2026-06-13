"""
浏览器自动化代理测试
"""

import pytest
from unittest.mock import Mock, patch, MagicMock

from agent.web.browser_agent import BrowserAgent


class TestBrowserAgentInit:
    """测试浏览器代理初始化"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_init_default_config(self):
        """测试默认配置初始化"""
        agent = BrowserAgent()
        
        assert agent._window_width == 1280
        assert agent._window_height == 800
        assert agent._page_load_timeout == 30
        assert agent._implicit_wait == 10
        assert agent._headless is True

    @pytest.mark.unit
    @pytest.mark.p1
    def test_init_custom_config(self):
        """测试自定义配置初始化"""
        config = {
            "window_width": 1920,
            "window_height": 1080,
            "page_load_timeout": 60,
            "headless": False,
        }
        agent = BrowserAgent(config=config)
        
        assert agent._window_width == 1920
        assert agent._window_height == 1080
        assert agent._page_load_timeout == 60
        assert agent._headless is False

    @pytest.mark.unit
    @pytest.mark.p1
    def test_init_with_chrome_path(self):
        """测试自定义 Chrome 路径"""
        config = {"chrome_path": "/custom/chrome"}
        agent = BrowserAgent(config=config)
        
        assert agent._chrome_path == "/custom/chrome"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_init_with_user_data_dir(self):
        """测试用户数据目录"""
        config = {"user_data_dir": "/tmp/chrome_data"}
        agent = BrowserAgent(config=config)
        
        assert agent._user_data_dir == "/tmp/chrome_data"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_initial_stats(self):
        """测试初始统计"""
        agent = BrowserAgent()
        
        assert agent._stats["pages_visited"] == 0
        assert agent._stats["screenshots_taken"] == 0
        assert agent._stats["actions_performed"] == 0
        assert agent._stats["errors"] == 0
        assert agent._stats["started_at"] is None

    @pytest.mark.unit
    @pytest.mark.p1
    def test_driver_none_initially(self):
        """测试初始驱动器为空"""
        agent = BrowserAgent()
        
        assert agent._driver is None


class TestBrowserStats:
    """测试浏览器统计"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_get_stats(self):
        """测试获取统计"""
        agent = BrowserAgent()
        
        stats = agent.get_stats()
        
        assert "pages_visited" in stats
        assert "screenshots_taken" in stats
        assert "actions_performed" in stats
        assert "errors" in stats

    @pytest.mark.unit
    @pytest.mark.p1
    def test_stats_update(self):
        """测试统计更新"""
        agent = BrowserAgent()
        
        # 手动更新统计
        agent._stats["pages_visited"] = 5
        
        stats = agent.get_stats()
        assert stats["pages_visited"] == 5


class TestBrowserMethods:
    """测试浏览器方法存在"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_has_navigate_method(self):
        """测试导航方法存在"""
        agent = BrowserAgent()
        assert hasattr(agent, "navigate")

    @pytest.mark.unit
    @pytest.mark.p1
    def test_has_screenshot_method(self):
        """测试截图方法存在"""
        agent = BrowserAgent()
        assert hasattr(agent, "screenshot")

    @pytest.mark.unit
    @pytest.mark.p1
    def test_has_get_html_method(self):
        """测试获取 HTML 方法存在"""
        agent = BrowserAgent()
        assert hasattr(agent, "get_html")

    @pytest.mark.unit
    @pytest.mark.p1
    def test_has_click_method(self):
        """测试点击方法存在"""
        agent = BrowserAgent()
        assert hasattr(agent, "click")

    @pytest.mark.unit
    @pytest.mark.p1
    def test_has_fill_form_method(self):
        """测试填写表单方法存在"""
        agent = BrowserAgent()
        assert hasattr(agent, "fill_form")

    @pytest.mark.unit
    @pytest.mark.p1
    def test_has_execute_js_method(self):
        """测试执行 JS 方法存在"""
        agent = BrowserAgent()
        # 方法名可能是 run_js 或其他
        assert hasattr(agent, "run_js") or hasattr(agent, "execute_script")

    @pytest.mark.unit
    @pytest.mark.p1
    def test_has_close_method(self):
        """测试关闭方法存在"""
        agent = BrowserAgent()
        assert hasattr(agent, "close")