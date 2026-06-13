# Grafana 仪表盘配置指南

> 本文档详细说明如何配置 Grafana 仪表盘以可视化展示云枢 V2 功能的 Prometheus 指标。

## 目录

1. [前提条件](#前提条件)
2. [快速开始](#快速开始)
3. [导入仪表盘](#导入仪表盘)
4. [配置数据源](#配置数据源)
5. [仪表盘功能说明](#仪表盘功能说明)
6. [告警配置](#告警配置)
7. [故障排查](#故障排查)

---

## 前提条件

### 必需组件

1. **Prometheus** - 用于采集指标
   - 安装指南：https://prometheus.io/docs/prometheus/latest/installation/
   - 或使用 Docker：`docker run -d -p 9090:9090 prom/prometheus`

2. **Grafana** - 用于可视化
   - 安装指南：https://grafana.com/docs/grafana/latest/installation/
   - 或使用 Docker：`docker run -d -p 3000:3000 grafana/grafana`

3. **prometheus_client** - Python 库
   ```bash
   pip install prometheus_client
   ```

---

## 快速开始

### 1. 启动 Prometheus

```bash
# 使用默认配置启动 Prometheus
prometheus --config.file=./monitoring/prometheus/prometheus.yml

# 或使用 Docker
docker run -d \
  --name prometheus \
  -p 9090:9090 \
  -v $(pwd)/monitoring/prometheus:/etc/prometheus \
  prom/prometheus \
  --config.file=/etc/prometheus/prometheus.yml
```

### 2. 启动 Grafana

```bash
# 使用 Docker 启动 Grafana
docker run -d \
  --name grafana \
  -p 3000:3000 \
  grafana/grafana

# 默认用户名密码：admin / admin
```

### 3. 启动云枢 V2 监控

```bash
# 确保已安装 prometheus_client
pip install prometheus_client

# 启动监控
python prometheus_example.py
```

---

## 导入仪表盘

### 方式 1：通过 UI 导入

1. 登录 Grafana（http://localhost:3000）
2. 点击左侧菜单 "Dashboards"
3. 点击 "Import"
4. 上传 JSON 文件或粘贴 JSON 内容
   - 文件位置：`monitoring/grafana_dashboards/Yunshu_v2_dashboard.json`
5. 选择 Prometheus 数据源（如果没有，先添加）
6. 点击 "Import"

### 方式 2：通过 API 导入

```bash
# 创建 API Token（在 Grafana UI 中：Configuration -> API Keys）
curl -X POST \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d @monitoring/grafana_dashboards/Yunshu_v2_dashboard.json \
  http://localhost:3000/api/dashboards/db
```

---

## 配置数据源

### 添加 Prometheus 数据源

1. 登录 Grafana
2. 点击左侧菜单 "Configuration" -> "Data Sources"
3. 点击 "Add data source"
4. 选择 "Prometheus"
5. 配置以下参数：
   - **Name**: `Prometheus`（或任意名称）
   - **URL**: `http://localhost:9090`（Prometheus 地址）
   - **Scrape interval**: `15s`
6. 点击 "Save & test"

### 验证数据源

成功配置后，会看到绿色提示："Data source is working"

---

## 仪表盘功能说明

导入的仪表盘包含以下面板：

### 1. V2 Module Load Duration (模块加载耗时)

**图表类型**: 时间序列图

**指标**:
- p95 加载耗时（95%分位数）
- p50 加载耗时（中位数）

**单位**: 毫秒（ms）

**关注点**:
- 模块加载时间是否过长（>100ms 需关注）
- 是否有异常波动

### 2. V2 Module Status (模块状态)

**图表类型**: 状态指示器

**指标**:
- LifeTrace: 是否启用
- Persona: 是否启用
- Distillation: 是否启用

**关注点**:
- 绿色 = 启用
- 红色 = 禁用

### 3. Module Load Count (模块加载次数)

**图表类型**: 时间序列图

**指标**:
- 按模块和状态（success/failure）统计

**关注点**:
- 失败次数是否异常增多
- 加载频率是否正常

### 4. Interaction Rate (交互速率)

**图表类型**: 时间序列图

**指标**:
- 每秒交互次数

**关注点**:
- 交互量是否正常
- 是否有流量异常

### 5. Interaction Duration (交互耗时)

**图表类型**: 时间序列图

**指标**:
- p95 交互耗时（95%分位数）
- p50 交互耗时（中位数）

**单位**: 毫秒（ms）

**关注点**:
- 交互响应时间是否过长
- 是否有性能瓶颈

### 6. Total Memories (记忆总数)

**图表类型**: 统计卡片

**指标**:
- 当前存储的记忆数量

**关注点**:
- 记忆数量是否合理
- 是否需要清理或压缩

### 7. Alerts (告警统计)

**图表类型**: 统计卡片 + 时间序列图

**指标**:
- Critical 级别告警数
- Warning 级别告警数
- 按级别分类的告警趋势

**关注点**:
- Critical 告警数 > 0 表示有危险操作被拦截
- Warning 告警数过多需关注

---

## 告警配置

### 创建告警规则

#### Critical 告警（危险操作拦截）

1. 进入仪表盘
2. 点击 "Alerts" 标签
3. 点击 "Create alert"
4. 配置告警条件：
   ```
   sum(Yunshu_alert_total{level="critical"}) > 0
   ```
5. 设置评估周期：1分钟
6. 配置通知渠道（见下文）

#### 模块加载失败告警

```
sum(rate(Yunshu_v2_module_load_total{status="failure"}[5m])) > 0
```

#### 交互超时告警

```
histogram_quantile(0.95, sum(rate(Yunshu_interaction_duration_seconds_bucket[5m])) by (le)) > 1000
```

### 配置通知渠道

#### 方式 1：Email

1. Configuration -> Contact points
2. 添加 Email 联系人
3. 配置 SMTP 设置

#### 方式 2：Webhook

```json
{
  "receiver": "webhook",
  "webhook_configs": [
    {
      "url": "http://your-webhook-endpoint.com/alerts"
    }
  ]
}
```

#### 方式 3：Slack

1. 在 Slack 创建 Incoming Webhook
2. Grafana 配置中选择 Slack
3. 填写 Webhook URL

---

## 故障排查

### 问题 1: 数据源连接失败

**症状**: "Failed to connect to Prometheus"

**解决方案**:
1. 确认 Prometheus 是否运行：`curl http://localhost:9090/-/healthy`
2. 确认 Prometheus 地址配置正确
3. 检查防火墙是否阻止了 9090 端口

### 问题 2: 指标不显示

**症状**: 仪表盘显示 "No data"

**解决方案**:
1. 确认云枢 V2 监控是否运行：`curl http://localhost:8000/metrics`
2. 确认 Prometheus 是否能抓取到指标
3. 检查 Prometheus Targets 页面：http://localhost:9090/targets

### 问题 3: 告警不触发

**症状**: 告警规则配置正确但不触发

**解决方案**:
1. 确认告警规则状态为 "Pending" 或 "Firing"
2. 检查通知渠道配置
3. 查看 Grafana 日志：`docker logs grafana`

---

## 性能基准参考

### 正常范围

| 指标 | 正常范围 | 警告阈值 | 告警阈值 |
|------|---------|---------|---------|
| 模块加载耗时 | < 50ms | > 100ms | > 500ms |
| 交互耗时 (p95) | < 500ms | > 1000ms | > 3000ms |
| 交互速率 | 0-10/sec | > 50/sec | > 100/sec |
| Critical 告警 | 0 | > 0 | > 5 |
| Warning 告警 | 0-5 | > 10 | > 20 |

---

## 进阶配置

### 1. 配置数据保留策略

```yaml
# prometheus.yml
global:
  scrape_interval: 15s

storage:
  tsdb:
    retention.time: 30d  # 保留 30 天数据
```

### 2. 配置高可用

```yaml
# Prometheus 配置多个副本
scrape_configs:
  - job_name: 'federate'
    honor_labels: true
    metrics_path: '/federate'
    params:
      'match[]':
        - '{job="Yunshu-v2"}'
    static_configs:
      - targets:
          - 'prometheus1:9090'
          - 'prometheus2:9090'
```

### 3. 配置 Prometheus Operator（Kubernetes）

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: Yunshu-v2
spec:
  selector:
    matchLabels:
      app: Yunshu-v2
  endpoints:
    - port: metrics
      interval: 15s
```

---

## 相关文档

- [Prometheus 官方文档](https://prometheus.io/docs/introduction/overview/)
- [Grafana 官方文档](https://grafana.com/docs/grafana/latest/)
- [Grafana Dashboard JSON Schema](https://grafana.com/developers/grafana/docs/json-schema/)

---

**文档版本**: 1.0  
**最后更新**: 2026-05-31  
**维护者**: 云枢开发团队
