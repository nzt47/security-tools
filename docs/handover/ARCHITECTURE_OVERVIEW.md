# 云枢智能体 - 可观测性体系架构概述

## 概述

本文档提供云枢智能体可观测性体系的整体架构说明，旨在帮助维护人员快速理解系统设计理念、模块划分和核心组件。

---

## 1. 架构设计理念

### 1.1 可观测性三支柱

云枢智能体的可观测性体系基于业界标准的三大支柱构建：

| 支柱 | 功能定位 | 核心组件 |
|------|---------|---------|
| **分布式追踪** | 追踪请求在系统中的完整执行路径 | OpenTelemetry SDK、Jaeger |
| **指标监控** | 收集和分析系统性能指标 | Prometheus、Grafana |
| **日志系统** | 记录和查询系统运行日志 | Loki、结构化日志 |

### 1.2 设计原则

- **标准化**: 遵循 OpenTelemetry 规范，支持多协议、多格式
- **可扩展性**: 模块化设计，易于添加新的监控维度
- **低侵入性**: 通过装饰器和上下文管理器实现无侵入式监控
- **高性能**: 采样机制、异步处理、缓存优化
- **可运维性**: 完善的诊断端点和健康检查机制

---

## 2. 系统架构图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          云枢智能体应用层                                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│  │ DigitalLife │  │ MemoryStore │  │  TaskPlanner│  │  ModelRouter│        │
│  │     业务层    │  │    记忆模块   │  │    任务规划   │  │    模型路由   │        │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘        │
│         │                │                │                │               │
│         ▼                ▼                ▼                ▼               │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                    可观测性中间层 (agent/monitoring/)                 │   │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐       │   │
│  │  │ tracing │ │ metrics │ │  logs   │ │ alerts  │ │ self   │       │   │
│  │  │  追踪模块  │ │ 指标模块  │ │ 日志模块  │ │ 告警模块  │ │ healing │       │   │
│  │  └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘       │   │
│  └───────│───────────│───────────│───────────│───────────│─────────────┘   │
│          │           │           │           │           │                  │
│          ▼           ▼           ▼           ▼           ▼                  │
└─────────┼───────────┼───────────┼───────────┼───────────┼──────────────────┘
          │           │           │           │           │
          ▼           ▼           ▼           ▼           ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        监控基础设施层                                        │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐       │
│  │   Jaeger     │ │  Prometheus  │ │    Loki      │ │  Grafana     │       │
│  │  分布式追踪   │ │   指标存储    │ │   日志存储    │ │   可视化     │       │
│  └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘       │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. 核心模块说明

### 3.1 追踪模块 (`tracing.py`)

**职责**: 提供分布式追踪能力，支持 OpenTelemetry 标准

**核心功能**:
- 生成唯一 Trace ID 和 Span ID
- 创建追踪上下文管理器 `TraceContext`
- 支持 W3C Trace Context 规范
- 支持采样策略（概率采样、基于父 Span 采样）
- 追踪上下文注入/提取
- 异步 Span 处理和缓存

**关键类**:
- `TraceContext`: 追踪上下文管理器
- `TraceContextError`: 追踪上下文异常基类

**核心函数**:
- `get_trace_id()`: 获取当前 Trace ID
- `extract_trace_context()`: 从 HTTP 头提取追踪上下文
- `inject_trace_context()`: 生成追踪上下文请求头
- `@trace()`: 追踪装饰器
- `@async_trace()`: 异步追踪装饰器

---

### 3.2 指标模块 (`metrics.py`)

**职责**: 收集和导出系统运行指标

**核心功能**:
- Prometheus 指标导出（`/metrics` 端点）
- 自定义指标注册
- 直方图、计数器、仪表盘等指标类型
- 运行时指标收集（CPU、内存、连接数等）

**指标分类**:
| 类别 | 示例指标 |
|------|---------|
| HTTP | `yunshu_http_requests_total`, `yunshu_http_request_duration_seconds` |
| LLM | `yunshu_llm_calls_total`, `yunshu_llm_token_usage_total` |
| 安全 | `yunshu_security_blocks_total`, `yunshu_security_scans_total` |
| 系统 | `yunshu_cpu_usage_percent`, `yunshu_memory_usage_percent` |

---

### 3.3 日志模块 (`log_system/`)

**职责**: 结构化日志记录和查询

**核心功能**:
- JSON 格式结构化日志输出
- 自动关联 Trace ID
- 日志级别控制
- 日志过滤和查询
- 敏感数据过滤

**日志字段**:
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

