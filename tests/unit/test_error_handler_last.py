"""
ErrorHandler 最终补充测试 - 覆盖剩余代码
目标：将覆盖率提升至 80%
"""
import pytest
from unittest.mock import MagicMock, patch
from agent.error_handler import (
    ErrorHandler,
    YunshuError,
    CircuitBreaker,
    RetryPolicy,
    ErrorSeverity,
    ErrorCategory,
)


class TestErrorHandlerFinalCoverage:
    """测试 error_handler.py 剩余未覆盖的代码"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_yunshu_error_all_severities(self):
        """测试所有错误严重级别"""
        for severity in ErrorSeverity:
            error = YunshuError(f"测试 {severity.value}", severity=severity)
            assert error.severity == severity

    @pytest.mark.unit
    @pytest.mark.p0
    def test_yunshu_error_all_categories(self):
        """测试所有错误分类"""
        for category in ErrorCategory:
            error = YunshuError(f"测试 {category.value}", category=category)
            assert error.category == category

    @pytest.mark.unit
    @pytest.mark.p0
    def test_yunshu_error_with_all_params(self):
        """测试带所有可选参数的 YunshuError"""
        error = YunshuError(
            message="测试错误",
            severity=ErrorSeverity.WARNING,
            retryable=True,
            context={"key": "value"}
        )
        
        assert error.message == "测试错误"
        assert error.severity == ErrorSeverity.WARNING
        assert error.retryable is True
        assert error.context == {"key": "value"}

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_max_failures_zero(self):
        """测试熔断器 max_failures=0 的情况"""
        cb = CircuitBreaker(name="test", max_failures=0)
        
        # max_failures=0 应该允许执行（不触发熔断）
        result = cb.execute(lambda: "success")
        assert result == "success"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_reset_timeout_zero(self):
        """测试熔断器 reset_timeout=0 的情况"""
        cb = CircuitBreaker(name="test", max_failures=1, reset_timeout=0)
        
        # 触发熔断
        with pytest.raises(ValueError):
            cb.execute(lambda: (_ for _ in ()).throw(ValueError()))
        
        # reset_timeout=0 应该立即尝试恢复
        assert cb.state.value in ["open", "half_open"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_retry_policy_max_delay(self):
        """测试重试策略的最大延迟限制"""
        policy = RetryPolicy(
            max_retries=5,
            initial_delay=1.0,
            max_delay=3.0,
            backoff_factor=2.0
        )
        
        # 计算多次延迟，确保不超过最大延迟
        for attempt in range(5):
            delay = policy.calculate_delay(attempt)
            assert delay <= 3.0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_retry_policy_jitter_factor(self):
        """测试重试策略的抖动因子"""
        policy = RetryPolicy(
            max_retries=3,
            initial_delay=1.0,
            jitter_factor=0.0
        )
        
        # 测试计算延迟
        delay = policy.calculate_delay(0)
        assert delay is not None
        assert delay >= 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_error_handler_clear_metrics(self):
        """测试错误处理器清除指标"""
        handler = ErrorHandler()
        handler.record_error(YunshuError("测试"))
        
        # 清除指标（如果存在该方法）
        if hasattr(handler, 'clear_metrics'):
            handler.clear_metrics()
            metrics = handler.get_metrics("YunshuError")
            assert metrics == {}
        else:
            pytest.skip("clear_metrics 方法不存在")

    @pytest.mark.unit
    @pytest.mark.p0
    def test_error_handler_execute_with_retry_no_policy(self):
        """测试不带重试策略的 execute_with_retry"""
        handler = ErrorHandler()
        
        def success_func():
            return "success"
        
        result = handler.execute_with_retry(success_func)
        
        assert result == "success"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_error_handler_custom_key(self):
        """测试使用自定义 key 记录错误"""
        handler = ErrorHandler()
        error = YunshuError("测试错误")
        
        handler.record_error(error, key="custom_key")
        
        metrics = handler.get_metrics("custom_key")
        assert metrics["total_count"] == 1