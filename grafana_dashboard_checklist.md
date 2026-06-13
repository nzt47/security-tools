# 📊 Grafana 常用监控面板导入清单

**适用项目**: Yunshu 监控栈  
**Grafana 版本**: 13.0.2  
**数据源**: Prometheus

---

## 🎯 快速导入指南

### 步骤 1: 登录 Grafana

- **URL**: http://localhost:3000
- **用户名**: admin
- **密码**: admin123

### 步骤 2: 导入仪表盘

1. 点击左侧菜单：**Dashboards** → **Import**
2. 点击 **"Upload dashboard JSON"**
3. 选择 JSON 文件
4. 选择数据源：**Prometheus**
5. 点击 **"Import"**

---

## 📋 推荐导入的监控面板

### 1. Yunshu 核心监控（必装）⭐⭐⭐⭐⭐

**文件**: `monitoring/grafana/dashboards/yunshu-alerts-monitor.json`

**包含面板**:
- Warning 告警数
- Critical 告警数
- Emergency 告警数
- 活跃告警趋势
- 错误率（5%/20%/50%）
- 95 分位延迟（500ms/1s/2s）
- 安全拦截速率（3/10/30 次/分）
- CPU 使用率（70%/90%）
- 内存使用率（80%/90%）
- 对话异常率（10%/30%）
- 服务可用性

**导入优先级**: ⭐⭐⭐⭐⭐  
**预计时间**: 2 分钟

---

### 2. Prometheus 系统监控（推荐）⭐⭐⭐⭐

**来源**: Grafana 官方库  
**ID**: 3662  
**URL**: https://grafana.com/grafana/dashboards/3662-prometheus-overview/

**包含面板**:
- Prometheus 服务器状态
- 抓取速率
- 查询性能
- 规则评估
- 通知速率
- 内存使用
- 存储使用

**导入方式**:
1. Dashboards → Import
2. 输入 ID: `3662`
3. 点击 **Load**
4. 选择 Prometheus 数据源
5. 点击 **Import**

**导入优先级**: ⭐⭐⭐⭐  
**预计时间**: 3 分钟

---

### 3. Docker 容器监控（推荐）⭐⭐⭐⭐

**来源**: Grafana 官方库  
**ID**: 11981  
**URL**: https://grafana.com/grafana/dashboards/11981-docker-container-monitoring/

**包含面板**:
- CPU 使用率
- 内存使用率
- 网络流量
- 磁盘 I/O
- 容器状态
- 重启次数

**导入方式**:
1. Dashboards → Import
2. 输入 ID: `11981`
3. 选择 Prometheus 数据源
4. 点击 **Import**

**导入优先级**: ⭐⭐⭐⭐  
**预计时间**: 3 分钟

---

### 4. Node Exporter 系统监控（可选）⭐⭐⭐

**来源**: Grafana 官方库  
**ID**: 1860  
**URL**: https://grafana.com/grafana/dashboards/1860-node-exporter-full/

**包含面板**:
- CPU 详细指标
- 内存详细指标
- 磁盘使用
- 网络统计
- 系统负载
- 进程统计

**导入方式**:
1. Dashboards → Import
2. 输入 ID: `1860`
3. 选择 Prometheus 数据源
4. 点击 **Import**

**导入优先级**: ⭐⭐⭐  
**预计时间**: 3 分钟

**注意**: 需要先安装 Node Exporter

---

### 5. 自定义业务监控（推荐）⭐⭐⭐⭐

**创建自己的仪表盘**:

**步骤**:
1. 点击 **Dashboards** → **New Dashboard**
2. 点击 **Add new panel**
3. 输入 PromQL 查询
4. 配置面板标题和描述
5. 选择可视化类型（Graph, Gauge, Stat 等）
6. 点击 **Apply**
7. 点击 **Save dashboard**

**推荐监控指标**:

```promql
# HTTP 请求速率
rate(yunshu_http_requests_total[5m])

# 错误率
sum(rate(yunshu_http_requests_total{status=~"5.."}[5m])) / sum(rate(yunshu_http_requests_total[5m]))

# 平均响应时间
rate(yunshu_http_request_duration_seconds_sum[5m]) / rate(yunshu_http_request_duration_seconds_count[5m])

# 安全拦截次数
yunshu_security_blocks_total

# 对话次数
yunshu_conversations_total

# CPU 使用率
yunshu_cpu_usage_percent

# 内存使用率
yunshu_memory_usage_percent
```

**导入优先级**: ⭐⭐⭐⭐  
**预计时间**: 10-20 分钟

---

## 🔧 数据源配置

### 配置 Prometheus 数据源

**步骤**:

1. **Configuration** → **Data Sources** → **Add data source**
2. 选择：**Prometheus**
3. 配置:
   - **Name**: Prometheus
   - **Type**: Prometheus
   - **URL**: `http://prometheus:9090` (容器内访问)
   - **Access**: Server (default)
4. 点击 **Save & Test**
5. 验证：**"Data source is working"**

---

## 📊 面板配置建议

### 告警面板配置

**推荐配置**:

| 面板类型 | 用途 | 阈值配置 |
|----------|------|----------|
| **Stat** | 显示当前告警数 | 根据严重级别着色 |
| **Time series** | 告警趋势图 | 显示 1 小时数据 |
| **Gauge** | 错误率/使用率 | 设置警告和危险阈值 |
| **Table** | 活跃告警列表 | 显示详细信息 |

### 性能面板配置

**推荐配置**:

| 指标 | 面板类型 | 警告阈值 | 危险阈值 |
|------|----------|----------|----------|
| 错误率 | Gauge | >5% | >20% |
| 延迟 (95 分位) | Stat | >500ms | >2s |
| CPU 使用率 | Gauge | >70% | >90% |
| 内存使用率 | Gauge | >80% | >90% |
| 安全拦截速率 | Time series | - | - |

---

## 🎨 面板美化建议

### 颜色配置

**推荐配色方案**:

```json
{
  "thresholds": {
    "mode": "absolute",
    "steps": [
      { "color": "green", "value": null },
      { "color": "yellow", "value": 70 },
      { "color": "red", "value": 90 }
    ]
  }
}
```

### 时间范围

**推荐默认设置**:
- 默认时间范围：`Last 1 hour`
- 自动刷新：`30s` 或 `1m`

### 变量配置

**推荐变量**:

```json
{
  "name": "instance",
  "type": "query",
  "query": "label_values(up, instance)"
}
```

---

## 📥 导入检查清单

### 导入前检查

- [ ] ✅ Grafana 运行正常
- [ ] ✅ Prometheus 数据源已配置
- [ ] ✅ JSON 文件存在且格式正确
- [ ] ✅ 有足够的权限导入仪表盘

### 导入后验证

- [ ] ✅ 所有面板正常显示
- [ ] ✅ 数据源连接正常
- [ ] ✅ 数据显示正确（无"no data"）
- [ ] ✅ 告警阈值配置正确
- [ ] ✅ 时间范围设置合理
- [ ] ✅ 自动刷新已启用

---

## 🔧 常见问题解决

### 问题 1: "Dashboard not found"

**解决方案**:
- 确认 JSON 文件路径正确
- 使用绝对路径
- 检查文件权限

### 问题 2: "Data source not found"

**解决方案**:
- 检查数据源名称是否匹配
- 重新配置 Prometheus 数据源
- 验证数据源连接

### 问题 3: "No data"

**解决方案**:
- 检查 Prometheus 是否正常运行
- 验证 PromQL 查询语法
- 检查时间范围是否合适
- 确认指标名称正确

### 问题 4: 面板显示错误

**解决方案**:
- 编辑面板检查查询
- 重新选择数据源
- 刷新仪表盘
- 清除浏览器缓存

---

## 📚 资源链接

### Grafana 官方资源

- [Grafana Dashboards](https://grafana.com/grafana/dashboards/)
- [Grafana Documentation](https://grafana.com/docs/)
- [Prometheus Exporters](https://prometheus.io/docs/operating/integrations/)

### 推荐仪表盘

- [Prometheus Overview (ID: 3662)](https://grafana.com/grafana/dashboards/3662)
- [Docker Monitoring (ID: 11981)](https://grafana.com/grafana/dashboards/11981)
- [Node Exporter Full (ID: 1860)](https://grafana.com/grafana/dashboards/1860)

---

## 🎯 导入优先级总结

| 仪表盘 | 优先级 | 时间 | 必要性 |
|--------|--------|------|--------|
| **Yunshu Alerts Monitor** | ⭐⭐⭐⭐⭐ | 2 分钟 | 必装 |
| **Prometheus Overview** | ⭐⭐⭐⭐ | 3 分钟 | 推荐 |
| **Docker Container Monitoring** | ⭐⭐⭐⭐ | 3 分钟 | 推荐 |
| **自定义业务监控** | ⭐⭐⭐⭐ | 10-20 分钟 | 推荐 |
| **Node Exporter Full** | ⭐⭐⭐ | 3 分钟 | 可选 |

**总预计时间**: 20-30 分钟（全部导入）

---

## ✅ 快速开始

### 5 分钟快速配置

```powershell
# 1. 打开 Grafana
Start-Process "http://localhost:3000"

# 2. 登录：admin / admin123

# 3. 导入 Yunshu 核心监控
# Dashboards → Import → Upload JSON
# 文件：monitoring/grafana/dashboards/yunshu-alerts-monitor.json

# 4. 验证 11 个面板正常显示

# 5. 配置自动刷新：30s
```

### 30 分钟完整配置

```powershell
# 1. 导入 Yunshu Alerts Monitor (2 分钟)
# 2. 导入 Prometheus Overview (3 分钟)
# 3. 导入 Docker Monitoring (3 分钟)
# 4. 配置自定义业务监控 (10 分钟)
# 5. 优化面板配置和美化 (10 分钟)
# 6. 配置告警通知 (2 分钟)
```

---

**文档版本**: 1.0  
**更新时间**: 2026-06-09  
**适用环境**: Yunshu 监控栈 + Prometheus + Grafana
