#!/usr/bin/env python3
"""
性能监控和错误处理装饰器

提供便捷的装饰器来自动追踪函数执行时间、调用次数和错误处理。

依赖解耦说明
-------------------
本模块**不再**在模块级导入 ``agent.error_handler``，以彻底切断
``monitoring → error_handler`` 的硬依赖（历史上 error_handler 反向依赖
monitoring.metrics，构成循环）。所有 error_handler 符号改为在使用点
（函数体内）延迟导入，保持 100% 向后兼容。

类型注解通过 ``from __future__ import annotations`` 延迟求值，
因此 ``ErrorCategory`` / ``ErrorSeverity`` 等类型可作字符串注解使用，
无需模块级导入。
"""

from __future__ import annotations

import time
import functools
import logging
import traceback
from typing import Callable, Optional, Type, Tuple, Any, Dict
from agent.monitoring.metrics import get_metrics_collector
from agent.monitoring.tracing import TraceContext, get_trace_id
from agent.monitoring.error_reporter import get_error_reporter, AlertLevel

logger = logging.getLogger(__name__)

def monitor_latency(metric_name: str):
    """延迟监控装饰器
    
    自动记录函数执行时间到指定的指标。
    
    用法:
        class VectorStore:
            @monitor_latency("latency.memory.search")
            def search(self, query):
                ...
    
    Args:
        metric_name: 指标名称 (如: latency.memory.search)
    
    Returns:
        装饰器函数
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            collector = get_metrics_collector()
            start = time.time()
            
            try:
                result = func(*args, **kwargs)
                # 成功时也记录延迟
                duration = time.time() - start
                collector.record_latency(metric_name, duration)
                return result
            except Exception as e:
                # 异常时也记录延迟
                duration = time.time() - start
                collector.record_latency(metric_name, duration)
                raise
        
        # 包装函数添加额外属性
        wrapper.__wrapped__ = func
        wrapper._metric_name = metric_name
        
        return wrapper
    return decorator

def monitor_counter(counter_name: str):
    """计数器监控装饰器
    
    每次函数调用成功时增加计数器。
    
    用法:
        class DigitalLife:
            @monitor_counter("count.chat.total")
            def chat(self, user_input):
                ...
    
    Args:
        counter_name: 计数器名称 (如: count.chat.total)
    
    Returns:
        装饰器函数
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            collector = get_metrics_collector()
            
            try:
                result = func(*args, **kwargs)
                collector.increment_counter(counter_name)
                return result
            except Exception:
                raise
        
        wrapper.__wrapped__ = func
        wrapper._counter_name = counter_name
        
        return wrapper
    return decorator

def monitor_both(metric_name: str, counter_name: str):
    """延迟和计数器双重监控装饰器
    
    同时记录函数执行时间和调用次数。
    
    用法:
        class DigitalLife:
            @monitor_both("latency.chat", "count.chat.total")
            def chat(self, user_input):
                ...
    
    Args:
        metric_name: 延迟指标名称
        counter_name: 计数器名称
    
    Returns:
        装饰器函数
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            collector = get_metrics_collector()
            start = time.time()
            
            try:
                result = func(*args, **kwargs)
                collector.increment_counter(counter_name)
                return result
            finally:
                duration = time.time() - start
                collector.record_latency(metric_name, duration)
        
        wrapper.__wrapped__ = func
        wrapper._metric_name = metric_name
        wrapper._counter_name = counter_name
        
        return wrapper
    return decorator

def trace_operation(service: str, operation: str):
    """追踪装饰器
    
    为函数添加 TraceContext 追踪。
    
    用法:
        @trace_operation("DigitalLife", "chat")
        def chat(self, user_input):
            ...
    
    Args:
        service: 服务名称
        operation: 操作名称
    
    Returns:
        装饰器函数
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with TraceContext(service, operation):
                return func(*args, **kwargs)
        
        wrapper.__wrapped__ = func
        wrapper._service = service
        wrapper._operation = operation
        
        return wrapper
    return decorator

