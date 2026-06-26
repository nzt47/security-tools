#!/usr/bin/env python3
"""
HTTP 客户端追踪上下文自动注入工具

提供 requests 和 httpx 的包装类，自动在请求头中注入 traceparent。

使用示例：
    from agent.monitoring.trace_http_client import TraceSession
    
    session = TraceSession()
    response = session.get('http://api.example.com/data')
    # 此时请求头中已自动包含 traceparent
"""

import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

try:
    import requests
    from requests.adapters import HTTPAdapter
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    requests = None
    HTTPAdapter = None

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    httpx = None

# 导入追踪模块
from agent.monitoring.tracing import inject_trace_context, get_trace_id


class TraceHTTPAdapter(HTTPAdapter):
    """自动注入追踪上下文的 requests HTTPAdapter
    
    该 Adapter 在每次发送请求前，自动将当前追踪上下文注入到请求头中。
    """
    
    def send(self, request, **kwargs):
        """发送请求前自动注入追踪上下文"""
        method = request.method
        url = str(request.url)
        
        # 记录请求开始
        logger.info(f"[TraceHTTPAdapter] 开始处理请求: {method} {url}")
        logger.debug(f"[TraceHTTPAdapter] 请求头(注入前): {dict(request.headers)}")
        
        try:
            # 获取当前追踪上下文
            current_trace_id = get_trace_id()
            logger.debug(f"[TraceHTTPAdapter] 当前 trace_id: {current_trace_id}")
            
            # 注入追踪上下文
            trace_headers = inject_trace_context()
            logger.debug(f"[TraceHTTPAdapter] 生成的追踪上下文: {trace_headers}")
            
            # 更新请求头
            request.headers.update(trace_headers)
            logger.info(f"[TraceHTTPAdapter] ✅ 追踪上下文注入成功")
            logger.debug(f"[TraceHTTPAdapter] 请求头(注入后): {dict(request.headers)}")
            
        except Exception as e:
            logger.error(f"[TraceHTTPAdapter] ❌ 注入追踪上下文失败: {str(e)}")
            logger.error(f"[TraceHTTPAdapter] 错误详情: {type(e).__name__} - {e}")
            import traceback
            logger.error(f"[TraceHTTPAdapter] 错误堆栈: {traceback.format_exc()}")
        
        # 调用父类方法发送请求
        try:
            response = super().send(request, **kwargs)
            logger.info(f"[TraceHTTPAdapter] 请求完成: {method} {url}, 状态码: {response.status_code}")
            return response
        except Exception as e:
            logger.error(f"[TraceHTTPAdapter] 请求发送失败: {method} {url}, 错误: {str(e)}")
            raise


