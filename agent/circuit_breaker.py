#!/usr/bin/env python3
"""熔断器 — 三状态（closed/open/half-open）故障保护

在连续错误率达到阈值时打开熔断，阻止请求打到下游依赖；
经过冷却期后进入半开状态，放行少量探测请求，成功则恢复，失败则重新熔断。

设计要点（与项目 memory 一致）：
- 三状态：closed（正常）→ open（熔断）→ half_open（探测）→ closed
- 错误率阈值默认 30%（可配置）
- 冷却期 60s，半开探测请求数 3
- 自动恢复：半开状态下成功达到阈值即恢复 closed
- 性能开销 < 5%（无锁读快路径 + 锁仅写状态变更）
- 与 Critic 评估、Schema 验证、Tool 调用集成

可观测性约束：
- 所有状态转换输出结构化 JSON 日志（trace_id/module_name/action/duration_ms）
- 状态变化埋点上报 BusinessMetricsCollector（吞掉异常不影响主流程）
"""

from __future__ import annotations

import functools
import json
import logging
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# trace_id 上下文（与项目其他模块对齐）
_trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="")


def set_trace_id(trace_id: str) -> None:
    """设置当前上下文的 trace_id"""
    _trace_id_ctx.set(trace_id or "")


def get_trace_id() -> str:
    """获取当前上下文的 trace_id"""
    return _trace_id_ctx.get()


class CircuitState(str, Enum):
    """熔断器三状态"""
    CLOSED = "closed"        # 正常放行
    OPEN = "open"            # 熔断，拒绝请求
    HALF_OPEN = "half_open"  # 半开，放行探测请求


class CircuitBreakerError(Exception):
    """熔断器打开时抛出的业务错误（带明确业务错误码）

    Attributes:
        error_code: 业务错误码，默认 "CIRCUIT_BREAKER_OPEN"
        state: 触发熔断时的熔断器状态
        name: 熔断器名称（便于定位是哪个依赖被熔断）
    """

    def __init__(
        self,
        message: str,
        error_code: str = "CIRCUIT_BREAKER_OPEN",
        state: CircuitState = CircuitState.OPEN,
        name: Optional[str] = None,
    ):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.state = state
        self.name = name


@dataclass
class CircuitBreakerConfig:
    """熔断器配置（dataclass，便于整体传入与序列化）

    字段说明：
        failure_threshold: 错误率阈值（0-1），默认 0.3 (30%)
        min_requests: 计算错误率所需的最少请求数（防止样本不足误判）
        reset_timeout: 熔断冷却期（秒），到期后进入半开
        window_seconds: 错误率计算的时间窗口（秒）
        max_attempts: 半开状态下最大探测请求数（同时作为成功恢复阈值）
        name: 熔断器名称（用于日志和指标）
    """
    failure_threshold: float = 0.3
    min_requests: int = 5
    reset_timeout: float = 30.0
    window_seconds: float = 60.0
    max_attempts: int = 3
    name: str = "default"


@dataclass
class CircuitStats:
    """熔断器统计快照

    新字段名（与测试契约对齐）：
        total_requests: 总请求数
        successes: 成功请求数
        failures: 失败请求数
        consecutive_failures: 连续失败数
        state_transitions: 状态转换次数
        last_reset: 最后一次重置的时间戳

    向后兼容属性（只读 property 别名，供旧调用方使用）：
        total_calls → total_requests
        success_count → successes
        failure_count → failures
    """
    state: CircuitState = CircuitState.CLOSED
    total_requests: int = 0
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    last_failure_time: float = 0.0
    last_state_change: float = 0.0
    half_open_attempts: int = 0
    half_open_successes: int = 0
    # 新增：状态转换计数与最后重置时间
    state_transitions: int = 0
    last_reset: float = 0.0
    # 历史调用窗口（用于错误率计算）
    _window_entries: list = field(default_factory=list)  # [(timestamp, is_success)]
    _window_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ── 向后兼容只读属性（旧字段名 → 新字段名）──────────────
    @property
    def total_calls(self) -> int:
        """旧字段名别名（向后兼容）"""
        return self.total_requests

    @property
    def success_count(self) -> int:
        """旧字段名别名（向后兼容）"""
        return self.successes

    @property
    def failure_count(self) -> int:
        """旧字段名别名（向后兼容）"""
        return self.failures


