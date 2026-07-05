"""error_handler 全面单元测试

测试目标：覆盖 agent/error_handler.py 的所有公开 API
覆盖维度：
1. 枚举类：ErrorSeverity / ErrorCategory / CircuitState
2. 数据类：ErrorMetrics / YunshuError 及子类
3. CircuitBreaker：状态转换、execute、get_status
4. RetryPolicy：should_retry / calculate_delay（fixed/linear/exponential + jitter）
5. ErrorHandler：record_error / execute_with_retry / get_metrics
6. 装饰器：with_retry / with_circuit_breaker

状态同步说明：每个用例使用独立实例避免全局污染；全局 handler 测试通过 reset 或独立 key 隔离。
"""
import asyncio
import time
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest

from agent.error_handler import (
    CircuitBreaker,
    CircuitState,
    CriticalError,
    DataInvalidError,
    ErrorCategory,
    ErrorHandler,
    ErrorMetrics,
    ErrorSeverity,
    ExternalServiceError,
    NetworkTimeoutError,
    RecoverableError,
    RetryPolicy,
    SecurityError,
    TemporaryNetworkError,
    YunshuError,
    async_with_retry,
    get_error_handler,
    with_circuit_breaker,
    with_retry,
)


# ── 1. 枚举 ──────────────────────────────────────────────


class TestErrorSeverity:
    def test_all_levels(self):
        assert ErrorSeverity.DEBUG
        assert ErrorSeverity.INFO
        assert ErrorSeverity.WARNING
        assert ErrorSeverity.ERROR
        assert ErrorSeverity.CRITICAL

    def test_values(self):
        assert ErrorSeverity.DEBUG.value == "debug"
        assert ErrorSeverity.CRITICAL.value == "critical"


class TestErrorCategory:
    def test_network_categories(self):
        assert ErrorCategory.NETWORK_TEMPORARY.value == "network_temporary"
        assert ErrorCategory.NETWORK_TIMEOUT.value == "network_timeout"
        assert ErrorCategory.NETWORK_CONNECTION.value == "network_connection"

    def test_resource_categories(self):
        assert ErrorCategory.RESOURCE_MEMORY
        assert ErrorCategory.RESOURCE_DISK
        assert ErrorCategory.RESOURCE_CPU

    def test_data_categories(self):
        assert ErrorCategory.DATA_INVALID
        assert ErrorCategory.DATA_MISSING
        assert ErrorCategory.DATA_CORRUPT

    def test_unknown_default(self):
        assert ErrorCategory.UNKNOWN.value == "unknown"


class TestCircuitState:
    def test_three_states(self):
        assert CircuitState.CLOSED
        assert CircuitState.OPEN
        assert CircuitState.HALF_OPEN

    def test_values(self):
        assert CircuitState.CLOSED.value == "closed"
        assert CircuitState.OPEN.value == "open"
        assert CircuitState.HALF_OPEN.value == "half_open"


# ── 2. ErrorMetrics ──────────────────────────────────────


class TestErrorMetrics:
    def test_default_values(self):
        m = ErrorMetrics()
        assert m.total_count == 0
        assert m.retry_attempts == 0
        assert m.first_occurrence is None
        assert m.last_occurrence is None

    def test_post_init_populates_severity(self):
        m = ErrorMetrics()
        for sev in ErrorSeverity:
            assert sev in m.count_by_severity
            assert m.count_by_severity[sev] == 0

    def test_post_init_populates_category(self):
        m = ErrorMetrics()
        for cat in ErrorCategory:
            assert cat in m.count_by_category
            assert m.count_by_category[cat] == 0


# ── 3. YunshuError 及子类 ────────────────────────────────


