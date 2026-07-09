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
        # span_id：每个 with 块生成新的 span_id（同一 trace 内多个 span）
        self.span_id: Optional[str] = None
        self.start_time: Optional[float] = None
        # 保存进入前的 trace_id / span_id，用于 __exit__ 恢复（栈式管理的核心）
        self._old_trace_id: Optional[str] = None
        self._old_span_id: Optional[str] = None
    
    def __enter__(self):
        """进入追踪上下文"""
        # 栈式管理第一步：保存进入前的 trace_id / span_id（可能为 None，也可能为外层/外部设置的值）
        self._old_trace_id = _current_trace_id.get()
        self._old_span_id = _current_span_id.get()
        # trace_id 复用外层（嵌套/外部传播场景），或生成新 ID（独立调用场景）
        self.trace_id = self._old_trace_id or self._generate_trace_id()
        # span_id 总是生成新值：每个 with 块代表一个独立 span，即便嵌套也是不同的 span
        self.span_id = self._generate_span_id()
        _current_trace_id.set(self.trace_id)
        _current_span_id.set(self.span_id)
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

        # 栈式管理第二步：恢复进入前的 trace_id / span_id 状态
        # - 嵌套退出后回到外层值，保证外层继续使用同一上下文
        # - 最外层退出后回到 None（或外部 set_*_id 设置的值），避免上下文泄漏
        # 这是保证不变量（__exit__ 后 get_*_id() == 进入前的值）的关键
        _current_trace_id.set(self._old_trace_id)
        _current_span_id.set(self._old_span_id)

        return False
    
    def _generate_trace_id(self) -> str:
        """生成16位十六进制 Trace ID

        Returns:
            16位十六进制字符串 (如: abc123def4567890)
        """
        return uuid.uuid4().hex[:16]

    def _generate_span_id(self) -> str:
        """生成16位十六进制 Span ID

        与 trace_id 区别：同一 trace 内可有多个 span，每次进入 with 块生成新 span_id。

        Returns:
            16位十六进制字符串 (如: 1234567812345678)
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


def extract_trace_context(headers: Optional[dict] = None) -> dict:
    """提取追踪上下文

    两种模式：
    1. 无参数：返回当前线程上下文 {"trace_id": ..., "span_id": ...}，未设置字段为 None
    2. 传 headers：从 HTTP 请求头解析跨服务传播的上下文
       支持 W3C traceparent 和 Jaeger uber-trace-id 两种格式
       解析失败（空 headers、格式非法）返回 {}（空字典）

    Args:
        headers: 可选的 HTTP 请求头字典；不传则返回当前线程上下文

    Returns:
        包含 trace_id / span_id 的字典
    """
    if headers is not None:
        return _extract_from_headers(headers)
    return {
        "trace_id": get_trace_id(),
        "span_id": get_span_id(),
    }


def _extract_from_headers(headers: dict) -> dict:
    """从 HTTP 请求头解析追踪上下文（支持 W3C traceparent 和 Jaeger uber-trace-id）

    大小写不敏感：traceparent / Traceparent / TRACEPARENT 均可。

    Returns:
        解析成功返回 {"trace_id": ..., "span_id": ...}，失败返回 {}
    """
    if not isinstance(headers, dict) or not headers:
        return {}

    # 大小写不敏感查找
    headers_lower = {k.lower(): v for k, v in headers.items()}

    # 优先 W3C traceparent
    traceparent = headers_lower.get("traceparent")
    if traceparent:
        parsed = _parse_w3c_traceparent(traceparent)
        if parsed:
            return parsed

    # 兼容 Jaeger uber-trace-id: {trace_id}:{span_id}:{parent_span_id}:{flags}
    uber = headers_lower.get("uber-trace-id")
    if uber:
        parsed = _parse_jaeger_trace_id(uber)
        if parsed:
            return parsed

    return {}


def _parse_w3c_traceparent(traceparent: str) -> Optional[dict]:
    """解析 W3C traceparent header

    格式：version-trace_id-span_id-flags（用 "-" 分隔的 4 段）
    - version: 2 位十六进制，本实现仅支持 "00"
    - trace_id: 32 位十六进制（W3C 标准 128 位）或 16 位（64 位兼容）
    - span_id: 16 位十六进制
    - flags: 2 位十六进制

    Returns:
        {"trace_id": ..., "span_id": ...}；格式非法返回 None
    """
    if not isinstance(traceparent, str):
        return None
    parts = traceparent.split("-")
    if len(parts) != 4:
        return None
    version, trace_id, span_id, flags = parts
    if version != "00":
        return None
    # trace_id 必须是 16 或 32 位十六进制
    if not _is_hex(trace_id) or len(trace_id) not in (16, 32):
        return None
    # span_id 必须是 16 位十六进制
    if not _is_hex(span_id) or len(span_id) != 16:
        return None
    # flags 必须是 2 位十六进制
    if not _is_hex(flags) or len(flags) != 2:
        return None
    return {"trace_id": trace_id, "span_id": span_id}


def _parse_jaeger_trace_id(uber: str) -> Optional[dict]:
    """解析 Jaeger uber-trace-id header

    格式：{trace_id}:{span_id}:{parent_span_id}:{flags}（用 ":" 分隔的 4 段）

    Returns:
        {"trace_id": ..., "span_id": ...}；格式非法返回 None
    """
    if not isinstance(uber, str):
        return None
    parts = uber.split(":")
    if len(parts) != 4:
        return None
    trace_id, span_id, _parent, _flags = parts
    if not trace_id or not span_id:
        return None
    # trace_id 必须是 16 或 32 位十六进制
    if not _is_hex(trace_id) or len(trace_id) not in (16, 32):
        return None
    # span_id 兼容 8 位（Jaeger 旧版）或 16 位
    if not _is_hex(span_id) or len(span_id) not in (8, 16):
        return None
    return {"trace_id": trace_id, "span_id": span_id}


def _is_hex(s: str) -> bool:
    """判断字符串是否为非空十六进制"""
    if not s:
        return False
    try:
        int(s, 16)
        return True
    except ValueError:
        return False


def inject_trace_context(context: Optional[dict] = None) -> Optional[dict]:
    """注入或序列化追踪上下文

    两种模式：
    1. 无参数：生成跨服务传播用的 HTTP headers dict（含 W3C traceparent）
       若当前线程上下文不完整（trace_id 或 span_id 缺失）返回 {}（空字典）
    2. 传 context：将 context 注入当前线程（用于接收跨服务请求时还原上下文）
       缺失字段保持原值，返回 None

    Args:
        context: 可选的含 trace_id / span_id 的字典

    Returns:
        无参数模式返回 headers dict；传参数模式返回 None
    """
    if context is not None:
        # 旧行为：注入到当前线程
        if not isinstance(context, dict):
            return None
        if "trace_id" in context:
            set_trace_id(context.get("trace_id"))
        if "span_id" in context:
            set_span_id(context.get("span_id"))
        return None

    # 新行为：生成 W3C traceparent headers
    trace_id = get_trace_id()
    span_id = get_span_id()
    if not trace_id or not span_id:
        return {}
    return {"traceparent": f"00-{trace_id}-{span_id}-01"}

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


def diagnose_opentelemetry_config() -> dict:
    """诊断 OpenTelemetry 配置状态

    返回包含以下字段的 dict：
    - opentelemetry_available: bool — opentelemetry 包是否已安装
    - tracer_initialized: bool — 是否已存在已初始化的全局 tracer
    - tracer_provider_set: bool — TracerProvider 是否已注册
    - sdk_version: Optional[str] — opentelemetry-sdk 版本（未安装为 None）
    - detection_context: dict — 当前 trace 上下文快照
    - issues: list[str] — 发现的问题列表（空列表表示无问题）

    边界显性化：任何异常都被捕获并记入 issues，绝不向上抛出。
    """
    issues: list = []
    sdk_version = None
    otel_available = False
    tracer_initialized = False
    provider_set = False

    try:
        import opentelemetry
        otel_available = True
        try:
            import opentelemetry.sdk as otel_sdk
            sdk_version = getattr(otel_sdk, "__version__", None)
        except ImportError:
            issues.append("opentelemetry-sdk 未安装（仅 API 可用，无导出能力）")

        try:
            from opentelemetry.trace import get_tracer_provider
            provider = get_tracer_provider()
            provider_set = provider is not None
            # TracerProvider 类名包含 "Proxy" 通常表示未真正初始化 SDK
            cls_name = type(provider).__name__
            if "Proxy" in cls_name:
                issues.append(f"TracerProvider 为默认 Proxy 实现 ({cls_name})，"
                              "可能未调用 TracerProviderResourceManager 初始化")
            else:
                tracer_initialized = True
        except Exception as exc:
            issues.append(f"获取 TracerProvider 失败: {type(exc).__name__}: {exc}")
    except ImportError:
        issues.append("opentelemetry 包未安装")

    return {
        "opentelemetry_available": otel_available,
        "tracer_initialized": tracer_initialized,
        "tracer_provider_set": provider_set,
        "sdk_version": sdk_version,
        "detection_context": {
            "trace_id": get_trace_id(),
            "span_id": get_span_id(),
        },
        "issues": issues,
    }


def init_observability(service_name: str = "yunshu-agent") -> bool:
    """初始化 OpenTelemetry 可观测性

    创建真正的 TracerProvider 并设置为全局，让 diagnose_opentelemetry_config()
    能检测到已初始化的 tracer。默认情况下 opentelemetry.trace.get_tracer_provider()
    返回的是 ProxyTracerProvider（仅 API 层占位），tracer_initialized 会是 False。

    幂等：重复调用不会重复初始化。

    Args:
        service_name: 服务名称（用于 resource 属性，便于在追踪后端识别）

    Returns:
        True 表示 OpenTelemetry SDK 可用且已初始化（或之前已初始化）；
        False 表示 opentelemetry-sdk 未安装或初始化失败。
    """
    try:
        from opentelemetry.trace import get_tracer_provider, set_tracer_provider
        provider = get_tracer_provider()
        cls_name = type(provider).__name__
        # 已是 SDK 实现（非 Proxy），无需重复初始化
        if "Proxy" not in cls_name:
            return True
        # 创建真正的 TracerProvider 并设置为全局
        try:
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            resource = Resource.create({"service.name": service_name})
            real_provider = TracerProvider(resource=resource)
            set_tracer_provider(real_provider)
            logger.info("OpenTelemetry 已初始化 (service=%s)", service_name)
            return True
        except ImportError:
            # opentelemetry-sdk 未安装，仅 API 可用 — 无法真正初始化
            logger.warning("opentelemetry-sdk 未安装，init_observability 无法初始化 TracerProvider")
            return False
    except Exception as exc:
        logger.warning("init_observability 失败: %s: %s", type(exc).__name__, exc)
        return False


def print_diagnosis_report() -> None:
    """打印 OpenTelemetry 配置诊断报告到 stdout

    用于排查追踪系统初始化问题。函数无返回值，所有输出直接 print。
    """
    diagnosis = diagnose_opentelemetry_config()
    print("\n" + "=" * 60)
    print("📊 OpenTelemetry 配置诊断报告")
    print("=" * 60)
    print(f"  opentelemetry_available: {diagnosis['opentelemetry_available']}")
    print(f"  tracer_initialized     : {diagnosis['tracer_initialized']}")
    print(f"  tracer_provider_set   : {diagnosis['tracer_provider_set']}")
    print(f"  sdk_version            : {diagnosis['sdk_version']}")
    ctx = diagnosis["detection_context"]
    print(f"  当前 trace_id          : {ctx['trace_id']}")
    print(f"  当前 span_id           : {ctx['span_id']}")
    if diagnosis["issues"]:
        print("\n  ⚠️ 发现问题:")
        for issue in diagnosis["issues"]:
            print(f"    - {issue}")
    else:
        print("\n  ✅ 未发现问题")
    print("=" * 60 + "\n")


def print_context_diagnosis() -> None:
    """打印当前追踪上下文诊断信息到 stdout

    用于排查上下文传播问题。函数无返回值。
    """
    print("\n" + "-" * 40)
    print("🔍 当前追踪上下文")
    print("-" * 40)
    print(f"  trace_id: {get_trace_id()}")
    print(f"  span_id : {get_span_id()}")
    health = check_tracing_health()
    print(f"  健康状态: {health['status']}")
    print(f"  trace_id_set: {health['trace_id_set']}")
    print(f"  span_id_set : {health['span_id_set']}")
    loss_scenarios = detect_context_loss_scenarios()
    if loss_scenarios:
        print(f"  ⚠️ 上下文丢失场景: {loss_scenarios}")
    print("-" * 40 + "\n")


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


def record_request_metrics(method: str, path: str, status_code: int, duration_ms: float) -> None:
    """记录 HTTP 请求指标（结构化日志，best-effort）"""
    logger.debug(
        '{"trace_id": "%s", "module_name": "tracing", "action": "record_request_metrics", '
        '"method": "%s", "path": "%s", "status_code": %d, "duration_ms": %.2f}',
        get_trace_id() or '', method, path, status_code, duration_ms
    )


def get_logger_with_context(name: str) -> logging.Logger:
    """获取带追踪上下文的 logger（返回标准 logger，trace_id 通过 filter/processor 注入）"""
    return logging.getLogger(name)
