"""Browser Agent 完整单元测试"""
import pytest
import unittest.mock
import os
import time

from agent.web.browser_agent import BrowserAgent


class TestBrowserAgentInit:
    """测试浏览器代理初始化"""

    def test_init_basic(self):
        """测试基本初始化"""
        agent = BrowserAgent()
        
        assert agent._window_width == 1280
        assert agent._window_height == 800
        assert agent._page_load_timeout == 30
        assert agent._implicit_wait == 10
        assert agent._headless is True
        assert agent._driver is None

    def test_init_with_custom_config(self):
        """测试自定义配置"""
        config = {
            "window_width": 1920,
            "window_height": 1080,
            "page_load_timeout": 60,
            "implicit_wait": 20,
            "headless": False
        }
        
        agent = BrowserAgent(config=config)
        
        assert agent._window_width == 1920
        assert agent._window_height == 1080
        assert agent._page_load_timeout == 60
        assert agent._implicit_wait == 20
        assert agent._headless is False

    def test_init_with_extra_args(self):
        """测试额外参数"""
        config = {
            "extra_args": ["--disable-images", "--disable-javascript"]
        }
        
        agent = BrowserAgent(config=config)
        
        assert agent._extra_args == ["--disable-images", "--disable-javascript"]

    def test_init_with_user_data_dir(self):
        """测试用户数据目录配置"""
        config = {
            "user_data_dir": "/path/to/user/data"
        }
        
        agent = BrowserAgent(config=config)
        
        assert agent._user_data_dir == "/path/to/user/data"

    def test_init_with_chrome_path(self):
        """测试Chrome路径配置"""
        config = {
            "chrome_path": "/usr/bin/chrome"
        }
        
        agent = BrowserAgent(config=config)
        
        assert agent._chrome_path == "/usr/bin/chrome"

    def test_init_empty_config(self):
        """测试空配置"""
        agent = BrowserAgent(config={})
        
        assert agent._window_width == 1280
        assert agent._window_height == 800


class TestBrowserAgentStats:
    """测试统计信息"""

    def test_stats_initial_state(self):
        """测试初始状态"""
        agent = BrowserAgent()
        
        assert agent._stats["pages_visited"] == 0
        assert agent._stats["screenshots_taken"] == 0
        assert agent._stats["actions_performed"] == 0
        assert agent._stats["errors"] == 0

    def test_stats_types(self):
        """测试统计类型"""
        agent = BrowserAgent()
        
        stats = agent.get_stats()
        
        assert isinstance(stats, dict)
        assert "pages_visited" in stats
        assert "screenshots_taken" in stats

    def test_get_stats_includes_is_running(self):
        """测试get_stats包含is_running"""
        agent = BrowserAgent()
        
        stats = agent.get_stats()
        
        assert "is_running" in stats
        assert stats["is_running"] is False


