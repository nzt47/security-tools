"""
Prometheus 监控系统集成模块

提供将 V2 功能性能指标导出到 Prometheus 的功能。
集成统一错误处理和自动重试机制。

使用方式：
    from agent.prometheus_exporter import PrometheusMetricsExporter
    
    exporter = PrometheusMetricsExporter()
    exporter.start()
"""

import logging
import time
import threading
from typing import Optional, Callable, Any

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter, Histogram, Gauge, start_http_server, REGISTRY
    _PROMETHEUS_AVAILABLE = True
except ImportError:
    logger.warning("[WARN] prometheus_client not installed, Prometheus export disabled")
    _PROMETHEUS_AVAILABLE = False


try:
    from agent.error_handler import (
        YunshuError,
        RecoverableError,
        TemporaryNetworkError,
        ExternalServiceError,
        DataInvalidError,
        ErrorSeverity,
        ErrorCategory,
        CircuitBreaker,
        RetryPolicy,
        ErrorHandler,
        get_error_handler,
        with_retry,
    )
    _ERROR_HANDLER_AVAILABLE = True
except ImportError:
    logger.warning("[WARN] error_handler module not available, error handling disabled")
    _ERROR_HANDLER_AVAILABLE = False


class PrometheusMetricsExporter:
    """Prometheus 指标导出器
    
    将 V2 功能的性能指标导出到 Prometheus，供监控系统采集。
    集成错误处理和自动重试机制。
    """
    
    def __init__(self, port: int = 8000, namespace: str = "Yunshu"):
        """
        初始化 Prometheus 导出器
        
        Args:
            port: HTTP 服务器端口
            namespace: 指标命名空间
        """
        if not _PROMETHEUS_AVAILABLE:
            raise RuntimeError("prometheus_client is not installed")
        
        self.port = port
        self.namespace = namespace
        
        # 初始化错误处理器
        self._error_handler = get_error_handler() if _ERROR_HANDLER_AVAILABLE else None
        
        # 初始化熔断器
        self._exporter_circuit_breaker = None
        if _ERROR_HANDLER_AVAILABLE:
            self._exporter_circuit_breaker = CircuitBreaker(
                max_failures=10,
                reset_timeout=60.0,
                name="prometheus-exporter"
            )
            self._error_handler.register_circuit_breaker(
                "prometheus-exporter",
                self._exporter_circuit_breaker
            )
        
        # 定义指标
        self.v2_module_load_duration = Histogram(
            f"{namespace}_v2_module_load_duration_seconds",
            "V2 module load duration in seconds",
            ["module"],
            buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
        )
        
        self.v2_module_load_total = Counter(
            f"{namespace}_v2_module_load_total",
            "Total V2 module load attempts",
            ["module", "status"]
        )
        
        self.v2_module_enabled = Gauge(
            f"{namespace}_v2_module_enabled",
            "V2 module enabled status (1=enabled, 0=disabled)",
            ["module"]
        )
        
        self.interaction_total = Counter(
            f"{namespace}_interaction_total",
            "Total number of interactions"
        )
        
        self.interaction_duration = Histogram(
            f"{namespace}_interaction_duration_duration_seconds",
            "Interaction processing duration in seconds",
            buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
        )
        
        self.memory_count = Gauge(
            f"{namespace}_memory_count",
            "Number of memories stored"
        )
        
        self.alert_total = Counter(
            f"{namespace}_alert_total",
            "Total number of security alerts",
            ["level"]
        )
        
        # 错误相关指标
        if _ERROR_HANDLER_AVAILABLE:
            self.error_total = Counter(
                f"{namespace}_error_total",
                "Total number of errors",
                ["severity", "category"]
            )
            
            self.error_retry_total = Counter(
                f"{namespace}_error_retry_total",
                "Total number of error retries",
                ["error_type"]
            )
            
            self.circuit_breaker_state = Gauge(
                f"{namespace}_circuit_breaker_state",
                "Circuit breaker state (0=closed, 1=open, 2=half_open)",
                ["name"]
            )
        
        self._server_thread: Optional[threading.Thread] = None
        self._running = False
    
    def _safe_record_error(self, error: Exception, context: Optional[dict] = None):
        """安全地记录错误（带错误处理保护）
        
        Args:
            error: 异常对象
            context: 错误上下文
        """
        if not _ERROR_HANDLER_AVAILABLE or not self._error_handler:
            return
        
        try:
            if isinstance(error, YunshuError):
                Yunshu_error = error
                if context:
                    Yunshu_error.context.update(context)
            else:
                Yunshu_error = YunshuError(
                    str(error),
                    category=ErrorCategory.UNKNOWN,
                    recoverable=False,
                    context=context or {}
                ).with_original(error)
            
            self._error_handler.record_error(Yunshu_error)
            
            # 更新 Prometheus 错误指标
            self.error_total.labels(
                severity=Yunshu_error.severity.value,
                category=Yunshu_error.category.value
            ).inc()
            
        except Exception as e:
            logger.error(f"[ERROR] Failed to record error: {e}")
    
    def _update_circuit_breaker_metrics(self):
        """更新熔断器指标"""
        if not _ERROR_HANDLER_AVAILABLE or not self._error_handler:
            return
        
        try:
            cb_status = self._error_handler.get_circuit_breaker_status()
            
            for name, status in cb_status.items():
                state_value = {
                    "closed": 0,
                    "open": 1,
                    "half_open": 2
                }.get(status["state"], 0)
                
                self.circuit_breaker_state.labels(name=name).set(state_value)
                
        except Exception as e:
            logger.error(f"[ERROR] Failed to update circuit breaker metrics: {e}")
    
    def record_module_load(self, module_name: str, duration_ms: float, success: bool):
        """记录模块加载时间
        
        Args:
            module_name: 模块名称 (lifetrace, persona, distillation)
            duration_ms: 加载耗时（毫秒）
            success: 是否成功
        """
        try:
            duration_sec = duration_ms / 1000.0
            self.v2_module_load_duration.labels(module=module_name).observe(duration_sec)
            self.v2_module_load_total.labels(
                module=module_name,
                status="success" if success else "failure"
            ).inc()
            
            logger.info(f"[METRIC] Module '{module_name}' load: {duration_ms:.2f}ms, success={success}")
            
        except Exception as e:
            logger.error(f"[ERROR] Failed to record module load: {e}")
            self._safe_record_error(e, {"module_name": module_name})
    
    def set_module_enabled(self, module_name: str, enabled: bool):
        """设置模块启用状态
        
        Args:
            module_name: 模块名称
            enabled: 是否启用
        """
        try:
            self.v2_module_enabled.labels(module=module_name).set(1 if enabled else 0)
            
        except Exception as e:
            logger.error(f"[ERROR] Failed to set module enabled: {e}")
            self._safe_record_error(e, {"module_name": module_name})
    
    def record_interaction(self, duration_ms: float):
        """记录一次交互
        
        Args:
            duration_ms: 交互处理耗时（毫秒）
        """
        try:
            duration_sec = duration_ms / 1000.0
            self.interaction_total.inc()
            self.interaction_duration.observe(duration_sec)
            
        except Exception as e:
            logger.error(f"[ERROR] Failed to record interaction: {e}")
            self._safe_record_error(e)
    
    def set_memory_count(self, count: int):
        """设置记忆数量
        
        Args:
            count: 当前记忆数量
        """
        try:
            self.memory_count.set(count)
            
        except Exception as e:
            logger.error(f"[ERROR] Failed to set memory count: {e}")
            self._safe_record_error(e)
    
    def record_alert(self, level: str):
        """记录一次告警
        
        Args:
            level: 告警级别 (critical, warning, safe)
        """
        try:
            self.alert_total.labels(level=level).inc()
            
        except Exception as e:
            logger.error(f"[ERROR] Failed to record alert: {e}")
            self._safe_record_error(e, {"alert_level": level})
    
    def start(self):
        """启动 Prometheus HTTP 服务器（带重试机制）"""
        if self._running:
            logger.warning("[WARN] Prometheus exporter already running")
            return
        
        def _start_server():
            """内部启动函数"""
            try:
                start_http_server(self.port)
                logger.info(f"[OK] Prometheus exporter started on port {self.port}")
                logger.info(f"[INFO] Metrics available at http://localhost:{self.port}/metrics")
            except Exception as e:
                logger.error(f"[ERROR] Failed to start Prometheus exporter: {e}")
                self._safe_record_error(e, {"operation": "start_http_server"})
                raise
        
        if _ERROR_HANDLER_AVAILABLE and self._error_handler:
            try:
                retry_policy = RetryPolicy(
                    max_retries=3,
                    initial_delay=1.0,
                    max_delay=10.0,
                    backoff_factor=2.0
                )
                
                self._error_handler.execute_with_retry(
                    _start_server,
                    retry_policy=retry_policy,
                    circuit_breaker=self._exporter_circuit_breaker,
                    retryable_exceptions=(OSError,)
                )
                
            except Exception as e:
                logger.error(f"[ERROR] Failed to start server after retries: {e}")
                self._safe_record_error(e, {"operation": "start_server_with_retry"})
                raise
        else:
            _start_server()
        
        self._running = True
    
    def stop(self):
        """停止 Prometheus HTTP 服务器"""
        self._running = False
        self._update_circuit_breaker_metrics()
        logger.info("[INFO] Prometheus exporter stopped")
    
    def __enter__(self):
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False
    
    def get_error_metrics(self) -> dict:
        """获取错误指标
        
        Returns:
            错误指标字典
        """
        if not _ERROR_HANDLER_AVAILABLE or not self._error_handler:
            return {}
        
        return self._error_handler.get_metrics()
    
    def get_circuit_breaker_status(self) -> dict:
        """获取熔断器状态
        
        Returns:
            熔断器状态字典
        """
        if not _ERROR_HANDLER_AVAILABLE or not self._error_handler:
            return {}
        
        return self._error_handler.get_circuit_breaker_status()
    
    def execute_with_error_handling(
        self,
        func: Callable,
        *args,
        retry_policy: Optional[RetryPolicy] = None,
        error_context: Optional[dict] = None,
        **kwargs
    ) -> Any:
        """使用错误处理执行函数
        
        Args:
            func: 要执行的函数
            retry_policy: 重试策略
            error_context: 错误上下文
            *args: 函数位置参数
            **kwargs: 函数关键字参数
            
        Returns:
            函数返回值
            
        Raises:
            原始异常或 YunshuError
        """
        if not _ERROR_HANDLER_AVAILABLE or not self._error_handler:
            return func(*args, **kwargs)
        
        retry_pol = retry_policy or RetryPolicy(
            max_retries=3,
            initial_delay=0.5,
            max_delay=30.0,
            backoff_factor=2.0
        )
        
        return self._error_handler.execute_with_retry(
            func,
            retry_policy=retry_pol,
            circuit_breaker=self._exporter_circuit_breaker,
            func_args=args,
            func_kwargs=kwargs,
        )


