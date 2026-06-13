# 错误处理流程文档

## 1. 概述

本文档详细描述灵犀系统中的错误处理机制，包括统一错误处理装饰器、配置校验逻辑、模块导入错误处理和日志轮转配置。

---

## 2. 错误处理装饰器

### 2.1 装饰器列表

| 装饰器名称 | 用途 | 适用场景 |
|-----------|------|----------|
| `handle_errors` | 统一错误处理 | 通用错误处理场景，支持重试、上报、日志 |
| `catch_and_report` | 捕获指定异常并上报 | 特定异常类型的精确处理 |
| `safe_call` | 安全调用，捕获所有异常 | 需要保证函数不抛异常的场景 |
| `async_handle_errors` | 异步函数错误处理 | 异步函数的错误处理 |

### 2.2 `handle_errors` 装饰器

#### 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `error_category` | ErrorCategory | UNKNOWN | 错误分类 |
| `error_severity` | ErrorSeverity | ERROR | 错误严重级别 |
| `report_error` | bool | True | 是否上报到监控系统 |
| `log_error` | bool | True | 是否记录错误日志 |
| `return_on_error` | Any | None | 错误时返回的默认值 |
| `retry_on_error` | bool | False | 是否自动重试 |
| `max_retries` | int | 3 | 最大重试次数 |
| `retry_delay` | float | 1.0 | 重试间隔（秒） |
| `error_counter` | str | None | 错误计数器名称 |
| `ignored_exceptions` | Tuple[Type[Exception], ...] | () | 忽略的异常类型 |
| `service_name` | str | None | 服务名称 |

#### 使用场景

```python
from agent.monitoring.decorators import handle_errors
from agent.error_handler import ErrorCategory, ErrorSeverity

@handle_errors(
    error_category=ErrorCategory.EXTERNAL_SERVICE,
    error_severity=ErrorSeverity.CRITICAL,
    report_error=True,
    retry_on_error=True,
    max_retries=3,
    retry_delay=2.0,
    return_on_error="服务暂时不可用"
)
def call_external_api():
    # 调用外部服务
    response = requests.get("https://api.example.com/data")
    return response.json()
```

#### 执行流程

```
┌─────────────────────────────────────────────────────────────┐
│                    handle_errors 装饰器流程                 │
├─────────────────────────────────────────────────────────────┤
│  1. 执行被装饰函数                                          │
│           │                                                │
│           ▼                                                │
│  2. 捕获异常                                               │
│     │                                                     │
│     ├─→ [忽略的异常] → 直接重新抛出                         │
│     │                                                     │
│     └─→ [其他异常]                                         │
│           │                                                │
│           ▼                                                │
│  3. 记录错误日志 (log_error=True)                           │
│           │                                                │
│           ▼                                                │
│  4. 增加错误计数器 (error_counter指定)                      │
│           │                                                │
│           ▼                                                │
│  5. 上报错误到监控系统 (report_error=True)                  │
│           │                                                │
│           ▼                                                │
│  6. 判断是否重试 (retry_on_error=True)                     │
│     │                                                     │
│     ├─→ [还有重试次数] → 等待 retry_delay 后重试            │
│     │                                                     │
│     └─→ [无重试次数]                                        │
│           │                                                │
│           ▼                                                │
│  7. 返回默认值或重新抛出异常                                │
│     │                                                     │
│     ├─→ [return_on_error 设置] → 返回默认值                │
│     │                                                     │
│     └─→ [return_on_error 未设置] → 重新抛出异常             │
└─────────────────────────────────────────────────────────────┘
```

### 2.3 `catch_and_report` 装饰器

#### 使用场景

```python
from agent.monitoring.decorators import catch_and_report
from agent.monitoring.error_reporter import AlertLevel

@catch_and_report(ValueError, level=AlertLevel.WARNING)
def process_data(data):
    if not data:
        raise ValueError("数据不能为空")
    return process(data)
```

### 2.4 `safe_call` 装饰器

#### 使用场景

```python
from agent.monitoring.decorators import safe_call

@safe_call(default_return="操作失败", log_errors=True, report_errors=False)
def risky_operation():
    # 可能失败的操作
    result = perform_risky_task()
    return result
```

### 2.5 `async_handle_errors` 装饰器

#### 使用场景

```python
from agent.monitoring.decorators import async_handle_errors

@async_handle_errors(
    report_error=True,
    retry_on_error=True,
    max_retries=2,
    return_on_error=None
)
async def fetch_data_async():
    async with aiohttp.ClientSession() as session:
        async with session.get("https://api.example.com/data") as resp:
            return await resp.json()
```

---

## 3. 配置校验逻辑

### 3.1 校验流程

