# 🚀 Prometheus + Grafana 生产环境部署指南

**文档版本**: 1.0  
**更新日期**: 2026-06-09  
**适用环境**: Linux/Windows (Docker)

---

## 📋 目录

1. [概述](#概述)
2. [前置要求](#前置要求)
3. [快速部署 (Docker)](#快速部署-docker)
4. [手动部署 (Linux)](#手动部署-linux)
5. [配置详解](#配置详解)
6. [监控指标说明](#监控指标说明)
7. [告警配置](#告警配置)
8. [故障排查](#故障排查)
9. [最佳实践](#最佳实践)

---

## 概述

本指南介绍如何在生产环境中部署 Prometheus 和 Grafana 来监控云枢 (Yunshu) 应用。

### 监控架构

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│   Yunshu    │────▶│  Prometheus  │────▶│   Grafana   │
│  (Flask App)│     │   (Metrics)  │     │  (Dashboard)│
└─────────────┘     └──────────────┘     └─────────────┘
       │                    │                    │
       │                    │                    │
       ▼                    ▼                    ▼
   业务指标            存储指标            可视化展示
   - 对话次数          - CPU/内存          - 实时图表
   - API 调用           - 请求耗时          - 告警面板
   - 安全拦截          - 错误率            - 历史趋势
```

---

## 前置要求

### 硬件要求

| 组件 | CPU | 内存 | 磁盘 |
|------|-----|------|------|
| Prometheus | 2 核 | 4GB | 50GB SSD |
| Grafana | 1 核 | 2GB | 10GB |
| Yunshu | 2 核 | 4GB | 20GB |

**推荐配置**: 4 核 CPU, 8GB 内存，100GB SSD

### 软件要求

- Docker 20.10+
- Docker Compose 2.0+
- 或者 Python 3.8+ (手动部署)

---

## 快速部署 (Docker)

### 步骤 1: 准备配置文件

项目根目录已包含 Docker Compose 配置：

```bash
# 检查文件
ls -la docker-compose.monitoring.yml
ls -la monitoring/prometheus.yml
ls -la monitoring/grafana/
```

### 步骤 2: 启动服务

```bash
# 启动 Prometheus 和 Grafana
docker-compose -f docker-compose.monitoring.yml up -d

# 查看日志
docker-compose -f docker-compose.monitoring.yml logs -f
```

### 步骤 3: 验证部署

访问以下地址：

- **Prometheus**: http://localhost:9090
- **Grafana**: http://localhost:3000
  - 用户名：`admin`
  - 密码：`admin123`

### 步骤 4: 配置 Yunshu

修改 `app_server.py` 确保 Prometheus 监控已启用：

```python
# 确认已安装
pip install prometheus_flask_exporter

# 启动 Yunshu
python app_server.py
```

### 步骤 5: 验证指标

访问 Yunshu 指标端点：

```bash
curl http://localhost:5678/metrics
```

应该看到类似输出：

```prometheus
# HELP yunshu_http_requests_total Total HTTP requests
# TYPE yunshu_http_requests_total counter
yunshu_http_requests_total{endpoint="api_health",method="GET",status="200"} 15.0
```

---

## 手动部署 (Linux)

### Prometheus 部署

#### 1. 下载并安装

```bash
# 创建用户
sudo useradd -rs /bin/false prometheus

# 下载最新版本
cd /tmp
wget https://github.com/prometheus/prometheus/releases/download/v2.45.0/prometheus-2.45.0.linux-amd64.tar.gz

# 解压
tar xvfz prometheus-*.tar.gz
cd prometheus-*

# 移动文件
sudo mv prometheus promtool /usr/local/bin/
sudo mv consoles/ console_libraries/ /etc/prometheus/
sudo mv prometheus.yml /etc/prometheus/

# 设置权限
sudo chown -R prometheus:prometheus /etc/prometheus
```

#### 2. 创建 systemd 服务

```bash
sudo nano /etc/systemd/system/prometheus.service
```

内容：

```ini
[Unit]
Description=Prometheus
Wants=network-online.target
After=network-online.target

[Service]
User=prometheus
Group=prometheus
Type=simple
ExecStart=/usr/local/bin/prometheus \
  --config.file=/etc/prometheus/prometheus.yml \
  --storage.tsdb.path=/var/lib/prometheus/ \
  --web.console.templates=/etc/prometheus/consoles \
  --web.console.libraries=/etc/prometheus/console_libraries \
  --web.listen-address=0.0.0.0:9090 \
  --web.enable-lifecycle

Restart=always

[Install]
WantedBy=multi-user.target
```

#### 3. 启动服务

```bash
# 重新加载 systemd
sudo systemctl daemon-reload

# 启动 Prometheus
sudo systemctl start prometheus
sudo systemctl enable prometheus

# 检查状态
sudo systemctl status prometheus
```

### Grafana 部署

#### 1. 添加仓库

```bash
# Ubuntu/Debian
sudo apt-get install -y apt-transport-https software-properties-common
wget -q -O - https://packages.grafana.com/gpg.key | sudo apt-key add -
echo "deb https://packages.grafana.com/oss/deb stable main" | sudo tee -a /etc/apt/sources.list.d/grafana.list

# CentOS/RHEL
sudo tee -a /etc/yum.repos.d/grafana.repo << EOF
[grafana]
name=grafana
baseurl=https://packages.grafana.com/oss/rpm
repo_gpgcheck=1
enabled=1
gpgcheck=1
gpgkey=https://packages.grafana.com/gpg.key
EOF
```

#### 2. 安装 Grafana

```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install -y grafana

# CentOS/RHEL
sudo yum install -y grafana
```

#### 3. 启动服务

```bash
sudo systemctl start grafana-server
sudo systemctl enable grafana-server
sudo systemctl status grafana-server
```

#### 4. 配置数据源

访问 Grafana UI (http://localhost:3000)，然后：

1. 登录 (admin/admin)
2. 点击 Configuration → Data Sources
3. 点击 Add data source
4. 选择 Prometheus
5. 配置 URL: `http://localhost:9090`
6. 点击 Save & Test

---

## 配置详解

### Prometheus 配置

文件位置：`monitoring/prometheus.yml`

```yaml
global:
  scrape_interval: 15s      # 抓取间隔
  evaluation_interval: 15s  # 规则评估间隔

scrape_configs:
  - job_name: 'yunshu'
    static_configs:
      - targets: ['host.docker.internal:5678']  # Yunshu 地址
    metrics_path: '/metrics'
    scrape_interval: 5s  # 更频繁的抓取
    
  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']
```

### Grafana 仪表盘导入

项目已包含预配置仪表盘：`monitoring/grafana/dashboards/yunshu-monitor.json`

**导入步骤**:

1. 访问 Grafana (http://localhost:3000)
2. 点击 Dashboards → Import
3. 上传 JSON 文件或粘贴内容
4. 选择 Prometheus 数据源
5. 点击 Import

---

## 监控指标说明

### Yunshu 业务指标

| 指标名称 | 类型 | 说明 | 标签 |
|----------|------|------|------|
| `yunshu_http_requests_total` | Counter | HTTP 请求总数 | endpoint, method, status |
| `yunshu_http_request_duration_seconds` | Histogram | 请求耗时 | endpoint, method, status |
| `yunshu_security_blocks_total` | Counter | 安全拦截次数 | rule, level, category |
| `yunshu_conversations_total` | Counter | 对话次数 | status |
| `yunshu_cpu_usage_percent` | Gauge | CPU 使用率 | - |
| `yunshu_memory_usage_percent` | Gauge | 内存使用率 | - |

### Prometheus 查询示例

**请求速率**:
```promql
rate(yunshu_http_requests_total[5m])
```

**错误率**:
```promql
rate(yunshu_http_requests_total{status=~"5.."}[5m]) / rate(yunshu_http_requests_total[5m])
```

**95 分位耗时**:
```promql
histogram_quantile(0.95, rate(yunshu_http_request_duration_seconds_bucket[5m]))
```

**安全拦截趋势**:
```promql
rate(yunshu_security_blocks_total[5m])
```

---

## 告警配置

### Prometheus AlertManager

#### 1. 创建告警规则

文件：`/etc/prometheus/alerts.yml`

```yaml
groups:
  - name: yunshu_alerts
    rules:
      - alert: HighErrorRate
        expr: rate(yunshu_http_requests_total{status=~"5.."}[5m]) > 0.1
        for: 5m
        annotations:
          summary: "高错误率检测"
          description: "错误率超过 10%"
          
      - alert: HighLatency
        expr: histogram_quantile(0.95, rate(yunshu_http_request_duration_seconds_bucket[5m])) > 5
        for: 5m
        annotations:
          summary: "高延迟检测"
          description: "95 分位响应时间超过 5 秒"
          
      - alert: SecurityAttack
        expr: rate(yunshu_security_blocks_total[5m]) > 10
        for: 1m
        annotations:
          summary: "安全攻击检测"
          description: "检测到频繁的安全拦截"
```

#### 2. 在 Prometheus 中启用

```yaml
# prometheus.yml
rule_files:
  - "alerts.yml"

alerting:
  alertmanagers:
    - static_configs:
        - targets: ['localhost:9093']
```

### Grafana 告警

1. 在 Grafana 仪表盘中选择面板
2. 点击 Alert → Create Alert
3. 配置条件和阈值
4. 设置通知渠道（邮件、Slack、钉钉等）

---

## 故障排查

### Prometheus 无法抓取指标

**问题**: Target 显示 DOWN

**解决**:

```bash
# 1. 检查 Yunshu 是否运行
curl http://localhost:5678/metrics

# 2. 检查网络连通性
ping localhost

# 3. 检查防火墙
sudo ufw allow 5678/tcp
```

### Grafana 无法连接 Prometheus

**问题**: Data source 显示 Error

**解决**:

1. 检查 Prometheus 是否运行
2. 验证 URL 配置
3. 检查网络连接

```bash
# 测试 Prometheus
curl http://localhost:9090/api/v1/query?query=up

# 应该返回
{"status":"success","data":{"resultType":"vector","result":[{"metric":{"__name__":"up"},"value":[1686297600,"1"]}]}}
```

### 指标数据缺失

**问题**: 某些指标未显示

**解决**:

1. 确认 Yunshu 代码中已注册指标
2. 触发相关操作生成数据
3. 等待 Prometheus 抓取（默认 15 秒）

```bash
# 手动触发请求
curl http://localhost:5678/api/health
curl http://localhost:5678/metrics | grep yunshu
```

---

## 最佳实践

### 1. 数据保留

**Prometheus 配置**:

```yaml
# prometheus.yml
global:
  scrape_interval: 15s
  
# 启动参数
--storage.tsdb.retention.time=30d
--storage.tsdb.retention.size=50GB
```

### 2. 高可用部署

**多 Prometheus 实例**:

```yaml
# docker-compose.yml
services:
  prometheus-1:
    image: prom/prometheus:latest
    volumes:
      - ./prometheus-1.yml:/etc/prometheus/prometheus.yml
      
  prometheus-2:
    image: prom/prometheus:latest
    volumes:
      - ./prometheus-2.yml:/etc/prometheus/prometheus.yml
```

### 3. 安全加固

**防火墙配置**:

```bash
# 只允许内网访问
sudo ufw allow from 192.168.1.0/24 to any port 9090
sudo ufw allow from 192.168.1.0/24 to any port 3000
```

**Grafana 认证**:

```ini
# grafana.ini
[security]
admin_user = admin
admin_password = 强密码
secret_key = 随机密钥
```

### 4. 性能优化

**Prometheus 优化**:

```yaml
# 减少抓取间隔（高负载时）
scrape_interval: 30s

# 使用远程存储
remote_write:
  - url: "http://remote-storage:9201/write"
```

### 5. 备份策略

```bash
#!/bin/bash
# backup_prometheus.sh

BACKUP_DIR="/backup/prometheus"
DATE=$(date +%Y%m%d)

# 备份 Prometheus 数据
tar -czf $BACKUP_DIR/prometheus_$DATE.tar.gz /var/lib/prometheus/

# 备份配置文件
tar -czf $BACKUP_DIR/prometheus_config_$DATE.tar.gz /etc/prometheus/

# 删除 30 天前的备份
find $BACKUP_DIR -name "*.tar.gz" -mtime +30 -delete
```

---

## 参考资源

### 官方文档

- [Prometheus 官方文档](https://prometheus.io/docs/)
- [Grafana 官方文档](https://grafana.com/docs/)
- [prometheus_flask_exporter](https://github.com/rycus86/prometheus_flask_exporter)

### 项目文件

- [docker-compose.monitoring.yml](file:///c:/Users/Administrator/agent/docker-compose.monitoring.yml)
- [monitoring/prometheus.yml](file:///c:/Users/Administrator/agent/monitoring/prometheus.yml)
- [monitoring/grafana/dashboards/yunshu-monitor.json](file:///c:/Users/Administrator/agent/monitoring/grafana/dashboards/yunshu-monitor.json)

### 监控仪表盘模板

- [Flask Application Dashboard (ID: 10619)](https://grafana.com/grafana/dashboards/10619)
- [Prometheus Stats (ID: 2)](https://grafana.com/grafana/dashboards/2)

---

**文档结束**
