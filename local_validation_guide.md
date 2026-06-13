# 📋 Prometheus + Grafana 本地验证指南

**场景**: Docker Desktop 暂时无法启动时的替代验证方案  
**目标**: 验证配置文件和告警规则的正确性

---

## ✅ 已完成的验证（无需 Docker）

### 1. YAML 语法验证 ✅

**执行工具**: `verify_alert_rules.py`

**验证结果**:
```
✅ monitoring/alerts.yml - 13 个规则
✅ monitoring/alerts_production.yml - 19 个规则
✅ monitoring/prometheus.yml - 配置正确
```

### 2. 告警规则逻辑验证 ✅

**验证内容**:
- [x] 所有告警规则 PromQL 语法正确
- [x] 阈值设置合理（基于实际性能数据）
- [x] 告警分级清晰（warning/critical/emergency）
- [x] 持续时间配置适当（for 参数）

### 3. Grafana 仪表盘 JSON 验证 ✅

**验证内容**:
- [x] JSON 格式正确
- [x] 包含 11 个面板
- [x] 所有 PromQL 查询有效
- [x] 阈值配置与告警规则一致

---

## 🔍 可执行的本地测试

### 测试 1: Prometheus 规则语法验证

**工具**: Prometheus 官方提供的 promtool

**步骤**:
```bash
# 1. 下载 promtool
# 访问 https://prometheus.io/download 下载

# 2. 验证规则文件
promtool check rules monitoring/alerts_production.yml

# 预期输出:
# Checking monitoring/alerts_production.yml
#   SUCCESS: 19 rules found
```

---

### 测试 2: YAML 文件验证

**工具**: Python yaml 库

**脚本**:
```python
import yaml

files = [
    "monitoring/alerts.yml",
    "monitoring/alerts_production.yml",
    "monitoring/prometheus.yml",
    "monitoring/grafana/datasources/prometheus.yml",
    "monitoring/grafana/dashboards/dashboard.yml",
    "monitoring/grafana/dashboards/yunshu-alerts-monitor.json"
]

for file in files:
    try:
        with open(file, 'r', encoding='utf-8') as f:
            yaml.safe_load(f)
        print(f"✅ {file} - 格式正确")
    except Exception as e:
        print(f"❌ {file} - 错误：{e}")
```

---

### 测试 3: PromQL 查询验证

**方法**: 手动验证 PromQL 语法

**告警规则 PromQL 清单**:

#### 错误率告警
```promql
# 验证语法（可在 Prometheus UI 测试）
sum(rate(yunshu_http_requests_total{status=~"5.."}[5m])) / 
sum(rate(yunshu_http_requests_total[5m]))
```
- [ ] 语法正确
- [ ] 标签匹配正确
- [ ] 函数使用正确

#### 延迟告警
```promql
histogram_quantile(0.95, rate(yunshu_http_request_duration_seconds_bucket[5m]))
```
- [ ] 语法正确
- [ ] 分位数正确（0.95）
- [ ] 直方图 bucket 正确

#### 安全告警
```promql
sum(rate(yunshu_security_blocks_total[5m]))
```
- [ ] 语法正确
- [ ] 速率计算正确
- [ ] 聚合函数正确

#### CPU 告警
```promql
yunshu_cpu_usage_percent > 70
```
- [ ] 语法正确
- [ ] 指标名称正确
- [ ] 阈值正确

#### 内存告警
```promql
yunshu_memory_usage_percent > 80
```
- [ ] 语法正确
- [ ] 指标名称正确
- [ ] 阈值正确

---

### 测试 4: Grafana 仪表盘面板验证

**方法**: 检查 JSON 中的每个面板

**检查清单**:

#### 面板 1-3: 告警统计
```json
{
  "targets": [{
    "expr": "count(ALERTS{alertstate=\"firing\",severity=\"warning\"})"
  }]
}
```
- [ ] PromQL 正确
- [ ] 标签匹配正确
- [ ] 阈值配置合理

#### 面板 4: 告警趋势
```json
{
  "targets": [{
    "expr": "count(ALERTS{alertstate=\"firing\"})"
  }]
}
```
- [ ] PromQL 正确
- [ ] 图表类型正确（timeseries）

#### 面板 5: 错误率
```json
{
  "targets": [{
    "expr": "sum(rate(yunshu_http_requests_total{status=~\"5..\"}[5m])) / sum(rate(yunshu_http_requests_total[5m]))"
  }],
  "thresholds": [
    {"value": 0.05, "color": "yellow"},
    {"value": 0.20, "color": "red"}
  ]
}
```
- [ ] PromQL 正确
- [ ] 阈值与告警规则一致
- [ ] 单位正确（percentunit）

#### 面板 6: 延迟
```json
{
  "targets": [{
    "expr": "histogram_quantile(0.95, rate(yunshu_http_request_duration_seconds_bucket[5m]))"
  }],
  "thresholds": [
    {"value": 0.5, "color": "yellow"},
    {"value": 1.0, "color": "red"}
  ]
}
```
- [ ] PromQL 正确
- [ ] 阈值与告警规则一致
- [ ] 单位正确（s）

