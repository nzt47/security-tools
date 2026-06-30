"""工具调用限流器 — 基于令牌桶算法的速率限制

为每个工具类别提供独立的速率限制，防止单个工具调用频率过高。
使用令牌桶算法实现平滑限流。

工具分类与默认限制：
- network: 5 tokens, 0.5 refill/s (网络工具)
- shell: 2 tokens, 0.2 refill/s (Shell 工具)
- file: 15 tokens, 1 refill/s (文件工具)
- default: 10 tokens, 1 refill/s (普通工具)
"""

import threading
import time
import logging
import json
import uuid

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]



class RateLimiter:
    """令牌桶限流器

    每个工具类别拥有独立的令牌桶，消费令牌后才能执行调用。
    令牌按固定速率补充，桶满后多余的令牌丢弃。

    Attributes:
        _limits: 各类别的 (capacity, refill_rate) 配置
        _buckets: 各类别的当前令牌桶状态
        _lock: 线程锁
    """

    def __init__(self, limits: dict | None = None):
        """初始化限流器

        Args:
            limits: 自定义限制配置，格式为 {category: (capacity, refill_rate)}
                    不提供则使用默认配置
        """
        self._limits = limits or {
            "default": (10, 1.0),     # 10 tokens, refill 1/s
            "network": (5, 0.5),      # 5 tokens, refill 0.5/s
            "shell": (2, 0.2),        # 2 tokens, refill 0.2/s
            "file": (15, 1.0),        # 15 tokens, refill 1/s
        }
        self._buckets: dict[str, dict] = {}  # category -> {tokens, last_refill}
        self._lock = threading.Lock()

    def check(self, tool_name: str) -> bool:
        """检查是否允许调用，消耗一个 token

        Args:
            tool_name: 工具名称

        Returns:
            True 允许调用，False 被限流
        """
        category = self.get_category(tool_name)
        capacity, rate = self._limits.get(category, self._limits["default"])

        with self._lock:
            bucket = self._buckets.get(category)
            now = time.time()

            if bucket is None:
                # 首次访问，初始化满桶
                self._buckets[category] = {
                    "tokens": capacity - 1,  # 消耗一个
                    "last_refill": now,
                }
                return True

            # 补充令牌
            elapsed = now - bucket["last_refill"]
            new_tokens = min(capacity, bucket["tokens"] + elapsed * rate)
            bucket["last_refill"] = now

            if new_tokens >= 1:
                bucket["tokens"] = new_tokens - 1
                return True
            else:
                bucket["tokens"] = new_tokens
                return False

    def get_category(self, tool_name: str) -> str:
        """根据工具名称确定类别

        分类规则（按优先级匹配）：
        - network: 包含 http, fetch, search, web, browse, download, post, xpath, css
        - shell: 包含 shell, execute, process, run_program, start_process
        - file: 包含 read, write, list, search_file, compress, decompress, diff
        - default: 其他

        Args:
            tool_name: 工具名称

        Returns:
            类别字符串
        """
        name_lower = tool_name.lower()

        # 网络工具关键词
        network_keywords = [
            "http", "fetch", "search", "web_", "browse", "download",
            "post", "xpath", "css", "scrape", "crawl", "news",
            "weather", "translate",
        ]
        if any(k in name_lower for k in network_keywords):
            return "network"

        # Shell 工具关键词
        shell_keywords = [
            "shell", "execute", "process", "run_program",
            "start_process", "stop_process",
        ]
        if any(k in name_lower for k in shell_keywords):
            return "shell"

        # 文件工具关键词
        file_keywords = [
            "read_file", "write_file", "list_dir", "search_file",
            "compress", "decompress", "diff", "get_file_info",
        ]
        if any(k in name_lower for k in file_keywords):
            return "file"

        return "default"

    def wait_time(self, tool_name: str) -> float:
        """获取需要等待的时间（秒）

        在 check() 返回 False 后调用，告知调用方需要等待多久才能再次调用。

        Args:
            tool_name: 工具名称

        Returns:
            等待秒数，保留一位小数
        """
        category = self.get_category(tool_name)
        capacity, rate = self._limits.get(category, self._limits["default"])

        with self._lock:
            bucket = self._buckets.get(category)
            if bucket is None:
                return 0.0
            if bucket["tokens"] >= 1:
                return 0.0
            # 计算还需要多久才能获得一个 token
            needed = 1.0 - bucket["tokens"]
            wait = needed / rate if rate > 0 else 1.0
            return round(wait, 1)

    def reset(self):
        """重置所有令牌桶（主要用于测试）"""
        with self._lock:
            self._buckets.clear()
            logger.debug("限流器已重置")


def _safe_call(func, *args, action="safe_call", **kwargs):
    """安全调用包装器——捕获异常并记录结构化日志后重新抛出

    用于边界显性化：可能失败的操作应通过此包装器调用，
    确保异常被记录后再向上传播，而非静默吞掉。
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.error(json.dumps({
            "trace_id": _trace_id(),
            "module_name": "rate_limiter",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
