"""
ErrorHandler 单元测试
测试 agent/error_handler.py 的功能
"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta
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
    CircuitBreaker,
    RetryPolicy,
    ErrorHandler,
    get_error_handler,
    with_retry,
    with_circuit_breaker,
)


class TestErrorSeverity:
    """测试错误严重程度枚举"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_severity_values(self):
        """测试严重程度值"""
        assert ErrorSeverity.DEBUG.value == "debug"
        assert ErrorSeverity.INFO.value == "info"
        assert ErrorSeverity.WARNING.value == "warning"
        assert ErrorSeverity.ERROR.value == "error"
        assert ErrorSeverity.CRITICAL.value == "critical"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_severity_order(self):
        """测试严重程度顺序"""
        values = list(ErrorSeverity)
        assert values[0] == ErrorSeverity.DEBUG
        assert values[-1] == ErrorSeverity.CRITICAL


class TestErrorMetrics:
    """测试 ErrorMetrics 类"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_error_metrics_initialization(self):
        """测试 ErrorMetrics 初始化"""
        from agent.error_handler import ErrorMetrics
        
        metrics = ErrorMetrics()
        assert metrics.total_count == 0
        assert metrics.retry_attempts == 0
        # 验证所有严重程度和分类都已初始化
        for severity in ErrorSeverity:
            assert severity in metrics.count_by_severity
            assert metrics.count_by_severity[severity] == 0
        for category in ErrorCategory:
            assert category in metrics.count_by_category
            assert metrics.count_by_category[category] == 0


class TestErrorCategory:
    """测试错误分类枚举"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_category_exists(self):
        """测试所有分类存在"""
        categories = [
            "NETWORK_TEMPORARY", "NETWORK_TIMEOUT", "NETWORK_CONNECTION",
            "RESOURCE_MEMORY", "RESOURCE_DISK", "RESOURCE_CPU",
            "EXTERNAL_SERVICE", "EXTERNAL_API",
            "DATA_INVALID", "DATA_MISSING", "DATA_CORRUPT",
            "PERMISSION_DENIED", "SECURITY_ALERT",
            "CONFIG_ERROR", "UNKNOWN"
        ]
        for cat in categories:
            assert hasattr(ErrorCategory, cat)


class TestYunshuError:
    """测试 YunshuError 基础异常类"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_error_creation(self):
        """测试错误创建"""
        error = YunshuError("测试错误")
        assert error.message == "测试错误"
        assert error.severity == ErrorSeverity.ERROR
        assert error.category == ErrorCategory.UNKNOWN
        assert error.recoverable is False
        assert error.retryable is False
        assert error.requires_restart is False
        assert error.requires_user_notification is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_error_with_parameters(self):
        """测试带参数的错误创建"""
        error = YunshuError(
            "测试错误",
            severity=ErrorSeverity.WARNING,
            category=ErrorCategory.DATA_INVALID,
            recoverable=True,
            retryable=True,
            context={"key": "value"}
        )
        assert error.severity == ErrorSeverity.WARNING
        assert error.category == ErrorCategory.DATA_INVALID
        assert error.recoverable is True
        assert error.retryable is True
        assert error.context == {"key": "value"}

    @pytest.mark.unit
    @pytest.mark.p1
    def test_error_to_dict(self):
        """测试转换为字典"""
        error = YunshuError("测试错误")
        result = error.to_dict()
        assert result["type"] == "YunshuError"
        assert result["message"] == "测试错误"
        assert "timestamp" in result
        assert "severity" in result
        assert "category" in result
        assert "recoverable" in result
        assert "retryable" in result

    @pytest.mark.unit
    @pytest.mark.p1
    def test_error_to_dict_with_original(self):
        """测试转换为字典时包含原始异常"""
        original_exc = ValueError("原始错误")
        error = YunshuError("包装错误").with_original(original_exc)
        result = error.to_dict()
        assert "original_exception" in result
        assert "原始错误" in result["original_exception"]

    @pytest.mark.unit
    @pytest.mark.p1
    def test_error_with_original(self):
        """测试记录原始异常"""
        original_exc = ValueError("原始错误")
        error = YunshuError("包装错误").with_original(original_exc)
        assert error._original_exception == original_exc
        # 测试链式调用返回 self
        assert error.with_original(ValueError("another")) is error


class TestErrorSubclasses:
    """测试错误子类"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_recoverable_error(self):
        """测试可恢复错误"""
        error = RecoverableError("可恢复错误")
        assert error.recoverable is True
        assert error.retryable is True
        assert error.severity == ErrorSeverity.WARNING

    @pytest.mark.unit
    @pytest.mark.p0
    def test_critical_error(self):
        """测试严重错误"""
        error = CriticalError("严重错误")
        assert error.requires_restart is True
        assert error.severity == ErrorSeverity.CRITICAL

    @pytest.mark.unit
    @pytest.mark.p1
    def test_network_errors(self):
        """测试网络错误"""
        temp_error = TemporaryNetworkError("临时网络错误")
        assert temp_error.category == ErrorCategory.NETWORK_TEMPORARY
        assert temp_error.default_retry_count == 5

        timeout_error = NetworkTimeoutError("超时错误")
        assert timeout_error.category == ErrorCategory.NETWORK_TIMEOUT

        service_error = ExternalServiceError("外部服务错误")
        assert service_error.category == ErrorCategory.EXTERNAL_SERVICE

    @pytest.mark.unit
    @pytest.mark.p1
    def test_data_and_security_errors(self):
        """测试数据和安全错误"""
        data_error = DataInvalidError("数据无效")
        assert data_error.category == ErrorCategory.DATA_INVALID

        security_error = SecurityError("安全告警")
        assert security_error.severity == ErrorSeverity.CRITICAL
        assert security_error.requires_user_notification is True


