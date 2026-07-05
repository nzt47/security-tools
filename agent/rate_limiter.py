"""工具调用限流器 — 基于令牌桶算法的多级速率限制

提供全局→接口→用户→并发的多级限流，失败时按优先级回退已消费的令牌。
支持 REJECT/QUEUE/DELAY 三种策略。

工具分类与默认限制（旧 API，向后兼容）：
- network: 5 tokens, 0.5 refill/s (网络工具)
- shell: 2 tokens, 0.2 refill/s (Shell 工具)
- file: 15 tokens, 1 refill/s (文件工具)
- default: 10 tokens, 1 refill/s (普通工具)
"""

from __future__ import annotations

import asyncio
import functools
import threading
import time
import logging
import json
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# 模块级导入（便于测试 patch）
try:
    from agent.monitoring.metrics import get_business_metrics_collector
except ImportError:
    def get_business_metrics_collector():
        return None


def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


# ── 令牌桶实现 ──────────────────────────────────────────────


class TokenBucket:
    """令牌桶 — 按固定速率补充令牌的限流原语

    Args:
        capacity: 桶容量（最大令牌数）
        refill_rate: 令牌补充速率（令牌/秒）
    """

    def __init__(self, capacity: float, refill_rate: float):
        self.capacity = float(capacity)
        self.refill_rate = float(refill_rate)
        self._tokens = float(capacity)
        self._last_refill = time.time()
        self._lock = threading.Lock()

    @property
    def tokens(self) -> float:
        """当前可用令牌数（自动补充）"""
        with self._lock:
            self._refill()
            return self._tokens

    def _refill(self) -> None:
        """补充令牌（必须持有锁）"""
        now = time.time()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_rate)
            self._last_refill = now

    def try_acquire(self, count: float = 1.0) -> bool:
        """尝试获取令牌，成功返回 True，失败返回 False"""
        with self._lock:
            self._refill()
            if self._tokens >= count:
                self._tokens -= count
                return True
            return False

    def release(self, count: float = 1.0) -> None:
        """释放令牌（回退操作，不超过容量上限）"""
        with self._lock:
            self._tokens = min(self.capacity, self._tokens + count)

    def reset(self) -> None:
        """重置为满桶"""
        with self._lock:
            self._tokens = float(self.capacity)
            self._last_refill = time.time()

    def get_wait_time(self, count: float = 1.0) -> float:
        """获取等待多久才能有足够令牌"""
        with self._lock:
            self._refill()
            if self._tokens >= count:
                return 0.0
            needed = count - self._tokens
            if self.refill_rate <= 0:
                return float("inf")
            return needed / self.refill_rate

    def to_dict(self) -> dict:
        """状态快照"""
        with self._lock:
            self._refill()
            return {
                "tokens": self._tokens,
                "capacity": self.capacity,
                "refill_rate": self.refill_rate,
            }


# ── 枚举与异常 ──────────────────────────────────────────────


class RateLimitStrategy(str, Enum):
    """限流策略"""
    REJECT = "reject"  # 直接拒绝
    QUEUE = "queue"    # 排队等待
    DELAY = "delay"    # 延迟返回


class RateLimitError(Exception):
    """限流触发时抛出的业务错误"""

    def __init__(
        self,
        message: str = "速率限制触发",
        error_code: str = "RATE_LIMIT_EXCEEDED",
        endpoint: Optional[str] = None,
        user_id: Optional[str] = None,
    ):
        super().__init__(message)
        self.error_code = error_code
        self.endpoint = endpoint
        self.user_id = user_id


# ── 限流器主类 ──────────────────────────────────────────────