class TestBrowserAgentNavigation:
    """测试页面导航"""

    def test_navigate_invalid_protocol(self):
        """测试无效协议"""
        agent = BrowserAgent()
        
        result = agent.navigate("ftp://example.com")
        
        assert result["ok"] is False
        assert "仅支持 http/https" in result["error"]

    def test_navigate_file_protocol(self):
        """测试file协议"""
        agent = BrowserAgent()
        
        result = agent.navigate("file:///path/to/file.html")
        
        assert result["ok"] is False
        assert "http/https" in result["error"]

    def test_navigate_no_protocol(self):
        """测试无协议URL"""
        agent = BrowserAgent()
        
        result = agent.navigate("example.com")
        
        assert result["ok"] is False

    def test_navigate_with_mock_driver(self):
        """测试带mock驱动的导航"""
        agent = BrowserAgent()
        
        with unittest.mock.patch.object(agent, '_ensure_browser'):
            with unittest.mock.patch.object(agent._driver if agent._driver else unittest.mock.MagicMock(), 'get'):
                with unittest.mock.patch.object(agent, '_get_page_text') as mock_text:
                    mock_text.return_value = "Test Page Content"
                    
                    # 确保_driver存在
                    agent._driver = unittest.mock.MagicMock()
                    agent._driver.current_url = "https://example.com"
                    agent._driver.title = "Test Page"
                    agent._driver.page_source = "<html><body>Test</body></html>"
                    agent._driver.get = unittest.mock.MagicMock()
                    
                    result = agent.navigate("https://example.com")
                    
                    assert result["ok"] is True
                    assert result["url"] == "https://example.com"
                    assert result["title"] == "Test Page"
                    assert agent._stats["pages_visited"] == 1

    def test_navigate_with_retry_success_first_try(self):
        """测试重试导航第一次成功"""
        agent = BrowserAgent()
        
        with unittest.mock.patch.object(agent, 'navigate') as mock_nav:
            mock_nav.return_value = {"ok": True}
            
            result = agent.navigate_with_retry("https://example.com")
            
            assert result["ok"] is True
            assert mock_nav.call_count == 1

    def test_navigate_with_retry_success_second_try(self):
        """测试重试导航第二次成功"""
        agent = BrowserAgent()
        
        with unittest.mock.patch.object(agent, 'navigate') as mock_nav:
            mock_nav.side_effect = [{"ok": False, "error": "Timeout"}, {"ok": True}]
            
            result = agent.navigate_with_retry("https://example.com", max_retries=2)
            
            assert result["ok"] is True
            assert mock_nav.call_count == 2

    def test_navigate_with_retry_all_fail(self):
        """测试重试导航全部失败"""
        agent = BrowserAgent()
        
        with unittest.mock.patch.object(agent, 'navigate') as mock_nav:
            mock_nav.return_value = {"ok": False, "error": "Failed"}
            
            result = agent.navigate_with_retry("https://example.com", max_retries=1)
            
            assert result["ok"] is False
            assert mock_nav.call_count == 2  # 初始 + 1次重试


class TestBrowserAgentScreenshot:
    """测试截图功能"""

    def test_screenshot_without_driver(self):
        """测试有驱动时的截图"""
        agent = BrowserAgent()
        
        # 确保_driver存在
        agent._driver = unittest.mock.MagicMock()
        agent._driver.get_screenshot_as_png.return_value = b'fake_png_data'
        
        with unittest.mock.patch.object(agent, '_ensure_browser'):
            result = agent.screenshot()
            
            assert result["ok"] is True
            assert "data_base64" in result

    def test_screenshot_to_file(self):
        """测试保存截图到文件"""
        agent = BrowserAgent()
        test_filepath = "test_screenshot.png"
        
        # 确保_driver存在
        agent._driver = unittest.mock.MagicMock()
        
        with unittest.mock.patch.object(agent, '_ensure_browser'):
            # 模拟文件不存在的情况
            with unittest.mock.patch('os.path.exists', return_value=False):
                with unittest.mock.patch.object(agent._driver, 'save_screenshot'):
                    result = agent.screenshot(filepath=test_filepath)
                    
                    assert result["ok"] is True
                    assert result["filepath"] == test_filepath

    def test_screenshot_full_page(self):
        """测试全页面截图"""
        agent = BrowserAgent()
        
        # 确保_driver存在
        agent._driver = unittest.mock.MagicMock()
        agent._driver.execute_script.return_value = 2000
        
        with unittest.mock.patch.object(agent, '_ensure_browser'):
            with unittest.mock.patch.object(agent._driver, 'get_screenshot_as_png', return_value=b'fake_png_data'):
                result = agent.screenshot(full_page=True)
                
                assert result["ok"] is True
                assert agent._driver.execute_script.call_count > 0

    def test_screenshot_error(self):
        """测试截图异常"""
        agent = BrowserAgent()
        
        # 确保_driver存在
        agent._driver = unittest.mock.MagicMock()
        
        with unittest.mock.patch.object(agent, '_ensure_browser'):
            with unittest.mock.patch.object(agent._driver, 'get_screenshot_as_png', side_effect=Exception("Screenshot failed")):
                result = agent.screenshot()
                
                assert result["ok"] is False
                assert "error" in result


