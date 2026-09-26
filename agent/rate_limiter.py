"""工具调用限流器 — 基于令牌桶算法的多级速率限制

提供全局→接口→用户→并发的多级限流，失败时按优先级回退已消费的令牌。
支持 REJECT/QUEUE/DELAY 三种策略。

工具分类与默认限制（旧 API，向后兼容）：
- network: 5 tokens, 0.5 refill/s (网络工具)
- shell: 2 tokens, 0.2 refill/s (Shell 工具)
- file: 15 tokens, 1 refill/s (文件工具)
- default: 10 tokens, 1 refill/s (普通工具)

**分桶判据的唯一真相**：``data/tool_definitions/*.yaml`` 的 ``tags`` / ``effect``
（见 :func:`RateLimiter.get_category`）。**改动前**用一串子串关键词并按
network→shell→file 的顺序匹配，于是：

    search_memory / search_files / search_lifetrace / software_search
        因含 "search" 命中 network 关键词 ⇒ 本地检索与真实网络共用最紧的桶
        （5 容量、0.5/s）——**与危险程度和网络无关的误判**；
    edit / grep
        一个关键词都不命中 ⇒ 落进 default，与 file 桶（15 容量、1.0/s）分家。

改动后按 YAML 分桶，``_limits`` 的键与数值**一字未改**（``network`` / ``shell`` /
``file`` / ``default`` 的容量与补充速率保持设计值）。

**C2 背压三件套**（本节实现，全部**逐实例开关**，默认不改变任何既有实例的行为）：

1. **确认级低容量桶**（``CONFIRM_LEVEL_LIMITS`` + :meth:`RateLimiter._check_old` 的追加判定）：
   在既有分类桶之上，对 YAML 里 ``effective_confirm_level`` 为 L2/L3 的能力再叠一层低容量桶。
2. **工具层并发闸门**（``concurrency_gate=True`` + :meth:`RateLimiter.acquire_concurrent` /
   :meth:`RateLimiter.release`）：旧 API 原本只判速率不占并发额度，生产工具限流器
   （``agent/tools/__init__.py`` 经 :func:`tool_limiter_from_env` 构造）现在**成对**占用/归还。
3. **HTTP 入口并发闸门**（:class:`ConcurrencyGate` + :class:`ConcurrencyGateMiddleware`）：
   waitress ``threads=16`` 之外的一道硬上限 + **排队超时**（waitress 的 ``channel_timeout``
   不是排队超时，见 Q8 §1），超时/超限返回 429，不静默排队。
"""

from __future__ import annotations

import asyncio
import functools
import os
import threading
import time
import logging
import json
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional
from agent.logging_utils import log_dict

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


# ── 工具类别派生（唯一真相：data/tool_definitions/*.yaml 的 tags / effect）─────

#: 类别取值集合（与 ``_limits`` 的键一一对应；**不得增删**）
CATEGORIES = ("default", "network", "shell", "file")

#: tags → 类别，**按顺序先命中先返回**（web 最专有，故排第一）
_TAG_CATEGORY_RULES = (
    ("network", ("web",)),
    ("shell", ("shell", "process")),
    ("file", ("code", "document", "data", "software")),
)

#: 元数据与类别缓存的锁（首次派生要读 97 个 YAML，只做一次）
_CATEGORY_LOCK = threading.Lock()
#: ``name → ToolMeta`` 缓存；``None`` = 尚未加载
_TOOL_META_CACHE: Optional[dict] = None
#: ``工具名 → 类别`` 缓存（每次限流检查都会问同一个名字，别再读一遍 YAML）
_CATEGORY_CACHE: dict = {}

#: ``工具名 → 确认级`` 缓存（同 ``_CATEGORY_CACHE`` 的理由：热路径不重复读 YAML）
_LEVEL_CACHE: dict = {}

