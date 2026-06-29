# 云枢智能体 - 可观测性完整手册

---

## 目录

1. [可观测性概述](#1-可观测性概述)
2. [追踪使用指南](#2-追踪使用指南)
3. [指标查询指南](#3-指标查询指南)
4. [告警配置指南](#4-告警配置指南)
5. [混沌工程使用指南](#5-混沌工程使用指南)
6. [故障排查手册](#6-故障排查手册)

---

## 1. 可观测性概述

### 1.1 什么是可观测性

可观测性是指通过外部输出（指标、日志、追踪）来推断系统内部状态的能力。云枢智能体的可观测性体系包含三大支柱：

| 支柱 | 描述 | 用途 |
|------|------|------|
| **分布式追踪** | 跨服务请求链路追踪 | 定位延迟瓶颈、故障定位 |
| **指标监控** | 聚合时间序列数据 | 系统健康评估、容量规划 |
| **日志系统** | 结构化事件记录 | 问题诊断、安全审计 |

### 1.2 架构设计

```
┌─────────────────────────────────────────────────────────────────┐
│                        云枢智能代理                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐           │
│  │ TraceContext│  │   @trace    │  │   @async_trace│         │
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
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
┌────────────────┐ ┌────────────────┐ ┌────────────────┐
│    Jaeger      │ │   Prometheus   │ │   Grafana      │
│  (追踪可视化)   │ │  (指标存储)     │ │  (可视化面板)   │
└────────────────┘ └────────────────┘ └────────────────┘
```

### 1.3 核心特性

- ✅ W3C Trace Context 标准兼容
- ✅ Jaeger / Zipkin 格式支持
- ✅ 多环境配置切换（开发/测试/生产）
- ✅ 高并发上下文隔离（基于 ContextVar）
- ✅ 网络异常容错处理
- ✅ 健康检查接口

---

## 2. 追踪使用指南

### 2.1 环境变量配置

| 环境变量 | 说明 | 默认值 | 生产推荐值 |
|---------|------|--------|-----------|
| TRACING_ENV | 运行环境 | development | production |
| TRACING_LOG_LEVEL | 日志级别 | DEBUG | WARN |
| TRACING_SAMPLER | 采样器类型 | ALWAYS_ON | PARENT_BASED_RATIO |
| TRACING_SAMPLER_RATIO | 采样比例 (0.0-1.0) | 1.0 | 0.1 (10%) |
| TRACING_EXPORTER | 导出器类型 | CONSOLE | OTLP |
| TRACING_EXPORTER_ENDPOINT | 导出器端点 | - | localhost:4317 |

### 2.2 快速开始

#### 使用 TraceContext 上下文管理器

```python
from agent.monitoring.tracing import TraceContext, get_trace_id

with TraceContext("DigitalLife", "chat", span_kind="server") as ctx:
    trace_id = ctx.trace_id
    span_id = ctx.span_id
    
    ctx.set_attribute("user_id", "user123")
    ctx.add_event("message_received", {"content_length": 100})
    
    with TraceContext("LLMService", "completion", span_kind="client") as nested_ctx:
        pass
```

#### 跨服务调用时的上下文传递

```python
from agent.monitoring.tracing import inject_trace_context, extract_trace_context

# 服务 A：注入上下文到请求头
headers = inject_trace_context()
response = requests.get("http://service-b/api/process", headers=headers)

# 服务 B：从请求头提取上下文
extracted = extract_trace_context(dict(request.headers))
set_trace_id(extracted.get("trace_id"))
set_span_id(extracted.get("span_id"))
```

### 2.3 采样策略

| 策略 | 说明 | 适用场景 |
|------|------|---------|
| ALWAYS_ON | 采样所有 Span | 开发/测试环境 |
| ALWAYS_OFF | 不采样任何 Span | 性能测试 |
| RATIO | 按比例采样 | 简单场景 |
| PARENT_BASED_RATIO | 基于父 Span 的概率采样 | **生产环境推荐** |

### 2.4 访问地址

| 服务 | URL |
|------|-----|
| Jaeger UI | http://localhost:16686 |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 |

---

## 3. 指标查询指南

### 3.1 Prometheus 指标查询

#### 常用查询语句

```promql
# API 请求总数
sum(yunshu_http_requests_total)

# 错误率
sum(rate(yunshu_http_requests_total{status=~"5.."}[5m])) / sum(rate(yunshu_http_requests_total[5m]))

# 平均响应时间
avg(yunshu_http_request_duration_seconds_sum / yunshu_http_request_duration_seconds_count)

# CPU 使用率
avg(yunshu_cpu_usage_percent)

# LLM 调用次数
sum(yunshu_llm_calls_total)

# 工具调用次数
sum(yunshu_tool_calls_total)
```

### 3.2 业务指标

业务指标分为四大类：

| 类别 | 指标示例 | 业务价值 |
|------|----------|----------|
| 用户交互 | `yunshu_interaction_total` | 衡量用户活跃度 |
| 任务完成 | `yunshu_task_completion_rate` | 衡量任务成功率 |
| 知识库 | `yunshu_memory_search_hit_rate` | 衡量记忆检索效率 |
| 扩展使用 | `yunshu_extension_install_total` | 衡量扩展获取频率 |

### 3.3 API 端点

| 端点 | 方法 | 描述 |
|------|------|------|
| `/metrics` | GET | Prometheus 格式指标导出 |
| `/api/diagnostics/metrics` | GET | JSON 格式运行时指标 |
| `/api/business/dashboard` | GET | 业务仪表盘总览 |
| `/api/business/health` | GET | 业务指标健康检查 |

---

## 4. 告警配置指南

### 4.1 告警规则配置

告警规则通过 `/api/observability/alerts` 端点管理：

```bash
# 获取告警规则列表
curl http://localhost:5678/api/observability/alerts

# 创建告警规则
curl -X POST http://localhost:5678/api/observability/alerts \
  -H "Content-Type: application/json" \
  -d '{
    "name": "high_error_rate",
    "expr": "sum(rate(yunshu_http_requests_total{status=~\"5..\"}[5m])) / sum(rate(yunshu_http_requests_total[5m])) > 0.1",
    "threshold": 0.1,
    "severity": "critical",
    "description": "错误率超过10%"
  }'

# 验证告警表达式
curl -X POST http://localhost:5678/api/observability/alerts/validate \
  -H "Content-Type: application/json" \
  -d '{"expr": "sum(rate(yunshu_http_requests_total[5m])) > 100"}'
```

### 4.2 告警验证检查清单

- [ ] 告警规则正确触发
- [ ] 告警级别正确设置
- [ ] 告警包含足够上下文
- [ ] 告警通知正确发送

---

## 5. 混沌工程使用指南

### 5.1 支持的故障类型

| 故障类型 | 说明 | 适用场景 |
|---------|------|---------|
| NETWORK_DELAY | 网络延迟注入 | 测试超时处理 |
| NETWORK_TIMEOUT | 网络超时注入 | 测试重试逻辑 |
| SERVICE_UNAVAILABLE | 服务不可用 | 测试降级机制 |
| MEMORY_PRESSURE | 内存压力 | 测试内存耗尽场景 |
| CPU_PRESSURE | CPU压力 | 测试CPU密集场景 |

### 5.2 快速开始

```python
from agent.monitoring import get_chaos_injector, FaultType, chaos_fault

# 使用上下文管理器
with chaos_fault(FaultType.NETWORK_DELAY, delay_ms=3000):
    make_request()

# 手动注入和清理
injector = get_chaos_injector()
injector.inject_network_delay(delay_ms=5000)
# 执行测试操作
injector.clear_fault(FaultType.NETWORK_DELAY)
```

### 5.3 混沌测试流程

```
1. 准备阶段 → 记录测试前的系统状态
2. 故障注入 → 使用 chaos_fault 上下文管理器
3. 执行测试 → 执行待验证的业务操作
4. 验证阶段 → 验证追踪、指标、日志、告警
5. 恢复阶段 → 自动清除故障
6. 报告生成 → 生成测试报告
```

### 5.4 可观测性验证检查清单

#### 追踪链路验证
- [ ] 追踪ID在故障场景下正常生成
- [ ] Span正确记录异常状态
- [ ] 错误事件被正确记录
- [ ] 分布式上下文正确传递

#### 指标验证
- [ ] 错误计数器正确递增
- [ ] 延迟直方图正确更新
- [ ] 熔断器状态指标正确反映
- [ ] 资源使用指标正确采集

#### 日志验证
- [ ] 异常信息包含trace_id
- [ ] 错误级别正确设置
- [ ] 错误堆栈完整记录

---

## 6. 故障排查手册

### 6.1 追踪上下文丢失

**问题现象**: 日志中缺少 trace_id、追踪链路中断

**排查步骤**:

```bash
# 检查 OpenTelemetry 是否可用
curl http://localhost:5678/api/diagnostics/health | jq '.opentelemetry_available'

# 检查当前追踪上下文
curl http://localhost:5678/api/diagnostics/trace

# 验证上下文提取功能
curl -X POST http://localhost:5678/api/diagnostics/trace/extract \
  -H "Content-Type: application/json" \
  -d '{"headers": {"traceparent": "00-abc123def4567890abc123def4567890-1234567812345678-01"}}'
```

### 6.2 Prometheus 指标不显示

**问题现象**: `/metrics` 端点返回空或不完整

**排查步骤**:

```bash
# 检查 Prometheus 端点
curl http://localhost:5678/metrics | head -20

# 检查运行时指标
curl http://localhost:5678/api/diagnostics/metrics

# 检查 prometheus_client 是否安装
python -c "import prometheus_client; print('OK')"
```

### 6.3 告警规则不生效

**问题现象**: 配置的告警规则未触发

**排查步骤**:

```bash
# 检查告警规则配置
curl http://localhost:5678/api/observability/alerts

# 验证告警表达式
curl -X POST http://localhost:5678/api/observability/alerts/validate \
  -H "Content-Type: application/json" \
  -d '{"expr": "sum(rate(yunshu_http_requests_total[5m])) > 100"}'
```

### 6.4 高延迟问题排查

**问题现象**: API 响应缓慢

**排查步骤**:

```promql
# 查看延迟指标
histogram_quantile(0.95, rate(yunshu_http_request_duration_seconds_bucket[5m]))

# 查看各端点延迟
histogram_quantile(0.95, rate(yunshu_http_request_duration_seconds_bucket{endpoint="/api/chat"}[5m]))

# 检查 CPU 和内存使用率
yunshu_cpu_usage_percent
yunshu_memory_usage_percent
```

---

## 附录：配置文件位置

| 配置项 | 路径 |
|--------|------|
| Prometheus 配置 | `monitoring/prometheus.yml` |
| 告警规则配置 | `monitoring/alerts.yml` |
| Docker Compose | `monitoring/docker-compose.yml` |
| 监控启动脚本 | `monitoring/start_monitoring.sh` |

---

**文档版本**: v1.0  
**最后更新**: 2026年6月  
**适用版本**: 云枢智能体 v2.x