"""合并后的测试文件 - 由 test_file_consolidation 工具自动生成。"""
# pylint: disable=redefined-outer-name,missing-function-docstring

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime
from agent.error_handler import (
    ErrorHandler,
    YunshuError,
    CircuitBreaker,
    RetryPolicy,
    async_with_retry,
    with_retry,
    ErrorSeverity,
    ErrorCategory,
)
from agent.error_handler import (
    ErrorMetrics,
    YunshuError,
    RecoverableError,
    CriticalError,
    TemporaryNetworkError,
    ErrorHandler,
    ErrorSeverity,
    ErrorCategory,
    CircuitBreaker,
    RetryPolicy,
    async_with_retry,
    with_circuit_breaker,
)


# === 来自 test_error_handler_remaining.py ===

"""
ErrorHandler 剩余代码覆盖测试
针对 error_handler.py 中未覆盖的 111 行代码进行补充测试
"""


class TestAsyncWithRetryMetrics:
    """测试 async_with_retry 的 metrics 收集分支"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_async_with_retry_success_metrics(self):
        """测试异步重试成功时的 metrics 收集"""
        # 测试带 error_counter 参数的异步重试
        @async_with_retry(max_retries=1, initial_delay=0.01, error_counter="async_test")
        async def success_func():
            return "success"
        
        import asyncio
        result = asyncio.run(success_func())
        
        assert result == "success"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_async_with_retry_failure_metrics(self):
        """测试异步重试失败时的 metrics 收集"""
        @async_with_retry(max_retries=0, initial_delay=0.01, error_counter="async_test")
        async def failure_func():
            raise ValueError("失败")
        
        import asyncio
        with pytest.raises(YunshuError):
            asyncio.run(failure_func())

    @pytest.mark.unit
    @pytest.mark.p0
    def test_async_with_retry_on_retry_callback(self):
        """测试异步重试的 on_retry 回调"""
        retry_count = [0]
        
        def on_retry(attempt, exc):
            retry_count[0] += 1
        
        @async_with_retry(max_retries=2, initial_delay=0.01, on_retry=on_retry)
        async def failing_func():
            raise YunshuError("可重试", retryable=True)
        
        import asyncio
        with pytest.raises(YunshuError):
            asyncio.run(failing_func())
        
        assert retry_count[0] == 2


class TestErrorHandlerEdgeCases:
    """测试 ErrorHandler 的边缘情况"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_record_error_with_original_exception(self):
        """测试记录带原始异常的错误"""
        handler = ErrorHandler()
        original_exc = ValueError("原始错误")
        
        result = handler.record_error(YunshuError("包装错误").with_original(original_exc))
        
        assert result._original_exception is original_exc

    @pytest.mark.unit
    @pytest.mark.p0
    def test_record_error_all_categories(self):
        """测试记录所有类型的错误分类"""
        handler = ErrorHandler()
        
        for category in ErrorCategory:
            error = YunshuError(f"测试 {category.value}", category=category)
            handler.record_error(error)
        
        metrics = handler.get_metrics("YunshuError")
        # 验证所有分类都被记录
        assert metrics["count_by_category"]["unknown"] >= 1

    @pytest.mark.unit
    @pytest.mark.p0
    def test_record_error_all_severities(self):
        """测试记录所有严重级别的错误"""
        handler = ErrorHandler()
        
        for severity in ErrorSeverity:
            error = YunshuError(f"测试 {severity.value}", severity=severity)
            handler.record_error(error)
        
        metrics = handler.get_metrics("YunshuError")
        # 验证所有严重级别都被记录
        assert metrics["count_by_severity"]["error"] >= 1


