#!/usr/bin/env python3
"""
分布式追踪模块
基于现有日志系统的增强实现

功能:
- 为每个操作生成唯一的 Trace ID
- 追踪完整的执行链路
- 记录操作耗时和错误信息
"""

import uuid
import logging
import time
from typing import Optional
from contextvars import ContextVar

logger = logging.getLogger(__name__)

# 使用 ContextVar 实现线程安全的上下文管理
_current_trace_id: ContextVar[Optional[str]] = ContextVar('trace_id', default=None)

class TraceContext:
    """追踪上下文管理器
    
    为每个操作生成唯一的 Trace ID，
    追踪完整的执行链路。
    
    使用示例:
        with TraceContext("DigitalLife", "chat") as ctx:
            logger.info(f"[{ctx.trace_id}] 开始处理")
            # ... 业务逻辑 ...
    
    输出示例:
        [abc123def456] START DigitalLife.chat
        [abc123def456] END DigitalLife.chat (duration=150.30ms)
    """
    
    def __init__(self, service_name: str, operation: str):
        """
        初始化追踪上下文
        
        Args:
            service_name: 服务名称 (如: DigitalLife, VectorMemory)
            operation: 操作名称 (如: chat, search, save)
        """
        self.service_name = service_name
        self.operation = operation
        self.trace_id: Optional[str] = None
        self.start_time: Optional[float] = None
    
    def __enter__(self):
        """进入追踪上下文"""
        # 生成或复用 Trace ID
        self.trace_id = _current_trace_id.get() or self._generate_trace_id()
        _current_trace_id.set(self.trace_id)
        self.start_time = time.time()
        
        # 打印开始日志
        logger.info(
            f"[{self.trace_id}] START {self.service_name}.{self.operation}",
            extra={
                'trace_id': self.trace_id,
                'service': self.service_name,
                'operation': self.operation,
                'event': 'start',
                'timestamp': self.start_time
            }
        )
        
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出追踪上下文"""
        duration = time.time() - self.start_time
        duration_ms = duration * 1000
        
        if exc_type:
            # 记录错误
            logger.error(
                f"[{self.trace_id}] ERROR {self.service_name}.{self.operation} "
                f"(duration={duration_ms:.2f}ms, error={exc_val})",
                extra={
                    'trace_id': self.trace_id,
                    'service': self.service_name,
                    'operation': self.operation,
                    'event': 'error',
                    'duration_ms': duration_ms,
                    'error': str(exc_val),
                    'error_type': exc_type.__name__ if exc_type else None
                }
            )
        else:
            # 记录成功
            logger.info(
                f"[{self.trace_id}] END {self.service_name}.{self.operation} "
                f"(duration={duration_ms:.2f}ms)",
                extra={
                    'trace_id': self.trace_id,
                    'service': self.service_name,
                    'operation': self.operation,
                    'event': 'end',
                    'duration_ms': duration_ms
                }
            )
        
        return False
    
    def _generate_trace_id(self) -> str:
        """生成16位十六进制 Trace ID
        
        Returns:
            16位十六进制字符串 (如: abc123def4567890)
        """
        return uuid.uuid4().hex[:16]
    
    @property
    def duration_ms(self) -> float:
        """获取当前持续时间（毫秒）
        
        Returns:
            持续时间（毫秒），如果未开始则返回0
        """
        if self.start_time:
            return (time.time() - self.start_time) * 1000
        return 0
    
    @property
    def duration_s(self) -> float:
        """获取当前持续时间（秒）
        
        Returns:
            持续时间（秒），如果未开始则返回0
        """
        return self.duration_ms / 1000

def get_trace_id() -> Optional[str]:
    """获取当前 Trace ID
    
    Returns:
        当前线程的 Trace ID，如果不存在则返回 None
    """
    return _current_trace_id.get()

def set_trace_id(trace_id: str):
    """手动设置 Trace ID
    
    用于在接收到外部请求时（如HTTP请求）设置 Trace ID
    
    Args:
        trace_id: 外部传入的 Trace ID
    """
    _current_trace_id.set(trace_id)

def trace(service: str, operation: str):
    """追踪装饰器
    
    为函数自动添加追踪功能。
    
    用法:
        @trace("DigitalLife", "chat")
        def chat(self, user_input: str):
            ...
    
    Args:
        service: 服务名称
        operation: 操作名称
    
    Returns:
        装饰器函数
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            with TraceContext(service, operation):
                return func(*args, **kwargs)
        return wrapper
    return decorator

# 辅助函数
def format_trace_log(trace_id: str, message: str, **kwargs) -> str:
    """格式化追踪日志消息
    
    Args:
        trace_id: Trace ID
        message: 日志消息
        **kwargs: 额外的键值对
    
    Returns:
        格式化后的日志消息
    """
    parts = [f"[{trace_id}] {message}"]
    for key, value in kwargs.items():
        parts.append(f"{key}={value}")
    return " ".join(parts)