class TestCircuitBreaker:
    """测试熔断器"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_initial_state(self):
        """测试熔断器初始状态"""
        cb = CircuitBreaker(max_failures=3, reset_timeout=60)
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0
        assert cb.success_count == 0
        assert cb.is_open() is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_record_success(self):
        """测试记录成功"""
        cb = CircuitBreaker(max_failures=3)
        cb.record_success()
        assert cb.success_count == 1
        assert cb.failure_count == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_trip(self):
        """测试熔断器跳闸"""
        cb = CircuitBreaker(max_failures=3)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.is_open() is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_execute_success(self):
        """测试执行成功"""
        cb = CircuitBreaker(max_failures=3)
        
        def success_func():
            return "success"
        
        result = cb.execute(success_func)
        assert result == "success"
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_execute_failure(self):
        """测试执行失败触发熔断"""
        cb = CircuitBreaker(max_failures=2)
        
        def failure_func():
            raise ValueError("失败")
        
        # 第一次失败
        with pytest.raises(ValueError):
            cb.execute(failure_func)
        assert cb.state == CircuitState.CLOSED
        
        # 第二次失败，触发熔断
        with pytest.raises(ValueError):
            cb.execute(failure_func)
        assert cb.state == CircuitState.OPEN

    @pytest.mark.unit
    @pytest.mark.p1
    def test_circuit_breaker_open_blocks(self):
        """测试熔断打开时阻止请求"""
        cb = CircuitBreaker(max_failures=1)
        cb.state = CircuitState.OPEN
        
        def func():
            return "test"
        
        with pytest.raises(CriticalError):
            cb.execute(func)

    @pytest.mark.unit
    @pytest.mark.p1
    def test_circuit_breaker_get_status(self):
        """测试获取状态"""
        cb = CircuitBreaker(name="test")
        cb.record_success()
        status = cb.get_status()
        assert status["name"] == "test"
        assert status["state"] == "closed"
        assert status["success_count"] == 1
        assert "last_success_time" in status
        assert "last_failure_time" in status

    @pytest.mark.unit
    @pytest.mark.p1
    def test_circuit_breaker_half_open_recovery(self):
        """测试半开状态恢复"""
        cb = CircuitBreaker(max_failures=1, reset_timeout=0.01)
        cb.record_failure()  # 触发熔断
        assert cb.state == CircuitState.OPEN
        
        # 等待超时
        import time
        time.sleep(0.02)
        
        # 第一次调用应该进入半开状态
        def success_func():
            return "success"
        
        result = cb.execute(success_func)
        assert result == "success"
        # 成功后应该恢复到闭合状态
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.unit
    @pytest.mark.p1
    def test_circuit_breaker_half_open_reopen(self):
        """测试半开状态再次失败"""
        cb = CircuitBreaker(max_failures=1, reset_timeout=0.01)
        cb.record_failure()  # 触发熔断
        
        # 等待超时
        import time
        time.sleep(0.02)
        
        # 半开状态调用失败
        def failure_func():
            raise ValueError("失败")
        
        with pytest.raises(ValueError):
            cb.execute(failure_func)
        # 应该重新断开
        assert cb.state == CircuitState.OPEN


class TestRetryPolicy:
    """测试重试策略"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_retry_policy_calculate_delay(self):
        """测试计算延迟"""
        policy = RetryPolicy(
            max_retries=5,
            initial_delay=1.0,
            max_delay=30.0,
            backoff_factor=2.0,
            jitter_factor=0.0
        )
        delay = policy.calculate_delay(0)
        assert delay == 1.0
        
        delay = policy.calculate_delay(1)
        assert delay == 2.0
        
        delay = policy.calculate_delay(2)
        assert delay == 4.0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_retry_policy_max_delay(self):
        """测试最大延迟限制"""
        policy = RetryPolicy(
            initial_delay=1.0,
            max_delay=10.0,
            backoff_factor=2.0,
            jitter_factor=0.0  # 禁用抖动以确保精确测试
        )
        # 第4次尝试应该达到最大延迟
        delay = policy.calculate_delay(4)
        assert delay == 10.0  # 1.0 * 2.0^4 = 16.0，但被限制到 10.0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_retry_policy_jitter(self):
        """测试抖动功能"""
        policy = RetryPolicy(
            initial_delay=1.0,
            max_delay=10.0,
            backoff_factor=1.0,
            jitter_factor=0.5
        )
        # 多次调用应该有不同的延迟（因为有抖动）
        delays = set()
        for _ in range(10):
            delays.add(policy.calculate_delay(0))
        # 应该有多个不同的延迟值
        assert len(delays) > 1


class TestDataInvalidError:
    """测试 DataInvalidError 类"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_data_invalid_error_defaults(self):
        """测试 DataInvalidError 默认属性"""
        error = DataInvalidError("数据无效")
        assert error.category == ErrorCategory.DATA_INVALID
        assert error.severity == ErrorSeverity.WARNING
        assert error.recoverable is True
        assert error.retryable is False


class TestSecurityError:
    """测试 SecurityError 类"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_security_error_defaults(self):
        """测试 SecurityError 默认属性"""
        error = SecurityError("安全警报")
        assert error.category == ErrorCategory.SECURITY_ALERT
        assert error.severity == ErrorSeverity.CRITICAL
        assert error.requires_user_notification is True


