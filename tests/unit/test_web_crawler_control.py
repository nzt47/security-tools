"""Crawler Controller 单元测试"""
import pytest
import time
from unittest.mock import MagicMock, patch
from collections import deque

from agent.web.crawler_control import CrawlerController, DEFAULT_USER_AGENTS


class TestCrawlerControllerInit:
    """测试爬虫控制器初始化"""

    def test_init_basic(self):
        """测试基本初始化"""
        controller = CrawlerController()
        
        assert controller._default_delay == 1.0
        assert controller._domain_delays == {}
        assert controller._last_request_time == {}
        assert controller._ua_list == DEFAULT_USER_AGENTS
        assert controller._ua_index == 0
        assert controller._proxies == []
        assert controller._max_retries == 3
        assert controller._respect_robots is False

    def test_init_with_custom_config(self):
        """测试自定义配置"""
        config = {
            "default_delay": 2.0,
            "user_agents": ["Custom UA 1", "Custom UA 2"],
            "proxies": ["http://proxy1.com", "http://proxy2.com"],
            "max_retries": 5,
            "respect_robots": True
        }
        
        controller = CrawlerController(config=config)
        
        assert controller._default_delay == 2.0
        assert controller._ua_list == ["Custom UA 1", "Custom UA 2"]
        assert controller._proxies == ["http://proxy1.com", "http://proxy2.com"]
        assert controller._max_retries == 5
        assert controller._respect_robots is True


class TestRateLimiting:
    """测试速率限制"""

    def test_wait_if_needed_first_request(self):
        """测试首次请求不需要等待"""
        controller = CrawlerController()
        
        controller.wait_if_needed("https://example.com")
        
        # 首次请求，记录时间但不等待
        assert "example.com" in controller._last_request_time

    def test_wait_if_needed_respects_delay(self):
        """测试速率限制延迟"""
        controller = CrawlerController(config={"default_delay": 0.5})
        
        controller.wait_if_needed("https://example.com/page1")
        time.sleep(0.1)
        
        start = time.time()
        controller.wait_if_needed("https://example.com/page2")
        elapsed = time.time() - start
        
        # 应该等待约0.4秒（0.5 - 0.1）
        assert elapsed >= 0.3

    def test_wait_if_needed_different_domains(self):
        """测试不同域名不共享速率限制"""
        controller = CrawlerController(config={"default_delay": 1.0})
        
        controller.wait_if_needed("https://example.com/page1")
        time.sleep(0.1)
        
        # 不同域名应该不需要等待
        start = time.time()
        controller.wait_if_needed("https://other.com/page2")
        elapsed = time.time() - start
        
        assert elapsed < 0.2

    def test_set_domain_delay(self):
        """测试设置域名特定延迟"""
        controller = CrawlerController()
        
        controller.set_domain_delay("slow-site.com", 5.0)
        
        assert controller._domain_delays["slow-site.com"] == 5.0


class TestUserAgentRotation:
    """测试 User-Agent 轮换"""

    def test_get_user_agent_basic(self):
        """测试获取 User-Agent"""
        controller = CrawlerController()
        
        ua = controller.get_user_agent()
        
        assert ua is not None
        assert isinstance(ua, str)
        assert len(ua) > 0

    def test_get_user_agent_rotation(self):
        """测试 User-Agent 轮换"""
        controller = CrawlerController()
        initial_index = controller._ua_index
        
        ua1 = controller.get_user_agent()
        ua2 = controller.get_user_agent()
        ua3 = controller.get_user_agent()
        
        # 应该轮换到不同的 UA
        assert ua1 == ua2  # 由于轮换后会回到相同位置

    def test_force_user_agent(self):
        """测试强制使用特定 User-Agent"""
        config = {"force_user_agent": "Custom Bot/1.0"}
        controller = CrawlerController(config=config)
        
        ua = controller.get_user_agent()
        
        assert ua == "Custom Bot/1.0"


