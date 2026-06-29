# 云枢智能体 - 可观测性操作手册

## 概述

本文档详细介绍云枢智能体的可观测性体系，包含分布式追踪、指标监控、日志系统三大支柱的使用方法和操作指南。

---

## 目录

1. [可观测性端点列表](#1-可观测性端点列表)
2. [追踪上下文传播机制](#2-追踪上下文传播机制)
3. [指标查询示例](#3-指标查询示例)
4. [日志查询示例](#4-日志查询示例)
5. [常见问题排查指南](#5-常见问题排查指南)
6. [验证测试脚本](#6-验证测试脚本)

---

## 1. 可观测性端点列表

### 1.1 健康检查端点

| 端点 | 方法 | 描述 | 是否需要认证 |
|------|------|------|-------------|
| `/api/health` | GET | 快速健康检查，返回身体传感器读数 | 否 |
| `/api/status` | GET | 获取系统状态摘要 | 否 |
| `/api/heartbeat` | GET | 心跳检查，记录健康状态 | 否 |
| `/api/diagnostics/health` | GET | 综合健康检查（含 OpenTelemetry 状态） | 否 |

#### 使用示例

```bash
# 快速健康检查
curl http://localhost:5678/api/health

# 综合健康检查
curl http://localhost:5678/api/diagnostics/health
```

**响应示例** (`/api/diagnostics/health`):

```json
{
  "overall_health": 1.0,
  "dimensions": {
    "memory": 1.0,
    "network": 1.0,
    "llm": 1.0
  },
  "issues": [],
  "opentelemetry_available": true,
  "timestamp": 1699584000.0
}
```

---

### 1.2 追踪端点

| 端点 | 方法 | 描述 | 是否需要认证 |
|------|------|------|-------------|
| `/api/diagnostics/trace` | GET | 获取当前追踪上下文 | 否 |
| `/api/diagnostics/trace/inject` | GET | 生成追踪上下文请求头 | 否 |
| `/api/diagnostics/trace/extract` | POST | 从请求头提取追踪上下文 | 否 |
| `/api/observability/traces` | GET | 获取追踪数据列表 | 是 |
| `/api/observability/traces/<trace_id>` | GET | 获取追踪详情 | 是 |

#### 使用示例

```bash
# 获取当前追踪上下文
curl http://localhost:5678/api/diagnostics/trace

# 生成追踪上下文请求头
curl http://localhost:5678/api/diagnostics/trace/inject

# 提取追踪上下文（POST）
curl -X POST http://localhost:5678/api/diagnostics/trace/extract \
  -H "Content-Type: application/json" \
  -d '{"headers": {"traceparent": "00-abc123def4567890abc123def4567890-1234567812345678-01"}}'
```

**响应示例** (`/api/diagnostics/trace/inject`):

```json
{
  "headers": {
    "traceparent": "00-abc123def4567890-1234567812345678-01",
    "tracestate": ""
  },
  "trace_id": "abc123def4567890",
  "span_id": "1234567812345678",
  "timestamp": 1699584000.0
}
```

---

### 1.3 指标端点

| 端点 | 方法 | 描述 | 是否需要认证 |
|------|------|------|-------------|
| `/metrics` | GET | Prometheus 格式指标导出 | 否 |
| `/api/diagnostics/metrics` | GET | JSON 格式运行时指标 | 否 |

#### 使用示例

```bash
# 获取 Prometheus 格式指标（供 Prometheus Server 采集）
curl http://localhost:5678/metrics

# 获取 JSON 格式运行时指标
curl http://localhost:5678/api/diagnostics/metrics
```

---

### 1.4 日志端点

| 端点 | 方法 | 描述 | 是否需要认证 |
|------|------|------|-------------|
| `/api/diagnostics/logs` | GET | 获取最近日志记录 | 是 |
| `/api/observability/logs` | GET | 查询日志（支持过滤） | 是 |
| `/api/observability/logs` | POST | 推送日志 | 是 |
| `/api/observability/logs/stream` | GET | 实时日志流（SSE） | 是 |
| `/api/observability/logs/labels` | GET | 获取日志标签列表 | 是 |

#### 使用示例

```bash
# 获取最近 50 条日志
curl http://localhost:5678/api/diagnostics/logs?limit=50

# 查询特定 trace_id 的日志
curl "http://localhost:5678/api/observability/logs?trace_id=abc123def4567890"

# 实时日志流
curl -N http://localhost:5678/api/observability/logs/stream
```

---

### 1.5 诊断端点

| 端点 | 方法 | 描述 | 是否需要认证 |
|------|------|------|-------------|
| `/api/diagnostics/tools` | GET | 获取已注册工具清单 | 否 |
| `/api/diagnostics/config` | GET | 获取配置状态摘要 | 是 |
| `/api/observability/state` | GET | 获取综合可观测性状态 | 否 |

#### 使用示例

```bash
# 获取已注册工具清单
curl http://localhost:5678/api/diagnostics/tools

# 获取综合可观测性状态
curl http://localhost:5678/api/observability/state
```

---

### 1.6 告警端点

| 端点 | 方法 | 描述 | 是否需要认证 |
|------|------|------|-------------|
| `/api/observability/alerts` | GET | 获取告警规则列表 | 是 |
| `/api/observability/alerts` | POST | 创建告警规则 | 是 |
| `/api/observability/alerts/<id>` | PUT | 更新告警规则 | 是 |
| `/api/observability/alerts/<id>` | DELETE | 删除告警规则 | 是 |
| `/api/observability/alerts/validate` | POST | 验证告警表达式 | 是 |

---

## 2. 追踪上下文传播机制

### 2.1 追踪上下文格式

云枢支持两种追踪上下文格式：

#### W3C Trace Context 格式（推荐）

```
traceparent: 00-{trace_id}-{span_id}-{flags}
```

- **version**: 固定为 `00`
- **trace_id**: 16或32位十六进制字符串
- **span_id**: 16位十六进制字符串
- **flags**: 2位十六进制，`01` 表示采样

#### Jaeger 格式

```
uber-trace-id: {trace_id}:{span_id}:{parent_span_id}:{flags}
```

### 2.2 上下文传播流程

```
┌──────────────┐     HTTP Request      ┌──────────────┐
│   客户端      │ ────────────────────→ │   API 网关    │
│              │    traceparent 头      │              │
│              │ ←──────────────────── │              │
└──────────────┘     响应              └──────────────┘
       │                                        │
       │ 内部调用                                │ 内部调用
       ↓                                        ↓
┌──────────────┐                       ┌──────────────┐
│   Service A  │                       │   Service B  │
│              │                       │              │
└──────────────┘                       └──────────────┘
```

### 2.3 代码中的追踪使用

#### 使用 TraceContext 上下文管理器

```python
from agent.monitoring.tracing import TraceContext, get_trace_id

# 创建追踪上下文
with TraceContext("DigitalLife", "chat", span_kind="server") as ctx:
    # 在上下文中执行业务逻辑
    trace_id = ctx.trace_id
    span_id = ctx.span_id
    
    # 获取当前 trace_id
    current_trace = get_trace_id()
    
    # 添加自定义属性
    ctx.set_attribute("user_id", "user123")
    ctx.add_event("message_received", {"content_length": 100})
    
    # 嵌套上下文（自动继承 trace_id）
    with TraceContext("LLMService", "completion", span_kind="client") as nested_ctx:
        # 执行 LLM 调用
        pass
```

#### 跨服务调用时的上下文传递

```python
from agent.monitoring.tracing import inject_trace_context, extract_trace_context, set_trace_id, set_span_id

# 服务 A：注入上下文到请求头
headers = inject_trace_context()
# headers = {"traceparent": "00-abc123...-1234...-01"}

# 发送请求到服务 B
response = requests.get("http://service-b/api/process", headers=headers)

# 服务 B：从请求头提取上下文
extracted = extract_trace_context(dict(request.headers))
set_trace_id(extracted.get("trace_id"))
set_span_id(extracted.get("span_id"))

# 继续处理（自动创建子 Span）
with TraceContext("ServiceB", "process") as ctx:
    # ctx.trace_id 与服务 A 的 trace_id 相同
    pass
```

---

## 3. 指标查询示例

### 3.1 Prometheus 指标查询

#### 常用查询语句

```promql
# 查询 API 请求总数
sum(yunshu_http_requests_total)

# 查询错误率（5xx 状态码比例）
sum(rate(yunshu_http_requests_total{status=~"5.."}[5m])) / sum(rate(yunshu_http_requests_total[5m]))

# 查询平均响应时间
avg(yunshu_http_request_duration_seconds_sum / yunshu_http_request_duration_seconds_count)

# 查询 CPU 使用率
avg(yunshu_cpu_usage_percent)

# 查询内存使用率
avg(yunshu_memory_usage_percent)

# 查询安全拦截次数
sum(yunshu_security_blocks_total)

# 查询 LLM 调用次数
sum(yunshu_llm_calls_total)

# 查询对话次数
sum(yunshu_conversations_total)

# 查询工具调用次数
sum(yunshu_tool_calls_total)

# 查询活跃连接数
avg(yunshu_active_connections)

# SafeFileReader 相关指标
sum(yunshu_safe_file_reader_errors_total)
sum(yunshu_safe_file_reader_encoding_fallbacks_total)
avg(yunshu_safe_file_reader_read_duration_seconds)
```

### 3.2 JSON 格式指标查询

```bash
# 获取所有运行时指标
curl http://localhost:5678/api/diagnostics/metrics
```

**响应示例**:

```json
{
  "histograms": {
    "latency.digital_life.chat": {
      "count": 100,
      "sum": 50.0,
      "avg": 0.5,
      "min": 0.1,
      "max": 1.2,
      "p50": 0.45,
      "p95": 0.95,
      "p99": 1.1
    }
  },
  "counters": {
    "count.chat.total": 1000,
    "count.errors.total": 5
  },
  "generated_at": 1699584000.0,
  "timestamp": 1699584000.0
}
```

---

## 4. 日志查询示例

### 4.1 日志格式

日志记录包含以下关键字段：

| 字段 | 类型 | 描述 |
|------|------|------|
| `timestamp` | float | 时间戳 |
| `level` | string | 日志级别 (DEBUG/INFO/WARN/ERROR) |
| `service` | string | 服务名称 |
| `operation` | string | 操作名称 |
| `trace_id` | string | 追踪 ID |
| `span_id` | string | Span ID |
| `message` | string | 日志消息 |
| `duration_ms` | float | 持续时间（毫秒） |

### 4.2 查询最近日志

```bash
# 获取最近 50 条日志
curl "http://localhost:5678/api/diagnostics/logs?limit=50"

# 获取最近 100 条日志
curl "http://localhost:5678/api/diagnostics/logs?limit=100"
```

### 4.3 按条件过滤日志

```bash
# 按 trace_id 过滤
curl "http://localhost:5678/api/observability/logs?trace_id=abc123def4567890"

# 按级别过滤
curl "http://localhost:5678/api/observability/logs?level=ERROR"

# 按服务过滤
curl "http://localhost:5678/api/observability/logs?service=DigitalLife"

# 组合过滤
curl "http://localhost:5678/api/observability/logs?level=ERROR&service=DigitalLife"
```

### 4.4 查询日志标签

```bash
# 获取可用的日志标签
curl http://localhost:5678/api/observability/logs/labels
```

---

## 5. 常见问题排查指南

### 5.1 追踪上下文丢失

**问题现象**:
- 日志中缺少 `trace_id`
- 追踪链路中断
- 跨服务调用时追踪上下文未传递

**排查步骤**:

1. **检查 OpenTelemetry 是否可用**
   ```bash
   curl http://localhost:5678/api/diagnostics/health | jq '.opentelemetry_available'
   ```

2. **检查当前追踪上下文**
   ```bash
   curl http://localhost:5678/api/diagnostics/trace
   ```

3. **验证上下文提取功能**
   ```bash
   curl -X POST http://localhost:5678/api/diagnostics/trace/extract \
     -H "Content-Type: application/json" \
     -d '{"headers": {"traceparent": "00-abc123def4567890abc123def4567890-1234567812345678-01"}}'
   ```

4. **检查代码中是否正确使用 TraceContext**
   - 确保所有入口点都包装在 `TraceContext` 中
   - 确保跨服务调用时传递 `traceparent` 头

---

### 5.2 Prometheus 指标不显示

**问题现象**:
- `/metrics` 端点返回空或不完整
- Prometheus Server 无法采集指标

**排查步骤**:

1. **检查 Prometheus 端点**
   ```bash
   curl http://localhost:5678/metrics | head -20
   ```

2. **检查运行时指标**
   ```bash
   curl http://localhost:5678/api/diagnostics/metrics
   ```

3. **检查 prometheus_client 是否安装**
   ```bash
   python -c "import prometheus_client; print('OK')"
   ```

4. **检查端口是否可访问**
   ```bash
   telnet localhost 5678
   ```

---

### 5.3 日志中缺少 trace_id

**问题现象**:
- 日志记录不包含 `trace_id` 字段
- 无法关联日志到追踪

**排查步骤**:

1. **检查日志配置**
   - 确保使用了 `SafeLogger`
   - 确保在 `TraceContext` 上下文中记录日志

2. **验证日志端点**
   ```bash
   curl http://localhost:5678/api/diagnostics/logs?limit=10 | jq '.[].trace_id'
   ```

3. **检查日志格式**
   - 确保日志使用 JSON 格式输出
   - 确保包含必要的追踪字段

---

### 5.4 告警规则不生效

**问题现象**:
- 配置的告警规则未触发
- Prometheus Alertmanager 未收到告警

**排查步骤**:

1. **检查告警规则配置**
   ```bash
   curl http://localhost:5678/api/observability/alerts
   ```

2. **验证告警表达式**
   ```bash
   curl -X POST http://localhost:5678/api/observability/alerts/validate \
     -H "Content-Type: application/json" \
     -d '{"expr": "sum(rate(yunshu_http_requests_total[5m])) > 100"}'
   ```

3. **检查 Prometheus 配置**
   - 确认 `prometheus.yml` 中配置了正确的告警规则文件路径
   - 确认规则文件存在且格式正确

4. **检查 Prometheus 服务状态**
   ```bash
   # 如果使用 Docker
   docker-compose -f monitoring/docker-compose.yml ps
   ```

---

### 5.5 高延迟问题排查

**问题现象**:
- API 响应缓慢
- 95th percentile 延迟过高

**排查步骤**:

1. **查看延迟指标**
   ```promql
   histogram_quantile(0.95, rate(yunshu_http_request_duration_seconds_bucket[5m]))
   ```

2. **查看各端点延迟**
   ```promql
   histogram_quantile(0.95, rate(yunshu_http_request_duration_seconds_bucket{endpoint="/api/chat"}[5m]))
   ```

3. **检查系统资源**
   ```bash
   curl http://localhost:5678/api/diagnostics/metrics | jq '.histograms'
   ```

4. **检查 CPU 和内存使用率**
   ```promql
   yunshu_cpu_usage_percent
   yunshu_memory_usage_percent
   ```

---

## 6. 验证测试脚本

### 6.1 运行端到端验证

```bash
# 基础验证
python tests/test_observability_e2e.py

# 生成详细报告
python tests/test_observability_e2e.py --report

# 指定服务 URL
python tests/test_observability_e2e.py --url http://localhost:5678 --report
```

### 6.2 验证内容

测试脚本验证以下内容：

| 验证项 | 描述 |
|--------|------|
| 健康端点 | `/api/health`, `/api/diagnostics/health`, `/api/status`, `/api/heartbeat` |
| 追踪端点 | `/api/diagnostics/trace`, `/api/diagnostics/trace/inject`, `/api/diagnostics/trace/extract` |
| 指标端点 | `/metrics`, `/api/diagnostics/metrics` |
| 日志端点 | `/api/diagnostics/logs` |
| 可观测性状态 | `/api/observability/state` |
| 工具诊断 | `/api/diagnostics/tools` |
| 上下文传播 | TraceContext 创建、注入、提取、嵌套 |

---

## 附录：配置文件位置

| 配置项 | 路径 |
|--------|------|
| Prometheus 配置 | `monitoring/prometheus.yml` |
| 告警规则配置 | `monitoring/alerts.yml` |
| Docker Compose | `monitoring/docker-compose.yml` |
| 监控启动脚本 | `monitoring/start_monitoring.sh` (Linux) / `monitoring/start_monitoring.ps1` (Windows) |

---

## 附录：启动监控服务

### Docker Compose 方式

```bash
cd monitoring

# 启动监控服务（Linux/macOS）
docker-compose up -d

# 启动监控服务（Windows）
docker-compose up -d

# 查看服务状态
docker-compose ps

# 停止服务
docker-compose down
```

### 服务访问地址

| 服务 | URL |
|------|-----|
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 |
| Jaeger | http://localhost:16686 |
| 云枢 API | http://localhost:5678 |

---

**文档版本**: v1.0  
**最后更新**: 2026年6月  
**适用版本**: 云枢智能体 v2.x
