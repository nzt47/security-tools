# 云枢 V2 监控堆栈

> 本目录包含完整的监控解决方案，包括 Prometheus、Grafana 和可视化仪表盘。

## 目录结构

```
monitoring/
├── prometheus/
│   └── prometheus.yml              # Prometheus 配置文件
├── grafana/
│   ├── dashboards/
│   │   ├── dashboard.yml           # Dashboard 自动配置
│   │   └── Yunshu_v2_dashboard.json  # V2 功能监控仪表盘
│   └── datasources/
│       └── prometheus.yml          # 数据源自动配置
├── docker-compose.yml              # Docker Compose 配置
├── start_monitoring.sh            # Linux/Mac 启动脚本
├── start_monitoring.ps1            # Windows PowerShell 启动脚本
└── GRAFANA_SETUP_GUIDE.md         # Grafana 使用指南
```

## 快速开始

### Windows (PowerShell)

```powershell
# 1. 启动监控堆栈
.\monitoring\start_monitoring.ps1

# 2. 启动云枢 V2 指标导出
python prometheus_example.py

# 3. 访问 Grafana
# 浏览器打开: http://localhost:3000
# 默认用户名: admin
# 默认密码: admin

# 4. 查看仪表盘
# 左侧菜单 -> Dashboards -> 云枢 V2 监控仪表盘
```

### Linux/Mac

```bash
# 1. 启动监控堆栈
chmod +x ./monitoring/start_monitoring.sh
./monitoring/start_monitoring.sh

# 2. 启动云枢 V2 指标导出
python prometheus_example.py

# 3. 访问 Grafana
# 浏览器打开: http://localhost:3000
```

## 服务访问

| 服务 | 地址 | 默认凭证 |
|------|------|---------|
| Prometheus | http://localhost:9090 | - |
| Grafana | http://localhost:3000 | admin / admin |

## 主要功能

### 1. V2 模块状态监控

实时显示三个 V2 模块的启用状态：
- ✅ LifeTrace - 三层记忆系统
- ✅ Persona - 人格系统
- ✅ Distillation - 人格蒸馏

### 2. 性能指标

- **模块加载耗时** - 追踪每个模块的初始化时间
- **交互速率** - 每秒处理的交互数量
- **交互耗时** - p95/p50 响应时间

### 3. 安全告警

- **Critical 级别** - 危险操作拦截（红色）
- **Warning 级别** - 可疑操作检测（黄色）

### 4. 记忆统计

- **总记忆数** - 存储的记忆条目数量

## 提供的 Prometheus 指标

| 指标名称 | 类型 | 说明 |
|---------|------|------|
| `Yunshu_v2_module_load_duration_seconds` | Histogram | V2 模块加载耗时 |
| `Yunshu_v2_module_load_total` | Counter | V2 模块加载次数 |
| `Yunshu_v2_module_enabled` | Gauge | V2 模块启用状态 |
| `Yunshu_interaction_total` | Counter | 交互总次数 |
| `Yunshu_interaction_duration_seconds` | Histogram | 交互处理耗时 |
| `Yunshu_memory_count` | Gauge | 记忆数量 |
| `Yunshu_alert_total` | Counter | 安全告警总数 |

## 仪表盘面板说明

### 时间序列面板

1. **V2 Module Load Duration** - 模块加载耗时趋势
2. **Module Load Count** - 模块加载频率
3. **Interaction Rate** - 交互速率变化
4. **Interaction Duration** - 响应时间趋势
5. **Alerts by Level** - 告警趋势图

### 统计面板

1. **LifeTrace Status** - LifeTrace 模块状态
2. **Persona Status** - Persona 模块状态
3. **Distillation Status** - Distillation 模块状态
4. **Total Memories** - 记忆总数
5. **Critical Alerts** - Critical 告警数
6. **Warning Alerts** - Warning 告警数

## 手动操作

### 启动 Prometheus (不使用 Docker)

```bash
prometheus --config.file=./monitoring/prometheus/prometheus.yml
```

### 启动 Grafana (不使用 Docker)

```bash
# 下载并安装 Grafana
# 然后启动
grafana-server
```

### 导入仪表盘

1. 打开 Grafana: http://localhost:3000
2. 左侧菜单 -> Dashboards -> Import
3. 上传或粘贴: `monitoring/grafana_dashboards/Yunshu_v2_dashboard.json`
4. 选择 Prometheus 数据源
5. 点击 Import

## 高级配置

### 修改数据保留时间

编辑 `monitoring/prometheus/prometheus.yml`:

```yaml
command:
  - '--storage.tsdb.retention.time=30d'  # 改为 30 天
```

### 添加更多监控目标

编辑 `monitoring/prometheus/prometheus.yml`:

```yaml
scrape_configs:
  - job_name: 'Yunshu-v2'
    static_configs:
      - targets: ['localhost:8000']
  - job_name: 'your-other-app'
    static_configs:
      - targets: ['your-host:your-port']
```

### 配置告警通知

Grafana 支持多种通知渠道：
- Email
- Slack
- Webhook
- PagerDuty
- 等等

详见 `GRAFANA_SETUP_GUIDE.md`。

## 性能基准

### 正常范围

| 指标 | 正常范围 | 警告阈值 |
|------|---------|---------|
| 模块加载耗时 | < 50ms | > 100ms |
| 交互耗时 (p95) | < 500ms | > 1000ms |
| 交互速率 | 0-10/sec | > 50/sec |
| Critical 告警 | 0 | > 0 |

## 故障排查

### Prometheus 无法连接

```bash
# 检查 Prometheus 是否运行
docker ps | grep prometheus

# 检查 Prometheus 日志
docker logs Yunshu-prometheus

# 检查端口
netstat -an | grep 9090
```

### Grafana 仪表盘空白

1. 检查数据源是否配置正确
2. 确认 Prometheus 能抓取到数据
3. 检查时间范围设置

### 指标不显示

```bash
# 检查云枢 V2 指标导出是否运行
curl http://localhost:8000/metrics

# 检查 Prometheus 是否抓取到数据
curl http://localhost:9090/api/v1/query?query=Yunshu_v2_module_enabled
```

## 相关文档

- [Grafana 使用指南](GRAFANA_SETUP_GUIDE.md) - 详细的配置和告警配置说明
- [Prometheus 官方文档](https://prometheus.io/docs/)
- [Grafana 官方文档](https://grafana.com/docs/)

## 许可证

本监控配置随云枢 V2 项目一起发布。

---

**最后更新**: 2026-05-31
**版本**: 1.0