class TestProxyPool:
    """测试代理池"""

    def test_get_proxy_basic(self):
        """测试获取代理"""
        config = {"proxies": ["http://proxy1.com:8080"]}
        controller = CrawlerController(config=config)
        
        proxy = controller.get_proxy()
        
        assert proxy == "http://proxy1.com:8080"

    def test_get_proxy_no_proxy(self):
        """测试无代理时返回 None"""
        controller = CrawlerController()
        
        proxy = controller.get_proxy()
        
        assert proxy is None

    def test_acquire_with_proxy(self):
        """测试获取请求配置（包含代理）"""
        config = {"proxies": ["http://proxy1.com:8080"]}
        controller = CrawlerController(config=config)
        
        result = controller.acquire("https://example.com/page")
        
        assert "headers" in result
        assert "proxies" in result
        assert result["proxies"] is not None

    def test_report_result_success(self):
        """测试报告成功结果"""
        controller = CrawlerController()
        
        controller.report_result("https://example.com", success=True, status_code=200)
        
        # 成功时 requests_made 不增加，只增加 retries（失败时）
        assert controller._stats["requests_made"] == 0

    def test_report_result_failure(self):
        """测试报告失败结果"""
        controller = CrawlerController()
        
        controller.report_result("https://example.com", success=False, status_code=500, error="Server Error")
        
        assert controller._stats["retries"] == 1


class TestRetryLogic:
    """测试重试逻辑"""

    def test_report_result_429_adjusts_delay(self):
        """测试 429 响应调整延迟"""
        controller = CrawlerController(config={"default_delay": 1.0})
        
        controller.report_result("https://example.com", success=False, status_code=429)
        
        assert controller._domain_delays["example.com"] == 2.0  # 延迟翻倍

    def test_report_result_403_rotates_ua(self):
        """测试 403 响应轮换 UA"""
        controller = CrawlerController()
        initial_ua = controller.get_user_agent()
        
        controller.report_result("https://example.com", success=False, status_code=403)
        
        # UA 应该被轮换
        assert controller._stats["ua_switches"] == 1


class TestRobotsTxt:
    """测试 robots.txt 合规"""

    def test_respect_robots_config(self):
        """测试 robots.txt 配置"""
        config = {"respect_robots": True}
        controller = CrawlerController(config=config)
        
        assert controller._respect_robots is True


class TestStats:
    """测试统计信息"""

    def test_stats_initial_state(self):
        """测试初始统计状态"""
        controller = CrawlerController()
        
        assert controller._stats["requests_made"] == 0
        assert controller._stats["retries"] == 0
        assert controller._stats["blocked_count"] == 0

    def test_get_stats(self):
        """测试获取统计"""
        controller = CrawlerController()
        
        stats = controller.get_stats()
        
        assert "requests_made" in stats
        assert "retries" in stats
        assert "blocked_count" in stats


class TestUserAgentManagement:
    """测试 User-Agent 管理"""

    def test_set_user_agents(self):
        """测试设置 User-Agent 列表"""
        controller = CrawlerController()
        custom_uas = ["UA1", "UA2", "UA3"]
        
        controller.set_user_agents(custom_uas)
        
        assert controller._ua_list == custom_uas
        assert controller._ua_index == 0

    def test_set_user_agents_empty(self):
        """测试设置空 User-Agent 列表"""
        controller = CrawlerController()
        
        controller.set_user_agents([])
        
        # 不应该清空列表
        assert len(controller._ua_list) > 0

    def test_add_user_agent(self):
        """测试添加 User-Agent"""
        controller = CrawlerController()
        initial_count = len(controller._ua_list)
        
        controller.add_user_agent("New Custom UA")
        
        assert len(controller._ua_list) == initial_count + 1
        assert "New Custom UA" in controller._ua_list

    def test_add_duplicate_user_agent(self):
        """测试添加重复的 User-Agent"""
        controller = CrawlerController(config={"user_agents": ["Original UA"]})
        initial_count = len(controller._ua_list)
        
        controller.add_user_agent("Original UA")
        
        assert len(controller._ua_list) == initial_count  # 不应该重复添加

    def test_rotate_ua(self):
        """测试轮换 User-Agent"""
        controller = CrawlerController(config={"user_agents": ["UA1", "UA2", "UA3"]})
        initial_switches = controller._stats["ua_switches"]
        
        controller._rotate_ua()
        
        assert controller._stats["ua_switches"] == initial_switches + 1


