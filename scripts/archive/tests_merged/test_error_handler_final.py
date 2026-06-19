"""
ErrorHandler 最终补充测试用例
覆盖剩余未覆盖的代码：metrics收集分支、get_metrics、get_circuit_breaker_status等
"""
import pytest
from unittest.mock import MagicMock, patch
from agent.error_handler import (
    ErrorHandler,
    ErrorMetrics,
    ErrorSeverity,
    ErrorCategory,
    YunshuError,
    CircuitBreaker,
    RetryPolicy,
    with_retry,
    get_error_handler,
)


class TestErrorHandlerMetrics:
    """测试 ErrorHandler 的 metrics 收集功能"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_metrics_with_key(self):
        """测试获取特定 key 的指标"""
        handler = ErrorHandler()
        handler.record_error(YunshuError("测试错误"))
        
        metrics = handler.get_metrics("YunshuError")
        
        assert metrics["key"] == "YunshuError"
        assert metrics["total_count"] == 1
        assert "count_by_severity" in metrics
        assert "count_by_category" in metrics
        assert "first_occurrence" in metrics
        assert "last_occurrence" in metrics

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_metrics_nonexistent_key(self):
        """测试获取不存在的 key 的指标"""
        handler = ErrorHandler()
        
        metrics = handler.get_metrics("nonexistent")
        
        assert metrics == {}

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_metrics_all(self):
        """测试获取所有指标"""
        handler = ErrorHandler()
        handler.record_error(YunshuError("错误1"))
        handler.record_error(YunshuError("错误2"))
        
        all_metrics = handler.get_metrics()
        
        assert isinstance(all_metrics, dict)
        assert "YunshuError" in all_metrics

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_circuit_breaker_status(self):
        """测试获取熔断器状态"""
        handler = ErrorHandler()
        cb1 = CircuitBreaker(name="cb1", max_failures=3)
        cb2 = CircuitBreaker(name="cb2", max_failures=5)
        
        handler.register_circuit_breaker("cb1", cb1)
        handler.register_circuit_breaker("cb2", cb2)
        
        status = handler.get_circuit_breaker_status()
        
        assert "cb1" in status
        assert "cb2" in status
        assert status["cb1"]["name"] == "cb1"
        assert status["cb2"]["name"] == "cb2"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_circuit_breaker_status_empty(self):
        """测试获取空熔断器状态"""
        handler = ErrorHandler()
        
        status = handler.get_circuit_breaker_status()
        
        assert status == {}


class TestExecuteWithRetryMetrics:
    """测试 execute_with_retry 的 metrics 收集分支"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_execute_with_retry_success_metrics(self):
        """测试成功执行时的 metrics 收集"""
        handler = ErrorHandler()
        
        with patch('agent.error_handler.get_metrics_collector') as mock_collector:
            mock_instance = MagicMock()
            mock_collector.return_value = mock_instance
            
            def success_func():
                return "success"
            
            result = handler.execute_with_retry(
                success_func,
                error_counter="test_counter"
            )
            
            assert result == "success"
            mock_instance.increment_counter.assert_called_with("test_counter.success")

    @pytest.mark.unit
    @pytest.mark.p0
    def test_execute_with_retry_failure_metrics(self):
        """测试失败执行时的 metrics 收集"""
        handler = ErrorHandler()
        
        with patch('agent.error_handler.get_metrics_collector') as mock_collector:
            mock_instance = MagicMock()
            mock_collector.return_value = mock_instance
            
            def failure_func():
                raise ValueError("失败")
            
            with pytest.raises(YunshuError):
                handler.execute_with_retry(
                    failure_func,
                    error_counter="test_counter",
                    retry_policy=RetryPolicy(max_retries=0)
                )
            
            mock_instance.increment_counter.assert_called_with("test_counter.failure")

    @pytest.mark.unit
    @pytest.mark.p0
    def test_execute_with_retry_on_retry_callback(self):
        """测试 on_retry 回调"""
        handler = ErrorHandler()
        retry_count = [0]
        
        def on_retry_callback(attempt, exc):
            retry_count[0] += 1
        
        def failing_func():
            raise YunshuError("可重试", retryable=True)
        
        with pytest.raises(YunshuError):
            handler.execute_with_retry(
                failing_func,
                retry_policy=RetryPolicy(max_retries=2, initial_delay=0.01),
                on_retry=on_retry_callback
            )
        
        assert retry_count[0] == 2