### 3.4 告警模块 (`alert_manager.py`, `alert_evaluator.py`)

**职责**: 监控指标异常检测和告警通知

**核心功能**:
- 告警规则管理（创建、更新、删除）
- 告警表达式验证
- 告警触发和抑制
- 多渠道通知（HTTP、日志）

**告警规则示例**:
```yaml
groups:
  - name: yunshu_alerts
    rules:
      - alert: HighErrorRate
        expr: sum(rate(yunshu_http_requests_total{status=~"5.."}[5m])) / sum(rate(yunshu_http_requests_total[5m])) > 0.1
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "高错误率告警"
```

---

### 3.5 自愈模块 (`self_healer.py`)

**职责**: 自动检测和修复系统问题

**核心功能**:
- 健康状态评估
- 自动故障恢复
- 资源清理和优化
- 服务重启和故障转移

---

## 4. 数据流架构

### 4.1 追踪数据流

```
用户请求 → API网关 → TraceContext创建 → 业务处理 → Span结束 → 
    ↓                          ↓
   日志记录              OpenTelemetry SDK → BatchSpanProcessor → OTLP Exporter → 
                                                                     ↓
                                                              OpenTelemetry Collector → Jaeger
```

### 4.2 指标数据流

```
业务代码 → metrics模块 → Prometheus Client → /metrics端点 → Prometheus Server → Grafana
                                                              ↓
                                                      Alertmanager → 告警通知
```

### 4.3 日志数据流

```
业务代码 → SafeLogger → 结构化日志 → Loki Client → Loki Server → Grafana查询
                                                      ↓
                                               日志存储 (云存储/本地)
```

---

## 5. 关键技术栈

| 组件 | 技术 | 版本要求 | 说明 |
|------|------|---------|------|
| 分布式追踪 | OpenTelemetry | 1.20+ | 标准追踪协议 |
| 追踪可视化 | Jaeger | 1.50+ | 追踪数据展示 |
| 指标收集 | Prometheus | 2.40+ | 时序数据库 |
| 指标可视化 | Grafana | 10.0+ | 仪表盘展示 |
| 日志存储 | Loki | 2.9+ | 日志聚合 |
| 采样策略 | OpenTelemetry SDK | - | 多种采样器支持 |
| HTTP客户端 | requests | 2.31+ | API调用 |

---

## 6. 部署架构

### 6.1 开发环境

```
┌──────────────────────────────────────────┐
│              开发机器                      │
│  ┌──────────────┐  ┌──────────────┐      │
│  │   云枢智能体  │  │ 监控服务容器   │      │
│  │  (Python)    │  │(Jaeger/Prom) │      │
│  └──────────────┘  └──────────────┘      │
└──────────────────────────────────────────┘
```

### 6.2 生产环境

```
┌─────────────────────────────────────────────────────────────────┐
│                      Kubernetes Cluster                        │
│  ┌───────────────────────┐  ┌───────────────────────────────┐  │
│  │     云枢智能体 Pod      │  │         监控栈 Pod            │  │
│  │  - DigitalLife        │  │  - Prometheus Server          │  │
│  │  - MemoryStore        │  │  - Grafana                    │  │
│  │  - TaskPlanner        │  │  - Loki                       │  │
│  │  - ModelRouter        │  │  - Jaeger Collector           │  │
│  └───────────────────────┘  └───────────────────────────────┘  │
│                                │                              │
│                                ▼                              │
│                      ┌─────────────────┐                      │
│                      │   持久化存储     │                      │
│                      │ (Elasticsearch) │                      │
│                      └─────────────────┘                      │
└─────────────────────────────────────────────────────────────────┘
```

---

## 7. 配置管理

### 7.1 配置文件位置

| 配置项 | 路径 |
|--------|------|
| 主配置 | `config.yaml` |
| 监控配置 | `agent/monitoring/tracing_config.py` |
| Prometheus | `monitoring/prometheus.yml` |
| 告警规则 | `monitoring/alerts.yml` |
| Docker Compose | `monitoring/docker-compose.yml` |

### 7.2 环境变量

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| `TRACING_ENV` | 运行环境 | `development` |
| `TRACING_LOG_LEVEL` | 日志级别 | `DEBUG` |
| `TRACING_SAMPLER` | 采样器类型 | `ALWAYS_ON` |
| `TRACING_SAMPLER_RATIO` | 采样比例 | `1.0` |
| `TRACING_EXPORTER` | 导出器类型 | `CONSOLE` |

---

**文档版本**: v1.0  
**最后更新**: 2026年6月  
**适用版本**: 云枢智能体 v2.x