class TestYunshuError:
    def test_basic_message(self):
        err = YunshuError("something failed")
        assert err.message == "something failed"
        assert err.severity == ErrorSeverity.ERROR
        assert err.category == ErrorCategory.UNKNOWN
        assert err.recoverable is False
        assert err.retryable is False

    def test_custom_severity(self):
        err = YunshuError("warn", severity=ErrorSeverity.WARNING)
        assert err.severity == ErrorSeverity.WARNING

    def test_custom_category(self):
        err = YunshuError("net", category=ErrorCategory.NETWORK_TIMEOUT)
        assert err.category == ErrorCategory.NETWORK_TIMEOUT

    def test_custom_flags(self):
        err = YunshuError(
            "x",
            recoverable=True,
            retryable=True,
            requires_restart=True,
            requires_user_notification=True,
        )
        assert err.recoverable is True
        assert err.retryable is True
        assert err.requires_restart is True
        assert err.requires_user_notification is True

    def test_context_default_empty(self):
        err = YunshuError("x")
        assert err.context == {}

    def test_context_provided(self):
        err = YunshuError("x", context={"user": "alice"})
        assert err.context["user"] == "alice"

    def test_with_original_chains(self):
        original = ValueError("original")
        err = YunshuError("wrapped").with_original(original)
        assert err._original_exception is original

    def test_to_dict(self):
        err = YunshuError("msg", severity=ErrorSeverity.WARNING)
        d = err.to_dict()
        assert d["type"] == "YunshuError"
        assert d["message"] == "msg"
        assert d["severity"] == "warning"
        assert d["category"] == "unknown"
        assert d["recoverable"] is False
        assert "timestamp" in d
        assert d["original_exception"] is None

    def test_to_dict_with_original(self):
        err = YunshuError("x").with_original(RuntimeError("orig"))
        d = err.to_dict()
        assert "orig" in d["original_exception"]

    def test_is_exception(self):
        assert isinstance(YunshuError("x"), Exception)


class TestErrorSubclasses:
    def test_recoverable_error(self):
        err = RecoverableError("r")
        assert err.recoverable is True
        assert err.retryable is True
        assert err.severity == ErrorSeverity.WARNING

    def test_critical_error(self):
        err = CriticalError("c")
        assert err.severity == ErrorSeverity.CRITICAL
        assert err.requires_restart is True

    def test_temporary_network_error(self):
        err = TemporaryNetworkError("net")
        assert err.category == ErrorCategory.NETWORK_TEMPORARY
        assert err.retryable is True
        assert err.default_retry_count == 5
        assert err.default_retry_delay == 0.5

    def test_network_timeout_error(self):
        err = NetworkTimeoutError("timeout")
        assert err.category == ErrorCategory.NETWORK_TIMEOUT
        assert err.default_retry_count == 3

    def test_external_service_error(self):
        err = ExternalServiceError("ext")
        assert err.category == ErrorCategory.EXTERNAL_SERVICE
        assert err.default_retry_delay == 2.0

    def test_data_invalid_error(self):
        err = DataInvalidError("bad")
        assert err.category == ErrorCategory.DATA_INVALID
        assert err.recoverable is True
        assert err.retryable is False

    def test_security_error(self):
        err = SecurityError("attack")
        assert err.severity == ErrorSeverity.CRITICAL
        assert err.category == ErrorCategory.SECURITY_ALERT
        assert err.requires_user_notification is True


# ── 4. CircuitBreaker ────────────────────────────────────


