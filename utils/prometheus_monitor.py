"""
Prometheus 监控中间件
用于跟踪 API 请求指标：错误率、请求耗时、请求计数等
"""

from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from prometheus_client import CollectorRegistry
import time
import os
from datetime import datetime

# 创建注册表
registry = CollectorRegistry()

# ════════════════════════════════════════════════════════════
# 定义指标
# ════════════════════════════════════════════════════════════

# 请求计数器
REQUEST_COUNT = Counter(
    'http_requests_total',
    'Total HTTP requests',
    ['method', 'endpoint', 'status', 'handler'],
    registry=registry
)

# 请求耗时直方图 (单位：秒)
REQUEST_LATENCY = Histogram(
    'http_request_duration_seconds',
    'HTTP request latency in seconds',
    ['method', 'endpoint', 'handler'],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=registry
)

# 请求大小直方图 (单位：字节)
REQUEST_SIZE = Histogram(
    'http_request_size_bytes',
    'HTTP request size in bytes',
    ['method', 'endpoint'],
    buckets=(256, 512, 1024, 2048, 4096, 8192, 16384, 32768, 65536),
    registry=registry
)

# 响应大小直方图 (单位：字节)
RESPONSE_SIZE = Histogram(
    'http_response_size_bytes',
    'HTTP response size in bytes',
    ['method', 'endpoint', 'status'],
    buckets=(256, 512, 1024, 2048, 4096, 8192, 16384, 32768, 65536),
    registry=registry
)

# 活跃请求数
REQUESTS_IN_PROGRESS = Gauge(
    'http_requests_in_progress',
    'Number of HTTP requests currently being processed',
    ['handler'],
    registry=registry
)

# 错误计数器
ERROR_COUNT = Counter(
    'http_errors_total',
    'Total HTTP errors',
    ['method', 'endpoint', 'status', 'error_type'],
    registry=registry
)

# 安全拦截计数器
SECURITY_BLOCKS = Counter(
    'security_blocks_total',
    'Total security blocks',
    ['rule', 'level', 'category'],
    registry=registry
)

# LLM 调用计数器
LLM_CALLS = Counter(
    'llm_calls_total',
    'Total LLM calls',
    ['provider', 'model', 'status'],
    registry=registry
)

# LLM 调用耗时
LLM_LATENCY = Histogram(
    'llm_call_duration_seconds',
    'LLM call latency in seconds',
    ['provider', 'model'],
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
    registry=registry
)

# 系统资源指标
CPU_USAGE = Gauge(
    'system_cpu_usage_percent',
    'CPU usage percentage',
    registry=registry
)

MEMORY_USAGE = Gauge(
    'system_memory_usage_percent',
    'Memory usage percentage',
    registry=registry
)

# ════════════════════════════════════════════════════════════
# Prometheus 中间件
# ════════════════════════════════════════════════════════════

class PrometheusMiddleware:
    """Flask Prometheus 监控中间件 (WSGI 中间件模式)"""
    
    def __init__(self, wsgi_app):
        """
        初始化 WSGI 中间件
        
        Args:
            wsgi_app: Flask 的 WSGI 应用
        """
        self.wsgi_app = wsgi_app
        
        # 注意：路由需要在 Flask 应用中注册，不是在中间件中
        # 这个中间件只负责拦截请求和记录指标
    
    def __call__(self, environ, start_response):
        """WSGI 应用调用"""
        from flask import request, g
        
        # 记录开始时间
        start_time = time.time()
        
        # 增加活跃请求数
        REQUESTS_IN_PROGRESS.labels(handler='flask').inc()
        
        # 记录请求大小
        try:
            content_length = int(environ.get('CONTENT_LENGTH', 0))
            if content_length > 0:
                REQUEST_SIZE.labels(
                    method=environ.get('REQUEST_METHOD', 'UNKNOWN'),
                    endpoint=environ.get('PATH_INFO', 'unknown')
                ).observe(content_length)
        except (ValueError, TypeError):
            pass
        
        # 包装 start_response 以捕获响应状态码和大小
        def custom_start_response(status, response_headers, exc_info=None):
            # 解析状态码
            status_code = int(status.split()[0])
            
            # 计算耗时
            latency = time.time() - start_time
            
            # 获取端点信息
            method = environ.get('REQUEST_METHOD', 'UNKNOWN')
            endpoint = environ.get('PATH_INFO', 'unknown')
            
            # 记录请求耗时
            REQUEST_LATENCY.labels(
                method=method,
                endpoint=endpoint,
                handler='flask'
            ).observe(latency)
            
            # 记录请求计数
            REQUEST_COUNT.labels(
                method=method,
                endpoint=endpoint,
                status=str(status_code),
                handler='flask'
            ).inc()
            
            # 记录错误
            if status_code >= 400:
                ERROR_COUNT.labels(
                    method=method,
                    endpoint=endpoint,
                    status=str(status_code),
                    error_type=f'{status_code}'
                ).inc()
            
            # 减少活跃请求数
            REQUESTS_IN_PROGRESS.labels(handler='flask').dec()
            
            # 调用原始 start_response
            return start_response(status, response_headers, exc_info)
        
        # 调用原始应用
        return self.app(environ, custom_start_response)


# ════════════════════════════════════════════════════════════
# 装饰器形式的监控
# ════════════════════════════════════════════════════════════

def monitor_llm_call(provider='unknown', model='unknown'):
    """LLM 调用监控装饰器"""
    def decorator(f):
        def wrapped(*args, **kwargs):
            start_time = time.time()
            try:
                result = f(*args, **kwargs)
                LLM_CALLS.labels(
                    provider=provider,
                    model=model,
                    status='success'
                ).inc()
                return result
            except Exception as e:
                LLM_CALLS.labels(
                    provider=provider,
                    model=model,
                    status='error'
                ).inc()
                raise
            finally:
                latency = time.time() - start_time
                LLM_LATENCY.labels(
                    provider=provider,
                    model=model
                ).observe(latency)
        return wrapped
    return decorator


def record_security_block(rule='unknown', level='unknown', category='unknown'):
    """记录安全拦截"""
    SECURITY_BLOCKS.labels(
        rule=rule,
        level=level,
        category=category
    ).inc()


def update_system_metrics():
    """更新系统资源指标"""
    try:
        import psutil
        CPU_USAGE.set(psutil.cpu_percent(interval=1))
        MEMORY_USAGE.set(psutil.virtual_memory().percent)
    except Exception:
        pass


# ════════════════════════════════════════════════════════════
# 指标查询辅助函数
# ════════════════════════════════════════════════════════════

def get_error_rate(time_range='1h'):
    """获取错误率 (需要 Prometheus 查询接口)"""
    # 这个函数需要在 Prometheus 中查询
    # 示例查询：rate(http_requests_total{status=~"5.."}[1h]) / rate(http_requests_total[1h])
    return None


def get_latency_percentile(percentile=95):
    """获取延迟百分位数 (需要 Prometheus 查询接口)"""
    # 示例查询：histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))
    return None


def get_metrics_summary():
    """获取指标摘要"""
    import io
    output = io.StringIO()
    for line in generate_latest(registry).decode('utf-8').split('\n'):
        if line and not line.startswith('#'):
            output.write(line + '\n')
    return output.getvalue()
