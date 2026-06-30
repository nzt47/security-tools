# 云枢智能体 - 关键技术决策记录

## 概述

本文档记录云枢智能体可观测性体系的关键技术决策，采用 ADR (Architecture Decision Record) 格式，便于后续维护人员理解设计决策的背景和理由。

---

## ADR-001: 选择 OpenTelemetry 作为分布式追踪标准

### 决策记录

| 属性 | 内容 |
|------|------|
| **标题** | 选择 OpenTelemetry 作为分布式追踪标准 |
| **状态** | 已实施 |
| **创建日期** | 2026-06-01 |
| **作者** | 架构团队 |
| **相关模块** | `agent/monitoring/tracing.py` |

### 背景

在构建云枢智能体的可观测性体系时，需要选择一个标准的分布式追踪方案。评估了多种方案：

| 方案 | 优点 | 缺点 |
|------|------|------|
| **OpenTelemetry** | 行业标准、多语言支持、生态成熟 | 相对较新 |
| **Jaeger** | 成熟稳定、可视化优秀 | 厂商锁定 |
| **Zipkin** | 轻量、易于集成 | 功能相对简单 |
| **自研方案** | 完全可控 | 重复造轮子、维护成本高 |

### 决策

选择 **OpenTelemetry** 作为分布式追踪标准，原因如下：

1. **行业标准**: OpenTelemetry 是 CNCF 孵化项目，已成为行业事实标准
2. **多协议支持**: 支持 OTLP、Jaeger、Zipkin 等多种协议
3. **丰富的生态**: 与 Prometheus、Grafana、Jaeger 等无缝集成
4. **未来兼容性**: 避免厂商锁定，便于未来扩展

### 影响

- 需要引入 OpenTelemetry SDK 依赖
- 需要配置多种导出器（Console、OTLP、Jaeger）
- 需要实现采样策略适配

### 实施

**依赖配置** (`requirements.txt`):
```
opentelemetry-api>=1.20.0
opentelemetry-sdk>=1.20.0
opentelemetry-exporter-otlp-proto-grpc>=1.20.0
opentelemetry-exporter-jaeger-thrift>=1.20.0
```

**核心代码位置**:
- `agent/monitoring/tracing.py` - 追踪模块
- `agent/monitoring/tracing_config.py` - 追踪配置
- `agent/monitoring/tracing_sampling.py` - 采样策略

---

## ADR-002: 采用 PARENT_BASED_RATIO 作为默认采样策略

### 决策记录

| 属性 | 内容 |
|------|------|
| **标题** | 采用 PARENT_BASED_RATIO 作为默认采样策略 |
| **状态** | 已实施 |
| **创建日期** | 2026-06-05 |
| **作者** | 架构团队 |
| **相关模块** | `agent/monitoring/tracing_sampling.py` |

### 背景

在生产环境中，全量采样会产生大量追踪数据，影响性能和存储成本。需要选择合适的采样策略。

### 评估的采样策略

| 策略 | 说明 | 适用场景 |
|------|------|---------|
| `ALWAYS_ON` | 采样所有 Span | 开发/测试 |
| `ALWAYS_OFF` | 不采样任何 Span | 性能测试 |
| `RATIO` | 固定概率采样 | 高流量场景 |
| `PARENT_BASED` | 基于父 Span 决定 | 分布式追踪 |
| `PARENT_BASED_RATIO` | 父 Span + 概率混合 | 生产环境推荐 |

### 决策

选择 **PARENT_BASED_RATIO** 作为默认采样策略：

1. **保证链路完整性**: 如果父 Span 被采样，子 Span 也会被采样
2. **控制数据量**: 根 Span 按比例采样，控制总体数据量
3. **灵活配置**: 可通过环境变量调整采样比例

### 配置

**环境变量**:
- `TRACING_SAMPLER=PARENT_BASED_RATIO`
- `TRACING_SAMPLER_RATIO=0.1` (生产环境默认 10%)
- `TRACING_SAMPLER_RATE_LIMIT=100` (每秒最多采样 100 个)

### 实施

**采样决策流程**:
```
请求进入 → 检查是否有父 Span → 
    是 → 继承父 Span 的采样决策
    否 → 按配置比例随机采样
```

---

## ADR-003: 使用 contextvars 实现线程安全的上下文传递

### 决策记录

| 属性 | 内容 |
|------|------|
| **标题** | 使用 contextvars 实现线程安全的上下文传递 |
| **状态** | 已实施 |
| **创建日期** | 2026-06-08 |
| **作者** | 架构团队 |
| **相关模块** | `agent/monitoring/tracing.py` |

### 背景

在多线程/协程环境中，需要安全地传递追踪上下文（Trace ID、Span ID）。

### 评估方案

| 方案 | 优点 | 缺点 |
|------|------|------|
| **threading.local()** | 线程安全 | 不支持协程 |
| **contextvars** | 支持线程和协程 | Python 3.7+ |
| **全局变量** | 简单 | 线程不安全 |
| **参数传递** | 显式可控 | 代码侵入性强 |