class TestCircuitBreaker:
    def test_init_defaults(self):
        cb = CircuitBreaker()
        assert cb.max_failures == 5
        assert cb.reset_timeout == 60.0
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0
        assert cb.success_count == 0

    def test_init_custom(self):
        cb = CircuitBreaker(max_failures=3, reset_timeout=10.0, name="custom")
        assert cb.max_failures == 3
        assert cb.reset_timeout == 10.0
        assert cb.name == "custom"

    def test_record_success_increments(self):
        cb = CircuitBreaker()
        cb.record_success()
        assert cb.success_count == 1
        assert cb.last_success_time is not None

    def test_record_success_resets_failure_count_when_closed(self):
        cb = CircuitBreaker()
        cb.failure_count = 2
        cb.record_success()
        assert cb.failure_count == 0

    def test_record_failure_increments(self):
        cb = CircuitBreaker()
        cb.record_failure()
        assert cb.failure_count == 1
        assert cb.last_failure_time is not None

    def test_record_failure_opens_circuit(self):
        cb = CircuitBreaker(max_failures=2)
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_record_success_recovers_from_half_open(self):
        cb = CircuitBreaker(max_failures=1)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        # 强制进入半开状态
        cb.state = CircuitState.HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0

    def test_record_failure_from_half_open_reopens(self):
        cb = CircuitBreaker(max_failures=1)
        cb.state = CircuitState.HALF_OPEN
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_can_reset_no_failure_time(self):
        cb = CircuitBreaker(reset_timeout=1.0)
        assert cb._can_reset() is False

    def test_can_reset_elapsed(self):
        cb = CircuitBreaker(reset_timeout=0.0)
        cb.last_failure_time = datetime.now() - timedelta(seconds=1)
        assert cb._can_reset() is True

    def test_can_reset_not_elapsed(self):
        cb = CircuitBreaker(reset_timeout=100.0)
        cb.last_failure_time = datetime.now()
        assert cb._can_reset() is False

    def test_can_half_open(self):
        cb = CircuitBreaker(reset_timeout=0.0)
        cb.state = CircuitState.OPEN
        cb.last_failure_time = datetime.now() - timedelta(seconds=1)
        assert cb._can_half_open() is True

    def test_can_half_open_not_open(self):
        cb = CircuitBreaker()
        assert cb._can_half_open() is False

    def test_execute_success(self):
        cb = CircuitBreaker()
        result = cb.execute(lambda x: x * 2, 5)
        assert result == 10
        assert cb.success_count == 1

    def test_execute_failure_records(self):
        cb = CircuitBreaker()

        def boom():
            raise ValueError("boom")

        with pytest.raises(ValueError):
            cb.execute(boom)
        assert cb.failure_count == 1

    def test_execute_open_raises_critical(self):
        cb = CircuitBreaker(max_failures=1, reset_timeout=100.0)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        with pytest.raises(CriticalError):
            cb.execute(lambda: 1)

    def test_execute_open_transitions_to_half_open(self):
        cb = CircuitBreaker(max_failures=1, reset_timeout=0.0)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        # 时间已过，应转入半开
        time.sleep(0.01)
        result = cb.execute(lambda: "ok")
        assert result == "ok"

    def test_is_open(self):
        cb = CircuitBreaker()
        assert cb.is_open() is False
        cb.state = CircuitState.OPEN
        assert cb.is_open() is True

    def test_get_status(self):
        cb = CircuitBreaker(name="test", max_failures=3)
        cb.record_success()
        status = cb.get_status()
        assert status["name"] == "test"
        assert status["state"] == "closed"
        assert status["success_count"] == 1
        assert status["max_failures"] == 3
        assert status["last_success_time"] is not None
        assert status["last_failure_time"] is None


# ── 5. RetryPolicy ───────────────────────────────────────


class TestRetryPolicy:
    def test_defaults(self):
        p = RetryPolicy()
        assert p.max_retries == 3
        assert p.initial_delay == 1.0
        assert p.max_delay == 30.0
        assert p.backoff_factor == 2.0
        assert p.strategy == "exponential"

    def test_should_retry_within_limit(self):
        p = RetryPolicy(max_retries=3)
        assert p.should_retry(ValueError("x"), 0) is True
        assert p.should_retry(ValueError("x"), 2) is True

    def test_should_retry_exceeds_limit(self):
        p = RetryPolicy(max_retries=2)
        assert p.should_retry(ValueError("x"), 2) is False

    def test_should_retry_exception_type_mismatch(self):
        p = RetryPolicy(retryable_exceptions=(ValueError,))
        assert p.should_retry(TypeError("x"), 0) is False

    def test_should_retry_exception_type_match(self):
        p = RetryPolicy(retryable_exceptions=(ValueError,))
        assert p.should_retry(ValueError("x"), 0) is True

    def test_should_retry_custom_condition_true(self):
        p = RetryPolicy(custom_retry_condition=lambda e: True)
        assert p.should_retry(ValueError("x"), 0) is True

    def test_should_retry_custom_condition_false(self):
        p = RetryPolicy(custom_retry_condition=lambda e: False)
        assert p.should_retry(ValueError("x"), 0) is False

    def test_calculate_delay_fixed(self):
        p = RetryPolicy(strategy="fixed", initial_delay=2.0, jitter_factor=0)
        assert p.calculate_delay(0) == 2.0
        assert p.calculate_delay(3) == 2.0

    def test_calculate_delay_linear(self):
        p = RetryPolicy(strategy="linear", initial_delay=1.0, jitter_factor=0)
        assert p.calculate_delay(0) == 1.0
        assert p.calculate_delay(1) == 2.0
        assert p.calculate_delay(2) == 3.0

    def test_calculate_delay_exponential(self):
        p = RetryPolicy(
            strategy="exponential",
            initial_delay=1.0,
            backoff_factor=2.0,
            max_delay=100.0,
            jitter_factor=0,
        )
        assert p.calculate_delay(0) == 1.0
        assert p.calculate_delay(1) == 2.0
        assert p.calculate_delay(2) == 4.0
        assert p.calculate_delay(3) == 8.0

    def test_calculate_delay_capped_by_max_delay(self):
        p = RetryPolicy(
            strategy="exponential",
            initial_delay=10.0,
            backoff_factor=10.0,
            max_delay=50.0,
            jitter_factor=0,
        )
        assert p.calculate_delay(0) == 10.0
        # 10 * 10 = 100 -> capped to 50
        assert p.calculate_delay(1) == 50.0

    def test_calculate_delay_with_jitter(self):
        p = RetryPolicy(
            strategy="fixed",
            initial_delay=10.0,
            jitter_factor=0.2,
        )
        for _ in range(20):
            d = p.calculate_delay(0)
            # 10 * (1 ± 0.2) = [8, 12]
            assert 8.0 <= d <= 12.0

    def test_calculate_delay_unknown_strategy_falls_back(self):
        p = RetryPolicy(strategy="unknown", initial_delay=5.0, jitter_factor=0)
        assert p.calculate_delay(0) == 5.0


