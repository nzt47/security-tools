"""API 路由追踪装饰器模块"""
from agent.monitoring.tracing import TraceContext


def trace_route(service_name="API"):
    """追踪路由装饰器
    
    自动为 API 路由添加追踪上下文，确保完整的链路追踪。
    
    Args:
        service_name: 服务名称，用于追踪标识
    
    Returns:
        装饰后的函数
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            operation = func.__name__.replace("api_", "").replace("_", ".")
            with TraceContext(service_name, operation):
                return func(*args, **kwargs)
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper
    return decorator


def trace_async_route(service_name="API"):
    """异步追踪路由装饰器
    
    用于异步路由函数的追踪装饰器。
    
    Args:
        service_name: 服务名称，用于追踪标识
    
    Returns:
        装饰后的函数
    """
    def decorator(func):
        async def wrapper(*args, **kwargs):
            operation = func.__name__.replace("api_", "").replace("_", ".")
            with TraceContext(service_name, operation):
                return await func(*args, **kwargs)
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper
    return decorator


def _safe_call(func, *args, action="safe_call", **kwargs):
    """安全调用包装器——捕获异常并记录结构化日志后重新抛出

    用于边界显性化：可能失败的操作应通过此包装器调用，
    确保异常被记录后再向上传播，而非静默吞掉。
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.error(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "tracing_decorator",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