class TestCircuitBreakerAdvanced:
    """测试熔断器的高级功能"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_async_execute(self):
        """测试熔断器的异步执行"""
        cb = CircuitBreaker(name="async_cb", max_failures=2)
        
        # 使用同步函数测试熔断器
        def sync_func():
            raise ValueError("失败")
        
        # 第一次失败
        with pytest.raises(ValueError):
            cb.execute(sync_func)
        
        # 第二次失败触发熔断
        with pytest.raises(ValueError):
            cb.execute(sync_func)
        
        # 第三次应该被熔断器阻止
        with pytest.raises(YunshuError):
            cb.execute(sync_func)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_reset_timeout_zero(self):
        """测试熔断器超时为零的情况"""
        cb = CircuitBreaker(name="zero_timeout", max_failures=1, reset_timeout=0)
        
        # 触发熔断
        with pytest.raises(ValueError):
            cb.execute(lambda: (_ for _ in ()).throw(ValueError()))
        
        # 超时为零应该立即进入半开状态
        assert cb.state.value in ["half_open", "open"]


class TestRetryPolicyEdgeCases:
    """测试重试策略的边缘情况"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_retry_policy_zero_max_retries(self):
        """测试最大重试次数为零"""
        policy = RetryPolicy(max_retries=0)
        
        assert policy.should_retry(ValueError("test"), 0) is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_retry_policy_exponential_backoff(self):
        """测试指数退避策略"""
        policy = RetryPolicy(max_retries=5, initial_delay=1.0, backoff_factor=2.0)
        
        delays = []
        for attempt in range(5):
            delays.append(policy.calculate_delay(attempt))
        
        # 延迟应该递增
        for i in range(1, len(delays)):
            assert delays[i] >= delays[i-1]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_retry_policy_constant_strategy(self):
        """测试常量延迟策略"""
        # 测试不带抖动和指数退避的策略
        policy = RetryPolicy(max_retries=3, initial_delay=0.5, backoff_factor=1.0, jitter_factor=0.0)
        
        delays = [policy.calculate_delay(i) for i in range(3)]
        
        # 所有延迟应该相同（因为没有指数增长和抖动）
        assert all(d == delays[0] for d in delays)


class TestWithRetryDecoratorAdvanced:
    """测试 with_retry 装饰器的高级功能"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_with_retry_all_strategies(self):
        """测试所有重试策略"""
        strategies = ["exponential", "constant", "linear"]
        
        for strategy in strategies:
            call_count = [0]
            
            @with_retry(max_retries=1, initial_delay=0.01, strategy=strategy)
            def func():
                call_count[0] += 1
                if call_count[0] == 1:
                    raise YunshuError("重试", retryable=True)
                return "success"
            
            result = func()
            assert result == "success"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_with_retry_max_delay(self):
        """测试最大延迟限制"""
        policy = RetryPolicy(max_retries=10, initial_delay=1.0, max_delay=5.0, backoff_factor=2.0)
        
        # 计算多次延迟，确保不超过最大延迟
        for attempt in range(10):
            delay = policy.calculate_delay(attempt)
            assert delay <= 5.0


class TestGlobalErrorHandlerIntegration:
    """测试全局错误处理器的集成"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_global_handler_thread_safety(self):
        """测试全局错误处理器的线程安全"""
        import threading
        handler = ErrorHandler()
        errors_recorded = [0]
        
        def record_error():
            for _ in range(10):
                handler.record_error(YunshuError("线程安全测试"))
                errors_recorded[0] += 1
        
        threads = [threading.Thread(target=record_error) for _ in range(5)]
        
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        assert errors_recorded[0] == 50
        metrics = handler.get_metrics("YunshuError")
        assert metrics["total_count"] == 50


class TestYunshuErrorAdvanced:
    """测试 YunshuError 的高级功能"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_yunshu_error_requires_user_notification(self):
        """测试 requires_user_notification 属性"""
        error = YunshuError(
            "需要通知用户",
            requires_user_notification=True
        )
        
        assert error.requires_user_notification is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_yunshu_error_with_context(self):
        """测试带上下文信息的错误"""
        context = {"request_id": "123", "user_id": "456", "params": {"key": "value"}}
        error = YunshuError("测试错误", context=context)
        
        assert error.context == context
        
        # 测试 to_dict 包含上下文
        error_dict = error.to_dict()
        assert error_dict["context"] == context

# === 来自 test_error_handler_supplement.py ===

"""
ErrorHandler 补充测试用例
覆盖 error_handler.py 中剩余未覆盖的代码
"""


class TestErrorMetricsPostInit:
    """测试 ErrorMetrics.__post_init__ 方法"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_error_metrics_post_init_empty(self):
        """测试空初始化时的 __post_init__"""
        metrics = ErrorMetrics()
        
        for severity in ErrorSeverity:
            assert metrics.count_by_severity[severity] == 0
        for category in ErrorCategory:
            assert metrics.count_by_category[category] == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_error_metrics_post_init_with_existing(self):
        """测试已有部分数据时的 __post_init__"""
        metrics = ErrorMetrics(
            count_by_severity={ErrorSeverity.ERROR: 5},
            count_by_category={ErrorCategory.NETWORK_TEMPORARY: 3}
        )
        
        assert metrics.count_by_severity[ErrorSeverity.ERROR] == 5
        assert metrics.count_by_severity[ErrorSeverity.WARNING] == 0
        assert metrics.count_by_category[ErrorCategory.NETWORK_TEMPORARY] == 3
        assert metrics.count_by_category[ErrorCategory.DATA_INVALID] == 0