class TestExternalServiceError:
    """测试 ExternalServiceError 类"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_external_service_error_defaults(self):
        """测试 ExternalServiceError 默认属性"""
        error = ExternalServiceError("外部服务错误")
        assert error.category == ErrorCategory.EXTERNAL_SERVICE
        assert error.default_retry_count == 3
        assert error.default_retry_delay == 2.0


class TestErrorHandler:
    """测试错误处理器"""

    @pytest.fixture
    def error_handler(self):
        """创建错误处理器实例"""
        return ErrorHandler()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_error_handler_initialization(self, error_handler):
        """测试错误处理器初始化"""
        assert error_handler is not None
        assert hasattr(error_handler, '_metrics')
        assert hasattr(error_handler, '_circuit_breakers')
        assert hasattr(error_handler, '_lock')

    @pytest.mark.unit
    @pytest.mark.p0
    def test_record_error(self, error_handler):
        """测试记录错误"""
        error = YunshuError("测试错误", severity=ErrorSeverity.WARNING)
        result = error_handler.record_error(error)
        
        assert isinstance(result, YunshuError)
        metrics = error_handler.get_metrics("YunshuError")
        assert metrics["total_count"] == 1
        assert "count_by_severity" in metrics
        assert "count_by_category" in metrics

    @pytest.mark.unit
    @pytest.mark.p0
    def test_record_error_with_key(self, error_handler):
        """测试使用自定义键记录错误"""
        error = YunshuError("测试错误")
        result = error_handler.record_error(error, key="custom_key")
        
        metrics = error_handler.get_metrics("custom_key")
        assert metrics["total_count"] == 1
        assert metrics["key"] == "custom_key"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_metrics_nonexistent_key(self, error_handler):
        """测试获取不存在的键的指标"""
        metrics = error_handler.get_metrics("nonexistent_key")
        assert metrics == {}

    @pytest.mark.unit
    @pytest.mark.p0
    def test_record_standard_exception(self, error_handler):
        """测试记录标准异常"""
        exc = ValueError("标准异常")
        result = error_handler.record_error(exc)
        
        assert isinstance(result, YunshuError)
        assert result.category == ErrorCategory.UNKNOWN
        assert result._original_exception is exc

    @pytest.mark.unit
    @pytest.mark.p0
    def test_record_all_severities(self, error_handler):
        """测试记录所有严重级别的错误"""
        for severity in ErrorSeverity:
            error = YunshuError(f"测试 {severity.value}", severity=severity)
            error_handler.record_error(error)
        
        metrics = error_handler.get_metrics("YunshuError")
        assert metrics["total_count"] == len(ErrorSeverity)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_register_circuit_breaker(self, error_handler):
        """测试注册熔断器"""
        cb = CircuitBreaker(name="test_cb")
        error_handler.register_circuit_breaker("test_cb", cb)
        
        retrieved = error_handler.get_circuit_breaker("test_cb")
        assert retrieved is cb

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_nonexistent_circuit_breaker(self, error_handler):
        """测试获取不存在的熔断器"""
        cb = error_handler.get_circuit_breaker("nonexistent")
        assert cb is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_circuit_breaker_status(self, error_handler):
        """测试获取所有熔断器状态"""
        cb1 = CircuitBreaker(name="cb1")
        cb2 = CircuitBreaker(name="cb2")
        error_handler.register_circuit_breaker("cb1", cb1)
        error_handler.register_circuit_breaker("cb2", cb2)
        
        status = error_handler.get_circuit_breaker_status()
        assert "cb1" in status
        assert "cb2" in status
        assert status["cb1"]["name"] == "cb1"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_execute_with_retry_success(self, error_handler):
        """测试带重试执行成功"""
        def success_func():
            return "success"
        
        result = error_handler.execute_with_retry(success_func)
        assert result == "success"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_execute_with_retry_with_circuit_breaker(self, error_handler):
        """测试带熔断器的重试执行"""
        cb = CircuitBreaker(max_failures=5)
        
        def success_func():
            return "success"
        
        result = error_handler.execute_with_retry(success_func, circuit_breaker=cb)
        assert result == "success"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_execute_with_retry_with_custom_policy(self, error_handler):
        """测试使用自定义重试策略"""
        policy = RetryPolicy(max_retries=1, initial_delay=0.01)
        
        def success_func():
            return "success"
        
        result = error_handler.execute_with_retry(success_func, retry_policy=policy)
        assert result == "success"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_execute_with_retry_retryable_error(self, error_handler):
        """测试重试可重试错误"""
        call_count = [0]
        
        def failing_func():
            call_count[0] += 1
            if call_count[0] < 3:
                raise RecoverableError("暂时失败")
            return "success"
        
        # 使用较小的延迟
        policy = RetryPolicy(max_retries=2, initial_delay=0.01)
        result = error_handler.execute_with_retry(failing_func, retry_policy=policy)
        assert result == "success"
        assert call_count[0] == 3

    @pytest.mark.unit
    @pytest.mark.p1
    def test_execute_with_retry_non_retryable(self, error_handler):
        """测试不可重试错误不重试"""
        call_count = [0]
        
        def failing_func():
            call_count[0] += 1
            raise ValueError("不可重试")
        
        with pytest.raises(YunshuError):
            error_handler.execute_with_retry(failing_func)
        assert call_count[0] == 1

    @pytest.mark.unit
    @pytest.mark.p1
    def test_execute_with_retry_custom_retryable_exceptions(self, error_handler):
        """测试自定义可重试异常"""
        call_count = [0]
        
        def failing_func():
            call_count[0] += 1
            if call_count[0] < 2:
                raise ValueError("可重试")
            return "success"
        
        # 使用自定义重试策略和可重试异常
        policy = RetryPolicy(max_retries=1, initial_delay=0.01)
        result = error_handler.execute_with_retry(
            failing_func,
            retry_policy=policy,
            retryable_exceptions=(ValueError,)
        )
        assert result == "success"
        assert call_count[0] == 2

    @pytest.mark.unit
    @pytest.mark.p1
    def test_get_metrics(self, error_handler):
        """测试获取指标"""
        error = YunshuError("测试")
        error_handler.record_error(error)
        
        metrics = error_handler.get_metrics()
        assert "YunshuError" in metrics

        specific = error_handler.get_metrics("YunshuError")
        assert specific["total_count"] == 1
        assert "first_occurrence" in specific
        assert "last_occurrence" in specific


class TestDecorators:
    """测试装饰器"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_with_retry_decorator(self):
        """测试 with_retry 装饰器"""
        call_count = [0]
        
        @with_retry(max_retries=2, initial_delay=0.01)
        def func():
            call_count[0] += 1
            if call_count[0] < 3:
                raise RecoverableError("失败")
            return "success"
        
        result = func()
        assert result == "success"
        assert call_count[0] == 3

    @pytest.mark.unit
    @pytest.mark.p1
    def test_with_circuit_breaker_decorator(self):
        """测试 with_circuit_breaker 装饰器"""
        cb = CircuitBreaker(max_failures=1)
        
        @with_circuit_breaker(cb)
        def func():
            raise ValueError("失败")
        
        with pytest.raises(ValueError):
            func()
        
        # 第二次调用应该触发熔断
        with pytest.raises(CriticalError):
            func()


