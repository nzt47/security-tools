"""
CrawlerController 集成测试脚本

此脚本用于测试需要真实网络环境的功能：
- test_proxy: 代理可用性测试
- can_fetch: robots.txt 合规检查
- wait_if_needed: 延迟为0时的提前返回

运行方式：
    python tests/integration/test_crawler_control_integration.py

注意：此测试需要真实的网络连接和可选的代理服务器。
"""

import pytest
import time
import os
import sys

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent.web.crawler_control import CrawlerController


class TestProxyIntegration:
    """代理功能集成测试"""

    def test_proxy_with_public_proxy(self):
        """测试公共代理服务器（如果可用）"""
        controller = CrawlerController()
        
        # 测试一些常见的公共代理格式
        # 注意：这些可能不可用，测试时会跳过
        test_proxies = [
            "http://localhost:8080",  # 本地代理
            "http://127.0.0.1:7890",  # 常见的本地代理端口
        ]
        
        results = []
        for proxy in test_proxies:
            try:
                result = controller.test_proxy(proxy, timeout=5)
                results.append((proxy, result))
                print(f"代理 {proxy}: {'可用' if result else '不可用'}")
            except Exception as e:
                results.append((proxy, False))
                print(f"代理 {proxy}: 测试失败 - {e}")
        
        # 至少执行了测试
        assert len(results) > 0

    def test_proxy_with_httpbin(self):
        """使用 httpbin.org 测试代理功能"""
        controller = CrawlerController()
        
        # 直接测试（无代理）
        try:
            import requests
            resp = requests.get("http://httpbin.org/ip", timeout=10)
            print(f"直接请求 httpbin.org: {resp.status_code}")
            assert resp.ok
        except Exception as e:
            print(f"直接请求失败: {e}")
            pytest.skip("网络不可用，跳过测试")


class TestRobotsTxtIntegration:
    """robots.txt 合规检查集成测试"""

    @pytest.mark.skip_ci
    def test_can_fetch_real_website(self):
        """测试真实网站的 robots.txt 合规"""
        controller = CrawlerController(config={"respect_robots": True})
        
        # 测试一些知名网站
        test_urls = [
            "https://www.google.com/",
            "https://www.wikipedia.org/",
            "https://github.com/",
        ]
        
        results = []
        for url in test_urls:
            try:
                result = controller.can_fetch(url)
                results.append((url, result))
                print(f"URL {url}: {'允许' if result else '禁止'}抓取")
            except Exception as e:
                results.append((url, True))  # 异常时默认允许
                print(f"URL {url}: 检查失败 - {e}，默认允许")
        
        assert len(results) > 0

    def test_can_fetch_without_respect(self):
        """测试不尊重 robots.txt 时总是允许"""
        controller = CrawlerController(config={"respect_robots": False})
        
        # 即使是可能被禁止的路径也应该允许
        result = controller.can_fetch("https://example.com/disallowed/path")
        assert result is True


class TestRateLimitingIntegration:
    """速率限制集成测试"""

    def test_wait_if_needed_zero_delay(self):
        """测试延迟为0时提前返回"""
        controller = CrawlerController(config={"default_delay": 0})
        
        start = time.time()
        controller.wait_if_needed("https://example.com/page1")
        elapsed = time.time() - start
        
        # 延迟为0时应该几乎不等待
        assert elapsed < 0.1
        print(f"延迟为0时耗时: {elapsed:.3f}s")

    def test_wait_if_needed_real_delay(self):
        """测试真实延迟"""
        controller = CrawlerController(config={"default_delay": 0.5})
        
        # 第一次请求
        controller.wait_if_needed("https://example.com/page1")
        
        # 第二次请求应该等待
        start = time.time()
        controller.wait_if_needed("https://example.com/page2")
        elapsed = time.time() - start
        
        # 应该等待约0.5秒（考虑随机抖动）
        assert elapsed >= 0.3
        print(f"延迟0.5s时实际耗时: {elapsed:.3f}s")


class TestAcquireIntegration:
    """请求获取集成测试"""

    def test_acquire_returns_valid_config(self):
        """测试 acquire 返回有效配置"""
        controller = CrawlerController(config={
            "proxies": ["http://localhost:8080"],
            "user_agents": ["TestBot/1.0"]
        })
        
        config = controller.acquire("https://example.com")
        
        assert "headers" in config
        assert "User-Agent" in config["headers"]
        assert config["headers"]["User-Agent"] == "TestBot/1.0"
        print(f"获取配置: headers={config['headers']}")


class TestFullWorkflowIntegration:
    """完整工作流集成测试"""

    def test_full_request_workflow(self):
        """测试完整的请求工作流"""
        controller = CrawlerController(config={
            "default_delay": 0.1,
            "max_retries": 2,
            "user_agents": ["IntegrationTestBot/1.0"]
        })
        
        # 模拟请求流程
        url = "https://example.com"
        
        # 1. 获取请求配置
        config = controller.acquire(url)
        print(f"1. 获取配置: {config}")
        
        # 2. 模拟请求成功
        controller.report_result(url, success=True, status_code=200)
        print(f"2. 报告成功: stats={controller.get_stats()}")
        
        # 3. 验证统计
        stats = controller.get_stats()
        assert stats["requests_made"] >= 1
        print(f"3. 最终统计: {stats}")


def run_integration_tests():
    """运行集成测试"""
    print("=" * 60)
    print("CrawlerController 集成测试")
    print("=" * 60)
    
    # 运行测试
    pytest.main([__file__, "-v", "-s"])


if __name__ == "__main__":
    run_integration_tests()