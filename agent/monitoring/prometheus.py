"""
Prometheus 监控系统集成模块

提供将 V2 功能性能指标导出到 Prometheus 的功能。
集成了 SafeFileReader 指标。

合并自：
- agent/prometheus_exporter.py
- utils/prometheus_exporter.py
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
        if not _PROMETHEUS_AVAILABLE:
            raise RuntimeError("prometheus_client is not installed")

        self.port = port
        self.namespace = namespace
        self._error_handler = get_error_handler() if _ERROR_HANDLER_AVAILABLE else None

        self._exporter_circuit_breaker = None
        if _ERROR_HANDLER_AVAILABLE:
            self._exporter_circuit_breaker = CircuitBreaker(
                max_failures=10, reset_timeout=60.0, name="prometheus-exporter"
            )
            self._error_handler.register_circuit_breaker(
                "prometheus-exporter", self._exporter_circuit_breaker
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
        """安全地记录错误"""
        if not _ERROR_HANDLER_AVAILABLE or not self._error_handler:
            return
        try:
            if isinstance(error, YunshuError):
                yunshu_error = error
                if context:
                    yunshu_error.context.update(context)
            else:
                yunshu_error = YunshuError(
                    str(error),
                    category=ErrorCategory.UNKNOWN,
                    recoverable=False,
                    context=context or {}
                ).with_original(error)
            self._error_handler.record_error(yunshu_error)
            self.error_total.labels(
                severity=yunshu_error.severity.value,
                category=yunshu_error.category.value
            ).inc()
        except Exception as e:
            logger.error("[ERROR] Failed to record error: %s", e)

    def _update_circuit_breaker_metrics(self):
        """更新熔断器指标"""
        if not _ERROR_HANDLER_AVAILABLE or not self._error_handler:
            return
        try:
            cb_status = self._error_handler.get_circuit_breaker_status()
            for name, status in cb_status.items():
                state_value = {"closed": 0, "open": 1, "half_open": 2}.get(status["state"], 0)
                self.circuit_breaker_state.labels(name=name).set(state_value)
        except Exception as e:
            logger.error("[ERROR] Failed to update circuit breaker metrics: %s", e)

    def record_module_load(self, module_name: str, duration_ms: float, success: bool):
        """记录模块加载时间"""
        try:
            duration_sec = duration_ms / 1000.0
            self.v2_module_load_duration.labels(module=module_name).observe(duration_sec)
            self.v2_module_load_total.labels(
                module=module_name, status="success" if success else "failure"
            ).inc()
        except Exception as e:
            logger.error("[ERROR] Failed to record module load: %s", e)
            self._safe_record_error(e, {"module_name": module_name})

    def set_module_enabled(self, module_name: str, enabled: bool):
        """设置模块启用状态"""
        try:
            self.v2_module_enabled.labels(module=module_name).set(1 if enabled else 0)
        except Exception as e:
            logger.error("[ERROR] Failed to set module enabled: %s", e)
            self._safe_record_error(e, {"module_name": module_name})

    def record_interaction(self, duration_ms: float):
        """记录一次交互"""
        try:
            duration_sec = duration_ms / 1000.0
            self.interaction_total.inc()
            self.interaction_duration.observe(duration_sec)
        except Exception as e:
            logger.error("[ERROR] Failed to record interaction: %s", e)
            self._safe_record_error(e)

    def set_memory_count(self, count: int):
        """设置记忆数量"""
        try:
            self.memory_count.set(count)
        except Exception as e:
            logger.error("[ERROR] Failed to set memory count: %s", e)
            self._safe_record_error(e)

    def record_alert(self, level: str):
        """记录一次告警"""
        try:
            self.alert_total.labels(level=level).inc()
        except Exception as e:
            logger.error("[ERROR] Failed to record alert: %s", e)
            self._safe_record_error(e, {"alert_level": level})

    def start(self):
        """启动 Prometheus HTTP 服务器（带重试机制）"""
        if self._running:
            logger.warning("[WARN] Prometheus exporter already running")
            return

        def _start_server():
            try:
                start_http_server(self.port)
                logger.info("[OK] Prometheus exporter started on port %d", self.port)
                logger.info("[INFO] Metrics available at http://localhost:%d/metrics", self.port)
            except Exception as e:
                logger.error("[ERROR] Failed to start Prometheus exporter: %s", e)
                self._safe_record_error(e, {"operation": "start_http_server"})
                raise

        if _ERROR_HANDLER_AVAILABLE and self._error_handler:
            try:
                retry_policy = RetryPolicy(
                    max_retries=3, initial_delay=1.0, max_delay=10.0, backoff_factor=2.0
                )
                self._error_handler.execute_with_retry(
                    _start_server, retry_policy=retry_policy,
                    circuit_breaker=self._exporter_circuit_breaker,
                    retryable_exceptions=(OSError,)
                )
            except Exception as e:
                logger.error("[ERROR] Failed to start server after retries: %s", e)
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
        if not _ERROR_HANDLER_AVAILABLE or not self._error_handler:
            return {}
        return self._error_handler.get_metrics()

    def get_circuit_breaker_status(self) -> dict:
        if not _ERROR_HANDLER_AVAILABLE or not self._error_handler:
            return {}
        return self._error_handler.get_circuit_breaker_status()

    def execute_with_error_handling(
        self, func: Callable, *args,
        retry_policy: Optional[RetryPolicy] = None,
        error_context: Optional[dict] = None, **kwargs
    ) -> Any:
        if not _ERROR_HANDLER_AVAILABLE or not self._error_handler:
            return func(*args, **kwargs)
        retry_pol = retry_policy or RetryPolicy(
            max_retries=3, initial_delay=0.5, max_delay=30.0, backoff_factor=2.0
        )
        return self._error_handler.execute_with_retry(
            func, retry_policy=retry_pol,
            circuit_breaker=self._exporter_circuit_breaker,
            func_args=args, func_kwargs=kwargs,
        )


def create_exporter_from_digital_life(dl, port: int = 8000) -> PrometheusMetricsExporter:
    """从 DigitalLife 实例创建 Prometheus 导出器"""
    exporter = PrometheusMetricsExporter(port=port)

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
        logger.error("[ERROR] Failed to initialize exporter from DigitalLife: %s", e)
        if _ERROR_HANDLER_AVAILABLE:
            exporter._safe_record_error(e, {"operation": "create_exporter_from_digital_life"})
    return exporter


class RetryablePrometheusOperation:
    """可重试的 Prometheus 操作封装器"""

    def __init__(self, exporter: PrometheusMetricsExporter, max_retries: int = 3, initial_delay: float = 1.0):
        self.exporter = exporter
        self.max_retries = max_retries
        self.initial_delay = initial_delay

    def record_metric(self, operation_name: str, operation_func: Callable, *args, **kwargs):
        if _ERROR_HANDLER_AVAILABLE:
            self.exporter.execute_with_error_handling(
                lambda: (
                    operation_func(*args, **kwargs),
                    logger.info("[OK] %s completed", operation_name)
                )[0],
                retry_policy=RetryPolicy(
                    max_retries=self.max_retries, initial_delay=self.initial_delay, backoff_factor=2.0
                ),
                error_context={"operation": operation_name}
            )
        else:
            operation_func(*args, **kwargs)


# ============================================================================
# SafeFileReader Prometheus 指标
# ============================================================================

# 降级实现：当 prometheus_client 不可用时使用 noop 对象，避免 NameError
class _NoopMetric:
    """prometheus_client 不可用时的 noop 降级基类"""
    def __init__(self, *args, **kwargs):
        pass
    def labels(self, *args, **kwargs):
        return self

class _NoopCounter(_NoopMetric):
    """Counter 降级实现"""
    def inc(self, *args, **kwargs):
        pass

class _NoopHistogram(_NoopMetric):
    """Histogram 降级实现"""
    def observe(self, *args, **kwargs):
        pass

class _NoopGauge(_NoopMetric):
    """Gauge 降级实现"""
    def set(self, *args, **kwargs):
        pass

def _safe_counter(name, doc, labels):
    # 降级处理：prometheus_client 不可用时返回 noop 对象
    if not _PROMETHEUS_AVAILABLE:
        return _NoopCounter()
    try:
        return Counter(name, doc, labels)
    except ValueError:
        from prometheus_client import REGISTRY as _R
        base = name[:-6] if name.endswith('_total') else name
        return _R._names_to_collectors[base]

def _safe_histogram(name, doc, labels, buckets=None):
    # 降级处理：prometheus_client 不可用时返回 noop 对象
    if not _PROMETHEUS_AVAILABLE:
        return _NoopHistogram()
    kwargs = {"buckets": buckets} if buckets else {}
    try:
        return Histogram(name, doc, labels, **kwargs)
    except ValueError:
        from prometheus_client import REGISTRY as _R
        return _R._names_to_collectors[name]

yunshu_safe_file_reader_errors_total = _safe_counter(
    'yunshu_safe_file_reader_errors_total',
    'SafeFileReader 错误总数',
    ['error_type', 'file_path']
)

yunshu_safe_file_reader_encoding_fallbacks_total = _safe_counter(
    'yunshu_safe_file_reader_encoding_fallbacks_total',
    'SafeFileReader 编码降级次数',
    ['file_path']
)

yunshu_safe_file_reader_read_duration_seconds = _safe_histogram(
    'yunshu_safe_file_reader_read_duration_seconds',
    'SafeFileReader 读取耗时',
    ['file_path'],
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0],
)

def _safe_gauge(name, doc, labels):
    # 降级处理：prometheus_client 不可用时返回 noop 对象，避免 NameError 崩溃
    if not _PROMETHEUS_AVAILABLE:
        return _NoopGauge()
    try:
        return Gauge(name, doc, labels)
    except ValueError:
        from prometheus_client import REGISTRY as _R
        return _R._names_to_collectors[name]

yunshu_safe_file_reader_loaded_history_count = _safe_gauge(
    'yunshu_safe_file_reader_loaded_history_count',
    'SafeFileReader 加载的历史对话数',
    ['file_path']
)

yunshu_safe_file_reader_invalid_ratio = _safe_gauge(
    'yunshu_safe_file_reader_invalid_ratio',
    'SafeFileReader 无效行比例',
    ['file_path']
)


def record_error(error_type, file_path):
    """记录错误"""
    yunshu_safe_file_reader_errors_total.labels(error_type=error_type, file_path=file_path).inc()


def record_encoding_fallback(file_path):
    """记录编码降级"""
    yunshu_safe_file_reader_encoding_fallbacks_total.labels(file_path=file_path).inc()


def record_read_duration(file_path, duration):
    """记录读取耗时"""
    yunshu_safe_file_reader_read_duration_seconds.labels(file_path=file_path).observe(duration)


def set_loaded_history_count(file_path, count):
    """设置加载的历史对话数"""
    yunshu_safe_file_reader_loaded_history_count.labels(file_path=file_path).set(count)


def set_invalid_ratio(file_path, ratio):
    """设置无效行比例"""
    yunshu_safe_file_reader_invalid_ratio.labels(file_path=file_path).set(ratio)
