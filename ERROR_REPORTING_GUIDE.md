# 🎯 错误自动上报系统 - 功能说明

## ✅ 检查结果

### 当前状态：已实现！

监控模块现在支持**完整的错误自动上报功能**。

---

## 📊 支持的上报方式

### 1. 控制台输出 (ConsoleReporter) ✅

**用途**：调试和开发环境

**配置**：
```python
config = {
    'console': {'enabled': True}
}
```

**输出示例**：
```
🚨 Error Report [CRITICAL]
   Time: 2026-05-30T19:44:52
   Type: ValueError
   Message: Something went wrong
   Trace ID: abc123def456
   Context: {"user_id": "123"}
```

---

### 2. 日志文件 (FileReporter) ✅

**用途**：持久化存储和日志分析

**配置**：
```python
config = {
    'file': {
        'enabled': True,
        'file_path': './logs/errors.log',
        'max_size_mb': 10,
        'backup_count': 5
    }
}
```

**特点**：
- 自动日志轮转
- 支持备份
- 大小限制保护

---

### 3. Webhook (WebhookReporter) ✅

**用途**：集成到外部系统

**配置**：
```python
config = {
    'webhook': {
        'enabled': True,
        'url': 'https://your-webhook-endpoint.com/errors',
        'headers': {
            'Authorization': 'Bearer your-token',
            'X-Custom-Header': 'value'
        },
        'timeout': 5,
        'retry_times': 3
    }
}
```

**特点**：
- HTTP POST 请求
- 自动重试机制
- 自定义请求头
- 超时控制

---

### 4. Slack (SlackReporter) ✅

**用途**：团队即时通知

**配置**：
```python
config = {
    'slack': {
        'enabled': True,
        'webhook_url': 'https://hooks.slack.com/services/xxx/yyy/zzz',
        'channel': '#errors',
        'username': 'Yunshu Error Bot',
        'icon_emoji': ':robot_face:'
    }
}
```

**特点**：
- 富文本消息格式
- 不同级别不同颜色
- 支持上下文信息
- 自动截断过长堆栈

---

### 5. Email (EmailReporter) ✅

**用途**：重要错误邮件通知

**配置**：
```python
config = {
    'email': {
        'enabled': True,
        'smtp_host': 'smtp.gmail.com',
        'smtp_port': 587,
        'smtp_user': 'your-email@gmail.com',
        'smtp_password': 'your-app-password',
        'from_addr': 'error-reporter@yourcompany.com',
        'to_addrs': ['admin@yourcompany.com', 'dev@yourcompany.com'],
        'use_tls': True
    }
}
```

**特点**：
- HTML 和纯文本双格式
- 支持多个收件人
- TLS 安全连接

---

## 🚀 快速开始

### 1. 基本使用

```python
from agent.monitoring import get_error_reporter, AlertLevel

# 获取全局错误上报器
reporter = get_error_reporter()

# 上报错误
try:
    # 业务代码
    result = risky_operation()
except Exception as e:
    reporter.report_error(
        error=e,
        level=AlertLevel.ERROR,
        context={'user_id': '123', 'action': 'test'}
    )
```

### 2. 快捷函数

```python
from agent.monitoring import report_error

# 直接上报错误
try:
    do_something()
except Exception as e:
    report_error(e, context={'operation': 'do_something'})
```

### 3. 自定义配置

```python
from agent.monitoring import ErrorReporter

config = {
    'console': {'enabled': True},
    'file': {
        'enabled': True,
        'file_path': './logs/app_errors.log'
    },
    'webhook': {
        'enabled': False,  # 禁用 webhook
        'url': 'https://...'
    }
}

reporter = ErrorReporter(config)
```

### 4. 与 TraceContext 集成

```python
from agent.monitoring import TraceContext, get_error_reporter, get_trace_id

def process_request(user_id):
    try:
        with TraceContext("Service", "process"):
            # 业务逻辑
            result = do_work()
            
    except Exception as e:
        # 自动包含 Trace ID
        reporter = get_error_reporter()
        reporter.report_error(
            error=e,
            context={
                'user_id': user_id,
                'trace_id': get_trace_id()
            }
        )
```

---

## 📋 告警级别