# ── 6. ErrorHandler ──────────────────────────────────────


class TestErrorHandler:
    def test_init(self):
        handler = ErrorHandler()
        assert handler._metrics == {} or len(handler._metrics) == 0

    def test_record_error_yunshu_error(self):
        handler = ErrorHandler()
        err = YunshuError("test", severity=ErrorSeverity.WARNING)
        result = handler.record_error(err)
        assert result is err
        metrics = handler.get_metrics("YunshuError")
        assert metrics["total_count"] == 1
        assert metrics["count_by_severity"]["warning"] == 1

    def test_record_error_plain_exception(self):
        handler = ErrorHandler()
        result = handler.record_error(ValueError("plain"))
        assert isinstance(result, YunshuError)
        assert result._original_exception is not None
        metrics = handler.get_metrics("YunshuError")
        assert metrics["total_count"] == 1

    def test_record_error_with_custom_key(self):
        handler = ErrorHandler()
        handler.record_error(YunshuError("x"), key="custom_key")
        metrics = handler.get_metrics("custom_key")
        assert metrics["total_count"] == 1

    def test_record_error_updates_occurrences(self):
        handler = ErrorHandler()
        handler.record_error(YunshuError("x"), key="k")
        handler.record_error(YunshuError("y"), key="k")
        metrics = handler.get_metrics("k")
        assert metrics["total_count"] == 2
        assert metrics["first_occurrence"] is not None
        assert metrics["last_occurrence"] is not None

    def test_get_metrics_missing_key(self):
        handler = ErrorHandler()
        assert handler.get_metrics("nonexistent") == {}

    def test_get_metrics_all(self):
        handler = ErrorHandler()
        handler.record_error(YunshuError("x"), key="k1")
        handler.record_error(YunshuError("y"), key="k2")
        all_metrics = handler.get_metrics()
        assert "k1" in all_metrics
        assert "k2" in all_metrics

    def test_register_and_get_circuit_breaker(self):
        handler = ErrorHandler()
        cb = CircuitBreaker(name="test")
        handler.register_circuit_breaker("test", cb)
        assert handler.get_circuit_breaker("test") is cb

    def test_get_circuit_breaker_missing(self):
        handler = ErrorHandler()
        assert handler.get_circuit_breaker("missing") is None

    def test_get_circuit_breaker_status(self):
        handler = ErrorHandler()
        handler.register_circuit_breaker("cb1", CircuitBreaker(name="cb1"))
        status = handler.get_circuit_breaker_status()
        assert "cb1" in status
        assert status["cb1"]["name"] == "cb1"

    def test_execute_with_retry_success(self):
        handler = ErrorHandler()
        result = handler.execute_with_retry(lambda: "ok")
        assert result == "ok"

    def test_execute_with_retry_with_args(self):
        handler = ErrorHandler()
        result = handler.execute_with_retry(lambda x, y: x + y, func_args=(1, 2))
        assert result == 3

    def test_execute_with_retry_no_retry_on_critical(self):
        handler = ErrorHandler()

        def fail():
            raise CriticalError("critical")

        with pytest.raises(CriticalError):
            handler.execute_with_retry(fail)

    def test_execute_with_retry_retries_then_succeeds(self):
        handler = ErrorHandler()
        attempts = [0]

        def flaky():
            attempts[0] += 1
            if attempts[0] < 2:
                raise RecoverableError("temp")
            return "success"

        policy = RetryPolicy(max_retries=3, initial_delay=0.001, jitter_factor=0)
        result = handler.execute_with_retry(flaky, retry_policy=policy)
        assert result == "success"
        assert attempts[0] == 2

    def test_execute_with_retry_exhausted(self):
        handler = ErrorHandler()

        def always_fail():
            raise RecoverableError("always")

        policy = RetryPolicy(max_retries=2, initial_delay=0.001, jitter_factor=0)
        with pytest.raises(YunshuError):
            handler.execute_with_retry(always_fail, retry_policy=policy)

    def test_execute_with_retry_on_retry_callback(self):
        handler = ErrorHandler()
        attempts = [0]
        callback_calls = []

        def flaky():
            attempts[0] += 1
            if attempts[0] < 3:
                raise RecoverableError("temp")
            return "ok"

        def on_retry(attempt, exc):
            callback_calls.append((attempt, str(exc)))

        policy = RetryPolicy(max_retries=5, initial_delay=0.001, jitter_factor=0)
        handler.execute_with_retry(flaky, retry_policy=policy, on_retry=on_retry)
        assert len(callback_calls) == 2
        assert callback_calls[0][0] == 1

    def test_execute_with_retry_circuit_breaker(self):
        handler = ErrorHandler()
        cb = CircuitBreaker(max_failures=10)
        result = handler.execute_with_retry(lambda: "ok", circuit_breaker=cb)
        assert result == "ok"
        assert cb.success_count == 1

    def test_execute_with_retry_non_retryable_raises_immediately(self):
        """当 retryable_exceptions 明确排除某异常时，应立即抛出不重试"""
        handler = ErrorHandler()
        attempts = [0]

        def fail():
            attempts[0] += 1
            raise TypeError("type error")

        with pytest.raises(YunshuError):
            handler.execute_with_retry(
                fail,
                retry_policy=RetryPolicy(max_retries=3, initial_delay=0.001),
                retryable_exceptions=(ValueError,),  # 只允许 ValueError 重试
            )
        # TypeError 不在 retryable_exceptions 中，立即抛出
        assert attempts[0] == 1

    def test_execute_with_retry_yunshu_error_always_retried(self):
        """YunshuError 子类即使 retryable=False 也会被默认 retryable 元组匹配而重试

        这是实现的既有行为：默认 retryable=(RecoverableError, YunshuError)，
        所有 YunshuError 子类都会匹配第二条 elif 分支而被重试。
        """
        handler = ErrorHandler()
        attempts = [0]

        def fail():
            attempts[0] += 1
            raise DataInvalidError("bad data")  # retryable=False

        with pytest.raises(YunshuError):
            handler.execute_with_retry(
                fail,
                retry_policy=RetryPolicy(max_retries=3, initial_delay=0.001, jitter_factor=0),
            )
        # max_retries=3 -> 4 次尝试（0,1,2,3）
        assert attempts[0] == 4

    def test_get_metrics_serializes_datetimes(self):
        handler = ErrorHandler()
        handler.record_error(YunshuError("x"), key="k")
        m = handler.get_metrics("k")
        # first_occurrence 应为 ISO 字符串
        assert isinstance(m["first_occurrence"], str)


