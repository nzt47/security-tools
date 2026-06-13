# 错误处理代码注释模板

## 快速复制模板

---

### 模板1：handle_errors 装饰器 - 外部API调用

```python
# ============================================================================
# 外部API调用错误处理模板
# 适用场景：调用外部服务、网络请求、第三方API
# 配置要点：启用重试机制、设置合理的重试次数和间隔
# ============================================================================
from agent.monitoring.decorators import handle_errors
from agent.error_handler import ErrorCategory, ErrorSeverity

@handle_errors(
    error_category=ErrorCategory.EXTERNAL_SERVICE,
    error_severity=ErrorSeverity.CRITICAL,
    report_error=True,
    log_error=True,
    retry_on_error=True,
    max_retries=3,
    retry_delay=2.0,
    return_on_error=None  # 失败时返回None，调用方需处理
)
def call_external_api(*args, **kwargs):
    """调用外部API"""
    # TODO: 实现API调用逻辑
    pass
```

---

### 模板2：catch_and_report 装饰器 - 数据处理

```python
# ============================================================================
# 数据处理错误捕获模板
# 适用场景：数据校验、转换、解析等场景
# 配置要点：指定需要捕获的异常类型
# ============================================================================
from agent.monitoring.decorators import catch_and_report
from agent.monitoring.error_reporter import AlertLevel

@catch_and_report(ValueError, level=AlertLevel.WARNING)
@catch_and_report(TypeError, level=AlertLevel.WARNING)
def process_data(data):
    """处理数据，捕获特定异常"""
    # TODO: 实现数据处理逻辑
    pass
```

---

### 模板3：safe_call 装饰器 - 工具函数

```python
# ============================================================================
# 安全调用模板（工具函数）
# 适用场景：工具函数、辅助函数、非关键路径
# 配置要点：设置默认返回值，确保函数不会抛出异常
# ============================================================================
from agent.monitoring.decorators import safe_call

@safe_call(
    default_return=None,
    log_errors=True,
    report_errors=False
)
def utility_function(*args, **kwargs):
    """工具函数，确保不会抛出异常"""
    # TODO: 实现工具函数逻辑
    pass
```

---

### 模板4：async_handle_errors 装饰器 - 异步操作

```python
# ============================================================================
# 异步操作错误处理模板
# 适用场景：异步函数、协程、异步IO操作
# 配置要点：与同步装饰器参数一致
# ============================================================================
from agent.monitoring.decorators import async_handle_errors
from agent.error_handler import ErrorCategory

@async_handle_errors(
    error_category=ErrorCategory.EXTERNAL_SERVICE,
    report_error=True,
    retry_on_error=True,
    max_retries=2,
    retry_delay=1.0,
    return_on_error=None
)
async def fetch_data_async(url):
    """异步获取数据"""
    # TODO: 实现异步数据获取逻辑
    pass
```

---

### 模板5：配置校验

```python
# ============================================================================
# 配置校验模板
# 适用场景：应用启动、配置加载、配置变更
# 配置要点：使用自动修复功能处理缺失配置
# ============================================================================
from config import validate_config, validate_and_fix_config, Config

def load_and_validate_config(overrides=None):
    """加载并校验配置"""
    # 方式1: 仅校验，不修复
    # errors = validate_config(config_dict)
    # if errors:
    #     logger.warning(f"配置校验失败: {errors}")
    
    # 方式2: 自动修复配置
    config = Config(overrides, validate=True)
    return config
```

---

### 模板6：安全模块导入

```python
# ============================================================================
# 安全模块导入模板
# 适用场景：可选依赖、第三方库、条件导入
# 配置要点：区分核心模块和可选模块
# ============================================================================
import logging
from agent.digital_life import _safe_import, _safe_import_from

logger = logging.getLogger(__name__)

# ------------------------------
# 核心模块 - 必须成功，否则终止程序
# ------------------------------
try:
    from core_module import EssentialClass
except ImportError as e:
    logger.critical(f"核心模块导入失败，程序无法启动: {e}")
    raise

# ------------------------------
# 可选模块 - 安全导入，失败则禁用功能
# ------------------------------
optional_module, module_available = _safe_import(
    'optional_module',
    lambda: __import__('optional_module'),
    fallback_value=None
)

# 批量导入多个名称
modules, all_loaded = _safe_import_from(
    'package_name',
    'ClassA',
    'ClassB',
    'function_c'
)
```

---

### 模板7：日志轮转配置

```python
# ============================================================================
# 日志轮转配置模板
# 适用场景：生产环境日志管理
# 配置要点：根据需求选择大小轮转或时间轮转
# ============================================================================
from agent.logging_utils import LogRotationConfig, setup_agent_logging

# 大小轮转配置（适合限制单个文件大小）
size_rotation = LogRotationConfig(
    max_bytes=50 * 1024 * 1024,  # 50MB
    backup_count=5,
    encoding="utf-8"
)

# 时间轮转配置（适合按时间归档）
time_rotation = LogRotationConfig(
    when="midnight",
    interval=1,
    backup_count=7,
    use_timed_rotation=True,
    encoding="utf-8"
)

# 设置主日志
logger = setup_agent_logging(
    debug_mode=True,
    log_file="./logs/agent.log",
    rotation_config=size_rotation
)
```

---

## 使用指南

### 装饰器选择决策表

| 场景 | 推荐装饰器 | 关键参数 |
|------|------------|----------|
| 外部API调用 | `handle_errors` | `retry_on_error=True`, `max_retries=3` |
| 数据处理 | `catch_and_report` | 指定异常类型 |
| 工具函数 | `safe_call` | `default_return` |
| 异步操作 | `async_handle_errors` | 与同步版本一致 |

### 错误分类选择

```python
# 根据业务场景选择错误分类
from agent.error_handler import ErrorCategory

# 外部服务错误
ErrorCategory.EXTERNAL_SERVICE  # API调用失败、网络问题

# 数据无效
ErrorCategory.DATA_INVALID      # 数据格式错误、校验失败

# 配置错误
ErrorCategory.CONFIG_ERROR      # 配置缺失、配置无效

# 内部错误
ErrorCategory.INTERNAL_ERROR    # 程序逻辑错误

# 认证错误
ErrorCategory.AUTHENTICATION    # 登录失败、权限不足

# 限流错误
ErrorCategory.RATE_LIMIT        # 请求被限流
```

### 错误严重级别选择

```python
from agent.error_handler import ErrorSeverity

# 选择合适的严重级别
ErrorSeverity.DEBUG     # 调试信息，仅记录
ErrorSeverity.INFO      # 一般信息，仅记录
ErrorSeverity.WARNING   # 警告，记录并关注
ErrorSeverity.ERROR     # 错误，记录、上报、通知
ErrorSeverity.CRITICAL  # 严重错误，记录、上报、通知、可能需要重启
```

---

**文档版本**: v1.0  
**创建日期**: 2026-06-03  
**适用范围**: 灵犀数字生命体系统
