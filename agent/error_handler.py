"""
统一错误处理和自动重试模块
提供完整的错误分类、自动重试、断路器模式等功能
"""
from __future__ import annotations
import time
import logging
import threading
import traceback
import functools
from enum import Enum
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Tuple,
    Type,
    TypeVar,
    Union,
    ParamSpec,
)
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict

# 类型定义
P = ParamSpec('P')
R = TypeVar('R')
F = TypeVar('F', bound=Callable[..., Any])

logger = logging.getLogger(__name__)


class ErrorSeverity(Enum):
    """错误严重程度"""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class ErrorCategory(Enum):
    """错误分类"""
    # 临时网络问题
    NETWORK_TEMPORARY = "network_temporary"
    NETWORK_TIMEOUT = "network_timeout"
    NETWORK_CONNECTION = "network_connection"
    # 系统资源
    RESOURCE_MEMORY = "resource_memory"
    RESOURCE_DISK = "resource_disk"
    RESOURCE_CPU = "resource_cpu"
    # 外部服务
    EXTERNAL_SERVICE = "external_service"
    EXTERNAL_API = "external_api"
    # 数据问题
    DATA_INVALID = "data_invalid"
    DATA_MISSING = "data_missing"
    DATA_CORRUPT = "data_corrupt"
    # 权限安全
    PERMISSION_DENIED = "permission_denied"
    SECURITY_ALERT = "security_alert"
    # 配置问题
    CONFIG_ERROR = "config_error"
    # 未知问题
    UNKNOWN = "unknown"


class CircuitState(Enum):
    """断路器状态"""
    CLOSED = "closed"  # 正常状态
    OPEN = "open"  # 断开状态，拒绝请求
    HALF_OPEN = "half_open"  # 半开状态，尝试恢复


@dataclass
class ErrorMetrics:
    """错误指标记录"""
    total_count: int = 0
    count_by_severity: Dict[ErrorSeverity, int] = field(default_factory=dict)
    count_by_category: Dict[ErrorCategory, int] = field(default_factory=dict)
    first_occurrence: Optional[datetime] = None
    last_occurrence: Optional[datetime] = None
    # 用于指数退避
    retry_attempts: int = 0
    last_retry_time: Optional[datetime] = None
    next_retry_time: Optional[datetime] = None

    def __post_init__(self):
        for severity in ErrorSeverity:
            if severity not in self.count_by_severity:
                self.count_by_severity[severity] = 0
        for category in ErrorCategory:
            if category not in self.count_by_category:
                self.count_by_category[category] = 0


class YunshuError(Exception):
    """云枢系统基础异常类"""
    severity: ErrorSeverity = ErrorSeverity.ERROR
    category: ErrorCategory = ErrorCategory.UNKNOWN
    recoverable: bool = False
    retryable: bool = False
    requires_restart: bool = False
    requires_user_notification: bool = False
    default_retry_count: int = 3
    default_retry_delay: float = 1.0

    def __init__(
        self,
        message: str,
        severity: Optional[ErrorSeverity] = None,
        category: Optional[ErrorCategory] = None,
        recoverable: Optional[bool] = None,
        retryable: Optional[bool] = None,
        requires_restart: Optional[bool] = None,
        requires_user_notification: Optional[bool] = None,
        context: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        if severity is not None:
            self.severity = severity
        if category is not None:
            self.category = category
        if recoverable is not None:
            self.recoverable = recoverable
        if retryable is not None:
            self.retryable = retryable
        if requires_restart is not None:
            self.requires_restart = requires_restart
        if requires_user_notification is not None:
            self.requires_user_notification = requires_user_notification
        self.context = context or {}
        self.timestamp = datetime.now()
        self._original_exception: Optional[Exception] = None

    def with_original(self, exc: Exception) -> YunshuError:
        """记录原始异常"""
        self._original_exception = exc
        return self

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "type": self.__class__.__name__,
            "message": self.message,
            "severity": self.severity.value,
            "category": self.category.value,
            "recoverable": self.recoverable,
            "retryable": self.retryable,
            "context": self.context,
            "timestamp": self.timestamp.isoformat(),
            "original_exception": str(self._original_exception) if self._original_exception else None,
        }


