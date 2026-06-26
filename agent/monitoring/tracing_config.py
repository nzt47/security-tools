#!/usr/bin/env python3
"""
追踪模块配置文件（纯委托层）

【配置收拢说明】
本模块已收拢到 `agent.monitoring.observability_config`：
- `TracingConfig` 等价于 `observability_config.get_tracing_config_compat()`，
  内部委托到 `ObservabilityConfig`，保留原 `TracingConfig` 的全部对外接口。
- 配置优先级：TRACING_* 环境变量 > ObservabilityConfig 热配置 > 内置默认值
- 现有代码 `from agent.monitoring.tracing_config import tracing_config` 无需修改即可工作。

支持不同环境的日志级别、采样器配置和导出器配置切换。
"""

from typing import Any, Dict

# 委托到可观测性统一配置：保留原 TracingConfig 接口与行为
from agent.monitoring.observability_config import (
    get_tracing_config_compat as TracingConfig,
)

# 模块级单例：等价于原 `TracingConfig()`
# 此处保持惰性委托——每次属性访问都会读取 ObservabilityConfig 最新值，
# 因此运行时 `config.set("tracing.env", ...)` 可即时生效。
tracing_config = TracingConfig()


def setup_tracing_logging():
    """根据 tracing_config 配置追踪模块日志级别与 Handler"""
    import logging

    logger = logging.getLogger('agent.monitoring.tracing')
    logger.setLevel(tracing_config.get_logging_level())

    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.propagate = False


def get_sampler():
    """根据 tracing_config.sampler_type 构造 OpenTelemetry 采样器"""
    from opentelemetry.sdk.trace.sampling import (
        ALWAYS_ON, ALWAYS_OFF, TraceIdRatioBased, ParentBased
    )

    sampler_type = tracing_config.sampler_type

    if sampler_type == 'ALWAYS_ON':
        return ALWAYS_ON
    elif sampler_type == 'ALWAYS_OFF':
        return ALWAYS_OFF
    elif sampler_type == 'RATIO':
        return TraceIdRatioBased(tracing_config.sampler_ratio)
    elif sampler_type == 'PARENT_BASED':
        return ParentBased(ALWAYS_ON)
    elif sampler_type == 'PARENT_BASED_RATIO':
        return ParentBased(TraceIdRatioBased(tracing_config.sampler_ratio))
    else:
        return ALWAYS_ON


def get_custom_sampler(sampler_name: str = None):
    """获取自定义采样器（使用tracing_sampling模块）"""
    try:
        from .tracing_sampling import (
            get_sampling_manager,
            AlwaysOnSampler,
            AlwaysOffSampler,
            ProbabilitySampler,
            RequestTypeSampler,
            LatencyBasedSampler,
            ErrorBasedSampler,
            RateLimitedSampler,
            DynamicSampler,
            setup_default_samplers
        )

        setup_default_samplers()
        manager = get_sampling_manager()

        if sampler_name:
            sampler = manager.get_sampler(sampler_name)
            if sampler:
                return sampler

        return ProbabilitySampler(tracing_config.sampler_ratio)

    except ImportError:
        return get_sampler()


def get_exporter_config() -> Dict[str, Any]:
    """返回导出器配置字典"""
    return {
        'type': tracing_config.exporter_type,
        'endpoint': tracing_config.exporter_endpoint,
        'protocol': tracing_config.exporter_protocol
    }


# 环境变量说明：
# TRACING_ENV: 环境类型 (development/staging/production)
# TRACING_LOG_LEVEL: 日志级别 (DEBUG/INFO/WARN/ERROR/CRITICAL)
# TRACING_SAMPLER: 采样器类型 (ALWAYS_ON/ALWAYS_OFF/RATIO/PARENT_BASED/PARENT_BASED_RATIO)
# TRACING_SAMPLER_RATIO: 采样比例 (0.0-1.0, 仅RATIO类型有效)
# TRACING_SAMPLER_RATE_LIMIT: 速率限制 (每秒最大采样数)
# TRACING_EXPORTER: 导出器类型 (CONSOLE/OTLP/JAEGER/ZIPKIN)
# TRACING_EXPORTER_ENDPOINT: 导出器端点 (如 localhost:4317)
# TRACING_EXPORTER_PROTOCOL: 导出器协议 (GRPC/HTTP)
# TRACING_DATA_RETENTION_DAYS: 数据保留天数
# TRACING_CACHE_ENABLED: 是否启用缓存 (true/false)
# TRACING_CACHE_SIZE: 缓存大小 (默认4096)
# TRACING_ASYNC_ENABLED: 是否启用异步处理 (true/false)

# 使用示例：
#
# 开发环境（默认）:
#   python app.py
#
# 生产环境:
#   TRACING_ENV=production python app.py
#
# 自定义配置:
#   TRACING_LOG_LEVEL=DEBUG TRACING_SAMPLER=ALWAYS_ON python app.py
#
# 生产环境高采样:
#   TRACING_ENV=production TRACING_SAMPLER_RATIO=0.5 python app.py
#
# 指定OTLP端点:
#   TRACING_EXPORTER=OTLP TRACING_EXPORTER_ENDPOINT=otel-collector:4317 python app.py