class CircuitBreaker:
    """熔断器实现

    构造方式（三种，均向后兼容）：
        1. CircuitBreaker()                                  # 无参，使用默认配置
        2. CircuitBreaker(CircuitBreakerConfig(...))         # 传入配置对象
        3. CircuitBreaker(name="x", failure_threshold=0.3)   # 散列关键字参数

    Args:
        config: 可选的 CircuitBreakerConfig 配置对象
        **kwargs: 散列配置参数（向后兼容），支持 name/failure_threshold/
                  min_calls/cooldown_seconds/half_open_max_calls/
                  half_open_success_threshold/window_seconds
    """

    def __init__(
        self,
        config: Optional[CircuitBreakerConfig] = None,
        **kwargs,
    ):
        if config is not None and isinstance(config, CircuitBreakerConfig):
            # ── 配置对象路径 ──────────────────────────────
            name = config.name
            failure_threshold = config.failure_threshold
            min_calls = config.min_requests
            cooldown_seconds = config.reset_timeout
            half_open_max_calls = config.max_attempts
            # 半开成功恢复阈值 = 最大探测数（探测全成功才恢复）
            half_open_success_threshold = config.max_attempts
            window_seconds = config.window_seconds
            self._config = config
        else:
            # ── 散列关键字参数路径（向后兼容）─────────────
            # 兼容首个位置参数为 name 字符串的旧用法
            if config is not None and isinstance(config, str):
                kwargs.setdefault("name", config)
            name = kwargs.get("name", "default")
            failure_threshold = kwargs.get("failure_threshold", 0.3)
            min_calls = kwargs.get("min_calls", 5)
            cooldown_seconds = kwargs.get("cooldown_seconds", 60.0)
            half_open_max_calls = kwargs.get("half_open_max_calls", 3)
            half_open_success_threshold = kwargs.get(
                "half_open_success_threshold", 2
            )
            window_seconds = kwargs.get("window_seconds", 60.0)
            # 构造等价配置对象，便于外部读取统一配置视图
            self._config = CircuitBreakerConfig(
                failure_threshold=failure_threshold,
                min_requests=min_calls,
                reset_timeout=cooldown_seconds,
                window_seconds=window_seconds,
                max_attempts=half_open_max_calls,
                name=name,
            )

        # 参数校验（failure_threshold ∈ (0, 1]，1.0 合法：全失败才熔断策略）
        if not 0 < failure_threshold <= 1:
            raise ValueError(f"failure_threshold 必须在 (0,1]，实际 {failure_threshold}")
        if min_calls < 1:
            raise ValueError(f"min_calls 必须 ≥1，实际 {min_calls}")

        self.name = name
        self.failure_threshold = failure_threshold
        self.min_calls = min_calls
        self.cooldown_seconds = cooldown_seconds
        self.half_open_max_calls = half_open_max_calls
        self.half_open_success_threshold = half_open_success_threshold
        self.window_seconds = window_seconds

        now = time.time()
        self._stats = CircuitStats(
            last_state_change=now,
            last_reset=now,
        )
        self._lock = threading.RLock()

    # ── 状态查询 ──────────────────────────────────────────────

    @property
    def state(self) -> CircuitState:
        """当前状态（自动从 OPEN 转为 HALF_OPEN）"""
        with self._lock:
            self._maybe_transition_to_half_open()
            return self._stats.state

    @property
    def stats(self) -> CircuitStats:
        """统计快照（不可变视图，向后兼容旧字段名访问）"""
        with self._lock:
            return CircuitStats(
                state=self._stats.state,
                total_requests=self._stats.total_requests,
                successes=self._stats.successes,
                failures=self._stats.failures,
                consecutive_failures=self._stats.consecutive_failures,
                last_failure_time=self._stats.last_failure_time,
                last_state_change=self._stats.last_state_change,
                half_open_attempts=self._stats.half_open_attempts,
                half_open_successes=self._stats.half_open_successes,
                state_transitions=self._stats.state_transitions,
                last_reset=self._stats.last_reset,
            )

    @property
    def metrics(self) -> CircuitStats:
        """指标对象（返回内部统计对象，支持属性访问）

        暴露字段：total_requests/successes/failures/
                  consecutive_failures/state_transitions/last_reset
        """
        return self._stats

    @property
    def _window_entries(self) -> list:
        """窗口条目列表（向后兼容外部访问）"""
        return self._stats._window_entries

    # ── 调用入口 ──────────────────────────────────────────────

    def call(self, func: Callable, *args, **kwargs) -> Any:
        """通过熔断器执行函数

        Args:
            func: 可调用对象
            *args, **kwargs: 传给 func 的参数

        Returns:
            func 的返回值

        Raises:
            CircuitBreakerError: 熔断器打开时
            Exception: func 抛出的原异常（同时记录失败）
        """
        if not self.allow_request():
            self._log_action("circuit_blocked", {"state": self._stats.state.value})
            raise CircuitBreakerError(
                f"熔断器 [{self.name}] 已打开，拒绝请求",
                state=self._stats.state,
                name=self.name,
            )

        is_success = False
        try:
            result = func(*args, **kwargs)
            is_success = True
            return result
        except Exception:
            # 重新抛出原异常，但要先记录失败
            raise
        finally:
            self.record_result(is_success)

    def allow_request(self) -> bool:
        """是否允许请求通过

        - CLOSED: 总是允许
        - OPEN: 冷却期到期则转为 HALF_OPEN 并允许探测，否则拒绝
        - HALF_OPEN: 探测数未达上限则允许，否则拒绝
        """
        with self._lock:
            self._maybe_transition_to_half_open()
            current = self._stats.state

            if current == CircuitState.CLOSED:
                return True
            if current == CircuitState.OPEN:
                return False
            # HALF_OPEN: 限制并发探测
            if self._stats.half_open_attempts < self.half_open_max_calls:
                self._stats.half_open_attempts += 1
                return True
            return False

    def record_result(self, is_success: bool) -> None:
        """记录调用结果，触发状态转换"""
        with self._lock:
            now = time.time()
            self._stats.total_requests += 1
            if is_success:
                self._stats.successes += 1
                self._stats.consecutive_failures = 0
            else:
                self._stats.failures += 1
                self._stats.consecutive_failures += 1
                self._stats.last_failure_time = now

            # 维护滑动窗口
            with self._stats._window_lock:
                self._stats._window_entries.append((now, is_success))
                cutoff = now - self.window_seconds
                self._stats._window_entries = [
                    (t, s) for t, s in self._stats._window_entries if t >= cutoff
                ]

            current = self._stats.state
            if current == CircuitState.HALF_OPEN:
                self._handle_half_open_result(is_success, now)
            elif current == CircuitState.CLOSED:
                self._maybe_open_circuit(now)

    # ── 便捷方法（与 output_schema.py 等调用方对齐） ─────────
    def record_failure(self) -> None:
        """记录一次失败调用（record_result(False) 的语义化别名）"""
        self.record_result(False)

    def record_success(self) -> None:
        """记录一次成功调用（record_result(True) 的语义化别名）"""
        self.record_result(True)

    # ── 状态转换内部方法 ─────────────────────────────────────

    def _maybe_transition_to_half_open(self) -> None:
        """OPEN 状态冷却期到期后转为 HALF_OPEN（必须持有锁）"""
        if self._stats.state != CircuitState.OPEN:
            return
        elapsed = time.time() - self._stats.last_state_change
        if elapsed >= self.cooldown_seconds:
            self._set_state(CircuitState.HALF_OPEN)
            self._stats.half_open_attempts = 0
            self._stats.half_open_successes = 0

    def _handle_half_open_result(self, is_success: bool, now: float) -> None:
        """半开状态下处理结果（必须持有锁）"""
        if is_success:
            self._stats.half_open_successes += 1
            if self._stats.half_open_successes >= self.half_open_success_threshold:
                # 探测成功，恢复 CLOSED
                self._set_state(CircuitState.CLOSED)
                self._stats.consecutive_failures = 0
        else:
            # 探测失败，重新打开
            self._set_state(CircuitState.OPEN)

    def _maybe_open_circuit(self, now: float) -> None:
        """CLOSED 状态下检查错误率，达标则打开熔断（必须持有锁）"""
        with self._stats._window_lock:
            window = list(self._stats._window_entries)
        if len(window) < self.min_calls:
            return
        failures = sum(1 for _, s in window if not s)
        error_rate = failures / len(window)
        if error_rate >= self.failure_threshold:
            self._set_state(CircuitState.OPEN)

    def _set_state(self, new_state: CircuitState) -> None:
        """切换状态并记录日志，同时递增状态转换计数"""
        old_state = self._stats.state
        if old_state == new_state:
            return
        self._stats.state = new_state
        self._stats.last_state_change = time.time()
        # 递增状态转换计数
        self._stats.state_transitions += 1
        self._log_action(
            "circuit_state_changed",
            {"from": old_state.value, "to": new_state.value},
        )

    def _prune_window(self) -> None:
        """清理过期窗口条目（按 window_seconds 阈值裁剪）"""
        with self._lock:
            now = time.time()
            cutoff = now - self.window_seconds
            with self._stats._window_lock:
                self._stats._window_entries = [
                    (t, s) for t, s in self._stats._window_entries if t >= cutoff
                ]

    # ── 控制方法 ──────────────────────────────────────────────

    def reset(self) -> None:
        """重置熔断器到 CLOSED 状态（主要用于测试）"""
        with self._lock:
            now = time.time()
            self._stats = CircuitStats(
                last_state_change=now,
                last_reset=now,
            )
            self._log_action("circuit_reset", {})

    def force_open(self) -> None:
        """强制打开熔断器（手动降级场景）"""
        with self._lock:
            self._set_state(CircuitState.OPEN)

    def force_close(self) -> None:
        """强制关闭熔断器（手动恢复场景）"""
        with self._lock:
            self._set_state(CircuitState.CLOSED)
            self._stats.consecutive_failures = 0

    # ── 状态视图 ──────────────────────────────────────────────

    def get_status(self) -> dict:
        """返回熔断器状态快照（dict 形式，便于序列化与监控上报）

        返回字段：
            name: 熔断器名称
            state: 当前状态（字符串）
            failure_threshold: 错误率阈值
            reset_timeout: 冷却期（秒）
            window_seconds: 时间窗口（秒）
            min_requests: 最少请求数
            max_attempts: 半开最大探测数
            metrics: 指标 dict（total_requests/successes/failures/
                     consecutive_failures/state_transitions/last_reset）
            current_failure_rate: 当前窗口失败率
            time_since_last_state_change: 距上次状态转换的秒数
            window_entries_count: 窗口条目数
        """
        with self._lock:
            self._maybe_transition_to_half_open()
            now = time.time()
            # 计算窗口内失败率
            with self._stats._window_lock:
                window = list(self._stats._window_entries)
            if window:
                failures_in_window = sum(1 for _, s in window if not s)
                current_failure_rate = failures_in_window / len(window)
            else:
                current_failure_rate = 0.0
            return {
                "name": self.name,
                "state": self._stats.state.value,
                "failure_threshold": self.failure_threshold,
                "reset_timeout": self.cooldown_seconds,
                "window_seconds": self.window_seconds,
                "min_requests": self.min_calls,
                "max_attempts": self.half_open_max_calls,
                "metrics": {
                    "total_requests": self._stats.total_requests,
                    "successes": self._stats.successes,
                    "failures": self._stats.failures,
                    "consecutive_failures": self._stats.consecutive_failures,
                    "state_transitions": self._stats.state_transitions,
                    "last_reset": self._stats.last_reset,
                },
                "current_failure_rate": current_failure_rate,
                "time_since_last_state_change": now - self._stats.last_state_change,
                "window_entries_count": len(window),
            }

    # ── 装饰器接口 ────────────────────────────────────────────

    def protect(self, func: Callable) -> Callable:
        """同步装饰器：通过熔断器保护目标函数

        使用方式：
            @breaker.protect
            def call_api(): ...

        熔断器打开时抛出 CircuitBreakerError；函数异常时记录失败并重新抛出。
        """
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if not self.allow_request():
                raise CircuitBreakerError(
                    f"熔断器 [{self.name}] 已打开，拒绝请求",
                    state=self._stats.state,
                    name=self.name,
                )
            is_success = False
            try:
                result = func(*args, **kwargs)
                is_success = True
                return result
            finally:
                self.record_result(is_success)

        return wrapper

    def protect_async(self, func: Callable) -> Callable:
        """异步装饰器：通过熔断器保护目标异步函数

        使用方式：
            @breaker.protect_async
            async def call_api(): ...
        """
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            if not self.allow_request():
                raise CircuitBreakerError(
                    f"熔断器 [{self.name}] 已打开，拒绝请求",
                    state=self._stats.state,
                    name=self.name,
                )
            is_success = False
            try:
                result = await func(*args, **kwargs)
                is_success = True
                return result
            finally:
                self.record_result(is_success)

        return wrapper

    # ── 可观测性 ──────────────────────────────────────────────

    def _log_action(self, action: str, payload: dict) -> None:
        """输出结构化 JSON 日志（trace_id/module_name/action/duration_ms）

        埋点失败不影响主业务流程（吞掉异常，仅日志记录）。
        """
        try:
            log_entry = {
                "trace_id": get_trace_id(),
                "module_name": f"circuit_breaker.{self.name}",
                "action": action,
                "duration_ms": 0,
                "timestamp": time.time(),
                **payload,
            }
            logger.info(json.dumps(log_entry, ensure_ascii=False))
        except Exception as exc:
            logger.debug("熔断器日志记录失败: %s", exc)


