#!/usr/bin/env python3
"""
追踪中间件单元测试

测试内容：
1. 中间件初始化和注册
2. 请求追踪上下文提取
3. 响应头注入
4. Span 属性设置
5. 指标记录
"""

import pytest
import json
from flask import Flask, jsonify

# 测试前确保 OpenTelemetry 可用
try:
    from agent.server_routes.tracing_middleware import (
        TracingMiddleware,
        register_tracing,
        get_current_trace_context,
        before_request_handler,
        after_request_handler
    )
    from agent.monitoring.tracing import (
        get_trace_id,
        set_trace_id,
        init_observability,
        is_opentelemetry_available
    )
    OPENTELEMETRY_AVAILABLE = is_opentelemetry_available()
except ImportError:
    OPENTELEMETRY_AVAILABLE = False


@pytest.fixture
def app():
    """创建测试用的 Flask 应用"""
    app = Flask(__name__)
    
    @app.route('/test')
    def test_route():
        return jsonify({"ok": True})
    
    @app.route('/test-error')
    def test_error_route():
        raise ValueError("Test error")
    
    return app


@pytest.mark.skipif(not OPENTELEMETRY_AVAILABLE, reason="OpenTelemetry not available")
class TestTracingMiddleware:
    """追踪中间件测试类"""
    
    def test_middleware_init(self, app):
        """测试中间件初始化"""
        middleware = TracingMiddleware(app)
        
        assert middleware.app == app
        assert hasattr(middleware, '_excluded_paths')
        assert '/favicon.ico' in middleware._excluded_paths
        assert '/static/' in middleware._excluded_paths
    
    def test_should_trace(self, app):
        """测试路径过滤逻辑"""
        middleware = TracingMiddleware(app)
        
        # 应该追踪的路径
        assert middleware._should_trace('/api/test') is True
        assert middleware._should_trace('/health') is True
        
        # 不应该追踪的路径
        assert middleware._should_trace('/favicon.ico') is False
        assert middleware._should_trace('/static/css/style.css') is False
        assert middleware._should_trace('/metrics') is False
    
    def test_register_tracing(self, app):
        """测试注册追踪中间件"""
        # 注册前检查
        wsgi_app_before = app.wsgi_app
        
        # 注册中间件
        register_tracing(app)
        
        # 注册后应该包装了中间件
        assert app.wsgi_app is not wsgi_app_before
        assert isinstance(app.wsgi_app, TracingMiddleware)
    
    def test_trace_context_extraction(self):
        """测试追踪上下文提取"""
        # 设置追踪上下文
        test_trace_id = "abc123def4567890"
        set_trace_id(test_trace_id)
        
        # 获取上下文
        context = get_current_trace_context()
        
        assert context['trace_id'] == test_trace_id
    
    def test_middleware_wsgi_call(self, app):
        """测试 WSGI 中间件调用"""
        register_tracing(app)
        
        # 创建模拟请求
        environ = {
            'PATH_INFO': '/test',
            'REQUEST_METHOD': 'GET',
            'SERVER_NAME': 'localhost',
            'SERVER_PORT': '5000',
            'wsgi.url_scheme': 'http',
        }
        
        # 调用中间件（使用应用上下文）
        response_data = []
        
        def start_response(status, headers, exc_info=None):
            response_data.append(status)
            response_data.append(dict(headers))
            return lambda data: response_data.append(data.decode('utf-8') if isinstance(data, bytes) else str(data))
        
        with app.app_context():
            response = app.wsgi_app(environ, start_response)
        
        # 收集响应数据
        for part in response:
            if callable(part):
                part(b'')
        
        # 验证响应
        assert len(response_data) >= 2
        assert response_data[0].startswith('200')
        
        # 检查响应头是否包含追踪上下文
        headers = response_data[1]
        assert 'traceparent' in headers or 'Traceparent' in headers
    
    def test_request_with_traceparent_header(self, app):
        """测试带 traceparent 头的请求"""
        register_tracing(app)
        
        # 创建带追踪上下文的请求
        environ = {
            'PATH_INFO': '/test',
            'REQUEST_METHOD': 'GET',
            'SERVER_NAME': 'localhost',
            'SERVER_PORT': '5000',
            'wsgi.url_scheme': 'http',
            'HTTP_TRACEPARENT': '00-abc123def4567890abc123def4567890-1234567812345678-01',
        }
        
        response_data = []
        
        def start_response(status, headers, exc_info=None):
            response_data.append(status)
            response_data.append(dict(headers))
            return lambda data: response_data.append(data.decode('utf-8') if isinstance(data, bytes) else str(data))
        
        # 使用应用上下文
        with app.app_context():
            response = app.wsgi_app(environ, start_response)
        
        # 收集响应
        for part in response:
            if callable(part):
                part(b'')
        
        # 验证响应状态
        assert response_data[0].startswith('200')


@pytest.mark.skipif(not OPENTELEMETRY_AVAILABLE, reason="OpenTelemetry not available")
class TestMetricsIntegration:
    """指标集成测试"""
    
    def test_record_request_metrics(self):
        """测试记录请求指标"""
        from agent.monitoring.tracing import record_request_metrics
        
        # 记录指标（应该不抛出异常）
        record_request_metrics('GET', '/test', 200, 150.5)
        record_request_metrics('POST', '/api/chat', 200, 500.3)
        record_request_metrics('GET', '/error', 500, 100.0)


@pytest.mark.skipif(not OPENTELEMETRY_AVAILABLE, reason="OpenTelemetry not available")
class TestLoggingIntegration:
    """日志集成测试"""
    
    def test_get_logger_with_context(self):
        """测试带上下文的 logger"""
        from agent.monitoring.tracing import get_logger_with_context
        
        logger = get_logger_with_context('test')
        
        # 检查 logger 有必要的方法
        assert hasattr(logger, 'debug')
        assert hasattr(logger, 'info')
        assert hasattr(logger, 'warning')
        assert hasattr(logger, 'error')
        assert hasattr(logger, 'critical')
        assert hasattr(logger, 'exception')
        
        # 设置追踪上下文
        test_trace_id = "test-trace-id-12345"
        set_trace_id(test_trace_id)
        
        # 调用日志方法（不应抛出异常）
        logger.info("Test log message")
        logger.debug("Debug message")


@pytest.mark.skipif(not OPENTELEMETRY_AVAILABLE, reason="OpenTelemetry not available")
class TestObservabilityInit:
    """可观测性初始化测试"""
    
    def test_init_observability(self):
        """测试初始化可观测性"""
        from agent.monitoring.tracing import init_observability
        
        # 应该不抛出异常
        init_observability()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
