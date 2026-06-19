import pytest
from unittest.mock import patch, MagicMock, call
import asyncio

from agent.monitoring.decorators import (
    monitor_latency,
    monitor_counter,
    monitor_both,
    trace_operation,
    monitored,
    handle_errors,
    catch_and_report,
    safe_call,
    async_handle_errors,
)
from agent.error_handler import ErrorCategory, ErrorSeverity


class TestMonitorLatency:
    """测试延迟监控装饰器"""

    @patch("agent.monitoring.decorators.get_metrics_collector")
    def test_monitor_latency_success(self, mock_collector):
        """测试成功执行时记录延迟"""
        mock_collector.return_value = MagicMock()
        
        @monitor_latency("latency.test")
        def test_func():
            return "success"
        
        result = test_func()
        assert result == "success"
        mock_collector.return_value.record_latency.assert_called_once()

    @patch("agent.monitoring.decorators.get_metrics_collector")
    def test_monitor_latency_exception(self, mock_collector):
        """测试异常时记录延迟"""
        mock_collector.return_value = MagicMock()
        
        @monitor_latency("latency.test")
        def test_func():
            raise ValueError("test error")
        
        with pytest.raises(ValueError):
            test_func()
        mock_collector.return_value.record_latency.assert_called_once()

    def test_monitor_latency_attributes(self):
        """测试装饰器添加的属性"""
        def test_func():
            return "test"
        
        wrapped = monitor_latency("latency.test")(test_func)
        assert hasattr(wrapped, '_metric_name')
        assert wrapped._metric_name == "latency.test"
        assert wrapped.__wrapped__ is test_func


class TestMonitorCounter:
    """测试计数器监控装饰器"""

    @patch("agent.monitoring.decorators.get_metrics_collector")
    def test_monitor_counter_success(self, mock_collector):
        """测试成功执行时增加计数器"""
        mock_collector.return_value = MagicMock()
        
        @monitor_counter("count.test")
        def test_func():
            return "success"
        
        result = test_func()
        assert result == "success"
        mock_collector.return_value.increment_counter.assert_called_once_with("count.test")

    @patch("agent.monitoring.decorators.get_metrics_collector")
    def test_monitor_counter_exception(self, mock_collector):
        """测试异常时不增加计数器"""
        mock_collector.return_value = MagicMock()
        
        @monitor_counter("count.test")
        def test_func():
            raise ValueError("test error")
        
        with pytest.raises(ValueError):
            test_func()
        mock_collector.return_value.increment_counter.assert_not_called()


class TestMonitorBoth:
    """测试双重监控装饰器"""

    @patch("agent.monitoring.decorators.get_metrics_collector")
    def test_monitor_both_success(self, mock_collector):
        """测试成功执行时记录延迟和增加计数器"""
        mock_collector.return_value = MagicMock()
        
        @monitor_both("latency.test", "count.test")
        def test_func():
            return "success"
        
        result = test_func()
        assert result == "success"
        mock_collector.return_value.increment_counter.assert_called_once_with("count.test")
        mock_collector.return_value.record_latency.assert_called_once()

    @patch("agent.monitoring.decorators.get_metrics_collector")
    def test_monitor_both_exception(self, mock_collector):
        """测试异常时仍记录延迟"""
        mock_collector.return_value = MagicMock()
        
        @monitor_both("latency.test", "count.test")
        def test_func():
            raise ValueError("test error")
        
        with pytest.raises(ValueError):
            test_func()
        # 计数器不增加，但延迟仍记录
        mock_collector.return_value.increment_counter.assert_not_called()
        mock_collector.return_value.record_latency.assert_called_once()


class TestTraceOperation:
    """测试追踪装饰器"""

    @patch("agent.monitoring.decorators.TraceContext")
    def test_trace_operation(self, mock_context):
        """测试追踪装饰器"""
        mock_ctx = MagicMock()
        mock_context.return_value.__enter__ = MagicMock(return_value=None)
        mock_context.return_value.__exit__ = MagicMock(return_value=False)
        
        @trace_operation("service", "operation")
        def test_func():
            return "success"
        
        result = test_func()
        assert result == "success"
        mock_context.assert_called_once_with("service", "operation")

    def test_trace_operation_attributes(self):
        """测试装饰器添加的属性"""
        def test_func():
            return "test"
        
        wrapped = trace_operation("service", "operation")(test_func)
        assert hasattr(wrapped, '_service')
        assert wrapped._service == "service"
        assert hasattr(wrapped, '_operation')
        assert wrapped._operation == "operation"


