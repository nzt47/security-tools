"""
爬虫控制模块测试
"""

import pytest
from unittest.mock import Mock, patch
import time

from agent.web.crawler_control import (
    CrawlerController,
    DEFAULT_USER_AGENTS,
)


class TestCrawlerControllerInit:
    """测试爬虫控制器初始化"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_init_default_config(self):
        """测试默认配置初始化"""
        controller = CrawlerController()
        
        assert controller._default_delay == 1.0
        assert controller._max_retries == 3
        assert controller._respect_robots is False

    @pytest.mark.unit
    @pytest.mark.p1
    def test_init_custom_config(self):
        """测试自定义配置初始化"""
        config = {
            "default_delay": 2.0,
            "max_retries": 5,
            "respect_robots": True,
        }
        controller = CrawlerController(config=config)
        
        assert controller._default_delay == 2.0
        assert controller._max_retries == 5
        assert controller._respect_robots is True

    @pytest.mark.unit
    @pytest.mark.p1
    def test_init_with_proxies(self):
        """测试带代理池初始化"""
        config = {
            "proxies": [
                "http://proxy1.example.com:8080",
                "http://proxy2.example.com:8080",
            ]
        }
        controller = CrawlerController(config=config)
        
        assert len(controller._proxies) == 2

    @pytest.mark.unit
    @pytest.mark.p1
    def test_init_with_custom_user_agents(self):
        """测试自定义 User-Agent 初始化"""
        custom_uas = ["CustomUA/1.0", "CustomUA/2.0"]
        config = {"user_agents": custom_uas}
        
        controller = CrawlerController(config=config)
        
        assert controller._ua_list == custom_uas

    @pytest.mark.unit
    @pytest.mark.p1
    def test_initial_stats(self):
        """测试初始统计"""
        controller = CrawlerController()
        
        # 统计应该存在
        assert controller._stats is not None


class TestDefaultUserAgents:
    """测试默认 User-Agent 池"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_default_ua_count(self):
        """测试默认 UA 数量"""
        assert len(DEFAULT_USER_AGENTS) >= 5

    @pytest.mark.unit
    @pytest.mark.p1
    def test_default_ua_contains_chrome(self):
        """测试默认 UA 包含 Chrome"""
        chrome_uas = [ua for ua in DEFAULT_USER_AGENTS if "Chrome" in ua]
        assert len(chrome_uas) >= 1

    @pytest.mark.unit
    @pytest.mark.p1
    def test_default_ua_contains_mobile(self):
        """测试默认 UA 包含移动端"""
        mobile_uas = [ua for ua in DEFAULT_USER_AGENTS if "Mobile" in ua or "iPhone" in ua]
        assert len(mobile_uas) >= 1


class TestRateControl:
    """测试速率控制"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_default_delay(self):
        """测试默认延迟"""
        controller = CrawlerController()
        
        assert controller._default_delay == 1.0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_custom_delay(self):
        """测试自定义延迟"""
        controller = CrawlerController(config={"default_delay": 2.0})
        
        assert controller._default_delay == 2.0


class TestUserAgentRotation:
    """测试 User-Agent 轮换"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_get_user_agent(self):
        """测试获取 User-Agent"""
        controller = CrawlerController()
        
        ua = controller.get_user_agent()
        
        assert ua is not None
        assert len(ua) > 0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_force_user_agent(self):
        """测试强制 User-Agent"""
        controller = CrawlerController(config={"force_user_agent": "ForcedUA/1.0"})
        
        ua = controller.get_user_agent()
        
        assert ua == "ForcedUA/1.0"


class TestProxyRotation:
    """测试代理轮换"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_get_proxy_no_proxies(self):
        """测试无代理时获取代理"""
        controller = CrawlerController()
        
        proxy = controller.get_proxy()
        
        assert proxy is None

    @pytest.mark.unit
    @pytest.mark.p1
    def test_get_proxy_with_proxies(self):
        """测试有代理时获取代理"""
        config = {
            "proxies": [
                "http://proxy1.example.com:8080",
                "http://proxy2.example.com:8080",
            ]
        }
        controller = CrawlerController(config=config)
        
        proxy = controller.get_proxy()
        
        assert proxy is not None
        assert proxy in controller._proxies


class TestRetryMechanism:
    """测试重试机制"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_max_retries(self):
        """测试最大重试次数"""
        controller = CrawlerController(config={"max_retries": 3})
        
        assert controller._max_retries == 3

    @pytest.mark.unit
    @pytest.mark.p1
    def test_retry_delay(self):
        """测试重试延迟"""
        controller = CrawlerController(config={"retry_backoff": 1.0})
        
        # 延迟应该存在
        delay = controller.retry_delay(1)
        assert delay > 0


class TestRobotsCompliance:
    """测试 robots.txt 合规"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_robots_disabled(self):
        """测试禁用 robots.txt 检查"""
        controller = CrawlerController(config={"respect_robots": False})
        
        assert controller._respect_robots is False

    @pytest.mark.unit
    @pytest.mark.p1
    def test_robots_enabled(self):
        """测试启用 robots.txt 检查"""
        controller = CrawlerController(config={"respect_robots": True})
        
        assert controller._respect_robots is True


class TestCrawlerStats:
    """测试爬虫统计"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_get_stats(self):
        """测试获取统计"""
        controller = CrawlerController()
        
        stats = controller.get_stats()
        
        assert stats is not None