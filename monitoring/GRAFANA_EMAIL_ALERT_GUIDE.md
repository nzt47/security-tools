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

编辑 **`docker-compose.monitoring.yml`（仓库根目录，权威文件）** 的 `grafana` 服务：

> 不要用 `monitoring/docker-compose.yml`——那是旧快照，它的挂载路径在本仓解析不到、且加载的是旧 scrape 配置。

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
| Subject | `[CRITICAL] 云枢告警 - {{ .GroupLabels.alertname }}` |
| Message | 自定义邮件内容模板 |

### 邮件内容模板示例

```html
<h2>云枢告警通知</h2>

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
<p>请立即检查云枢系统状态，确认是否有危险操作被拦截。</p>

<p>查看详情: <a href="http://localhost:3000/d/yunshu-alerts">仪表盘链接</a>（uid 取自 `monitoring/grafana/dashboards/yunshu-alerts-monitor.json`）</p>
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
| Namespace | `云枢告警`（自定义命名空间名，与指标名无关） |
| Query | `sum(yunshu_security_blocks_total{level="critical"}) > 0` |
| Evaluation interval | `1m` |
| For duration | `0s`（立即触发） |

> **为什么换成这个**：`Yunshu_alert_total`（大写 Y 命名空间）在本部署不存在（那套 exporter 从未实例化）。
> 语义等价的真实指标是 `yunshu_security_blocks_total`，其 `level` 标签实测存在（`critical`/`warning`），
> 与 `monitoring/prometheus/alert_rules.yml` 里的规则同源。

#### Annotations 配置

| 字段 | 值 |
|------|---|
| description | `检测到危险操作被拦截` |
| severity | `critical` |
| runbook_url | `http://localhost:3000/d/yunshu-alerts`（真实看板 uid = `yunshu-alerts`） |

#### 规则 2：应用抓取失败告警（替代原「模块加载失败告警」）

> **原规则作废**：`Yunshu_v2_module_load_total` 这类 V2 模块指标**本部署无对应指标**
> （模块加载链路的 exporter 从未接出），配上去只会是一条永不触发的死规则。
> 该位置改用真实可用的采集健康规则：

| 字段 | 值 |
|------|---|
| Rule name | `Yunshu Scrape Down` |
| Query | `up{job="yunshu"} == 0` |
| Evaluation interval | `1m` |
| For duration | `1m` |

> 另一个等价选择：`up{job="yunshu-business"} == 0`（业务指标端点 `/api/business/prometheus` 抓取失败，
> 它挂掉时 `job="yunshu"` 可能仍是 1，两者建议各配一条）。

#### 规则 3：交互耗时告警

| 字段 | 值 |
|------|---|
| Rule name | `Interaction Slow` |
| Query | `max(yunshu_interaction_duration_seconds{quantile="0.95"}) > 3` |
| Evaluation interval | `5m` |
| For duration | `2m` |

> **为什么不能写 `histogram_quantile(... _bucket ...)`**：
> `yunshu_interaction_duration_seconds` 由云枢**自带的文本导出器**输出，只导出 `{quantile="0.5|0.95|0.99"}`
> 分位数样本、**没有 `_bucket` 序列**，histogram_quantile 会返回空。直接取分位数即可。
> 另注意单位是**秒**（旧文档写 `> 1000` 是拿毫秒当秒用，等于永不触发）。
> 有真直方图的是 HTTP 层：`histogram_quantile(0.95, sum(rate(yunshu_http_request_duration_seconds_bucket[5m])) by (le))`。

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

### 方式 2：直接在 Prometheus 里验证查询有值（推荐先做这步）

Grafana 规则不触发，九成是查询本身没有数据。先用真实指标确认链路是通的：

```bash
# 安全拦截总数（危险操作拦截规则的源）
curl -s 'http://localhost:9090/api/v1/query?query=sum(yunshu_security_blocks_total{level="critical"})'

# 交互 p95（单位：秒）
curl -s 'http://localhost:9090/api/v1/query?query=max(yunshu_interaction_duration_seconds{quantile="0.95"})'

# 采集是否正常：两个 job 都应为 1
curl -s 'http://localhost:9090/api/v1/query?query=up{job=~"yunshu.*"}'
```

返回值为空（`"result":[]`）说明指标名不对或指标无写入——此时改告警规则再多次也没用。

### 方式 3：触发一次真实事件

危险操作拦截类告警来自真实拦截动作（`yunshu_security_blocks_total` 由拦截链路写入）。
要端到端验证，可在测试环境故意触发一次被拦截的操作，然后确认：

```bash
# 拦截计数是否增长
curl -s 'http://localhost:9090/api/v1/query?query=sum(yunshu_security_blocks_total)'
```

> 旧文档里的 `PrometheusMetricsExporter(port=8000) + exporter.record_alert("critical")` 已删除：
> 那是 `Yunshu_*` 命名空间的 exporter，本部署**从未实例化**（`agent/monitoring/prometheus.py` 中已注释），
> 且端口也应是 5678。照它做只会得到一个不存在的指标。

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
3. 查看 Grafana 日志：`docker logs yunshu-grafana`（容器名以权威 compose 为准）

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
  - name: 'yunshu_alert_template'   # 模板名，与指标名无关；小写以免与已作废的 Yunshu_* 指标命名混淆
    template: |
      <h2>云枢告警通知</h2>
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
| Critical | `sum(yunshu_security_blocks_total{level="critical"}) > 0`（危险操作被拦截） | 立即检查系统 |
| Warning | `sum(yunshu_security_blocks_total{level="warning"}) > 5` 或交互 p95 > 3s | 关注并评估 |
| Info | `up{job="yunshu"}` 抖动、配置/版本变更 | 记录并跟踪 |

> `level` 取值 `critical` / `warning` 来自 `yunshu_security_blocks_total` 的标签，
> 与 `monitoring/prometheus/alert_rules.yml`、`monitoring/health_recording_rules.yml` 中的用法一致。

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