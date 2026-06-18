# 🚀 Yunshu 监控栈快速部署指南

**目标**: 5 分钟内完成 Prometheus + Grafana 监控栈部署  
**适用环境**: Windows 10/11 + Docker Desktop

---

## 📋 三种部署方案

### 方案一：使用阿里云镜像（推荐）⭐

**适用场景**: 可以直接访问阿里云镜像仓库

**步骤**:

```powershell
# 1. 拉取镜像（使用阿里云源）
docker pull registry.cn-hangzhou.aliyuncs.com/prometheus/prometheus:v2.45.0
docker pull registry.cn-hangzhou.aliyuncs.com/grafana/grafana:10.0.0

# 2. 启动监控栈
docker-compose -f docker-compose.monitoring.aliyun.yml up -d

# 3. 验证
docker-compose -f docker-compose.monitoring.aliyun.yml ps
```

**访问**:
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000 (admin/admin123)

---

### 方案二：配置镜像加速器

**适用场景**: 阿里云镜像不可用，需要配置多个镜像源

**步骤**:

```powershell
# 1. 运行自动配置脚本
.\configure_docker_mirror.ps1

# 2. 重启 Docker Desktop（按脚本提示操作）

# 3. 拉取镜像
docker pull prom/prometheus:latest
docker pull grafana/grafana:latest

# 4. 启动监控栈
docker-compose -f docker-compose.monitoring.yml up -d
```

---

### 方案三：离线镜像导入

**适用场景**: 完全无外网连接的生产环境

**步骤**:

```powershell
# 1. 准备镜像文件（从其他机器导出）
# prometheus-v2.45.0.tar
# grafana-10.0.0.tar

# 2. 运行导入脚本
.\import_and_start.ps1

# 或者手动导入
docker load -i prometheus-v2.45.0.tar
docker load -i grafana-10.0.0.tar
docker-compose -f docker-compose.monitoring.aliyun.yml up -d
```

---

## 🎯 选择方案决策树

```
可以访问阿里云镜像？
├─ 是 → 方案一（最快）
└─ 否 → 配置镜像加速器？
    ├─ 是 → 方案二
    └─ 否 → 完全无网？
        ├─ 是 → 方案三（离线导入）
        └─ 否 → 检查网络配置
```

---

## 📁 已创建的文件

### 配置文件