class TestMonitored:
    """测试综合监控装饰器"""

    @patch("agent.monitoring.decorators.get_metrics_collector")
    @patch("agent.monitoring.decorators.get_trace_id")
    @patch("agent.monitoring.decorators.TraceContext")
    def test_monitored_all_features(self, mock_context, mock_trace_id, mock_collector):
        """测试启用所有功能"""
        mock_trace_id.return_value = "trace123"
        mock_collector.return_value = MagicMock()
        mock_ctx = MagicMock()
        mock_context.return_value.__enter__ = MagicMock(return_value=None)
        mock_context.return_value.__exit__ = MagicMock(return_value=False)
        
        @monitored(
            metric_name="latency.test",
            counter_name="count.test",
            service="service",
            operation="operation"
        )
        def test_func():
            return "success"
        
        result = test_func()
        assert result == "success"
        mock_collector.return_value.increment_counter.assert_called_once_with("count.test")
        mock_collector.return_value.record_latency.assert_called_once()
        mock_context.assert_called_once_with("service", "operation")

    @patch("agent.monitoring.decorators.get_metrics_collector")
    def test_monitored_only_metric(self, mock_collector):
        """测试只监控延迟"""
        mock_collector.return_value = MagicMock()
        
        @monitored(metric_name="latency.test")
        def test_func():
            return "success"
        
        test_func()
        mock_collector.return_value.record_latency.assert_called_once()

    @patch("agent.monitoring.decorators.get_metrics_collector")
    def test_monitored_only_counter(self, mock_collector):
        """测试只监控计数"""
        mock_collector.return_value = MagicMock()
        
        @monitored(counter_name="count.test")
        def test_func():
            return "success"
        
        test_func()
        mock_collector.return_value.increment_counter.assert_called_once_with("count.test")


class TestHandleErrors:
    """测试错误处理装饰器"""

    @patch("agent.monitoring.decorators.get_trace_id")
    def test_handle_errors_success(self, mock_trace_id):
        """测试成功执行"""
        mock_trace_id.return_value = "trace123"
        
        @handle_errors()
        def test_func():
            return "success"
        
        result = test_func()
        assert result == "success"

    @patch("agent.monitoring.decorators.get_trace_id")
    @patch("agent.monitoring.decorators.logger")
    def test_handle_errors_exception(self, mock_logger, mock_trace_id):
        """测试异常处理"""
        mock_trace_id.return_value = "trace123"
        
        @handle_errors(log_error=True)
        def test_func():
            raise ValueError("test error")
        
        with pytest.raises(ValueError):
            test_func()
        mock_logger.error.assert_called_once()

    @patch("agent.monitoring.decorators.get_trace_id")
    def test_handle_errors_return_on_error(self, mock_trace_id):
        """测试返回默认值"""
        mock_trace_id.return_value = "trace123"
        
        @handle_errors(return_on_error="fallback")
        def test_func():
            raise ValueError("test error")
        
        result = test_func()
        assert result == "fallback"

    @patch("agent.monitoring.decorators.get_trace_id")
    @patch("agent.monitoring.decorators.time")
    def test_handle_errors_retry(self, mock_time, mock_trace_id):
        """测试自动重试"""
        mock_trace_id.return_value = "trace123"
        mock_time.sleep = MagicMock()
        
        call_count = [0]
        
        @handle_errors(retry_on_error=True, max_retries=2, retry_delay=0.1)
        def test_func():
            call_count[0] += 1
            if call_count[0] < 3:
                raise ValueError("retry error")
            return "success"
        
        result = test_func()
        assert result == "success"
        assert call_count[0] == 3
        assert mock_time.sleep.call_count == 2

    @patch("agent.monitoring.decorators.get_trace_id")
    def test_handle_errors_ignored_exceptions(self, mock_trace_id):
        """测试忽略的异常类型"""
        mock_trace_id.return_value = "trace123"
        
        @handle_errors(ignored_exceptions=(ValueError,))
        def test_func():
            raise ValueError("ignored")
        
        with pytest.raises(ValueError):
            test_func()


