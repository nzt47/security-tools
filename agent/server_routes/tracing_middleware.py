#!/usr/bin/env python3
"""
Flask 分布式追踪中间件

功能：
- 自动为所有请求创建 OpenTelemetry Span
- 从请求头提取追踪上下文（支持 W3C Trace Context 和 Jaeger 格式）
- 注入追踪上下文到响应头
- 添加自定义 span 属性（请求路径、状态码、耗时等）
- 实现追踪与日志的关联

遵循 OpenTelemetry HTTP 语义规范：
https://github.com/open-telemetry/semantic-conventions/blob/main/docs/http/http-spans.md
"""

import time
import logging
from flask import request, g, has_request_context

from agent.monitoring.tracing import (
    extract_trace_context,
    inject_trace_context,
    set_trace_id,
    set_span_id,
    get_trace_id,
    get_span_id,
    capture_context,
    restore_context,
    init_observability,
    is_opentelemetry_available,
)

# 条件导入 OpenTelemetry 符号（不可用时定义 stub 以保证降级路径可运行）
try:
    from opentelemetry.trace import get_tracer, SpanKind, StatusCode
    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False

    class _SpanKindStub:
        SERVER = "server"
        CLIENT = "client"
        INTERNAL = "internal"

    class _StatusCodeStub:
        OK = "ok"
        ERROR = "error"

    def get_tracer(name=None, **kwargs):
        return None

    SpanKind = _SpanKindStub
    StatusCode = _StatusCodeStub


def record_request_metrics(method, path, status_code, duration_ms):
    """记录请求指标（stub：tracing.py 未提供此函数时的降级实现）"""
    logger.debug(
        '{"trace_id": "%s", "module_name": "tracing_middleware", "action": "record_metrics", '
        '"method": "%s", "path": "%s", "status_code": %d, "duration_ms": %.2f}',
        get_trace_id(), method, path, status_code, duration_ms
    )


def get_logger_with_context(name):
    """获取带追踪上下文的 logger（降级实现：返回标准 logger）"""
    return logging.getLogger(name)


logger = get_logger_with_context(__name__)

# 缓存的 Tracer 实例
_tracer = None

def _get_tracer():
    """获取 Tracer 实例（延迟初始化）"""
    global _tracer
    if _tracer is None:
        init_observability()
        if is_opentelemetry_available():
            _tracer = get_tracer("yunshu-flask")
    return _tracer