class TestGlobalErrorHandler:
    """测试全局错误处理器"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_error_handler(self):
        """测试获取全局错误处理器"""
        handler1 = get_error_handler()
        handler2 = get_error_handler()
        
        assert handler1 is handler2
        assert isinstance(handler1, ErrorHandler)


class TestErrorMetricsPostInit:
    """测试 ErrorMetrics.__post_init__ 方法（覆盖行 92-97）"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_post_init_populates_severity_dict(self):
        """测试 __post_init__ 自动填充 count_by_severity"""
        from agent.error_handler import ErrorMetrics
        
        metrics = ErrorMetrics()
        # 验证所有严重程度都已初始化（覆盖行 92-94）
        for severity in ErrorSeverity:
            assert severity in metrics.count_by_severity
            assert metrics.count_by_severity[severity] == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_post_init_populates_category_dict(self):
        """测试 __post_init__ 自动填充 count_by_category"""
        from agent.error_handler import ErrorMetrics
        
        metrics = ErrorMetrics()
        # 验证所有分类都已初始化（覆盖行 95-97）
        for category in ErrorCategory:
            assert category in metrics.count_by_category
            assert metrics.count_by_category[category] == 0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_post_init_preserves_existing_values(self):
        """测试 __post_init__ 保留已存在的值"""
        from agent.error_handler import ErrorMetrics
        
        # 创建带有预填充值的 metrics
        metrics = ErrorMetrics(
            count_by_severity={ErrorSeverity.ERROR: 5},
            count_by_category={ErrorCategory.NETWORK_TEMPORARY: 3}
        )
        # 验证预填充值被保留
        assert metrics.count_by_severity[ErrorSeverity.ERROR] == 5
        assert metrics.count_by_category[ErrorCategory.NETWORK_TEMPORARY] == 3
        # 验证其他值被初始化为 0
        assert metrics.count_by_severity[ErrorSeverity.WARNING] == 0
        assert metrics.count_by_category[ErrorCategory.DATA_INVALID] == 0


class TestYunshuErrorFullInit:
    """测试 YunshuError 完整初始化流程（覆盖行 120-132）"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_full_init_with_all_params(self):
        """测试完整初始化（覆盖行 120-132）"""
        error = YunshuError(
            "完整测试错误",
            severity=ErrorSeverity.CRITICAL,
            category=ErrorCategory.SECURITY_ALERT,
            recoverable=True,
            retryable=True,
            context={"key": "value", "count": 10}
        )
        # 验证所有属性（覆盖行 121-132）
        assert error.message == "完整测试错误"
        assert error.severity == ErrorSeverity.CRITICAL
        assert error.category == ErrorCategory.SECURITY_ALERT
        assert error.recoverable is True
        assert error.retryable is True
        assert error.context == {"key": "value", "count": 10}
        assert error.timestamp is not None
        assert error._original_exception is None

    @pytest.mark.unit
    @pytest.mark.p1
    def test_init_with_none_context(self):
        """测试 context 为 None 时使用空字典"""
        error = YunshuError("测试", context=None)
        assert error.context == {}  # 覆盖行 130


class TestCircuitBreakerFullInit:
    """测试 CircuitBreaker 完整初始化流程（覆盖行 213-226）"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_full_init(self):
        """测试完整初始化（覆盖行 213-226）"""
        cb = CircuitBreaker(
            max_failures=10,
            reset_timeout=120.0,
            half_open_timeout=60.0,
            name="test_full"
        )
        # 验证所有属性（覆盖行 213-224）
        assert cb.max_failures == 10
        assert cb.reset_timeout == 120.0
        assert cb.half_open_timeout == 60.0
        assert cb.name == "test_full"
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0
        assert cb.success_count == 0
        assert cb.last_failure_time is None
        assert cb.last_success_time is None
        assert cb.half_open_start is None
        assert cb._lock is not None


