# 🚀 Yunshu 监控栈部署快速参考卡片

---

## 📋 三种部署方案对比

| 方案 | 适用场景 | 时间 | 成功率 | 推荐度 |
|------|----------|------|--------|--------|
| **在线拉取** | 网络良好，可访问镜像源 | 10-30 分钟 | 60% | ⭐⭐⭐ |
| **GUI 配置** | Docker Desktop 可用 | 15-40 分钟 | 70% | ⭐⭐⭐⭐ |
| **离线导入** | 网络受限或完全无网 | 20-50 分钟 | 100% | ⭐⭐⭐⭐⭐ |

---

## 🎯 快速决策树

```
网络是否良好？
├─ 是 → 尝试在线拉取
│   └─ 失败 → GUI 配置镜像加速器
└─ 否 → 使用离线导入（推荐）
```

---

## 📁 关键文件索引

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
| [verify_and_pull.ps1](file:///c:/Users/Administrator/agent/verify_and_pull.ps1) | **验证配置 + 拉取镜像** |
| [fix_docker_mirror_simple.ps1](file:///c:/Users/Administrator/agent/fix_docker_mirror_simple.ps1) | 自动配置镜像加速器 |
| [import_offline.ps1](file:///c:/Users/Administrator/agent/import_offline.ps1) | 离线镜像导入 |

### 文档指南

| 文件 | 用途 |
|------|------|
| [QUICK_START_GUIDE.md](file:///c:/Users/Administrator/agent/QUICK_START_GUIDE.md) | 快速开始（3 种方案） |
| [offline_image_import_complete.md](file:///c:/Users/Administrator/agent/offline_image_import_complete.md) | **完整离线导入指南** |
| [docker_mirror_troubleshooting_report.md](file:///c:/Users/Administrator/agent/docker_mirror_troubleshooting_report.md) | 故障排查报告 |
| [docker_startup_troubleshooting.md](file:///c:/Users/Administrator/agent/docker_startup_troubleshooting.md) | 启动问题排查 |

---

## 🔧 快速命令参考

### Docker 配置

```powershell
# 查看 Docker 配置
docker info --format '{{json .RegistryConfig}}'

# 查看镜像加速器
docker info --format '{{.RegistryConfig.Mirrors}}'

# 查看配置文件
Get-Content $env:USERPROFILE\.docker\daemon.json
```

### 镜像操作

```powershell
# 拉取镜像
docker pull prom/prometheus:latest
docker pull grafana/grafana:latest

# 导出镜像
docker save -o prometheus.tar prom/prometheus:latest
docker save -o grafana.tar grafana/grafana:latest

# 导入镜像
docker load -i prometheus.tar
docker load -i grafana.tar

# 查看镜像
docker images | Select-String "prometheus|grafana"
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

### 验证服务

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

## ⚡ 一键脚本

### 方案 1: 验证配置并拉取

```powershell
.\verify_and_pull.ps1
```

**功能**: 
- 检查 Docker 状态
- 验证镜像加速器配置
- 尝试拉取 Prometheus 镜像
- 显示详细结果

### 方案 2: 自动配置镜像加速器

```powershell
.\fix_docker_mirror_simple.ps1
```

**功能**:
- 创建 daemon.json 配置
- 配置 4 个镜像加速器
- 自动重启 Docker Desktop
- 验证配置

### 方案 3: 离线导入

```powershell
.\import_offline.ps1
```

**功能**:
- 验证镜像文件
- 导入 Prometheus 和 Grafana
- 启动监控栈
- 验证服务

---

## 🎯 当前状态检查

### 检查清单

```powershell
# 1. Docker 是否运行？
docker version

# 2. 镜像加速器是否配置？
docker info --format '{{.RegistryConfig.Mirrors}}'

# 3. 镜像是否已下载？
docker images prom/prometheus grafana/grafana

# 4. 容器是否运行？
docker-compose -f docker-compose.monitoring.yml ps
```

### 预期输出

**Docker 运行**:
```
Client + Server information
```

**镜像加速器** (如果配置成功):
```
[docker.m.daocloud.io, docker.1panel.live, ...]
```

**镜像已下载**:
```
prom/prometheus   latest   150MB   2 weeks ago
grafana/grafana   latest   350MB   1 week ago
```

**容器运行**:
```
NAME                   STATUS          PORTS
yunshu-prometheus      Up (healthy)    0.0.0.0:9090->9090/tcp
yunshu-grafana         Up (healthy)    0.0.0.0:3000->3000/tcp
```

---

## 🔍 常见问题快速解决

### 问题 1: 镜像拉取超时

**症状**: `dial tcp ... timeout`

**解决**:
```powershell
# 方案 A: GUI 配置镜像加速器
# Docker Desktop → Settings → Docker Engine → 添加配置

# 方案 B: 使用离线导入
# 参考：offline_image_import_complete.md
```

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

# 或修改端口
# docker-compose.monitoring.yml
# ports:
#   - "9091:9090"
#   - "3001:3000"
```

### 问题 4: 配置文件不存在

**症状**: `no such file or directory`

**解决**:
```powershell
# 检查文件
Test-Path .\monitoring\prometheus.yml

# 使用绝对路径
# 编辑 docker-compose.monitoring.yml
volumes:
  - C:\Users\Administrator\agent\monitoring\prometheus.yml:/etc/prometheus/prometheus.yml
```

---

## 📊 部署成功标准

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

### 场景 A: 网络良好

1. 运行 `.\verify_and_pull.ps1`
2. 如果成功，继续拉取 Grafana
3. 启动监控栈
4. 验证服务

**时间**: 10-30 分钟

### 场景 B: 网络一般

1. 运行 `.\fix_docker_mirror_simple.ps1`
2. 重启 Docker Desktop
3. 运行 `.\verify_and_pull.ps1`
4. 如果失败，使用离线导入

**时间**: 15-40 分钟

### 场景 C: 网络受限（推荐）⭐

1. 使用 U 盘在另一台机器下载镜像
2. 运行 `.\import_offline.ps1`
3. 验证服务

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

# 网络诊断
ping registry-1.docker.io > network_test.txt
```

### 支持文档

- [完整离线导入指南](file:///c:/Users/Administrator/agent/offline_image_import_complete.md)
- [故障排查报告](file:///c:/Users/Administrator/agent/docker_mirror_troubleshooting_report.md)
- [快速开始指南](file:///c:/Users/Administrator/agent/QUICK_START_GUIDE.md)

### 外部资源

- Docker 官方文档：https://docs.docker.com
- Prometheus 文档：https://prometheus.io/docs
- Grafana 文档：https://grafana.com/docs

---

**快速提示**: 
- 网络不好直接用离线导入（最可靠）
- 所有问题先看 [docker_mirror_troubleshooting_report.md](file:///c:/Users/Administrator/agent/docker_mirror_troubleshooting_report.md)
- 离线导入参考 [offline_image_import_complete.md](file:///c:/Users/Administrator/agent/offline_image_import_complete.md)

**更新日期**: 2026-06-09