class TracingMiddleware:
    """
    Flask 分布式追踪中间件
    
    为每个 HTTP 请求自动创建追踪 Span，并记录请求相关的属性。
    
    使用方式：
        from agent.server_routes.tracing_middleware import TracingMiddleware
        
        app = Flask(__name__)
        app.wsgi_app = TracingMiddleware(app.wsgi_app)
    
    支持的追踪上下文格式：
    - W3C Trace Context: traceparent 头
    - Jaeger: uber-trace-id 头
    """
    
    def __init__(self, app):
        self.app = app
        self._excluded_paths = {
            '/favicon.ico',
            '/robots.txt',
            '/static/',
            '/metrics',
        }
        logger.info("[Tracing Middleware] 已初始化")
    
    def _should_trace(self, path):
        """判断是否应该追踪该请求"""
        for excluded in self._excluded_paths:
            if path.startswith(excluded):
                return False
        return True
    
    def _get_client_ip(self):
        """获取客户端 IP 地址"""
        if request.headers.get('X-Forwarded-For'):
            return request.headers['X-Forwarded-For'].split(',')[0].strip()
        if request.headers.get('X-Real-IP'):
            return request.headers['X-Real-IP']
        return request.remote_addr or 'unknown'
    
    def _get_request_size(self):
        """获取请求体大小"""
        try:
            return request.content_length or 0
        except Exception:
            return 0
    
    def __call__(self, environ, start_response):
        """WSGI 中间件入口"""
        # 获取请求路径
        path = environ.get('PATH_INFO', '/')
        
        # 检查是否需要追踪
        if not self._should_trace(path):
            return self.app(environ, start_response)
        
        # 记录开始时间
        start_time = time.time()
        
        # 保存原始上下文（用于嵌套追踪场景）
        original_context = capture_context()
        
        # 从请求头提取追踪上下文
        headers = {}
        for key, value in environ.items():
            if key.startswith('HTTP_'):
                header_name = key[5:].replace('_', '-').lower()
                headers[header_name] = value
        
        # 提取追踪上下文
        trace_context = extract_trace_context(headers)
        
        # 设置追踪上下文
        if trace_context.get('trace_id'):
            set_trace_id(trace_context['trace_id'])
            logger.debug(f"[Tracing] 从请求头提取 trace_id: {trace_context['trace_id']}")
        
        if trace_context.get('span_id'):
            set_span_id(trace_context['span_id'])
            logger.debug(f"[Tracing] 从请求头提取 span_id: {trace_context['span_id']}")
        
        # 创建 OpenTelemetry Span（仅在 Flask 请求上下文中创建）
        otel_span = None
        tracer = _get_tracer() if has_request_context() else None
        span_name = f"{environ.get('REQUEST_METHOD', 'GET')} {path}"

        if tracer:
            try:
                otel_span = tracer.start_span(
                    name=span_name,
                    kind=SpanKind.SERVER
                )

                # 设置标准 HTTP 属性
                otel_span.set_attribute("http.method", environ.get('REQUEST_METHOD', 'GET'))
                otel_span.set_attribute("http.url", path)
                otel_span.set_attribute("http.scheme", environ.get('wsgi.url_scheme', 'http'))
                otel_span.set_attribute("server.address", environ.get('SERVER_NAME', 'localhost'))
                otel_span.set_attribute("server.port", int(environ.get('SERVER_PORT', 5000)))
                otel_span.set_attribute("client.address", self._get_client_ip())
                otel_span.set_attribute("http.request.size", self._get_request_size())

                # 设置自定义属性
                otel_span.set_attribute("app.name", "yunshu-agent")
                otel_span.set_attribute("trace.id", get_trace_id() or "unknown")

                logger.debug(f"[Tracing] 创建 Span: {span_name}, trace_id={get_trace_id()}")

            except Exception as e:
                logger.error(f"[Tracing] 创建 OTel Span 失败: {e}")
                otel_span = None
        
        # 存储 span 到请求上下文，供后续使用
        # 需要先确保有应用上下文
        try:
            g.tracing_span = otel_span
            g.trace_id = get_trace_id()
            g.span_start_time = start_time
        except RuntimeError as e:
            logger.debug(f"[Tracing] 当前不在应用上下文，跳过 g 对象设置: {e}")
        
        # 自定义 start_response 来捕获响应状态码
        status_code = 200
        response_headers = []
        
        def custom_start_response(status, headers, exc_info=None):
            nonlocal status_code
            # 提取状态码
            try:
                status_code = int(status.split()[0])
            except (ValueError, IndexError):
                status_code = 500
            
            # 保存响应头
            response_headers.extend(headers)
            
            # 注入追踪上下文到响应头
            trace_headers = inject_trace_context()
            for key, value in trace_headers.items():
                headers.append((key, value))
            
            return start_response(status, headers, exc_info)
        
        try:
            # 执行请求
            response = self.app(environ, custom_start_response)
            
            # 计算耗时
            duration_ms = (time.time() - start_time) * 1000
            
            # 更新 span 属性
            if otel_span:
                try:
                    otel_span.set_attribute("http.status_code", status_code)
                    otel_span.set_attribute("http.response.duration_ms", duration_ms)
                    otel_span.set_attribute("http.response.size", 0)  # 后续可优化
                    
                    # 设置状态码
                    if status_code >= 500:
                        otel_span.set_status(StatusCode.ERROR)
                    elif status_code >= 400:
                        otel_span.set_status(StatusCode.ERROR)
                    else:
                        otel_span.set_status(StatusCode.OK)
                    
                    logger.info(
                        '{"trace_id": "%s", "module_name": "tracing_middleware", "action": "request", '
                        '"http.method": "%s", "http.url": "%s", "http.status_code": %d, "duration_ms": %.2f}',
                        get_trace_id(),
                        environ.get('REQUEST_METHOD', 'GET'),
                        path,
                        status_code,
                        duration_ms
                    )
                    
                    # 记录请求指标
                    record_request_metrics(
                        method=environ.get('REQUEST_METHOD', 'GET'),
                        path=path,
                        status_code=status_code,
                        duration_ms=duration_ms
                    )
                    
                except Exception as e:
                    logger.error(f"[Tracing] 更新 Span 属性失败: {e}")
            
            return response
            
        except Exception as e:
            # 处理异常
            duration_ms = (time.time() - start_time) * 1000
            
            if otel_span:
                try:
                    otel_span.set_status(StatusCode.ERROR, str(e))
                    otel_span.record_exception(e)
                    otel_span.set_attribute("http.status_code", 500)
                    otel_span.set_attribute("http.response.duration_ms", duration_ms)
                except Exception as span_ex:
                    logger.error(f"[Tracing] 记录异常失败: {span_ex}")
            
            logger.error(
                '{"trace_id": "%s", "module_name": "tracing_middleware", "action": "request_error", '
                '"http.method": "%s", "http.url": "%s", "duration_ms": %.2f, "error": "%s"}',
                get_trace_id(),
                environ.get('REQUEST_METHOD', 'GET'),
                path,
                duration_ms,
                str(e)
            )
            
            raise
        
        finally:
            # 结束 span
            if otel_span:
                try:
                    otel_span.end()
                    logger.debug(f"[Tracing] 结束 Span: {span_name}")
                except Exception as e:
                    logger.error(f"[Tracing] 结束 Span 失败: {e}")
            
            # 恢复原始上下文
            restore_context(original_context)


def before_request_handler():
    """Flask before_request 处理器
    
    在每个请求处理前设置追踪上下文
    """
    # 确保 OpenTelemetry 已初始化
    init_observability()
    
    # 从请求头提取追踪上下文
    headers = dict(request.headers)
    trace_context = extract_trace_context(headers)
    
    if trace_context.get('trace_id'):
        set_trace_id(trace_context['trace_id'])
    if trace_context.get('span_id'):
        set_trace_id(trace_context['span_id'])


def after_request_handler(response):
    """Flask after_request 处理器
    
    在每个请求处理后注入追踪上下文到响应头
    """
    # 注入追踪上下文
    trace_headers = inject_trace_context()
    for key, value in trace_headers.items():
        response.headers[key] = value
    
    return response


def register_tracing(app):
    """注册追踪中间件和处理器
    
    Args:
        app: Flask 应用实例
    """
    # 注册 WSGI 中间件（优先）
    app.wsgi_app = TracingMiddleware(app.wsgi_app)
    
    # 注册 before_request 和 after_request 处理器
    app.before_request(before_request_handler)
    app.after_request(after_request_handler)
    
    logger.info("[Tracing Middleware] 已注册到 Flask 应用")


def get_current_trace_context():
    """获取当前请求的追踪上下文
    
    Returns:
        dict: 包含 trace_id, span_id 的字典
    """
    return {
        'trace_id': get_trace_id(),
        'span_id': get_span_id()
    }
