# 生产环境分布式追踪配置指南

## 概述

本指南描述了如何配置生产环境的分布式追踪基础设施，包括：
- OpenTelemetry Collector 配置
- Jaeger 可视化工具
- 采样策略配置
- 数据保留策略

## 架构说明

```
┌─────────────────────────────────────────────────────────────────┐
│                        云枢智能代理                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐           │
│  │  TraceContext│  │   @trace    │  │   @async_trace│         │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘           │
└─────────│────────────────│────────────────│────────────────────┘
          │                │                │
          ▼                ▼                ▼
┌─────────────────────────────────────────────────────────────────┐
│                    OpenTelemetry SDK                           │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐           │
│  │  Sampler    │  │  Tracer     │  │  Exporter   │           │
│  │(PARENT_BASED)│  │Provider    │  │   OTLP      │           │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘           │
└─────────│────────────────│────────────────│────────────────────┘
          │                │                │
          └────────────────┼────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│              OpenTelemetry Collector                           │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐           │
│  │  Receiver   │  │  Processor  │  │  Exporter   │           │
│  │  (OTLP)     │  │ (Batch/Sample)│ │  (Jaeger)   │           │
│  └─────────────┘  └─────────────┘  └──────┬──────┘           │
└────────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Jaeger                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐           │
│  │  Storage    │  │  Query      │  │  UI         │           │
│  │  (Badger)   │  │  Service    │  │  (16686)    │           │
│  └─────────────┘  └─────────────┘  └─────────────┘           │
└─────────────────────────────────────────────────────────────────┘
```

## 环境变量配置

### 基础配置

| 环境变量 | 说明 | 默认值 | 生产推荐值 |
|---------|------|--------|-----------|
| TRACING_ENV | 运行环境 | development | production |
| TRACING_LOG_LEVEL | 日志级别 | DEBUG | WARN |

### 采样策略配置

| 环境变量 | 说明 | 可选值 | 生产推荐值 |
|---------|------|--------|-----------|
| TRACING_SAMPLER | 采样器类型 | ALWAYS_ON, ALWAYS_OFF, RATIO, PARENT_BASED, PARENT_BASED_RATIO | PARENT_BASED_RATIO |
| TRACING_SAMPLER_RATIO | 采样比例 (0.0-1.0) | 0.0-1.0 | 0.1 (10%) |
| TRACING_SAMPLER_RATE_LIMIT | 速率限制 (每秒最大采样数) | 正整数 | 100 |

### 导出器配置

| 环境变量 | 说明 | 可选值 | 生产推荐值 |
|---------|------|--------|-----------|
| TRACING_EXPORTER | 导出器类型 | CONSOLE, OTLP, JAEGER, ZIPKIN | OTLP |
| TRACING_EXPORTER_ENDPOINT | 导出器端点 | - | localhost:4317 |
| TRACING_EXPORTER_PROTOCOL | 导出器协议 | GRPC, HTTP | GRPC |

### 数据保留配置

| 环境变量 | 说明 | 默认值 | 生产推荐值 |
|---------|------|--------|-----------|
| TRACING_DATA_RETENTION_DAYS | 追踪数据保留天数 | 7 | 30 |

## 启动方式

### 使用 Docker Compose（推荐）

```bash
# 启动所有监控服务
cd monitoring
docker-compose up -d

# 查看服务状态
docker-compose ps
```

### 使用环境变量启动代理

```bash
# 生产环境启动
TRACING_ENV=production \
TRACING_SAMPLER=PARENT_BASED_RATIO \
TRACING_SAMPLER_RATIO=0.1 \
TRACING_EXPORTER=OTLP \
TRACING_EXPORTER_ENDPOINT=localhost:4317 \
python main.py
```

### 开发环境启动

```bash
# 开发环境（默认）
python main.py

# 强制调试模式
TRACING_LOG_LEVEL=DEBUG python main.py
```

## 采样策略说明

### PARENT_BASED_RATIO（推荐）

基于父 Span 的概率采样策略：
- 如果父 Span 被采样，则子 Span 也被采样
- 如果没有父 Span（根 Span），则按配置比例采样
- 保证追踪链路的完整性

### RATIO

纯概率采样：
- 每个 Span 独立决定是否采样
- 可能导致追踪链路不完整
- 适合高流量场景

### ALWAYS_ON

采样所有 Span：
- 适合开发和测试环境
- 生产环境慎用（会产生大量数据）

### ALWAYS_OFF

不采样任何 Span：
- 适合性能测试或禁用追踪时使用

## 服务端口说明

