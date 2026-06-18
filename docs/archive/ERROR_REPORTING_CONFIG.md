# 🎯 错误上报配置指南

## 快速配置

### 1. Webhook.site（测试用）

1. 访问 https://webhook.site
2. 点击 "Create" 获取临时 URL
3. 复制 URL 并配置

```python
config = {
    'webhook': {
        'enabled': True,
        'url': 'https://webhook.site/your-unique-id'
    }
}
```

### 2. Slack（生产推荐）

1. 创建 Slack App：https://api.slack.com/apps
2. 启用 Incoming Webhooks
3. 创建 Webhook URL

```python
config = {
    'slack': {
        'enabled': True,
        'webhook_url': 'https://hooks.slack.com/services/xxx/yyy/zzz',
        'channel': '#errors'
    }
}
```

### 3. 企业微信群机器人

1. 群设置 → 添加群机器人
2. 复制 Webhook URL

```python
config = {
    'webhook': {
        'enabled': True,
        'url': 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx'
    }
}
```

### 4. 钉钉群机器人

1. 群设置 → 智能群助手
2. 添加机器人 → 自定义
3. 复制 Webhook URL + Secret

```python
import hashlib
import time
import base64
import hmac

# 生成签名（钉钉需要）
timestamp = str(round(time.time() * 1000))
secret = 'your-secret'
sign = base64.b64encode(hmac.new(secret.encode(), timestamp.encode(), digestmod=hashlib.sha256).digest()).decode()

url = f'https://oapi.dingtalk.com/robot/send?access_token=xxx&timestamp={timestamp}&sign={sign}'

config = {
    'webhook': {
        'enabled': True,
        'url': url
    }
}
```

---

## 完整配置示例

### 开发环境

```python
dev_config = {
    'console': {
        'enabled': True,
        'min_level': 'debug'
    },
    'file': {
        'enabled': True,
        'file_path': './logs/dev_errors.log'
    },
    'webhook': {'enabled': False},
    'slack': {'enabled': False},
    'email': {'enabled': False}
}
```

### 生产环境

```python
prod_config = {
    'console': {'enabled': False},
    'file': {
        'enabled': True,
        'file_path': '/var/log/Yunshu/errors.log',
        'max_size_mb': 100,
        'backup_count': 10
    },
    'webhook': {
        'enabled': True,
        'url': 'https://your-monitoring.com/webhook',
        'retry_times': 3
    },
    'slack': {
        'enabled': True,
        'webhook_url': 'https://hooks.slack.com/services/xxx',
        'channel': '#prod-alerts'
    },
    'email': {
        'enabled': True,
        'smtp_host': 'smtp.gmail.com',
        'to_addrs': ['admin@company.com'],
        'min_level': 'critical'  # 只有严重错误才发邮件
    }
}
```

---

## 集成示例

### 方式 1：全局错误处理器

```python
import sys
from agent.monitoring import get_error_reporter, AlertLevel

# 设置全局错误处理器
original_excepthook = sys.excepthook

def custom_excepthook(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        original_excepthook(exc_type, exc_value, exc_traceback)
        return
    
    reporter = get_error_reporter()
    reporter.report_error(
        error=exc_value,
        level=AlertLevel.CRITICAL if 'critical' in str(exc_value).lower() else AlertLevel.ERROR
    )

sys.excepthook = custom_excepthook
```

### 方式 2：装饰器

```python
from functools import wraps
from agent.monitoring import get_error_reporter, AlertLevel, get_trace_id

def monitored(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            reporter = get_error_reporter()
            reporter.report_error(
                error=e,
                context={
                    'function': func.__name__,
                    'trace_id': get_trace_id(),
                    'args': str(args)[:200]
                }
            )
            raise
    return wrapper

# 使用
@monitored
def risky_operation():
    # 业务逻辑
    pass
```

### 方式 3：上下文管理器

```python
from agent.monitoring import TraceContext, get_error_reporter

class ErrorContext:
    def __init__(self, context: dict):
        self.context = context
        self.reporter = get_error_reporter()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.reporter.report_error(
                error=exc_val,
                context=self.context
            )
        return False

# 使用
with ErrorContext({'user_id': '123', 'operation': 'test'}):
    do_something()
```

---

## 测试

### 1. 测试 Webhook

```bash
python test_error_reporter.py
```

### 2. 手动测试

```python
from agent.monitoring import get_error_reporter

reporter = get_error_reporter({
    'webhook': {
        'enabled': True,
        'url': '你的-webhook-url'
    }
})

reporter.report_error(
    error=Exception("Test error"),
    context={'test': True}
)
```

---

## 故障排除

### Webhook 不工作

1. 检查 URL 是否正确
2. 检查网络连接
3. 查看日志输出

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Slack 不发送

1. 验证 Webhook URL
2. 检查 App 权限
3. 确认 Channel 存在

### Email 不发送

1. 验证 SMTP 配置
2. 允许低安全性应用访问（ Gmail）
3. 使用 App Password

---

## 下一步

1. 选择合适的告警渠道
2. 配置 Webhook URL
3. 测试告警发送
4. 设置告警规则（频率、级别）
5. 集成到现有监控系统