# ── 装饰器形式 ──────────────────────────────────────────────

def circuit_protected(
    name: str = "default",
    failure_threshold: float = 0.3,
    cooldown_seconds: float = 60.0,
    **kwargs,
):
    """函数装饰器：通过熔断器保护目标函数

    使用方式：
        @circuit_protected("external_api", failure_threshold=0.5)
        def call_external(): ...
    """
    breaker = CircuitBreaker(
        name=name,
        failure_threshold=failure_threshold,
        cooldown_seconds=cooldown_seconds,
        **kwargs,
    )

    def decorator(func):
        def wrapper(*args, **kw):
            return breaker.call(func, *args, **kw)
        wrapper.circuit_breaker = breaker
        return wrapper
    return decorator


# ── 全局熔断器注册表（按名称复用，避免每个调用点都新建实例） ─────
_breakers: dict[str, "CircuitBreaker"] = {}
_breakers_lock = threading.Lock()


def get_circuit_breaker(
    name: str = "default",
    failure_threshold: float = 0.3,
    cooldown_seconds: float = 60.0,
    **kwargs,
) -> "CircuitBreaker":
    """按名称获取（或创建）全局共享的熔断器实例

    幂等：同名重复调用返回同一实例；首次调用按参数创建。
    线程安全：内部使用 `_breakers_lock` 保护注册表读写。
    """
    with _breakers_lock:
        if name not in _breakers:
            _breakers[name] = CircuitBreaker(
                name=name,
                failure_threshold=failure_threshold,
                cooldown_seconds=cooldown_seconds,
                **kwargs,
            )
        return _breakers[name]