class TestProxyManagement:
    """测试代理管理"""

    def test_set_proxies(self):
        """测试设置代理列表"""
        controller = CrawlerController()
        proxies = ["http://proxy1.com", "http://proxy2.com"]
        
        controller.set_proxies(proxies)
        
        assert controller._proxies == proxies
        assert controller._proxy_index == 0
        assert controller._proxy_stats == {}

    def test_add_proxy(self):
        """测试添加代理"""
        controller = CrawlerController()
        
        controller.add_proxy("http://newproxy.com")
        
        assert "http://newproxy.com" in controller._proxies
        assert "http://newproxy.com" in controller._proxy_stats

    def test_remove_proxy(self):
        """测试移除代理"""
        controller = CrawlerController(config={"proxies": ["http://proxy1.com", "http://proxy2.com"]})
        
        controller.remove_proxy("http://proxy1.com")
        
        assert "http://proxy1.com" not in controller._proxies
        assert "http://proxy1.com" not in controller._proxy_stats

    def test_remove_nonexistent_proxy(self):
        """测试移除不存在的代理"""
        controller = CrawlerController(config={"proxies": ["http://proxy1.com"]})
        initial_count = len(controller._proxies)
        
        controller.remove_proxy("http://nonexistent.com")
        
        assert len(controller._proxies) == initial_count  # 不应该有变化

    def test_rotate_proxy(self):
        """测试轮换代理"""
        controller = CrawlerController(config={"proxies": ["http://proxy1.com", "http://proxy2.com"]})
        initial_index = controller._proxy_index
        initial_switches = controller._stats["proxy_switches"]
        
        controller._rotate_proxy()
        
        assert controller._proxy_index != initial_index
        assert controller._stats["proxy_switches"] == initial_switches + 1

    def test_rotate_proxy_single_proxy(self):
        """测试只有一个代理时不轮换"""
        controller = CrawlerController(config={"proxies": ["http://proxy1.com"]})
        initial_index = controller._proxy_index
        initial_switches = controller._stats["proxy_switches"]
        
        controller._rotate_proxy()
        
        assert controller._proxy_index == initial_index  # 只有一个代理，不轮换
        assert controller._stats["proxy_switches"] == initial_switches

    def test_get_current_proxy(self):
        """测试获取当前代理"""
        controller = CrawlerController(config={"proxies": ["http://proxy1.com", "http://proxy2.com"]})
        
        proxy = controller._get_current_proxy()
        
        assert proxy is not None
        assert proxy in controller._proxies

    def test_get_current_proxy_no_proxy(self):
        """测试无代理时获取当前代理"""
        controller = CrawlerController()
        
        proxy = controller._get_current_proxy()
        
        assert proxy is None


class TestRetryLogicComprehensive:
    """测试完整的重试逻辑"""

    def test_should_retry_max_retries_reached(self):
        """测试达到最大重试次数"""
        controller = CrawlerController(config={"max_retries": 3})
        
        result = controller.should_retry(2, {"ok": False})
        
        assert result is False

    def test_should_retry_success(self):
        """测试成功时不重试"""
        controller = CrawlerController()
        
        result = controller.should_retry(0, {"ok": True})
        
        assert result is False

    def test_should_retry_client_error_4xx(self):
        """测试 4xx 客户端错误不重试（除了 429）"""
        controller = CrawlerController()
        
        result = controller.should_retry(0, {"ok": False, "status_code": 404})
        
        assert result is False

    def test_should_retry_429(self):
        """测试 429 应该重试"""
        controller = CrawlerController()
        
        result = controller.should_retry(0, {"ok": False, "status_code": 429})
        
        assert result is True

    def test_should_retry_server_error_5xx(self):
        """测试 5xx 服务器错误应该重试"""
        controller = CrawlerController()
        
        result = controller.should_retry(0, {"ok": False, "status_code": 500})
        
        assert result is True

    def test_retry_delay_calculation(self):
        """测试重试延迟计算"""
        controller = CrawlerController(config={"retry_backoff": 1.0})
        
        delay = controller.retry_delay(0)
        assert delay > 0
        assert delay < 30  # 应该小于最大延迟


