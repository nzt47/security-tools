"""
Mock 浏览器工厂类 - 用于测试场景
提供各种浏览器状态的 Mock 对象
"""
from unittest.mock import MagicMock, PropertyMock


class MockBrowserFactory:
    """Mock 浏览器工厂类 - 统一模拟各种异常场景"""

    @staticmethod
    def create_success_browser():
        """创建成功初始化的浏览器"""
        browser = MagicMock()
        browser.set_page_load_timeout = MagicMock()
        browser.window_handles = ["window1"]
        return browser

    @staticmethod
    def create_timeout_error_browser():
        """创建设置超时失败的浏览器"""
        browser = MagicMock()
        browser.set_page_load_timeout.side_effect = Exception("timeout error")
        browser.quit = MagicMock()
        return browser

    @staticmethod
    def create_window_handles_error_browser():
        """创建获取窗口句柄失败的浏览器"""
        browser = MagicMock()
        browser.set_page_load_timeout = MagicMock()
        # 使用 PropertyMock 模拟属性访问异常
        type(browser).window_handles = PropertyMock(side_effect=Exception("handle error"))
        return browser

    @staticmethod
    def create_chrome_init_error_browser():
        """创建 Chrome 初始化失败的浏览器"""
        browser = MagicMock()
        browser.set_page_load_timeout.side_effect = Exception("timeout error")
        browser.quit = MagicMock()
        return browser

    @staticmethod
    def create_navigate_success_browser():
        """创建导航成功的浏览器"""
        browser = MockBrowserFactory.create_success_browser()
        browser.get = MagicMock()
        browser.title = "Test Page"
        browser.current_url = "http://example.com"
        body = MagicMock()
        body.text = "Page content"
        browser.find_element.return_value = body
        return browser

    @staticmethod
    def create_navigate_error_browser():
        """创建导航失败的浏览器"""
        browser = MockBrowserFactory.create_success_browser()
        browser.get.side_effect = Exception("navigation failed")
        return browser

    @staticmethod
    def create_screenshot_success_browser():
        """创建截图成功的浏览器"""
        browser = MockBrowserFactory.create_success_browser()
        browser.get_screenshot_as_base64.return_value = "base64_screenshot_data"
        return browser

    @staticmethod
    def create_screenshot_error_browser():
        """创建截图失败的浏览器"""
        browser = MockBrowserFactory.create_success_browser()
        browser.get_screenshot_as_base64.side_effect = Exception("screenshot failed")
        return browser

    @staticmethod
    def create_quit_error_browser():
        """创建 quit 失败的浏览器"""
        browser = MagicMock()
        browser.quit.side_effect = Exception("quit failed")
        return browser