class RecoverableError(YunshuError):
    """可恢复错误 - 自动重试"""
    severity: ErrorSeverity = ErrorSeverity.WARNING
    recoverable: bool = True
    retryable: bool = True


class CriticalError(YunshuError):
    """严重错误 - 需要重启或人工干预"""
    severity: ErrorSeverity = ErrorSeverity.CRITICAL
    requires_restart: bool = True


class TemporaryNetworkError(RecoverableError):
    """临时网络错误"""
    category: ErrorCategory = ErrorCategory.NETWORK_TEMPORARY
    default_retry_count: int = 5
    default_retry_delay: float = 0.5


class NetworkTimeoutError(RecoverableError):
    """网络超时错误"""
    category: ErrorCategory = ErrorCategory.NETWORK_TIMEOUT
    default_retry_count: int = 3
    default_retry_delay: float = 1.0


class ExternalServiceError(RecoverableError):
    """外部服务错误"""
    category: ErrorCategory = ErrorCategory.EXTERNAL_SERVICE
    default_retry_count: int = 3
    default_retry_delay: float = 2.0


class DataInvalidError(YunshuError):
    """数据无效错误"""
    category: ErrorCategory = ErrorCategory.DATA_INVALID
    severity: ErrorSeverity = ErrorSeverity.WARNING
    recoverable: bool = True
    retryable: bool = False


class SecurityError(YunshuError):
    """安全错误"""
    severity: ErrorSeverity = ErrorSeverity.CRITICAL
    category: ErrorCategory = ErrorCategory.SECURITY_ALERT
    requires_user_notification: bool = True


class CircuitBreaker:
    """熔断器模式实现"""

    def __init__(
        self,
        max_failures: int = 5,
        reset_timeout: float = 60.0,
        half_open_timeout: float = 30.0,
        name: str = "default",
    ):
        self.max_failures = max_failures
        self.reset_timeout = reset_timeout
        self.half_open_timeout = half_open_timeout
        self.name = name
        
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.last_success_time: Optional[datetime] = None
        self.half_open_start: Optional[datetime] = None
        self._lock = threading.Lock()
        
        logger.info(f"Circuit breaker '{name}' initialized: max_failures={max_failures}")

    def _can_reset(self) -> bool:
        """检查是否可以重置断路器"""
        if self.last_failure_time is None:
            return False
        elapsed = (datetime.now() - self.last_failure_time).total_seconds()
        return elapsed >= self.reset_timeout

    def _can_half_open(self) -> bool:
        """检查是否可以进入半开状态"""
        return self.state == CircuitState.OPEN and self._can_reset()

    def record_success(self) -> None:
        """记录成功"""
        with self._lock:
            self.success_count += 1
            self.last_success_time = datetime.now()
            
            if self.state == CircuitState.HALF_OPEN:
                # 半开状态成功，恢复到闭合
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                logger.info(f"Circuit breaker '{self.name}': recovered to CLOSED state")
            elif self.state == CircuitState.CLOSED:
                # 正常状态，重置失败计数
                self.failure_count = 0

    def record_failure(self) -> None:
        """记录失败"""
        with self._lock:
            self.failure_count += 1
            self.last_failure_time = datetime.now()
            
            if self.state == CircuitState.CLOSED:
                if self.failure_count >= self.max_failures:
                    self.state = CircuitState.OPEN
                    logger.warning(
                        f"Circuit breaker '{self.name}': OPENED after {self.failure_count} failures"
                    )
            elif self.state == CircuitState.HALF_OPEN:
                # 半开状态失败，重新断开
                self.state = CircuitState.OPEN
                self.last_failure_time = datetime.now()
                logger.warning(
                    f"Circuit breaker '{self.name}': reopened after failure in half-open state"
                )

    def execute(self, func: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> R:
        """执行函数，受断路器保护"""
        with self._lock:
            if self.state == CircuitState.OPEN:
                if self._can_half_open():
                    self.state = CircuitState.HALF_OPEN
                    self.half_open_start = datetime.now()
                    logger.info(f"Circuit breaker '{self.name}': transitioning to HALF_OPEN")
                else:
                    raise CriticalError(
                        f"Circuit breaker '{self.name}' is OPEN",
                        category=ErrorCategory.EXTERNAL_SERVICE,
                    )
        
        try:
            result = func(*args, **kwargs)
            self.record_success()
            return result
        except Exception:
            self.record_failure()
            raise

    def is_open(self) -> bool:
        """检查断路器是否打开"""
        return self.state == CircuitState.OPEN

    def get_status(self) -> Dict[str, Any]:
        """获取断路器状态"""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "max_failures": self.max_failures,
            "last_failure_time": self.last_failure_time.isoformat() if self.last_failure_time else None,
            "last_success_time": self.last_success_time.isoformat() if self.last_success_time else None,
        }