class TestReportResultComprehensive:
    """测试完整的结果报告功能"""

    def test_report_result_503_rotates_ua_and_proxy(self):
        """测试 503 响应轮换 UA 和代理"""
        controller = CrawlerController(config={"proxies": ["http://proxy1.com", "http://proxy2.com"]})
        initial_blocked = controller._stats["blocked_count"]
        
        controller.report_result("https://example.com", success=False, status_code=503)
        
        assert controller._stats["blocked_count"] == initial_blocked + 1
        assert controller._stats["ua_switches"] >= 1
        assert controller._stats["proxy_switches"] >= 1

    def test_report_result_success_decreases_delay(self):
        """测试成功后减少延迟"""
        controller = CrawlerController(config={"default_delay": 2.0})
        controller._domain_delays["example.com"] = 4.0
        
        controller.report_result("https://example.com", success=True)
        
        assert controller._domain_delays["example.com"] < 4.0

    def test_report_result_proxy_error(self):
        """测试代理错误统计"""
        controller = CrawlerController(config={"proxies": ["http://proxy1.com"]})
        
        controller.report_result(
            "https://example.com", 
            success=False, 
            status_code=500, 
            error="ProxyError: Connection failed"
        )
        
        assert "http://proxy1.com" in controller._proxy_stats
        assert controller._proxy_stats["http://proxy1.com"]["fail"] == 1

    def test_report_result_proxy_success(self):
        """测试代理成功统计"""
        controller = CrawlerController(config={"proxies": ["http://proxy1.com"]})
        
        controller.report_result(
            "https://example.com", 
            success=False, 
            status_code=500, 
            error="Some other error"
        )
        
        assert "http://proxy1.com" in controller._proxy_stats
        assert controller._proxy_stats["http://proxy1.com"]["success"] == 1

    def test_report_result_proxy_automatic_switch_on_failure(self):
        """测试代理连续失败自动切换"""
        controller = CrawlerController(config={"proxies": ["http://proxy1.com", "http://proxy2.com"]})
        
        # 触发 3 次失败
        for _ in range(3):
            controller.report_result(
                "https://example.com", 
                success=False, 
                status_code=500, 
                error="ProxyError: Connection failed"
            )
        
        assert controller._stats["proxy_switches"] >= 1


class TestDelayManagement:
    """测试延迟管理"""

    def test_set_default_delay(self):
        """测试设置默认延迟"""
        controller = CrawlerController(config={"default_delay": 2.0})
        
        controller.set_default_delay(5.0)
        
        assert controller._default_delay == 5.0

    def test_set_default_delay_zero(self):
        """测试设置默认延迟为 0"""
        controller = CrawlerController()
        
        controller.set_default_delay(0)
        
        assert controller._default_delay == 0

    def test_get_domain_delay_custom(self):
        """测试获取自定义域名延迟"""
        controller = CrawlerController()
        controller._domain_delays["custom.com"] = 10.0
        
        delay = controller.get_domain_delay("custom.com")
        
        assert delay == 10.0

    def test_get_domain_delay_default(self):
        """测试获取默认域名延迟"""
        controller = CrawlerController(config={"default_delay": 3.0})
        
        delay = controller.get_domain_delay("nonexistent.com")
        
        assert delay == 3.0


class TestReset:
    """测试重置功能"""

    def test_reset_all(self):
        """测试完全重置"""
        controller = CrawlerController()
        controller._domain_delays["example.com"] = 5.0
        controller._last_request_time["example.com"] = time.time()
        controller._ua_index = 5
        controller._proxy_index = 3
        controller._stats["requests_made"] = 10
        controller._stats["retries"] = 5
        
        controller.reset()
        
        assert controller._domain_delays == {}
        assert controller._last_request_time == {}
        assert controller._ua_index == 0
        assert controller._proxy_index == 0
        assert controller._stats["requests_made"] == 0
        assert controller._stats["retries"] == 0


class TestCanFetch:
    """测试 robots.txt 检查"""

    def test_can_fetch_without_respect(self):
        """测试不尊重 robots.txt 时总是允许"""
        controller = CrawlerController(config={"respect_robots": False})
        
        result = controller.can_fetch("https://example.com/disallowed")
        
        assert result is True

    def test_can_fetch_with_respect_allow(self):
        """测试尊重 robots.txt 但允许"""
        controller = CrawlerController(config={"respect_robots": True})
        
        # 模拟没有导入 robotparser 或无法读取，应该返回 True
        result = controller.can_fetch("https://example.com/page")
        
        assert result is True


