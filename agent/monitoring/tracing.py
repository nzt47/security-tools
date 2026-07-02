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
import json
import time
from typing import Optional
from contextvars import ContextVar

logger = logging.getLogger(__name__)

# 使用 ContextVar 实现线程安全的上下文管理
_current_trace_id: ContextVar[Optional[str]] = ContextVar('trace_id', default=None)
_current_span_id: ContextVar[Optional[str]] = ContextVar('span_id', default=None)

class TraceContext:
    """追踪上下文管理器（栈式 trace_id 管理）

    核心设计：trace_id 栈式保存与恢复
    ----------------------------------------------
    通过 ContextVar (`_current_trace_id`) 在线程/协程内传递 trace_id，
    并在 `__enter__` 保存旧值、`__exit__` 恢复旧值，形成"栈式"管理，
    从而同时满足以下四类场景：

    1. 独立调用唯一性：多次独立 `with TraceContext(...)` 调用，
       每次进入时旧值为 None → 生成新 trace_id；退出时恢复为 None。
       ⇒ 多次调用产生不同 trace_id。

    2. 嵌套上下文共享：外层 `with` 内再开 `with`，
       内层进入时旧值=外层 ID → 复用外层 ID；退出时恢复为外层 ID。
       ⇒ 内外层共享同一 trace_id，构成完整调用链。

    3. 外部传播：调用方通过 `set_trace_id(...)` 预先设置 ID，
       `with TraceContext(...)` 进入时旧值=外部 ID → 复用；退出时恢复为外部 ID。
       ⇒ 不破坏调用方已建立的追踪上下文。

    4. 异常安全：无论 `with` 块内是否抛异常，`__exit__` 都会执行恢复，
       ⇒ 不会因异常导致 trace_id 泄漏到后续逻辑。

    不变量（Invariant）：
        - `__exit__` 执行后，`get_trace_id()` 的返回值等于进入前的值。
        - 嵌套场景下，内层退出不影响外层继续使用同一 trace_id。

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
        # 保存进入前的 trace_id，用于 __exit__ 恢复（栈式管理的核心）
        self._old_trace_id: Optional[str] = None
    
    def __enter__(self):
        """进入追踪上下文"""
        # 栈式管理第一步：保存进入前的 trace_id（可能为 None，也可能为外层/外部设置的 ID）
        self._old_trace_id = _current_trace_id.get()
        # 复用已有 trace_id（嵌套/外部传播场景），或生成新 ID（独立调用场景）
        self.trace_id = self._old_trace_id or self._generate_trace_id()
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

        # 栈式管理第二步：恢复进入前的 trace_id 状态
        # - 嵌套退出后回到外层 ID，保证外层继续使用同一 trace_id
        # - 最外层退出后回到 None（或外部 set_trace_id 设置的值），避免 trace_id 泄漏
        # 这是保证不变量（__exit__ 后 get_trace_id() == 进入前的值）的关键
        _current_trace_id.set(self._old_trace_id)

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


def get_span_id() -> Optional[str]:
    """获取当前 Span ID（同一 trace 内某段操作的标识）"""
    return _current_span_id.get()


def set_span_id(span_id: Optional[str]) -> None:
    """手动设置 Span ID

    Args:
        span_id: Span ID；传 None 清除当前 span
    """
    _current_span_id.set(span_id)


def extract_trace_context() -> dict:
    """提取当前线程的追踪上下文（用于跨服务传播前序列化）

    Returns:
        {"trace_id": ..., "span_id": ...}；未设置的字段为 None
    """
    return {
        "trace_id": get_trace_id(),
        "span_id": get_span_id(),
    }


def inject_trace_context(context: dict) -> None:
    """将外部传入的追踪上下文注入当前线程（用于接收跨服务请求时还原上下文）

    Args:
        context: 含 trace_id / span_id 的字典；缺失字段保持原值
    """
    if not isinstance(context, dict):
        return
    if "trace_id" in context:
        set_trace_id(context.get("trace_id"))
    if "span_id" in context:
        set_span_id(context.get("span_id"))

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
            "module_name": "tracing",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise


# ── 跨服务上下文传播辅助（与 test_tracing_coverage 对齐） ─────


class TraceContextError(Exception):
    """追踪上下文相关错误"""

    def __init__(self, message: str, error_code: str = "TRACE_CONTEXT_ERROR"):
        super().__init__(message)
        self.error_code = error_code


class InvalidTraceParentError(TraceContextError):
    """traceparent 头格式非法"""

    def __init__(self, message: str = "Invalid traceparent header"):
        super().__init__(message, error_code="INVALID_TRACE_PARENT")


def safe_extract_trace_context() -> dict:
    """安全提取追踪上下文（异常时返回空字典，不抛错）"""
    try:
        return extract_trace_context()
    except Exception as exc:
        logger.debug("safe_extract_trace_context 失败: %s", exc)
        return {"trace_id": None, "span_id": None}


def safe_inject_trace_context(context: dict) -> None:
    """安全注入追踪上下文（异常时仅记录日志，不抛错）"""
    try:
        inject_trace_context(context)
    except Exception as exc:
        logger.debug("safe_inject_trace_context 失败: %s", exc)


def check_tracing_health() -> dict:
    """检查追踪系统健康度（用于健康检查端点）"""
    return {
        "status": "healthy",
        "trace_id_set": get_trace_id() is not None,
        "span_id_set": get_span_id() is not None,
    }


def validate_trace_context(context: dict) -> bool:
    """校验追踪上下文字典是否合法"""
    if not isinstance(context, dict):
        return False
    trace_id = context.get("trace_id")
    if trace_id is not None and not isinstance(trace_id, str):
        return False
    span_id = context.get("span_id")
    if span_id is not None and not isinstance(span_id, str):
        return False
    return True


def detect_context_loss_scenarios() -> list:
    """检测可能导致上下文丢失的场景（返回场景列表）"""
    scenarios = []
    if get_trace_id() is None:
        scenarios.append("trace_id_missing")
    if get_span_id() is None:
        scenarios.append("span_id_missing")
    return scenarios


def capture_context() -> dict:
    """捕获当前上下文快照（与 extract_trace_context 等价，语义化命名）"""
    return extract_trace_context()


def restore_context(context: dict) -> None:
    """从快照恢复上下文（与 inject_trace_context 等价，语义化命名）"""
    inject_trace_context(context)


def run_with_context(context: dict, func, *args, **kwargs):
    """在指定上下文中执行函数（执行后恢复原上下文）"""
    old = extract_trace_context()
    try:
        inject_trace_context(context)
        return func(*args, **kwargs)
    finally:
        inject_trace_context(old)


def is_opentelemetry_available() -> bool:
    """检测 OpenTelemetry 是否可用（未安装时返回 False）"""
    try:
        import opentelemetry  # noqa: F401
        return True
    except ImportError:
        return False


# ── Trace 存储（与 observability.subscriber 对齐的轻量实现） ───
def _get_trace_storage_singleton():
    """惰性加载 TraceStorage 单例（避免循环导入）"""
    from agent.observability.subscriber import TraceStore
    return TraceStore()


_trace_storage_singleton = None
_trace_storage_lock = __import__('threading').Lock()


def get_trace_storage():
    """获取全局 TraceStorage 单例"""
    global _trace_storage_singleton
    with _trace_storage_lock:
        if _trace_storage_singleton is None:
            _trace_storage_singleton = _get_trace_storage_singleton()
        return _trace_storage_singleton


# TraceStorage 作为 TraceStore 的别名（与测试导入对齐）
from agent.observability.subscriber import TraceStore as TraceStorage, TraceRecord  # noqa: E402


def record_trace_span(trace_id: str, span_name: str, **fields) -> None:
    """记录一个 trace span 到存储（best-effort，失败仅日志）"""
    try:
        storage = get_trace_storage()
        if hasattr(storage, 'add_span'):
            storage.add_span(trace_id, span_name, **fields)
    except Exception as exc:
        logger.debug("record_trace_span 失败: %s", exc)


def get_recent_traces(limit: int = 20) -> list:
    """获取最近的 trace 列表"""
    try:
        storage = get_trace_storage()
        if hasattr(storage, 'recent'):
            return storage.recent(limit)
        if hasattr(storage, '_traces'):
            items = list(storage._traces.values())
            return items[-limit:]
    except Exception as exc:
        logger.debug("get_recent_traces 失败: %s", exc)
    return []


def get_trace_detail(trace_id: str):
    """获取指定 trace 的详情"""
    try:
        storage = get_trace_storage()
        if hasattr(storage, 'get'):
            return storage.get(trace_id)
        if hasattr(storage, '_traces'):
            return storage._traces.get(trace_id)
    except Exception as exc:
        logger.debug("get_trace_detail 失败: %s", exc)
    return None


def get_decision_sequence(trace_id: str) -> list:
    """获取指定 trace 的决策序列（用于回放）"""
    detail = get_trace_detail(trace_id)
    if detail is None:
        return []
    spans = getattr(detail, 'spans', []) or []
    return [
        {
            "span_id": getattr(s, 'span_id', None),
            "name": getattr(s, 'name', ''),
            "start_time": getattr(s, 'start_time', 0),
            "end_time": getattr(s, 'end_time', None),
            "status": getattr(s, 'status', 'unknown'),
        }
        for s in spans
    ]
