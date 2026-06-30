# Grafana 仪表盘快速配置指南

> 本指南提供快速配置 Grafana 仪表盘以实时查看云枢 Prometheus 指标的完整步骤。

## 一、快速启动（推荐）

### 方式 1：使用 Docker Compose（一键启动）

```bash
# 进入监控目录
cd monitoring

# 启动 Prometheus + Grafana
docker-compose up -d

# 查看服务状态
docker-compose ps
```

启动后访问：
- **Prometheus**: http://localhost:9090
- **Grafana**: http://localhost:3000 (默认账号: admin/admin)

### 方式 2：使用 PowerShell 脚本（Windows）

```powershell
# 运行启动脚本
.\monitoring\start_monitoring.ps1

# 或手动启动
docker run -d --name prometheus -p 9090:9090 \
  -v ${PWD}/monitoring/prometheus:/etc/prometheus \
  prom/prometheus

docker run -d --name grafana -p 3000:3000 \
  -v ${PWD}/monitoring/grafana:/etc/grafana/provisioning \
  grafana/grafana
```

---

## 二、导入仪表盘

### 方式 1：通过 Grafana UI 导入

1. 登录 Grafana (http://localhost:3000)
2. 左侧菜单 → **Dashboards** → **Import**
3. 选择以下任一仪表盘 JSON 文件上传：
   - `monitoring/grafana/dashboards/yunshu-full-monitoring.json` (推荐 - 全链路监控)
   - `monitoring/grafana/dashboards/yunshu-monitor.json` (基础监控)
   - `monitoring/grafana_dashboards/yunshu_v2_dashboard.json` (V2 版本)
4. 选择 Prometheus 数据源
5. 点击 **Import**

### 方式 2：通过 API 导入

```bash
# 设置 Grafana API Key（在 Grafana UI 中创建）
GRAFANA_API_KEY="your_api_key_here"

# 导入全链路监控仪表盘
curl -X POST \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $GRAFANA_API_KEY" \
  -d @monitoring/grafana/dashboards/yunshu-full-monitoring.json \
  http://localhost:3000/api/dashboards/db
```

### 方式 3：自动加载（使用 Provisioning）

Grafana 会自动加载 `grafana/provisioning/dashboards/` 目录下的仪表盘：

```yaml
# monitoring/grafana/dashboards/dashboard.yml (已配置)
apiVersion: 1

providers:
  - name: 'Yunshu Dashboards'
    orgId: 1
    folder: 'Yunshu'
    folderUid: 'yunshu-folder'
    type: file
    disableDeletion: false
    updateIntervalSeconds: 30
    allowUiUpdates: true
    options:
      path: /etc/grafana/provisioning/dashboards
```

---

## 三、配置 Prometheus 数据源

### 自动配置（Provisioning）

数据源已通过 `monitoring/grafana/datasources/prometheus.yml` 自动配置：

```yaml
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: true
    jsonData:
      timeInterval: "15s"
      httpMethod: POST
```

### 手动配置

1. Grafana UI → **Configuration** → **Data Sources**
2. 点击 **Add data source**
3. 选择 **Prometheus**
4. 配置参数：
   - **Name**: `Prometheus`
   - **URL**: `http://localhost:9090` 或 `http://prometheus:9090`
   - **Scrape interval**: `15s`
   - **HTTP Method**: `POST`
5. 点击 **Save & Test**
6. 确认显示绿色提示："Data source is working"

---

## 四、仪表盘面板说明

### 全链路监控仪表盘 (`yunshu-full-monitoring.json`)

#### 1. 服务健康状态概览
| 面板 | 指标 | 说明 |
|------|------|------|
| 服务状态 | `up{job="yunshu-app"}` | UP/DOWN 状态指示 |
| 24小时可用率 | `yunshu:uptime_percent:24h` | 服务可用性百分比 |
| CPU 使用率 | `yunshu_cpu_usage_percent` | 实时 CPU 使用率 |
| 内存使用率 | `yunshu_memory_usage_percent` | 实时内存使用率 |
| 响应时间 (P95) | `yunshu:latency_p95:5m` | 95分位响应时间 |
| 错误率 | `yunshu:error_rate:5m` | 错误请求百分比 |

#### 2. 请求流量与响应时间
| 面板 | 指标 | 说明 |
|------|------|------|
| 请求速率趋势 | `rate(yunshu_http_requests_total[5m])` | 每秒请求数趋势 |
| 响应时间分布 | `yunshu:latency_p50/p95/p99:5m` | 响应时间分位数 |

#### 3. 错误与告警监控
| 面板 | 指标 | 说明 |
|------|------|------|
| 错误分布趋势 | `increase(yunshu_error_total[5m])` | 按严重级别分类 |
| 告警统计 | `increase(yunshu_alert_total[1h])` | 每小时告警数量 |

#### 4. 部署与回滚监控
| 面板 | 指标 | 说明 |
|------|------|------|
| 部署状态 | `yunshu_deployment_status` | Stable/Deploying/Rollback/Failed |
| 24小时回滚次数 | `increase(yunshu_rollback_total[24h])` | 回滚次数统计 |
| 部署耗时 | `yunshu_deployment_duration_seconds` | 各环境部署耗时 |
| 部署失败次数 | `increase(yunshu_deployment_failures_total[24h])` | 失败次数统计 |
| 部署历史趋势 | `increase(yunshu_deployment_total[1h])` | 按状态分类趋势 |

#### 5. CI/CD 流水线监控
| 面板 | 指标 | 说明 |
|------|------|------|
| CI 流水线耗时 | `yunshu_ci_pipeline_duration_seconds` | 流水线总耗时 |
| 测试覆盖率 | `yunshu_ci_test_coverage_percent` | 测试覆盖率百分比 |
| 测试失败次数 | `increase(yunshu_ci_test_failures_total[24h])` | 24小时失败数 |
| 构建失败次数 | `increase(yunshu_ci_build_failures_total[24h])` | 24小时构建失败 |
| CI/CD 各阶段趋势 | `rate(yunshu_ci_pipeline_runs_total[1h])` | 各阶段运行趋势 |

#### 6. 业务指标监控
| 面板 | 指标 | 说明 |
|------|------|------|
| 记忆总数 | `yunshu_memory_count` | 存储的记忆数量 |
| 活跃用户数 | `yunshu:active_users:5m` | 活跃用户统计 |
| 安全拦截总数 | `yunshu_security_blocks_total` | 安全拦截次数 |
| 工具调用成功率 | `yunshu:tool_call_success_rate:5m` | 工具调用成功率 |
| Token 使用量 | `yunshu:token_usage:1h` | 每小时 Token 使用 |
| 每小时成本 | `yunshu:cost_per_hour` | 运行成本统计 |

---

## 五、告警配置

### Grafana 告警规则示例

#### 1. 服务宕机告警
```yaml
条件: up{job="yunshu-app"} == 0
评估周期: 1m
持续时间: 1m
通知渠道: Email/Webhook/Slack
```

#### 2. 高错误率告警
```yaml
条件: yunshu:error_rate:5m * 100 > 5
评估周期: 1m
持续时间: 2m
严重级别: warning
```

#### 3. 响应时间告警
```yaml
条件: yunshu:latency_p95:5m * 1000 > 1000
评估周期: 1m
持续时间: 3m
严重级别: warning
```

#### 4. 部署失败告警
```yaml
条件: increase(yunshu_deployment_failures_total[1h]) > 0
评估周期: 5m
严重级别: critical
```

#### 5. 回滚触发告警
```yaml
条件: increase(yunshu_rollback_total[1h]) > 0
评估周期: 5m
严重级别: critical
```

### 配置通知渠道

#### Email 通知
1. Grafana UI → **Alerting** → **Contact points**
2. 添加 Email 联系人
3. 配置 SMTP（在 `grafana.ini` 中）：
```ini
[smtp]
enabled = true
host = smtp.example.com:587
user = your_email@example.com
password = your_password
from_address = grafana@example.com
```

#### Webhook 通知
```json
{
  "name": "Webhook",
  "type": "webhook",
  "settings": {
    "url": "http://your-webhook-endpoint.com/alerts",
    "httpMethod": "POST"
  }
}
```

#### 企业微信/钉钉通知
使用 Webhook 方式，配置企业微信/钉钉机器人 Webhook URL。

---

## 六、验证步骤

### 1. 验证 Prometheus 数据采集

```bash
# 检查 Prometheus Targets
curl http://localhost:9090/api/v1/targets

# 查询指标
curl 'http://localhost:9090/api/v1/query?query=up{job="yunshu-app"}'
curl 'http://localhost:9090/api/v1/query?query=yunshu:error_rate:5m'
```

### 2. 验证 Grafana 数据源

```bash
# 测试数据源连接
curl -H "Authorization: Bearer $GRAFANA_API_KEY" \
  http://localhost:3000/api/datasources/proxy/1/api/v1/query?query=up
```

### 3. 验证仪表盘数据

在 Grafana UI 中：
1. 打开仪表盘
2. 检查各面板是否显示数据
3. 确认无 "No data" 提示
4. 检查时间范围设置（默认: now-1h to now）

---

## 七、常见问题排查

### 问题 1: 仪表盘显示 "No data"

**原因**:
- Prometheus 未采集到指标
- 数据源配置错误
- 时间范围不匹配

**解决方案**:
```bash
# 1. 检查 Prometheus Targets
curl http://localhost:9090/targets

# 2. 检查应用是否暴露指标
curl http://localhost:8000/metrics

# 3. 检查 Prometheus 配置
cat monitoring/prometheus/prometheus.yml
```

### 问题 2: 数据源连接失败

**原因**:
- Prometheus 未启动
- 网络隔离（Docker 网络问题）
- URL 配置错误

**解决方案**:
```bash
# 1. 检查 Prometheus 状态
docker ps | grep prometheus

# 2. 检查网络连接
docker network inspect monitoring_default

# 3. 使用正确的 URL
# Docker Compose: http://prometheus:9090
# 本地运行: http://localhost:9090
```

### 问题 3: 告警不触发

**原因**:
- 告警规则配置错误
- 通知渠道未配置
- 告警状态未达到触发条件

**解决方案**:
```bash
# 1. 检查告警规则状态
curl -H "Authorization: Bearer $GRAFANA_API_KEY" \
  http://localhost:3000/api/alerts

# 2. 检查通知渠道
curl -H "Authorization: Bearer $GRAFANA_API_KEY" \
  http://localhost:3000/api/alert-notifications

# 3. 手动测试告警
# 在 Grafana UI 中点击 "Test" 按钮
```

---

## 八、性能优化建议

### 1. 数据保留策略

```yaml
# prometheus.yml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

storage:
  tsdb:
    retention.time: 30d  # 保留 30 天
    retention.size: 10GB # 最大存储 10GB
```

### 2. Recording Rules（预聚合）

使用 `monitoring/recording_rules.yml` 中定义的预聚合规则：
- `yunshu:error_rate:5m` - 错误率预聚合
- `yunshu:latency_p50/p95/p99:5m` - 响应时间预聚合
- `yunshu:uptime_percent:24h` - 可用率预聚合

### 3. 仪表盘刷新频率

根据需求调整刷新频率：
- 实时监控: 5s - 10s
- 日常监控: 30s - 1m
- 历史分析: 5m - 15m

---

## 九、进阶配置

### 1. 多环境仪表盘

为不同环境创建仪表盘副本：
- Development: `yunshu-dev-monitoring.json`
- Staging: `yunshu-staging-monitoring.json`
- Production: `yunshu-prod-monitoring.json`

### 2. 自定义变量

在仪表盘 Templating 中添加变量：
- `environment`: 环境选择 (dev/staging/production)
- `endpoint`: API 端点选择
- `instance`: 实例选择

### 3. 嵌入外部系统

将 Grafana 仪表盘嵌入到其他系统：
```html
<iframe 
  src="http://grafana:3000/d/yunshu-full-monitoring?kiosk=1" 
  width="100%" 
  height="600px"
></iframe>
```

---

## 十、相关文档

- [Prometheus 官方文档](https://prometheus.io/docs/introduction/overview/)
- [Grafana 官方文档](https://grafana.com/docs/grafana/latest/)
- [Grafana Dashboard JSON Schema](https://grafana.com/developers/grafana/docs/json-schema/)
- [Prometheus Recording Rules](https://prometheus.io/docs/prometheus/latest/configuration/recording_rules/)
- [Grafana Alerting](https://grafana.com/docs/grafana/latest/alerting/)

---

**文档版本**: 2.0  
**最后更新**: 2026-06-24  
**维护者**: 云枢开发团队