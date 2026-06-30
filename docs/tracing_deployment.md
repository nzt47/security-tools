# 追踪模块部署与配置文档

## 目录

1. [概述](#概述)
2. [环境变量配置](#环境变量配置)
3. [不同环境配置说明](#不同环境配置说明)
4. [快速开始](#快速开始)
5. [API 参考](#api-参考)
6. [边缘场景处理](#边缘场景处理)
7. [测试与验证](#测试与验证)
8. [常见问题](#常见问题)

---

## 概述

本模块提供分布式追踪（Distributed Tracing）功能，基于 OpenTelemetry 实现，
支持跨服务上下文传播、采样控制、性能监控等特性。

### 核心特性

- ✅ W3C Trace Context 标准兼容
- ✅ Jaeger / Zipkin 格式支持
- ✅ 多环境配置切换
- ✅ 高并发上下文隔离
- ✅ 网络异常容错处理
- ✅ 健康检查接口

---

## 环境变量配置

所有配置通过环境变量控制，无需修改代码。

| 环境变量 | 类型 | 默认值 | 说明 |
|---------|------|--------|------|
| `TRACING_ENV` | string | `development` | 环境类型：development/staging/production |
| `TRACING_LOG_LEVEL` | string | 按环境 | 日志级别：DEBUG/INFO/WARN/ERROR/CRITICAL |
| `TRACING_SAMPLER` | string | 按环境 | 采样器类型：ALWAYS_ON/ALWAYS_OFF/RATIO/PARENT_BASED |
| `TRACING_SAMPLER_RATIO` | float | `0.1` | 采样比例（0.0-1.0，仅 RATIO 类型有效） |
| `TRACING_EXPORTER` | string | 按环境 | 导出器类型：CONSOLE/OTLP |

---

## 不同环境配置说明

### 开发环境 (development)

```bash
# Windows PowerShell
$env:TRACING_ENV="development"

# Linux/macOS
export TRACING_ENV=development
```

**默认配置：**
- 日志级别：`DEBUG`
- 采样器：`ALWAYS_ON`（全部采样）
- 导出器：`CONSOLE`（控制台输出）
- 调试模式：开启

**适用场景：**
- 本地开发调试
- 问题排查
- 功能验证

### 测试环境 (staging)

```bash
# Windows PowerShell
$env:TRACING_ENV="staging"

# Linux/macOS
export TRACING_ENV=staging
```

**默认配置：**
- 日志级别：`INFO`
- 采样器：`ALWAYS_ON`（全部采样）
- 导出器：`CONSOLE`（控制台输出）
- 调试模式：关闭

**适用场景：**
- 集成测试
- 性能测试
- 预发布验证

### 生产环境 (production)

```bash
# Windows PowerShell
$env:TRACING_ENV="production"

# Linux/macOS
export TRACING_ENV=production
```

**默认配置：**
- 日志级别：`WARN`
- 采样器：`RATIO`（比例采样）
- 采样比例：`0.1`（10%）
- 导出器：`OTLP`（OTLP 协议）
- 调试模式：关闭

**适用场景：**
- 生产部署
- 高并发场景
- 成本控制

---

## 快速开始

### 1. 基础使用

```python
from agent.monitoring import TraceContext, get_trace_id

# 创建追踪上下文
with TraceContext("UserService", "get_user") as ctx:
    print(f"Trace ID: {ctx.trace_id}")
    print(f"Span ID: {ctx.span_id}")
    
    # 添加事件
    ctx.add_event("db_query", {"table": "users"})
    
    # 设置属性
    ctx.set_attribute("user_id", "123")
```

### 2. 跨服务调用

```python
import requests
from agent.monitoring import inject_trace_context, extract_trace_context

# 服务 A：注入上下文到请求头
def call_service_b():
    with TraceContext("ServiceA", "call_b"):
        headers = inject_trace_context()
        response = requests.get("http://service-b/api", headers=headers)
        return response.json()

# 服务 B：从请求头提取上下文
def handle_request(request):
    context = extract_trace_context(request.headers)
    if context:
        set_trace_id(context['trace_id'])
        set_span_id(context['span_id'])
    
    with TraceContext("ServiceB", "handle"):
        # 处理请求
        pass
```

### 3. 使用装饰器

```python
from agent.monitoring import trace

@trace("UserService", "get_user")
def get_user(user_id):
    # 函数逻辑
    return user
```

---

## API 参考

### 核心类

#### TraceContext

上下文管理器，用于创建和管理追踪 Span。

```python
TraceContext(
    service_name: str,      # 服务名称
    operation: str,         # 操作名称
    span_kind: str = "internal",  # Span 类型
    attributes: dict = None       # 附加属性
)
```

**方法：**
- `add_event(name, attributes)`：添加事件
- `set_attribute(key, value)`：设置属性
- `set_status(status)`：设置状态

### 核心函数

#### 上下文管理

| 函数 | 说明 |
|------|------|
| `get_trace_id()` | 获取当前 Trace ID |
| `get_span_id()` | 获取当前 Span ID |
| `set_trace_id(trace_id)` | 设置 Trace ID |
| `set_span_id(span_id)` | 设置 Span ID |
| `capture_context()` | 捕获当前上下文 |
| `restore_context(context)` | 恢复上下文 |
| `run_with_context(context, func, *args, **kwargs)` | 在指定上下文中运行函数 |

#### 上下文传播

| 函数 | 说明 |
|------|------|
| `inject_trace_context()` | 注入上下文到请求头 |
| `extract_trace_context(headers)` | 从请求头提取上下文 |
| `safe_extract_trace_context(headers)` | 安全提取（永不抛异常） |
| `safe_inject_trace_context()` | 安全注入（永不抛异常） |

#### 诊断与健康检查

| 函数 | 说明 |
|------|------|
| `diagnose_opentelemetry_config()` | 诊断 OpenTelemetry 配置 |
| `print_diagnosis_report()` | 打印诊断报告 |
| `check_context_consistency()` | 检查上下文一致性 |
| `detect_context_loss_scenarios()` | 检测上下文丢失场景 |
| `print_context_diagnosis()` | 打印上下文诊断 |
| `check_tracing_health()` | 检查追踪系统健康状态 |

#### 网络异常处理

| 函数/装饰器 | 说明 |
|------------|------|
| `trace_network_call(service, op, timeout_ms)` | 网络调用上下文管理器 |
| `with_trace_context_retry(max_retries, delay_ms)` | 带重试的装饰器 |

### 异常类

| 异常类 | 说明 |
|--------|------|
| `TraceContextError` | 追踪上下文基础异常 |
| `InvalidTraceParentError` | 无效的 traceparent 格式 |
| `InvalidUberTraceIdError` | 无效的 Jaeger 格式 |
| `TraceIdValidationError` | Trace ID 验证失败 |
| `TraceContextTimeoutError` | 上下文超时错误 |
| `TraceContextNetworkError` | 网络错误 |

---

## 边缘场景处理

### 1. 网络超时

使用 `trace_network_call` 上下文管理器处理超时：

```python
from agent.monitoring import trace_network_call

with trace_network_call("PaymentService", "charge", timeout_ms=3000) as ctx:
    # 执行网络调用
    result = requests.post(
        "https://api.payment.com/charge",
        headers=ctx.headers,
        timeout=3  # 与 timeout_ms 保持一致
    )
```

**特性：**
- 自动检测超时并记录事件
- 异常时上下文正确保留
- 退出时自动恢复原始上下文

### 2. 网络中断 / 断网

使用 `safe_extract_trace_context` 和 `safe_inject_trace_context`：

```python
from agent.monitoring import safe_extract_trace_context, safe_inject_trace_context

# 安全提取（即使格式错误也不会崩溃）
context = safe_extract_trace_context(headers)
if context:
    # 有有效上下文
    set_trace_id(context['trace_id'])
else:
    # 没有有效上下文，创建新的
    pass

# 安全注入
headers = safe_inject_trace_context()
```

### 3. 重试场景

使用 `with_trace_context_retry` 装饰器：

```python
from agent.monitoring import with_trace_context_retry

@with_trace_context_retry(max_retries=3, retry_delay_ms=100)
def call_remote_api(url, headers):
    response = requests.get(url, headers=headers, timeout=5)
    response.raise_for_status()
    return response.json()
```

### 4. 高并发场景

ContextVar 天然支持线程安全和异步安全：

```python
import concurrent.futures
from agent.monitoring import TraceContext, inject_trace_context

def process_request(request_id):
    with TraceContext("API", f"req_{request_id}") as ctx:
        # 每个请求有独立的 trace_id
        headers = inject_trace_context()
        # 处理请求
        return ctx.trace_id

# 并发处理
with concurrent.futures.ThreadPoolExecutor(max_workers=100) as executor:
    futures = [executor.submit(process_request, i) for i in range(1000)]
    results = [f.result() for f in futures]
```

### 5. 嵌套上下文

TraceContext 支持嵌套，退出时自动恢复：

```python
with TraceContext("Outer", "operation") as outer:
    print(f"Outer: {outer.trace_id}")
    
    with TraceContext("Inner", "sub_operation") as inner:
        # 内部继承 trace_id，但 span_id 不同
        print(f"Inner trace: {inner.trace_id}")  # 与 outer 相同
        print(f"Inner span: {inner.span_id}")    # 新的 span_id
    
    # 退出内部后，回到外部上下文
    print(f"After inner: {get_span_id()}")  # outer 的 span_id
```

---

## 测试与验证

### 运行单元测试

```bash
# 运行所有单元测试
python -m pytest tests/unit/test_tracing_context_propagation.py -v

# 运行特定测试
python -m pytest tests/unit/test_tracing_context_propagation.py::TestCrossServicePropagation -v
```

### 运行集成测试

```bash
python tests/integration/test_trace_integration.py -v
```

### 运行压测

```bash
# 简化版压测（推荐）
python tests/stress/test_stress_simple.py

# 完整版压测（详细报告）
python tests/stress/test_tracing_stress.py
```

### 验证配置

```python
from agent.monitoring.tracing_config import tracing_config, get_sampler

print(f"环境: {tracing_config.env}")
print(f"日志级别: {tracing_config.log_level}")
print(f"采样器: {tracing_config.sampler_type}")
```

### 健康检查

```python
from agent.monitoring import check_tracing_health

health = check_tracing_health()
print(f"健康状态: {'正常' if health['healthy'] else '异常'}")
print(f"组件: {health['components']}")
```

---

## 常见问题

### Q1: 为什么看不到 DEBUG 日志？

**A:** 确保日志级别设置正确：

```bash
# 开发环境默认就是 DEBUG
$env:TRACING_ENV="development"

# 或手动设置
$env:TRACING_LOG_LEVEL="DEBUG"
```

### Q2: 生产环境如何调整采样率？

**A:** 通过环境变量调整：

```bash
# 设置 20% 采样率
$env:TRACING_SAMPLER="RATIO"
$env:TRACING_SAMPLER_RATIO="0.2"
```

### Q3: 如何验证上下文是否正确传播？

**A:** 使用诊断函数：

```python
from agent.monitoring import print_context_diagnosis

with TraceContext("Test", "operation"):
    print_context_diagnosis()
```

### Q4: 网络异常时上下文会丢失吗？

**A:** 不会。TraceContext 使用 `try/finally` 确保上下文正确恢复。
对于网络调用，推荐使用 `trace_network_call` 上下文管理器。

### Q5: 支持哪些传播格式？

**A:** 目前支持：
- W3C Trace Context（`traceparent` / `tracestate`）
- Jaeger（`uber-trace-id`）

### Q6: 如何在异步代码中使用？

**A:** ContextVar 天然支持 asyncio，可以直接使用：

```python
import asyncio
from agent.monitoring import TraceContext

async def async_operation():
    with TraceContext("AsyncService", "op") as ctx:
        await asyncio.sleep(1)
        return ctx.trace_id
```

---

## 相关文件

| 文件路径 | 说明 |
|---------|------|
| `agent/monitoring/tracing.py` | 追踪核心模块 |
| `agent/monitoring/tracing_config.py` | 配置管理模块 |
| `agent/monitoring/__init__.py` | 模块导出 |
| `tests/unit/test_tracing_context_propagation.py` | 单元测试 |
| `tests/integration/test_trace_integration.py` | 集成测试 |
| `tests/stress/test_stress_simple.py` | 简化压测 |
| `tests/stress/test_tracing_stress.py` | 完整压测 |

---

## 更新记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 1.0.0 | 2026-06-24 | 初始版本，支持基础追踪功能 |
| 1.1.0 | 2026-06-24 | 添加网络异常处理、健康检查、压测工具 |