class TestCircuitBreakerInternalMethods:
    """测试 CircuitBreaker 内部方法（覆盖行 230-237）"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_can_reset_no_failure_time(self):
        """测试 _can_reset 无失败时间时返回 False（覆盖行 230-231）"""
        cb = CircuitBreaker()
        assert cb._can_reset() is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_can_reset_with_failure_time(self):
        """测试 _can_reset 有失败时间时计算 elapsed（覆盖行 232-233）"""
        cb = CircuitBreaker(reset_timeout=0.1)
        cb.record_failure()
        # 等待超过 reset_timeout
        import time
        time.sleep(0.15)
        assert cb._can_reset() is True

    @pytest.mark.unit
    @pytest.mark.p1
    def test_can_half_open(self):
        """测试 _can_half_open（覆盖行 237）"""
        cb = CircuitBreaker(reset_timeout=0.1)
        cb.state = CircuitState.OPEN
        assert cb._can_half_open() is False  # 还没超时
        
        cb.record_failure()  # 设置 last_failure_time
        import time
        time.sleep(0.15)
        assert cb._can_half_open() is True  # 已超时


class TestCircuitBreakerRecordMethods:
    """测试 CircuitBreaker 记录方法（覆盖行 241-270）"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_record_success_in_half_open(self):
        """测试半开状态记录成功恢复（覆盖行 245-249）"""
        cb = CircuitBreaker(max_failures=1, reset_timeout=0.01)
        cb.record_failure()  # 触发熔断
        import time
        time.sleep(0.02)
        
        # 进入半开状态后记录成功
        cb.state = CircuitState.HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_record_success_in_closed(self):
        """测试闭合状态记录成功重置失败计数（覆盖行 250-252）"""
        cb = CircuitBreaker()
        cb.failure_count = 5  # 模拟一些失败
        cb.record_success()
        assert cb.failure_count == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_record_failure_in_closed_reaches_threshold(self):
        """测试闭合状态失败达到阈值触发熔断（覆盖行 260-265）"""
        cb = CircuitBreaker(max_failures=2)
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()  # 达到阈值
        assert cb.state == CircuitState.OPEN

    @pytest.mark.unit
    @pytest.mark.p1
    def test_record_failure_in_half_open_reopens(self):
        """测试半开状态失败重新断开（覆盖行 266-272）"""
        cb = CircuitBreaker(max_failures=1, reset_timeout=0.01)
        cb.record_failure()  # 触发熔断
        import time
        time.sleep(0.02)
        
        cb.state = CircuitState.HALF_OPEN
        cb.record_failure()
        assert cb.state == CircuitState.OPEN


class TestCircuitBreakerExecuteFull:
    """测试 CircuitBreaker execute 完整流程（覆盖行 276-294）"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_execute_transitions_to_half_open(self):
        """测试执行时从 OPEN 转换到 HALF_OPEN（覆盖行 278-281）"""
        cb = CircuitBreaker(max_failures=1, reset_timeout=0.01)
        cb.record_failure()  # 触发熔断
        import time
        time.sleep(0.02)
        
        def success_func():
            return "success"
        
        result = cb.execute(success_func)
        assert result == "success"
        assert cb.half_open_start is not None

    @pytest.mark.unit
    @pytest.mark.p1
    def test_execute_raises_critical_error_when_open(self):
        """测试 OPEN 状态执行抛出 CriticalError（覆盖行 283-286）"""
        cb = CircuitBreaker(max_failures=1)
        cb.record_failure()  # 触发熔断
        
        def func():
            return "test"
        
        with pytest.raises(CriticalError) as exc_info:
            cb.execute(func)
        assert "is OPEN" in str(exc_info.value.message)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_execute_success_records_success(self):
        """测试执行成功后记录成功（覆盖行 289-291）"""
        cb = CircuitBreaker()
        
        def success_func():
            return "result"
        
        result = cb.execute(success_func)
        assert result == "result"
        assert cb.success_count == 1

    @pytest.mark.unit
    @pytest.mark.p0
    def test_execute_failure_records_failure(self):
        """测试执行失败后记录失败（覆盖行 292-294）"""
        cb = CircuitBreaker()
        
        def failure_func():
            raise ValueError("失败")
        
        with pytest.raises(ValueError):
            cb.execute(failure_func)
        assert cb.failure_count == 1


class TestCircuitBreakerStatusMethods:
    """测试 CircuitBreaker 状态方法（覆盖行 298-310）"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_is_open_true(self):
        """测试 is_open 返回 True（覆盖行 298）"""
        cb = CircuitBreaker()
        cb.state = CircuitState.OPEN
        assert cb.is_open() is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_is_open_false(self):
        """测试 is_open 返回 False"""
        cb = CircuitBreaker()
        assert cb.is_open() is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_status_full(self):
        """测试 get_status 返回完整状态（覆盖行 302-310）"""
        cb = CircuitBreaker(name="status_test", max_failures=10)
        cb.record_failure()
        cb.record_success()
        
        status = cb.get_status()
        assert status["name"] == "status_test"
        assert status["state"] == "closed"
        assert status["failure_count"] == 0  # 成功后重置
        assert status["success_count"] == 1
        assert status["max_failures"] == 10
        assert status["last_failure_time"] is not None
        assert status["last_success_time"] is not None


class TestRetryPolicyFullInit:
    """测试 RetryPolicy 完整初始化（覆盖行 324-328）"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_full_init(self):
        """测试完整初始化（覆盖行 324-328）"""
        policy = RetryPolicy(
            max_retries=5,
            initial_delay=2.0,
            max_delay=60.0,
            backoff_factor=3.0,
            jitter_factor=0.2
        )
        assert policy.max_retries == 5
        assert policy.initial_delay == 2.0
        assert policy.max_delay == 60.0
        assert policy.backoff_factor == 3.0
        assert policy.jitter_factor == 0.2


class TestRetryPolicyCalculateDelayWithRandom:
    """测试 RetryPolicy.calculate_delay 包含 random（覆盖行 332-339）"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_calculate_delay_imports_random(self):
        """测试 calculate_delay 导入 random（覆盖行 332）"""
        policy = RetryPolicy(jitter_factor=0.1)
        # 调用 calculate_delay 确保内部导入 random
        delay = policy.calculate_delay(0)
        assert delay > 0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_calculate_delay_with_jitter(self):
        """测试带抖动的延迟计算（覆盖行 338-339）"""
        policy = RetryPolicy(
            initial_delay=1.0,
            max_delay=10.0,
            backoff_factor=2.0,
            jitter_factor=0.5
        )
        # 多次调用验证抖动效果
        delays = [policy.calculate_delay(1) for _ in range(10)]
        # 由于抖动，延迟应该在 0.5 * 2.0 到 1.5 * 2.0 范围内
        for delay in delays:
            assert 1.0 <= delay <= 3.0