#: 确认级 → ``(容量, 每秒补充)``。**只给 L2/L3 设桶**：
#:   L0/L1 是日常读写（读文件、检索、列目录……），分类桶已覆盖它们的速率，
#:   再加一层只会平白拖慢正常链路，与"背压"要防的东西（危险能力的连发）无关。
#:
#: 取值依据（见 docs/audit_skill_governance/C2.md 的设定表与理由）：
#:   L2（10 个：edit / write_file / apply_patch / git / run_program / decompress /
#:       workspace_delete / schedule_task / fan_out / ext_send_channel）
#:       ⇒ (5, 0.5)：突发 5 次后 1 次/2s。**刻意留出正常编辑节奏**：
#:       L2 里 ``edit``/``write_file`` 是编码链路的主力，容量过小会把
#:       "写 6 个文件"变成"被限流"，那是把性能判据误当安全判据。
#:   L3（10 个：shell_execute / run_sandbox / ext_install / ext_uninstall /
#:       generate_tool / connect_mcp / disconnect_mcp / scan_mcp / ext_toggle /
#:       ext_configure）
#:       ⇒ (2, 0.1)：突发 2 次后 1 次/10s。给"扫描→连接"这类两步流程留出突发额度，
#:       但把"同一危险能力被连发"压到 ~10 秒一次。
CONFIRM_LEVEL_LIMITS: dict = {
    "L2": (5, 0.5),
    "L3": (2, 0.1),
}

#: 确认级取值集合（与 ``data/tool_definitions/*.yaml`` 的 ``effective_confirm_level`` 对齐）
CONFIRM_LEVELS = ("L0", "L1", "L2", "L3")


def _tool_meta() -> dict:
    """惰性加载并缓存工具能力元数据（``data/tool_definitions/*.yaml``）

    【为什么缓存】``get_category()`` 在**每次限流检查**里被调用，而加载要读 97 个
        YAML ⇒ 不缓存等于把 97 次文件 IO 放进热路径。
    【简易】加载失败（缺 ``yaml`` 依赖 / 目录不可读 / 任何异常）⇒ 空 dict，不抛异常；
        调用方据此回退字符串启发式（**不是** fail-closed，理由见 ``get_category``）。
    """
    global _TOOL_META_CACHE
    if _TOOL_META_CACHE is None:
        with _CATEGORY_LOCK:
            if _TOOL_META_CACHE is None:
                try:
                    from agent.lines import load_tool_meta
                    _TOOL_META_CACHE = dict(load_tool_meta())
                except Exception as e:  # noqa: BLE001  读不到元数据 ⇒ 回退启发式
                    logger.warning("[rate_limiter] 工具元数据加载失败（回退字符串启发式"
                                   "分桶）: %s: %s", type(e).__name__, e)
                    _TOOL_META_CACHE = {}
    return _TOOL_META_CACHE


def clear_category_cache() -> None:
    """清空"工具名 → 类别"/"工具名 → 确认级"缓存与元数据缓存（**测试用**）"""
    global _TOOL_META_CACHE
    with _CATEGORY_LOCK:
        _CATEGORY_CACHE.clear()
        _LEVEL_CACHE.clear()
        _TOOL_META_CACHE = None


def _category_from_meta(meta: Any) -> str:
    """按 YAML 元数据分桶：``tags`` 优先，其次 ``effect == "write"`` ⇒ file"""
    tags = {str(t).strip().lower() for t in (getattr(meta, "tags", ()) or ())}
    for category, keys in _TAG_CATEGORY_RULES:
        if tags.intersection(keys):
            return category
    if str(getattr(meta, "effect", "") or "").strip().lower() == "write":
        return "file"
    return "default"


def _level_from_meta(meta: Any) -> str:
    """按 YAML 元数据取确认级；取值缺失/非法 ⇒ ``""``（**不猜**，不设确认级桶）

    与 :meth:`RateLimiter.get_category` 同一纪律：限流是**性能**判据，
    读不出级别时按"最宽"处理（只留分类桶），不把链路卡死。
    """
    level = str(getattr(meta, "effective_confirm_level", "") or "").strip().upper()
    return level if level in CONFIRM_LEVELS else ""