def monitored(metric_name: str = None, counter_name: str = None, 
              service: str = None, operation: str = None):
    """综合监控装饰器
    
    根据参数组合启用不同的监控功能。
    
    用法:
        # 只监控延迟
        @monitored(metric_name="latency.operation")
        def operation():
            ...
        
        # 监控延迟 + 计数
        @monitored(metric_name="latency.op", counter_name="count.op")
        def operation():
            ...
        
        # 添加追踪
        @monitored(service="MyService", operation="myOp")
        def operation():
            ...
    
    Args:
        metric_name: 延迟指标名称
        counter_name: 计数器名称
        service: 服务名称（用于追踪）
        operation: 操作名称（用于追踪）
    
    Returns:
        装饰器函数
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            collector = get_metrics_collector()
            start = time.time()
            trace_id = get_trace_id()
            
            # 执行追踪
            if service and operation:
                context = TraceContext(service, operation)
                context.__enter__()
            
            try:
                result = func(*args, **kwargs)
                
                # 增加计数器
                if counter_name:
                    collector.increment_counter(counter_name)
                
                return result
            finally:
                # 记录延迟
                if metric_name:
                    duration = time.time() - start
                    collector.record_latency(metric_name, duration)
                
                # 退出追踪
                if service and operation:
                    context.__exit__(None, None, None)
        
        return wrapper
    return decorator


# ════════════════════════════════════════════════════════════════════════════════
#  错误处理装饰器
# ════════════════════════════════════════════════════════════════════════════════

def handle_errors(
    error_category: Optional[ErrorCategory] = None,
    error_severity: Optional[ErrorSeverity] = None,
    report_error: bool = True,
    log_error: bool = True,
    return_on_error: Any = None,
    retry_on_error: bool = False,
    max_retries: int = 3,
    retry_delay: float = 1.0,
    error_counter: str = None,
    ignored_exceptions: Tuple[Type[Exception], ...] = (),
    service_name: str = None,
):
    """
    统一错误处理装饰器
    
    自动捕获异常、记录日志、上报错误，并可选重试或返回默认值。
    
    用法:
        @handle_errors(
            error_category=ErrorCategory.EXTERNAL_SERVICE,
            report_error=True,
            log_error=True,
            return_on_error="处理失败"
        )
        def my_function():
            ...
    
    Args:
        error_category: 错误分类
        error_severity: 错误严重级别
        report_error: 是否上报错误到监控系统
        log_error: 是否记录错误日志
        return_on_error: 发生错误时返回的默认值
        retry_on_error: 是否自动重试
        max_retries: 最大重试次数
        retry_delay: 重试间隔（秒）
        error_counter: 错误计数器名称（用于监控）
        ignored_exceptions: 忽略的异常类型（不记录、不上报）
        service_name: 服务名称（用于错误上报）
    
    Returns:
        装饰器函数
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            func_name = func.__name__
            actual_service = service_name or func_name
            trace_id = get_trace_id()

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)

                except ignored_exceptions:
                    # 忽略的异常类型，直接重新抛出
                    raise

                except Exception as e:
                    # 延迟导入 error_handler 符号：仅在异常路径触发，
                    # 成功路径完全不依赖 error_handler，彻底切断 monitoring → error_handler 硬依赖
                    from agent.error_handler import (
                        get_error_handler,
                        ErrorCategory as _ErrorCategory,
                        ErrorSeverity as _ErrorSeverity,
                        YunshuError as _YunshuError,
                    )
                    _error_category = error_category if error_category is not None else _ErrorCategory.UNKNOWN
                    _error_severity = error_severity if error_severity is not None else _ErrorSeverity.ERROR

                    # 构建错误上下文
                    context = {
                        'function': func_name,
                        'attempt': attempt + 1,
                        'trace_id': trace_id,
                        'args': str(args)[:200] if args else None,
                        'kwargs': str(kwargs)[:200] if kwargs else None,
                    }

                    # 记录错误日志
                    if log_error:
                        logger.error(
                            f"[{trace_id}] 函数 {func_name} 执行失败 (尝试 {attempt + 1}/{max_retries}): {e}",
                            exc_info=True
                        )

                    # 增加错误计数器
                    if error_counter:
                        try:
                            collector = get_metrics_collector()
                            collector.increment_counter(error_counter)
                        except Exception:
                            pass

                    # 上报错误
                    if report_error:
                        try:
                            reporter = get_error_reporter()
                            reporter.report_error(
                                error=e,
                                level=AlertLevel(_error_severity.value),
                                context=context,
                                trace_id=trace_id,
                                service=actual_service
                            )
                        except Exception as report_e:
                            logger.error(f"错误上报失败: {report_e}")

                    # 记录到错误处理器
                    try:
                        error_handler = get_error_handler()
                        yunshu_error = _YunshuError(
                            str(e),
                            severity=_error_severity,
                            category=_error_category,
                            context=context
                        ).with_original(e)
                        error_handler.record_error(yunshu_error)
                    except Exception as handler_e:
                        logger.error(f"错误处理记录失败: {handler_e}")
                    
                    # 判断是否需要重试
                    if retry_on_error and attempt < max_retries:
                        logger.warning(
                            f"[{trace_id}] 函数 {func_name} 将在 {retry_delay} 秒后重试 (尝试 {attempt + 1}/{max_retries})"
                        )
                        time.sleep(retry_delay)
                        continue
                    
                    # 返回默认值或重新抛出异常
                    if return_on_error is not None:
                        return return_on_error
                    raise
        
        return wrapper
    return decorator