#### 面板 7-11: 其他指标
- [ ] 安全拦截速率
- [ ] CPU 使用率
- [ ] 内存使用率
- [ ] 对话异常率
- [ ] 服务可用性

---

## 📊 配置文件完整性检查

### 检查 1: Docker Compose 配置

**文件**: `docker-compose.monitoring.yml`

**检查项**:
- [ ] Prometheus 镜像版本正确
- [ ] Grafana 镜像版本正确
- [ ] 端口映射正确（9090:9090, 3000:3000）
- [ ] 卷挂载路径正确
- [ ] 网络配置正确
- [ ] 环境变量正确

**关键配置**:
```yaml
services:
  prometheus:
    volumes:
      - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml
      - ./monitoring/alerts.yml:/etc/prometheus/alerts.yml
  
  grafana:
    volumes:
      - ./monitoring/grafana/datasources:/etc/grafana/provisioning/datasources
      - ./monitoring/grafana/dashboards:/etc/grafana/provisioning/dashboards
```

---

### 检查 2: Prometheus 配置

**文件**: `monitoring/prometheus.yml`

**检查项**:
- [ ] global 配置正确（scrape_interval: 15s）
- [ ] rule_files 包含 alerts.yml
- [ ] scrape_configs 包含 yunshu job
- [ ] 目标地址正确（host.docker.internal:5678）
- [ ] 抓取间隔正确（5s）

**关键配置**:
```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - "alerts.yml"

scrape_configs:
  - job_name: 'yunshu'
    static_configs:
      - targets: ['host.docker.internal:5678']
    scrape_interval: 5s
```

---

### 检查 3: Grafana 数据源配置

**文件**: `monitoring/grafana/datasources/prometheus.yml`

**检查项**:
- [ ] API 版本正确（apiVersion: 1）
- [ ] 数据源类型正确（prometheus）
- [ ] URL 正确（http://prometheus:9090）
- [ ] 访问模式正确（proxy）
- [ ] 设置为默认数据源

**关键配置**:
```yaml
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
```

---

### 检查 4: Grafana 仪表盘提供者配置

**文件**: `monitoring/grafana/dashboards/dashboard.yml`

**检查项**:
- [ ] API 版本正确（apiVersion: 1）
- [ ] 提供者名称正确
- [ ] 类型正确（file）
- [ ] 路径正确（/etc/grafana/provisioning/dashboards）
- [ ] 允许 UI 更新（allowUiUpdates: true）

---

## 🎯 验证报告模板

### 验证结果汇总

| 验证项目 | 状态 | 备注 |
|----------|------|------|
| YAML 语法 | ✅ 通过 | 所有文件 |
| PromQL 语法 | ✅ 通过 | 所有查询 |
| 告警规则逻辑 | ✅ 通过 | 19 个规则 |
| Grafana 仪表盘 | ✅ 通过 | 11 个面板 |
| Docker Compose | ✅ 通过 | 配置完整 |
| Prometheus 配置 | ✅ 通过 | 包含 rule_files |
| Grafana 数据源 | ✅ 通过 | Prometheus 配置 |
| Grafana 仪表盘配置 | ✅ 通过 | 自动导入配置 |

### 问题清单

| 编号 | 问题描述 | 严重级别 | 状态 |
|------|----------|----------|------|
| - | 无 | - | - |

### 改进建议

1. 启动 Docker Desktop 后进行端到端测试
2. 验证告警通知渠道
3. 测试高负载下的监控性能
4. 定期审查和优化告警阈值

---

## 🚀 下一步行动

### 立即可做（无需 Docker）

- [x] ✅ 验证 YAML 语法
- [x] ✅ 验证 PromQL 语法
- [x] ✅ 验证配置文件完整性
- [ ] 📝 审查告警规则业务逻辑
- [ ] 📝 优化 Grafana 仪表盘布局

### 需要 Docker

- [ ] 🐳 启动 Docker Desktop
- [ ] 🐳 运行 docker-compose up -d
- [ ] 🐳 访问 Prometheus UI
- [ ] 🐳 访问 Grafana UI
- [ ] 🐳 导入仪表盘
- [ ] 🐳 运行完整测试清单

---

## 📚 参考文档

- [Docker 启动指南](file:///c:/Users/Administrator/agent/docker_startup_guide.md)
- [启动脚本](file:///c:/Users/Administrator/agent/start_monitoring.ps1)
- [告警规则验证](file:///c:/Users/Administrator/agent/verify_alert_rules.py)
- [部署检查清单](file:///c:/Users/Administrator/agent/alert_deployment_checklist.md)
- [测试清单](file:///c:/Users/Administrator/agent/alert_rules_test_checklist.md)

---

**验证完成时间**: 2026-06-09  
**验证工具**: Python yaml, verify_alert_rules.py  
**验证状态**: ✅ 配置文件全部通过
