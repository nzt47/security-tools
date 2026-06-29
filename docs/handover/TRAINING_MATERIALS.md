# 云枢智能体 - 可观测性培训材料

## 概述

本文档是为新维护人员准备的可观测性体系培训材料，包含基础概念、系统设计、代码解读和扩展开发指南。

---

## 目录

1. [可观测性基础概念](#1-可观测性基础概念)
2. [系统设计思路](#2-系统设计思路)
3. [核心代码解读](#3-核心代码解读)
4. [扩展开发指南](#4-扩展开发指南)

---

## 1. 可观测性基础概念

### 1.1 什么是可观测性

可观测性是指通过外部输出（指标、日志、追踪）推断系统内部状态的能力。

**三大支柱**:
- **Metrics（指标）**: 数值化的测量数据，如 CPU 使用率、请求延迟
- **Logging（日志）**: 事件的离散记录，用于追踪系统行为
- **Tracing（追踪）**: 分布式系统中请求的完整路径记录

### 1.2 可观测性 vs 监控

| 对比维度 | 监控 | 可观测性 |
|---------|------|---------|
| 关注点 | 已知的已知 | 未知的未知 |
| 方法 | 预设指标告警 | 探索式分析 |
| 响应方式 | 被动响应 | 主动发现 |
| 适用场景 | 已知问题 | 未知问题 |

### 1.3 关键概念

**分布式追踪**:
- **Trace（追踪）**: 一个请求从开始到结束的完整路径
- **Span（跨度）**: 追踪中的单个操作单元
- **Span Context（上下文）**: 跨服务传递的追踪信息
- **Trace ID / Span ID**: 追踪和跨度的唯一标识

**指标类型**:
- **Counter（计数器）**: 单调递增的值
- **Histogram（直方图）**: 分布统计
- **Gauge（仪表盘）**: 瞬时值
- **Summary（摘要）**: 分位数统计

---

## 2. 系统设计思路

### 2.1 架构设计原则

**分层架构**:
```
┌─────────────────────────────────────────────────────────────┐
│                     应用层 (Application)                    │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│  │   API服务   │  │  智能体服务  │  │  工具服务   │        │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘        │
└─────────┼────────────────┼────────────────┼─────────────────┘
          │                │                │
          ▼                ▼                ▼
┌─────────────────────────────────────────────────────────────┐
│                   可观测性层 (Observability)                 │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Tracing    │  Metrics    │  Logging    │ Alerting  │    │
│  │  分布式追踪  │  指标监控    │  日志系统   │  告警系统  │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
          │                │                │
          ▼                ▼                ▼
┌─────────────────────────────────────────────────────────────┐
│                    数据存储层 (Storage)                      │
│   Prometheus    │    Jaeger     │     ELK      │  AlertMgr  │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 设计模式

**装饰器模式**:
```python
@traced(span_name="process_request")
def process_request(request):
    pass
```

**上下文管理器模式**:
```python
with TraceContext("service", "operation") as ctx:
    ctx.add_attribute("key", "value")
    # 业务逻辑
```

**工厂模式**:
```python
class ExporterFactory:
    @staticmethod
    def create_exporter(type):
        if type == "OTLP":
            return OTLPExporter()
        elif type == "JAEGER":
            return JaegerExporter()
```

### 2.3 数据流设计

```
请求进入 → 创建Span → 记录指标 → 输出日志 → 导出数据
     │           │           │           │           │
     ▼           ▼           ▼           ▼           ▼
   API层      TraceContext  Metrics    Logger     Exporter
                    │           │           │           │
                    └───────────┴───────────┴───────────┘
                                        │
                                        ▼
                                  监控存储系统
```

---

## 3. 核心代码解读

### 3.1 追踪模块核心代码

**TraceContext 上下文管理器**:
```python
class TraceContext:
    def __init__(self, service_name: str, operation_name: str):
        self.service_name = service_name
        self.operation_name = operation_name
        self._span = None
    
    def __enter__(self):
        # 创建Span
        tracer = get_tracer(self.service_name)
        self._span = tracer.start_span(self.operation_name)
        
        # 设置Span属性
        self._span.set_attribute("service.name", self.service_name)
        
        # 将Span注入到上下文中
        token = TRACE_CONTEXT.set(self._span)
        self._token = token
        
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        # 结束Span
        if self._span:
            if exc_type:
                self._span.set_status(Status(StatusCode.ERROR))
                self._span.record_exception(exc_val)
            self._span.end()
        
        # 恢复上下文
        TRACE_CONTEXT.reset(self._token)
    
    def add_attribute(self, key: str, value):
        if self._span:
            self._span.set_attribute(key, value)
```

**关键设计点**:
- 使用 `contextvars` 实现线程安全的上下文传递
- `__enter__` 创建Span并注入上下文
- `__exit__` 自动结束Span并处理异常
- 支持异常状态记录

### 3.2 指标模块核心代码

**Histogram 指标**:
```python
class LatencyHistogram:
    def __init__(self, name: str, description: str):
        self._histogram = Histogram(
            name=name,
            description=description,
            unit="seconds",
            buckets=[0.001, 0.01, 0.1, 0.5, 1.0, 5.0]
        )
    
    def record(self, duration: float, **labels):
        self._histogram.observe(duration, **labels)
    
    def get_summary(self):
        """获取统计摘要"""
        histogram_data = self._histogram.collect()[0]
        buckets = histogram_data.buckets
        
        return {
            "count": histogram_data.count,
            "sum": histogram_data.sum,
            "p50": self._get_percentile(buckets, 0.5),
            "p95": self._get_percentile(buckets, 0.95),
            "p99": self._get_percentile(buckets, 0.99),
            "avg": histogram_data.sum / histogram_data.count if histogram_data.count > 0 else 0
        }
```

### 3.3 日志模块核心代码

**结构化日志**:
```python
class StructuredLogger:
    def __init__(self, name: str):
        self._logger = logging.getLogger(name)
        self._formatter = JsonFormatter()
    
    def _get_trace_info(self):
        """获取当前追踪上下文信息"""
        span = TRACE_CONTEXT.get()
        if span:
            return {
                "trace_id": format_trace_id(span.get_span_context().trace_id),
                "span_id": format_span_id(span.get_span_context().span_id)
            }
        return {}
    
    def info(self, message: str, **kwargs):
        extra = {**self._get_trace_info(), **kwargs}
        self._logger.info(message, extra=extra)
    
    def error(self, message: str, **kwargs):
        extra = {**self._get_trace_info(), **kwargs}
        self._logger.error(message, extra=extra)
```

---

## 4. 扩展开发指南

### 4.1 添加新的追踪装饰器

**步骤**:
1. 在 `agent/monitoring/tracing/decorators.py` 中添加新装饰器
2. 在 `agent/monitoring/tracing/__init__.py` 中导出
3. 在需要的地方导入使用

**示例**:
```python
def traced_with_custom_tags(**tags):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            service_name = tags.get("service", "unknown")
            operation_name = tags.get("operation", func.__name__)
            
            with TraceContext(service_name, operation_name) as ctx:
                for key, value in tags.items():
                    if key not in ["service", "operation"]:
                        ctx.add_attribute(key, value)
                
                return func(*args, **kwargs)
        
        return wrapper
    return decorator
```

### 4.2 添加新的指标

**步骤**:
1. 在 `agent/monitoring/metrics/__init__.py` 中注册新指标
2. 在 `MetricsRegistry` 中添加指标定义
3. 在业务代码中使用

**示例**:
```python
# 在 metrics/__init__.py 中注册
registry = MetricsRegistry()
registry.register_histogram(
    name="api_request_duration",
    description="API请求延迟",
    buckets=[0.001, 0.01, 0.1, 0.5, 1.0, 5.0]
)

# 在业务代码中使用
from agent.monitoring.metrics import registry

def handle_request():
    start_time = time.time()
    try:
        # 业务逻辑
        pass
    finally:
        duration = time.time() - start_time
        registry.histograms["api_request_duration"].record(
            duration,
            endpoint="/api/test",
            status_code=200
        )
```

### 4.3 添加新的导出器

**步骤**:
1. 创建新的导出器类，继承 `BaseExporter`
2. 实现 `export()` 方法
3. 在 `ExporterFactory` 中注册

**示例**:
```python
class CustomExporter(BaseExporter):
    def __init__(self, config):
        self.config = config
    
    def export(self, data):
        # 实现导出逻辑
        print(f"Exporting to custom destination: {data}")

# 在工厂中注册
class ExporterFactory:
    @staticmethod
    def create_exporter(type, config):
        if type == "CUSTOM":
            return CustomExporter(config)
        # ... 其他导出器
```

### 4.4 添加新的告警规则

**步骤**:
1. 在 `config/alert_rules.yaml` 中定义规则
2. 在 `AlertManager` 中加载规则
3. 配置告警通知方式

**示例**:
```yaml
rules:
  - name: high_latency_alert
    description: API延迟超过1秒
    expr: api_request_duration_p95 > 1
    for: 5m
    labels:
      severity: warning
    annotations:
      summary: "高延迟告警"
      description: "P95延迟超过1秒"
```

---

## 培训进度规划

| 阶段 | 主题 | 时长 | 目标 |
|------|------|------|------|
| 第1天 | 可观测性基础概念 | 2小时 | 理解三大支柱 |
| 第1天 | 系统架构概述 | 2小时 | 理解整体设计 |
| 第2天 | 追踪模块详解 | 3小时 | 掌握追踪实现 |
| 第2天 | 指标模块详解 | 3小时 | 掌握指标实现 |
| 第3天 | 日志模块详解 | 2小时 | 掌握日志实现 |
| 第3天 | 告警系统详解 | 2小时 | 掌握告警配置 |
| 第4天 | 实战演练 | 4小时 | 动手实践 |
| 第5天 | 故障排查实战 | 4小时 | 模拟故障处理 |

---

**文档版本**: v1.0  
**最后更新**: 2026年6月  
**适用版本**: 云枢智能体 v2.x