def catch_and_report(
    exception_type: Type[Exception] = Exception,
    level: AlertLevel = AlertLevel.ERROR,
    context: Optional[Dict[str, Any]] = None,
):
    """
    捕获指定类型异常并上报的简化装饰器
    
    用法:
        @catch_and_report(MyCustomError, level=AlertLevel.WARNING)
        def my_function():
            ...
    
    Args:
        exception_type: 要捕获的异常类型
        level: 告警级别
        context: 额外的上下文信息
    
    Returns:
        装饰器函数
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except exception_type as e:
                trace_id = get_trace_id()
                extra_context = context or {}
                extra_context.update({
                    'function': func.__name__,
                    'trace_id': trace_id,
                })
                
                logger.error(f"[{trace_id}] 捕获异常 {exception_type.__name__}: {e}")
                
                try:
                    reporter = get_error_reporter()
                    reporter.report_error(
                        error=e,
                        level=level,
                        context=extra_context,
                        trace_id=trace_id
                    )
                except Exception as report_e:
                    logger.error(f"错误上报失败: {report_e}")
                
                raise
        
        return wrapper
    return decorator


def safe_call(
    default_return: Any = None,
    log_errors: bool = True,
    report_errors: bool = False,
):
    """
    安全调用装饰器 - 捕获所有异常并返回默认值
    
    用法:
        @safe_call(default_return="操作失败")
        def my_function():
            ...
    
    Args:
        default_return: 异常时返回的默认值
        log_errors: 是否记录错误日志
        report_errors: 是否上报错误
    
    Returns:
        装饰器函数
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                trace_id = get_trace_id()
                if log_errors:
                    logger.error(f"[{trace_id}] 安全调用失败: {e}")
                
                if report_errors:
                    try:
                        reporter = get_error_reporter()
                        reporter.report_error(
                            error=e,
                            level=AlertLevel.WARNING,
                            context={'function': func.__name__, 'trace_id': trace_id}
                        )
                    except Exception:
                        pass
                
                return default_return
        
        return wrapper
    return decorator


def async_handle_errors(**kwargs):
    """
    异步函数的错误处理装饰器
    
    用法:
        @async_handle_errors(report_error=True)
        async def my_async_function():
            ...
    
    Args:
        **kwargs: 同 handle_errors 参数
    
    Returns:
        装饰器函数
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs_inner):
            func_name = func.__name__
            trace_id = get_trace_id()
            max_retries = kwargs.get('max_retries', 3)
            retry_delay = kwargs.get('retry_delay', 1.0)
            return_on_error = kwargs.get('return_on_error')

            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs_inner)

                except Exception as e:
                    # 延迟导入 ErrorSeverity：仅在异常路径触发，成功路径不依赖 error_handler
                    from agent.error_handler import ErrorSeverity as _ErrorSeverity
                    error_severity = kwargs.get('error_severity') or _ErrorSeverity.ERROR

                    context = {
                        'function': func_name,
                        'attempt': attempt + 1,
                        'trace_id': trace_id,
                    }

                    if kwargs.get('log_error', True):
                        logger.error(
                            f"[{trace_id}] 异步函数 {func_name} 执行失败: {e}",
                            exc_info=True
                        )

                    if kwargs.get('report_error', True):
                        try:
                            reporter = get_error_reporter()
                            reporter.report_error(
                                error=e,
                                level=AlertLevel(error_severity.value),
                                context=context,
                                trace_id=trace_id
                            )
                        except Exception:
                            pass
                    
                    if kwargs.get('retry_on_error', False) and attempt < max_retries:
                        import asyncio
                        await asyncio.sleep(retry_delay)
                        continue
                    
                    if return_on_error is not None:
                        return return_on_error
                    raise
        
        return wrapper
    return decorator