| 文件 | 用途 | 方案 |
|------|------|------|
| [docker-compose.monitoring.aliyun.yml](file:///c:/Users/Administrator/agent/docker-compose.monitoring.aliyun.yml) | 使用阿里云镜像 | 方案一 |
| [docker-compose.monitoring.yml](file:///c:/Users/Administrator/agent/docker-compose.monitoring.yml) | 使用官方镜像 | 方案二 |

### 脚本文件

| 文件 | 用途 | 方案 |
|------|------|------|
| [configure_docker_mirror.ps1](file:///c:/Users/Administrator/agent/configure_docker_mirror.ps1) | 自动配置镜像加速器 | 方案二 |
| [start_monitoring.ps1](file:///c:/Users/Administrator/agent/start_monitoring.ps1) | 自动启动监控栈 | 通用 |
| [import_and_start.ps1](file:///c:/Users/Administrator/agent/import_and_start.ps1) | 离线导入并启动 | 方案三 |

### 文档文件

| 文件 | 用途 |
|------|------|
| [offline_image_import_guide.md](file:///c:/Users/Administrator/agent/offline_image_import_guide.md) | **离线导入完整指南** |
| [docker_startup_guide.md](file:///c:/Users/Administrator/agent/docker_startup_guide.md) | Docker 启动指南 |
| [deployment_summary_report.md](file:///c:/Users/Administrator/agent/deployment_summary_report.md) | 部署总结报告 |
| [local_validation_guide.md](file:///c:/Users/Administrator/agent/local_validation_guide.md) | 本地验证指南 |

---

## ✅ 部署后验证清单

### 1. 容器状态

```powershell
docker-compose -f docker-compose.monitoring.aliyun.yml ps
```

**预期**:
```
NAME                   STATUS          PORTS
yunshu-prometheus      Up (healthy)    0.0.0.0:9090->9090/tcp
yunshu-grafana         Up (healthy)    0.0.0.0:3000->3000/tcp
```

### 2. Prometheus 验证

**访问**: http://localhost:9090

**检查项**:
- [ ] 页面正常加载
- [ ] Status → Rules 显示 **19 个告警规则**
- [ ] Targets 页面显示 yunshu 为 UP

### 3. Grafana 验证

**访问**: http://localhost:3000

**检查项**:
- [ ] 可以使用 admin/admin123 登录
- [ ] Dashboards → Import 可以导入仪表盘
- [ ] 导入 `yunshu-alerts-monitor.json` 后显示 **11 个面板**
- [ ] 所有面板显示数据

---

## 🔧 常见问题快速解决

### 问题：无法拉取镜像

**解决方案**:
```powershell
# 1. 尝试阿里云镜像
docker pull registry.cn-hangzhou.aliyuncs.com/prometheus/prometheus:v2.45.0

# 2. 配置镜像加速器
.\configure_docker_mirror.ps1

# 3. 使用离线导入
# 参考：offline_image_import_guide.md
```

### 问题：端口被占用

**解决方案**:
```powershell
# 查找占用进程
netstat -ano | findstr :9090
netstat -ano | findstr :3000

# 停止进程
taskkill /PID <进程 ID> /F

# 或修改 docker-compose 文件中的端口映射
```

### 问题：容器启动失败

**解决方案**:
```powershell
# 查看日志
docker-compose -f docker-compose.monitoring.aliyun.yml logs prometheus
docker-compose -f docker-compose.monitoring.aliyun.yml logs grafana

# 重新创建容器
docker-compose -f docker-compose.monitoring.aliyun.yml down
docker-compose -f docker-compose.monitoring.aliyun.yml up -d --force-recreate
```

---

## 📊 部署成功标准

### Prometheus

- ✅ 容器状态：Up (healthy)
- ✅ 访问正常：http://localhost:9090
- ✅ 告警规则：19 个（Status → Rules）
- ✅ 监控目标：yunshu 为 UP

### Grafana

- ✅ 容器状态：Up (healthy)
- ✅ 访问正常：http://localhost:3000
- ✅ 登录成功：admin/admin123
- ✅ 仪表盘：11 个面板正常显示
- ✅ 数据源：Prometheus 连接正常

---

## 🎉 快速开始（推荐流程）

**如果你有稳定的网络环境**:

```powershell
# 步骤 1: 拉取阿里云镜像
docker pull registry.cn-hangzhou.aliyuncs.com/prometheus/prometheus:v2.45.0
docker pull registry.cn-hangzhou.aliyuncs.com/grafana/grafana:10.0.0

# 步骤 2: 启动监控栈
docker-compose -f docker-compose.monitoring.aliyun.yml up -d

# 步骤 3: 验证
docker-compose -f docker-compose.monitoring.aliyun.yml ps

# 步骤 4: 访问
Start-Process "http://localhost:9090"
Start-Process "http://localhost:3000"
```

**如果网络不稳定**:

1. 使用方案三（离线导入）
2. 参考：[offline_image_import_guide.md](file:///c:/Users/Administrator/agent/offline_image_import_guide.md)

---

## 📚 详细文档索引

### 部署方案

- [方案一：阿里云镜像](file:///c:/Users/Administrator/agent/docker-compose.monitoring.aliyun.yml) - Docker Compose 配置
- [方案二：镜像加速器](file:///c:/Users/Administrator/agent/configure_docker_mirror.ps1) - 自动配置脚本
- [方案三：离线导入](file:///c:/Users/Administrator/agent/offline_image_import_guide.md) - **完整指南**

### 验证和测试

- [告警规则验证](file:///c:/Users/Administrator/agent/verify_alert_rules.py) - Python 脚本
- [部署检查清单](file:///c:/Users/Administrator/agent/alert_deployment_checklist.md) - 详细清单
- [测试清单](file:///c:/Users/Administrator/agent/alert_rules_test_checklist.md) - 13 项测试

### 故障排查

- [Docker 启动指南](file:///c:/Users/Administrator/agent/docker_startup_guide.md) - 启动问题
- [本地验证指南](file:///c:/Users/Administrator/agent/local_validation_guide.md) - 无需 Docker 验证
- [部署总结报告](file:///c:/Users/Administrator/agent/deployment_summary_report.md) - 完整报告

---

**快速帮助**:

```powershell
# 查看当前目录下的所有相关文件
Get-ChildItem -Filter "*.yml" | Select-Object Name
Get-ChildItem -Filter "*.ps1" | Select-Object Name
Get-ChildItem -Filter "*guide*.md" | Select-Object Name
```

**推荐起点**: 从 [offline_image_import_guide.md](file:///c:/Users/Administrator/agent/offline_image_import_guide.md) 开始，选择适合你的方案！