def create_exporter_from_digital_life(dl, port: int = 8000) -> PrometheusMetricsExporter:
    """从 DigitalLife 实例创建 Prometheus 导出器
    
    Args:
        dl: DigitalLife 实例
        port: HTTP 服务器端口
        
    Returns:
        PrometheusMetricsExporter 实例
    """
    exporter = PrometheusMetricsExporter(port=port)
    
    # 使用错误处理获取模块状态
    def _get_features():
        return dl.get_v2_features()
    
    def _get_memory_stats():
        return dl.get_memory_stats()
    
    try:
        if _ERROR_HANDLER_AVAILABLE:
            features = exporter.execute_with_error_handling(_get_features)
        else:
            features = _get_features()
        
        exporter.set_module_enabled("lifetrace", features.get("v2_lifetrace", False))
        exporter.set_module_enabled("persona", features.get("v2_persona", False))
        exporter.set_module_enabled("distillation", features.get("v2_distillation", False))
        
        if _ERROR_HANDLER_AVAILABLE:
            memory_stats = exporter.execute_with_error_handling(_get_memory_stats)
        else:
            memory_stats = _get_memory_stats()
        
        if memory_stats.get("available"):
            exporter.set_memory_count(memory_stats.get("total_memories", 0))
            
    except Exception as e:
        logger.error(f"[ERROR] Failed to initialize exporter from DigitalLife: {e}")
        if _ERROR_HANDLER_AVAILABLE:
            exporter._safe_record_error(e, {"operation": "create_exporter_from_digital_life"})
    
    return exporter


class RetryablePrometheusOperation:
    """可重试的 Prometheus 操作封装器"""
    
    def __init__(
        self,
        exporter: PrometheusMetricsExporter,
        max_retries: int = 3,
        initial_delay: float = 1.0
    ):
        """
        初始化操作封装器
        
        Args:
            exporter: Prometheus 导出器实例
            max_retries: 最大重试次数
            initial_delay: 初始延迟
        """
        self.exporter = exporter
        self.max_retries = max_retries
        self.initial_delay = initial_delay
    
    def record_metric(self, operation_name: str, operation_func: Callable, *args, **kwargs):
        """记录指标（带重试）
        
        Args:
            operation_name: 操作名称
            operation_func: 操作函数
            *args: 函数参数
            **kwargs: 函数关键字参数
        """
        if _ERROR_HANDLER_AVAILABLE:
            self.exporter.execute_with_error_handling(
                lambda: (
                    operation_func(*args, **kwargs),
                    logger.info(f"[OK] {operation_name} completed")
                )[0],
                retry_policy=RetryPolicy(
                    max_retries=self.max_retries,
                    initial_delay=self.initial_delay,
                    backoff_factor=2.0
                ),
                error_context={"operation": operation_name}
            )
        else:
            operation_func(*args, **kwargs)