class TestErrorHandlerFullInit:
    """测试 ErrorHandler 完整初始化（覆盖行 357-358）"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_full_init(self):
        """测试完整初始化（覆盖行 357-358）"""
        handler = ErrorHandler()
        assert handler._metrics is not None
        assert handler._circuit_breakers == {}
        assert handler._lock is not None


class TestErrorHandlerRecordErrorFull:
    """测试 ErrorHandler.record_error 完整流程（覆盖行 370-411）"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_record_error_updates_metrics(self):
        """测试记录错误更新指标（覆盖行 382-390）"""
        handler = ErrorHandler()
        error = YunshuError("测试错误", severity=ErrorSeverity.WARNING)
        
        handler.record_error(error)
        metrics = handler._metrics["YunshuError"]
        
        assert metrics.total_count == 1
        assert metrics.count_by_severity[ErrorSeverity.WARNING] == 1
        assert metrics.count_by_category[ErrorCategory.UNKNOWN] == 1
        assert metrics.first_occurrence is not None
        assert metrics.last_occurrence is not None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_record_error_logs_by_severity(self):
        """测试根据严重级别记录日志（覆盖行 393-409）"""
        handler = ErrorHandler()
        
        # 测试所有严重级别的日志记录
        for severity in ErrorSeverity:
            error = YunshuError(f"测试 {severity.value}", severity=severity)
            handler.record_error(error)

    @pytest.mark.unit
    @pytest.mark.p1
    def test_record_error_with_original_exception_logs_traceback(self):
        """测试带原始异常时记录 traceback（覆盖行 406-409）"""
        handler = ErrorHandler()
        original = ValueError("原始错误")
        error = YunshuError("包装错误").with_original(original)
        
        result = handler.record_error(error)
        assert result._original_exception is original


class TestErrorHandlerExecuteWithRetryFull:
    """测试 ErrorHandler.execute_with_retry 完整流程（覆盖行 423-455）"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_execute_with_retry_non_retryable_yunshu_error(self):
        """测试不可重试的 YunshuError 直接抛出（覆盖行 435-437）"""
        handler = ErrorHandler()
        
        def func():
            raise YunshuError("不可重试", retryable=False)
        
        with pytest.raises(YunshuError):
            handler.execute_with_retry(func)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_execute_with_retry_exhausts_attempts(self):
        """测试重试次数耗尽（覆盖行 443-448）"""
        handler = ErrorHandler()
        call_count = [0]
        
        def always_fail():
            call_count[0] += 1
            raise RecoverableError("总是失败")
        
        policy = RetryPolicy(max_retries=2, initial_delay=0.01)
        
        with pytest.raises(YunshuError):
            handler.execute_with_retry(always_fail, retry_policy=policy)
        
        assert call_count[0] == 3  # 1 次初始 + 2 次重试

    @pytest.mark.unit
    @pytest.mark.p1
    def test_execute_with_retry_logs_retry_warning(self):
        """测试重试时记录警告日志（覆盖行 450-455）"""
        handler = ErrorHandler()
        call_count = [0]
        
        def fail_twice():
            call_count[0] += 1
            if call_count[0] < 3:
                raise RecoverableError("暂时失败")
            return "success"
        
        policy = RetryPolicy(max_retries=2, initial_delay=0.01)
        result = handler.execute_with_retry(fail_twice, retry_policy=policy)
        assert result == "success"


class TestErrorHandlerGetMetricsFull:
    """测试 ErrorHandler.get_metrics 完整流程（覆盖行 459-477）"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_metrics_all_keys(self):
        """测试获取所有键的指标（覆盖行 477-479）"""
        handler = ErrorHandler()
        handler.record_error(YunshuError("错误1"))
        handler.record_error(RecoverableError("错误2"), key="recoverable")
        
        all_metrics = handler.get_metrics()
        assert "YunshuError" in all_metrics
        assert "recoverable" in all_metrics

    @pytest.mark.unit
    @pytest.mark.p1
    def test_get_metrics_formats_datetime(self):
        """测试指标中 datetime 格式化（覆盖行 473-474）"""
        handler = ErrorHandler()
        handler.record_error(YunshuError("测试"))
        
        metrics = handler.get_metrics("YunshuError")
        assert metrics["first_occurrence"] is not None
        assert metrics["last_occurrence"] is not None
        # 验证是 ISO 格式字符串
        assert "T" in metrics["first_occurrence"]


class TestDecoratorsFull:
    """测试装饰器完整流程（覆盖行 515-534, 549-554）"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_with_retry_decorator_full_params(self):
        """测试 with_retry 装饰器完整参数（覆盖行 515-534）"""
        call_count = [0]
        
        @with_retry(
            max_retries=3,
            initial_delay=0.01,
            max_delay=0.1,
            backoff_factor=1.5
        )
        def func():
            call_count[0] += 1
            if call_count[0] < 4:
                raise RecoverableError("失败")
            return "success"
        
        result = func()
        assert result == "success"
        assert call_count[0] == 4

    @pytest.mark.unit
    @pytest.mark.p0
    def test_with_circuit_breaker_decorator_success(self):
        """测试 with_circuit_breaker 装饰器成功执行（覆盖行 549-554）"""
        cb = CircuitBreaker(max_failures=5)
        
        @with_circuit_breaker(cb)
        def success_func():
            return "success"
        
        result = success_func()
        assert result == "success"
        assert cb.success_count == 1


class TestErrorMetricsAdditional:
    """测试 ErrorMetrics 额外字段（覆盖行 87-89）"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_error_metrics_retry_fields(self):
        """测试 retry_attempts, last_retry_time, next_retry_time 字段"""
        from agent.error_handler import ErrorMetrics
        
        metrics = ErrorMetrics()
        assert metrics.retry_attempts == 0
        assert metrics.last_retry_time is None
        assert metrics.next_retry_time is None
        
        # 设置这些字段
        now = datetime.now()
        metrics.retry_attempts = 5
        metrics.last_retry_time = now
        metrics.next_retry_time = now + timedelta(seconds=10)
        
        assert metrics.retry_attempts == 5
        assert metrics.last_retry_time == now
        assert metrics.next_retry_time == now + timedelta(seconds=10)


