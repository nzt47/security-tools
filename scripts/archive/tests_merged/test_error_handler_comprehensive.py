"""
ErrorHandler 综合测试 - 覆盖剩余未覆盖的代码
目标：将覆盖率从 30% 提升至 90%+
"""
import pytest
import asyncio
import time
from unittest.mock import MagicMock, patch, call
from datetime import datetime, timedelta
from agent.error_handler import (
    ErrorSeverity,
    ErrorCategory,
    CircuitState,
    YunshuError,
    RecoverableError,
    CriticalError,
    TemporaryNetworkError,
    NetworkTimeoutError,
    ExternalServiceError,
    DataInvalidError,
    SecurityError,
    ErrorMetrics,
    CircuitBreaker,
    RetryPolicy,
    ErrorHandler,
    get_error_handler,
    with_retry,
    async_with_retry,
    with_circuit_breaker,
)


class TestErrorHandlerRemainingCoverage:
    """测试 ErrorHandler 剩余未覆盖的代码"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_execute_with_retry_with_args_kwargs(self):
        """测试 execute_with_retry 使用 func_args 和 func_kwargs 参数"""
        handler = ErrorHandler()
        
        def test_func(a, b, c=3):
            return a + b + c
        
        result = handler.execute_with_retry(
            test_func,
            func_args=(1, 2),
            func_kwargs={'c': 4}
        )
        assert result == 7

    @pytest.mark.unit
    @pytest.mark.p0
    def test_execute_with_retry_on_retry_callback(self):
        """测试 execute_with_retry 的 on_retry 回调"""
        handler = ErrorHandler()
        retry_count = [0]
        
        def on_retry_callback(attempt, exc):
            retry_count[0] += 1
        
        def failing_func():
            raise RecoverableError("总是失败")
        
        with pytest.raises(YunshuError):
            handler.execute_with_retry(
                failing_func,
                retry_policy=RetryPolicy(max_retries=2, initial_delay=0.01),
                on_retry=on_retry_callback
            )
        
        assert retry_count[0] == 2

    @pytest.mark.unit
    @pytest.mark.p0
    def test_execute_with_retry_error_counter(self):
        """测试 execute_with_retry 的 error_counter 参数"""
        handler = ErrorHandler()
        
        def success_func():
            return "success"
        
        result = handler.execute_with_retry(
            success_func,
            error_counter="test_counter"
        )
        assert result == "success"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_get_metrics_all(self):
        """测试 get_metrics 不带参数时返回所有指标"""
        handler = ErrorHandler()
        handler.record_error(YunshuError("测试错误1"))
        handler.record_error(YunshuError("测试错误2"))
        
        all_metrics = handler.get_metrics()
        assert isinstance(all_metrics, dict)
        assert "YunshuError" in all_metrics


class TestRetryPolicyAdditional:
    """测试 RetryPolicy 剩余未覆盖的代码"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_retry_policy_linear_strategy(self):
        """测试线性重试策略"""
        policy = RetryPolicy(strategy="linear", initial_delay=1.0)
        delay = policy.calculate_delay(2)
        assert delay == 3.0  # 1.0 * (2 + 1)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_retry_policy_fixed_strategy(self):
        """测试固定重试策略"""
        policy = RetryPolicy(strategy="fixed", initial_delay=2.0)
        delay = policy.calculate_delay(5)
        assert delay == 2.0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_retry_policy_invalid_strategy(self):
        """测试无效策略使用默认值"""
        policy = RetryPolicy(strategy="invalid", initial_delay=1.5)
        delay = policy.calculate_delay(2)
        assert delay == 1.5  # 默认使用固定延迟

    @pytest.mark.unit
    @pytest.mark.p1
    def test_retry_policy_jitter_disabled(self):
        """测试禁用抖动"""
        policy = RetryPolicy(jitter_factor=0.0)
        delay = policy.calculate_delay(0)
        assert delay == policy.initial_delay

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_retry_with_custom_condition(self):
        """测试自定义重试条件"""
        def custom_condition(exc):
            return "retry" in str(exc)
        
        policy = RetryPolicy(
            max_retries=3,
            custom_retry_condition=custom_condition
        )
        
        assert policy.should_retry(ValueError("should retry"), 0) is True
        assert policy.should_retry(ValueError("no retry"), 0) is False