class TestWithRetryDecorator:
    """测试 with_retry 装饰器"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_with_retry_success(self):
        """测试同步重试装饰器 - 成功"""
        call_count = [0]
        
        @with_retry(max_retries=2, initial_delay=0.01)
        def func():
            call_count[0] += 1
            return "success"
        
        result = func()
        
        assert result == "success"
        assert call_count[0] == 1

    @pytest.mark.unit
    @pytest.mark.p0
    def test_with_retry_with_circuit_breaker(self):
        """测试同步重试装饰器 - 带熔断器"""
        cb = CircuitBreaker(name="test_cb", max_failures=2)
        
        @with_retry(max_retries=1, initial_delay=0.01, circuit_breaker=cb)
        def func():
            raise ValueError("失败")
        
        # 第一次失败
        with pytest.raises(YunshuError):
            func()
        
        # 第二次失败触发熔断
        with pytest.raises(YunshuError):
            func()
        
        # 第三次应该被熔断器阻止
        with pytest.raises(YunshuError):
            func()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_with_retry_error_counter(self):
        """测试同步重试装饰器 - error_counter"""
        call_count = [0]
        
        with patch('agent.error_handler.get_metrics_collector') as mock_collector:
            mock_instance = MagicMock()
            mock_collector.return_value = mock_instance
            
            @with_retry(max_retries=0, error_counter="decorator_test")
            def func():
                call_count[0] += 1
                raise ValueError("失败")
            
            with pytest.raises(YunshuError):
                func()
            
            mock_instance.increment_counter.assert_called_with("decorator_test.failure")


class TestRetryPolicyComplete:
    """测试 RetryPolicy 的完整功能"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_retry_policy_custom_condition(self):
        """测试自定义重试条件"""
        def custom_condition(exc, attempt):
            return "custom" in str(exc)
        
        policy = RetryPolicy(
            max_retries=2,
            custom_retry_condition=custom_condition
        )
        
        assert policy.should_retry(ValueError("custom error"), 0) is True
        assert policy.should_retry(ValueError("other error"), 0) is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_retry_policy_retryable_exceptions(self):
        """测试可重试异常列表"""
        policy = RetryPolicy(
            max_retries=2,
            retryable_exceptions=(ValueError, TypeError)
        )
        
        assert policy.should_retry(ValueError("test"), 0) is True
        assert policy.should_retry(TypeError("test"), 0) is True
        assert policy.should_retry(IOError("test"), 0) is False


class TestGlobalErrorHandler:
    """测试全局错误处理器"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_error_handler_singleton(self):
        """测试获取全局错误处理器单例"""
        handler1 = get_error_handler()
        handler2 = get_error_handler()
        
        assert handler1 is handler2

    @pytest.mark.unit
    @pytest.mark.p0
    def test_global_error_handler_functionality(self):
        """测试全局错误处理器功能"""
        handler = get_error_handler()
        error = YunshuError("全局测试")
        
        result = handler.record_error(error)
        
        assert result is error
        metrics = handler.get_metrics("YunshuError")
        assert metrics["total_count"] >= 1


class TestCircuitBreakerComplete:
    """测试熔断器完整功能"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_reset_timeout(self):
        """测试熔断器超时重置"""
        cb = CircuitBreaker(name="test_reset", max_failures=1, reset_timeout=0.1)
        
        # 触发熔断
        with pytest.raises(ValueError):
            cb.execute(lambda: (_ for _ in ()).throw(ValueError()))
        
        # 等待超时
        import time
        time.sleep(0.2)
        
        # 熔断器应该进入半开状态
        assert cb.state.value == "half_open"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_success_after_reset(self):
        """测试熔断后成功恢复"""
        call_count = [0]
        
        cb = CircuitBreaker(name="test_recovery", max_failures=1, reset_timeout=0.1)
        
        def func():
            call_count[0] += 1
            if call_count[0] == 1:
                raise ValueError("第一次失败")
            return "success"
        
        # 第一次调用失败，触发熔断
        with pytest.raises(ValueError):
            cb.execute(func)
        
        # 等待超时
        import time
        time.sleep(0.2)
        
        # 第二次调用应该成功
        result = cb.execute(func)
        
        assert result == "success"
        assert cb.state.value == "closed"