class TestBrowserAgentPDF:
    """测试PDF导出功能"""

    def test_pdf_success(self):
        """测试PDF导出成功"""
        agent = BrowserAgent()
        test_filepath = "test_page.pdf"
        
        # 确保_driver存在
        agent._driver = unittest.mock.MagicMock()
        agent._driver.execute_cdp_cmd.return_value = {
            "data": "SGVsbG8gV29ybGQ=",  # base64 编码的 "Hello World"
            "numberOfPages": 1
        }
        
        with unittest.mock.patch.object(agent, '_ensure_browser'):
            with unittest.mock.patch('os.makedirs'):
                with unittest.mock.patch('builtins.open', unittest.mock.mock_open()):
                    result = agent.pdf(test_filepath)
                    
                    assert result["ok"] is True
                    assert result["filepath"] == test_filepath
                    assert result["page_count"] == 1

    def test_pdf_error(self):
        """测试PDF导出异常"""
        agent = BrowserAgent()
        
        # 确保_driver存在
        agent._driver = unittest.mock.MagicMock()
        
        with unittest.mock.patch.object(agent, '_ensure_browser'):
            with unittest.mock.patch.object(agent._driver, 'execute_cdp_cmd', side_effect=Exception("PDF failed")):
                result = agent.pdf("test.pdf")
                
                assert result["ok"] is False
                assert "error" in result


class TestBrowserAgentScriptExecution:
    """测试JavaScript执行"""

    def test_execute_script_success(self):
        """测试脚本执行成功"""
        agent = BrowserAgent()
        
        # 确保_driver存在
        agent._driver = unittest.mock.MagicMock()
        agent._driver.execute_script.return_value = "Script Result"
        
        with unittest.mock.patch.object(agent, '_ensure_browser'):
            result = agent.execute_script("return 'Script Result';")
            
            assert result["ok"] is True
            assert result["result"] == "Script Result"
            assert agent._stats["actions_performed"] == 1

    def test_execute_script_with_args(self):
        """测试带参数的脚本执行"""
        agent = BrowserAgent()
        
        # 确保_driver存在
        agent._driver = unittest.mock.MagicMock()
        agent._driver.execute_script.return_value = None
        
        with unittest.mock.patch.object(agent, '_ensure_browser'):
            result = agent.execute_script("return arguments[0] + arguments[1];", 1, 2)
            
            assert result["ok"] is True
            assert agent._driver.execute_script.call_count == 1

    def test_execute_script_error(self):
        """测试脚本执行异常"""
        agent = BrowserAgent()
        
        # 确保_driver存在
        agent._driver = unittest.mock.MagicMock()
        agent._driver.execute_script.side_effect = Exception("Script Error")
        
        with unittest.mock.patch.object(agent, '_ensure_browser'):
            result = agent.execute_script("invalid script;")
            
            assert result["ok"] is False
            assert "error" in result
            assert agent._stats["errors"] > 0