| 服务 | 端口 | 说明 |
|-----|------|------|
| Jaeger UI | 16686 | 追踪可视化界面 |
| OTLP gRPC | 4317 | OpenTelemetry 协议端点 |
| OTLP HTTP | 4318 | OpenTelemetry HTTP 端点 |
| Jaeger Collector | 14250 | Jaeger 收集器端点 |
| Zipkin | 9411 | Zipkin 兼容端点 |
| Prometheus | 9090 | 指标收集端点 |
| Grafana | 3000 | 指标可视化界面 |

## 访问地址

- **Jaeger UI**: http://localhost:16686
- **Grafana**: http://localhost:3000 (用户名: admin, 密码: admin)
- **Prometheus**: http://localhost:9090

## 配置文件结构

```
monitoring/
├── otel-collector-config.yaml   # OpenTelemetry Collector 配置
├── docker-compose.yml           # Docker Compose 配置
├── prometheus/
│   └── prometheus.yml           # Prometheus 配置
└── grafana_datasources/
    └── prometheus.yml           # Grafana 数据源配置
```

## 数据保留策略

### OpenTelemetry Collector

在 `otel-collector-config.yaml` 中配置：
- `batch.timeout`: 批量发送超时时间（推荐 10s）
- `memory_limiter.limit_mib`: 内存限制（推荐 4000 MiB）

### Jaeger

在 `docker-compose.yml` 中配置：
- 使用 Badger 持久化存储
- 数据保留由 Badger 管理

### 清理策略

推荐配置定时任务清理旧数据：

```bash
# 示例：保留最近30天的数据
find /path/to/traces -type f -mtime +30 -delete
```

## 性能优化建议

### 采样策略调整

根据业务场景调整采样比例：
- 低流量服务：50%-100% 采样
- 高流量服务：1%-10% 采样
- 关键路径：100% 采样

### Batch 处理

启用 BatchSpanProcessor（生产环境默认启用）：
- 减少网络调用次数
- 提高吞吐量
- 增加少量延迟（可接受）

### 资源限制

配置合理的资源限制：
- CPU: 根据服务规模调整
- 内存: 至少 2GB
- 存储: 根据保留策略调整

## 故障排除

### 检查 OpenTelemetry 配置

```python
from agent.monitoring.tracing import print_diagnosis_report

print_diagnosis_report()
```

### 检查服务状态

```bash
# 检查 Jaeger 状态
curl http://localhost:16686/api/services

# 检查 OTLP Collector 状态
grpcurl -d '{}' localhost:4317 grpc.health.v1.Health/Check
```

### 常见问题

**问题**: 追踪数据没有显示在 Jaeger 中

**排查步骤**:
1. 检查环境变量配置是否正确
2. 检查 OTLP Collector 是否运行
3. 检查网络连接是否正常
4. 查看服务日志

**问题**: 采样比例不生效

**排查步骤**:
1. 确认 `TRACING_SAMPLER` 设置为 `RATIO` 或 `PARENT_BASED_RATIO`
2. 确认 `TRACING_SAMPLER_RATIO` 在 0.0-1.0 范围内
3. 检查调试模式是否覆盖了采样配置

## 安全建议

### 生产环境配置

1. **禁用调试模式**: 设置 `TRACING_ENV=production`
2. **限制日志级别**: 使用 `WARN` 或 `ERROR`
3. **配置防火墙**: 限制外部访问 Jaeger UI
4. **启用认证**: 为 Grafana 配置强密码

### 数据保护

1. **敏感信息过滤**: 不要在 Span 属性中存储敏感数据
2. **数据加密**: 传输和存储时加密追踪数据
3. **访问控制**: 限制追踪数据的访问权限

## 监控指标

追踪系统本身也需要监控：

| 指标 | 说明 |
|-----|------|
| spans_total | 生成的 Span 总数 |
| spans_sampled | 被采样的 Span 数 |
| spans_dropped | 被丢弃的 Span 数 |
| exporter_errors | 导出错误数 |
| trace_duration_ms | 追踪平均耗时 |

## 版本兼容性

| 组件 | 推荐版本 |
|-----|---------|
| OpenTelemetry SDK | 1.20+ |
| OpenTelemetry Collector | 0.90+ |
| Jaeger | 1.50+ |
| Python | 3.8+ |

## 附录：完整环境变量示例

```bash
# 生产环境完整配置
export TRACING_ENV=production
export TRACING_LOG_LEVEL=WARN
export TRACING_SAMPLER=PARENT_BASED_RATIO
export TRACING_SAMPLER_RATIO=0.1
export TRACING_SAMPLER_RATE_LIMIT=100
export TRACING_EXPORTER=OTLP
export TRACING_EXPORTER_ENDPOINT=localhost:4317
export TRACING_EXPORTER_PROTOCOL=GRPC
export TRACING_DATA_RETENTION_DAYS=30
```