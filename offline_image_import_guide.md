# 📦 Docker 离线镜像导入和验证完整指南

**适用场景**: 
- Docker Hub 和镜像加速器都无法访问
- 生产环境无外网连接
- 需要快速部署多个相同环境

---

## 📋 目录

1. [准备工作](#准备工作)
2. [方法一：从其他机器导出镜像](#方法一从其他机器导出镜像)
3. [方法二：手动下载镜像文件](#方法二手动下载镜像文件)
4. [导入镜像到 Docker](#导入镜像到-docker)
5. [启动监控栈](#启动监控栈)
6. [验证服务](#验证服务)
7. [故障排查](#故障排查)

---

## 准备工作

### 系统要求

- Windows 10/11 专业版或企业版
- Docker Desktop 29.4.3 或更高版本
- PowerShell 7.0 或更高版本
- 至少 2GB 可用磁盘空间

### 检查 Docker 状态

```powershell
# 检查 Docker 是否运行
docker version

# 检查 Docker 信息
docker info
```

**预期输出**:
```
Client:
 Version:           29.4.3
 ...

Server:
 Engine:
  Version:          29.4.3
  ...
```

---

## 方法一：从其他机器导出镜像

### 步骤 1: 在有网络的机器上下载镜像

```bash
# 1. 拉取 Prometheus 镜像
docker pull registry.cn-hangzhou.aliyuncs.com/prometheus/prometheus:v2.45.0

# 2. 拉取 Grafana 镜像
docker pull registry.cn-hangzhou.aliyuncs.com/grafana/grafana:10.0.0

# 3. 验证镜像
docker images | Select-String "prometheus|grafana"
```

**预期输出**:
```
registry.cn-hangzhou.aliyuncs.com/prometheus/prometheus   v2.45.0   abc123   150MB
registry.cn-hangzhou.aliyuncs.com/grafana/grafana         10.0.0    def456   350MB
```

### 步骤 2: 导出镜像为 tar 文件

```bash
# 导出 Prometheus 镜像
docker save -o prometheus-v2.45.0.tar registry.cn-hangzhou.aliyuncs.com/prometheus/prometheus:v2.45.0

# 导出 Grafana 镜像
docker save -o grafana-10.0.0.tar registry.cn-hangzhou.aliyuncs.com/grafana/grafana:10.0.0

# 验证文件大小
ls -lh *.tar
```

**预期输出**:
```
prometheus-v2.45.0.tar   150MB
grafana-10.0.0.tar       350MB
```

### 步骤 3: 传输镜像文件到目标机器

**方式 1: 使用 U 盘**
```bash
# 复制文件到 U 盘
copy prometheus-v2.45.0.tar X:\
copy grafana-10.0.0.tar X:\
```

**方式 2: 使用网络传输**
```bash
# 使用 scp (Linux/Mac)
scp prometheus-v2.45.0.tar user@target-machine:/path/
scp grafana-10.0.0.tar user@target-machine:/path/

# 使用 PowerShell (Windows)
# 在目标机器上创建共享文件夹，然后复制
```

**方式 3: 使用压缩工具**
```bash
# 压缩镜像文件
7z a monitoring-images.7z prometheus-v2.45.0.tar grafana-10.0.0.tar

# 通过邮件、网盘等方式传输
```

---

## 方法二：手动下载镜像文件

### 从镜像仓库直接下载

#### 1. 访问阿里云镜像仓库

**Prometheus**:
- 地址：https://cr.console.aliyun.com/cn-hangzhou/instances/images
- 搜索：prometheus/prometheus
- 选择版本：v2.45.0

**Grafana**:
- 地址：https://cr.console.aliyun.com/cn-hangzhou/instances/images
- 搜索：grafana/grafana
- 选择版本：10.0.0

#### 2. 使用 wget 或 curl 下载

```bash
# Prometheus v2.45.0
wget https://registry.cn-hangzhou.aliyuncs.com/prometheus/prometheus/manifests/v2.45.0 -O prometheus-manifest.json

# Grafana 10.0.0
wget https://registry.cn-hangzhou.aliyuncs.com/grafana/grafana/manifests/10.0.0 -O grafana-manifest.json
```

**注意**: 直接下载镜像层比较复杂，建议使用方法一。

---

## 导入镜像到 Docker

### 步骤 1: 验证镜像文件

```powershell
# 检查文件是否存在
Test-Path prometheus-v2.45.0.tar
Test-Path grafana-10.0.0.tar

# 查看文件大小
Get-ChildItem *.tar | Select-Object Name, Length
```

**预期输出**:
```
Name                      Length
----                      ------
prometheus-v2.45.0.tar    157286400
grafana-10.0.0.tar        367001600
```

### 步骤 2: 导入 Prometheus 镜像

```powershell
# 导入镜像
docker load -i prometheus-v2.45.0.tar

# 预期输出:
# Loaded image: registry.cn-hangzhou.aliyuncs.com/prometheus/prometheus:v2.45.0
```

### 步骤 3: 导入 Grafana 镜像

```powershell
# 导入镜像
docker load -i grafana-10.0.0.tar

# 预期输出:
# Loaded image: registry.cn-hangzhou.aliyuncs.com/grafana/grafana:10.0.0
```

### 步骤 4: 验证导入的镜像

```powershell
# 列出所有镜像
docker images

# 或者使用筛选
docker images | Select-String "prometheus|grafana"
```

**预期输出**:
```
REPOSITORY                                           TAG       IMAGE ID   CREATED   SIZE
registry.cn-hangzhou.aliyuncs.com/prometheus/prometheus   v2.45.0   abc123   1 week    150MB
registry.cn-hangzhou.aliyuncs.com/grafana/grafana         10.0.0    def456   2 weeks   350MB
```

### 步骤 5: 标记镜像（可选）

如果需要修改为官方镜像名称：

```powershell
# 标记 Prometheus 镜像
docker tag registry.cn-hangzhou.aliyuncs.com/prometheus/prometheus:v2.45.0 prom/prometheus:latest

# 标记 Grafana 镜像
docker tag registry.cn-hangzhou.aliyuncs.com/grafana/grafana:10.0.0 grafana/grafana:latest

# 验证
docker images | Select-String "prometheus|grafana"
```

---

## 启动监控栈

### 方式 1: 使用阿里云镜像配置

```powershell
# 使用已导入的阿里云镜像启动
docker-compose -f docker-compose.monitoring.aliyun.yml up -d
```

### 方式 2: 使用官方镜像名称

如果使用 `docker tag` 修改了镜像名称，使用原始配置文件：

```powershell
# 使用官方镜像名称启动
docker-compose -f docker-compose.monitoring.yml up -d
```

### 方式 3: 手动启动容器

```powershell
# 1. 创建网络
docker network create yunshu-monitoring

# 2. 启动 Prometheus
docker run -d `
  --name yunshu-prometheus `
  -p 9090:9090 `
  -v "$PWD\monitoring\prometheus.yml:/etc/prometheus/prometheus.yml" `
  -v "$PWD\monitoring\alerts.yml:/etc/prometheus/alerts.yml" `
  -v prometheus_data:/prometheus `
  --network yunshu-monitoring `
  registry.cn-hangzhou.aliyuncs.com/prometheus/prometheus:v2.45.0 `
  --config.file=/etc/prometheus/prometheus.yml `
  --storage.tsdb.path=/prometheus `
  --web.enable-lifecycle

# 3. 启动 Grafana
docker run -d `
  --name yunshu-grafana `
  -p 3000:3000 `
  -e GF_SECURITY_ADMIN_USER=admin `
  -e GF_SECURITY_ADMIN_PASSWORD=admin123 `
  -v grafana_data:/var/lib/grafana `
  -v "$PWD\monitoring\grafana\datasources:/etc/grafana/provisioning/datasources" `
  -v "$PWD\monitoring\grafana\dashboards:/etc/grafana/provisioning/dashboards" `
  --network yunshu-monitoring `
  --depends-on yunshu-prometheus `
  registry.cn-hangzhou.aliyuncs.com/grafana/grafana:10.0.0

# 4. 查看容器状态
docker ps
```

---

## 验证服务

### 1. 验证容器运行

```powershell
# 查看容器状态
docker-compose -f docker-compose.monitoring.aliyun.yml ps

# 或者
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

**预期输出**:
```
NAMES                  STATUS          PORTS
yunshu-prometheus      Up (healthy)    0.0.0.0:9090->9090/tcp
yunshu-grafana         Up (healthy)    0.0.0.0:3000->3000/tcp
```

### 2. 验证 Prometheus

```powershell
# 访问 Prometheus UI
Start-Process "http://localhost:9090"

# 使用 curl 验证
curl http://localhost:9090/-/healthy
```

**预期输出**:
```
Prometheus Server is Healthy.
```

### 3. 验证告警规则加载

在浏览器中访问 http://localhost:9090，然后：

1. 点击 **Status** 菜单
2. 选择 **Rules**
3. 确认显示 **19 个告警规则**

或者使用 API 验证：

```powershell
# 获取所有告警规则
curl http://localhost:9090/api/v1/rules | ConvertFrom-Json | 
  Select-Object -ExpandProperty data | 
  Select-Object -ExpandProperty groups | 
  ForEach-Object { $_.rules.Count } | 
  Measure-Object -Sum | 
  Select-Object -ExpandProperty Sum
```

**预期输出**:
```
19
```

### 4. 验证 Grafana

```powershell
# 访问 Grafana UI
Start-Process "http://localhost:3000"

# 使用 curl 验证
curl http://localhost:3000/api/health
```

**预期输出**:
```json
{
  "commit": "abc123",
  "database": "ok",
  "version": "10.0.0"
}
```

### 5. 登录 Grafana

1. 访问：http://localhost:3000
2. 用户名：`admin`
3. 密码：`admin123`

### 6. 导入仪表盘

1. 点击左侧菜单 **Dashboards**
2. 选择 **Import**
3. 上传文件：`monitoring/grafana/dashboards/yunshu-alerts-monitor.json`
4. 选择数据源：**Prometheus**
5. 点击 **Import**

### 7. 验证仪表盘面板

确认显示以下 11 个面板：

1. ✅ Warning 告警数
2. ✅ Critical 告警数
3. ✅ Emergency 告警数
4. ✅ 活跃告警趋势
5. ✅ 错误率
6. ✅ 95 分位延迟
7. ✅ 安全拦截速率
8. ✅ CPU 使用率
9. ✅ 内存使用率
10. ✅ 对话异常率
11. ✅ 服务可用性

---

## 故障排查

### 问题 1: 镜像导入失败

**错误**:
```
open prometheus-v2.45.0.tar: no such file or directory
```

**解决方案**:
```powershell
# 确认文件路径
Get-ChildItem *.tar

# 使用绝对路径
docker load -i "C:\Users\Administrator\agent\prometheus-v2.45.0.tar"
```

---

### 问题 2: 容器启动失败

**错误**:
```
Error starting userland proxy: listen tcp4 0.0.0.0:9090: bind: address already in use
```

**解决方案**:
```powershell
# 查找占用端口的进程
netstat -ano | findstr :9090

# 停止占用进程
taskkill /PID <进程 ID> /F

# 或者修改端口映射
# 编辑 docker-compose.monitoring.aliyun.yml
# 将 9090:9090 改为 9091:9090
```

---

### 问题 3: Prometheus 无法加载告警规则

**错误日志**:
```
error loading rules, previous rule set retained.  open /etc/prometheus/alerts.yml: no such file or directory
```

**解决方案**:
```powershell
# 1. 检查文件是否存在
Test-Path monitoring\alerts.yml

# 2. 检查路径映射
docker-compose -f docker-compose.monitoring.aliyun.yml config

# 3. 使用绝对路径
# 编辑 docker-compose.monitoring.aliyun.yml
# volumes:
#   - C:\Users\Administrator\agent\monitoring\alerts.yml:/etc/prometheus/alerts.yml
```

---

### 问题 4: Grafana 无法连接 Prometheus

**症状**:
- 仪表盘显示 "No data"
- 数据源测试失败

**解决方案**:
```powershell
# 1. 验证 Prometheus 运行
docker ps | Select-String prometheus

# 2. 测试 Prometheus API
curl http://localhost:9090/api/v1/query?query=up

# 3. 检查 Grafana 数据源配置
# 访问 http://localhost:3000
# Configuration → Data sources → Prometheus
# URL 应该是：http://prometheus:9090

# 4. 重启 Grafana
docker-compose -f docker-compose.monitoring.aliyun.yml restart grafana
```

---

### 问题 5: 容器健康检查失败

**错误**:
```
healthcheck: command failed
```

**解决方案**:
```powershell
# 1. 查看容器日志
docker-compose -f docker-compose.monitoring.aliyun.yml logs prometheus

# 2. 手动执行健康检查
docker exec yunshu-prometheus wget -q --spider http://localhost:9090/-/healthy

# 3. 如果 wget 不可用，使用 curl
docker exec yunshu-prometheus curl -s http://localhost:9090/-/healthy

# 4. 临时禁用健康检查
# 编辑 docker-compose.monitoring.aliyun.yml
# 注释掉 healthcheck 部分
```

---

## 自动化脚本

### 一键导入和启动脚本

创建 `import_and_start.ps1`:

```powershell
# Yunshu 监控栈 - 离线镜像导入和启动脚本

$ErrorActionPreference = "Stop"

Write-Host "`n📦 Yunshu 监控栈离线部署工具" -ForegroundColor Cyan
Write-Host ""

# 1. 验证镜像文件
Write-Host "[1/5] 验证镜像文件..." -ForegroundColor Yellow
$prometheusTar = "prometheus-v2.45.0.tar"
$grafanaTar = "grafana-10.0.0.tar"

if (-not (Test-Path $prometheusTar)) {
    Write-Host "   ❌ 找不到 $prometheusTar" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $grafanaTar)) {
    Write-Host "   ❌ 找不到 $grafanaTar" -ForegroundColor Red
    exit 1
}
Write-Host "   ✅ 镜像文件存在" -ForegroundColor Green

# 2. 导入镜像
Write-Host "`n[2/5] 导入 Prometheus 镜像..." -ForegroundColor Yellow
docker load -i $prometheusTar
Write-Host "   ✅ Prometheus 镜像导入成功" -ForegroundColor Green

Write-Host "`n[3/5] 导入 Grafana 镜像..." -ForegroundColor Yellow
docker load -i $grafanaTar
Write-Host "   ✅ Grafana 镜像导入成功" -ForegroundColor Green

# 3. 验证镜像
Write-Host "`n[4/5] 验证导入的镜像..." -ForegroundColor Yellow
$images = docker images --format "{{.Repository}}:{{.Tag}}"
if ($images -match "prometheus" -and $images -match "grafana") {
    Write-Host "   ✅ 镜像验证通过" -ForegroundColor Green
} else {
    Write-Host "   ❌ 镜像验证失败" -ForegroundColor Red
    exit 1
}

# 4. 启动监控栈
Write-Host "`n[5/5] 启动监控栈..." -ForegroundColor Yellow
docker-compose -f docker-compose.monitoring.aliyun.yml up -d

# 5. 验证服务
Write-Host "`n⏳ 等待服务启动..." -ForegroundColor Cyan
Start-Sleep -Seconds 15

$containers = docker-compose -f docker-compose.monitoring.aliyun.yml ps --format "{{.State}}"
if ($containers -match "running") {
    Write-Host "   ✅ 容器启动成功" -ForegroundColor Green
} else {
    Write-Host "   ⚠️  容器状态异常" -ForegroundColor Yellow
}

# 显示访问信息
Write-Host "`n🎉 部署完成!" -ForegroundColor Cyan
Write-Host "`n📊 访问地址:" -ForegroundColor Yellow
Write-Host "   Prometheus: http://localhost:9090" -ForegroundColor Cyan
Write-Host "   Grafana: http://localhost:3000 (admin/admin123)" -ForegroundColor Cyan
Write-Host ""
```

**使用方法**:
```powershell
.\import_and_start.ps1
```

---

## 清理和卸载

### 停止服务

```powershell
# 停止容器
docker-compose -f docker-compose.monitoring.aliyun.yml down
```

### 删除容器

```powershell
# 删除容器和网络
docker-compose -f docker-compose.monitoring.aliyun.yml down -v
```

### 删除镜像

```powershell
# 删除 Prometheus 镜像
docker rmi registry.cn-hangzhou.aliyuncs.com/prometheus/prometheus:v2.45.0 -f

# 删除 Grafana 镜像
docker rmi registry.cn-hangzhou.aliyuncs.com/grafana/grafana:10.0.0 -f
```

### 清理所有数据

```powershell
# 删除所有 Yunshu 相关容器、网络、卷
docker rm -f $(docker ps -a --filter "name=yunshu*" --format "{{.ID}}")
docker volume rm $(docker volume ls --filter "name=prometheus_data" --format "{{.Name}}")
docker volume rm $(docker volume ls --filter "name=grafana_data" --format "{{.Name}}")
docker network rm yunshu-monitoring
```

---

## 总结

### 完整流程

1. ✅ 准备镜像文件（导出或下载）
2. ✅ 导入镜像到 Docker
3. ✅ 验证镜像导入成功
4. ✅ 启动监控栈
5. ✅ 验证服务运行
6. ✅ 导入 Grafana 仪表盘
7. ✅ 验证所有功能正常

### 常见问题

- **镜像文件损坏**: 重新导出或下载
- **端口冲突**: 修改端口映射
- **路径问题**: 使用绝对路径
- **权限问题**: 以管理员身份运行 PowerShell

### 参考文档

- [Docker 镜像加速器配置](file:///c:/Users/Administrator/agent/configure_docker_mirror.ps1)
- [Docker Compose 配置](file:///c:/Users/Administrator/agent/docker-compose.monitoring.aliyun.yml)
- [部署总结报告](file:///c:/Users/Administrator/agent/deployment_summary_report.md)

---

**文档版本**: 1.0  
**更新日期**: 2026-06-09  
**适用环境**: Windows 10/11 + Docker Desktop