```
┌─────────────────────────────────────────────────────────────┐
│                    配置校验流程                             │
├─────────────────────────────────────────────────────────────┤
│  1. 调用 validate_config(config)                           │
│           │                                                │
│           ▼                                                │
│  2. 检查 Pydantic 是否可用                                 │
│     │                                                     │
│     ├─→ [可用] → 使用 Pydantic 模型校验                     │
│     │         │                                            │
│     │         ├─→ 校验通过 → 返回空列表                     │
│     │         │                                            │
│     │         └─→ 校验失败 → 返回错误列表                   │
│     │                                                     │
│     └─→ [不可用] → 使用基础校验 (_basic_validation)         │
│               │                                            │
│               ├─→ 检查必需配置节                           │
│               ├─→ 检查 memory 配置                         │
│               ├─→ 检查 behavior 配置                       │
│               └─→ 检查 security 配置                       │
│                     │                                      │
│                     ▼                                      │
│               返回错误列表                                  │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 校验模型

#### LLMConfig

| 字段 | 类型 | 默认值 | 校验规则 |
|------|------|--------|----------|
| `provider` | str | "" | 必须是 "openai" 或 "anthropic" |
| `api_key` | str | "" | 无 |
| `model` | str | "" | 无 |
| `timeout` | int | 30 | 1-300 之间的整数 |

#### MemoryConfig

| 字段 | 类型 | 默认值 | 校验规则 |
|------|------|--------|----------|
| `data_dir` | str | "./data" | 无 |
| `token_limit` | int | 4096 | 512-32768 之间的整数 |
| `compress_threshold` | float | 0.8 | 0.0-1.0 之间 |
| `async_compress` | AsyncCompressConfig | 默认 | 嵌套校验 |
| `llm` | LLMConfig | 默认 | 嵌套校验 |
| `blackbox` | BlackboxConfig | 默认 | 嵌套校验 |

### 3.3 自动修复功能

`validate_and_fix_config` 函数提供自动修复功能：

```python
from config import validate_and_fix_config

config = {"sensor": {}}  # 缺少多个配置节

fixed_config, errors = validate_and_fix_config(config)
# fixed_config 将包含所有必需配置节的默认值
# errors 包含修复记录
```

---

## 4. 模块导入错误处理

### 4.1 安全导入函数

#### `_safe_import`

```python
def _safe_import(module_name: str, import_func, fallback_value=None) -> Tuple[Any, bool]:
    # 返回值：(导入的对象或回退值, 是否成功)
```

#### `_safe_import_from`

```python
def _safe_import_from(package: str, *names: str) -> Tuple[Dict[str, Any], bool]:
    # 返回值：({名称: 对象}, 是否全部成功)
```

### 4.2 导入策略

| 模块类型 | 处理方式 | 失败影响 |
|----------|----------|----------|
| 核心模块 | try-except 包裹，失败则终止程序 | 无法启动 |
| 可选模块 | 使用 `_safe_import` 安全导入 | 功能禁用 |

### 4.3 导入示例

```python
# 核心模块 - 必须成功
try:
    from sensor import BodySensor
    from cognitive import PromptInjector
except ImportError as e:
    logger.critical(f"核心模块导入失败: {e}")
    raise

# 可选模块 - 安全导入
_lifetrace_modules, _LIFETRACE_AVAILABLE = _safe_import_from(
    'lifetrace', 'TraceRecorder', 'MemoryRetriever'
)
```

### 4.4 状态报告

模块导入完成后会自动生成状态报告：

```
════════════════════════════════════════════════════════════════════════════════
📦 模块导入状态汇总
════════════════════════════════════════════════════════════════════════════════
   ✅ lifetrace: 已加载
   ✅ persona: 已加载
   ❌ planning: 未加载
   ✅ vector_memory: 已加载
════════════════════════════════════════════════════════════════════════════════
   总计: 3/4 模块加载成功
   ⚠️ 1 个可选模块未加载，相关功能将被禁用
════════════════════════════════════════════════════════════════════════════════
```

---

## 5. 日志轮转配置

### 5.1 LogRotationConfig 配置

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_bytes` | int | 50MB | 单个日志文件最大大小 |
| `backup_count` | int | 5 | 保留的备份文件数量 |
| `encoding` | str | "utf-8" | 日志文件编码 |
| `when` | str | "midnight" | 时间轮转单位 |
| `interval` | int | 1 | 轮转间隔 |
| `utc` | bool | False | 是否使用 UTC 时间 |
| `use_timed_rotation` | bool | False | 是否使用时间轮转 |

### 5.2 两种轮转模式

| 模式 | 触发条件 | 适用场景 |
|------|----------|----------|
| 大小轮转 | 文件达到 max_bytes | 需要限制单个文件大小 |
| 时间轮转 | 达到指定时间间隔 | 需要按时间归档 |

### 5.3 使用示例

