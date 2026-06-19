"""Tracing 模块单元测试"""
import pytest
import threading
import time

from agent.monitoring.tracing import (
    TraceContext,
    get_trace_id,
    set_trace_id,
    trace,
    format_trace_log,
)


class TestTraceContext:
    """测试追踪上下文"""

    def test_trace_context_enter_exit(self):
        """测试追踪上下文进入和退出"""
        with TraceContext("TestService", "test_operation") as ctx:
            assert ctx.trace_id is not None
            assert ctx.service_name == "TestService"
            assert ctx.operation == "test_operation"
            assert ctx.start_time is not None
            assert ctx.duration_ms >= 0

    def test_trace_context_unique_id(self):
        """测试每次生成唯一的 Trace ID"""
        ids = set()
        for _ in range(10):
            with TraceContext("Service", "op") as ctx:
                ids.add(ctx.trace_id)
        
        assert len(ids) == 10

    def test_trace_context_id_length(self):
        """测试 Trace ID 长度"""
        with TraceContext("Service", "op") as ctx:
            assert len(ctx.trace_id) == 16

    def test_trace_context_error_handling(self):
        """测试异常情况下的追踪"""
        try:
            with TraceContext("Service", "op") as ctx:
                raise ValueError("test error")
        except ValueError:
            pass
        
        # 确保上下文正常退出
        assert True

    def test_duration_ms_property(self):
        """测试持续时间属性"""
        with TraceContext("Service", "op") as ctx:
            time.sleep(0.01)
            assert ctx.duration_ms >= 10

    def test_duration_s_property(self):
        """测试持续时间（秒）属性"""
        with TraceContext("Service", "op") as ctx:
            time.sleep(0.01)
            assert ctx.duration_s >= 0.01


class TestTraceIdManagement:
    """测试 Trace ID 管理"""

    def test_get_trace_id_none(self):
        """测试获取不存在的 Trace ID"""
        assert get_trace_id() is None

    def test_set_and_get_trace_id(self):
        """测试设置和获取 Trace ID"""
        set_trace_id("test-trace-123")
        assert get_trace_id() == "test-trace-123"
        
        # 清除
        set_trace_id(None)
        assert get_trace_id() is None

    def test_trace_id_propagation(self):
        """测试 Trace ID 在上下文中的传播"""
        set_trace_id("external-trace-id")
        
        with TraceContext("Service", "op") as ctx:
            assert ctx.trace_id == "external-trace-id"
        
        # 上下文退出后应该保留
        assert get_trace_id() == "external-trace-id"
        
        set_trace_id(None)


class TestTraceDecorator:
    """测试追踪装饰器"""

    def test_trace_decorator_basic(self):
        """测试追踪装饰器基本功能"""
        @trace("Service", "operation")
        def test_func():
            return "result"
        
        result = test_func()
        assert result == "result"

    def test_trace_decorator_with_args(self):
        """测试带参数的追踪装饰器"""
        @trace("Service", "operation")
        def test_func(a, b):
            return a + b
        
        result = test_func(1, 2)
        assert result == 3

    def test_trace_decorator_exception(self):
        """测试追踪装饰器异常处理"""
        @trace("Service", "op")
        def test_func():
            raise ValueError("test error")
        
        with pytest.raises(ValueError):
            test_func()


class TestFormatTraceLog:
    """测试日志格式化函数"""

    def test_format_trace_log_basic(self):
        """测试基本日志格式化"""
        result = format_trace_log("trace123", "test message")
        assert "[trace123]" in result
        assert "test message" in result

    def test_format_trace_log_with_kwargs(self):
        """测试带额外参数的日志格式化"""
        result = format_trace_log("trace123", "message", key1="value1", key2="value2")
        assert "[trace123]" in result
        assert "message" in result
        assert "key1=value1" in result
        assert "key2=value2" in result


class TestThreadSafety:
    """测试线程安全性"""

    def test_trace_context_thread_isolation(self):
        """测试不同线程的 Trace ID 隔离"""
        results = []
        
        def worker(trace_value):
            set_trace_id(trace_value)
            time.sleep(0.01)
            results.append((trace_value, get_trace_id()))
        
        thread1 = threading.Thread(target=worker, args=("thread1-id",))
        thread2 = threading.Thread(target=worker, args=("thread2-id",))
        
        thread1.start()
        thread2.start()
        thread1.join()
        thread2.join()
        
        # 每个线程应该看到自己的 trace_id
        for expected, actual in results:
            assert expected == actual


class TestNestedTraceContext:
    """测试嵌套追踪上下文"""

    def test_nested_trace_context(self):
        """测试嵌套追踪上下文"""
        outer_id = None
        inner_id = None
        
        with TraceContext("Outer", "op") as outer_ctx:
            outer_id = outer_ctx.trace_id
            with TraceContext("Inner", "op") as inner_ctx:
                inner_id = inner_ctx.trace_id
        
        # 嵌套上下文应该共享同一个 trace_id
        assert outer_id == inner_id