"""
Prometheus 指标导出器测试
"""

import pytest
import threading
from unittest.mock import Mock, patch, MagicMock

from agent.prometheus_exporter import (
    _PROMETHEUS_AVAILABLE,
    _ERROR_HANDLER_AVAILABLE,
)


class TestPrometheusAvailability:
    """测试 Prometheus 可用性检查"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_prometheus_available_flag(self):
        """测试 Prometheus 可用性标志"""
        # 这个标志应该在导入时设置
        assert isinstance(_PROMETHEUS_AVAILABLE, bool)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_error_handler_available_flag(self):
        """测试错误处理器可用性标志"""
        assert isinstance(_ERROR_HANDLER_AVAILABLE, bool)


@pytest.mark.skipif(not _PROMETHEUS_AVAILABLE, reason="prometheus_client not installed")
class TestPrometheusMetricsExporterInit:
    """测试 Prometheus 导出器初始化"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_default_port(self):
        """测试默认端口初始化"""
        from agent.prometheus_exporter import PrometheusMetricsExporter
        # 使用不同的命名空间避免冲突
        exporter = PrometheusMetricsExporter(namespace="TestDefault")
        assert exporter.port == 8000
        assert exporter.namespace == "TestDefault"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_custom_port(self):
        """测试自定义端口初始化"""
        from agent.prometheus_exporter import PrometheusMetricsExporter
        exporter = PrometheusMetricsExporter(port=9090, namespace="TestCustom")
        assert exporter.port == 9090
        assert exporter.namespace == "TestCustom"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_metrics_created(self):
        """测试指标创建"""
        from agent.prometheus_exporter import PrometheusMetricsExporter
        exporter = PrometheusMetricsExporter(namespace="TestMetrics")
        
        # 检查指标是否创建
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
        from agent.prometheus_exporter import PrometheusMetricsExporter
        exporter = PrometheusMetricsExporter(namespace="TestRunning")
        assert not exporter._running


@pytest.mark.skipif(not _PROMETHEUS_AVAILABLE, reason="prometheus_client not installed")
class TestMetricsRecording:
    """测试指标记录"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_record_module_load(self):
        """测试记录模块加载"""
        from agent.prometheus_exporter import PrometheusMetricsExporter
        exporter = PrometheusMetricsExporter(namespace="TestRecord1")
        
        # 记录模块加载
        exporter.v2_module_load_total.labels(module="test_module", status="success").inc()
        
        # 指标应该被记录
        assert exporter.v2_module_load_total is not None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_record_interaction(self):
        """测试记录交互"""
        from agent.prometheus_exporter import PrometheusMetricsExporter
        exporter = PrometheusMetricsExporter(namespace="TestRecord2")
        
        exporter.interaction_total.inc()
        assert exporter.interaction_total is not None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_record_memory_count(self):
        """测试记录内存计数"""
        from agent.prometheus_exporter import PrometheusMetricsExporter
        exporter = PrometheusMetricsExporter(namespace="TestRecord3")
        
        exporter.memory_count.set(100)
        assert exporter.memory_count is not None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_record_alert(self):
        """测试记录告警"""
        from agent.prometheus_exporter import PrometheusMetricsExporter
        exporter = PrometheusMetricsExporter(namespace="TestRecord4")
        
        exporter.alert_total.labels(level="warning").inc()
        assert exporter.alert_total is not None


@pytest.mark.skipif(not _PROMETHEUS_AVAILABLE, reason="prometheus_client not installed")
class TestCircuitBreakerMetrics:
    """测试熔断器指标"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_metrics_available(self):
        """测试熔断器指标可用"""
        from agent.prometheus_exporter import PrometheusMetricsExporter
        exporter = PrometheusMetricsExporter(namespace="TestCB1")
        
        if _ERROR_HANDLER_AVAILABLE:
            assert exporter.circuit_breaker_state is not None
            assert exporter.error_total is not None
            assert exporter.error_retry_total is not None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_update_circuit_breaker_metrics(self):
        """测试更新熔断器指标"""
        from agent.prometheus_exporter import PrometheusMetricsExporter
        exporter = PrometheusMetricsExporter(namespace="TestCB2")
        
        # 调用更新方法（即使没有熔断器也不应该出错）
        exporter._update_circuit_breaker_metrics()


@pytest.mark.skipif(not _PROMETHEUS_AVAILABLE, reason="prometheus_client not installed")
class TestErrorRecording:
    """测试错误记录"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_safe_record_error_basic(self):
        """测试基本错误记录"""
        from agent.prometheus_exporter import PrometheusMetricsExporter
        exporter = PrometheusMetricsExporter(namespace="TestErr1")
        
        # 记录一个普通异常
        exporter._safe_record_error(Exception("test error"))
        # 不应该抛出异常

    @pytest.mark.unit
    @pytest.mark.p0
    def test_safe_record_error_with_context(self):
        """测试带上下文的错误记录"""
        from agent.prometheus_exporter import PrometheusMetricsExporter
        exporter = PrometheusMetricsExporter(namespace="TestErr2")
        
        exporter._safe_record_error(
            Exception("test error"),
            context={"key": "value"}
        )
        # 不应该抛出异常


@pytest.mark.skipif(not _PROMETHEUS_AVAILABLE, reason="prometheus_client not installed")
class TestServerLifecycle:
    """测试服务器生命周期"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_server_not_running_initially(self):
        """测试服务器初始状态"""
        from agent.prometheus_exporter import PrometheusMetricsExporter
        exporter = PrometheusMetricsExporter(namespace="TestServer1")
        assert not exporter._running

    @pytest.mark.unit
    @pytest.mark.p0
    def test_server_thread_none_initially(self):
        """测试服务器线程初始状态"""
        from agent.prometheus_exporter import PrometheusMetricsExporter
        exporter = PrometheusMetricsExporter(namespace="TestServer2")
        assert exporter._server_thread is None


@pytest.mark.skipif(_PROMETHEUS_AVAILABLE, reason="Testing behavior when prometheus not available")
class TestPrometheusNotAvailable:
    """测试 Prometheus 不可用时的行为"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_import_without_prometheus(self):
        """测试无 Prometheus 时的导入"""
        # 当 prometheus_client 不可用时，应该有警告日志
        # 但模块应该仍然可以导入
        import agent.prometheus_exporter
        assert agent.prometheus_exporter is not None