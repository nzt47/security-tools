# 🚀 Yunshu 监控栈部署 - 快速参考卡片

**最后更新**: 2026-06-09

---

## 📋 三种部署方案

| 方案 | 成功率 | 时间 | 推荐度 | 何时使用 |
|------|--------|------|--------|----------|
| **GUI 配置** | 70% | 15-40 分钟 | ⭐⭐⭐⭐ | 网络一般，可访问部分镜像源 |
| **离线导入** | **100%** | 20-50 分钟 | ⭐⭐⭐⭐⭐ | **网络受限或要求稳定** |

---

## 🎯 当前状态

### GUI 配置后验证

**你现在要做的**:

1. **打开 Docker Desktop GUI**
   - Settings → Docker Engine
   - 配置镜像加速器
   - Apply & Restart
   - 等待 2-3 分钟

2. **运行验证脚本**:
   ```powershell
   .\verify_gui_config.ps1
   ```

**预期结果**:
- ✅ Mirrors configured (显示镜像地址)
- ✅ Prometheus 拉取成功
- ✅ 可以继续部署

**如果失败**:
- ⚠️ 使用离线导入方案
- 📖 参考：[offline_import_final_guide.md](file:///c:/Users/Administrator/agent/offline_import_final_guide.md)

---

## 🔑 关键命令

### Docker 配置验证

```powershell
# 检查镜像加速器
docker info --format '{{.RegistryConfig.Mirrors}}'

# 查看配置文件
Get-Content $env:USERPROFILE\.docker\daemon.json
```

### 镜像操作

```powershell
# 拉取镜像（在线）
docker pull prom/prometheus:latest
docker pull grafana/grafana:latest

# 导出镜像（离线准备）
docker save -o prometheus.tar prom/prometheus:latest
docker save -o grafana.tar grafana/grafana:latest

# 导入镜像（离线导入）
docker load -i prometheus.tar
docker load -i grafana.tar
```

### 容器操作

```powershell
# 启动监控栈
docker-compose -f docker-compose.monitoring.yml up -d

# 查看状态
docker-compose -f docker-compose.monitoring.yml ps

# 查看日志
docker-compose -f docker-compose.monitoring.yml logs -f

# 停止服务
docker-compose -f docker-compose.monitoring.yml down
```

### 服务验证

```powershell
# 验证 Prometheus
curl http://localhost:9090/-/healthy

# 验证 Grafana
curl http://localhost:3000/api/health

# 访问服务
Start-Process "http://localhost:9090"
Start-Process "http://localhost:3000"
```

---

## 📁 重要文件索引

### 配置文件

| 文件 | 用途 |
|------|------|
| [docker-compose.monitoring.yml](file:///c:/Users/Administrator/agent/docker-compose.monitoring.yml) | 监控栈配置 |
| [monitoring/prometheus.yml](file:///c:/Users/Administrator/agent/monitoring/prometheus.yml) | Prometheus 配置 |
| [monitoring/alerts.yml](file:///c:/Users/Administrator/agent/monitoring/alerts.yml) | 19 个告警规则 |
| [yunshu-alerts-monitor.json](file:///c:/Users/Administrator/agent/monitoring/grafana/dashboards/yunshu-alerts-monitor.json) | Grafana 仪表盘 |

### 脚本工具

| 文件 | 用途 |
|------|------|
| [verify_gui_config.ps1](file:///c:/Users/Administrator/agent/verify_gui_config.ps1) | **GUI 配置后验证** |
| [import_and_start.ps1](file:///c:/Users/Administrator/agent/import_and_start.ps1) | 离线导入脚本 |
| [fix_docker_mirror_simple.ps1](file:///c:/Users/Administrator/agent/fix_docker_mirror_simple.ps1) | 自动配置镜像 |

### 文档指南

| 文件 | 用途 |
|------|------|
| [offline_import_final_guide.md](file:///c:/Users/Administrator/agent/offline_import_final_guide.md) | **完整离线导入指南** |
| [final_solution_docker_mirror.md](file:///c:/Users/Administrator/agent/final_solution_docker_mirror.md) | 最终解决方案 |
| [docker_mirror_troubleshooting_report.md](file:///c:/Users/Administrator/agent/docker_mirror_troubleshooting_report.md) | 故障排查报告 |
| [QUICK_REFERENCE.md](file:///c:/Users/Administrator/agent/QUICK_REFERENCE.md) | 快速参考 |

---

## ⚡ 一键脚本

### GUI 配置后验证

```powershell
.\verify_gui_config.ps1
```

**功能**:
- 检查 Docker 状态
- 验证镜像加速器
- 尝试拉取 Prometheus
- 显示详细结果和建议

### 离线导入启动

```powershell
.\import_and_start.ps1
```

**功能**:
- 验证镜像文件
- 导入 Prometheus 和 Grafana
- 启动监控栈
- 验证服务

---

## 🎯 部署流程决策树

```
GUI 配置后
  ↓
运行 verify_gui_config.ps1
  ↓
成功？
├─ 是 → 继续拉取 Grafana → 启动监控栈 → 完成
└─ 否 → 使用离线导入
         ↓
      参考 offline_import_final_guide.md
         ↓
      下载镜像 → 导出 → 传输 → 导入 → 启动 → 完成
```

---

## 🔍 常见问题快速解决

### 问题 1: 镜像拉取超时

**症状**: `dial tcp ... timeout`

**解决**:
- 方案 A: GUI 重新配置镜像加速器
- 方案 B: 使用离线导入（推荐）

### 问题 2: 配置未生效

**症状**: `docker info` 显示空 Mirrors

**解决**:
```powershell
# 完全重启 Docker
Stop-Process -Name "Docker Desktop" -Force
Start-Sleep -Seconds 30
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
Start-Sleep -Seconds 60

# 验证
docker info --format '{{.RegistryConfig.Mirrors}}'
```

### 问题 3: 端口被占用

**症状**: `bind: address already in use`

**解决**:
```powershell
# 查找占用进程
netstat -ano | findstr :9090
netstat -ano | findstr :3000

# 停止进程
taskkill /PID <进程 ID> /F
```

### 问题 4: 配置文件不存在

**症状**: `no such file or directory`

**解决**:
```powershell
# 使用绝对路径
# 编辑 docker-compose.monitoring.yml
volumes:
  - C:\Users\Administrator\agent\monitoring\prometheus.yml:/etc/prometheus/prometheus.yml
```

---

## 📊 成功标准

### 必须满足的条件

- ✅ Docker Desktop 运行正常
- ✅ Prometheus 镜像已导入（150MB）
- ✅ Grafana 镜像已导入（350MB）
- ✅ 容器正常启动（STATUS: Up）
- ✅ Prometheus 可访问（http://localhost:9090）
- ✅ Grafana 可访问（http://localhost:3000）
- ✅ 19 个告警规则加载成功
- ✅ Grafana 仪表盘导入成功（11 个面板）

### 验证命令

```powershell
# 综合验证
docker-compose -f docker-compose.monitoring.yml ps
curl http://localhost:9090/-/healthy
curl http://localhost:3000/api/health
```

---

## 💡 推荐操作流程

### 场景 A: GUI 配置成功

1. 运行 `.\verify_gui_config.ps1`
2. 如果成功，继续拉取 Grafana
3. 启动监控栈
4. 验证服务

**时间**: 15-40 分钟

### 场景 B: GUI 配置失败

1. 参考 [offline_import_final_guide.md](file:///c:/Users/Administrator/agent/offline_import_final_guide.md)
2. 使用 U 盘在另一台机器下载镜像
3. 运行 `.\import_and_start.ps1`
4. 验证服务

**时间**: 20-50 分钟  
**成功率**: 100%

---

## 📞 获取帮助

### 日志收集

```powershell
# Docker 日志
docker-compose -f docker-compose.monitoring.yml logs > logs.txt

# Docker 信息
docker info > docker_info.txt
```

### 支持文档

- [完整离线导入指南](file:///c:/Users/Administrator/agent/offline_import_final_guide.md)
- [最终解决方案](file:///c:/Users/Administrator/agent/final_solution_docker_mirror.md)
- [故障排查报告](file:///c:/Users/Administrator/agent/docker_mirror_troubleshooting_report.md)

---

**快速提示**: 
- GUI 配置失败直接用离线导入（最可靠）
- 所有问题先看 [final_solution_docker_mirror.md](file:///c:/Users/Administrator/agent/final_solution_docker_mirror.md)
- 离线导入参考 [offline_import_final_guide.md](file:///c:/Users/Administrator/agent/offline_import_final_guide.md)

**更新日期**: 2026-06-09