class RetryPolicy:
    """重试策略"""

    def __init__(
        self,
        max_retries: int = 3,
        initial_delay: float = 1.0,
        max_delay: float = 30.0,
        backoff_factor: float = 2.0,
        jitter_factor: float = 0.1,
    ):
        self.max_retries = max_retries
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.backoff_factor = backoff_factor
        self.jitter_factor = jitter_factor

    def calculate_delay(self, attempt: int) -> float:
        """计算当前尝试的延迟（指数退避+抖动）"""
        import random
        delay = min(
            self.initial_delay * (self.backoff_factor ** attempt),
            self.max_delay,
        )
        # 添加抖动，避免惊群效应
        jitter = random.uniform(1 - self.jitter_factor, 1 + self.jitter_factor)
        return delay * jitter


class ErrorHandler:
    """统一错误处理器"""

    def __init__(self):
        self._metrics: Dict[str, ErrorMetrics] = defaultdict(ErrorMetrics)
        self._circuit_breakers: Dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()
        logger.info("Error handler initialized")

    def register_circuit_breaker(
        self,
        name: str,
        circuit_breaker: CircuitBreaker,
    ) -> None:
        """注册熔断器"""
        with self._lock:
            self._circuit_breakers[name] = circuit_breaker

    def get_circuit_breaker(self, name: str) -> Optional[CircuitBreaker]:
        """获取熔断器"""
        return self._circuit_breakers.get(name)

    def record_error(
        self,
        error: Union[Exception, YunshuError],
        key: Optional[str] = None,
    ) -> YunshuError:
        """记录错误并返回标准化的YunshuError"""
        if isinstance(error, YunshuError):
            Yunshu_error = error
        else:
            # 将普通异常转换为YunshuError
            Yunshu_error = YunshuError(
                str(error),
                category=ErrorCategory.UNKNOWN,
                recoverable=False,
            ).with_original(error)

        error_key = key or Yunshu_error.__class__.__name__

        with self._lock:
            metrics = self._metrics[error_key]
            metrics.total_count += 1
            metrics.count_by_severity[Yunshu_error.severity] += 1
            metrics.count_by_category[Yunshu_error.category] += 1
            
            if metrics.first_occurrence is None:
                metrics.first_occurrence = Yunshu_error.timestamp
            metrics.last_occurrence = Yunshu_error.timestamp

        # 根据严重级别记录日志
        log_method = {
            ErrorSeverity.DEBUG: logger.debug,
            ErrorSeverity.INFO: logger.info,
            ErrorSeverity.WARNING: logger.warning,
            ErrorSeverity.ERROR: logger.error,
            ErrorSeverity.CRITICAL: logger.critical,
        }.get(Yunshu_error.severity, logger.error)

        log_message = (
            f"[{Yunshu_error.severity.value}] {Yunshu_error.category.value}: "
            f"{Yunshu_error.message}"
        )
        
        if Yunshu_error._original_exception:
            log_method(log_message, exc_info=Yunshu_error._original_exception)
        else:
            log_method(log_message)

        return Yunshu_error

    def execute_with_retry(
        self,
        func: Callable[P, R],
        retry_policy: Optional[RetryPolicy] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
        retryable_exceptions: Optional[Tuple[Type[Exception], ...]] = None,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> R:
        """执行函数，带自动重试和熔断器保护"""
        policy = retry_policy or RetryPolicy()
        
        retryable = retryable_exceptions or (RecoverableError, YunshuError)
        
        for attempt in range(policy.max_retries + 1):
            try:
                if circuit_breaker:
                    return circuit_breaker.execute(func, *args, **kwargs)
                else:
                    return func(*args, **kwargs)
            except Exception as e:
                # 检查是否可以重试
                if isinstance(e, YunshuError):
                    if not e.retryable:
                        raise self.record_error(e)
                elif not any(
                    issubclass(e.__class__, cls) for cls in retryable
                ):
                    raise self.record_error(e)
                
                if attempt >= policy.max_retries:
                    logger.error(
                        f"All {policy.max_retries} retry attempts exhausted",
                        exc_info=True,
                    )
                    raise self.record_error(e)
                
                delay = policy.calculate_delay(attempt)
                logger.warning(
                    f"Attempt {attempt + 1}/{policy.max_retries} failed, "
                    f"retrying in {delay:.2f}s: {e}"
                )
                time.sleep(delay)

    def get_metrics(self, key: Optional[str] = None) -> Dict[str, Any]:
        """获取错误指标"""
        with self._lock:
            if key:
                if key not in self._metrics:
                    return {}
                m = self._metrics[key]
                return {
                    "key": key,
                    "total_count": m.total_count,
                    "count_by_severity": {
                        s.value: c for s, c in m.count_by_severity.items()
                    },
                    "count_by_category": {
                        c.value: cnt for c, cnt in m.count_by_category.items()
                    },
                    "first_occurrence": m.first_occurrence.isoformat() if m.first_occurrence else None,
                    "last_occurrence": m.last_occurrence.isoformat() if m.last_occurrence else None,
                }
            else:
                return {
                    key: self.get_metrics(key) for key in self._metrics
                }

    def get_circuit_breaker_status(self) -> Dict[str, Dict[str, Any]]:
        """获取所有熔断器状态"""
        with self._lock:
            return {
                name: cb.get_status()
                for name, cb in self._circuit_breakers.items()
            }


# 全局错误处理器实例
_global_error_handler = ErrorHandler()


def get_error_handler() -> ErrorHandler:
    """获取全局错误处理器"""
    return _global_error_handler


def with_retry(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    circuit_breaker: Optional[CircuitBreaker] = None,
    retryable_exceptions: Optional[Tuple[Type[Exception], ...]] = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    装饰器：自动重试
    
    用法:
        @with_retry(max_retries=3, initial_delay=1.0)
        def my_function():
            ...
    """
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            policy = RetryPolicy(
                max_retries=max_retries,
                initial_delay=initial_delay,
                max_delay=max_delay,
                backoff_factor=backoff_factor,
            )
            handler = get_error_handler()
            return handler.execute_with_retry(
                func,
                retry_policy=policy,
                circuit_breaker=circuit_breaker,
                retryable_exceptions=retryable_exceptions,
                *args,
                **kwargs,
            )
        return wrapper
    return decorator


def with_circuit_breaker(
    circuit_breaker: CircuitBreaker,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    装饰器：熔断器保护
    
    用法:
        cb = CircuitBreaker(max_failures=5)
        @with_circuit_breaker(cb)
        def my_function():
            ...
    """
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            return circuit_breaker.execute(func, *args, **kwargs)
        return wrapper
    return decorator