def register_circuit_breaker(
    name: str,
    config: Optional[CircuitBreakerConfig] = None,
) -> "CircuitBreaker":
    """注册（或覆盖）全局共享的熔断器实例

    Args:
        name: 熔断器名称
        config: 可选的配置对象；为 None 时按 name 创建默认配置

    Returns:
        新建的熔断器实例
    """
    with _breakers_lock:
        if config is None:
            config = CircuitBreakerConfig(name=name)
        breaker = CircuitBreaker(config)
        _breakers[name] = breaker
        return breaker


def get_all_circuit_breaker_status() -> dict:
    """获取所有全局熔断器的状态快照（dict 形式）

    Returns:
        {name: status_dict, ...}
    """
    with _breakers_lock:
        return {
            name: breaker.get_status()
            for name, breaker in _breakers.items()
        }


def reset_breakers() -> None:
    """清空全局熔断器注册表（测试用：在 fixture 中重置状态）"""
    with _breakers_lock:
        _breakers.clear()


# ── 熔断器管理器（多实例隔离场景） ─────────────────────────────
class CircuitBreakerManager:
    """熔断器管理器：管理一组命名熔断器（独立于全局注册表）

    适用场景：需要多套独立熔断器集合（如不同租户/模块）时使用。
    """

    def __init__(self):
        self._breakers: dict[str, "CircuitBreaker"] = {}
        self._lock = threading.Lock()

    def register(
        self,
        name: str,
        config: Optional[CircuitBreakerConfig] = None,
    ) -> "CircuitBreaker":
        """注册（或覆盖）一个命名熔断器

        Args:
            name: 熔断器名称
            config: 可选配置对象；为 None 时按 name 创建默认配置

        Returns:
            新建的熔断器实例
        """
        with self._lock:
            if config is None:
                config = CircuitBreakerConfig(name=name)
            breaker = CircuitBreaker(config)
            self._breakers[name] = breaker
            return breaker

    def get(self, name: str) -> "CircuitBreaker":
        """获取命名熔断器；不存在则自动创建默认配置实例"""
        with self._lock:
            if name not in self._breakers:
                config = CircuitBreakerConfig(name=name)
                self._breakers[name] = CircuitBreaker(config)
            return self._breakers[name]

    def get_all_status(self) -> dict:
        """获取所有熔断器的状态快照"""
        with self._lock:
            return {
                name: breaker.get_status()
                for name, breaker in self._breakers.items()
            }

    def reset_all(self) -> None:
        """重置所有熔断器到初始 CLOSED 状态"""
        with self._lock:
            for breaker in self._breakers.values():
                breaker.reset()


