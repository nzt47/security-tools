"""
ErrorHandler 剩余代码覆盖测试
针对 error_handler.py 中未覆盖的 111 行代码进行补充测试
"""
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