class TestYunshuErrorAdditional:
    """测试 YunshuError 额外属性（覆盖行 106-107）"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_yunshu_error_requires_restart(self):
        """测试 requires_restart 属性"""
        error = YunshuError("测试")
        assert error.requires_restart is False
        
        error2 = YunshuError("需要重启", requires_restart=True)
        assert error2.requires_restart is True

    @pytest.mark.unit
    @pytest.mark.p1
    def test_yunshu_error_requires_user_notification(self):
        """测试 requires_user_notification 属性"""
        error = YunshuError("测试")
        assert error.requires_user_notification is False
        
        error2 = YunshuError("需要通知", requires_user_notification=True)
        assert error2.requires_user_notification is True


class TestCircuitBreakerHalfOpenTimeout:
    """测试 CircuitBreaker half_open_timeout 参数（覆盖行 215）"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_circuit_breaker_half_open_timeout_init(self):
        """测试 half_open_timeout 初始化"""
        cb = CircuitBreaker(
            max_failures=5,
            reset_timeout=60.0,
            half_open_timeout=30.0,
            name="test_half_open"
        )
        assert cb.half_open_timeout == 30.0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_circuit_breaker_half_open_start(self):
        """测试 half_open_start 记录（覆盖行 280）"""
        cb = CircuitBreaker(max_failures=1, reset_timeout=0.01)
        cb.record_failure()  # 触发熔断
        
        import time
        time.sleep(0.02)
        
        def success_func():
            return "success"
        
        cb.execute(success_func)
        assert cb.half_open_start is not None


class TestErrorHandlerConcurrency:
    """测试 ErrorHandler 并发访问（覆盖锁的使用）"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_error_handler_thread_safety(self):
        """测试错误处理器的线程安全性"""
        import threading
        
        handler = ErrorHandler()
        error_count = [0]
        
        def record_errors():
            for i in range(10):
                error = YunshuError(f"thread_error_{i}")
                handler.record_error(error)
                error_count[0] += 1
        
        threads = []
        for _ in range(5):
            t = threading.Thread(target=record_errors)
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()
        
        metrics = handler.get_metrics("YunshuError")
        assert metrics["total_count"] == 50


class TestErrorHandlerExecuteWithRetryEdgeCases:
    """测试 ErrorHandler.execute_with_retry 边界情况"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_execute_with_retry_no_args(self, error_handler):
        """测试无参数调用"""
        def no_arg_func():
            return "no_args"
        
        result = error_handler.execute_with_retry(no_arg_func)
        assert result == "no_args"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_execute_with_retry_with_args(self, error_handler):
        """测试带参数调用"""
        def with_args_func(a, b, c=3):
            return a + b + c
        
        result = error_handler.execute_with_retry(with_args_func, 1, 2, c=4)
        assert result == 7


class TestRetryPolicyEdgeCases:
    """测试 RetryPolicy 边界情况"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_retry_policy_zero_initial_delay(self):
        """测试初始延迟为0"""
        policy = RetryPolicy(initial_delay=0.0, jitter_factor=0.0)
        delay = policy.calculate_delay(0)
        assert delay == 0.0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_retry_policy_zero_backoff(self):
        """测试退避因子为1（无指数增长）"""
        policy = RetryPolicy(initial_delay=1.0, backoff_factor=1.0, jitter_factor=0.0)
        delay1 = policy.calculate_delay(0)
        delay2 = policy.calculate_delay(1)
        delay3 = policy.calculate_delay(2)
        assert delay1 == delay2 == delay3 == 1.0


class TestErrorHandlerEmptyMetrics:
    """测试 ErrorHandler 空指标情况"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_get_metrics_empty(self, error_handler):
        """测试获取空指标"""
        metrics = error_handler.get_metrics()
        assert metrics == {}


