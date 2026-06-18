# 📦 Docker 离线镜像导入完整指南（最终备选方案）

**适用场景**: 
- ✅ 所有在线拉取方案都失败
- ✅ 生产环境无外网连接
- ✅ 需要快速部署多个相同环境
- ✅ 网络条件极差

**预计时间**: 10-30 分钟（取决于网络传输速度）

---

## 📋 目录

1. [方案概述](#方案概述)
2. [准备工作](#准备工作)
3. [方法一：U 盘传输](#方法一 u 盘传输)
4. [方法二：网络共享](#方法二网络共享)
5. [方法三：直接下载镜像文件](#方法三直接下载镜像文件)
6. [导入镜像](#导入镜像)
7. [验证和启动](#验证和启动)
8. [故障排查](#故障排查)

---

## 方案概述

### 三种离线导入方法对比

| 方法 | 适用场景 | 速度 | 难度 | 推荐度 |
|------|----------|------|------|--------|
| **U 盘传输** | 有另一台可上网的电脑 | ⭐⭐⭐⭐⭐ | ⭐ | ⭐⭐⭐⭐⭐ |
| **网络共享** | 同一局域网内有可上网电脑 | ⭐⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐⭐ |
| **直接下载** | 可以访问第三方镜像站 | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ |

### 所需资源

**镜像文件大小**:
- Prometheus: ~150MB
- Grafana: ~350MB
- **总计**: ~500MB

**存储介质**:
- U 盘（至少 1GB）
- 或网络共享文件夹
- 或本地磁盘空间

---

## 准备工作

### 步骤 1: 确认目标机器信息

**在目标机器上执行**（当前机器）:

```powershell
# 检查 Docker 版本
docker version

# 检查 Docker 架构
docker info --format '{{.Architecture}}'

# 检查操作系统
systeminfo | Select-String "OS Name","OS Version"
```

**预期输出**:
```
Docker Version: 29.4.3
Architecture: x86_64
OS: Windows 10/11 Professional
```

### 步骤 2: 准备存储介质

**U 盘准备**:
1. 准备一个至少 1GB 的 U 盘
2. 格式化为 NTFS 或 FAT32
3. 确保有足够可用空间

**网络共享准备**:
1. 确保局域网内有可上网的电脑
2. 创建共享文件夹
3. 确保目标机器可以访问共享文件夹

---

## 方法一：U 盘传输（推荐）⭐

### 步骤 1: 在可上网的机器上下载镜像

**在另一台可以正常访问 Docker Hub 的电脑上执行**:

```bash
# 1. 拉取 Prometheus 镜像
docker pull prom/prometheus:latest

# 2. 拉取 Grafana 镜像
docker pull grafana/grafana:latest

# 3. 验证镜像
docker images | Select-String "prometheus|grafana"
```

**预期输出**:
```
prom/prometheus               latest    abc123def456   2 weeks   150MB
grafana/grafana               latest    ghi789jkl012   1 week    350MB
```

### 步骤 2: 导出镜像为 tar 文件

```bash
# 1. 导出 Prometheus 镜像
docker save -o prometheus.tar prom/prometheus:latest

# 2. 导出 Grafana 镜像
docker save -o grafana.tar grafana/grafana:latest

# 3. 验证文件大小
ls -lh *.tar
```

**预期输出**:
```
prometheus.tar   150M
grafana.tar      350M
```

**可选：压缩文件**（节省空间）:
```bash
# 使用 7-Zip 压缩
7z a monitoring-images.7z prometheus.tar grafana.tar

# 或使用 zip
Compress-Archive -Path prometheus.tar,grafana.tar -DestinationPath monitoring-images.zip
```

### 步骤 3: 复制到 U 盘

```bash
# Windows 资源管理器操作
# 1. 插入 U 盘
# 2. 复制 prometheus.tar 和 grafana.tar 到 U 盘
# 3. 安全移除 U 盘
```

**或使用 PowerShell**:
```powershell
# 假设 U 盘盘符为 E:
Copy-Item prometheus.tar E:\
Copy-Item grafana.tar E:\

# 验证复制
Get-ChildItem E:\*.tar
```

### 步骤 4: 在目标机器上导入镜像

**在目标机器上执行**:

```powershell
# 1. 从 U 盘复制文件到本地
$usbDrive = "E:"  # 根据实际盘符修改
$destPath = "C:\Users\Administrator\agent"

Copy-Item "$usbDrive\prometheus.tar" "$destPath\"
Copy-Item "$usbDrive\grafana.tar" "$destPath\"

# 2. 验证文件
Get-ChildItem "$destPath\*.tar" | Select-Object Name, Length
```

**预期输出**:
```
Name              Length
----              ------
prometheus.tar    157286400
grafana.tar       367001600
```

---

## 方法二：网络共享

### 步骤 1: 在可上网的机器上创建共享

**在可上网的电脑上执行**:

```powershell
# 1. 创建共享文件夹
$sharePath = "C:\DockerImages"
New-Item -ItemType Directory -Path $sharePath -Force

# 2. 下载并导出镜像
docker pull prom/prometheus:latest
docker pull grafana/grafana:latest
docker save -o "$sharePath\prometheus.tar" prom/prometheus:latest
docker save -o "$sharePath\grafana.tar" grafana/grafana:latest

# 3. 设置共享
$shareName = "DockerImages"
New-SmbShare -Name $shareName -Path $sharePath -FullAccess "Everyone"

# 4. 查看共享信息
Get-SmbShare | Where-Object Name -eq $shareName
```

**预期输出**:
```
ShareName  : DockerImages
Path       : C:\DockerImages
Description:
```

### 步骤 2: 在目标机器上访问共享

**在目标机器上执行**:

```powershell
# 1. 查看网络共享
# 假设可上网电脑的 IP 为 192.168.1.100
$serverIP = "192.168.1.100"
$shareName = "DockerImages"

# 2. 映射网络驱动器
New-PSDrive -Name "Z" -PSProvider FileSystem -Root "\\$serverIP\$shareName"

# 3. 复制文件
$destPath = "C:\Users\Administrator\agent"
Copy-Item "Z:\prometheus.tar" "$destPath\"
Copy-Item "Z:\grafana.tar" "$destPath\"

# 4. 验证
Get-ChildItem "$destPath\*.tar"
```

---

## 方法三：直接下载镜像文件

### 从第三方镜像站下载

**适用**: 可以直接访问第三方镜像仓库

#### 选项 1: DaoCloud 镜像站

```powershell
# 1. 创建下载目录
$downloadPath = "C:\DockerImages"
New-Item -ItemType Directory -Path $downloadPath -Force

# 2. 下载 Prometheus 镜像（使用 wget 或 Invoke-WebRequest）
$url = "https://docker.m.daocloud.io/v2/prom/prometheus/manifests/latest"
# 注意：直接下载镜像层比较复杂，建议使用 docker pull + docker save 方式
```

**注意**: 直接下载镜像文件比较复杂，推荐使用方法一或方法二。

#### 选项 2: 使用镜像加速工具

有些第三方工具可以直接下载镜像并导出，例如：

- **Docker Image Puller** (第三方工具)
- **Image Export/Import Tools**

**使用步骤**:
1. 下载工具
2. 指定要下载的镜像
3. 自动下载并导出为 tar 文件
4. 传输到目标机器

---

## 导入镜像

### 步骤 1: 验证镜像文件

**在目标机器上执行**:

```powershell
# 1. 检查文件是否存在
$prometheusTar = "C:\Users\Administrator\agent\prometheus.tar"
$grafanaTar = "C:\Users\Administrator\agent\grafana.tar"

Test-Path $prometheusTar
Test-Path $grafanaTar

# 2. 查看文件大小
Get-ChildItem $prometheusTar, $grafanaTar | 
    Select-Object Name, @{Name="Size(MB)";Expression={[math]::Round($_.Length/1MB, 2)}}
```

**预期输出**:
```
Name              Size(MB)
----              --------
prometheus.tar    150.00
grafana.tar       350.00
```

### 步骤 2: 导入 Prometheus 镜像

```powershell
Write-Host "Importing Prometheus image..." -ForegroundColor Cyan
docker load -i $prometheusTar

# 预期输出:
# Loaded image: prom/prometheus:latest
```

### 步骤 3: 导入 Grafana 镜像

```powershell
Write-Host "Importing Grafana image..." -ForegroundColor Cyan
docker load -i $grafanaTar

# 预期输出:
# Loaded image: grafana/grafana:latest
```

### 步骤 4: 验证导入的镜像

```powershell
# 1. 列出所有镜像
docker images

# 2. 筛选 Yunshu 相关镜像
docker images | Select-String "prometheus|grafana"

# 3. 或使用格式化输出
docker images prom/prometheus grafana/grafana --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.CreatedSince}}"
```

**预期输出**:
```
REPOSITORY          TAG       SIZE      CREATED
prom/prometheus     latest    150MB     2 weeks ago
grafana/grafana     latest    350MB     1 week ago
```

### 步骤 5: 标记镜像（可选）

如果需要指定版本:

```powershell
# 标记为特定版本
docker tag prom/prometheus:latest prom/prometheus:v2.45.0
docker tag grafana/grafana:latest grafana/grafana:10.0.0

# 验证
docker images | Select-String "prometheus|grafana"
```

---

## 验证和启动

### 步骤 1: 验证 Docker 配置

```powershell
# 1. 检查 Docker 状态
docker version
docker info

# 2. 验证镜像
docker images prom/prometheus grafana/grafana
```

### 步骤 2: 启动监控栈

```powershell
# 1. 启动容器
docker-compose -f docker-compose.monitoring.yml up -d

# 2. 查看状态
docker-compose -f docker-compose.monitoring.yml ps

# 3. 查看日志
docker-compose -f docker-compose.monitoring.yml logs -f
```

**预期输出**:
```
NAME                   STATUS          PORTS
yunshu-prometheus      Up (healthy)    0.0.0.0:9090->9090/tcp
yunshu-grafana         Up (healthy)    0.0.0.0:3000->3000/tcp
```

### 步骤 3: 验证服务

```powershell
# 1. 验证 Prometheus
curl http://localhost:9090/-/healthy

# 2. 验证 Grafana
curl http://localhost:3000/api/health

# 3. 或使用浏览器访问
Start-Process "http://localhost:9090"
Start-Process "http://localhost:3000"
```

### 步骤 4: 导入 Grafana 仪表盘

**在浏览器中操作**:

1. 访问 Grafana: http://localhost:3000
2. 登录：admin / admin123
3. 点击 Dashboards → Import
4. 上传文件：`monitoring/grafana/dashboards/yunshu-alerts-monitor.json`
5. 选择 Prometheus 数据源
6. 点击 Import

### 步骤 5: 验证告警规则

**在浏览器中操作**:

1. 访问 Prometheus: http://localhost:9090
2. 点击 Status → Rules
3. 确认显示 **19 个告警规则**

---

## 故障排查

### 问题 1: 镜像导入失败

**错误**:
```
open prometheus.tar: no such file or directory
```

**解决方案**:
```powershell
# 1. 确认文件路径
Get-ChildItem *.tar

# 2. 使用绝对路径
docker load -i "C:\Users\Administrator\agent\prometheus.tar"

# 3. 检查文件完整性
$fileSize = (Get-Item "C:\Users\Administrator\agent\prometheus.tar").Length
Write-Host "File size: $([math]::Round($fileSize/1MB, 2)) MB"

# 正常大小应该在 150MB 左右
if ($fileSize -lt 100MB) {
    Write-Host "WARNING: File may be corrupted!" -ForegroundColor Red
}
```

### 问题 2: 镜像版本不匹配

**错误**:
```
manifest for prom/prometheus:latest not found: manifest unknown
```

**解决方案**:
```powershell
# 1. 查看实际导入的镜像标签
docker images | Select-String "prometheus"

# 2. 如果标签不同，使用实际标签启动
# 编辑 docker-compose.monitoring.yml
# image: prom/prometheus:<实际标签>

# 3. 或重新标记镜像
docker tag prom/prometheus:<实际标签> prom/prometheus:latest
```

### 问题 3: 容器启动失败

**错误**:
```
Error starting userland proxy: listen tcp4 0.0.0.0:9090: bind: address already in use
```

**解决方案**:
```powershell
# 1. 查找占用端口的进程
netstat -ano | findstr :9090
netstat -ano | findstr :3000

# 2. 停止进程
taskkill /PID <进程 ID> /F

# 3. 或修改端口映射
# 编辑 docker-compose.monitoring.yml
# ports:
#   - "9091:9090"  # 改为 9091
#   - "3001:3000"  # 改为 3001
```

### 问题 4: 配置文件路径错误

**错误**:
```
error loading config file: open /etc/prometheus/prometheus.yml: no such file or directory
```

**解决方案**:
```powershell
# 1. 检查配置文件是否存在
Test-Path .\monitoring\prometheus.yml
Test-Path .\monitoring\alerts.yml

# 2. 使用绝对路径
# 编辑 docker-compose.monitoring.yml
volumes:
  - C:\Users\Administrator\agent\monitoring\prometheus.yml:/etc/prometheus/prometheus.yml
  - C:\Users\Administrator\agent\monitoring\alerts.yml:/etc/prometheus/alerts.yml

# 3. 或在 PowerShell 中使用 ${PWD}
volumes:
  - ${PWD}/monitoring/prometheus.yml:/etc/prometheus/prometheus.yml
```

### 问题 5: 镜像文件损坏

**症状**:
- 导入时出错
- 导入后容器无法启动

**解决方案**:
```powershell
# 1. 检查文件哈希值（如果有源文件的哈希）
Get-FileHash prometheus.tar -Algorithm SHA256

# 2. 重新传输文件
# 从 U 盘或网络共享重新复制

# 3. 验证文件大小
Get-ChildItem *.tar | Select-Object Name, Length

# Prometheus 应该在 150MB 左右
# Grafana 应该在 350MB 左右
```

---

## 自动化脚本

### 一键导入和启动脚本

创建 `import_offline.ps1`:

```powershell
# Yunshu 监控栈 - 离线镜像导入脚本

$ErrorActionPreference = "Stop"

Write-Host "`n=== Yunshu Offline Image Import ===" -ForegroundColor Cyan
Write-Host ""

# 配置
$prometheusTar = "prometheus.tar"
$grafanaTar = "grafana.tar"
$workDir = "C:\Users\Administrator\agent"

Set-Location $workDir

# Step 1: Verify files
Write-Host "[1/5] Verifying image files..." -ForegroundColor Yellow
if (-not (Test-Path $prometheusTar)) {
    Write-Host "   ERROR: $prometheusTar not found!" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $grafanaTar)) {
    Write-Host "   ERROR: $grafanaTar not found!" -ForegroundColor Red
    exit 1
}
Write-Host "   Files found" -ForegroundColor Green

# Step 2: Import Prometheus
Write-Host "`n[2/5] Importing Prometheus image..." -ForegroundColor Yellow
docker load -i $prometheusTar
if ($LASTEXITCODE -ne 0) {
    Write-Host "   ERROR: Import failed!" -ForegroundColor Red
    exit 1
}
Write-Host "   OK" -ForegroundColor Green

# Step 3: Import Grafana
Write-Host "`n[3/5] Importing Grafana image..." -ForegroundColor Yellow
docker load -i $grafanaTar
if ($LASTEXITCODE -ne 0) {
    Write-Host "   ERROR: Import failed!" -ForegroundColor Red
    exit 1
}
Write-Host "   OK" -ForegroundColor Green

# Step 4: Verify images
Write-Host "`n[4/5] Verifying imported images..." -ForegroundColor Yellow
$images = docker images --format "{{.Repository}}:{{.Tag}}"
if ($images -match "prom/prometheus" -and $images -match "grafana/grafana") {
    Write-Host "   Images verified" -ForegroundColor Green
} else {
    Write-Host "   ERROR: Images not found!" -ForegroundColor Red
    exit 1
}

# Step 5: Start stack
Write-Host "`n[5/5] Starting monitoring stack..." -ForegroundColor Yellow
docker-compose -f docker-compose.monitoring.yml up -d

# Wait for startup
Write-Host "   Waiting for services to start..." -ForegroundColor Cyan
Start-Sleep -Seconds 15

# Verify
$containers = docker-compose -f docker-compose.monitoring.yml ps --format "{{.State}}"
if ($containers -match "running") {
    Write-Host "   Stack started successfully!" -ForegroundColor Green
} else {
    Write-Host "   WARNING: Containers may not be running" -ForegroundColor Yellow
}

# Summary
Write-Host "`n=== Summary ===" -ForegroundColor Cyan
Write-Host "Prometheus: http://localhost:9090" -ForegroundColor Cyan
Write-Host "Grafana: http://localhost:3000 (admin/admin123)" -ForegroundColor Cyan
Write-Host ""
```

**使用方法**:
```powershell
.\import_offline.ps1
```

---

## 清理和卸载

### 停止服务

```powershell
# 停止容器
docker-compose -f docker-compose.monitoring.yml down
```

### 删除容器

```powershell
# 删除容器和网络
docker-compose -f docker-compose.monitoring.yml down -v
```

### 删除镜像

```powershell
# 删除 Prometheus 镜像
docker rmi prom/prometheus:latest -f

# 删除 Grafana 镜像
docker rmi grafana/grafana:latest -f
```

### 清理所有数据

```powershell
# 删除所有相关容器
docker rm -f $(docker ps -a --filter "name=yunshu*" --format "{{.ID}}")

# 删除卷
docker volume rm $(docker volume ls --filter "name=prometheus_data" --format "{{.Name}}")
docker volume rm $(docker volume ls --filter "name=grafana_data" --format "{{.Name}}")

# 删除网络
docker network rm yunshu-monitoring
```

---

## 总结

### 完整流程

1. ✅ 准备存储介质（U 盘或网络共享）
2. ✅ 在可上网机器上下载并导出镜像
3. ✅ 传输到目标机器
4. ✅ 导入镜像到 Docker
5. ✅ 验证镜像导入成功
6. ✅ 启动监控栈
7. ✅ 验证所有服务正常

### 时间估算

| 步骤 | 预计时间 |
|------|----------|
| 下载镜像 | 10-30 分钟 |
| 导出镜像 | 2-5 分钟 |
| 传输文件 | 1-5 分钟 |
| 导入镜像 | 2-5 分钟 |
| 启动验证 | 5 分钟 |
| **总计** | **20-50 分钟** |

### 成功标准

- ✅ 镜像文件完整（Prometheus ~150MB, Grafana ~350MB）
- ✅ Docker 成功导入镜像
- ✅ 容器正常启动
- ✅ Prometheus 可访问（http://localhost:9090）
- ✅ Grafana 可访问（http://localhost:3000）
- ✅ 19 个告警规则加载成功
- ✅ Grafana 仪表盘导入成功

---

## 参考文档

- [Docker 官方文档](https://docs.docker.com/engine/reference/commandline/save/)
- [Docker 镜像导入导出](https://docs.docker.com/get-started/07_multi_container/)
- [Yunshu 部署指南](file:///c:/Users/Administrator/agent/QUICK_START_GUIDE.md)
- [故障排查报告](file:///c:/Users/Administrator/agent/docker_mirror_troubleshooting_report.md)

---

**文档版本**: 1.0  
**更新日期**: 2026-06-09  
**适用环境**: Windows 10/11 + Docker Desktop（离线环境）
