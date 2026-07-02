"""
HTTP 请求引擎 — 会话管理、重试、代理、Cookie

提供统一的 HTTP/HTTPS 请求接口，支持同步/异步，
集成重试机制、超时控制、Cookie 持久化。
"""

import asyncio
import json
import logging
import time
from typing import Optional, Dict, Any, List, Union
from urllib.parse import urlparse, urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# 默认配置（向后兼容常量，实际值应通过 get_http_max_retries() 从 Config 读取）
DEFAULT_TIMEOUT = 30
DEFAULT_MAX_RETRIES = 3  # 向后兼容别名
DEFAULT_CONNECT_TIMEOUT = 10
DEFAULT_POOL_SIZE = 20


class HttpClient:
    """HTTP 请求引擎 — 云枢的互联网访问基础

    功能：
    - GET/POST/PUT/DELETE/HEAD 请求
    - 自动重试（指数退避）
    - Cookie 会话持久化
    - 请求头自定义
    - 代理支持（HTTP/HTTPS/SOCKS）
    - 响应自动解码
    - 流式下载
    """

    def __init__(self, config: Optional[dict] = None):
        self._config = config or {}
        self._session = self._build_session()
        self._cookies_file = self._config.get("cookies_file")
        self._load_cookies()
        self._stats = {
            "total_requests": 0,
            "success_count": 0,
            "error_count": 0,
            "total_bytes": 0,
            "started_at": time.time(),
        }
        logger.info("HTTP 请求引擎已初始化")

    def _build_session(self) -> requests.Session:
        """构建 requests Session 含连接池和重试策略"""
        session = requests.Session()

        # 默认请求头
        session.headers.update({
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        })

        # 重试策略（包含 403 以应对反爬限制）
        # 配置化：从 Config 读取默认重试次数（支持热加载）
        from agent.monitoring.observability_config import get_http_max_retries
        retry_strategy = Retry(
            total=self._config.get("max_retries", get_http_max_retries()),
            backoff_factor=self._config.get("backoff_factor", 0.5),
            status_forcelist=[429, 500, 502, 503, 504, 403],
            allowed_methods=["GET", "POST", "HEAD"],
        )

        # HTTP 和 HTTPS 适配器
        adapter = HTTPAdapter(
            pool_connections=self._config.get("pool_size", DEFAULT_POOL_SIZE),
            pool_maxsize=self._config.get("pool_size", DEFAULT_POOL_SIZE) * 2,
            max_retries=retry_strategy,
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        # 代理
        proxy = self._config.get("proxy")
        if proxy:
            session.proxies = {"http": proxy, "https": proxy}

        return session

    # ── 核心请求方法 ──────────────────────────────────────────────

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[dict] = None,
        data: Any = None,
        json_data: Optional[dict] = None,
        headers: Optional[dict] = None,
        cookies: Optional[dict] = None,
        timeout: Optional[int] = None,
        allow_redirects: bool = True,
        stream: bool = False,
        verify: bool = True,
        **kwargs,
    ) -> dict:
        """通用 HTTP 请求方法

        Args:
            method: HTTP 方法（GET/POST/PUT/DELETE/HEAD）
            url: 目标 URL
            params: URL 查询参数
            data: 表单数据或原始数据
            json_data: JSON 数据
            headers: 自定义请求头
            cookies: 自定义 Cookie
            timeout: 超时秒数
            allow_redirects: 是否跟随重定向
            stream: 是否流式响应
            verify: 是否验证 SSL
            **kwargs: 传递给 requests 的其他参数

        Returns:
            dict: {ok, status_code, headers, content, text, url, elapsed, error, ...}
        """
        self._stats["total_requests"] += 1
        start = time.time()

        # 合并请求头
        req_headers = {}
        if headers:
            req_headers.update(headers)

        # URL 校验
        if not url.startswith(("http://", "https://")):
            return self._error_result(url, "仅支持 http/https 协议", start)

        try:
            # 过滤与显式参数同名的键，避免 **kwargs 展开冲突
            _http_reserved = {"method", "url", "params", "data", "json",
                              "headers", "cookies", "timeout",
                              "allow_redirects", "stream", "verify"}
            safe_kwargs = {k: v for k, v in kwargs.items() if k not in _http_reserved}
            resp = self._session.request(
                method=method.upper(),
                url=url,
                params=params,
                data=data,
                json=json_data,
                headers=req_headers or None,
                cookies=cookies,
                timeout=timeout or self._config.get("timeout", DEFAULT_TIMEOUT),
                allow_redirects=allow_redirects,
                stream=stream,
                verify=verify,
                **safe_kwargs,
            )

            elapsed = time.time() - start
            content = resp.content
            self._stats["success_count"] += 1
            self._stats["total_bytes"] += len(content)

            # 尝试解码文本
            text = None
            encoding = None
            try:
                if resp.encoding:
                    encoding = resp.encoding
                    text = resp.text
                else:
                    # 自动检测编码
                    import chardet
                    detected = chardet.detect(content)
                    encoding = detected.get("encoding", "utf-8")
                    text = content.decode(encoding, errors="replace")
            except Exception:
                text = content.decode("utf-8", errors="replace")
                encoding = "utf-8 (with replacements)"

            result = {
                "ok": resp.ok,
                "status_code": resp.status_code,
                "reason": resp.reason,
                "headers": dict(resp.headers),
                "content_length": len(content),
                "content": None if stream else content,  # 流式不自动读取
                "text": text if not stream else None,
                "encoding": encoding,
                "url": resp.url,
                "elapsed": round(elapsed, 3),
                "cookies": dict(resp.cookies),
                "redirect_history": [r.url for r in resp.history] if resp.history else [],
            }

            if not resp.ok:
                result["error"] = f"HTTP {resp.status_code}: {resp.reason}"

            return result

        except requests.exceptions.Timeout as e:
            self._stats["error_count"] += 1
            return self._error_result(url, f"请求超时: {e}", start)
        except requests.exceptions.ConnectionError as e:
            self._stats["error_count"] += 1
            return self._error_result(url, f"连接失败: {e}", start)
        except requests.exceptions.RequestException as e:
            self._stats["error_count"] += 1
            return self._error_result(url, f"请求异常: {e}", start)
        except Exception as e:
            self._stats["error_count"] += 1
            logger.exception("HTTP 请求未知异常")
            return self._error_result(url, f"未知错误: {e}", start)

    def get(self, url: str, **kwargs) -> dict:
        """GET 请求"""
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> dict:
        """POST 请求"""
        return self.request("POST", url, **kwargs)

    def head(self, url: str, **kwargs) -> dict:
        """HEAD 请求（获取响应头）"""
        return self.request("HEAD", url, **kwargs)

    # ── 高级功能 ──────────────────────────────────────────────────

    def download(self, url: str, filepath: str, chunk_size: int = 8192, **kwargs) -> dict:
        """下载文件到本地

        Args:
            url: 文件 URL
            filepath: 本地存储路径
            chunk_size: 分块大小

        Returns:
            dict: {ok, filepath, size, elapsed, error}
        """
        import os
        start = time.time()
        try:
            _http_reserved = {"url", "stream", "timeout"}
            safe_kwargs = {k: v for k, v in kwargs.items() if k not in _http_reserved}
            resp = self._session.get(url, stream=True, timeout=DEFAULT_TIMEOUT, **safe_kwargs)
            resp.raise_for_status()

            os.makedirs(os.path.dirname(os.path.abspath(filepath)) or ".", exist_ok=True)
            total = 0
            with open(filepath, "wb") as f:
                for chunk in resp.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
                        total += len(chunk)

            self._stats["total_bytes"] += total
            return {
                "ok": True,
                "filepath": filepath,
                "size": total,
                "elapsed": round(time.time() - start, 3),
            }
        except Exception as e:
            return {"ok": False, "error": str(e), "elapsed": round(time.time() - start, 3)}

    def batch_request(self, urls: List[str], method: str = "GET",
                      max_concurrency: int = 5, **kwargs) -> List[dict]:
        """批量请求（同步，简单并发控制）

        Args:
            urls: URL 列表
            method: HTTP 方法
            max_concurrency: 最大并发数
            **kwargs: 传递给 request 的参数

        Returns:
            List[dict]: 请求结果列表
        """
        results = []
        for i in range(0, len(urls), max_concurrency):
            batch = urls[i:i + max_concurrency]
            batch_results = []
            for url in batch:
                result = self.request(method, url, **kwargs)
                batch_results.append(result)
            results.extend(batch_results)
        return results

    # ── Cookie 管理 ──────────────────────────────────────────────

    def set_cookies(self, cookies: dict, domain: Optional[str] = None):
        """手动设置 Cookie"""
        for name, value in cookies.items():
            if domain:
                from requests.cookies import create_cookie
                cookie = create_cookie(name=name, value=value, domain=domain)
                self._session.cookies.set_cookie(cookie)
            else:
                self._session.cookies.set(name, value)

    def get_cookies(self, domain: Optional[str] = None) -> dict:
        """获取当前会话的 Cookie"""
        if domain:
            return dict(self._session.cookies.get_dict(domain=domain))
        return dict(self._session.cookies.get_dict())

    def clear_cookies(self):
        """清空所有 Cookie"""
        self._session.cookies.clear()

    def _load_cookies(self):
        """从文件加载持久化 Cookie"""
        if not self._cookies_file:
            return
        try:
            import os
            if os.path.exists(self._cookies_file):
                with open(self._cookies_file, "r", encoding="utf-8") as f:
                    cookies = json.load(f)
                    for name, value in cookies.items():
                        self._session.cookies.set(name, value)
                logger.info("已加载 %d 个持久化 Cookie", len(cookies))
        except Exception as e:
            logger.warning("加载 Cookie 失败: %s", e)

    def save_cookies(self):
        """保存 Cookie 到文件"""
        if not self._cookies_file:
            return
        try:
            import os
            os.makedirs(os.path.dirname(self._cookies_file) or ".", exist_ok=True)
            with open(self._cookies_file, "w", encoding="utf-8") as f:
                json.dump(dict(self._session.cookies.get_dict()), f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("保存 Cookie 失败: %s", e)

    # ── 会话管理 ──────────────────────────────────────────────────

    def update_headers(self, headers: dict):
        """更新会话默认请求头"""
        self._session.headers.update(headers)

    def set_proxy(self, proxy: Optional[str]):
        """动态设置/清除代理"""
        if proxy:
            self._session.proxies = {"http": proxy, "https": proxy}
        else:
            self._session.proxies = {}

    def reset_session(self):
        """重置会话（新会话、清 Cookie）"""
        old_cookies = self._session.cookies.get_dict()
        self._session = self._build_session()
        # 恢复 Cookie 白名单
        for name, value in old_cookies.items():
            self._session.cookies.set(name, value)

    # ── URL 工具 ──────────────────────────────────────────────────

    @staticmethod
    def is_valid_url(url: str) -> bool:
        """检查 URL 是否有效"""
        try:
            result = urlparse(url)
            return all([result.scheme, result.netloc])
        except Exception:
            return False

    @staticmethod
    def join_url(base: str, path: str) -> str:
        """拼接 URL"""
        return urljoin(base, path)

    # ── 统计 ──────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """获取请求统计"""
        uptime = time.time() - self._stats["started_at"]
        return {
            **self._stats,
            "uptime_sec": round(uptime, 1),
            "avg_speed_kbps": round(self._stats["total_bytes"] / 1024 / max(uptime, 1), 1),
        }

    def close(self):
        """关闭会话释放资源"""
        try:
            self.save_cookies()
            self._session.close()
        except Exception:
            pass

    def _error_result(self, url: str, error: str, start: float) -> dict:
        return {
            "ok": False,
            "error": error,
            "url": url,
            "elapsed": round(time.time() - start, 3),
        }

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
