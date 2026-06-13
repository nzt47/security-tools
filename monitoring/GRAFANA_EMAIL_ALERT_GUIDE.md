# Grafana Email 告警配置指南

> 本文档详细说明如何在 Grafana 中配置 Email 告警通知，以便在 Critical 告警触发时自动发送邮件。

## 目录

1. [前提条件](#前提条件)
2. [配置 SMTP 服务器](#配置-smtp-服务器)
3. [创建 Contact Point](#创建-contact-point)
4. [配置告警规则](#配置告警规则)
5. [测试告警](#测试告警)
6. [常见问题](#常见问题)

---

## 前提条件

### 必需信息

1. **SMTP 服务器地址**（如：smtp.gmail.com）
2. **SMTP 端口**（如：465 或 587）
3. **发件人邮箱地址**
4. **SMTP 用户名和密码**
5. **收件人邮箱地址**

### 常用 SMTP 配置

| 邮箱服务 | SMTP 地址 | 端口 | 加密方式 |
|---------|----------|------|---------|
| Gmail | smtp.gmail.com | 465 | SSL |
| Gmail | smtp.gmail.com | 587 | TLS |
| Outlook | smtp.office365.com | 587 | TLS |
| QQ邮箱 | smtp.qq.com | 465 | SSL |
| 163邮箱 | smtp.163.com | 465 | SSL |

---

## 配置 SMTP 服务器

### 方式 1：通过 Grafana 配置文件

编辑 Grafana 配置文件（通常在 `/etc/grafana/grafana.ini` 或 Docker 容器内的 `/usr/share/grafana/conf/grafana.ini`）：

```ini
[smtp]
enabled = true
host = smtp.gmail.com:465
user = your-email@gmail.com
password = your-app-password
from_address = your-email@gmail.com
from_name = Grafana Alerts
skip_verify = false
```

### 方式 2：通过 Docker Compose 环境变量

编辑 `monitoring/docker-compose.yml`：

```yaml
services:
  grafana:
    environment:
      - GF_SMTP_ENABLED=true
      - GF_SMTP_HOST=smtp.gmail.com:465
      - GF_SMTP_USER=your-email@gmail.com
      - GF_SMTP_PASSWORD=your-app-password
      - GF_SMTP_FROM_ADDRESS=your-email@gmail.com
      - GF_SMTP_FROM_NAME=Grafana Alerts
```

### Gmail 特殊配置

如果使用 Gmail，需要创建 **应用专用密码**：

1. 登录 Google 账户
2. 访问 https://myaccount.google.com/security
3. 启用 "两步验证"
4. 搜索 "应用专用密码"
5. 创建新密码，选择 "邮件" 和 "其他设备"
6. 复制生成的 16 位密码

---

## 创建 Contact Point

### 步骤 1：登录 Grafana

访问 http://localhost:3000，使用 admin/admin 登录。

### 步骤 2：导航到 Contact Points

1. 左侧菜单 -> **Alerting** -> **Contact points**
2. 点击 **Add contact point**

### 步骤 3：配置 Email Contact Point

填写以下信息：

| 字段 | 值 |
|------|---|
| Name | `Critical Alert Email` |
| Integration | Email |
| Addresses | `recipient@example.com`（收件人邮箱） |
| Subject | `[CRITICAL] Yunshu V2 Alert - {{ .GroupLabels.alertname }}` |
| Message | 自定义邮件内容模板 |

### 邮件内容模板示例

```html
<h2>云枢 V2 告警通知</h2>

<p><strong>告警级别:</strong> {{ .GroupLabels.level }}</p>
<p><strong>告警名称:</strong> {{ .GroupLabels.alertname }}</p>
<p><strong>触发时间:</strong> {{ .StartsAt }}</p>

<h3>告警详情</h3>
{{ range .Alerts }}
<p>
  <strong>描述:</strong> {{ .Annotations.description }}<br>
  <strong>值:</strong> {{ .Annotations.value }}<br>
</p>
{{ end }}

<h3>建议操作</h3>
<p>请立即检查云枢 V2 系统状态，确认是否有危险操作被拦截。</p>

<p>查看详情: <a href="http://localhost:3000/d/Yunshu-v2-dashboard">仪表盘链接</a></p>
```

### 步骤 4：保存 Contact Point

点击 **Save contact point** 保存配置。

---

## 配置告警规则

### 步骤 1：创建 Alert Rule

1. 左侧菜单 -> **Alerting** -> **Alert rules**
2. 点击 **New alert rule**

### 步骤 2：配置 Critical 告警规则

#### 规则 1：危险操作拦截告警

| 字段 | 值 |
|------|---|
| Rule name | `Critical Alert Detected` |
| Group | `Security Alerts` |
| Namespace | `Yunshu V2` |
| Query | `sum(Yunshu_alert_total{level="critical"}) > 0` |
| Evaluation interval | `1m` |
| For duration | `0s`（立即触发） |

#### Annotations 配置

| 字段 | 值 |
|------|---|
| description | `检测到危险操作被拦截` |
| severity | `critical` |
| runbook_url | `http://localhost:3000/d/Yunshu-v2-dashboard` |

#### 规则 2：模块加载失败告警

| 字段 | 值 |
|------|---|
| Rule name | `Module Load Failure` |
| Query | `sum(rate(Yunshu_v2_module_load_total{status="failure"}[5m])) > 0` |
| Evaluation interval | `5m` |
| For duration | `1m` |

#### 规则 3：交互超时告警

| 字段 | 值 |
|------|---|
| Rule name | `Interaction Timeout` |
| Query | `histogram_quantile(0.95, sum(rate(Yunshu_interaction_duration_seconds_bucket[5m])) by (le)) > 1000` |
| Evaluation interval | `5m` |
| For duration | `2m` |

### 步骤 3：关联 Contact Point

在告警规则配置页面：

1. 找到 **Contact point** 部分
2. 选择之前创建的 `Critical Alert Email`
3. 点击 **Save rule**

---

## 测试告警

### 方式 1：手动触发测试

1. 左侧菜单 -> **Alerting** -> **Contact points**
2. 找到 `Critical Alert Email`
3. 点击 **Test** 按钮
4. 选择 **Send test notification**
5. 查看邮箱是否收到测试邮件

### 方式 2：通过 API 触发

```bash
# 模拟 Critical 告警
curl -X POST http://localhost:8000/api/alert \
  -H "Content-Type: application/json" \
  -d '{"level": "critical", "message": "Test alert"}'
```

### 方式 3：通过云枢 V2 触发

运行以下 Python 代码：

```python
from agent.digital_life import DigitalLife
from agent.prometheus_exporter import PrometheusMetricsExporter

dl = DigitalLife()
exporter = PrometheusMetricsExporter(port=8000)
exporter.start()

# 触发 Critical 告警
exporter.record_alert("critical")

# 保持运行以便 Prometheus 抓取
import time
time.sleep(60)
```

---

## 常见问题

### 问题 1：邮件发送失败

**症状**: "Failed to send email"

**解决方案**:
1. 检查 SMTP 配置是否正确
2. 确认 SMTP 用户名和密码
3. 检查 SMTP 端口和加密方式
4. 确认发件人邮箱地址

### 问题 2：Gmail 应用密码无效

**症状**: "Authentication failed"

**解决方案**:
1. 确认两步验证已启用
2. 重新生成应用专用密码
3. 使用 16 位密码（无空格）

### 问题 3：告警不触发

**症状**: 告警规则配置正确但不发送邮件

**解决方案**:
1. 确认告警规则状态为 "Firing"
2. 检查 Contact Point 是否关联
3. 查看 Grafana 日志：`docker logs Yunshu-grafana`

### 问题 4：邮件延迟

**症状**: 邮件发送延迟超过 5 分钟

**解决方案**:
1. 检查 SMTP 服务器响应时间
2. 调整 Evaluation interval
3. 检查网络连接

---

## 高级配置

### 配置邮件模板

创建自定义邮件模板文件：

```yaml
# alerting_templates.yml
templates:
  - name: 'Yunshu_alert_template'
    template: |
      <h2>云枢 V2 告警通知</h2>
      <p><strong>告警级别:</strong> {{ .GroupLabels.level }}</p>
      <p><strong>触发时间:</strong> {{ .StartsAt }}</p>
      <hr>
      {{ range .Alerts }}
      <h3>告警详情</h3>
      <p>{{ .Annotations.description }}</p>
      {{ end }}
```

### 配置多收件人

在 Contact Point 中添加多个邮箱地址：

```
recipient1@example.com, recipient2@example.com, recipient3@example.com
```

### 配置告警抑制

创建抑制规则，避免重复告警：

```yaml
inhibit_rules:
  - source_matchers:
      - severity = "critical"
    target_matchers:
      - severity = "warning"
    equal: ['alertname']
```

---

## 告警级别参考

| 级别 | 触发条件 | 建议操作 |
|------|---------|---------|
| Critical | 危险操作被拦截 | 立即检查系统 |
| Warning | 可疑操作检测 | 关注并评估 |
| Info | 系统状态变化 | 记录并跟踪 |

---

## 验证清单

- [ ] SMTP 服务器配置正确
- [ ] Contact Point 创建成功
- [ ] 告警规则配置完成
- [ ] Contact Point 关联到告警规则
- [ ] 测试邮件发送成功
- [ ] 实际告警触发后邮件收到

---

## 相关文档

- [Grafana Alerting 文档](https://grafana.com/docs/grafana/latest/alerting/)
- [Prometheus Alerting 规则](https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/)
- [Gmail SMTP 配置](https://support.google.com/mail/answer/7120)

---

**文档版本**: 1.0  
**最后更新**: 2026-05-31  
**维护者**: 云枢开发团队