class TestCatchAndReport:
    """测试捕获上报装饰器"""

    @patch("agent.monitoring.decorators.get_trace_id")
    @patch("agent.monitoring.decorators.get_error_reporter")
    def test_catch_and_report(self, mock_reporter, mock_trace_id):
        """测试捕获异常并上报"""
        mock_trace_id.return_value = "trace123"
        mock_reporter.return_value = MagicMock()
        
        @catch_and_report(ValueError)
        def test_func():
            raise ValueError("test error")
        
        with pytest.raises(ValueError):
            test_func()
        mock_reporter.return_value.report_error.assert_called_once()

    @patch("agent.monitoring.decorators.get_trace_id")
    @patch("agent.monitoring.decorators.get_error_reporter")
    def test_catch_and_report_other_exception(self, mock_reporter, mock_trace_id):
        """测试不捕获其他类型异常"""
        mock_trace_id.return_value = "trace123"
        mock_reporter.return_value = MagicMock()
        
        @catch_and_report(ValueError)
        def test_func():
            raise TypeError("other error")
        
        with pytest.raises(TypeError):
            test_func()
        mock_reporter.return_value.report_error.assert_not_called()


class TestSafeCall:
    """测试安全调用装饰器"""

    @patch("agent.monitoring.decorators.get_trace_id")
    @patch("agent.monitoring.decorators.logger")
    def test_safe_call_success(self, mock_logger, mock_trace_id):
        """测试成功执行"""
        mock_trace_id.return_value = "trace123"
        
        @safe_call(default_return="fallback")
        def test_func():
            return "success"
        
        result = test_func()
        assert result == "success"
        mock_logger.error.assert_not_called()

    @patch("agent.monitoring.decorators.get_trace_id")
    @patch("agent.monitoring.decorators.logger")
    def test_safe_call_exception(self, mock_logger, mock_trace_id):
        """测试异常时返回默认值"""
        mock_trace_id.return_value = "trace123"
        
        @safe_call(default_return="fallback")
        def test_func():
            raise ValueError("test error")
        
        result = test_func()
        assert result == "fallback"
        mock_logger.error.assert_called_once()

    @patch("agent.monitoring.decorators.get_trace_id")
    def test_safe_call_no_log(self, mock_trace_id):
        """测试不记录日志"""
        mock_trace_id.return_value = "trace123"
        
        @safe_call(default_return="fallback", log_errors=False)
        def test_func():
            raise ValueError("test error")
        
        result = test_func()
        assert result == "fallback"


class TestAsyncHandleErrors:
    """测试异步错误处理装饰器"""

    @patch("agent.monitoring.decorators.get_trace_id")
    @patch("agent.monitoring.decorators.logger")
    @pytest.mark.asyncio
    async def test_async_handle_errors_success(self, mock_logger, mock_trace_id):
        """测试异步函数成功执行"""
        mock_trace_id.return_value = "trace123"
        
        @async_handle_errors()
        async def test_func():
            return "success"
        
        result = await test_func()
        assert result == "success"

    @patch("agent.monitoring.decorators.get_trace_id")
    @patch("agent.monitoring.decorators.logger")
    @pytest.mark.asyncio
    async def test_async_handle_errors_exception(self, mock_logger, mock_trace_id):
        """测试异步函数异常"""
        mock_trace_id.return_value = "trace123"
        
        @async_handle_errors(return_on_error="fallback")
        async def test_func():
            raise ValueError("test error")
        
        result = await test_func()
        assert result == "fallback"

    @patch("agent.monitoring.decorators.get_trace_id")
    @pytest.mark.asyncio
    async def test_async_handle_errors_retry(self, mock_trace_id):
        """测试异步函数重试"""
        mock_trace_id.return_value = "trace123"
        call_count = [0]
        
        @async_handle_errors(retry_on_error=True, max_retries=2, retry_delay=0.01)
        async def test_func():
            call_count[0] += 1
            if call_count[0] < 3:
                raise ValueError("retry error")
            return "success"
        
        result = await test_func()
        assert result == "success"
        assert call_count[0] == 3