class TestYunshuErrorDefaultRetrySettings:
    """测试 YunshuError 默认重试设置（覆盖行 108-109）"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_default_retry_count(self):
        """测试 default_retry_count"""
        error = YunshuError("测试")
        assert error.default_retry_count == 3

    @pytest.mark.unit
    @pytest.mark.p1
    def test_default_retry_delay(self):
        """测试 default_retry_delay"""
        error = YunshuError("测试")
        assert error.default_retry_delay == 1.0


class TestRetryPolicyCoverage:
    """测试 RetryPolicy 的完整覆盖"""
    
    @pytest.mark.unit
    @pytest.mark.p0
    def test_retry_policy_full_init(self):
        """测试 RetryPolicy 完整初始化"""
        policy = RetryPolicy(
            max_retries=5,
            initial_delay=2.0,
            max_delay=60.0,
            backoff_factor=3.0,
            jitter_factor=0.2
        )
        assert policy.max_retries == 5
        assert policy.initial_delay == 2.0
        assert policy.max_delay == 60.0
        assert policy.backoff_factor == 3.0
        assert policy.jitter_factor == 0.2
    
    @pytest.mark.unit
    @pytest.mark.p0
    def test_calculate_delay_with_jitter(self):
        """测试 calculate_delay 包含抖动计算"""
        policy = RetryPolicy(
            initial_delay=1.0,
            max_delay=10.0,
            jitter_factor=0.0  # 无抖动
        )
        # 测试不同尝试次数
        delay0 = policy.calculate_delay(0)
        delay1 = policy.calculate_delay(1)
        delay2 = policy.calculate_delay(2)
        
        assert delay0 == 1.0  # 1 * 2^0
        assert delay1 == 2.0  # 1 * 2^1
        assert delay2 == 4.0  # 1 * 2^2
    
    @pytest.mark.unit
    @pytest.mark.p0
    def test_calculate_delay_max_limit(self):
        """测试延迟超过 max_delay 时的限制"""
        policy = RetryPolicy(
            initial_delay=10.0,
            max_delay=5.0,  # 最大值小于初始值
            jitter_factor=0.0
        )
        delay = policy.calculate_delay(0)
        assert delay == 5.0


class TestErrorHandlerAdditionalCoverage:
    """测试 ErrorHandler 的额外功能覆盖"""
    
    @pytest.fixture
    def error_handler_with_circuit_breaker(self):
        """带熔断器的错误处理器 fixture"""
        handler = ErrorHandler()
        cb = CircuitBreaker(max_failures=3)
        handler.register_circuit_breaker("test_cb", cb)
        return handler
    
    @pytest.mark.unit
    @pytest.mark.p0
    def test_register_and_get_circuit_breaker(self, error_handler_with_circuit_breaker):
        """测试注册和获取熔断器"""
        handler = error_handler_with_circuit_breaker
        
        cb = handler.get_circuit_breaker("test_cb")
        assert cb is not None
        assert cb.max_failures == 3
        
        # 测试获取不存在的熔断器
        not_found = handler.get_circuit_breaker("not_found")
        assert not_found is None
    
    @pytest.mark.unit
    @pytest.mark.p0
    def test_record_error_with_key(self):
        """测试使用自定义 key 记录错误"""
        handler = ErrorHandler()
        
        error = YunshuError("测试错误")
        recorded = handler.record_error(error, key="custom_key")
        
        # 获取指标验证
        metrics = handler.get_metrics("custom_key")
        assert metrics["total_count"] == 1
        assert metrics["key"] == "custom_key"
    
    @pytest.mark.unit
    @pytest.mark.p0
    def test_record_error_with_original_exception(self):
        """测试记录带有原始异常的错误"""
        handler = ErrorHandler()
        
        try:
            raise ValueError("原始错误")
        except ValueError as e:
            error = YunshuError("包装错误")
            error.with_original(e)
            recorded = handler.record_error(error)
        
        # 验证 to_dict 包含原始异常信息
        error_dict = recorded.to_dict()
        assert "original_exception" in error_dict
    
    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_all_metrics(self):
        """测试获取所有指标"""
        handler = ErrorHandler()
        
        # 记录多个不同的错误
        handler.record_error(YunshuError("错误1"))
        handler.record_error(RecoverableError("错误2"))
        
        # 获取所有指标
        all_metrics = handler.get_metrics()
        assert isinstance(all_metrics, dict)
        assert len(all_metrics) >= 2
    
    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_metrics_nonexistent_key(self):
        """测试获取不存在的 key 的指标"""
        handler = ErrorHandler()
        metrics = handler.get_metrics("nonexistent_key")
        assert metrics == {}
    
    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_circuit_breaker_status(self, error_handler_with_circuit_breaker):
        """测试获取所有熔断器状态"""
        handler = error_handler_with_circuit_breaker
        
        status = handler.get_circuit_breaker_status()
        assert "test_cb" in status
        assert "state" in status["test_cb"]
        assert "failure_count" in status["test_cb"]
    
    @pytest.mark.unit
    @pytest.mark.p0
    def test_execute_with_retry_with_custom_retryable(self):
        """测试使用自定义的可重试异常类型"""
        handler = ErrorHandler()
        policy = RetryPolicy(max_retries=1)
        
        call_count = [0]
        
        def failing_func():
            call_count[0] += 1
            raise TypeError("自定义可重试异常")
        
        # 只有 TypeError 是可重试的
        try:
            handler.execute_with_retry(
                failing_func,
                retry_policy=policy,
                retryable_exceptions=(TypeError,)
            )
            pytest.fail("Expected exception not raised")
        except Exception as e:
            pass
        
        # 验证重试了一次（第一次失败 + 一次重试）
        assert call_count[0] == 2


class TestGlobalErrorHandler:
    """测试全局错误处理器"""
    
    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_error_handler(self):
        """测试获取全局错误处理器"""
        handler = get_error_handler()
        assert isinstance(handler, ErrorHandler)
        
        # 再次获取应该是同一个实例
        handler2 = get_error_handler()
        assert handler is handler2


class TestDecoratorCoverage:
    """测试装饰器的完整覆盖"""
    
    @pytest.mark.unit
    @pytest.mark.p0
    def test_with_retry_decorator_full(self):
        """测试 with_retry 装饰器的完整参数"""
        cb = CircuitBreaker(max_failures=2)
        
        @with_retry(
            max_retries=1,
            initial_delay=0.1,
            max_delay=10.0,
            backoff_factor=1.5,
            circuit_breaker=cb,
            retryable_exceptions=(ValueError,)
        )
        def test_func(x):
            return x * 2
        
        result = test_func(5)
        assert result == 10
    
    @pytest.mark.unit
    @pytest.mark.p0
    def test_with_circuit_breaker_decorator(self):
        """测试 with_circuit_breaker 装饰器"""
        cb = CircuitBreaker(max_failures=1)
        
        @with_circuit_breaker(cb)
        def success_func():
            return "success"
        
        @with_circuit_breaker(cb)
        def failure_func():
            raise ValueError("Oops")
        
        # 测试成功
        result = success_func()
        assert result == "success"
        assert cb.success_count == 1
        
        # 测试失败
        try:
            failure_func()
        except Exception:
            pass
        
        assert cb.failure_count == 1