class RateLimiter:
    """多级令牌桶限流器

    支持两种构造形式：
    1. 新 API: RateLimiter(max_concurrent=100, strategy=RateLimitStrategy.REJECT)
    2. 旧 API: RateLimiter(limits={"default": (10, 1.0), ...})

    多级限流优先级：全局 → 接口 → 用户 → 并发
    """

    def __init__(
        self,
        limits: Optional[dict] = None,
        *,
        max_concurrent: int = 100,
        strategy: RateLimitStrategy = RateLimitStrategy.REJECT,
        **kwargs,
    ):
        # 判断是否使用旧 API（第一个位置参数是 dict）
        if isinstance(limits, dict):
            self._limits = limits
            self._old_api = True
            self.max_concurrent = max_concurrent
        else:
            self._limits = {
                "default": (10, 1.0),
                "network": (5, 0.5),
                "shell": (2, 0.2),
                "file": (15, 1.0),
            }
            self._old_api = False
            self.max_concurrent = max_concurrent

        self.strategy = strategy
        self._rules: dict[str, tuple[float, float]] = {}
        self._buckets: dict[str, TokenBucket] = {}
        self._global_bucket = TokenBucket(capacity=100, refill_rate=10.0)
        self._current_concurrent = 0
        self._concurrent_lock = threading.Lock()
        self._concurrent_cond = threading.Condition(self._concurrent_lock)
        self._queue_event = threading.Event()
        self._lock = threading.RLock()

    # ── 多级限流 check（新 API） ─────────────────────────────

    def check(
        self,
        tool_name: Optional[str] = None,
        *,
        endpoint: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> bool:
        """检查是否允许调用

        新 API: check(endpoint="api/chat", user_id="user1")
        旧 API: check("tool_name")

        多级限流：全局 → 接口 → 用户 → 并发，失败时回退已消费的令牌。
        """
        # 旧 API 兼容：传了 tool_name 且未传 endpoint/user_id
        if tool_name is not None and endpoint is None and user_id is None:
            return self._check_old(tool_name)

        return self._check_multi_level(endpoint, user_id)

    def _check_multi_level(
        self,
        endpoint: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> bool:
        """多级限流检查（全局→接口→用户→并发）"""
        # 1. 全局限流
        if not self._global_bucket.try_acquire():
            self._safe_metric("global")
            return False

        acquired_levels = ["global"]

        # 2. 接口限流
        if endpoint is not None:
            ep_bucket = self._get_endpoint_bucket(endpoint)
            if not ep_bucket.try_acquire():
                self._global_bucket.release()
                self._safe_metric("endpoint")
                return False
            acquired_levels.append(("endpoint", endpoint))

        # 3. 用户限流（独立于接口限流）
        if user_id is not None:
            user_bucket = self._get_user_bucket(user_id)
            if not user_bucket.try_acquire():
                # 回退已获取的令牌
                for key in reversed(acquired_levels):
                    if key == "global":
                        self._global_bucket.release()
                    else:
                        bucket_type, ident = key
                        if bucket_type == "endpoint":
                            self._get_endpoint_bucket(ident).release()
                self._safe_metric("user")
                return False
            acquired_levels.append(("user", user_id))

        # 4. 并发限制
        if not self._acquire_concurrent():
            # 回退令牌
            for key in reversed(acquired_levels):
                if key == "global":
                    self._global_bucket.release()
                else:
                    bucket_type, ident = key
                    if bucket_type == "endpoint":
                        self._get_endpoint_bucket(ident).release()
                    else:
                        self._get_user_bucket(ident).release()
            self._safe_metric("concurrent")
            return False

        return True

    def _acquire_concurrent(self) -> bool:
        """获取并发槽位"""
        with self._concurrent_cond:
            if self._current_concurrent >= self.max_concurrent:
                if self.strategy == RateLimitStrategy.QUEUE:
                    # 排队等待（带超时）
                    timeout = 5.0
                    deadline = time.time() + timeout
                    while self._current_concurrent >= self.max_concurrent:
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            return False
                        self._concurrent_cond.wait(timeout=remaining)
                    self._current_concurrent += 1
                    return True
                return False
            self._current_concurrent += 1
            return True

    def release(self) -> None:
        """释放并发槽位（请求完成后调用）"""
        with self._concurrent_cond:
            if self._current_concurrent > 0:
                self._current_concurrent -= 1
            self._concurrent_cond.notify_all()

    # ── 旧 API 兼容 ─────────────────────────────────────────

    def _check_old(self, tool_name: str) -> bool:
        """旧 API: 按工具类别限流"""
        category = self.get_category(tool_name)
        capacity, rate = self._limits.get(category, self._limits["default"])

        with self._lock:
            bucket = self._buckets.get(category)
            now = time.time()

            if bucket is None:
                self._buckets[category] = {
                    "tokens": capacity - 1,
                    "last_refill": now,
                }
                return True

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
        """根据工具名称确定类别（旧 API）"""
        name_lower = tool_name.lower()

        network_keywords = [
            "http", "fetch", "search", "web_", "browse", "download",
            "post", "xpath", "css", "scrape", "crawl", "news",
            "weather", "translate",
        ]
        if any(k in name_lower for k in network_keywords):
            return "network"

        shell_keywords = [
            "shell", "execute", "process", "run_program",
            "start_process", "stop_process",
        ]
        if any(k in name_lower for k in shell_keywords):
            return "shell"

        file_keywords = [
            "read_file", "write_file", "list_dir", "search_file",
            "compress", "decompress", "diff", "get_file_info",
        ]
        if any(k in name_lower for k in file_keywords):
            return "file"

        return "default"

    # ── 规则与桶管理 ─────────────────────────────────────────

    def register_rule(self, name: str, capacity: float, refill_rate: float) -> None:
        """注册限流规则"""
        with self._lock:
            self._rules[name] = (capacity, refill_rate)
            self._buckets[name] = TokenBucket(capacity=capacity, refill_rate=refill_rate)

    def _get_bucket(self, name: str) -> TokenBucket:
        """获取（或自动创建）令牌桶"""
        with self._lock:
            if name not in self._buckets:
                if name in self._rules:
                    cap, rate = self._rules[name]
                else:
                    cap, rate = 10.0, 1.0
                self._buckets[name] = TokenBucket(capacity=cap, refill_rate=rate)
            return self._buckets[name]

    def _get_endpoint_bucket(self, endpoint: str) -> TokenBucket:
        """获取接口令牌桶（尝试 endpoint/{ep} 和 {ep} 两种规则名）"""
        ep_key = f"endpoint/{endpoint}"
        with self._lock:
            if ep_key not in self._buckets:
                if ep_key in self._rules:
                    cap, rate = self._rules[ep_key]
                elif endpoint in self._rules:
                    cap, rate = self._rules[endpoint]
                else:
                    cap, rate = 10.0, 1.0
                self._buckets[ep_key] = TokenBucket(capacity=cap, refill_rate=rate)
            return self._buckets[ep_key]

    def _get_user_bucket(self, user_id: str) -> TokenBucket:
        """获取用户令牌桶（使用 "user" 规则的参数，按用户隔离）"""
        user_key = f"user/{user_id}"
        with self._lock:
            if user_key not in self._buckets:
                if "user" in self._rules:
                    cap, rate = self._rules["user"]
                else:
                    cap, rate = 10.0, 1.0
                self._buckets[user_key] = TokenBucket(capacity=cap, refill_rate=rate)
            return self._buckets[user_key]

    def wait_time(
        self,
        tool_name: Optional[str] = None,
        *,
        endpoint: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Any:
        """获取需要等待的时间

        新 API 返回 (float, str)：(等待秒数, 限流级别)
        旧 API 返回 float
        """
        if tool_name is not None and endpoint is None and user_id is None:
            return self._wait_time_old(tool_name)

        # 新 API：返回多级中最大等待时间
        waits = []
        global_wait = self._global_bucket.get_wait_time()
        if global_wait > 0:
            waits.append((global_wait, "global"))

        if endpoint is not None:
            ep_wait = self._get_endpoint_bucket(endpoint).get_wait_time()
            if ep_wait > 0:
                waits.append((ep_wait, "endpoint"))

        if user_id is not None:
            user_wait = self._get_user_bucket(user_id).get_wait_time()
            if user_wait > 0:
                waits.append((user_wait, "user"))

        if not waits:
            return (0.0, "none")
        return max(waits, key=lambda x: x[0])

    def _wait_time_old(self, tool_name: str) -> float:
        """旧 API 的 wait_time"""
        category = self.get_category(tool_name)
        capacity, rate = self._limits.get(category, self._limits["default"])

        with self._lock:
            bucket = self._buckets.get(category)
            if bucket is None:
                return 0.0
            if bucket["tokens"] >= 1:
                return 0.0
            needed = 1.0 - bucket["tokens"]
            wait = needed / rate if rate > 0 else 1.0
            return round(wait, 1)

    # ── 状态查询 ─────────────────────────────────────────────

    def get_status(self) -> dict:
        """获取限流器完整状态"""
        with self._lock:
            return {
                "max_concurrent": self.max_concurrent,
                "current_concurrent": self._current_concurrent,
                "strategy": self.strategy.value if isinstance(self.strategy, RateLimitStrategy) else str(self.strategy),
                "global_bucket": self._global_bucket.to_dict(),
                "rules": dict(self._rules),
                "buckets": {k: v.to_dict() for k, v in self._buckets.items() if isinstance(v, TokenBucket)},
            }

    def reset(self) -> None:
        """重置所有状态"""
        with self._lock:
            self._buckets.clear()
            self._global_bucket.reset()
            self._current_concurrent = 0
            logger.debug("限流器已重置")

    # ── 装饰器 ───────────────────────────────────────────────

    def limit(self, endpoint: Optional[str] = None):
        """同步限流装饰器"""
        def decorator(func):
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                user_id = kwargs.get("user_id")
                if not self.check(endpoint=endpoint, user_id=user_id):
                    raise RateLimitError(
                        error_code="RATE_LIMIT_EXCEEDED",
                        endpoint=endpoint,
                        user_id=user_id,
                    )
                try:
                    return func(*args, **kwargs)
                finally:
                    self.release()
            return wrapper
        return decorator

    def limit_async(self, endpoint: Optional[str] = None):
        """异步限流装饰器"""
        def decorator(func):
            @functools.wraps(func)
            async def wrapper(*args, **kwargs):
                user_id = kwargs.get("user_id")
                if not self.check(endpoint=endpoint, user_id=user_id):
                    raise RateLimitError(
                        error_code="RATE_LIMIT_EXCEEDED",
                        endpoint=endpoint,
                        user_id=user_id,
                    )
                try:
                    return await func(*args, **kwargs)
                finally:
                    self.release()
            return wrapper
        return decorator

    # ── 可观测性 ─────────────────────────────────────────────

    def _safe_metric(self, level: str) -> None:
        """埋点上报（吞掉异常不影响主流程）"""
        try:
            collector = get_business_metrics_collector()
            if collector and hasattr(collector, "record_rate_limit_trigger"):
                collector.record_rate_limit_trigger(level=level)
        except Exception:
            pass


# ── 限流器管理器 ────────────────────────────────────────────


class RateLimiterManager:
    """限流器管理器 — 按名称注册和复用 RateLimiter 实例"""

    def __init__(self):
        self._limiters: dict[str, RateLimiter] = {}
        self._lock = threading.Lock()

    def register(
        self,
        name: str,
        max_concurrent: int = 100,
        strategy: RateLimitStrategy = RateLimitStrategy.REJECT,
        **kwargs,
    ) -> RateLimiter:
        """注册（或替换）一个限流器"""
        with self._lock:
            limiter = RateLimiter(max_concurrent=max_concurrent, strategy=strategy, **kwargs)
            self._limiters[name] = limiter
            return limiter

    def get(self, name: str) -> RateLimiter:
        """获取限流器（不存在则自动创建默认实例）"""
        with self._lock:
            if name not in self._limiters:
                self._limiters[name] = RateLimiter()
            return self._limiters[name]

    def get_all_status(self) -> dict:
        """获取所有限流器状态"""
        with self._lock:
            return {name: limiter.get_status() for name, limiter in self._limiters.items()}

    def reset_all(self) -> None:
        """重置所有限流器"""
        with self._lock:
            for limiter in self._limiters.values():
                limiter.reset()


# ── 全局限流器注册表（单例模式） ────────────────────────────

_global_limiters: dict[str, RateLimiter] = {}
_global_limiters_lock = threading.Lock()
_default_limiter: Optional[RateLimiter] = None


def get_rate_limiter(name: str = "default", **kwargs) -> RateLimiter:
    """获取（或创建）全局共享的限流器实例"""
    global _default_limiter
    with _global_limiters_lock:
        if name == "default" and _default_limiter is not None:
            return _default_limiter
        if name not in _global_limiters:
            _global_limiters[name] = RateLimiter(**kwargs)
        if name == "default":
            _default_limiter = _global_limiters[name]
        return _global_limiters[name]


def register_rate_limiter(name: str, **kwargs) -> RateLimiter:
    """注册一个限流器到全局注册表"""
    with _global_limiters_lock:
        limiter = RateLimiter(**kwargs)
        _global_limiters[name] = limiter
        return limiter


def get_all_rate_limiter_status() -> dict:
    """获取所有限流器状态"""
    with _global_limiters_lock:
        return {name: limiter.get_status() for name, limiter in _global_limiters.items()}


def reset_global_limiters() -> None:
    """重置所有限流器（测试用）"""
    global _default_limiter
    with _global_limiters_lock:
        for limiter in _global_limiters.values():
            limiter.reset()
        _default_limiter = None


def _safe_call(func, *args, action="safe_call", **kwargs):
    """安全调用包装器——捕获异常并记录结构化日志后重新抛出"""
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
