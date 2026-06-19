"""
Prometheus 指标导出器测试 - 覆盖指标格式转换、暴露接口
"""

import pytest
import threading
from unittest.mock import Mock, patch, MagicMock

from agent.prometheus_exporter import (
    _PROMETHEUS_AVAILABLE,
    _ERROR_HANDLER_AVAILABLE,
    PrometheusMetricsExporter,
    create_exporter_from_digital_life,
    RetryablePrometheusOperation,
)


class TestPrometheusAvailability:
    """测试 Prometheus 可用性检查"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_prometheus_available_flag(self):
        """测试 Prometheus 可用性标志"""
        assert isinstance(_PROMETHEUS_AVAILABLE, bool)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_error_handler_available_flag(self):
        """测试错误处理器可用性标志"""
        assert isinstance(_ERROR_HANDLER_AVAILABLE, bool)


@ pytest.mark.skipif(not _PROMETHEUS_AVAILABLE, reason="prometheus_client not installed")
class TestPrometheusMetricsExporterInit:
    """测试 Prometheus 导出器初始化"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_default_port(self):
        """测试默认端口初始化"""
        exporter = PrometheusMetricsExporter(namespace="TestDefault")
        assert exporter.port == 8000
        assert exporter.namespace == "TestDefault"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_custom_port(self):
        """测试自定义端口初始化"""
        exporter = PrometheusMetricsExporter(port=9090, namespace="TestCustom")
        assert exporter.port == 9090
        assert exporter.namespace == "TestCustom"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_metrics_created(self):
        """测试指标创建"""
        exporter = PrometheusMetricsExporter(namespace="TestMetrics")

        assert exporter.v2_module_load_duration is not None
        assert exporter.v2_module_load_total is not None
        assert exporter.v2_module_enabled is not None
        assert exporter.interaction_total is not None
        assert exporter.interaction_duration is not None
        assert exporter.memory_count is not None
        assert exporter.alert_total is not None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_not_running(self):
        """测试初始化时未运行"""
        exporter = PrometheusMetricsExporter(namespace="TestRunning")
        assert not exporter._running


@ pytest.mark.skipif(not _PROMETHEUS_AVAILABLE, reason="prometheus_client not installed")
class TestMetricsRecording:
    """测试指标记录"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_record_module_load(self):
        """测试记录模块加载"""
        exporter = PrometheusMetricsExporter(namespace="TestRecord1")

        exporter.record_module_load("test_module", 123.45, success=True)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_record_module_load_failure(self):
        """测试记录失败的模块加载"""
        exporter = PrometheusMetricsExporter(namespace="TestRecordFail")

        exporter.record_module_load("test_module", 50.0, success=False)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_record_interaction(self):
        """测试记录交互"""
        exporter = PrometheusMetricsExporter(namespace="TestRecord2")

        exporter.record_interaction(150.0)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_set_memory_count(self):
        """测试设置内存计数"""
        exporter = PrometheusMetricsExporter(namespace="TestRecord3")

        exporter.set_memory_count(100)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_record_alert(self):
        """测试记录告警"""
        exporter = PrometheusMetricsExporter(namespace="TestRecord4")

        exporter.record_alert("warning")
        exporter.record_alert("critical")
        exporter.record_alert("safe")


@ pytest.mark.skipif(not _PROMETHEUS_AVAILABLE, reason="prometheus_client not installed")
class TestCircuitBreakerMetrics:
    """测试熔断器指标"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_metrics_available(self):
        """测试熔断器指标可用"""
        exporter = PrometheusMetricsExporter(namespace="TestCB1")

        if _ERROR_HANDLER_AVAILABLE:
            assert exporter.circuit_breaker_state is not None
            assert exporter.error_total is not None
            assert exporter.error_retry_total is not None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_update_circuit_breaker_metrics(self):
        """测试更新熔断器指标"""
        exporter = PrometheusMetricsExporter(namespace="TestCB2")

        exporter._update_circuit_breaker_metrics()