# ── 别名（与测试契约对齐） ─────────────────────────────────────
CircuitBreakerState = CircuitState
CircuitBreakerMetrics = CircuitStats


# ════════════════════════════════════════════════════════════════
#  三级熔断器（SESSION / USER / GLOBAL）
# ════════════════════════════════════════════════════════════════


class CircuitScope(str, Enum):
    """三级熔断器作用域

    [不易] SESSION 优先级最高（单会话死循环隔离），
           GLOBAL 优先级最低（全局过载保护）。
    """
    SESSION = "session"
    USER = "user"
    GLOBAL = "global"


@dataclass
class ThreeLevelBreakerConfig:
    """三级熔断器配置（组合 3 个独立 CircuitBreakerConfig）

    [变易] 每级独立阈值与冷却策略，按 scope 隔离。
    默认阈值：SESSION=5 / USER=20 / GLOBAL=100（连续失败数）。
    默认冷却：SESSION=60s / USER=300s / GLOBAL=600s。
    """
    session: CircuitBreakerConfig = field(default_factory=lambda: CircuitBreakerConfig(
        failure_threshold=1.0, min_requests=5, reset_timeout=60.0,
        window_seconds=60.0, max_attempts=1, name="session",
    ))
    user: CircuitBreakerConfig = field(default_factory=lambda: CircuitBreakerConfig(
        failure_threshold=1.0, min_requests=20, reset_timeout=300.0,
        window_seconds=60.0, max_attempts=2, name="user",
    ))
    global_: CircuitBreakerConfig = field(default_factory=lambda: CircuitBreakerConfig(
        failure_threshold=1.0, min_requests=100, reset_timeout=600.0,
        window_seconds=60.0, max_attempts=3, name="global",
    ))