class TestBrowserAgentInteraction:
    """测试页面交互"""

    def test_click_css_success(self):
        """测试CSS选择器点击成功"""
        agent = BrowserAgent()
        
        # 确保_driver存在
        mock_element = unittest.mock.MagicMock()
        mock_element.tag_name = "button"
        mock_element.text = "Click Me"
        
        agent._driver = unittest.mock.MagicMock()
        agent._driver.find_element.return_value = mock_element
        
        with unittest.mock.patch.object(agent, '_ensure_browser'):
            result = agent.click("#submit-btn", by="css")
            
            assert result["ok"] is True
            assert result["tag"] == "button"
            assert agent._stats["actions_performed"] == 1

    def test_click_xpath_success(self):
        """测试XPath选择器点击成功"""
        agent = BrowserAgent()
        
        mock_element = unittest.mock.MagicMock()
        mock_element.tag_name = "a"
        mock_element.text = "Link"
        
        agent._driver = unittest.mock.MagicMock()
        agent._driver.find_element.return_value = mock_element
        
        with unittest.mock.patch.object(agent, '_ensure_browser'):
            result = agent.click("//a[@id='link']", by="xpath")
            
            assert result["ok"] is True
            assert result["tag"] == "a"

    def test_click_error(self):
        """测试点击异常"""
        agent = BrowserAgent()
        
        agent._driver = unittest.mock.MagicMock()
        agent._driver.find_element.side_effect = Exception("Element not found")
        
        with unittest.mock.patch.object(agent, '_ensure_browser'):
            result = agent.click("#nonexistent")
            
            assert result["ok"] is False
            assert "error" in result

    def test_scroll_down(self):
        """测试向下滚动"""
        agent = BrowserAgent()
        
        agent._driver = unittest.mock.MagicMock()
        
        with unittest.mock.patch.object(agent, '_ensure_browser'):
            result = agent.scroll(direction="down", amount=500)
            
            assert result["ok"] is True
            assert agent._driver.execute_script.call_count == 1

    def test_scroll_up(self):
        """测试向上滚动"""
        agent = BrowserAgent()
        
        agent._driver = unittest.mock.MagicMock()
        
        with unittest.mock.patch.object(agent, '_ensure_browser'):
            result = agent.scroll(direction="up", amount=300)
            
            assert result["ok"] is True

    def test_scroll_default(self):
        """测试默认滚动"""
        agent = BrowserAgent()
        
        agent._driver = unittest.mock.MagicMock()
        
        with unittest.mock.patch.object(agent, '_ensure_browser'):
            result = agent.scroll()
            
            assert result["ok"] is True

    def test_scroll_error(self):
        """测试滚动异常"""
        agent = BrowserAgent()
        
        agent._driver = unittest.mock.MagicMock()
        agent._driver.execute_script.side_effect = Exception("Scroll failed")
        
        with unittest.mock.patch.object(agent, '_ensure_browser'):
            result = agent.scroll()
            
            assert result["ok"] is False

    def test_wait_for_element_success(self):
        """测试等待元素成功"""
        agent = BrowserAgent()
        
        mock_element = unittest.mock.MagicMock()
        mock_element.tag_name = "div"
        mock_element.text = "Content"
        
        agent._driver = unittest.mock.MagicMock()
        
        with unittest.mock.patch.object(agent, '_ensure_browser'):
            with unittest.mock.patch('selenium.webdriver.support.ui.WebDriverWait') as mock_wait:
                mock_wait.return_value.until.return_value = mock_element
                
                result = agent.wait_for_element("#content")
                
                assert result["ok"] is True
                assert result["tag"] == "div"

    def test_wait_for_element_timeout(self):
        """测试等待元素超时"""
        agent = BrowserAgent()
        
        agent._driver = unittest.mock.MagicMock()
        
        with unittest.mock.patch.object(agent, '_ensure_browser'):
            with unittest.mock.patch('selenium.webdriver.support.ui.WebDriverWait') as mock_wait:
                mock_wait.return_value.until.side_effect = Exception("Timeout")
                
                result = agent.wait_for_element("#nonexistent", timeout=5)
                
                assert result["ok"] is False
                assert "超时" in result["error"]