# ── 7. 全局 handler ──────────────────────────────────────


class TestGlobalHandler:
    def test_get_error_handler_singleton(self):
        h1 = get_error_handler()
        h2 = get_error_handler()
        assert h1 is h2

    def test_global_handler_can_record(self):
        handler = get_error_handler()
        handler.record_error(YunshuError("global test"), key="global_test_key")
        assert handler.get_metrics("global_test_key")["total_count"] >= 1


# ── 8. with_retry 装饰器 ─────────────────────────────────


class TestWithRetryDecorator:
    def test_success_no_retry(self):
        @with_retry(max_retries=3, initial_delay=0.001)
        def func():
            return "ok"

        assert func() == "ok"

    def test_retry_then_success(self):
        attempts = [0]

        @with_retry(max_retries=3, initial_delay=0.001, jitter_factor=0)
        def func():
            attempts[0] += 1
            if attempts[0] < 2:
                raise RecoverableError("temp")
            return "success"

        assert func() == "success"
        assert attempts[0] == 2

    def test_retry_exhausted_raises(self):
        @with_retry(max_retries=1, initial_delay=0.001, jitter_factor=0)
        def func():
            raise RecoverableError("always")

        with pytest.raises(YunshuError):
            func()

    def test_preserves_function_name(self):
        @with_retry(max_retries=1, initial_delay=0.001)
        def my_function():
            return "ok"

        assert my_function.__name__ == "my_function"

    def test_with_circuit_breaker_decorator(self):
        cb = CircuitBreaker(max_failures=10)

        @with_circuit_breaker(cb)
        def func(x):
            return x * 2

        assert func(5) == 10
        assert cb.success_count == 1