class ThreeLevelCircuitBreaker:
    """三级熔断器（SESSION / USER / GLOBAL 级联）

    [不易] 组合优于继承：内部管理 3 个独立的 CircuitBreaker 注册表，
           现有 CircuitBreaker 公开 API 完全不变。
    [变易] 触发顺序：SESSION → USER(仅高危工具) → GLOBAL，
           任一触发即熔断，记录是哪级触发。
    [简易] 三级检查 = 3 次 dict 查询 + 3 次 CircuitBreaker.allow_request()，
           满足 < 0.1ms 性能要求（纯内存操作）。

    Args:
        config: ThreeLevelBreakerConfig 配置对象，None 时使用默认配置
        trace_recorder: 可选的 trace 记录器（需实现 record_circuit_event 方法）
    """

    def __init__(
        self,
        config: Optional[ThreeLevelBreakerConfig] = None,
        trace_recorder: Any = None,
    ):
        self._config = config or ThreeLevelBreakerConfig()
        self._trace_recorder = trace_recorder
        # 三级独立注册表（双检锁模式：读路径无锁，创建路径加锁）
        self._session_breakers: dict = {}  # (session_id, tool_name) -> CircuitBreaker
        self._user_breakers: dict = {}     # (user_id, tool_name) -> CircuitBreaker
        self._global_breakers: dict = {}   # tool_name -> CircuitBreaker
        self._lock = threading.Lock()

    # ── 内部：获取/创建各级 breaker（双检锁）────────────────

    def _get_session_breaker(self, session_id: str, tool_name: str) -> CircuitBreaker:
        """按 (session_id, tool_name) 获取 SESSION 级 breaker"""
        key = (session_id, tool_name)
        breaker = self._session_breakers.get(key)
        if breaker is None:
            with self._lock:
                breaker = self._session_breakers.get(key)
                if breaker is None:
                    breaker = CircuitBreaker(self._config.session)
                    self._session_breakers[key] = breaker
        return breaker

    def _get_user_breaker(self, user_id: str, tool_name: str) -> CircuitBreaker:
        """按 (user_id, tool_name) 获取 USER 级 breaker"""
        key = (user_id, tool_name)
        breaker = self._user_breakers.get(key)
        if breaker is None:
            with self._lock:
                breaker = self._user_breakers.get(key)
                if breaker is None:
                    breaker = CircuitBreaker(self._config.user)
                    self._user_breakers[key] = breaker
        return breaker

    def _get_global_breaker(self, tool_name: str) -> CircuitBreaker:
        """按 tool_name 获取 GLOBAL 级 breaker"""
        breaker = self._global_breakers.get(tool_name)
        if breaker is None:
            with self._lock:
                breaker = self._global_breakers.get(tool_name)
                if breaker is None:
                    breaker = CircuitBreaker(self._config.global_)
                    self._global_breakers[tool_name] = breaker
        return breaker

    # ── 公开 API ─────────────────────────────────────────────

    def allow_request(
        self,
        session_id: str,
        user_id: str,
        tool_name: str,
        is_high_risk: bool = False,
    ) -> tuple:
        """检查是否允许请求通过（三级级联短路）

        [不易] 触发顺序：SESSION → USER(仅高危) → GLOBAL，
               任一触发即返回 (False, scope)，短路不检查后续级别。

        Args:
            session_id: 会话 ID
            user_id: 用户 ID
            tool_name: 工具名称
            is_high_risk: 是否高危工具（高危才检查 USER 级）

        Returns:
            (allowed, scope): allowed=True 时 scope=None；
                              allowed=False 时 scope=触发的 CircuitScope
        """
        # 1. SESSION 级检查（最高优先级：单会话死循环隔离）
        session_breaker = self._get_session_breaker(session_id, tool_name)
        if not session_breaker.allow_request():
            logger.info(
                "[ThreeLevelCB] SESSION 级熔断阻断: session=%s user=%s tool=%s state=%s",
                session_id, user_id, tool_name, session_breaker.state.value,
            )
            self._emit_trace_event(
                CircuitScope.SESSION, session_id, user_id, tool_name, blocked=True,
            )
            return False, CircuitScope.SESSION

        # 2. USER 级检查（仅高危工具：单用户跨会话累积）
        if is_high_risk:
            user_breaker = self._get_user_breaker(user_id, tool_name)
            if not user_breaker.allow_request():
                logger.info(
                    "[ThreeLevelCB] USER 级熔断阻断: session=%s user=%s tool=%s state=%s",
                    session_id, user_id, tool_name, user_breaker.state.value,
                )
                self._emit_trace_event(
                    CircuitScope.USER, session_id, user_id, tool_name, blocked=True,
                )
                return False, CircuitScope.USER

        # 3. GLOBAL 级检查（最低优先级：全局过载保护）
        global_breaker = self._get_global_breaker(tool_name)
        if not global_breaker.allow_request():
            logger.info(
                "[ThreeLevelCB] GLOBAL 级熔断阻断: session=%s user=%s tool=%s state=%s",
                session_id, user_id, tool_name, global_breaker.state.value,
            )
            self._emit_trace_event(
                CircuitScope.GLOBAL, session_id, user_id, tool_name, blocked=True,
            )
            return False, CircuitScope.GLOBAL

        logger.debug(
            "[ThreeLevelCB] 请求放行: session=%s user=%s tool=%s high_risk=%s",
            session_id, user_id, tool_name, is_high_risk,
        )
        return True, None

    def record_result(
        self,
        session_id: str,
        user_id: str,
        tool_name: str,
        is_success: bool,
    ) -> None:
        """记录调用结果到三级熔断器（三级均记录，独立计数）

        [不易] 三级独立计数，互不污染。
        [变易] 检测状态转换并输出结构化日志，便于排查触发逻辑。

        Args:
            session_id: 会话 ID
            user_id: 用户 ID
            tool_name: 工具名称
            is_success: 调用是否成功
        """
        # 获取三级 breaker（不存在则创建）
        session_breaker = self._get_session_breaker(session_id, tool_name)
        user_breaker = self._get_user_breaker(user_id, tool_name)
        global_breaker = self._get_global_breaker(tool_name)

        # 记录前快照各 breaker 状态（用于检测状态转换）
        old_session_state = session_breaker.state
        old_user_state = user_breaker.state
        old_global_state = global_breaker.state

        # 记录到三级（各自独立维护滑动窗口与错误率）
        session_breaker.record_result(is_success)
        user_breaker.record_result(is_success)
        global_breaker.record_result(is_success)

        # 检测状态转换并输出日志（仅在转换时 logger.info，减少噪音）
        new_session_state = session_breaker.state
        if new_session_state != old_session_state:
            logger.info(
                "[ThreeLevelCB] SESSION 级状态转换: session=%s user=%s tool=%s "
                "%s → %s",
                session_id, user_id, tool_name,
                old_session_state.value, new_session_state.value,
            )

        new_user_state = user_breaker.state
        if new_user_state != old_user_state:
            logger.info(
                "[ThreeLevelCB] USER 级状态转换: session=%s user=%s tool=%s "
                "%s → %s",
                session_id, user_id, tool_name,
                old_user_state.value, new_user_state.value,
            )

        new_global_state = global_breaker.state
        if new_global_state != old_global_state:
            logger.info(
                "[ThreeLevelCB] GLOBAL 级状态转换: session=%s user=%s tool=%s "
                "%s → %s",
                session_id, user_id, tool_name,
                old_global_state.value, new_global_state.value,
            )

    def get_triggered_level(
        self,
        session_id: str,
        user_id: str,
        tool_name: str,
    ) -> Optional[CircuitScope]:
        """返回第一个 OPEN 的级别（无则 None）

        检查顺序：SESSION → USER → GLOBAL。
        """
        if self._get_session_breaker(session_id, tool_name).state == CircuitState.OPEN:
            return CircuitScope.SESSION
        if self._get_user_breaker(user_id, tool_name).state == CircuitState.OPEN:
            return CircuitScope.USER
        if self._get_global_breaker(tool_name).state == CircuitState.OPEN:
            return CircuitScope.GLOBAL
        return None

    def get_status(
        self,
        session_id: str,
        user_id: str,
        tool_name: str,
    ) -> dict:
        """返回三级状态快照

        Returns:
            {"session": breaker.get_status(), "user": ..., "global": ...}
        """
        return {
            "session": self._get_session_breaker(session_id, tool_name).get_status(),
            "user": self._get_user_breaker(user_id, tool_name).get_status(),
            "global": self._get_global_breaker(tool_name).get_status(),
        }

    def reset(self) -> None:
        """重置所有三级熔断器（清空注册表）"""
        with self._lock:
            for breaker in self._session_breakers.values():
                breaker.reset()
            for breaker in self._user_breakers.values():
                breaker.reset()
            for breaker in self._global_breakers.values():
                breaker.reset()
            self._session_breakers.clear()
            self._user_breakers.clear()
            self._global_breakers.clear()
        logger.info("[ThreeLevelCB] 已重置所有三级熔断器")

    def call_with_breaker(
        self,
        func: Callable,
        *args,
        session_id: str,
        user_id: str,
        tool_name: str,
        **kwargs,
    ) -> Any:
        """通过三级熔断器执行函数

        Args:
            func: 可调用对象
            *args, **kwargs: 传给 func 的参数
            session_id, user_id, tool_name: 三级熔断器键

        Returns:
            func 的返回值

        Raises:
            CircuitBreakerError: 熔断器打开时（含触发级别信息）
            Exception: func 抛出的原异常（同时记录失败）
        """
        allowed, scope = self.allow_request(
            session_id, user_id, tool_name,
            is_high_risk=kwargs.pop("is_high_risk", False),
        )
        if not allowed:
            raise CircuitBreakerError(
                f"三级熔断器 [{scope.value}] 级已打开，拒绝请求: "
                f"session={session_id} user={user_id} tool={tool_name}",
                state=CircuitState.OPEN,
                name=scope.value,
            )

        is_success = False
        try:
            result = func(*args, **kwargs)
            is_success = True
            return result
        finally:
            self.record_result(session_id, user_id, tool_name, is_success)

    # ── 内部：trace 事件 ─────────────────────────────────────

    def _emit_trace_event(
        self,
        scope: CircuitScope,
        session_id: str,
        user_id: str,
        tool_name: str,
        blocked: bool,
    ) -> None:
        """调用 trace_recorder 记录熔断事件（吞掉异常不影响主流程）

        [不易] 熔断事件写入 tool_trace（任务 2 的接口）。
        [简易] trace_recorder 为 None 时静默跳过，不影响主路径。
        """
        if self._trace_recorder is None:
            return
        try:
            self._trace_recorder.record_circuit_event(
                scope=scope,
                session_id=session_id,
                user_id=user_id,
                tool_name=tool_name,
                blocked=blocked,
            )
        except Exception as exc:
            logger.debug("[ThreeLevelCB] trace 事件记录失败: %s", exc)