def _category_from_name(tool_name: str) -> str:
    """**元数据缺失时**的字符串启发式兜底（改动前的原实现，顺序一字未改）

    它有两个已知缺陷（network 关键词过宽、edit/grep 无关键词），只有在 YAML 读不到
    或工具未登记时才会走到这里。
    """
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

    【C2 两个逐实例开关】**默认都为 False = 与改动前逐字一致**（其它实例——api_gateway /
    modules_api / 既有单测——的行为不受影响）；生产工具限流器由
    :func:`tool_limiter_from_env` 按 env 打开：
      - ``concurrency_gate``：旧 API 判定通过后**同时**占用并发额度，
        调用方必须用 :meth:`release` 成对归还（``agent/tools/__init__.py`` 用 try/finally）。
      - ``level_buckets``：在分类桶之上叠加 L2/L3 确认级低容量桶
        （``CONFIRM_LEVEL_LIMITS``）。
    """

    def __init__(
        self,
        limits: Optional[dict] = None,
        *,
        max_concurrent: int = 100,
        strategy: RateLimitStrategy = RateLimitStrategy.REJECT,
        concurrency_gate: bool = False,
        level_buckets: bool = False,
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

        # C2 开关（默认关 ⇒ 既有实例/既有单测行为一字不变）
        self.concurrency_gate = bool(concurrency_gate)
        self._level_buckets = bool(level_buckets)

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
        """旧 API: 按工具类别限流

        【C2】**分类桶部分（容量/速率/内联字典实现）一字未改**，只在其后**追加**
        「确认级桶」判定，且仅当 ``self._level_buckets`` 为真时才走
        （默认 False ⇒ 其它实例与既有单测逐字保持原行为）。
        两桶都通过才算放行；**确认级桶拒绝时退回分类桶刚扣掉的令牌**
        （模块既有纪律："失败时回退已消费的令牌"，否则一次被挡的调用会
        白吃一个分类令牌，等于把两类判据的代价叠加）。
        """
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
                category_ok = True
            else:
                elapsed = now - bucket["last_refill"]
                new_tokens = min(capacity, bucket["tokens"] + elapsed * rate)
                bucket["last_refill"] = now

                if new_tokens >= 1:
                    bucket["tokens"] = new_tokens - 1
                    category_ok = True
                else:
                    bucket["tokens"] = new_tokens
                    category_ok = False

        if not category_ok:
            return False

        if not self._level_buckets:
            return True

        level = self.get_confirm_level(tool_name)
        level_limit = CONFIRM_LEVEL_LIMITS.get(level)
        if level_limit is None:
            return True
        if self._consume_bucket("level:" + level, level_limit, time.time()):
            return True
        with self._lock:  # 退回分类桶令牌（见 docstring）
            refund = self._buckets.get(category)
            if isinstance(refund, dict):
                refund["tokens"] = min(capacity, refund["tokens"] + 1)
        return False

    def _consume_bucket(self, key: str, limits: tuple, now: float) -> bool:
        """按 ``(capacity, refill_rate)`` 消耗一个令牌（与分类桶同一实现口径）

        与 ``_check_old`` 里的分类桶一样，桶是 ``self._buckets`` 下的**内联字典**
        （``{"tokens", "last_refill"}``），不是 :class:`TokenBucket` 实例——
        沿用既有实现口径，避免同名字段下两种桶混用。
        """
        capacity, rate = limits
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                self._buckets[key] = {"tokens": capacity - 1, "last_refill": now}
                return True
            elapsed = now - bucket["last_refill"]
            new_tokens = min(capacity, bucket["tokens"] + elapsed * rate)
            bucket["last_refill"] = now
            if new_tokens >= 1:
                bucket["tokens"] = new_tokens - 1
                return True
            bucket["tokens"] = new_tokens
            return False

    def _bucket_wait_time(self, key: str, limits: tuple) -> float:
        """按 ``(capacity, refill_rate)`` 估算还需等待多久才能拿到 1 个令牌"""
        _capacity, rate = limits
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None or bucket["tokens"] >= 1:
                return 0.0
            needed = 1.0 - bucket["tokens"]
            return needed / rate if rate > 0 else 1.0

    def acquire_concurrent(self) -> bool:
        """获取一个并发槽位（**必须与 :meth:`release` 成对**）

        【C2】旧 API（``check(tool_name)``）只判速率、**不占**并发额度
        （见 :meth:`_check_old`），因此并发额度由调用方显式占用/归还：
        ``agent/tools/__init__.py`` 在限流通过后调用本方法、并在 try/finally 里
        :meth:`release`（异常/超时/未知工具路径都不泄漏额度）。

        ``concurrency_gate=False``（默认）时直接返回 True 且**不占额度**，
        调用方据此不应 release（生产调用点用 ``getattr(limiter, "concurrency_gate", False)``
        判定，见该处注释）。
        """
        if not self.concurrency_gate:
            return True
        return self._acquire_concurrent()

    def get_category(self, tool_name: str) -> str:
        """根据工具名确定类别（旧 API）

        **判据（唯一真相：``data/tool_definitions/*.yaml``）**，按 ``tags`` / ``effect``
        分桶，与 ``HITLManager.assess`` / ``tool_gate`` 共用同一份元数据：

            tags 含 web                                    → network
            tags 含 shell 或 process                        → shell
            tags 含 code / document / data / software，
            或 effect == "write"                            → file
            其余                                             → default

        **元数据缺失 ⇒ 回退改动前的字符串启发式（刻意不 fail-closed）**：
            限流是**性能**判据，不是安全判据 —— "分不出桶"按 default 放行不会让任何危险
            操作绕过审批，却会让"YAML 读不到"直接演变成"所有工具被最紧的桶限流、主链路
            卡死"。故此处与 :meth:`HITLManager.assess` 的 **fail-closed** 形成**刻意的不
            对称**：安全判据从严（读不到就不放行），性能判据从宽（读不到就走旧启发式）。

        返回值恒为 ``CATEGORIES`` 之一（``network`` / ``shell`` / ``file`` / ``default``）
        —— ``_limits`` 字典依赖它们；**签名与取值集合不得改动**。
        """
        name = str(tool_name or "").strip()
        if not name:
            return "default"

        with _CATEGORY_LOCK:
            cached = _CATEGORY_CACHE.get(name)
        if cached is not None:
            return cached

        metas = _tool_meta()
        meta = metas.get(name) or metas.get(name.lower())
        if meta is not None:
            category = _category_from_meta(meta)
        else:
            category = _category_from_name(name)
        if category not in CATEGORIES:  # 防御：派生结果越界即按 default（不放宽也不收紧）
            category = "default"

        with _CATEGORY_LOCK:
            _CATEGORY_CACHE[name] = category
        return category

    def get_confirm_level(self, tool_name: str) -> str:
        """取工具的**确认级**（``L0``~``L3``）；判不出返回 ``""``

        **与 ``agent.tool_gate`` / HITLManager 同一真相源**：``data/tool_definitions/*.yaml``
        经 ``agent.lines.load_tool_meta`` 读出的 ``effective_confirm_level``
        （复用 :func:`_tool_meta` 的缓存，不另起一份级别表、不额外读 YAML）。

        **判不出 ⇒ ``""``（不设确认级桶）**：与 :meth:`get_category` 同一纪律——
        限流是性能判据不是安全判据，读不到元数据时按最宽处理，
        不让"YAML 读不到"演变成"所有危险能力被最紧的桶限流、主链路卡死"。
        """
        name = str(tool_name or "").strip()
        if not name:
            return ""

        with _CATEGORY_LOCK:
            cached = _LEVEL_CACHE.get(name)
        if cached is not None:
            return cached

        metas = _tool_meta()
        meta = metas.get(name) or metas.get(name.lower())
        level = _level_from_meta(meta) if meta is not None else ""

        with _CATEGORY_LOCK:
            _LEVEL_CACHE[name] = level
        return level

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
        """旧 API 的 wait_time

        【C2】开了确认级桶时取**两个桶里更久的那个** —— 否则被确认级桶挡下的调用
        会拿到 ``retry_after=0.0``（分类桶还有令牌），报错文案里的重试提示就成了假话。
        分类桶分支的算式与返回值与改动前逐字一致（未开确认级桶时结果不变）。
        """
        category = self.get_category(tool_name)
        capacity, rate = self._limits.get(category, self._limits["default"])

        with self._lock:
            bucket = self._buckets.get(category)
            if bucket is None or bucket["tokens"] >= 1:
                wait = 0.0
            else:
                needed = 1.0 - bucket["tokens"]
                wait = needed / rate if rate > 0 else 1.0

        if self._level_buckets:
            level_limit = CONFIRM_LEVEL_LIMITS.get(self.get_confirm_level(tool_name))
            if level_limit is not None:
                wait = max(wait, self._bucket_wait_time("level:" + self.get_confirm_level(tool_name),
                                                        level_limit))
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


# ════════════════════════════════════════════════════════════
#  C2 · HTTP 入口并发闸门（背压三件套之三）+ env 工厂
# ════════════════════════════════════════════════════════════
# 【为什么必须在应用层做】waitress 3.0.2 的 ``channel_timeout=120`` **不是排队超时**：
#   请求在派发给工作线程**之前**就已写入 ``channel.requests``（channel.py:229-236），
#   而 maintenance() 只回收"没有任何排队请求且空闲超时"的通道（server.py:342-351）
#   ⇒ 服务端对"已解析但等不到线程"的请求**没有任何超时**，第 17~100 个连接会无限期排队，
#   只能靠客户端自己放弃（Q8 §1/§3.4 实测）。waitress 亦无排队超时配置项。
# 【与 waitress threads=16 的关系】threads 决定"能同时跑多少个请求"，
#   本闸门决定"允许多少个请求**开始跑**"：闸门上限 < threads 时，
#   被挡住的请求在闸门处按 queue_timeout 有界等待，超时即 429 ——
#   于是"排队"这件事**有界且可见**，而不是静默堆在 waitress 的任务队列里。

#: HTTP 闸门默认豁免前缀（逗号分隔，可用 ``CP_HTTP_GATE_EXEMPT`` 覆盖）
#: 【为什么必须豁免】① A1 的就绪门用进程内 ``/api/health`` 自证可服务性，
#:   闸门若拦住健康端点，启动自证会随并发抖动而假失败；
#:   ② 健康采集线程与前端状态栏高频轮询这些端点，让它们排队等于
#:   "心跳把自己饿死"，制造假故障。豁免的只是**廉价只读**端点。
DEFAULT_HTTP_GATE_EXEMPT = "/api/health,/api/heartbeat,/metrics,/static,/favicon.ico"

#: 视为"关闭"的开关取值
_FALSY_VALUES = frozenset({"0", "false", "no", "off", ""})


def _env_flag(name: str, default: bool) -> bool:
    """读布尔开关；未设置 ⇒ ``default``；``0/false/no/off``（不分大小写）⇒ False"""
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() not in _FALSY_VALUES


def _env_int(name: str, default: int) -> int:
    """读整数；非法/未设置 ⇒ ``default``（降级而不是让配置错误阻断启动）"""
    raw = os.environ.get(name)
    if raw is None:
        return int(default)
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("[rate_limiter] %s=%r 不是整数，回退默认 %s", name, raw, default)
        return int(default)


def _env_float(name: str, default: float) -> float:
    """读浮点；非法/未设置 ⇒ ``default``"""
    raw = os.environ.get(name)
    if raw is None:
        return float(default)
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("[rate_limiter] %s=%r 不是数字，回退默认 %s", name, raw, default)
        return float(default)


def _env_csv(name: str, default: str) -> tuple:
    """读逗号分隔列表"""
    raw = os.environ.get(name, default)
    return tuple(p.strip() for p in str(raw).split(",") if p.strip())


@dataclass(frozen=True)
class GateDecision:
    """一次闸门判定的结果

    ``reason`` 取值：``admitted``（拿到执行额度）/ ``exempt``（豁免路径）/
    ``timeout``（等满 queue_timeout 仍无额度）/ ``queue_full``（等待队列已达上限，立即拒）。
    """
    allowed: bool
    reason: str
    waited: float = 0.0


class ConcurrencyGate:
    """有界并发闸门：**同时执行**的请求数硬上限 + 有界排队（超时即拒）

    Args:
        max_concurrent: 同时**执行**的请求数上限（>0）
        queue_timeout: 在闸门处的最大等待秒数；<=0 表示"不排队，拿不到就立即拒"
        max_queue: 允许同时在闸门处等待的请求数上限；<=0 表示不限（默认，理由见下）
        exempt_prefixes: 豁免路径前缀（健康/指标/静态资源）
        name: 指标/日志里的闸门名

    **为什么 max_queue 默认关闭**：闸门的排队深度天然被 waitress ``threads`` 限住
    （最多 16 个请求同时在应用内），queue_timeout 已经给出确定的上界；
    再加一个队列上限只是把"等 20s 后被拒"提前成"立即被拒"，属于运维口味，
    故留成开关而不是默认行为（默认 = 只靠 queue_timeout 兜底）。
    """

    def __init__(
        self,
        max_concurrent: int = 8,
        queue_timeout: float = 20.0,
        max_queue: int = 0,
        exempt_prefixes: tuple = (),
        name: str = "http",
    ):
        if int(max_concurrent) < 1:
            raise ValueError("max_concurrent 必须 >= 1（闸门只允许收窄，不允许关闭）")
        self.max_concurrent = int(max_concurrent)
        self.queue_timeout = float(queue_timeout)
        self.max_queue = int(max_queue or 0)
        self.exempt_prefixes = tuple(exempt_prefixes or ())
        self.name = name
        #: 归还有界：多还一次会抛 ValueError（把"配对写错"暴露在测试里，
        #: 而不是让额度悄悄超过上限）
        self._sem = threading.BoundedSemaphore(self.max_concurrent)
        self._lock = threading.Lock()
        self._in_flight = 0
        self._waiting = 0
        self._admitted = 0
        self._completed = 0
        self._exempt = 0
        self._rejected_timeout = 0
        self._rejected_queue_full = 0
        self._unpaired_releases = 0
        #: 最近 256 次在闸门处的等待秒数（供压测/巡检读 p50/p90/max）
        self._waits = deque(maxlen=256)
        #: 等待超过该秒数即打一条 INFO（不刷屏，但"慢在哪"可复盘）
        self.slow_wait_log = 1.0

    # ── 判定 ────────────────────────────────────────────────

    def is_exempt(self, path: str) -> bool:
        """该路径是否豁免（健康/心跳/指标/静态资源）"""
        p = str(path or "")
        return any(p.startswith(prefix) for prefix in self.exempt_prefixes)

    def acquire(self, path: str = "") -> GateDecision:
        """申请一个执行额度：拿到 ⇒ ``allowed=True``；等满 queue_timeout ⇒ 拒绝

        **必须与 :meth:`release` 成对**（WSGI 中间件保证：响应迭代结束/关闭时归还）。
        """
        if self.is_exempt(path):
            with self._lock:
                self._exempt += 1
            return GateDecision(True, "exempt", 0.0)

        if self.max_queue > 0:
            with self._lock:
                over_queue = self._waiting >= self.max_queue
                if over_queue:
                    self._rejected_queue_full += 1
                    in_flight, waiting = self._in_flight, self._waiting
            if over_queue:
                self._reject_metric("queue_full")
                logger.warning(
                    "[背压] %s 闸门拒绝（等待队列已满 max_queue=%s, in_flight=%s, waiting=%s）",
                    self.name, self.max_queue, in_flight, waiting)
                return GateDecision(False, "queue_full", 0.0)

        t0 = time.monotonic()
        with self._lock:
            self._waiting += 1
        try:
            if self.queue_timeout > 0:
                got = self._sem.acquire(timeout=self.queue_timeout)
            else:
                got = self._sem.acquire(blocking=False)
        finally:
            with self._lock:
                self._waiting -= 1
        waited = time.monotonic() - t0

        with self._lock:
            if not got:
                self._rejected_timeout += 1
                self._waits.append(waited)
                in_flight, waiting = self._in_flight, self._waiting
            else:
                self._in_flight += 1
                self._admitted += 1
                self._waits.append(waited)
                in_flight = waiting = None

        if not got:
            self._reject_metric("timeout")
            logger.warning(
                "[背压] %s 闸门排队超时：等待 %.1fs 未获得执行额度"
                "（上限 %s，在跑 %s，仍在等 %s）=> 拒绝本次请求",
                self.name, waited, self.max_concurrent, in_flight, waiting)
            return GateDecision(False, "timeout", waited)

        if waited >= self.slow_wait_log:
            logger.info("[背压] %s 闸门排队 %.2fs 后放行（上限 %s）",
                        self.name, waited, self.max_concurrent)
        return GateDecision(True, "admitted", waited)

    def release(self) -> None:
        """归还执行额度（**必须与 acquire 成对**）

        未持有额度时**不**调用 ``sem.release()``：那会让额度虚增（BoundedSemaphore
        直接抛 ValueError、普通 Semaphore 则静默超过上限）。这里只记一条
        计数器 + ERROR 日志，把配对错误暴露出来，但不破坏闸门自身的守恒。
        """
        with self._lock:
            if self._in_flight <= 0:
                self._unpaired_releases += 1
                over = True
            else:
                self._in_flight -= 1
                self._completed += 1
                over = False
        if over:
            logger.error("[背压] %s 闸门检测到**未配对的 release**（当前在跑 0）=> 忽略本次归还",
                         self.name)
            return
        self._sem.release()

    # ── 观测 ────────────────────────────────────────────────

    def snapshot(self) -> dict:
        """闸门状态快照（日志/巡检用；不对外暴露成路由）"""
        with self._lock:
            waits = sorted(self._waits)
            return {
                "name": self.name,
                "max_concurrent": self.max_concurrent,
                "queue_timeout": self.queue_timeout,
                "max_queue": self.max_queue,
                "in_flight": self._in_flight,
                "waiting": self._waiting,
                "admitted": self._admitted,
                "completed": self._completed,
                "exempt": self._exempt,
                "rejected_timeout": self._rejected_timeout,
                "rejected_queue_full": self._rejected_queue_full,
                "unpaired_releases": self._unpaired_releases,
                "wait_p50": _percentile(waits, 50),
                "wait_p90": _percentile(waits, 90),
                "wait_max": waits[-1] if waits else 0.0,
            }

    def _reject_metric(self, level: str) -> None:
        """拒绝埋点（吞异常，不影响拒绝语义）"""
        try:
            collector = get_business_metrics_collector()
            if collector and hasattr(collector, "record_rate_limit_trigger"):
                collector.record_rate_limit_trigger(level=level)
        except Exception:
            pass

    @property
    def retry_after(self) -> int:
        """建议客户端等待秒数（响应头 Retry-After）"""
        return max(1, int(min(self.queue_timeout or 1.0, 30.0)))


def _percentile(sorted_values: list, pct: float) -> float:
    """最近邻分位数（``values`` 必须已排序）；空 ⇒ 0.0"""
    if not sorted_values:
        return 0.0
    if pct >= 100:
        return sorted_values[-1]
    idx = int(round((pct / 100.0) * (len(sorted_values) - 1)))
    return sorted_values[max(0, min(idx, len(sorted_values) - 1))]


class _GateReleasingIterable:
    """WSGI 响应迭代器包装：迭代结束**或** close() 时归还额度（恰好一次）"""

    __slots__ = ("_iterable", "_iterator", "_gate", "_released")

    def __init__(self, iterable, gate: ConcurrencyGate):
        # WSGI 允许应用返回**可迭代对象**（Flask 常常直接返回 list），不是只有迭代器；
        # 故这里显式取一次 iter()，__next__ 只对它推进。
        self._iterable = iterable
        self._iterator = iter(iterable)
        self._gate = gate
        self._released = False

    def __iter__(self):
        return self

    def __next__(self):
        try:
            return next(self._iterator)
        except BaseException:
            self._release_once()
            raise

    def _release_once(self) -> None:
        if not self._released:
            self._released = True
            self._gate.release()

    def close(self) -> None:
        try:
            close = getattr(self._iterable, "close", None)
            if close is not None:
                close()
        finally:
            self._release_once()


class ConcurrencyGateMiddleware:
    """WSGI 中间件：在**请求入口**申请执行额度，响应迭代结束后归还

    【为什么是 WSGI 而不是 Flask before/after_request】
      Flask 的 ``teardown_request`` 在"视图返回、响应体**还没**被服务器迭代完"时
      就执行（``Flask.wsgi_app`` 的 finally 早于 WSGI 迭代），对 SSE / 流式响应会
      **提前**归还额度，于是闸门管不住真正还在跑的流式请求。WSGI 迭代器的
      ``close()``（waitress 在响应发送完毕后调用）才是"这个请求真的结束了"。
    【失败姿态】应用自身抛异常 ⇒ 立刻归还后再抛出（额度绝不泄漏）。
    【豁免路径**不包装**（实测踩到的坑）】豁免请求（健康探针、/metrics 抓取）**没有**
      占用额度，若也套上"迭代结束即 release"，它就会去归还**别人**的额度：
      实测服务运行 1 分钟内产生 **473 条** "未配对的 release"，
      闸门因此被反复放水、永远填不满（压测中 8 条长轮询占不住 8 个额度，
      21 并发全部 200、0 个 429）。⇒ 只有 reason=="admitted" 才包装/归还。
    """

    def __init__(self, app, gate: ConcurrencyGate):
        self.app = app
        self.gate = gate

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO") or "/"
        decision = self.gate.acquire(path)
        if not decision.allowed:
            return self._reject_response(environ, start_response, decision)
        holds_slot = decision.reason == "admitted"
        try:
            iterable = self.app(environ, start_response)
        except BaseException:
            if holds_slot:
                self.gate.release()
            raise
        if not holds_slot:
            return iterable          # 豁免请求：不占额度，也**不归还**（见 docstring）
        return _GateReleasingIterable(iterable, self.gate)

    def _reject_response(self, environ, start_response, decision: GateDecision):
        """429 + JSON（可识别的拒绝，**不是**静默排队）"""
        is_timeout = decision.reason == "timeout"
        payload = {
            "ok": False,
            "error": (
                "服务繁忙：在并发闸门处等待 %.1fs 仍未获得执行额度（上限 %s）⇒ 本次请求未执行，请稍后重试"
                % (decision.waited, self.gate.max_concurrent)
                if is_timeout else
                "服务繁忙：闸门等待队列已满（max_queue=%s，上限 %s）⇒ 本次请求未执行，请稍后重试"
                % (self.gate.max_queue, self.gate.max_concurrent)
            ),
            "error_code": "SERVER_BUSY_TIMEOUT" if is_timeout else "SERVER_BUSY",
            "gate": self.gate.name,
            "limit": self.gate.max_concurrent,
            "queue_timeout": self.gate.queue_timeout,
            "waited": round(decision.waited, 3),
            "retry_after": self.gate.retry_after,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Retry-After", str(self.gate.retry_after)),
            ("Cache-Control", "no-store"),
        ]
        start_response("429 Too Many Requests", headers)
        return [body]


def build_http_gate_from_env() -> Optional[ConcurrencyGate]:
    """按 env 构造 HTTP 入口闸门；**开关关闭 ⇒ 返回 None**（回到无闸门的现状）

    | env | 默认 | 含义 |
    |---|---|---|
    | ``CP_HTTP_CONCURRENCY_GATE`` | 1 | 总开关，置 0 ⇒ 不装闸门（可回滚） |
    | ``CP_HTTP_MAX_CONCURRENT`` | 8 | 并发**执行**上限（waitress threads=16 的一半，给健康采集/后台留余量） |
    | ``CP_HTTP_QUEUE_TIMEOUT`` | 20 | 闸门处最大等待秒数（对齐 15s 前端预算 + 余量），超时 ⇒ 429 |
    | ``CP_HTTP_MAX_QUEUE`` | 0 | 等待队列上限；0 = 不限（只靠 queue_timeout 兜底） |
    | ``CP_HTTP_GATE_EXEMPT`` | 见 :data:`DEFAULT_HTTP_GATE_EXEMPT` | 豁免路径前缀（逗号分隔） |
    """
    if not _env_flag("CP_HTTP_CONCURRENCY_GATE", True):
        logger.info("[背压] HTTP 入口并发闸门已按 CP_HTTP_CONCURRENCY_GATE=0 关闭（回到现状）")
        return None
    gate = ConcurrencyGate(
        max_concurrent=_env_int("CP_HTTP_MAX_CONCURRENT", 8),
        queue_timeout=_env_float("CP_HTTP_QUEUE_TIMEOUT", 20.0),
        max_queue=_env_int("CP_HTTP_MAX_QUEUE", 0),
        exempt_prefixes=_env_csv("CP_HTTP_GATE_EXEMPT", DEFAULT_HTTP_GATE_EXEMPT),
        name="http",
    )
    logger.info("[背压] HTTP 入口并发闸门已装载：上限 %s、排队超时 %.1fs、豁免 %s",
                gate.max_concurrent, gate.queue_timeout, list(gate.exempt_prefixes))
    return gate


def tool_limiter_from_env() -> "RateLimiter":
    """按 env 构造**生产工具限流器**（``agent/tools/__init__.py`` 的唯一构造点）

    | env | 默认 | 含义 |
    |---|---|---|
    | ``CP_TOOL_CONCURRENCY_GATE`` | 1 | 工具层并发闸门；置 0 ⇒ 与改动前一致（只判速率） |
    | ``CP_TOOL_MAX_CONCURRENT`` | 16 | 同时**执行**的工具调用上限（= LLM 池/线程数规模，非主约束） |
    | ``CP_TOOL_LEVEL_BUCKET`` | 1 | L2/L3 确认级低容量桶；置 0 ⇒ 只留分类桶（改动前行为） |

    分类桶（``_limits``）与改动前**取值一字未改**——本工厂只决定新加的两层开不开。

    **闸门关掉时 ``max_concurrent`` 保持构造默认 100**：闸门不开就没人 acquire，
    该字段无实际作用，"回到现状"应当逐字回到现状（含字段值），而不是留一个
    改过的数字给后续读者误判。
    """
    gate_on = _env_flag("CP_TOOL_CONCURRENCY_GATE", True)
    return RateLimiter(
        max_concurrent=_env_int("CP_TOOL_MAX_CONCURRENT", 16) if gate_on else 100,
        concurrency_gate=gate_on,
        level_buckets=_env_flag("CP_TOOL_LEVEL_BUCKET", True),
    )



def _safe_call(func, *args, action="safe_call", **kwargs):
    """安全调用包装器——捕获异常并记录结构化日志后重新抛出"""
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.error(log_dict({'module_name': 'rate_limiter', 'action': action + '.failed', 'error': f'{type(e).__name__}: {e}'}))
        raise