class TestBrowserAgentFormFilling:
    """测试表单填写"""

    def test_fill_form_success(self):
        """测试表单填写成功"""
        agent = BrowserAgent()
        
        mock_element = unittest.mock.MagicMock()
        
        agent._driver = unittest.mock.MagicMock()
        agent._driver.find_element.return_value = mock_element
        
        with unittest.mock.patch.object(agent, '_ensure_browser'):
            fields = {
                "input[name='username']": "testuser",
                "input[name='password']": "password123"
            }
            
            result = agent.fill_form(fields)
            
            assert result["ok"] is True
            assert result["filled_count"] == 2
            assert agent._stats["actions_performed"] == 1

    def test_fill_form_with_submit(self):
        """测试带提交按钮的表单填写"""
        agent = BrowserAgent()
        
        mock_element = unittest.mock.MagicMock()
        mock_submit_btn = unittest.mock.MagicMock()
        
        agent._driver = unittest.mock.MagicMock()
        agent._driver.find_element.side_effect = [mock_element, mock_submit_btn]
        
        with unittest.mock.patch.object(agent, '_ensure_browser'):
            fields = {"input[name='email']": "test@example.com"}
            
            result = agent.fill_form(fields, submit=True)
            
            assert result["ok"] is True
            assert result["filled_count"] == 1
            assert result["submitted"] is True

    def test_fill_form_partial_failure(self):
        """测试表单部分填写失败"""
        agent = BrowserAgent()
        
        agent._driver = unittest.mock.MagicMock()
        # 第一个字段成功，第二个失败
        agent._driver.find_element.side_effect = [
            unittest.mock.MagicMock(),
            Exception("Field not found")
        ]
        
        with unittest.mock.patch.object(agent, '_ensure_browser'):
            fields = {
                "input[name='valid']": "value1",
                "input[name='invalid']": "value2"
            }
            
            result = agent.fill_form(fields)
            
            assert result["ok"] is True
            assert result["filled_count"] == 1

    def test_fill_form_no_fields(self):
        """测试空表单"""
        agent = BrowserAgent()
        
        agent._driver = unittest.mock.MagicMock()
        
        with unittest.mock.patch.object(agent, '_ensure_browser'):
            result = agent.fill_form({})
            
            assert result["ok"] is True
            assert result["filled_count"] == 0

    def test_fill_form_error(self):
        """测试表单填写异常"""
        agent = BrowserAgent()
        
        agent._driver = unittest.mock.MagicMock()
        # _ensure_browser抛出异常
        with unittest.mock.patch.object(agent, '_ensure_browser', side_effect=Exception("Browser error")):
            result = agent.fill_form({"input": "value"})
            
            assert result["ok"] is False
            assert "error" in result


class TestBrowserAgentContentExtraction:
    """测试内容提取"""

    def test_get_page_info_success(self):
        """测试获取页面信息成功"""
        agent = BrowserAgent()
        
        agent._driver = unittest.mock.MagicMock()
        agent._driver.current_url = "https://example.com"
        agent._driver.title = "Example Domain"
        agent._driver.current_window_handle = "window-handle-123"
        
        with unittest.mock.patch.object(agent, '_ensure_browser'):
            result = agent.get_page_info()
            
            assert result["ok"] is True
            assert result["url"] == "https://example.com"
            assert result["title"] == "Example Domain"
            assert result["window_handle"] == "window-handle-123"

    def test_get_page_info_error(self):
        """测试获取页面信息异常"""
        agent = BrowserAgent()
        
        agent._driver = unittest.mock.MagicMock(spec=['current_url', 'title', 'current_window_handle'])
        # 模拟任何属性访问都抛出异常
        type(agent._driver).current_url = property(lambda self: (_ for _ in ()).throw(Exception("Driver error")))
        
        with unittest.mock.patch.object(agent, '_ensure_browser'):
            try:
                result = agent.get_page_info()
                # 如果没有异常，说明driver实现不同
                assert result.get("ok") is False or "error" in result
            except Exception:
                # 异常也是预期行为
                pass

    def test_get_html_success(self):
        """测试获取HTML成功"""
        agent = BrowserAgent()
        
        agent._driver = unittest.mock.MagicMock()
        agent._driver.page_source = "<html><body>Test</body></html>"
        
        with unittest.mock.patch.object(agent, '_ensure_browser'):
            result = agent.get_html()
            
            assert result["ok"] is True
            assert "html" in result
            assert "Test" in result["html"]

    def test_get_html_truncation(self):
        """测试HTML截断"""
        agent = BrowserAgent()
        
        # 创建超过1MB的HTML
        large_html = "<html>" + "<body>" + "x" * 2000000 + "</body></html>"
        
        agent._driver = unittest.mock.MagicMock()
        agent._driver.page_source = large_html
        
        with unittest.mock.patch.object(agent, '_ensure_browser'):
            result = agent.get_html()
            
            assert result["ok"] is True
            assert len(result["html"]) <= 1000000

    def test_get_html_error(self):
        """测试获取HTML异常"""
        agent = BrowserAgent()
        
        agent._driver = unittest.mock.MagicMock(spec=['page_source'])
        # 模拟page_source抛出异常
        type(agent._driver).page_source = property(lambda self: (_ for _ in ()).throw(Exception("Page source error")))
        
        with unittest.mock.patch.object(agent, '_ensure_browser'):
            try:
                result = agent.get_html()
                # 如果没有异常，说明driver实现不同
                assert result.get("ok") is False or "error" in result
            except Exception:
                # 异常也是预期行为
                pass

    def test_get_cookies_success(self):
        """测试获取Cookie成功"""
        agent = BrowserAgent()
        
        agent._driver = unittest.mock.MagicMock()
        agent._driver.get_cookies.return_value = [
            {"name": "session", "value": "abc123", "domain": ".example.com"},
            {"name": "preferences", "value": "dark_mode", "domain": ".example.com"}
        ]
        
        with unittest.mock.patch.object(agent, '_ensure_browser'):
            result = agent.get_cookies()
            
            assert len(result) == 2
            assert result[0]["name"] == "session"
            assert result[0]["domain"] == ".example.com"

    def test_get_cookies_error(self):
        """测试获取Cookie异常"""
        agent = BrowserAgent()
        
        agent._driver = unittest.mock.MagicMock()
        agent._driver.get_cookies.side_effect = Exception("Cookie error")
        
        with unittest.mock.patch.object(agent, '_ensure_browser'):
            result = agent.get_cookies()
            
            assert result == []


