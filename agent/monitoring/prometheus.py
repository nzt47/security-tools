"""
Prometheus 监控系统集成模块

提供将 V2 功能性能指标导出到 Prometheus 的功能。
集成了 SafeFileReader 指标。

合并自：
- agent/prometheus_exporter.py
- utils/prometheus_exporter.py
"""

import logging
import json
import uuid
import time
import threading
from typing import Optional, Callable, Any
from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


try:
    from prometheus_client import Counter, Histogram, Gauge, start_http_server, REGISTRY
    _PROMETHEUS_AVAILABLE = True
except ImportError:
    logger.warning(log_dict({'module_name': 'prometheus', 'action': 'prometheus_client.not.installed', 'msg': '[WARN] prometheus_client not installed, Prometheus export disabled'}))
    _PROMETHEUS_AVAILABLE = False

# error_handler 延迟导入（避免循环依赖：prometheus → error_handler → monitoring → prometheus）
# 各方法在使用时通过 from agent.error_handler import ... 延迟导入
_ERROR_HANDLER_AVAILABLE = True


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
        try:
            from agent.error_handler import get_error_handler
            self._error_handler = get_error_handler()
        except ImportError:
            self._error_handler = None

        # 配置化重试次数（支持热加载，每次初始化时读取最新值）
        try:
            from agent.monitoring.observability_config import get_prometheus_max_retries
            self._max_retries = get_prometheus_max_retries()
        except Exception:
            self._max_retries = 3

        self._exporter_circuit_breaker = None
        if _ERROR_HANDLER_AVAILABLE and self._error_handler:
            try:
                from agent.error_handler import CircuitBreaker
                self._exporter_circuit_breaker = CircuitBreaker(
                    max_failures=10, reset_timeout=60.0, name="prometheus-exporter"
                )
                self._error_handler.register_circuit_breaker(
                    "prometheus-exporter", self._exporter_circuit_breaker
                )
            except ImportError:
                pass

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

        # 会话指标（比 interaction 更高一级：一个会话包含多次交互）
        # [不易] status 标签对齐 alerts_production.yml 与 yunshu-alerts-monitor.json 的 {status="exception"} 过滤
        # 取值：success（正常完成）/ exception（异常终止）
        # PromQL: sum(rate(Yunshu_conversations_total{status="exception"}[5m])) / sum(rate(Yunshu_conversations_total[5m]))
        self.conversation_total = Counter(
            f"{namespace}_conversations_total",
            "Total number of conversations",
            ["status"]
        )

        # 活跃连接数（对齐 alerts_production.yml 的 HighActiveConnections 告警，阈值 100）
        # 【不易】无标签 Gauge，与告警规则 Yunshu_active_connections > 100 用法一致
        self.active_connections = Gauge(
            f"{namespace}_active_connections",
            "Number of active connections",
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

        # CI/CD 流水线指标（对齐 dashboard yunshu-full-monitoring.json）
        # [不易] 标签严格匹配 dashboard PromQL：by(stage)→[stage]、{{environment}}→[environment]、by(status)→[status]
        self.ci_pipeline_duration = Gauge(
            f"{namespace}_ci_pipeline_duration_seconds",
            "CI pipeline duration in seconds",
        )
        self.ci_test_coverage = Gauge(
            f"{namespace}_ci_test_coverage_percent",
            "CI test coverage percentage",
        )
        self.ci_test_failures = Counter(
            f"{namespace}_ci_test_failures_total",
            "Total CI test failures",
        )
        self.ci_build_failures = Counter(
            f"{namespace}_ci_build_failures_total",
            "Total CI build failures",
        )
        self.ci_pipeline_runs = Counter(
            f"{namespace}_ci_pipeline_runs_total",
            "Total CI pipeline runs",
            ["stage"],
        )

        # 部署与回滚指标
        # [不易] deployment_status 语义：0=Stable, 1=Deploying, 2=Rollback, 3=Failed（对齐 dashboard mappings）
        self.deployment_status = Gauge(
            f"{namespace}_deployment_status",
            "Deployment status (0=Stable, 1=Deploying, 2=Rollback, 3=Failed)",
            ["environment"],
        )
        self.deployment_duration = Gauge(
            f"{namespace}_deployment_duration_seconds",
            "Deployment duration in seconds",
            ["environment"],
        )
        self.deployment_failures = Counter(
            f"{namespace}_deployment_failures_total",
            "Total deployment failures",
        )
        self.deployment_total = Counter(
            f"{namespace}_deployment_total",
            "Total deployments",
            ["status"],
        )
        self.rollback_total = Counter(
            f"{namespace}_rollback_total",
            "Total rollbacks",
        )

        self._server_thread: Optional[threading.Thread] = None
        self._running = False

    def _safe_record_error(self, error: Exception, context: Optional[dict] = None):
        """安全地记录错误"""
        if not _ERROR_HANDLER_AVAILABLE or not self._error_handler:
            return
        try:
            from agent.error_handler import YunshuError, ErrorCategory
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
            logger.error(log_dict({'module_name': 'prometheus', 'action': 'error', 'msg': "[ERROR] Failed to record error: %s" % e}))

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
            logger.error(log_dict({'module_name': 'prometheus', 'action': 'error', 'msg': "[ERROR] Failed to update circuit breaker metrics: %s" % e}))

    def record_module_load(self, module_name: str, duration_ms: float, success: bool):
        """记录模块加载时间"""
        try:
            duration_sec = duration_ms / 1000.0
            self.v2_module_load_duration.labels(module=module_name).observe(duration_sec)
            self.v2_module_load_total.labels(
                module=module_name, status="success" if success else "failure"
            ).inc()
        except Exception as e:
            logger.error(log_dict({'module_name': 'prometheus', 'action': 'error', 'msg': "[ERROR] Failed to record module load: %s" % e}))
            self._safe_record_error(e, {"module_name": module_name})

    def set_module_enabled(self, module_name: str, enabled: bool):
        """设置模块启用状态"""
        try:
            self.v2_module_enabled.labels(module=module_name).set(1 if enabled else 0)
        except Exception as e:
            logger.error(log_dict({'module_name': 'prometheus', 'action': 'error', 'msg': "[ERROR] Failed to set module enabled: %s" % e}))
            self._safe_record_error(e, {"module_name": module_name})

    def record_interaction(self, duration_ms: float):
        """记录一次交互"""
        try:
            duration_sec = duration_ms / 1000.0
            self.interaction_total.inc()
            self.interaction_duration.observe(duration_sec)
        except Exception as e:
            logger.error(log_dict({'module_name': 'prometheus', 'action': 'error', 'msg': "[ERROR] Failed to record interaction: %s" % e}))
            self._safe_record_error(e)

    def record_conversation(self, status: str):
        """记录一次会话（按 status 分组）

        [不易] status 取值对齐 alerts_production.yml 的 {status="exception"} 过滤：
            - success: 正常完成的会话
            - exception: 异常终止的会话（触发 HighConversationErrorRate 告警）
        """
        try:
            self.conversation_total.labels(status=status).inc()
        except Exception as e:
            logger.error(log_dict({'module_name': 'prometheus', 'action': 'error', 'msg': "[ERROR] Failed to record conversation: %s" % e}))
            self._safe_record_error(e, {"conversation_status": status})

    def set_active_connections(self, count: int):
        """设置当前活跃连接数

        [不易] 对齐 alerts_production.yml 的 HighActiveConnections 告警（阈值 100）
        建议在 HTTP 中间件中调用：连接建立时 +1，连接断开时 -1，定期 set 当前值
        """
        try:
            self.active_connections.set(count)
        except Exception as e:
            logger.error(log_dict({'module_name': 'prometheus', 'action': 'error', 'msg': "[ERROR] Failed to set active connections: %s" % e}))
            self._safe_record_error(e)

    def set_memory_count(self, count: int):
        """设置记忆数量"""
        try:
            self.memory_count.set(count)
        except Exception as e:
            logger.error(log_dict({'module_name': 'prometheus', 'action': 'error', 'msg': "[ERROR] Failed to set memory count: %s" % e}))
            self._safe_record_error(e)

    def record_alert(self, level: str):
        """记录一次告警"""
        try:
            self.alert_total.labels(level=level).inc()
        except Exception as e:
            logger.error(log_dict({'module_name': 'prometheus', 'action': 'error', 'msg': "[ERROR] Failed to record alert: %s" % e}))
            self._safe_record_error(e, {"alert_level": level})

    # === CI/CD 指标记录方法 ===
    def set_ci_pipeline_duration(self, duration_seconds: float):
        """设置 CI 流水线耗时（秒）"""
        try:
            self.ci_pipeline_duration.set(duration_seconds)
        except Exception as e:
            logger.error(log_dict({'module_name': 'prometheus', 'action': 'error', 'msg': "[ERROR] Failed to set CI pipeline duration: %s" % e}))
            self._safe_record_error(e)

    def set_ci_test_coverage(self, coverage_percent: float):
        """设置测试覆盖率（百分比）"""
        try:
            self.ci_test_coverage.set(coverage_percent)
        except Exception as e:
            logger.error(log_dict({'module_name': 'prometheus', 'action': 'error', 'msg': "[ERROR] Failed to set CI test coverage: %s" % e}))
            self._safe_record_error(e)

    def record_ci_test_failure(self):
        """记录一次 CI 测试失败"""
        try:
            self.ci_test_failures.inc()
        except Exception as e:
            logger.error(log_dict({'module_name': 'prometheus', 'action': 'error', 'msg': "[ERROR] Failed to record CI test failure: %s" % e}))
            self._safe_record_error(e)

    def record_ci_build_failure(self):
        """记录一次 CI 构建失败"""
        try:
            self.ci_build_failures.inc()
        except Exception as e:
            logger.error(log_dict({'module_name': 'prometheus', 'action': 'error', 'msg': "[ERROR] Failed to record CI build failure: %s" % e}))
            self._safe_record_error(e)

    def record_ci_pipeline_run(self, stage: str):
        """记录一次 CI 流水线运行（按 stage 分组）"""
        try:
            self.ci_pipeline_runs.labels(stage=stage).inc()
        except Exception as e:
            logger.error(log_dict({'module_name': 'prometheus', 'action': 'error', 'msg': "[ERROR] Failed to record CI pipeline run: %s" % e}))
            self._safe_record_error(e)

    # === 部署与回滚指标记录方法 ===
    def set_deployment_status(self, environment: str, status: int):
        """设置部署状态（0=Stable, 1=Deploying, 2=Rollback, 3=Failed）"""
        try:
            self.deployment_status.labels(environment=environment).set(status)
        except Exception as e:
            logger.error(log_dict({'module_name': 'prometheus', 'action': 'error', 'msg': "[ERROR] Failed to set deployment status: %s" % e}))
            self._safe_record_error(e)

    def set_deployment_duration(self, environment: str, duration_seconds: float):
        """设置部署耗时（秒）"""
        try:
            self.deployment_duration.labels(environment=environment).set(duration_seconds)
        except Exception as e:
            logger.error(log_dict({'module_name': 'prometheus', 'action': 'error', 'msg': "[ERROR] Failed to set deployment duration: %s" % e}))
            self._safe_record_error(e)

    def record_deployment_failure(self):
        """记录一次部署失败"""
        try:
            self.deployment_failures.inc()
        except Exception as e:
            logger.error(log_dict({'module_name': 'prometheus', 'action': 'error', 'msg': "[ERROR] Failed to record deployment failure: %s" % e}))
            self._safe_record_error(e)

    def record_deployment(self, status: str):
        """记录一次部署（按 status 分组：success/failure/rollback）"""
        try:
            self.deployment_total.labels(status=status).inc()
        except Exception as e:
            logger.error(log_dict({'module_name': 'prometheus', 'action': 'error', 'msg': "[ERROR] Failed to record deployment: %s" % e}))
            self._safe_record_error(e)

    def record_rollback(self):
        """记录一次回滚"""
        try:
            self.rollback_total.inc()
        except Exception as e:
            logger.error(log_dict({'module_name': 'prometheus', 'action': 'error', 'msg': "[ERROR] Failed to record rollback: %s" % e}))
            self._safe_record_error(e)

    def start(self):
        """启动 Prometheus HTTP 服务器（带重试机制）"""
        if self._running:
            logger.warning(log_dict({'module_name': 'prometheus', 'action': 'prometheus.exporter.already', 'msg': '[WARN] Prometheus exporter already running'}))
            return

        def _start_server():
            try:
                start_http_server(self.port)
                logger.info(log_dict({'module_name': 'prometheus', 'action': 'ok', 'msg': "[OK] Prometheus exporter started on port %d" % self.port}))
                logger.info(log_dict({'module_name': 'prometheus', 'action': 'info', 'msg': "[INFO] Metrics available at http://localhost:%d/metrics" % self.port}))
            except Exception as e:
                logger.error(log_dict({'module_name': 'prometheus', 'action': 'error', 'msg': "[ERROR] Failed to start Prometheus exporter: %s" % e}))
                self._safe_record_error(e, {"operation": "start_http_server"})
                raise

        if _ERROR_HANDLER_AVAILABLE and self._error_handler:
            try:
                from agent.error_handler import RetryPolicy
                retry_policy = RetryPolicy(
                    max_retries=self._max_retries, initial_delay=1.0, max_delay=10.0, backoff_factor=2.0
                )
                self._error_handler.execute_with_retry(
                    _start_server, retry_policy=retry_policy,
                    circuit_breaker=self._exporter_circuit_breaker,
                    retryable_exceptions=(OSError,)
                )
            except Exception as e:
                logger.error(log_dict({'module_name': 'prometheus', 'action': 'error', 'msg': "[ERROR] Failed to start server after retries: %s" % e}))
                self._safe_record_error(e, {"operation": "start_server_with_retry"})
                raise
        else:
            _start_server()

        self._running = True

    def stop(self):
        """停止 Prometheus HTTP 服务器"""
        self._running = False
        self._update_circuit_breaker_metrics()
        logger.info(log_dict({'module_name': 'prometheus', 'action': 'prometheus.exporter.stopped', 'msg': '[INFO] Prometheus exporter stopped'}))

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
        retry_policy: "Optional[RetryPolicy]" = None,
        error_context: Optional[dict] = None, **kwargs
    ) -> Any:
        if not _ERROR_HANDLER_AVAILABLE or not self._error_handler:
            return func(*args, **kwargs)
        from agent.error_handler import RetryPolicy
        retry_pol = retry_policy or RetryPolicy(
            max_retries=self._max_retries, initial_delay=0.5, max_delay=30.0, backoff_factor=2.0
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
        logger.error(log_dict({'module_name': 'prometheus', 'action': 'error', 'msg': "[ERROR] Failed to initialize exporter from DigitalLife: %s" % e}))
        if _ERROR_HANDLER_AVAILABLE:
            exporter._safe_record_error(e, {"operation": "create_exporter_from_digital_life"})
    return exporter


class RetryablePrometheusOperation:
    """可重试的 Prometheus 操作封装器"""

    def __init__(self, exporter: PrometheusMetricsExporter, max_retries: Optional[int] = None, initial_delay: float = 1.0):
        self.exporter = exporter
        # 配置化重试次数（支持热加载，None 时从 Config 读取）
        if max_retries is None:
            try:
                from agent.monitoring.observability_config import get_prometheus_max_retries
                max_retries = get_prometheus_max_retries()
            except Exception:
                max_retries = 3
        self.max_retries = max_retries
        self.initial_delay = initial_delay

    def record_metric(self, operation_name: str, operation_func: Callable, *args, **kwargs):
        if _ERROR_HANDLER_AVAILABLE:
            from agent.error_handler import RetryPolicy
            self.exporter.execute_with_error_handling(
                lambda: (
                    operation_func(*args, **kwargs),
                    logger.info(log_dict({'module_name': 'prometheus', 'action': 'ok', 'msg': "[OK] %s completed" % operation_name}))
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


# ============================================================================
# 技能检索指标（供 HPA + Grafana dashboard 消费）
# [不易] HPA 的 histogram_quantile 和 rate() 需要 prometheus_client 原生指标
# BusinessMetricsCollector 的自管理字典不支持 prometheus_client 原生 histogram
# bucket，故在此独立定义，与 emit_metric 的 yunshu_skill_* 并存（向后兼容）
# [变易] buckets 覆盖 HPA P99 阈值 40ms 附近（压测报告 5000 技能 P99≈42ms）
# ============================================================================

skill_match_latency_ms = _safe_histogram(
    'skill_match_latency_ms',
    'Skill match latency in milliseconds (HPA P99 source)',
    ['layer', 'method', 'success'],
    # buckets 对齐 HPA 阈值 40ms：1/5/10/20/30/40/50/75/100/200/500/1000
    # 40ms 是 HPA 扩容触发点，需精确覆盖以支持 histogram_quantile(0.99)
    buckets=[1, 5, 10, 20, 30, 40, 50, 75, 100, 200, 500, 1000]
)

skill_match_count_total = _safe_counter(
    'skill_match_count_total',
    'Total skill match requests (Counter for HPA rate() / QPS calculation)',
    ['layer', 'method', 'success']
)


def record_skill_match_latency(layer: str, method: str, success: bool, duration_ms: float):
    """记录技能匹配延迟（prometheus_client 原生 Histogram，支持 histogram_quantile）

    HPA 通过 Prometheus Adapter 查询:
        histogram_quantile(0.99, sum(rate(skill_match_latency_ms_bucket[5m])) by (le))

    Args:
        layer: 架构层级（"1"=元数据层）
        method: 检索方法（tfidf / vector / rrf）
        success: 是否成功
        duration_ms: 延迟（毫秒）
    """
    skill_match_latency_ms.labels(
        layer=layer, method=method, success="true" if success else "false"
    ).observe(duration_ms)


def record_skill_match_count(layer: str, method: str, success: bool):
    """记录技能匹配请求计数（prometheus_client 原生 Counter，支持 rate() 计算 QPS）

    HPA 通过 Prometheus Adapter 查询:
        sum(rate(skill_match_count_total[1m]))

    Args:
        layer: 架构层级（"1"=元数据层）
        method: 检索方法（tfidf / vector / rrf）
        success: 是否成功
    """
    skill_match_count_total.labels(
        layer=layer, method=method, success="true" if success else "false"
    ).inc()


# ============================================================================
# 意图识别三层漏斗占比指标（任务5：三层占比统计）
# 参考 skill_match_count_total 模块级定义模式
# layer 取值: rule(规则层) / template(模板层) / semantic(语义层) / llm(大模型层) / reject(拒识)
# ============================================================================

yunshu_intent_layer_total = _safe_counter(
    'yunshu_intent_layer_total',
    'Intent routing layer hit counter (rule/template/semantic/llm/reject)',
    ['layer']
)

yunshu_intent_layer_ratio = _safe_gauge(
    'yunshu_intent_layer_ratio',
    'Intent routing layer hit ratio (rule/template/semantic/llm/reject)',
    ['layer']
)

# 【变易】模块级计数视图：维护各 layer 的相对计数，用于实时计算 ratio。
# Counter 是单调递增的累计值，无法重置；ratio 通过本 dict 的相对值计算。
_intent_layer_counts: dict = {}
# TD-3: 读改写非原子（get + += 多步），加锁保证高并发下计数准确（锁内仅内存操作，无 I/O）
_counts_lock = threading.Lock()


def record_intent_layer(layer: str):
    """记录意图识别各层命中次数（供三层占比统计）

    Grafana 可通过以下 PromQL 计算各层占比:
        sum by (layer) (rate(yunshu_intent_layer_total[5m]))
        / on() sum(rate(yunshu_intent_layer_total[5m]))

    Args:
        layer: 命中层级
            - "rule": 规则层(WorkflowEngine)命中
            - "template": 模板层(IntentRouter+ResponseTemplates)命中
            - "semantic": 语义层(SkillLoader RRF)命中
            - "semantic_failed": 语义层异常降级（TD-2，与 semantic 互斥）
            - "llm": 大模型层处理
            - "llm_error": LLM 调用失败（TD-1，llm 的失败子指标）
            - "reject": 未知意图拒识
    """
    yunshu_intent_layer_total.labels(layer=layer).inc()
    # 【变易】同步更新 ratio Gauge：维护各 layer 相对计数，实时计算占比。
    # ratio 总和始终 = 1.0（count/total 求和 = total/total），不超 100%。
    try:
        with _counts_lock:
            _intent_layer_counts[layer] = _intent_layer_counts.get(layer, 0) + 1
            total = sum(_intent_layer_counts.values())
            if total > 0:
                for _layer, _count in _intent_layer_counts.items():
                    yunshu_intent_layer_ratio.labels(layer=_layer).set(_count / total)
    except Exception:
        pass  # ratio 计算失败不影响 Counter 主链路


def reset_intent_layer_counts():
    """重置模块级 ratio 计数视图（仅清空 _intent_layer_counts，不影响 Counter）

    用于 mock 测试和诊断脚本隔离测试间状态。
    Counter 是 prometheus_client 进程级单调递增值，无法重置。
    """
    with _counts_lock:
        _intent_layer_counts.clear()


# ============================================================================
# ContextAssembler 组装指标（CEL 旁路注入，供持续观察）
# 依据集成验证总结报告 §6.1：
#   组装耗时实测均值 4.10ms / p95 4.40ms → buckets 以 2.5ms 为主刻度覆盖 P99
#   degraded 计数为降级告警源；injected_tokens 监控 budget 占用风险
# [变易] 沿用 _safe_* 条件注册模式：prometheus_client 不可用 / 重复注册时安全降级
# ============================================================================

context_assembler_injected_total = _safe_counter(
    'context_assembler_injected_total',
    'ContextAssembler 旁路注入成功次数（观察模式注入量）',
    []
)

context_assembler_degraded_total = _safe_counter(
    'context_assembler_degraded_total',
    'ContextAssembler 组装异常降级次数（告警源）',
    []
)

context_assembler_duration_ms = _safe_histogram(
    'context_assembler_duration_ms',
    'ContextAssembler 组装耗时（毫秒，P95/P99 监控）',
    [],
    # buckets 对齐实测分布：均值 4.1ms / p95 4.4ms，正常 < 10ms，异常阈值 50ms
    buckets=[1, 2.5, 5, 7.5, 10, 25, 50, 100],
)

context_assembler_injected_tokens = _safe_gauge(
    'context_assembler_injected_tokens',
    '最近一次注入的 token 数（budget 占用监控）',
    []
)


def record_context_assembler_injected(duration_ms: float, tokens: int):
    """记录一次成功的旁路注入（耗时 + 注入 token）"""
    context_assembler_injected_total.inc()
    context_assembler_duration_ms.observe(duration_ms)
    context_assembler_injected_tokens.set(tokens)


def record_context_assembler_degraded():
    """记录一次组装异常降级（主链路零影响，但需持续观察占比）"""
    context_assembler_degraded_total.inc()


def record_context_assembler_duration(duration_ms: float):
    """记录组装耗时（empty / degraded 路径也计时，反映真实开销）"""
    context_assembler_duration_ms.observe(duration_ms)