### 决策

选择 **contextvars**，原因：

1. **协程支持**: 支持 asyncio 协程，符合项目异步架构
2. **线程安全**: 自动隔离不同线程/协程的上下文
3. **标准库**: 无需额外依赖

### 实施

```python
from contextvars import ContextVar

_current_trace_id: ContextVar[Optional[str]] = ContextVar('trace_id', default=None)
_current_span_id: ContextVar[Optional[str]] = ContextVar('span_id', default=None)
```

**使用方式**:
```python
# 设置上下文
_current_trace_id.set(trace_id)

# 获取上下文
trace_id = _current_trace_id.get()
```

---

## ADR-004: 异步 Span 处理与缓存优化

### 决策记录

| 属性 | 内容 |
|------|------|
| **标题** | 异步 Span 处理与缓存优化 |
| **状态** | 已实施 |
| **创建日期** | 2026-06-12 |
| **作者** | 架构团队 |
| **相关模块** | `agent/monitoring/tracing_cache.py` |

### 背景

高流量场景下，追踪数据的写入会成为性能瓶颈。需要优化 Span 的处理和存储方式。

### 决策

1. **异步写入**: 将 Span 数据放入队列，由后台线程异步写入持久化存储
2. **对象池**: 使用对象池复用 Span 对象，减少内存分配
3. **批量处理**: 批量提交 Span 数据，减少网络调用次数

### 实施

**架构**:
```
┌─────────────────────┐
│   TraceContext      │ 创建 Span
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│   对象池 (Pool)     │ 获取/释放 Span
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│   缓存管理器        │ 暂存 Span 数据
└─────────┬───────────┘
          │ 异步写入
          ▼
┌─────────────────────┐
│   持久化存储        │ SQLite/其他存储
└─────────────────────┘
```

**关键配置**:
- 队列最大长度: 10000
- 批量提交大小: 100
- 提交间隔: 500ms

---

## ADR-005: 使用 Prometheus 作为指标收集标准

### 决策记录

| 属性 | 内容 |
|------|------|
| **标题** | 使用 Prometheus 作为指标收集标准 |
| **状态** | 已实施 |
| **创建日期** | 2026-06-15 |
| **作者** | 架构团队 |
| **相关模块** | `agent/monitoring/metrics.py` |

### 背景

需要选择一个标准的指标收集方案，支持实时监控和告警。

### 评估方案

| 方案 | 优点 | 缺点 |
|------|------|------|
| **Prometheus** | 行业标准、查询语言强大、生态成熟 | 需要额外部署 |
| **InfluxDB** | 时序数据优化、查询灵活 | 相对复杂 |
| **StatsD** | 轻量、易于集成 | 功能相对简单 |

### 决策

选择 **Prometheus**，原因：

1. **行业标准**: 与 Kubernetes、Grafana 无缝集成
2. **强大的查询语言**: PromQL 支持复杂的指标分析
3. **告警集成**: 内置告警规则引擎

### 实施

**指标端点**: `/metrics` (Prometheus 格式)

**指标命名规范**:
```
yunshu_<模块>_<指标类型>_<名称>

示例:
yunshu_http_requests_total
yunshu_llm_calls_total
yunshu_cpu_usage_percent
```

---

## ADR-006: JSON 结构化日志格式

### 决策记录

| 属性 | 内容 |
|------|------|
| **标题** | 使用 JSON 作为结构化日志格式 |
| **状态** | 已实施 |
| **创建日期** | 2026-06-18 |
| **作者** | 架构团队 |
| **相关模块** | `agent/log_system/` |

### 背景

日志需要支持结构化查询和分析，便于与 Loki、Elasticsearch 等工具集成。

### 决策

采用 **JSON 格式** 作为日志输出格式，包含以下关键字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `timestamp` | float | 时间戳 |
| `level` | string | 日志级别 |
| `service` | string | 服务名称 |
| `operation` | string | 操作名称 |
| `trace_id` | string | 追踪 ID |
| `span_id` | string | Span ID |
| `message` | string | 日志消息 |
| `duration_ms` | float | 持续时间 |

### 实施

**日志输出示例**:
```json
{
  "timestamp": 1699584000.0,
  "level": "INFO",
  "service": "DigitalLife",
  "operation": "chat",
  "trace_id": "abc123def4567890",
  "span_id": "1234567812345678",
  "message": "用户消息处理完成",
  "duration_ms": 150.5
}
```

---

## ADR-007: 敏感数据过滤机制

### 决策记录

| 属性 | 内容 |
|------|------|
| **标题** | 实现敏感数据自动过滤机制 |
| **状态** | 已实施 |
| **创建日期** | 2026-06-20 |
| **作者** | 安全团队 |
| **相关模块** | `agent/log_system/safe_logger.py`, `agent/monitoring/sensitive_data_filter.py` |

### 背景

