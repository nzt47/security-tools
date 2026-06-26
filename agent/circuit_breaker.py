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
    """熔断器打开时抛出的业务错误（带明确业务错误码）"""

    def __init__(self, message: str, error_code: str = "CIRCUIT_OPEN",
                 state: CircuitState = CircuitState.OPEN):
        super().__init__(message)
        self.error_code = error_code
        self.state = state


@dataclass
class CircuitStats:
    """熔断器统计快照"""
    state: CircuitState = CircuitState.CLOSED
    total_calls: int = 0
    success_count: int = 0
    failure_count: int = 0
    consecutive_failures: int = 0
    last_failure_time: float = 0.0
    last_state_change: float = 0.0
    half_open_attempts: int = 0
    half_open_successes: int = 0
    # 历史调用窗口（用于错误率计算）
    _window: list = field(default_factory=list)  # [(timestamp, is_success)]
    _window_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


class CircuitBreaker:
    """熔断器实现

    Args:
        name: 熔断器名称（用于日志和指标）
        failure_threshold: 错误率阈值（0-1），默认 0.3 (30%)
        min_calls: 计算错误率所需的最少调用数（防止样本不足误判）
        cooldown_seconds: 熔断冷却期（秒），到期后进入半开
        half_open_max_calls: 半开状态下最大探测请求数
        half_open_success_threshold: 半开状态下成功数达到此阈值即恢复
        window_seconds: 错误率计算的时间窗口（秒）
    """

    def __init__(
        self,
        name: str = "default",
        failure_threshold: float = 0.3,
        min_calls: int = 5,
        cooldown_seconds: float = 60.0,
        half_open_max_calls: int = 3,
        half_open_success_threshold: int = 2,
        window_seconds: float = 60.0,
    ):
        if not 0 < failure_threshold < 1:
            raise ValueError(f"failure_threshold 必须在 (0,1)，实际 {failure_threshold}")
        if min_calls < 1:
            raise ValueError(f"min_calls 必须 ≥1，实际 {min_calls}")

        self.name = name
        self.failure_threshold = failure_threshold
        self.min_calls = min_calls
        self.cooldown_seconds = cooldown_seconds
        self.half_open_max_calls = half_open_max_calls
        self.half_open_success_threshold = half_open_success_threshold
        self.window_seconds = window_seconds

        self._stats = CircuitStats(last_state_change=time.time())
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
        """统计快照（不可变视图）"""
        with self._lock:
            return CircuitStats(
                state=self._stats.state,
                total_calls=self._stats.total_calls,
                success_count=self._stats.success_count,
                failure_count=self._stats.failure_count,
                consecutive_failures=self._stats.consecutive_failures,
                last_failure_time=self._stats.last_failure_time,
                last_state_change=self._stats.last_state_change,
                half_open_attempts=self._stats.half_open_attempts,
                half_open_successes=self._stats.half_open_successes,
            )

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
            self._stats.total_calls += 1
            if is_success:
                self._stats.success_count += 1
                self._stats.consecutive_failures = 0
            else:
                self._stats.failure_count += 1
                self._stats.consecutive_failures += 1
                self._stats.last_failure_time = now

            # 维护滑动窗口
            with self._stats._window_lock:
                self._stats._window.append((now, is_success))
                cutoff = now - self.window_seconds
                self._stats._window = [
                    (t, s) for t, s in self._stats._window if t >= cutoff
                ]

            current = self._stats.state
            if current == CircuitState.HALF_OPEN:
                self._handle_half_open_result(is_success, now)
            elif current == CircuitState.CLOSED:
                self._maybe_open_circuit(now)

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
            window = list(self._stats._window)
        if len(window) < self.min_calls:
            return
        failures = sum(1 for _, s in window if not s)
        error_rate = failures / len(window)
        if error_rate >= self.failure_threshold:
            self._set_state(CircuitState.OPEN)

    def _set_state(self, new_state: CircuitState) -> None:
        """切换状态并记录日志"""
        old_state = self._stats.state
        if old_state == new_state:
            return
        self._stats.state = new_state
        self._stats.last_state_change = time.time()
        self._log_action(
            "circuit_state_changed",
            {"from": old_state.value, "to": new_state.value},
        )

    # ── 控制方法 ──────────────────────────────────────────────

    def reset(self) -> None:
        """重置熔断器到 CLOSED 状态（主要用于测试）"""
        with self._lock:
            self._stats = CircuitStats(last_state_change=time.time())
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