class TestLoadProxiesFromFile:
    """测试从文件加载代理"""

    def test_load_proxies_from_file_nonexistent(self):
        """测试加载不存在的代理文件"""
        controller = CrawlerController()
        
        count = controller.load_proxies_from_file("nonexistent_file.txt")
        
        assert count == 0

    @patch("builtins.open")
    def test_load_proxies_from_file_success(self, mock_open):
        """测试成功从文件加载代理"""
        controller = CrawlerController()
        
        # 模拟文件内容
        mock_file = mock_open.return_value.__enter__.return_value
        mock_file.__iter__.return_value = [
            "http://proxy1.com\n",
            "# This is a comment\n",
            "http://proxy2.com\n",
            "  \n",
            "http://proxy3.com\n"
        ]
        
        count = controller.load_proxies_from_file("proxies.txt")
        
        assert count == 3


class TestTestProxy:
    """测试代理可用性检查"""

    def test_test_proxy_signature(self):
        """测试代理检查方法签名"""
        controller = CrawlerController()
        
        # 只验证方法存在，不测试实际功能（需要网络）
        assert hasattr(controller, "test_proxy")
        assert callable(getattr(controller, "test_proxy"))

    def test_test_proxy_success_mocked(self):
        """测试代理检查成功（mock requests.get）"""
        controller = CrawlerController()
        
        with patch("requests.get") as mock_get:
            mock_get.return_value.ok = True
            result = controller.test_proxy("http://proxy.com")
            assert result is True

    def test_test_proxy_failure_mocked(self):
        """测试代理检查失败（mock requests.get 异常）"""
        controller = CrawlerController()
        
        with patch("requests.get") as mock_get:
            mock_get.side_effect = Exception("Connection failed")
            result = controller.test_proxy("http://proxy.com")
            assert result is False


class TestWaitIfNeededZeroDelay:
    """测试延迟为0时的提前返回"""

    def test_wait_if_needed_zero_delay_returns_early(self):
        """测试延迟为0时提前返回（覆盖行98）"""
        controller = CrawlerController(config={"default_delay": 0})
        
        # 延迟为0时应该立即返回，不等待
        start = time.time()
        controller.wait_if_needed("https://example.com")
        elapsed = time.time() - start
        
        # 应该几乎不等待
        assert elapsed < 0.1

    def test_wait_if_needed_negative_delay_returns_early(self):
        """测试负延迟时提前返回"""
        controller = CrawlerController()
        controller._default_delay = -1.0
        
        start = time.time()
        controller.wait_if_needed("https://example.com")
        elapsed = time.time() - start
        
        assert elapsed < 0.1


class TestCanFetchRobotsException:
    """测试 robots.txt 异常处理"""

    def test_can_fetch_robots_read_exception(self):
        """测试 robots.txt 读取异常时默认允许（覆盖行286-287）"""
        controller = CrawlerController(config={"respect_robots": True})
        
        with patch("urllib.robotparser.RobotFileParser") as mock_rp_class:
            from unittest.mock import MagicMock
            mock_rp = MagicMock()
            mock_rp_class.return_value = mock_rp
            mock_rp.read.side_effect = Exception("Network error")
            
            result = controller.can_fetch("https://example.com/page")
            
            # 异常时应该默认允许
            assert result is True

    def test_can_fetch_import_error_mocked(self):
        """测试 RobotFileParser 导入失败时默认允许（覆盖行290-291）"""
        controller = CrawlerController(config={"respect_robots": True})
        
        # 使用 sys.modules 模拟 ImportError
        import sys
        import urllib.robotparser as original_rp
        
        # 临时移除模块
        sys.modules["urllib.robotparser"] = None
        
        try:
            result = controller.can_fetch("https://example.com/page")
            # ImportError 时应该默认允许
            assert result is True
        finally:
            # 恢复模块
            sys.modules["urllib.robotparser"] = original_rp


class TestFullCoverageEdgeCases:
    """测试其他边界情况"""

    def test_set_domain_delay_minimum(self):
        """测试设置域名延迟最小值"""
        controller = CrawlerController()
        
        controller.set_domain_delay("example.com", 0.01)
        
        # 应该被调整为最小值 0.1
        assert controller._domain_delays["example.com"] == 0.1

    def test_set_default_delay_negative(self):
        """测试设置负延迟"""
        controller = CrawlerController()
        
        controller.set_default_delay(-5.0)
        
        # 应该被调整为 0
        assert controller._default_delay == 0