class TestCircuitBreakerAdditional:
    """测试 CircuitBreaker 剩余未覆盖的代码"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_half_open_timeout(self):
        """测试半开状态超时"""
        cb = CircuitBreaker(max_failures=1, reset_timeout=0.1)
        cb.record_failure()  # 打开断路器
        
        time.sleep(0.15)  # 超过重置超时
        
        with patch.object(cb, '_can_reset', return_value=True):
            cb._can_half_open()
        
        assert cb.state == CircuitState.OPEN  # 状态不会自动改变，需要执行时检查

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_execute_raises_critical_error(self):
        """测试断路器打开时执行抛出 CriticalError"""
        cb = CircuitBreaker(max_failures=1)
        cb.record_failure()  # 打开断路器
        
        def test_func():
            return "test"
        
        with pytest.raises(CriticalError):
            cb.execute(test_func)

    @pytest.mark.unit
    @pytest.mark.p1
    def test_circuit_breaker_success_after_reset(self):
        """测试断路器重置后的成功执行"""
        cb = CircuitBreaker(max_failures=1, reset_timeout=0.1)
        cb.record_failure()  # 打开断路器
        
        time.sleep(0.15)  # 等待超时
        
        def success_func():
            return "success"
        
        result = cb.execute(success_func)
        assert result == "success"
        assert cb.state == CircuitState.CLOSED


class TestDecorators:
    """测试装饰器功能"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_with_retry_decorator(self):
        """测试 with_retry 装饰器"""
        call_count = [0]
        
        @with_retry(max_retries=2, initial_delay=0.01)
        def flaky_func():
            call_count[0] += 1
            if call_count[0] <= 2:
                raise RecoverableError("暂时失败")
            return "success"
        
        result = flaky_func()
        assert result == "success"
        assert call_count[0] == 3

    @pytest.mark.unit
    @pytest.mark.p0
    def test_with_retry_decorator_non_retryable(self):
        """测试 with_retry 装饰器处理不可重试异常"""
        @with_retry(max_retries=2)
        def always_fail():
            raise ValueError("不可重试")
        
        with pytest.raises(YunshuError):
            always_fail()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_with_circuit_breaker_decorator(self):
        """测试 with_circuit_breaker 装饰器"""
        cb = CircuitBreaker(max_failures=2)
        
        @with_circuit_breaker(cb)
        def test_func():
            return "success"
        
        result = test_func()
        assert result == "success"

    @pytest.mark.unit
    @pytest.mark.p1
    @pytest.mark.asyncio
    async def test_async_with_retry_decorator(self):
        """测试 async_with_retry 装饰器"""
        call_count = [0]
        
        @async_with_retry(max_retries=2, initial_delay=0.01)
        async def async_flaky_func():
            call_count[0] += 1
            if call_count[0] <= 2:
                raise RecoverableError("暂时失败")
            return "success"
        
        result = await async_flaky_func()
        assert result == "success"
        assert call_count[0] == 3

    @pytest.mark.unit
    @pytest.mark.p1
    @pytest.mark.asyncio
    async def test_async_with_retry_non_retryable(self):
        """测试 async_with_retry 装饰器处理不可重试异常"""
        @async_with_retry(max_retries=2)
        async def async_fail():
            raise ValueError("不可重试")
        
        with pytest.raises(YunshuError):
            await async_fail()


class TestErrorCategories:
    """测试所有错误分类"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_all_error_categories(self):
        """测试所有错误分类值"""
        categories = [
            ErrorCategory.NETWORK_TEMPORARY,
            ErrorCategory.NETWORK_TIMEOUT,
            ErrorCategory.NETWORK_CONNECTION,
            ErrorCategory.RESOURCE_MEMORY,
            ErrorCategory.RESOURCE_DISK,
            ErrorCategory.RESOURCE_CPU,
            ErrorCategory.EXTERNAL_SERVICE,
            ErrorCategory.EXTERNAL_API,
            ErrorCategory.DATA_INVALID,
            ErrorCategory.DATA_MISSING,
            ErrorCategory.DATA_CORRUPT,
            ErrorCategory.PERMISSION_DENIED,
            ErrorCategory.SECURITY_ALERT,
            ErrorCategory.CONFIG_ERROR,
            ErrorCategory.UNKNOWN,
        ]
        
        for cat in categories:
            assert isinstance(cat.value, str)


class TestGlobalErrorHandler:
    """测试全局错误处理器"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_global_error_handler_singleton(self):
        """测试全局错误处理器是单例"""
        handler1 = get_error_handler()
        handler2 = get_error_handler()
        assert handler1 is handler2

    @pytest.mark.unit
    @pytest.mark.p0
    def test_global_error_handler_functionality(self):
        """测试全局错误处理器功能"""
        handler = get_error_handler()
        error = handler.record_error(ValueError("测试"))
        assert isinstance(error, YunshuError)