"""
爬虫控制模块 — 限速、UA 轮换、代理池、合规、异常恢复

智能请求频率控制，遵守 robots.txt，动态切换 UA 和代理。
"""

import re
import json
import time
import random
import logging
import threading
from typing import Optional, List, Dict, Any
from collections import deque
from urllib.parse import urlparse
from pathlib import Path

logger = logging.getLogger(__name__)

# 默认 User-Agent 池（覆盖主流浏览器和更多客户端）
DEFAULT_USER_AGENTS = [
    # Chrome Windows (最新版本)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Edge Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    # Firefox Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    # Chrome macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    # Safari macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    # Chrome Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    # Firefox Linux
    "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0",
    # Mobile Chrome Android
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; Samsung Galaxy S24) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
    # Mobile Safari iOS
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    # iPad
    "Mozilla/5.0 (iPad; CPU OS 17_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1",
    # Opera
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 OPR/111.0.0.0",
    # Brave
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]


class CrawlerController:
    """爬虫控制 — 限速、UA 轮换、代理池、异常恢复

    功能：
    - 令牌桶速率限制（per-domain）
    - User-Agent 智能轮换
    - HTTP/HTTPS 代理池管理
    - 异常自动重试（指数退避）
    - robots.txt 合规检查（可选）
    """

    def __init__(self, config: Optional[dict] = None):
        cfg = config or {}

        # 速率控制
        self._default_delay = cfg.get("default_delay", 1.0)       # 同一域名请求间隔（秒）
        self._domain_delays: Dict[str, float] = {}                 # 域名自定义延迟
        self._last_request_time: Dict[str, float] = {}             # 域名最后请求时间
        self._rate_lock = threading.Lock()

        # User-Agent
        self._ua_list = cfg.get("user_agents", DEFAULT_USER_AGENTS)
        self._ua_index = 0
        self._force_ua = cfg.get("force_user_agent", "")

        # 代理
        self._proxies: List[str] = cfg.get("proxies", [])
        self._proxy_index = 0
        self._proxy_stats: Dict[str, Dict] = {}                    # proxy → {success, fail, last_used}

        # 重试
        self._max_retries = cfg.get("max_retries", 3)
        self._retry_backoff_base = cfg.get("retry_backoff", 1.0)

        # 合规
        self._respect_robots = cfg.get("respect_robots", False)
        self._robots_cache: Dict[str, Any] = {}

        # 统计
        self._stats = {
            "requests_made": 0,
            "retries": 0,
            "blocked_count": 0,
            "proxy_switches": 0,
            "ua_switches": 0,
        }

        logger.info("CrawlerController 已初始化 (延迟=%.1fs, 代理=%d, UA=%d)",
                     self._default_delay, len(self._proxies), len(self._ua_list))

    # ── 请求控制 ──────────────────────────────────────────────────

    def wait_if_needed(self, url: str):
        """根据域名限速等待（阻塞当前线程）"""
        domain = urlparse(url).netloc
        delay = self._domain_delays.get(domain, self._default_delay)
        if delay <= 0:
            return

        with self._rate_lock:
            last = self._last_request_time.get(domain, 0)
            elapsed = time.time() - last
            if elapsed < delay:
                sleep_time = delay - elapsed
                # 加入随机抖动 ±20%
                sleep_time *= random.uniform(0.8, 1.2)
                time.sleep(sleep_time)
            self._last_request_time[domain] = time.time()

    def acquire(self, url: str) -> dict:
        """获取请求配置（包含 UA 和代理），同时执行限速

        Returns:
            dict: {headers, proxies, delay}
        """
        self.wait_if_needed(url)

        headers = {"User-Agent": self.get_user_agent()}
        proxy = self.get_proxy()
        proxies = {"http": proxy, "https": proxy} if proxy else None

        self._stats["requests_made"] += 1

        return {
            "headers": headers,
            "proxies": proxies,
        }

    def report_result(self, url: str, success: bool, status_code: int = 0, error: str = ""):
        """报告请求结果，用于调整策略"""
        domain = urlparse(url).netloc

        if not success:
            self._stats["retries"] += 1

            # 429 = 被限速，增大延迟
            if status_code == 429:
                current = self._domain_delays.get(domain, self._default_delay)
                self._domain_delays[domain] = min(current * 2, 60)
                logger.warning("域名 %s 被限速(429)，延迟调整为 %.1fs", domain, self._domain_delays[domain])

            # 403/503 = 可能被屏蔽，切换 UA 和代理
            if status_code in (403, 503):
                self._stats["blocked_count"] += 1
                self._rotate_ua()
                self._rotate_proxy()

            # 代理错误，标记并切换
            if proxy := self._get_current_proxy():
                if proxy not in self._proxy_stats:
                    self._proxy_stats[proxy] = {"success": 0, "fail": 0, "last_used": 0}
                if error and ("ConnectionError" in error or "Timeout" in error or "ProxyError" in error):
                    self._proxy_stats[proxy]["fail"] += 1
                    # 连续失败自动切换
                    if self._proxy_stats[proxy]["fail"] >= 3:
                        self._rotate_proxy()
                else:
                    self._proxy_stats[proxy]["success"] += 1
                self._proxy_stats[proxy]["last_used"] = time.time()
        else:
            self._domain_delays[domain] = max(self._default_delay, self._domain_delays.get(domain, 0) * 0.9)

    # ── User-Agent 管理 ─────────────────────────────────────────────

    def get_user_agent(self) -> str:
        """获取当前 User-Agent"""
        if self._force_ua:
            return self._force_ua
        return self._ua_list[self._ua_index % len(self._ua_list)]

    def _rotate_ua(self):
        """轮换 User-Agent"""
        self._ua_index = (self._ua_index + random.randint(1, 3)) % len(self._ua_list)
        self._stats["ua_switches"] += 1

    def set_user_agents(self, ua_list: List[str]):
        """自定义 User-Agent 列表"""
        if ua_list:
            self._ua_list = ua_list
            self._ua_index = 0

    def add_user_agent(self, ua: str):
        """添加 User-Agent"""
        if ua not in self._ua_list:
            self._ua_list.append(ua)

    # ── 代理管理 ──────────────────────────────────────────────────

    def get_proxy(self) -> Optional[str]:
        """获取当前代理"""
        if not self._proxies:
            return None
        return self._proxies[self._proxy_index % len(self._proxies)]

    def _get_current_proxy(self) -> Optional[str]:
        return self._proxies[self._proxy_index % len(self._proxies)] if self._proxies else None

    def _rotate_proxy(self):
        """轮换代理"""
        if len(self._proxies) > 1:
            self._proxy_index = (self._proxy_index + 1) % len(self._proxies)
            self._stats["proxy_switches"] += 1

    def set_proxies(self, proxy_list: List[str]):
        """设置代理列表"""
        self._proxies = proxy_list
        self._proxy_index = 0
        self._proxy_stats = {}
        logger.info("已加载 %d 个代理", len(proxy_list))

    def add_proxy(self, proxy: str):
        """添加单个代理"""
        self._proxies.append(proxy)
        self._proxy_stats[proxy] = {"success": 0, "fail": 0, "last_used": 0}

    def remove_proxy(self, proxy: str):
        """移除代理"""
        if proxy in self._proxies:
            self._proxies.remove(proxy)
            self._proxy_stats.pop(proxy, None)

    def load_proxies_from_file(self, filepath: str) -> int:
        """从文件加载代理列表（每行一个）"""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                proxies = [line.strip() for line in f if line.strip() and not line.startswith("#")]
            self.set_proxies(proxies)
            return len(proxies)
        except Exception as e:
            logger.error("加载代理文件失败: %s", e)
            return 0

    def test_proxy(self, proxy: str, test_url: str = "http://httpbin.org/ip", timeout: int = None) -> bool:
        """测试单个代理是否可用
        
        Args:
            proxy: 代理地址
            test_url: 测试 URL
            timeout: 超时时间（秒），如果为 None 则从配置系统获取
        """
        # 从配置系统获取爬虫超时设置
        if timeout is None:
            from config import Config
            try:
                global_config = Config()
                timeout = global_config.get("network", "crawler_timeout")
            except Exception:
                logger.warning("[爬虫控制] 无法从配置系统获取 crawler_timeout，使用默认值 30")
                timeout = 30
                
        try:
            import requests
            resp = requests.get(test_url, proxies={"http": proxy, "https": proxy}, timeout=timeout)
            return resp.ok
        except Exception:
            return False

    # ── 重试逻辑 ──────────────────────────────────────────────────

    def should_retry(self, attempt: int, result: dict) -> bool:
        """判断是否需要重试

        Args:
            attempt: 已尝试次数（从 0 开始）
            result: 请求结果 dict

        Returns:
            bool: 是否应该重试
        """
        if attempt >= self._max_retries - 1:
            return False
        if result.get("ok"):
            return False
        # 4xx 客户端错误不重试（除了 429）
        status = result.get("status_code", 0)
        if 400 <= status < 500 and status != 429:
            return False
        return True

    def retry_delay(self, attempt: int) -> float:
        """计算重试等待时间（指数退避 + 抖动）"""
        delay = self._retry_backoff_base * (2 ** attempt)
        delay *= random.uniform(0.5, 1.5)
        return min(delay, 30)  # 最大 30 秒

    # ── 合规 ──────────────────────────────────────────────────────

    def can_fetch(self, url: str, user_agent: str = "*") -> bool:
        """检查 robots.txt 是否允许抓取（需安装 robotparser）"""
        if not self._respect_robots:
            return True
        try:
            from urllib.robotparser import RobotFileParser
            parsed = urlparse(url)
            base = f"{parsed.scheme}://{parsed.netloc}"
            rp = self._robots_cache.get(base)
            if rp is None:
                rp = RobotFileParser()
                rp.set_url(f"{base}/robots.txt")
                try:
                    rp.read()
                except Exception:
                    return True  # 无法读取 robots.txt 时默认允许
                self._robots_cache[base] = rp
            return rp.can_fetch(user_agent, url)
        except ImportError:
            return True

    # ── 配置 ──────────────────────────────────────────────────────

    def set_domain_delay(self, domain: str, delay: float):
        """设置特定域名的请求延迟（秒）"""
        self._domain_delays[domain] = max(0.1, delay)

    def set_default_delay(self, delay: float):
        """设置默认请求延迟"""
        self._default_delay = max(0, delay)

    def get_domain_delay(self, domain: str) -> float:
        """获取指定域名的请求延迟"""
        return self._domain_delays.get(domain, self._default_delay)

    # ── 统计 ──────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """获取爬虫控制统计"""
        return {
            **self._stats,
            "active_proxies": len(self._proxies),
            "user_agents_count": len(self._ua_list),
            "domain_delays": dict(self._domain_delays),
            "proxy_stats": self._proxy_stats,
            "default_delay": self._default_delay,
        }

    def reset(self):
        """重置所有控制状态"""
        self._domain_delays.clear()
        self._last_request_time.clear()
        self._ua_index = 0
        self._proxy_index = 0
        self._proxy_stats = {}
        self._robots_cache.clear()
        self._stats = {k: 0 for k in self._stats}
