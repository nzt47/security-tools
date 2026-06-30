# 云枢智能体 - 可观测性模块功能说明

## 概述

本文档详细说明云枢智能体可观测性体系中各模块的功能、接口和使用方式。

---

## 目录

1. [追踪模块 (`tracing.py`)](#1-追踪模块-tracingpy)
2. [追踪配置模块 (`tracing_config.py`)](#2-追踪配置模块-tracing_configpy)
3. [采样模块 (`tracing_sampling.py`)](#3-采样模块-tracing_samplingpy)
4. [缓存模块 (`tracing_cache.py`)](#4-缓存模块-tracing_cachepy)
5. [指标模块 (`metrics.py`)](#5-指标模块-metricspy)
6. [日志系统 (`log_system/`)](#6-日志系统-log_system)
7. [告警模块](#7-告警模块)
8. [自愈模块 (`self_healer.py`)](#8-自愈模块-self_healerpy)
9. [装饰器模块 (`decorators.py`)](#9-装饰器模块-decoratorspy)

---

## 1. 追踪模块 (`tracing.py`)

### 1.1 核心功能

| 功能 | 描述 | 实现方式 |
|------|------|---------|
| Trace ID 生成 | 生成唯一的16位十六进制追踪ID | `uuid.uuid4().hex[:16]` |
| Span ID 生成 | 生成唯一的16位十六进制Span ID | `uuid.uuid4().hex[:16]` |
| 追踪上下文管理 | 管理线程安全的追踪上下文 | `contextvars.ContextVar` |
| OpenTelemetry集成 | 支持标准追踪协议 | OpenTelemetry SDK |
| 采样控制 | 支持多种采样策略 | 采样器管理器 |
| 上下文传播 | 跨服务追踪上下文传递 | HTTP Header |

### 1.2 关键类

#### TraceContext

追踪上下文管理器，用于创建和管理追踪Span。

**构造参数**:

| 参数 | 类型 | 说明 | 默认值 |
|------|------|------|--------|
| `service_name` | str | 服务名称 | - |
| `operation` | str | 操作名称 | - |
| `span_kind` | str | Span类型 | "internal" |
| `attributes` | Dict | 附加属性 | None |
| `sampler_name` | str | 采样器名称 | None |
| `skip_sampling` | bool | 是否跳过采样 | False |

**方法**:

| 方法 | 说明 |
|------|------|
| `add_event(name, attributes)` | 添加事件到Span |
| `set_attribute(key, value)` | 设置Span属性 |
| `duration_ms` | 获取持续时间（毫秒） |

**使用示例**:

```python
from agent.monitoring.tracing import TraceContext

with TraceContext("DigitalLife", "chat", span_kind="server") as ctx:
    ctx.set_attribute("user_id", "user123")
    ctx.add_event("message_received", {"length": 100})
    # 业务逻辑
```

### 1.3 核心函数

| 函数 | 说明 | 参数 | 返回值 |
|------|------|------|--------|
| `get_trace_id()` | 获取当前Trace ID | - | str or None |
| `get_span_id()` | 获取当前Span ID | - | str or None |
| `set_trace_id(trace_id)` | 设置Trace ID | trace_id: str | None |
| `set_span_id(span_id)` | 设置Span ID | span_id: str | None |
| `extract_trace_context(headers)` | 从HTTP头提取追踪上下文 | headers: Dict | Dict |
| `inject_trace_context()` | 生成追踪上下文请求头 | - | Dict |
| `diagnose_opentelemetry_config()` | 诊断OpenTelemetry配置 | - | Dict |
| `print_diagnosis_report()` | 打印诊断报告 | - | Dict |

---

## 2. 追踪配置模块 (`tracing_config.py`)

### 2.1 功能概述

管理追踪系统的配置参数，包括采样策略、导出器配置等。

### 2.2 配置项

| 配置项 | 环境变量 | 说明 | 默认值 |
|--------|---------|------|--------|
| `env` | `TRACING_ENV` | 运行环境 | "development" |
| `log_level` | `TRACING_LOG_LEVEL` | 日志级别 | "DEBUG" |
| `sampler_type` | `TRACING_SAMPLER` | 采样器类型 | "ALWAYS_ON" |
| `sampler_ratio` | `TRACING_SAMPLER_RATIO` | 采样比例 | 1.0 |
| `sampler_rate_limit` | `TRACING_SAMPLER_RATE_LIMIT` | 速率限制 | 100 |
| `exporter_type` | `TRACING_EXPORTER` | 导出器类型 | "CONSOLE" |
| `exporter_endpoint` | `TRACING_EXPORTER_ENDPOINT` | 导出器端点 | "localhost:4317" |
| `exporter_protocol` | `TRACING_EXPORTER_PROTOCOL` | 导出器协议 | "GRPC" |

### 2.3 支持的采样器类型

| 类型 | 说明 | 适用场景 |
|------|------|---------|
| `ALWAYS_ON` | 采样所有Span | 开发/测试 |
| `ALWAYS_OFF` | 不采样任何Span | 性能测试 |
| `RATIO` | 概率采样 | 高流量生产环境 |
| `PARENT_BASED` | 基于父Span采样 | 分布式追踪 |
| `PARENT_BASED_RATIO` | 基于父Span的概率采样 | 生产环境推荐 |

---

## 3. 采样模块 (`tracing_sampling.py`)

### 3.1 功能概述

提供采样决策逻辑，支持多种采样策略。

### 3.2 采样决策流程

```
请求进入 → 获取Trace ID → 判断采样器类型 → 
    ↓
采样决策 → 记录采样原因 → 返回采样结果
```

### 3.3 采样管理器

**核心方法**:

| 方法 | 说明 |
|------|------|
| `sample(trace_id, sampler_name, **kwargs)` | 执行采样决策 |
| `get_sampling_manager()` | 获取采样管理器实例 |

---

## 4. 缓存模块 (`tracing_cache.py`)

### 4.1 功能概述

提供追踪数据的缓存和异步写入能力，优化性能。

### 4.2 核心功能

| 功能 | 说明 |
|------|------|
| Span对象池 | 复用Span对象，减少内存分配 |
| 异步写入 | 异步持久化追踪数据 |
| 缓存管理 | 管理追踪数据缓存 |

### 4.3 缓存管理器

**方法**:

| 方法 | 说明 |
|------|------|
| `acquire_span()` | 从对象池获取Span |
| `release_span(span)` | 释放Span到对象池 |
| `add_span(trace_id, span_data)` | 添加Span到缓存 |
| `async_write(span_data)` | 异步写入Span数据 |

---

## 5. 指标模块 (`metrics.py`)

### 5.1 功能概述

收集和导出系统运行指标，支持Prometheus格式。

### 5.2 指标类型

| 类型 | 说明 | 示例 |
|------|------|------|
| Counter | 计数器，单调递增 | `yunshu_http_requests_total` |
| Histogram | 直方图，统计分布 | `yunshu_http_request_duration_seconds` |
| Gauge | 仪表盘，可增可减 | `yunshu_memory_usage_percent` |
| Summary | 摘要统计 | `yunshu_llm_response_time` |

### 5.3 预设指标

| 指标名 | 类型 | 说明 |
|--------|------|------|
| `yunshu_http_requests_total` | Counter | HTTP请求总数 |
| `yunshu_http_request_duration_seconds` | Histogram | HTTP请求耗时 |
| `yunshu_llm_calls_total` | Counter | LLM调用次数 |
| `yunshu_llm_token_usage_total` | Counter | LLM Token使用量 |
| `yunshu_cpu_usage_percent` | Gauge | CPU使用率 |
| `yunshu_memory_usage_percent` | Gauge | 内存使用率 |
| `yunshu_security_blocks_total` | Counter | 安全拦截次数 |
| `yunshu_tool_calls_total` | Counter | 工具调用次数 |

### 5.4 指标端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/metrics` | GET | Prometheus格式指标 |
| `/api/diagnostics/metrics` | GET | JSON格式运行时指标 |

---

## 6. 日志系统 (`log_system/`)

### 6.1 模块结构

```
log_system/
├── __init__.py
├── analyzer.py      # 日志分析
├── collectors.py    # 日志收集
├── dashboard.py     # 日志仪表盘
├── emoji_map.py     # emoji映射
├── formatter.py     # 日志格式化
├── handlers.py      # 日志处理器
├── models.py        # 数据模型
├── safe_logger.py   # 安全日志记录器
└── storage.py       # 日志存储
```

### 6.2 SafeLogger

安全日志记录器，支持敏感数据过滤。

**特性**:
- JSON格式结构化输出
- 自动关联Trace ID
- 敏感数据自动脱敏
- 支持日志级别控制

**使用示例**:

```python
from agent.log_system.safe_logger import SafeLogger

logger = SafeLogger("DigitalLife")
logger.info("用户登录", {"user_id": "user123", "email": "user@example.com"})
```

### 6.3 日志查询端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/diagnostics/logs` | GET | 获取最近日志 |
| `/api/observability/logs` | GET | 查询日志（支持过滤） |
| `/api/observability/logs` | POST | 推送日志 |
| `/api/observability/logs/stream` | GET | 实时日志流(SSE) |
| `/api/observability/logs/labels` | GET | 获取日志标签 |

---

## 7. 告警模块

### 7.1 模块结构

```
agent/monitoring/
├── alert_manager.py    # 告警管理器
├── alert_evaluator.py  # 告警评估器
└── alert_notifier.py   # 告警通知器
```

### 7.2 AlertManager

**职责**: 管理告警规则的创建、更新、删除和查询。

**方法**:

| 方法 | 说明 |
|------|------|
| `create_rule(rule)` | 创建告警规则 |
| `update_rule(rule_id, rule)` | 更新告警规则 |
| `delete_rule(rule_id)` | 删除告警规则 |
| `get_rules()` | 获取所有告警规则 |
| `get_rule(rule_id)` | 获取单个告警规则 |

### 7.3 AlertEvaluator

**职责**: 评估告警表达式，判断是否触发告警。

**方法**:

| 方法 | 说明 |
|------|------|
| `evaluate(expr)` | 评估告警表达式 |
| `validate(expr)` | 验证表达式语法 |

### 7.4 告警规则结构

```yaml
alert: HighErrorRate
expr: sum(rate(yunshu_http_requests_total{status=~"5.."}[5m])) / sum(rate(yunshu_http_requests_total[5m])) > 0.1
for: 5m
labels:
  severity: critical
annotations:
  summary: "高错误率告警"
  description: "错误率超过10%"
```

---

## 8. 自愈模块 (`self_healer.py`)

### 8.1 功能概述

自动检测系统健康状态并执行修复操作。

### 8.2 自愈流程

```
定期检查 → 健康评估 → 发现问题 → 自动修复 → 验证恢复
```

### 8.3 健康评估维度

| 维度 | 检查项 | 修复策略 |
|------|--------|---------|
| 内存 | 内存使用率 | 清理缓存 |
| 磁盘 | 磁盘使用率 | 删除旧日志 |
| 网络 | 网络连接 | 重连/切换 |
| 服务 | 服务状态 | 重启服务 |
| 线程 | 线程池状态 | 扩容/收缩 |

### 8.4 API端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/health` | GET | 快速健康检查 |
| `/api/status` | GET | 系统状态摘要 |
| `/api/heartbeat` | GET | 心跳检查 |
| `/api/diagnostics/health` | GET | 综合健康检查 |

---

## 9. 装饰器模块 (`decorators.py`)

### 9.1 功能概述

提供无侵入式的追踪和监控装饰器。

### 9.2 装饰器列表

| 装饰器 | 说明 | 适用场景 |
|--------|------|---------|
| `@trace(service, operation, span_kind)` | 同步函数追踪 | 同步业务方法 |
| `@async_trace(service, operation, span_kind)` | 异步函数追踪 | 异步业务方法 |
| `@log_method_call` | 方法调用日志 | 需要记录调用的方法 |
| `@measure_performance` | 性能度量 | 需要监控性能的方法 |

### 9.3 使用示例

```python
from agent.monitoring.decorators import trace, async_trace

@trace("DigitalLife", "chat", span_kind="server")
def handle_chat(user_input):
    # 业务逻辑
    pass

@async_trace("LLMService", "completion", span_kind="client")
async def call_llm(prompt):
    # 异步LLM调用
    pass
```

---

**文档版本**: v1.0  
**最后更新**: 2026年6月  
**适用版本**: 云枢智能体 v2.x