# ── 9. async_with_retry 装饰器 ───────────────────────────


class TestAsyncWithRetry:
    def test_async_success(self):
        @async_with_retry(max_retries=2, initial_delay=0.001)
        async def func():
            return "async_ok"

        result = asyncio.run(func())
        assert result == "async_ok"

    def test_async_retry_then_success(self):
        attempts = [0]

        @async_with_retry(max_retries=3, initial_delay=0.001, jitter_factor=0)
        async def func():
            attempts[0] += 1
            if attempts[0] < 2:
                raise RecoverableError("temp")
            return "recovered"

        result = asyncio.run(func())
        assert result == "recovered"
        assert attempts[0] == 2

    def test_async_exhausted_raises(self):
        @async_with_retry(max_retries=1, initial_delay=0.001, jitter_factor=0)
        async def func():
            raise RecoverableError("always")

        with pytest.raises(YunshuError):
            asyncio.run(func())


# ── 10. 集成场景 ─────────────────────────────────────────


class TestIntegration:
    def test_circuit_breaker_with_retry_decorator(self):
        """熔断器+重试装饰器组合"""
        cb = CircuitBreaker(max_failures=5)

        @with_retry(max_retries=2, initial_delay=0.001, jitter_factor=0, circuit_breaker=cb)
        def func():
            return "ok"

        assert func() == "ok"
        assert cb.success_count == 1
        assert cb.state == CircuitState.CLOSED

    def test_full_error_flow(self):
        """完整错误流程：抛出、记录、统计"""
        handler = ErrorHandler()
        cb = CircuitBreaker(max_failures=3, name="flow_test")
        handler.register_circuit_breaker("flow_test", cb)

        attempts = [0]

        def flaky():
            attempts[0] += 1
            if attempts[0] < 2:
                raise TemporaryNetworkError("network down")
            return "recovered"

        policy = RetryPolicy(max_retries=3, initial_delay=0.001, jitter_factor=0)
        result = handler.execute_with_retry(
            flaky, retry_policy=policy, circuit_breaker=cb
        )
        assert result == "recovered"
        assert cb.success_count == 1

    def test_security_error_is_retried_as_yunshu_error(self):
        """SecurityError 是 YunshuError 子类，默认会被重试（既有行为）

        若需阻止重试，需显式设置 retryable_exceptions 不包含 YunshuError。
        """
        handler = ErrorHandler()
        attempts = [0]

        def fail():
            attempts[0] += 1
            raise SecurityError("attack detected")

        with pytest.raises(YunshuError):
            handler.execute_with_retry(
                fail,
                retry_policy=RetryPolicy(max_retries=3, initial_delay=0.001, jitter_factor=0),
            )
        # max_retries=3 -> 4 次尝试
        assert attempts[0] == 4

    def test_security_error_not_retried_with_explicit_exceptions(self):
        """显式 retryable_exceptions 排除 YunshuError 时，SecurityError 不重试"""
        handler = ErrorHandler()
        attempts = [0]

        def fail():
            attempts[0] += 1
            raise SecurityError("attack detected")

        with pytest.raises(YunshuError):
            handler.execute_with_retry(
                fail,
                retry_policy=RetryPolicy(max_retries=3, initial_delay=0.001),
                retryable_exceptions=(RecoverableError,),  # 只允许 RecoverableError
            )
        # SecurityError 不是 RecoverableError 子类，不重试
        assert attempts[0] == 1