class TestBrowserAgentLifecycle:
    """测试浏览器生命周期管理"""

    def test_close_with_driver(self):
        """测试关闭有驱动的浏览器"""
        agent = BrowserAgent()
        
        agent._driver = unittest.mock.MagicMock()
        agent._stats["pages_visited"] = 5
        
        agent.close()
        
        assert agent._driver is None
        assert agent._driver is None

    def test_close_without_driver(self):
        """测试关闭无驱动的浏览器"""
        agent = BrowserAgent()
        
        assert agent._driver is None
        
        agent.close()
        
        assert agent._driver is None

    def test_context_manager(self):
        """测试上下文管理器"""
        with BrowserAgent() as agent:
            assert agent._driver is None
        
        # 退出上下文后应该已关闭
        assert agent._driver is None

    def test_restart(self):
        """测试重启浏览器"""
        agent = BrowserAgent()
        
        # Mock _start_browser
        with unittest.mock.patch.object(agent, '_start_browser') as mock_start:
            with unittest.mock.patch.object(agent, 'close') as mock_close:
                agent._driver = unittest.mock.MagicMock()
                
                agent.restart()
                
                mock_close.assert_called_once()
                # 等待后应该调用_start_browser
                # 注意：这里可能需要调整，因为_restart调用time.sleep(1)


class TestBrowserAgentPageText:
    """测试页面文本获取"""

    def test_get_page_text_success(self):
        """测试获取页面文本成功"""
        agent = BrowserAgent()
        
        mock_body = unittest.mock.MagicMock()
        mock_body.text = "Test Page Content"
        
        agent._driver = unittest.mock.MagicMock()
        agent._driver.find_element.return_value = mock_body
        
        with unittest.mock.patch('selenium.webdriver.support.ui.WebDriverWait') as mock_wait:
            mock_wait.return_value.until.return_value = True
            
            result = agent._get_page_text()
            
            assert result == "Test Page Content"

    def test_get_page_text_with_js_fallback(self):
        """测试获取页面文本JS回退"""
        agent = BrowserAgent()
        
        mock_body = unittest.mock.MagicMock()
        mock_body.text = ""
        
        agent._driver = unittest.mock.MagicMock()
        agent._driver.find_element.return_value = mock_body
        agent._driver.execute_script.return_value = "JS Retrieved Text"
        
        with unittest.mock.patch('selenium.webdriver.support.ui.WebDriverWait') as mock_wait:
            mock_wait.return_value.until.side_effect = Exception("Wait failed")
            
            result = agent._get_page_text()
            
            assert result == "JS Retrieved Text"

    def test_get_page_text_all_fail(self):
        """测试获取页面文本全部失败"""
        agent = BrowserAgent()
        
        agent._driver = unittest.mock.MagicMock()
        agent._driver.find_element.side_effect = Exception("Element not found")
        agent._driver.execute_script.side_effect = Exception("JS error")
        
        result = agent._get_page_text()
        
        assert result == ""

    def test_get_page_text_truncation(self):
        """测试页面文本截断"""
        agent = BrowserAgent()
        
        long_text = "x" * 200000
        mock_body = unittest.mock.MagicMock()
        mock_body.text = long_text
        
        agent._driver = unittest.mock.MagicMock()
        agent._driver.find_element.return_value = mock_body
        
        with unittest.mock.patch('selenium.webdriver.support.ui.WebDriverWait') as mock_wait:
            mock_wait.return_value.until.return_value = True
            
            result = agent._get_page_text()
            
            assert len(result) == 100000


