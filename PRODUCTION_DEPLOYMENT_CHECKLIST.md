# Digital Life 生产环境部署检查清单

## 概述

本文档提供生产环境部署前的完整检查清单，确保监控和错误上报功能正确配置。

---

## 1. 监控模块检查

### 1.1 错误上报配置

- [ ] 错误上报模块已正确导入
- [ ] ConsoleReporter 已启用（DEBUG 或 INFO 级别）
- [ ] FileReporter 已启用，日志路径配置正确
- [ ] 至少一种外部上报渠道已配置（Webhook/Slack/Email）
- [ ] 上报级别设置合理（WARNING 或 ERROR）

### 1.2 性能监控配置

- [ ] TraceContext 已正确初始化
- [ ] 指标收集器已启动
- [ ] 关键指标已配置（对话次数、错误次数等）

---

## 2. 错误上报渠道配置

### 2.1 基础配置（必需）

```python
# 检查配置是否正确
from agent.monitoring import get_error_reporter

config = {
    'console': {'enabled': True, 'min_level': 'warning'},
    'file': {
        'enabled': True,
        'file_path': './logs/digital_life_errors.log',
        'min_level': 'error'
    }
}

reporter = get_error_reporter(config)
assert len(reporter.reporters) >= 2, "至少需要 2 个上报渠道"
```

### 2.2 Webhook 配置（推荐）

- [ ] Webhook URL 可访问
- [ ] 响应超时设置合理（3-10 秒）
- [ ] 认证 Header 已配置（如需要）
- [ ] 已测试 Webhook 连通性

```python
# 测试 Webhook
import requests
response = requests.post(
    YOUR_WEBHOOK_URL,
    json={"test": "true"},
    timeout=5
)
assert response.status_code == 200, "Webhook 连通性测试失败"
```

### 2.3 Slack 配置（可选但推荐）

- [ ] Slack Webhook URL 已获取（见 slack_config.py）
- [ ] 目标频道已存在
- [ ] Bot 用户有发送消息权限
- [ ] 已测试 Slack 消息发送

```python
# 测试 Slack
from slack_config import test_slack_config
test_slack_config()
```

### 2.4 Email 配置（可选）

- [ ] SMTP 服务器地址正确
- [ ] 端口配置正确（587 或 465）
- [ ] 发件人和收件人邮箱已配置
- [ ] 认证凭据安全存储（不要硬编码）

---

## 3. DigitalLife 集成检查

### 3.1 chat() 方法

- [ ] 异常捕获块存在
- [ ] report_error() 被调用
- [ ] 上下文信息完整（user_input, trace_id, session_id 等）
- [ ] 上报级别正确（AlertLevel.ERROR）

### 3.2 _chat_with_planning() 方法

- [ ] 异常捕获块存在
- [ ] report_error() 被调用
- [ ] 上下文信息完整
- [ ] 上报级别正确（AlertLevel.WARNING）

### 3.3 全局异常处理

- [ ] 未处理异常能被捕获
- [ ] 程序崩溃前有日志记录
- [ ] 崩溃信息会上报

---

## 4. 日志系统检查

### 4.1 日志目录

- [ ] `./logs/` 目录存在
- [ ] 目录可写权限
- [ ] 日志文件轮转策略已配置（大小/日期）

### 4.2 日志内容

- [ ] 所有 ERROR 级别日志都有堆栈
- [ ] 日志包含 Trace ID
- [ ] 日志包含时间戳
- [ ] 敏感信息已脱敏（API Key、密码等）

---

## 5. 性能检查

### 5.1 异步上报

- [ ] 错误上报是异步的（不阻塞主线程）
- [ ] 队列大小合理（不会 OOM）
- [ ] 队列溢出有处理策略（丢弃/记录）

### 5.2 资源使用

- [ ] 监控模块内存占用合理
- [ ] 日志写入速度不会成为瓶颈
- [ ] 网络请求有超时设置

---

## 6. 安全检查

### 6.1 敏感信息

- [ ] 错误信息不包含 API Key
- [ ] 错误信息不包含密码
- [ ] 错误信息不包含用户隐私数据
- [ ] Webhook URL 安全存储（环境变量/配置文件）

### 6.2 访问控制

- [ ] 日志文件权限正确（仅服务账号可写）
- [ ] 配置文件权限正确（仅管理员可读）
- [ ] 没有敏感信息提交到版本控制

---

## 7. 测试验证

### 7.1 功能测试

```bash
# 运行完整测试
python test_error_reporter.py
python test_digital_life_error_reporting.py
```

- [ ] 所有测试通过
- [ ] 测试错误能正确上报到所有配置的渠道
- [ ] 日志文件有记录

### 7.2 端到端测试

1. [ ] 启动 DigitalLife
2. [ ] 发送正常对话 → 验证成功
3. [ ] 故意制造错误 → 验证错误上报
4. [ ] 检查所有渠道都收到通知

---

## 8. 监控和告警

### 8.1 监控指标

- [ ] 对话总数监控
- [ ] 错误率监控（< 1% 为正常）
- [ ] 响应时间监控
- [ ] 内存和 CPU 使用监控

### 8.2 告警规则

- [ ] 错误率 > 5% 触发告警
- [ ] 连续 10 次错误触发告警
- [ ] 内存 > 80% 触发告警
- [ ] 响应时间 > 5s 触发告警

---

## 9. 灾备和恢复

### 9.1 日志备份

- [ ] 日志定期备份策略
- [ ] 备份存储在安全位置
- [ ] 日志保留策略明确

### 9.2 故障恢复

- [ ] 服务崩溃自动重启机制
- [ ] 监控模块失败不影响主功能
- [ ] 降级策略明确（如：禁用 Slack 时仅用文件）

---

## 10. 文档和知识

- [ ] 配置文档已更新
- [ ] 运维手册已编写
- [ ] 团队成员已培训
- [ ] 故障处理流程已明确

---

## 部署前最终检查

在部署到生产环境前，请确保：

- [ ] 所有上面的检查项已完成
- [ ] 测试环境已充分验证
- [ ] 有回滚方案
- [ ] 有监控和告警
- [ ] 团队已准备好响应

---

## 配置示例

### 最小可用配置

```python
config = {
    'console': {'enabled': True, 'min_level': 'error'},
    'file': {
        'enabled': True,
        'file_path': './logs/digital_life_errors.log'
    }
}
```

### 推荐生产配置

```python
config = {
    'console': {'enabled': True, 'min_level': 'warning'},
    'file': {
        'enabled': True,
        'file_path': './logs/digital_life_errors.log',
        'min_level': 'error'
    },
    'slack': {
        'enabled': True,
        'webhook_url': os.environ['SLACK_WEBHOOK_URL'],
        'channel': '#digital-life-alerts',
        'min_level': 'warning'
    },
    'webhook': {
        'enabled': True,
        'url': 'https://your-monitoring-service.com/webhook',
        'min_level': 'error'
    }
}
```

---

## 联系与支持

如有问题，请查看：
- [agent/monitoring/error_reporter.py](file:///c:/Users/Administrator/agent/agent/monitoring/error_reporter.py) - 错误上报核心模块
- [test_error_reporter.py](file:///c:/Users/Administrator/agent/test_error_reporter.py) - 测试脚本
- [slack_config.py](file:///c:/Users/Administrator/agent/slack_config.py) - Slack 配置工具