| 级别 | 值 | 说明 | 默认上报 |
|------|-----|------|---------|
| DEBUG | debug | 调试信息 | ❌ |
| INFO | info | 一般信息 | ❌ |
| WARNING | warning | 警告信息 | ❌ |
| ERROR | error | 错误信息 | ✅ |
| CRITICAL | critical | 严重错误 | ✅ |

**配置最小上报级别**：
```python
config = {
    'console': {
        'enabled': True,
        'min_level': 'warning'  # 只上报 warning 及以上
    }
}
```

---

## 🎯 最佳实践

### 1. 生产环境配置

```python
prod_config = {
    'console': {'enabled': False},  # 生产环境关闭控制台
    'file': {
        'enabled': True,
        'file_path': '/var/log/Yunshu/errors.log',
        'max_size_mb': 100,
        'backup_count': 10
    },
    'webhook': {
        'enabled': True,
        'url': 'https://your-monitoring-system.com/webhook'
    },
    'slack': {
        'enabled': True,
        'webhook_url': 'https://hooks.slack.com/...',
        'channel': '#prod-errors'
    }
}
```

### 2. 开发环境配置

```python
dev_config = {
    'console': {'enabled': True, 'min_level': 'debug'},
    'file': {
        'enabled': True,
        'file_path': './logs/dev_errors.log'
    },
    'slack': {'enabled': False}  # 开发环境不打扰
}
```

### 3. 错误上下文

```python
reporter.report_error(
    error=e,
    level=AlertLevel.ERROR,
    context={
        'user_id': user_id,
        'session_id': session_id,
        'request_path': '/api/users',
        'request_method': 'POST',
        'database': 'users_table',
        'query': 'SELECT * FROM users WHERE id = ?'
    }
)
```

---

## 🔧 高级功能

### 1. 异步上报

```python
# 异步上报，不阻塞主线程
reporter.report_error(
    error=e,
    async_report=True  # 放入队列异步处理
)
```

### 2. 全局错误处理器

```python
import sys

def global_exception_handler(exc_type, exc_value, exc_traceback):
    """设置为全局错误处理器"""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    
    reporter = get_error_reporter()
    reporter.report_error(
        error=exc_value,
        level=AlertLevel.CRITICAL,
        context={'type': exc_type.__name__}
    )

sys.excepthook = global_exception_handler
```

### 3. 自动集成到 DigitalLife

```python
# agent/digital_life.py
from agent.monitoring import get_error_reporter

class DigitalLife:
    def chat(self, user_input: str) -> str:
        try:
            # 原有逻辑
            result = self._process(user_input)
            return result
        except Exception as e:
            # 自动上报错误
            reporter = get_error_reporter()
            reporter.report_error(
                error=e,
                context={'user_input': user_input}
            )
            return f"处理出错: {e}"
```

---

## 📊 测试结果

### ✅ 已验证功能

```
Features tested:
  - Console reporting: OK
  - File reporting: OK
  - Multiple alert levels: OK
  - Shortcut function: OK
  - Global singleton: OK
  - Webhook support: OK (configured)
```

---

## 📚 相关文件

| 文件 | 说明 |
|------|------|
| `agent/monitoring/error_reporter.py` | 错误上报核心实现 |
| `test_error_reporter.py` | 功能测试脚本 |
| `P1_MONITORING_INTEGRATION.md` | 监控模块集成说明 |
| `P1_MONITORING_PLAN.md` | 监控模块规划文档 |

---

## 🎯 下一步

### 1. 配置 Webhook

```python
# 获取 Webhook URL
# 1. 访问 https://webhook.site 创建测试 webhook
# 2. 或配置 Slack Incoming Webhook
# 3. 或配置企业微信/钉钉群机器人

config = {
    'webhook': {
        'enabled': True,
        'url': '你的-webhook-url'
    }
}
```

### 2. 配置 Email 通知

```python
config = {
    'email': {
        'enabled': True,
        'smtp_host': 'smtp.gmail.com',
        'to_addrs': ['your-email@example.com']
    }
}
```

### 3. 集成到监控系统

```python
# 可以对接：
# - Grafana Alerting
# - Prometheus AlertManager
# - PagerDuty
# - OpsGenie
# - 自定义监控系统
```

---

**错误上报功能已完整实现！** ✅

支持 Webhook、Slack、Email、文件等多种方式，可以轻松集成到任何外部系统。🚀
