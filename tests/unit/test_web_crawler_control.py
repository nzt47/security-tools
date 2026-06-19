"""CrawlerController 单元测试"""
import pytest
import time
from unittest.mock import patch, MagicMock

from agent.web.crawler_control import CrawlerController, DEFAULT_USER_AGENTS


class TestCrawlerController:
    """测试爬虫控制器"""

    def test_crawler_controller_init_default(self):
        """测试默认初始化"""
        controller = CrawlerController()
        
        assert controller._default_delay == 1.0
        assert len(controller._ua_list) == len(DEFAULT_USER_AGENTS)
        assert controller._proxies == []
        assert controller._max_retries == 3

    def test_crawler_controller_init_custom_config(self):
        """测试自定义配置初始化"""
        config = {
            "default_delay": 2.0,
            "max_retries": 5,
            "respect_robots": True
        }
        controller = CrawlerController(config)
        
        assert controller._default_delay == 2.0
        assert controller._max_retries == 5
        assert controller._respect_robots is True

    def test_wait_if_needed(self):
        """测试限速等待"""
        controller = CrawlerController({"default_delay": 0.1})
        
        start = time.time()
        controller.wait_if_needed("https://example.com")
        elapsed = time.time() - start
        
        # 第一次请求不应该等待
        assert elapsed < 0.05
        
        # 第二次请求应该等待
        start = time.time()
        controller.wait_if_needed("https://example.com")
        elapsed = time.time() - start
        
        assert elapsed >= 0.08  # 带抖动的延迟

    def test_acquire(self):
        """测试获取请求配置"""
        controller = CrawlerController()
        
        result = controller.acquire("https://example.com")
        
        assert "headers" in result
        assert "User-Agent" in result["headers"]
        assert "proxies" in result
        assert result["headers"]["User-Agent"] in DEFAULT_USER_AGENTS

    def test_get_user_agent(self):
        """测试获取 User-Agent"""
        controller = CrawlerController()
        
        ua = controller.get_user_agent()
        
        assert ua is not None
        assert isinstance(ua, str)
        assert ua in DEFAULT_USER_AGENTS

    def test_get_user_agent_force(self):
        """测试强制 User-Agent"""
        controller = CrawlerController({"force_user_agent": "Test-UA"})
        
        ua = controller.get_user_agent()
        
        assert ua == "Test-UA"

    def test_rotate_ua(self):
        """测试轮换 User-Agent"""
        controller = CrawlerController()
        original_ua = controller.get_user_agent()
        
        controller._rotate_ua()
        
        new_ua = controller.get_user_agent()
        assert new_ua in DEFAULT_USER_AGENTS

    def test_set_user_agents(self):
        """测试设置自定义 User-Agent 列表"""
        custom_agents = ["Custom UA 1", "Custom UA 2"]
        controller = CrawlerController()
        
        controller.set_user_agents(custom_agents)
        
        assert len(controller._ua_list) == 2
        assert controller.get_user_agent() in custom_agents

    def test_add_user_agent(self):
        """测试添加 User-Agent"""
        controller = CrawlerController()
        original_count = len(controller._ua_list)
        
        controller.add_user_agent("New-UA")
        
        assert len(controller._ua_list) == original_count + 1
        assert "New-UA" in controller._ua_list

    def test_get_proxy_empty(self):
        """测试获取代理（空列表）"""
        controller = CrawlerController()
        
        proxy = controller.get_proxy()
        
        assert proxy is None

    def test_get_proxy_with_proxies(self):
        """测试获取代理（有代理列表）"""
        controller = CrawlerController({"proxies": ["http://proxy1:8080", "http://proxy2:8080"]})
        
        proxy = controller.get_proxy()
        
        assert proxy == "http://proxy1:8080"

    def test_rotate_proxy(self):
        """测试轮换代理"""
        controller = CrawlerController({"proxies": ["http://proxy1:8080", "http://proxy2:8080"]})
        
        assert controller.get_proxy() == "http://proxy1:8080"
        controller._rotate_proxy()
        assert controller.get_proxy() == "http://proxy2:8080"

    def test_set_proxies(self):
        """测试设置代理列表"""
        proxies = ["http://proxy1:8080", "http://proxy2:8080"]
        controller = CrawlerController()
        
        controller.set_proxies(proxies)
        
        assert len(controller._proxies) == 2
        assert controller.get_proxy() == "http://proxy1:8080"

    def test_add_proxy(self):
        """测试添加代理"""
        controller = CrawlerController()
        
        controller.add_proxy("http://proxy1:8080")
        
        assert len(controller._proxies) == 1
        assert "http://proxy1:8080" in controller._proxies

    def test_remove_proxy(self):
        """测试移除代理"""
        controller = CrawlerController({"proxies": ["http://proxy1:8080"]})
        
        controller.remove_proxy("http://proxy1:8080")
        
        assert len(controller._proxies) == 0

    def test_report_result_success(self):
        """测试报告成功结果"""
        controller = CrawlerController()
        
        controller.report_result("https://example.com", success=True)
        
        assert controller._stats["requests_made"] == 0  # acquire 才增加
        assert controller._stats["retries"] == 0

    def test_report_result_failure_429(self):
        """测试报告失败结果（429 被限速）"""
        controller = CrawlerController()
        
        controller.report_result("https://example.com", success=False, status_code=429)
        
        assert controller._stats["retries"] == 1
        assert controller._domain_delays["example.com"] >= 2.0  # 延迟加倍

    def test_report_result_failure_403(self):
        """测试报告失败结果（403 被屏蔽）"""
        controller = CrawlerController({"proxies": ["http://proxy1:8080"]})
        original_ua_index = controller._ua_index
        
        controller.report_result("https://example.com", success=False, status_code=403)
        
        assert controller._stats["retries"] == 1
        assert controller._stats["blocked_count"] == 1
        assert controller._stats["ua_switches"] >= 1

    def test_should_retry(self):
        """测试是否应该重试"""
        controller = CrawlerController()
        
        assert controller.should_retry(0, {"ok": False, "status_code": 500}) is True
        assert controller.should_retry(0, {"ok": False, "status_code": 429}) is True
        assert controller.should_retry(0, {"ok": False, "status_code": 404}) is False
        assert controller.should_retry(0, {"ok": True}) is False
        assert controller.should_retry(2, {"ok": False}) is False  # 达到最大重试次数

    def test_retry_delay(self):
        """测试重试延迟计算"""
        controller = CrawlerController({"retry_backoff": 1.0})
        
        delay = controller.retry_delay(0)
        assert 0.5 <= delay <= 1.5
        
        delay = controller.retry_delay(1)
        assert 1.0 <= delay <= 3.0

    def test_can_fetch_respect_robots_disabled(self):
        """测试 can_fetch（不遵守 robots.txt）"""
        controller = CrawlerController({"respect_robots": False})
        
        result = controller.can_fetch("https://example.com/path")
        
        assert result is True

    def test_set_domain_delay(self):
        """测试设置域名延迟"""
        controller = CrawlerController()
        
        controller.set_domain_delay("example.com", 5.0)
        
        assert controller.get_domain_delay("example.com") == 5.0

    def test_set_default_delay(self):
        """测试设置默认延迟"""
        controller = CrawlerController()
        
        controller.set_default_delay(3.0)
        
        assert controller._default_delay == 3.0

    def test_get_domain_delay_default(self):
        """测试获取域名延迟（使用默认值）"""
        controller = CrawlerController({"default_delay": 2.0})
        
        delay = controller.get_domain_delay("unknown.com")
        
        assert delay == 2.0

    def test_get_stats(self):
        """测试获取统计信息"""
        controller = CrawlerController()
        
        stats = controller.get_stats()
        
        assert "requests_made" in stats
        assert "retries" in stats
        assert "blocked_count" in stats
        assert "active_proxies" in stats
        assert "user_agents_count" in stats

    def test_reset(self):
        """测试重置状态"""
        controller = CrawlerController({"proxies": ["http://proxy1:8080"]})
        controller._domain_delays["example.com"] = 5.0
        controller._stats["requests_made"] = 10
        
        controller.reset()
        
        assert len(controller._domain_delays) == 0
        assert controller._stats["requests_made"] == 0

    def test_load_proxies_from_file(self, tmp_path):
        """测试从文件加载代理列表"""
        proxy_file = tmp_path / "proxies.txt"
        proxy_file.write_text("http://proxy1:8080\nhttp://proxy2:8080\n# comment\nhttp://proxy3:8080")
        
        controller = CrawlerController()
        count = controller.load_proxies_from_file(str(proxy_file))
        
        assert count == 3
        assert len(controller._proxies) == 3

    def test_load_proxies_from_file_failure(self):
        """测试加载代理文件失败"""
        controller = CrawlerController()
        count = controller.load_proxies_from_file("/nonexistent/path/proxies.txt")
        
        assert count == 0

    @patch("requests.get")
    def test_test_proxy_success(self, mock_get):
        """测试测试代理（成功）"""
        mock_get.return_value.ok = True
        
        controller = CrawlerController()
        result = controller.test_proxy("http://proxy1:8080", timeout=5)
        
        assert result is True
        mock_get.assert_called_once()

    @patch("requests.get")
    def test_test_proxy_failure(self, mock_get):
        """测试测试代理（失败）"""
        mock_get.side_effect = Exception("Connection error")
        
        controller = CrawlerController()
        result = controller.test_proxy("http://proxy1:8080", timeout=5)
        
        assert result is False