class TraceSession(requests.Session):
    """自动注入追踪上下文的 requests Session
    
    使用该 Session 发送的所有请求都会自动携带 traceparent 头。
    
    示例:
        session = TraceSession()
        response = session.get('http://api.example.com')
        # 请求头中自动包含 traceparent
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 替换默认的 adapter，实现自动注入
        self.mount('http://', TraceHTTPAdapter())
        self.mount('https://', TraceHTTPAdapter())
    
    def request(self, method, url, **kwargs):
        """重写 request 方法，添加追踪日志"""
        trace_id = get_trace_id()
        logger.info(f"[TraceSession] 发送请求: {method} {url}, trace_id={trace_id}")
        response = super().request(method, url, **kwargs)
        logger.info(f"[TraceSession] 请求完成: {method} {url}, status={response.status_code}, trace_id={trace_id}")
        return response


class TraceHttpxClient(httpx.Client):
    """自动注入追踪上下文的 httpx Client
    
    使用该 Client 发送的所有请求都会自动携带 traceparent 头。
    
    示例:
        client = TraceHttpxClient()
        response = client.get('http://api.example.com')
        # 请求头中自动包含 traceparent
    """
    
    def send(self, request, **kwargs):
        """发送请求前自动注入追踪上下文"""
        method = request.method
        url = str(request.url)
        
        logger.info(f"[TraceHttpxClient] 开始处理请求: {method} {url}")
        logger.debug(f"[TraceHttpxClient] 请求头(注入前): {dict(request.headers)}")
        
        try:
            current_trace_id = get_trace_id()
            logger.debug(f"[TraceHttpxClient] 当前 trace_id: {current_trace_id}")
            
            trace_headers = inject_trace_context()
            logger.debug(f"[TraceHttpxClient] 生成的追踪上下文: {trace_headers}")
            
            request.headers.update(trace_headers)
            logger.info(f"[TraceHttpxClient] ✅ 追踪上下文注入成功")
            logger.debug(f"[TraceHttpxClient] 请求头(注入后): {dict(request.headers)}")
            
        except Exception as e:
            logger.error(f"[TraceHttpxClient] ❌ 注入追踪上下文失败: {str(e)}")
            logger.error(f"[TraceHttpxClient] 错误详情: {type(e).__name__} - {e}")
            import traceback
            logger.error(f"[TraceHttpxClient] 错误堆栈: {traceback.format_exc()}")
        
        try:
            response = super().send(request, **kwargs)
            logger.info(f"[TraceHttpxClient] 请求完成: {method} {url}, 状态码: {response.status_code}")
            return response
        except Exception as e:
            logger.error(f"[TraceHttpxClient] 请求发送失败: {method} {url}, 错误: {str(e)}")
            raise


class TraceHttpxAsyncClient(httpx.AsyncClient):
    """自动注入追踪上下文的 httpx AsyncClient
    
    异步版本，用于 async/await 模式。
    """
    
    async def send(self, request, **kwargs):
        """发送请求前自动注入追踪上下文"""
        method = request.method
        url = str(request.url)
        
        logger.info(f"[TraceHttpxAsyncClient] 开始处理请求: {method} {url}")
        logger.debug(f"[TraceHttpxAsyncClient] 请求头(注入前): {dict(request.headers)}")
        
        try:
            current_trace_id = get_trace_id()
            logger.debug(f"[TraceHttpxAsyncClient] 当前 trace_id: {current_trace_id}")
            
            trace_headers = inject_trace_context()
            logger.debug(f"[TraceHttpxAsyncClient] 生成的追踪上下文: {trace_headers}")
            
            request.headers.update(trace_headers)
            logger.info(f"[TraceHttpxAsyncClient] ✅ 追踪上下文注入成功")
            logger.debug(f"[TraceHttpxAsyncClient] 请求头(注入后): {dict(request.headers)}")
            
        except Exception as e:
            logger.error(f"[TraceHttpxAsyncClient] ❌ 注入追踪上下文失败: {str(e)}")
            logger.error(f"[TraceHttpxAsyncClient] 错误详情: {type(e).__name__} - {e}")
            import traceback
            logger.error(f"[TraceHttpxAsyncClient] 错误堆栈: {traceback.format_exc()}")
        
        try:
            response = await super().send(request, **kwargs)
            logger.info(f"[TraceHttpxAsyncClient] 请求完成: {method} {url}, 状态码: {response.status_code}")
            return response
        except Exception as e:
            logger.error(f"[TraceHttpxAsyncClient] 请求发送失败: {method} {url}, 错误: {str(e)}")
            raise


def create_trace_requests_session() -> requests.Session:
    """创建带有追踪上下文自动注入的 requests Session"""
    if not REQUESTS_AVAILABLE:
        raise ImportError("requests 库未安装，请执行: pip install requests")
    
    return TraceSession()


def create_trace_httpx_client(*args, **kwargs) -> httpx.Client:
    """创建带有追踪上下文自动注入的 httpx Client"""
    if not HTTPX_AVAILABLE:
        raise ImportError("httpx 库未安装，请执行: pip install httpx")
    
    return TraceHttpxClient(*args, **kwargs)


def create_trace_httpx_async_client(*args, **kwargs) -> httpx.AsyncClient:
    """创建带有追踪上下文自动注入的 httpx AsyncClient"""
    if not HTTPX_AVAILABLE:
        raise ImportError("httpx 库未安装，请执行: pip install httpx")
    
    return TraceHttpxAsyncClient(*args, **kwargs)


def inject_trace_headers(headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """手动注入追踪上下文到请求头
    
    Args:
        headers: 现有请求头字典，可选
        
    Returns:
        包含追踪上下文的请求头字典
        
    示例:
        headers = {'Content-Type': 'application/json'}
        headers = inject_trace_headers(headers)
        response = requests.get(url, headers=headers)
    """
    result = headers.copy() if headers else {}
    trace_headers = inject_trace_context()
    result.update(trace_headers)
    return result


# 快捷函数
def trace_get(url, **kwargs):
    """自动注入追踪上下文的 GET 请求"""
    headers = kwargs.pop('headers', {})
    headers = inject_trace_headers(headers)
    return requests.get(url, headers=headers, **kwargs)


def trace_post(url, **kwargs):
    """自动注入追踪上下文的 POST 请求"""
    headers = kwargs.pop('headers', {})
    headers = inject_trace_headers(headers)
    return requests.post(url, headers=headers, **kwargs)


def trace_put(url, **kwargs):
    """自动注入追踪上下文的 PUT 请求"""
    headers = kwargs.pop('headers', {})
    headers = inject_trace_headers(headers)
    return requests.put(url, headers=headers, **kwargs)


def trace_delete(url, **kwargs):
    """自动注入追踪上下文的 DELETE 请求"""
    headers = kwargs.pop('headers', {})
    headers = inject_trace_headers(headers)
    return requests.delete(url, headers=headers, **kwargs)


__all__ = [
    'TraceHTTPAdapter',
    'TraceSession',
    'TraceHttpxClient',
    'TraceHttpxAsyncClient',
    'create_trace_requests_session',
    'create_trace_httpx_client',
    'create_trace_httpx_async_client',
    'inject_trace_headers',
    'trace_get',
    'trace_post',
    'trace_put',
    'trace_delete',
]