@ pytest.mark.skipif(not _PROMETHEUS_AVAILABLE, reason="prometheus_client not installed")
class TestErrorRecording:
    """测试错误记录"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_safe_record_error_basic(self):
        """测试基本错误记录"""
        exporter = PrometheusMetricsExporter(namespace="TestErr1")

        exporter._safe_record_error(Exception("test error"))

    @pytest.mark.unit
    @pytest.mark.p0
    def test_safe_record_error_with_context(self):
        """测试带上下文的错误记录"""
        exporter = PrometheusMetricsExporter(namespace="TestErr2")

        exporter._safe_record_error(
            Exception("test error"),
            context={"key": "value"}
        )

    @pytest.mark.unit
    @pytest.mark.p1
    def test_get_error_metrics(self):
        """测试获取错误指标"""
        exporter = PrometheusMetricsExporter(namespace="TestErrMetrics")

        metrics = exporter.get_error_metrics()
        assert isinstance(metrics, dict)


@ pytest.mark.skipif(not _PROMETHEUS_AVAILABLE, reason="prometheus_client not installed")
class TestServerLifecycle:
    """测试服务器生命周期"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_server_not_running_initially(self):
        """测试服务器初始状态"""
        exporter = PrometheusMetricsExporter(namespace="TestServer1")
        assert not exporter._running

    @pytest.mark.unit
    @pytest.mark.p0
    def test_server_thread_none_initially(self):
        """测试服务器线程初始状态"""
        exporter = PrometheusMetricsExporter(namespace="TestServer2")
        assert exporter._server_thread is None

    @pytest.mark.unit
    @pytest.mark.p1
    def test_start_stop_server(self):
        """测试启动和停止服务器"""
        exporter = PrometheusMetricsExporter(port=9091, namespace="TestServerStart")

        try:
            exporter.start()
            assert exporter._running is True
        finally:
            exporter.stop()
            assert exporter._running is False

    @pytest.mark.unit
    @pytest.mark.p1
    def test_context_manager(self):
        """测试上下文管理器"""
        with PrometheusMetricsExporter(port=9092, namespace="TestContext") as exporter:
            assert exporter._running is True

        assert exporter._running is False

    @pytest.mark.unit
    @pytest.mark.p1
    def test_start_already_running(self):
        """测试启动已运行的服务器"""
        exporter = PrometheusMetricsExporter(port=9093, namespace="TestAlreadyRunning")

        try:
            exporter.start()
            exporter.start()
        finally:
            exporter.stop()


@ pytest.mark.skipif(not _PROMETHEUS_AVAILABLE, reason="prometheus_client not installed")
class TestSetModuleEnabled:
    """测试设置模块启用状态"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_set_module_enabled_true(self):
        """测试设置模块启用"""
        exporter = PrometheusMetricsExporter(namespace="TestModuleEnabled")

        exporter.set_module_enabled("test_module", True)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_set_module_enabled_false(self):
        """测试设置模块禁用"""
        exporter = PrometheusMetricsExporter(namespace="TestModuleDisabled")

        exporter.set_module_enabled("test_module", False)


@ pytest.mark.skipif(not _PROMETHEUS_AVAILABLE, reason="prometheus_client not installed")
class TestRetryablePrometheusOperation:
    """测试可重试的 Prometheus 操作"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init(self):
        """测试初始化"""
        exporter = PrometheusMetricsExporter(namespace="TestRetry")
        operation = RetryablePrometheusOperation(exporter, max_retries=3, initial_delay=1.0)

        assert operation.exporter is exporter
        assert operation.max_retries == 3
        assert operation.initial_delay == 1.0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_record_metric(self):
        """测试记录指标"""
        exporter = PrometheusMetricsExporter(namespace="TestRetryOp")
        operation = RetryablePrometheusOperation(exporter)

        def test_func():
            exporter.interaction_total.inc()

        operation.record_metric("test_operation", test_func)


@ pytest.mark.skipif(not _PROMETHEUS_AVAILABLE, reason="prometheus_client not installed")
class TestCreateExporterFromDigitalLife:
    """测试从 DigitalLife 创建导出器"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_create_exporter_from_digital_life(self):
        """测试从模拟的 DigitalLife 创建导出器"""
        mock_dl = Mock()
        mock_dl.get_v2_features.return_value = {
            "v2_lifetrace": True,
            "v2_persona": False,
            "v2_distillation": True
        }
        mock_dl.get_memory_stats.return_value = {
            "available": True,
            "total_memories": 150
        }

        exporter = create_exporter_from_digital_life(mock_dl, port=9094)
        assert exporter is not None
        assert isinstance(exporter, PrometheusMetricsExporter)


@ pytest.mark.skipif(not _PROMETHEUS_AVAILABLE, reason="prometheus_client not installed")
class TestCircuitBreakerStatus:
    """测试熔断器状态"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_get_circuit_breaker_status(self):
        """测试获取熔断器状态"""
        exporter = PrometheusMetricsExporter(namespace="TestCBStatus")

        status = exporter.get_circuit_breaker_status()
        assert isinstance(status, dict)


@ pytest.mark.skipif(_PROMETHEUS_AVAILABLE, reason="Testing behavior when prometheus not available")
class TestPrometheusNotAvailable:
    """测试 Prometheus 不可用时的行为"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_import_without_prometheus(self):
        """测试无 Prometheus 时的导入"""
        import agent.prometheus_exporter
        assert agent.prometheus_exporter is not None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_without_prometheus(self):
        """测试无 Prometheus 时初始化导出器"""
        with pytest.raises(RuntimeError):
            PrometheusMetricsExporter()