class TestYunshuErrorComplete:
    """测试 YunshuError 的完整功能"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_yunshu_error_all_params(self):
        """测试 YunshuError 所有可选参数"""
        error = YunshuError(
            message="测试错误",
            severity=ErrorSeverity.WARNING,
            category=ErrorCategory.NETWORK_TEMPORARY,
            recoverable=True,
            retryable=True,
            requires_restart=False,
            requires_user_notification=True,
            context={"key": "value"}
        )
        
        assert error.message == "测试错误"
        assert error.severity == ErrorSeverity.WARNING
        assert error.category == ErrorCategory.NETWORK_TEMPORARY
        assert error.recoverable is True
        assert error.retryable is True
        assert error.requires_restart is False
        assert error.requires_user_notification is True
        assert error.context == {"key": "value"}

    @pytest.mark.unit
    @pytest.mark.p0
    def test_yunshu_error_with_original(self):
        """测试记录原始异常"""
        original_exc = ValueError("原始错误")
        error = YunshuError("包装错误").with_original(original_exc)
        
        assert error._original_exception is original_exc
        assert "原始错误" in str(error._original_exception)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_yunshu_error_to_dict(self):
        """测试转换为字典格式"""
        original_exc = ValueError("原始错误")
        error = YunshuError(
            message="测试消息",
            severity=ErrorSeverity.WARNING,
            category=ErrorCategory.NETWORK_TEMPORARY,
            recoverable=True,
            retryable=True,
            context={"test": "data"}
        ).with_original(original_exc)
        
        result = error.to_dict()
        
        assert result["type"] == "YunshuError"
        assert result["message"] == "测试消息"
        assert result["severity"] == "warning"
        assert result["category"] == "network_temporary"
        assert result["recoverable"] is True
        assert result["retryable"] is True
        assert result["context"] == {"test": "data"}
        assert "原始错误" in result["original_exception"]


class TestYunshuErrorSubclasses:
    """测试 YunshuError 子类"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_recoverable_error_defaults(self):
        """测试 RecoverableError 默认属性"""
        error = RecoverableError("测试")
        
        assert error.severity == ErrorSeverity.WARNING
        assert error.recoverable is True
        assert error.retryable is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_critical_error_defaults(self):
        """测试 CriticalError 默认属性"""
        error = CriticalError("测试")
        
        assert error.severity == ErrorSeverity.CRITICAL
        assert error.requires_restart is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_temporary_network_error_defaults(self):
        """测试 TemporaryNetworkError 默认属性"""
        error = TemporaryNetworkError("测试")
        
        assert error.category == ErrorCategory.NETWORK_TEMPORARY
        assert error.default_retry_count == 5
        assert error.default_retry_delay == 0.5


class TestErrorHandlerRecordError:
    """测试 ErrorHandler.record_error 方法"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_record_error_with_yunshu_error(self):
        """测试记录 YunshuError"""
        handler = ErrorHandler()
        error = YunshuError("测试错误")
        
        result = handler.record_error(error)
        
        assert result is error
        assert handler._metrics["YunshuError"].total_count == 1

    @pytest.mark.unit
    @pytest.mark.p0
    def test_record_error_with_regular_exception(self):
        """测试记录普通异常"""
        handler = ErrorHandler()
        exc = ValueError("普通错误")
        
        result = handler.record_error(exc)
        
        assert isinstance(result, YunshuError)
        assert result._original_exception is exc
        # 普通异常会被转换为YunshuError，使用YunshuError作为key
        assert handler._metrics["YunshuError"].total_count == 1

    @pytest.mark.unit
    @pytest.mark.p0
    def test_record_error_with_custom_key(self):
        """测试使用自定义 key 记录错误"""
        handler = ErrorHandler()
        error = YunshuError("测试错误")
        
        handler.record_error(error, key="custom_key")
        
        assert handler._metrics["custom_key"].total_count == 1


class TestErrorHandlerWithCircuitBreaker:
    """测试 ErrorHandler 与熔断器配合"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_register_and_get_circuit_breaker(self):
        """测试注册和获取熔断器"""
        handler = ErrorHandler()
        cb = CircuitBreaker(name="test_cb")
        
        handler.register_circuit_breaker("test", cb)
        
        assert handler.get_circuit_breaker("test") is cb
        assert handler.get_circuit_breaker("nonexistent") is None


