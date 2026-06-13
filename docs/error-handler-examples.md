# 统一错误处理模块使用指南

## 概述
本文档展示如何使用 `agent.error_handler` 模块提供的统一错误处理和自动重试功能。

## 快速开始

### 基本导入
```python
from agent.error_handler import (
    # 核心类
    YunshuError,
    RecoverableError,
    CriticalError,
    # 错误分类
    ErrorSeverity,
    ErrorCategory,
    # 错误处理
    ErrorHandler,
    get_error_handler,
    # 重试与熔断器
    RetryPolicy,
    CircuitBreaker,
    CircuitState,
    # 装饰器
    with_retry,
    with_circuit_breaker,
)
```

---

## 示例1: 基础错误使用

```python
from agent.error_handler import YunshuError, RecoverableError, CriticalError
from agent.error_handler import ErrorSeverity, ErrorCategory

# 1. 创建简单的错误
try:
    raise YunshuError(
        message="发生了一个错误",
        severity=ErrorSeverity.ERROR,
        category=ErrorCategory.UNKNOWN,
        context={"user_id": 123},
    )
except YunshuError as e:
    print(f"错误类型: {e.category}")
    print(f"错误级别: {e.severity}")
    print(f"上下文: {e.context}")

# 2. 使用预定义错误
try:
    # 网络超时错误（可重试）
    raise RecoverableError("请求超时", category=ErrorCategory.NETWORK_TIMEOUT)
except YunshuError as e:
    print(f"是否可恢复: {e.recoverable}")
    print(f"是否可重试: {e.retryable}")

# 3. 严重错误
try:
    raise CriticalError("关键系统故障，需要重启")
except CriticalError as e:
    print(f"是否需要重启: {e.requires_restart}")
```

---

## 示例2: 使用自动重试装饰器

```python
import time
import random
from agent.error_handler import (
    with_retry,
    RetryPolicy,
    RecoverableError,
    TemporaryNetworkError,
)

# 示例1: 基础重试装饰器
@with_retry(max_retries=3, initial_delay=1.0, backoff_factor=2.0)
def unstable_operation():
    """一个可能失败的操作"""
    if random.random() < 0.6:
        print("失败了...")
        raise RecoverableError("临时错误")
    print("成功！")
    return "结果"

# 使用
result = unstable_operation()
print(f"最终结果: {result}")


# 示例2: 自定义重试策略
custom_policy = RetryPolicy(
    max_retries=5,
    initial_delay=0.5,
    max_delay=10.0,
    backoff_factor=1.5,
)

@with_retry(
    max_retries=custom_policy.max_retries,
    initial_delay=custom_policy.initial_delay,
    max_delay=custom_policy.max_delay,
    backoff_factor=custom_policy.backoff_factor,
)
def custom_policy_example():
    if random.random() < 0.7:
        print("操作失败")
        raise TemporaryNetworkError("连接超时")
    print("操作成功")
    return "完成"

# 使用
custom_policy_example()


# 示例3: 仅重试特定异常
@with_retry(
    max_retries=3,
    retryable_exceptions=(TemporaryNetworkError,),
)
def selective_retry_example():
    # 只有TemporaryNetworkError会被重试
    if random.random() < 0.5:
        raise TemporaryNetworkError("网络问题")
    else:
        # 其他错误不会重试
        raise ValueError("不重试的错误")
```

---

## 示例3: 使用熔断器模式

```python
import time
import random
from agent.error_handler import CircuitBreaker, CircuitState, with_circuit_breaker

# 创建熔断器
cb = CircuitBreaker(
    max_failures=5,          # 最多连续5次失败后断开
    reset_timeout=30,         # 30秒后尝试恢复
    half_open_timeout=10,     # 半开状态10秒
    name="my-service",
)

# 方式1: 直接使用
def critical_operation():
    """可能失败的关键操作"""
    if random.random() < 0.7:
        print("服务失败")
        raise Exception("服务不可用")
    print("服务正常")
    return "OK"

# 使用熔断器执行
try:
    result = cb.execute(critical_operation)
    print(f"结果: {result}")
except Exception as e:
    print(f"错误: {e}")

# 查看熔断器状态
status = cb.get_status()
print(f"状态: {status['state']}")
print(f"失败次数: {status['failure_count']}")


# 方式2: 使用装饰器
@with_circuit_breaker(cb)
def decorated_operation():
    if random.random() < 0.7:
        raise Exception("服务不可用")
    return "OK"

# 使用
for i in range(10):
    try:
        print(f"尝试 {i+1}:", end=" ")
        decorated_operation()
    except Exception as e:
        print(f"失败: {e}")
```

---

## 示例4: 使用全局错误处理器

```python
from agent.error_handler import (
    get_error_handler,
    YunshuError,
    RecoverableError,
    TemporaryNetworkError,
    ErrorHandler,
)

# 获取全局错误处理器
handler = get_error_handler()

# 1. 注册熔断器
api_circuit = CircuitBreaker(max_failures=3, reset_timeout=60, name="api-service")
db_circuit = CircuitBreaker(max_failures=5, reset_timeout=120, name="database")
handler.register_circuit_breaker("api-service", api_circuit)
handler.register_circuit_breaker("database", db_circuit)


# 2. 定义一个函数并执行
def fetch_external_data():
    """调用外部API"""
    if random.random() < 0.5:
        raise TemporaryNetworkError("API超时")
    return {"data": "external"}

# 用处理器执行（带重试和熔断）
result = handler.execute_with_retry(
    fetch_external_data,
    circuit_breaker=api_circuit,
    retry_policy=RetryPolicy(max_retries=3),
)


# 3. 记录错误
try:
    1 / 0
except Exception as e:
    error = handler.record_error(e, key="division-by-zero")
    print(f"记录的错误: {error}")


# 4. 获取指标
metrics = handler.get_metrics()
print("所有错误指标:", metrics)

# 特定错误的指标
specific_metrics = handler.get_metrics("division-by-zero")
print("特定错误指标:", specific_metrics)


# 5. 获取熔断器状态
cb_status = handler.get_circuit_breaker_status()
print("熔断器状态:", cb_status)
```