```python
from agent.logging_utils import LogRotationConfig, setup_agent_logging, setup_error_logging

# 配置大小轮转（50MB/文件，保留5个备份）
rotation_config = LogRotationConfig(
    max_bytes=50 * 1024 * 1024,
    backup_count=5
)

# 配置时间轮转（每天轮转）
timed_rotation = LogRotationConfig(
    when="midnight",
    interval=1,
    backup_count=7,
    use_timed_rotation=True
)

# 设置主日志系统
logger = setup_agent_logging(
    debug_mode=True,
    log_file="./logs/agent.log",
    rotation_config=rotation_config
)

# 设置错误日志（独立轮转）
error_logger = setup_error_logging(
    log_file="./logs/errors.log",
    rotation_config=LogRotationConfig(
        max_bytes=20 * 1024 * 1024,
        backup_count=10
    )
)
```

---

## 6. 错误分类与严重级别

### 6.1 ErrorCategory（错误分类）

| 分类 | 说明 |
|------|------|
| `UNKNOWN` | 未知错误 |
| `EXTERNAL_SERVICE` | 外部服务错误 |
| `DATA_INVALID` | 数据无效 |
| `CONFIG_ERROR` | 配置错误 |
| `INTERNAL_ERROR` | 内部错误 |
| `AUTHENTICATION` | 认证错误 |
| `RATE_LIMIT` | 限流错误 |

### 6.2 ErrorSeverity（错误严重级别）

| 级别 | 说明 | 处理建议 |
|------|------|----------|
| `DEBUG` | 调试信息 | 仅记录 |
| `INFO` | 一般信息 | 仅记录 |
| `WARNING` | 警告 | 记录并关注 |
| `ERROR` | 错误 | 记录、上报、通知 |
| `CRITICAL` | 严重错误 | 记录、上报、通知、可能需要重启 |

---

## 7. 最佳实践

### 7.1 装饰器选择指南

| 场景 | 推荐装饰器 |
|------|------------|
| 外部 API 调用 | `handle_errors(retry_on_error=True)` |
| 数据处理 | `catch_and_report(ValueError)` |
| 工具函数 | `safe_call(default_return=None)` |
| 异步操作 | `async_handle_errors()` |

### 7.2 配置校验最佳实践

1. 在应用启动时调用 `validate_config` 进行预检查
2. 使用 `validate_and_fix_config` 自动修复配置问题
3. 配置变更时重新校验

### 7.3 错误处理最佳实践

1. 为每个关键函数添加错误处理装饰器
2. 合理设置错误分类和严重级别
3. 对于可恢复错误启用重试机制
4. 关键业务异常必须上报监控系统

---

## 8. 监控与审计

### 8.1 错误上报

所有通过装饰器处理的错误都会自动上报到监控系统，包含以下信息：

- 错误类型和消息
- 函数名称和参数
- Trace ID（用于链路追踪）
- 尝试次数
- 时间戳

### 8.2 审计日志

审计日志记录安全相关操作：

- 配置访问和修改
- 权限变更
- 敏感信息访问
- 认证尝试

---

## 附录：错误处理流程图

```
┌─────────────────────────────────────────────────────────────────────┐
│                     错误处理总流程图                                │
├─────────────────────────────────────────────────────────────────────┤
│                                                                    │
│   用户请求                                                         │
│       │                                                            │
│       ▼                                                            │
│   ┌──────────────────┐                                             │
│   │  handle_errors   │ ← 统一错误处理装饰器                          │
│   └────────┬─────────┘                                             │
│            │                                                       │
│            ▼                                                       │
│   ┌──────────────────┐                                             │
│   │   执行函数        │                                             │
│   └────────┬─────────┘                                             │
│            │                                                       │
│      ┌─────┴─────┐                                                 │
│      │           │                                                 │
│      ▼           ▼                                                 │
│   成功        异常                                                  │
│      │           │                                                 │
│      │           ▼                                                 │
│      │   ┌──────────────────┐                                     │
│      │   │ 记录错误日志      │                                     │
│      │   └────────┬─────────┘                                     │
│      │            │                                                │
│      │            ▼                                                │
│      │   ┌──────────────────┐                                     │
│      │   │ 上报监控系统      │ ← get_error_reporter().report_error │
│      │   └────────┬─────────┘                                     │
│      │            │                                                │
│      │            ▼                                                │
│      │   ┌──────────────────┐                                     │
│      │   │ 记录到错误处理器  │ ← get_error_handler().record_error  │
│      │   └────────┬─────────┘                                     │
│      │            │                                                │
│      │            ▼                                                │
│      │   ┌──────────────────┐                                     │
│      │   │  判断是否重试     │                                     │
│      │   └────────┬─────────┘                                     │
│      │            │                                                │
│      │      ┌─────┴─────┐                                          │
│      │      │           │                                          │
│      │      ▼           ▼                                          │
│      │   重试         返回/抛出                                     │
│      │      │           │                                          │
│      │      └───────────┘                                          │
│      │                                                            │
│      └─────────────┬───────────────────────────────────────────────┘
│                    │                                                │
│                    ▼                                                │
│              返回结果                                               │
│                                                                    │
└─────────────────────────────────────────────────────────────────────┘
```

---

**文档版本**: v1.0  
**创建日期**: 2026-06-03  
**适用范围**: 灵犀数字生命体系统
