"""
统一错误处理和自动重试模块
提供完整的错误分类、自动重试、断路器模式等功能
"""
from __future__ import annotations
import time
import json
import uuid
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


def _trace_id() -> str:
    """生成 trace_id（结构化日志用）"""
    return uuid.uuid4().hex[:16]


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
        
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "circuit_breaker.init", "duration_ms": 0, "name": name, "max_failures": max_failures}, ensure_ascii=False))

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
                logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "circuit_breaker.recovered", "duration_ms": 0, "name": self.name, "state": "CLOSED"}, ensure_ascii=False))
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
                    logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "circuit_breaker.opened", "duration_ms": 0, "name": self.name, "failure_count": self.failure_count}, ensure_ascii=False))
            elif self.state == CircuitState.HALF_OPEN:
                # 半开状态失败，重新断开
                self.state = CircuitState.OPEN
                self.last_failure_time = datetime.now()
                logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "circuit_breaker.reopened", "duration_ms": 0, "name": self.name, "state": "OPEN"}, ensure_ascii=False))

    def execute(self, func: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> R:
        """执行函数，受断路器保护"""
        with self._lock:
            if self.state == CircuitState.OPEN:
                if self._can_half_open():
                    self.state = CircuitState.HALF_OPEN
                    self.half_open_start = datetime.now()
                    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "circuit_breaker.half_open", "duration_ms": 0, "name": self.name, "state": "HALF_OPEN"}, ensure_ascii=False))
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
        max_retries: Optional[int] = None,
        initial_delay: float = 1.0,
        max_delay: float = 30.0,
        backoff_factor: float = 2.0,
        jitter_factor: float = 0.1,
        strategy: str = "exponential",
        retryable_exceptions: Optional[Tuple[Type[Exception], ...]] = None,
        retryable_status_codes: Optional[List[int]] = None,
        custom_retry_condition: Optional[Callable[[Exception], bool]] = None,
    ):
        # 配置化：未显式指定时从 Config 读取默认值（支持热加载）
        if max_retries is None:
            from agent.monitoring.observability_config import get_default_max_retries
            max_retries = get_default_max_retries()
        self.max_retries = max_retries
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.backoff_factor = backoff_factor
        self.jitter_factor = jitter_factor
        self.strategy = strategy
        self.retryable_exceptions = retryable_exceptions
        self.retryable_status_codes = retryable_status_codes
        self.custom_retry_condition = custom_retry_condition

    def should_retry(self, exception: Exception, attempt: int) -> bool:
        """判断是否应该重试"""
        if attempt >= self.max_retries:
            logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "attempt.attempt.max_retries", "msg": f"[RetryPolicy.should_retry] 重试次数已用尽: attempt={attempt}, max_retries={self.max_retries}"}, ensure_ascii=False))
            return False
        
        if self.retryable_exceptions and not isinstance(exception, self.retryable_exceptions):
            logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "exception_type.type.exception", "msg": f"[RetryPolicy.should_retry] 异常类型不匹配: exception_type={type(exception).__name__}, "
                f"retryable_exceptions={self.retryable_exceptions}"}, ensure_ascii=False))
            return False
        
        if self.custom_retry_condition and not self.custom_retry_condition(exception):
            logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "exception.exception", "msg": f"[RetryPolicy.should_retry] 自定义重试条件未满足: exception={exception}"}, ensure_ascii=False))
            return False
        
        logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "attempt.attempt.exception", "msg": f"[RetryPolicy.should_retry] 允许重试: attempt={attempt}, exception={exception}"}, ensure_ascii=False))
        return True

    def calculate_delay(self, attempt: int) -> float:
        """计算当前尝试的延迟"""
        import random
        
        logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "attempt.attempt.strategy", "msg": f"[RetryPolicy.calculate_delay] 开始计算延迟: attempt={attempt}, strategy={self.strategy}, "
            f"initial_delay={self.initial_delay}, backoff_factor={self.backoff_factor}, "
            f"max_delay={self.max_delay}, jitter_factor={self.jitter_factor}"}, ensure_ascii=False))
        
        if self.strategy == "fixed":
            delay = self.initial_delay
            logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "delay.delay", "msg": f"[RetryPolicy.calculate_delay] 使用固定延迟策略: delay={delay}"}, ensure_ascii=False))
        elif self.strategy == "linear":
            delay = self.initial_delay * (attempt + 1)
            logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "delay.delay", "msg": f"[RetryPolicy.calculate_delay] 使用线性延迟策略: delay={delay}"}, ensure_ascii=False))
        elif self.strategy == "exponential":
            raw_delay = self.initial_delay * (self.backoff_factor ** attempt)
            delay = min(raw_delay, self.max_delay)
            logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "raw_delay.raw_delay", "msg": f"[RetryPolicy.calculate_delay] 使用指数退避策略: raw_delay={raw_delay}, "
                f"max_delay={self.max_delay}, applied_delay={delay}"}, ensure_ascii=False))
        else:
            delay = self.initial_delay
            logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "delay.delay", "msg": f"[RetryPolicy.calculate_delay] 使用默认延迟策略: delay={delay}"}, ensure_ascii=False))
        
        original_delay = delay
        if self.jitter_factor > 0:
            jitter = random.uniform(1 - self.jitter_factor, 1 + self.jitter_factor)
            delay = delay * jitter
            logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "jitter.jitter", "msg": f"[RetryPolicy.calculate_delay] 应用抖动: jitter={jitter:.4f}, "
                f"original_delay={original_delay:.4f}, jittered_delay={delay:.4f}"}, ensure_ascii=False))
        
        final_delay = min(delay, self.max_delay)
        logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "final_delay.final_delay", "msg": f"[RetryPolicy.calculate_delay] 计算完成: final_delay={final_delay:.4f}"}, ensure_ascii=False))
        
        return final_delay