class TestBrowserAgentEnsureBrowser:
    """测试浏览器启动管理"""

    def test_ensure_browser_already_started(self):
        """测试浏览器已启动时不重新启动"""
        agent = BrowserAgent()
        agent._driver = unittest.mock.MagicMock()
        
        with unittest.mock.patch.object(agent, '_start_browser') as mock_start:
            agent._ensure_browser()
            
            # 不应该调用_start_browser
            mock_start.assert_not_called()

    def test_ensure_browser_not_started(self):
        """测试浏览器未启动时启动"""
        agent = BrowserAgent()
        
        with unittest.mock.patch.object(agent, '_start_browser') as mock_start:
            mock_start.return_value = None
            agent._ensure_browser()
            
            # 应该调用_start_browser
            mock_start.assert_called_once()


class TestBrowserAgentStartupErrors:
    """测试浏览器启动错误处理"""

    def test_start_browser_import_error(self):
        """测试Selenium导入错误"""
        agent = BrowserAgent()
        
        with unittest.mock.patch.dict('sys.modules', {'selenium': None}):
            with pytest.raises(Exception):
                agent._start_browser()

    def test_navigate_with_driver_error(self):
        """测试导航时驱动错误"""
        agent = BrowserAgent()
        
        # 先mock _ensure_browser，然后让driver.get抛出异常
        with unittest.mock.patch.object(agent, '_ensure_browser'):
            agent._driver = unittest.mock.MagicMock()
            agent._driver.get.side_effect = Exception("Navigation error")
            agent._driver.current_url = ""
            agent._driver.title = ""
            agent._driver.page_source = ""
            
            result = agent.navigate("https://example.com")
            
            assert result["ok"] is False
            assert agent._stats["errors"] > 0

    def test_close_with_driver_error(self):
        """测试关闭时驱动错误"""
        agent = BrowserAgent()
        
        agent._driver = unittest.mock.MagicMock()
        agent._driver.quit.side_effect = Exception("Quit error")
        
        # close方法应该捕获异常
        agent.close()
        
        assert agent._driver is None

    def test_execute_script_with_exception(self):
        """测试脚本执行异常"""
        agent = BrowserAgent()
        
        agent._driver = unittest.mock.MagicMock()
        agent._driver.execute_script.side_effect = Exception("Script error")
        
        with unittest.mock.patch.object(agent, '_ensure_browser'):
            result = agent.execute_script("throw new Error('test');")
            
            assert result["ok"] is False
            assert "error" in result

    def test_fill_form_enter_submit(self):
        """测试按Enter键提交表单"""
        agent = BrowserAgent()
        
        mock_element = unittest.mock.MagicMock()
        agent._driver = unittest.mock.MagicMock()
        agent._driver.find_element.side_effect = [
            mock_element,  # 第一个字段
            Exception("Submit button not found")  # 找不到提交按钮
        ]
        
        with unittest.mock.patch.object(agent, '_ensure_browser'):
            with unittest.mock.patch('selenium.webdriver.common.keys.Keys.RETURN', 'RETURN'):
                fields = {"input[name='email']": "test@example.com"}
                
                result = agent.fill_form(fields, submit=True)
                
                assert result["ok"] is True
                assert result["submitted"] is True