---

## 示例5: 自定义错误类型

```python
from agent.error_handler import (
    YunshuError,
    RecoverableError,
    ErrorCategory,
    ErrorSeverity,
)

# 创建自定义错误类
class PaymentFailureError(RecoverableError):
    """支付失败错误"""
    category: ErrorCategory = ErrorCategory.EXTERNAL_SERVICE
    severity: ErrorSeverity = ErrorSeverity.WARNING
    default_retry_count = 3
    default_retry_delay = 5.0


class DatabaseCorruptionError(YunshuError):
    """数据库损坏错误（严重）"""
    category: ErrorCategory = ErrorCategory.DATA_CORRUPT
    severity: ErrorSeverity = ErrorSeverity.CRITICAL
    requires_restart: bool = True


# 使用自定义错误
try:
    raise PaymentFailureError(
        message="支付网关暂时不可用",
        context={"order_id": "12345", "amount": 99.99},
    )
except PaymentFailureError as e:
    print(f"错误类别: {e.category}")
    print(f"建议重试: {e.retryable}")
    print(f"默认重试次数: {e.default_retry_count}")
```

---

## 示例6: 集成到实际应用

```python
from agent.error_handler import (
    with_retry,
    CircuitBreaker,
    get_error_handler,
    TemporaryNetworkError,
    ExternalServiceError,
)
import requests

# 初始化
handler = get_error_handler()
api_cb = CircuitBreaker(max_failures=5, reset_timeout=60, name="third-party-api")
handler.register_circuit_breaker("third-party-api", api_cb)


class APIClient:
    """API客户端示例"""
    
    def __init__(self, base_url: str):
        self.base_url = base_url
    
    @with_retry(max_retries=3, initial_delay=1.0, circuit_breaker=api_cb)
    def get_user_data(self, user_id: int):
        """获取用户数据，带重试和熔断"""
        response = requests.get(f"{self.base_url}/users/{user_id}", timeout=5)
        if response.status_code == 503:
            raise TemporaryNetworkError("服务不可用")
        elif response.status_code >= 400:
            raise ExternalServiceError(f"请求失败: {response.status_code}")
        return response.json()
    
    def get_with_explicit_handler(self, user_id: int):
        """显式使用ErrorHandler的版本"""
        def fetch():
            response = requests.get(f"{self.base_url}/users/{user_id}", timeout=5)
            if response.status_code == 503:
                raise TemporaryNetworkError("服务不可用")
            return response.json()
        
        return handler.execute_with_retry(
            fetch,
            circuit_breaker=api_cb,
            retry_policy=RetryPolicy(max_retries=3),
        )


# 使用示例
if __name__ == "__main__":
    client = APIClient("https://api.example.com")
    
    try:
        user_data = client.get_user_data(123)
        print("获取成功:", user_data)
    except Exception as e:
        print("最终失败:", e)
    
    # 查看监控状态
    print("\n错误指标:", handler.get_metrics())
    print("\n熔断器状态:", handler.get_circuit_breaker_status())
```

---

## 最佳实践

### 1. 选择合适的错误类别
| 场景 | 推荐使用 |
|------|---------|
| 临时网络问题 | TemporaryNetworkError |
| API调用超时 | NetworkTimeoutError |
| 外部服务异常 | ExternalServiceError |
| 数据验证失败 | DataInvalidError |
| 安全相关错误 | SecurityError |
| 严重系统问题 | CriticalError |

### 2. 合理配置重试参数
```python
# 网络操作 - 多次重试
network_policy = RetryPolicy(
    max_retries=5,
    initial_delay=0.5,
    max_delay=10.0,
)

# 关键数据库操作 - 较少重试
db_policy = RetryPolicy(
    max_retries=2,
    initial_delay=2.0,
    max_delay=5.0,
)
```

### 3. 合理的熔断器阈值
```python
# 高可靠性服务 - 更保守
critical_cb = CircuitBreaker(max_failures=3, reset_timeout=120)

# 非关键服务 - 更宽松
non_critical_cb = CircuitBreaker(max_failures=10, reset_timeout=30)
```

### 4. 监控和日志
- 定期检查错误指标
- 设置告警阈值
- 分析错误模式，优化系统

---

## 附录: 错误类型速查表

| 错误类型 | 类别 | 可恢复 | 可重试 | 默认重试次数 |
|---------|------|-------|--------|------------|
| YunshuError | UNKNOWN | False | False | 3 |
| RecoverableError | UNKNOWN | True | True | 3 |
| CriticalError | UNKNOWN | False | False | 3 |
| TemporaryNetworkError | NETWORK_TEMPORARY | True | True | 5 |
| NetworkTimeoutError | NETWORK_TIMEOUT | True | True | 3 |
| ExternalServiceError | EXTERNAL_SERVICE | True | True | 3 |
| DataInvalidError | DATA_INVALID | True | False | 3 |
| SecurityError | SECURITY_ALERT | False | False | 3 |

---

## 更多信息

- [完整API文档](file:///c:/Users/Administrator/agent/agent/error_handler.py)
- [ADR决策记录](file:///c:/Users/Administrator/agent/docs/adr/003-error-handling-retry.md)
- [系统评价报告](file:///c:/Users/Administrator/agent/SYSTEM_EVALUATION_REPORT.md)