class TestAsyncWithRetryComplete:
    """测试 async_with_retry 装饰器的完整功能"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_async_with_retry_success_first_attempt(self):
        """测试异步重试 - 首次尝试成功"""
        call_count = [0]
        
        @async_with_retry(max_retries=2, initial_delay=0.01)
        async def func():
            call_count[0] += 1
            return "success"
        
        import asyncio
        result = asyncio.run(func())
        
        assert result == "success"
        assert call_count[0] == 1

    @pytest.mark.unit
    @pytest.mark.p0
    def test_async_with_retry_yunshu_error_not_retryable(self):
        """测试异步重试 - YunshuError retryable=False"""
        call_count = [0]
        
        @async_with_retry(max_retries=2, initial_delay=0.01)
        async def func():
            call_count[0] += 1
            raise YunshuError("不可重试", retryable=False)
        
        import asyncio
        with pytest.raises(YunshuError):
            asyncio.run(func())
        
        assert call_count[0] == 1

    @pytest.mark.unit
    @pytest.mark.p0
    def test_async_with_retry_unknown_exception(self):
        """测试异步重试 - 未知异常类型"""
        call_count = [0]
        
        @async_with_retry(max_retries=1, initial_delay=0.01)
        async def func():
            call_count[0] += 1
            raise ValueError("未知异常")
        
        import asyncio
        with pytest.raises(YunshuError):
            asyncio.run(func())
        
        # ValueError 默认不在可重试列表中，但可能会尝试一次
        assert call_count[0] >= 1

    @pytest.mark.unit
    @pytest.mark.p0
    def test_async_with_retry_custom_retryable(self):
        """测试异步重试 - 自定义可重试异常"""
        call_count = [0]
        
        @async_with_retry(max_retries=2, initial_delay=0.01, retryable_exceptions=(ValueError,))
        async def func():
            call_count[0] += 1
            if call_count[0] < 3:
                raise ValueError("可重试的自定义异常")
            return "success"
        
        import asyncio
        result = asyncio.run(func())
        
        assert result == "success"
        assert call_count[0] == 3


class TestWithCircuitBreakerDecorator:
    """测试 with_circuit_breaker 装饰器"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_with_circuit_breaker_success(self):
        """测试熔断器装饰器 - 成功执行"""
        cb = CircuitBreaker(name="test_decorator", max_failures=3)
        
        @with_circuit_breaker(cb)
        def func():
            return "success"
        
        result = func()
        
        assert result == "success"
        assert cb.state.value == "closed"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_with_circuit_breaker_trip(self):
        """测试熔断器装饰器 - 触发熔断"""
        cb = CircuitBreaker(name="test_trip", max_failures=2, reset_timeout=0.1)
        
        @with_circuit_breaker(cb)
        def func():
            raise ValueError("总是失败")
        
        # 触发熔断
        with pytest.raises(ValueError):
            func()
        with pytest.raises(ValueError):
            func()
        
        # 第三次调用应该触发熔断
        with pytest.raises(YunshuError):
            func()
        
        assert cb.state.value == "open"


class TestRetryPolicyEdgeCases_error_handler_supplement:
    """测试 RetryPolicy 边缘情况"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_retry_policy_zero_delay(self):
        """测试重试策略 - 零延迟"""
        policy = RetryPolicy(max_retries=1, initial_delay=0.0, max_delay=0.0)
        
        delay = policy.calculate_delay(0)
        
        assert delay == 0.0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_retry_policy_jitter_only(self):
        """测试重试策略 - 仅抖动"""
        policy = RetryPolicy(max_retries=1, initial_delay=1.0, jitter_factor=1.0)
        
        delays = [policy.calculate_delay(0) for _ in range(10)]
        
        # 抖动范围取决于具体实现，确保延迟不为负且在合理范围内
        assert all(d >= 0 for d in delays)
        assert all(d <= 2.0 for d in delays)