class ErrorHandler:
    """统一错误处理器"""

    def __init__(
        self,
        metrics_collector_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        """
        Args:
            metrics_collector_factory: 可选的 metrics collector 工厂（DI 模式）。
                若提供则优先使用，未提供时在需要时延迟 import get_metrics_collector。
                用于打破 error_handler -> agent.monitoring.metrics 的硬依赖，便于测试注入 mock。
        """
        self._metrics: Dict[str, ErrorMetrics] = defaultdict(ErrorMetrics)
        self._circuit_breakers: Dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()
        self._metrics_collector_factory: Optional[Callable[[], Any]] = metrics_collector_factory
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "error.handler.initialized", "msg": "Error handler initialized"}, ensure_ascii=False))

    def _get_metrics_collector(self) -> Any:
        """获取 metrics collector（DI 优先，未注入时延迟 import）

        成功路径零依赖模式：未注入 factory 时才在调用点延迟 import agent.monitoring.metrics，
        避免模块级硬依赖。返回 None 时调用方应自行容错。
        """
        factory = self._metrics_collector_factory
        if factory is not None:
            return factory()
        # 延迟 import 打破循环依赖
        try:
            from agent.monitoring.metrics import get_metrics_collector
            return get_metrics_collector()
        except Exception:
            return None

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
        on_retry: Optional[Callable[[int, Exception], None]] = None,
        error_counter: Optional[str] = None,
        func_args: Optional[Tuple] = None,
        func_kwargs: Optional[Dict] = None,
    ) -> R:
        """执行函数，带自动重试和熔断器保护
        
        Args:
            func: 要执行的函数
            retry_policy: 重试策略
            circuit_breaker: 熔断器
            retryable_exceptions: 可重试的异常类型
            on_retry: 重试回调
            error_counter: 错误计数器名称
            func_args: 函数的位置参数（元组）
            func_kwargs: 函数的关键字参数（字典）
        """
        # 详细日志：记录调用参数
        logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "func.func.__name__", "msg": f"[execute_with_retry] 开始执行: func={func.__name__}, "
            f"retry_policy={retry_policy}, circuit_breaker={circuit_breaker}, "
            f"retryable_exceptions={retryable_exceptions}, on_retry={on_retry}, "
            f"error_counter={error_counter}, func_args={func_args}, func_kwargs={func_kwargs}"}, ensure_ascii=False))
        
        policy = retry_policy or RetryPolicy()
        
        retryable = retryable_exceptions or (RecoverableError, YunshuError)
        
        # 使用空元组和空字典作为默认值
        args = func_args or ()
        kwargs = func_kwargs or {}
        
        logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "args.args.kwargs", "msg": f"[execute_with_retry] 参数解析完成: args={args}, kwargs={kwargs}, "
            f"policy.max_retries={policy.max_retries}, policy.strategy={policy.strategy}"}, ensure_ascii=False))
        
        for attempt in range(policy.max_retries + 1):
            try:
                logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "attempt.func.func", "msg": f"[execute_with_retry] 第 {attempt + 1} 次尝试执行: func={func.__name__}"}, ensure_ascii=False))
                if circuit_breaker:
                    logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "circuit_breaker.circuit_breaker.name", "msg": f"[execute_with_retry] 通过熔断器执行: circuit_breaker={circuit_breaker.name}"}, ensure_ascii=False))
                    result = circuit_breaker.execute(func, *args, **kwargs)
                else:
                    result = func(*args, **kwargs)
                
                if error_counter and attempt == 0:
                    try:
                        collector = self._get_metrics_collector()
                        if collector is not None:
                            collector.increment_counter(f"{error_counter}.success")
                            logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "metrics.error_counter.success", "msg": f"[execute_with_retry] metrics 成功计数: {error_counter}.success"}, ensure_ascii=False))
                    except Exception as metrics_err:
                        logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "metrics.metrics_err", "msg": f"[execute_with_retry] metrics 记录失败: {metrics_err}"}, ensure_ascii=False))
                
                logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "func.func.__name__", "msg": f"[execute_with_retry] 执行成功: func={func.__name__}, result_type={type(result).__name__}"}, ensure_ascii=False))
                return result
            except Exception as e:
                logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "attempt.func.func", "msg": f"[execute_with_retry] 第 {attempt + 1} 次尝试失败: func={func.__name__}, "
                    f"exception_type={type(e).__name__}, exception_msg={str(e)[:100]}"}, ensure_ascii=False))
                # 判断是否应该重试
                should_retry = False
                
                # 1. 首先检查是否是 YunshuError 并且是可重试的
                if isinstance(e, YunshuError) and e.retryable:
                    should_retry = True
                    logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "yunshuerror.retryable", "msg": f"[execute_with_retry] YunshuError 可重试: retryable={e.retryable}"}, ensure_ascii=False))
                # 2. 然后检查是否是自定义可重试异常
                elif retryable and any(issubclass(e.__class__, cls) for cls in retryable):
                    should_retry = True
                    logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "type.__name__", "msg": f"[execute_with_retry] 匹配可重试异常类型: {type(e).__name__}"}, ensure_ascii=False))
                # 3. 最后检查重试策略的自定义规则（如果有）
                elif policy.retryable_exceptions or policy.custom_retry_condition:
                    if policy.should_retry(e, attempt):
                        should_retry = True
                        logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "policy.should_retry.true", "msg": f"[execute_with_retry] 策略判定可重试: policy.should_retry=True"}, ensure_ascii=False))
                
                # 如果不应该重试，立即抛出
                if not should_retry:
                    logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "func.func.__name__", "msg": f"[execute_with_retry] 异常不可重试，立即抛出: func={func.__name__}, "
                        f"exception_type={type(e).__name__}"}, ensure_ascii=False))
                    if error_counter:
                        try:
                            collector = self._get_metrics_collector()
                            if collector is not None:
                                collector.increment_counter(f"{error_counter}.failure")
                                logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "metrics.error_counter.failure", "msg": f"[execute_with_retry] metrics 失败计数: {error_counter}.failure"}, ensure_ascii=False))
                        except Exception as metrics_err:
                            logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "metrics.metrics_err", "msg": f"[execute_with_retry] metrics 记录失败: {metrics_err}"}, ensure_ascii=False))
                    raise self.record_error(e)
                
                if attempt >= policy.max_retries:
                    logger.error(
                        f"All {policy.max_retries} retry attempts exhausted",
                        exc_info=True,
                    )
                    raise self.record_error(e)
                
                if on_retry:
                    try:
                        on_retry(attempt + 1, e)
                    except Exception:
                        pass
                
                delay = policy.calculate_delay(attempt)
                logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "attempt.attempt.policy", "msg": f"Attempt {attempt + 1}/{policy.max_retries} failed, "
                    f"retrying in {delay:.2f}s: {e}"}, ensure_ascii=False))
                time.sleep(delay)

    def _format_metric(self, m, key: str) -> Dict[str, Any]:
        """格式化单个指标为 dict。

        注意：本方法不获取 self._lock，调用者必须已持有锁。
        之所以单独抽出，是因为 self._lock 是 threading.Lock（不可重入），
        若 get_metrics() 在锁内递归调用 self.get_metrics(key) 会死锁。
        """
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

    def get_metrics(self, key: Optional[str] = None) -> Dict[str, Any]:
        """获取错误指标"""
        with self._lock:
            if key:
                if key not in self._metrics:
                    return {}
                return self._format_metric(self._metrics[key], key)
            return {
                k: self._format_metric(m, k) for k, m in self._metrics.items()
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
    max_retries: Optional[int] = None,
    initial_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    strategy: str = "exponential",
    jitter_factor: float = 0.1,
    circuit_breaker: Optional[CircuitBreaker] = None,
    retryable_exceptions: Optional[Tuple[Type[Exception], ...]] = None,
    on_retry: Optional[Callable[[int, Exception], None]] = None,
    error_counter: Optional[str] = None,
    metrics_collector_factory: Optional[Callable[[], Any]] = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    装饰器：自动重试

    Args:
        metrics_collector_factory: 可选的 metrics collector 工厂（DI 模式）。
            装饰器内部会将 factory 同步到全局 ErrorHandler 实例，
            便于测试注入 mock collector 而无需 patch 延迟导入路径。

    用法:
        @with_retry(max_retries=3, initial_delay=1.0)
        def my_function():
            ...
    """
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            # 配置化：未显式指定时从 Config 读取默认值（支持热加载，每次调用读取最新值）
            _max_retries = max_retries
            if _max_retries is None:
                from agent.monitoring.observability_config import get_default_max_retries
                _max_retries = get_default_max_retries()
            logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "func.func.__name__", "msg": f"[with_retry] 开始执行函数: func={func.__name__}, max_retries={_max_retries}, "
                f"strategy={strategy}, initial_delay={initial_delay}, max_delay={max_delay}, "
                f"backoff_factor={backoff_factor}, jitter_factor={jitter_factor}"}, ensure_ascii=False))

            policy = RetryPolicy(
                max_retries=_max_retries,
                initial_delay=initial_delay,
                max_delay=max_delay,
                backoff_factor=backoff_factor,
                strategy=strategy,
                jitter_factor=jitter_factor,
                retryable_exceptions=retryable_exceptions,
            )
            handler = get_error_handler()
            # DI 同步：将装饰器传入的 factory 同步到全局 ErrorHandler 实例，
            # 这样 execute_with_retry 内部可通过 self._get_metrics_collector() 使用注入的 mock collector
            if metrics_collector_factory is not None:
                handler._metrics_collector_factory = metrics_collector_factory
            result = handler.execute_with_retry(
                func,
                retry_policy=policy,
                circuit_breaker=circuit_breaker,
                retryable_exceptions=retryable_exceptions,
                on_retry=on_retry,
                error_counter=error_counter,
                func_args=args,
                func_kwargs=kwargs,
            )

            logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "func.func.__name__", "msg": f"[with_retry] 函数执行成功: func={func.__name__}"}, ensure_ascii=False))
            return result
        return wrapper
    return decorator


def async_with_retry(
    max_retries: Optional[int] = None,
    initial_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    strategy: str = "exponential",
    jitter_factor: float = 0.1,
    circuit_breaker: Optional[CircuitBreaker] = None,
    retryable_exceptions: Optional[Tuple[Type[Exception], ...]] = None,
    on_retry: Optional[Callable[[int, Exception], None]] = None,
    error_counter: Optional[str] = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    装饰器：异步函数的自动重试
    
    用法:
        @async_with_retry(max_retries=3, initial_delay=1.0)
        async def my_function():
            ...
    """
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            # 配置化：未显式指定时从 Config 读取默认值（支持热加载，每次调用读取最新值）
            _max_retries = max_retries
            if _max_retries is None:
                from agent.monitoring.observability_config import get_default_max_retries
                _max_retries = get_default_max_retries()
            logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "func.func.__name__", "msg": f"[async_with_retry] 开始执行异步函数: func={func.__name__}, max_retries={_max_retries}, "
                f"strategy={strategy}, initial_delay={initial_delay}, max_delay={max_delay}, "
                f"backoff_factor={backoff_factor}, jitter_factor={jitter_factor}"}, ensure_ascii=False))

            policy = RetryPolicy(
                max_retries=_max_retries,
                initial_delay=initial_delay,
                max_delay=max_delay,
                backoff_factor=backoff_factor,
                strategy=strategy,
                jitter_factor=jitter_factor,
                retryable_exceptions=retryable_exceptions,
            )
            handler = get_error_handler()
            
            retryable = retryable_exceptions or (RecoverableError, YunshuError)
            
            for attempt in range(policy.max_retries + 1):
                logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "attempt.func.func", "msg": f"[async_with_retry] 执行第 {attempt + 1} 次尝试: func={func.__name__}"}, ensure_ascii=False))
                
                try:
                    if circuit_breaker:
                        logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "func.func.__name__", "msg": f"[async_with_retry] 通过熔断器执行: func={func.__name__}, "
                            f"circuit_breaker={circuit_breaker.name}"}, ensure_ascii=False))
                        result = await circuit_breaker.execute(func, *args, **kwargs)
                    else:
                        result = await func(*args, **kwargs)
                    
                    if error_counter and attempt == 0:
                        try:
                            from agent.monitoring.metrics import get_metrics_collector
                            collector = get_metrics_collector()
                            collector.increment_counter(f"{error_counter}.success")
                        except Exception:
                            pass
                    
                    logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "func.func.__name__", "msg": f"[async_with_retry] 异步函数执行成功: func={func.__name__}"}, ensure_ascii=False))
                    return result
                except Exception as e:
                    should_retry = False
                    
                    logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "func.func.__name__", "msg": f"[async_with_retry] 捕获异常: func={func.__name__}, attempt={attempt}, "
                        f"exception_type={type(e).__name__}, exception={e}"}, ensure_ascii=False))
                    
                    # 首先检查是否是 YunshuError 并明确设置了 retryable=False
                    if isinstance(e, YunshuError):
                        if e.retryable:
                            should_retry = True
                            logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "yunshuerror.retryable", "msg": f"[async_with_retry] YunshuError 标记为可重试: retryable={e.retryable}"}, ensure_ascii=False))
                    elif policy.should_retry(e, attempt):
                        should_retry = True
                        logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "log", "msg": f"[async_with_retry] 重试策略允许重试"}, ensure_ascii=False))
                    elif retryable and any(issubclass(e.__class__, cls) for cls in retryable):
                        should_retry = True
                        logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "log", "msg": f"[async_with_retry] 异常类型在可重试列表中"}, ensure_ascii=False))
                    
                    if not should_retry:
                        logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "func.func.__name__", "msg": f"[async_with_retry] 不允许重试，直接抛出异常: func={func.__name__}"}, ensure_ascii=False))
                        if error_counter:
                            try:
                                from agent.monitoring.metrics import get_metrics_collector
                                collector = get_metrics_collector()
                                collector.increment_counter(f"{error_counter}.failure")
                            except Exception:
                                pass
                        raise handler.record_error(e)
                    
                    if attempt >= policy.max_retries:
                        logger.error(
                            f"[async_with_retry] 所有 {policy.max_retries} 次重试已用尽: func={func.__name__}",
                            exc_info=True,
                        )
                        raise handler.record_error(e)
                    
                    if on_retry:
                        try:
                            on_retry(attempt + 1, e)
                        except Exception:
                            pass
                    
                    delay = policy.calculate_delay(attempt)
                    logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "attempt.policy.max_retries", "msg": f"[async_with_retry] 第 {attempt + 1}/{policy.max_retries} 次尝试失败, "
                        f"等待 {delay:.2f}s 后重试: {e}"}, ensure_ascii=False))
                    import asyncio
                    await asyncio.sleep(delay)
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