日志和追踪数据中可能包含敏感信息（如用户凭证、个人信息），需要自动过滤。

### 决策

实现敏感数据过滤机制：

1. **关键字检测**: 自动检测密码、token、密钥等关键字
2. **正则匹配**: 使用正则表达式匹配敏感模式（邮箱、手机号、身份证号等）
3. **自定义规则**: 支持配置自定义过滤规则

### 过滤规则

| 规则类型 | 正则模式 | 替换方式 |
|----------|---------|---------|
| 邮箱 | `[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}` | `***@***.com` |
| 手机号 | `1[3-9]\d{9}` | `1*** **** ****` |
| 身份证号 | `\d{17}[\dXx]` | `*****************` |
| Token | `[a-zA-Z0-9]{32,}` | `***token***` |
| 密码 | `password.*` | `***` |

### 实施

**使用方式**:
```python
from agent.log_system.safe_logger import SafeLogger

logger = SafeLogger("DigitalLife")
logger.info("用户登录", {
    "user_id": "user123",
    "email": "user@example.com",  # 会被过滤
    "password": "secret123"       # 会被过滤
})
```

---

## ADR-008: 自愈机制设计

### 决策记录

| 属性 | 内容 |
|------|------|
| **标题** | 实现自动自愈机制 |
| **状态** | 已实施 |
| **创建日期** | 2026-06-22 |
| **作者** | 架构团队 |
| **相关模块** | `agent/monitoring/self_healer.py` |

### 背景

系统需要具备自动检测和修复常见问题的能力，提高可用性。

### 决策

实现多层次自愈机制：

| 层次 | 功能 | 修复策略 |
|------|------|---------|
| **健康检查** | 定期检查系统状态 | - |
| **问题检测** | 识别异常状态 | - |
| **自动修复** | 执行修复操作 | 清理缓存、重启服务、切换节点 |
| **验证恢复** | 确认修复成功 | 检查服务状态 |

### 健康检查维度

| 维度 | 检查项 | 阈值 |
|------|--------|------|
| 内存 | 内存使用率 | > 80% |
| 磁盘 | 磁盘使用率 | > 90% |
| CPU | CPU使用率 | > 90% (持续5分钟) |
| 连接数 | 活跃连接数 | > 1000 |
| 服务状态 | 核心服务健康 | 不健康 |

### 实施

**自愈流程**:
```
定期检查 (每30秒) → 评估健康状态 → 
    正常 → 继续
    异常 → 执行修复 → 验证结果 → 
        成功 → 记录日志
        失败 → 触发告警
```

---

## ADR-009: 诊断端点设计

### 决策记录

| 属性 | 内容 |
|------|------|
| **标题** | 设计完善的诊断端点 |
| **状态** | 已实施 |
| **创建日期** | 2026-06-24 |
| **作者** | 运维团队 |
| **相关模块** | `agent/server_routes/routes_logging.py` |

### 背景

需要提供完善的诊断接口，便于运维人员排查问题。

### 决策

设计以下诊断端点：

| 端点 | 方法 | 说明 | 认证 |
|------|------|------|------|
| `/api/health` | GET | 快速健康检查 | 否 |
| `/api/status` | GET | 系统状态摘要 | 否 |
| `/api/heartbeat` | GET | 心跳检查 | 否 |
| `/api/diagnostics/health` | GET | 综合健康检查 | 否 |
| `/api/diagnostics/trace` | GET | 当前追踪上下文 | 否 |
| `/api/diagnostics/trace/inject` | GET | 生成追踪上下文 | 否 |
| `/api/diagnostics/trace/extract` | POST | 提取追踪上下文 | 否 |
| `/api/diagnostics/tools` | GET | 已注册工具清单 | 否 |
| `/api/diagnostics/config` | GET | 配置状态 | 是 |
| `/api/diagnostics/metrics` | GET | 运行时指标 | 否 |

---

## ADR-010: W3C Trace Context 协议支持

### 决策记录

| 属性 | 内容 |
|------|------|
| **标题** | 支持 W3C Trace Context 协议 |
| **状态** | 已实施 |
| **创建日期** | 2026-06-25 |
| **作者** | 架构团队 |
| **相关模块** | `agent/monitoring/tracing.py` |

### 背景

需要支持标准的追踪上下文传递协议，便于与其他服务集成。

### 决策

支持两种追踪上下文格式：

1. **W3C Trace Context** (推荐)
   ```
   traceparent: 00-{trace_id}-{span_id}-{flags}
   ```

2. **Jaeger 格式** (兼容)
   ```
   uber-trace-id: {trace_id}:{span_id}:{parent_span_id}:{flags}
   ```

### 实施

**协议优先级**:
1. 优先检查 `traceparent` 头
2. 如果不存在，检查 `uber-trace-id` 头
3. 如果都不存在，生成新的追踪上下文

---

**文档版本**: v1.0  
**最后更新**: 2026年6月  
**适用版本**: 云枢智能体 v2.x