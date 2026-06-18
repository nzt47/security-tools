# 🔧 Docker Desktop 故障排查报告

**故障时间**: 2026-06-09 17:07  
**故障状态**: ❌ Docker Desktop 无响应

---

## 📊 故障现象

### 观察到的问题

1. **docker-compose 命令失败**
   ```
   request returned 500 Internal Server Error
   ```

2. **docker ps 命令失败**
   ```
   request returned 500 Internal Server Error for API route
   ```

3. **Prometheus 告警规则未加载**
   - 容器可能已停止
   - 或配置文件未正确挂载

---

## 🔍 根本原因

**可能原因**:
1. Docker Desktop 进程卡死
2. Docker 引擎崩溃
3. 资源不足导致服务停止
4. 配置冲突

---

## 🎯 解决方案

### 方案 A: 完全重启 Docker Desktop（推荐）⭐

**步骤**:

```powershell
# 1. 完全停止所有 Docker 进程
Stop-Process -Name "Docker Desktop" -Force
Stop-Process -Name "com.docker.*" -Force -ErrorAction SilentlyContinue
Stop-Process -Name "docker" -Force -ErrorAction SilentlyContinue

# 2. 等待 30 秒
Start-Sleep -Seconds 30

# 3. 验证进程已停止
Get-Process | Where-Object {$_.Name -like "*docker*"}

# 4. 重新启动 Docker Desktop
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"

# 5. 等待完全启动（60 秒）
Start-Sleep -Seconds 60

# 6. 验证 Docker 恢复
docker version
```

**预期结果**: Docker 恢复正常

---

### 方案 B: 重启容器

**Docker 恢复后执行**:

```powershell
# 1. 查看容器
docker ps -a

# 2. 启动容器
docker start yunshu-prometheus
docker start yunshu-grafana

# 3. 或重启所有容器
docker-compose -f docker-compose.monitoring.yml restart

# 4. 验证
docker ps
```

---

### 方案 C: 重新部署

**如果容器配置有问题**:

```powershell
# 1. 停止并删除容器
docker-compose -f docker-compose.monitoring.yml down

# 2. 重新启动
docker-compose -f docker-compose.monitoring.yml up -d

# 3. 验证
docker-compose -f docker-compose.monitoring.yml ps
```

---

## 📋 验证清单

### Docker 恢复后必须验证的项目

- [ ] ✅ Docker 版本正常
  ```powershell
  docker version
  ```

- [ ] ✅ 容器运行正常
  ```powershell
  docker ps
  ```

- [ ] ✅ Prometheus 健康
  ```powershell
  curl http://localhost:9090/-/healthy
  ```

- [ ] ✅ Grafana 健康
  ```powershell
  curl http://localhost:3000/api/health
  ```

- [ ] ✅ 告警规则加载
  ```powershell
  curl http://localhost:9090/api/v1/rules
  ```

---

## 🔧 自动化修复脚本

创建 `fix_docker_crash.ps1`:

```powershell
# Docker Crash Recovery Script

$ErrorActionPreference = "Continue"

Write-Host "`n=== Docker Crash Recovery ===" -ForegroundColor Cyan

# Stop Docker
Write-Host "`nStopping Docker..." -ForegroundColor Yellow
Stop-Process -Name "Docker Desktop" -Force -ErrorAction SilentlyContinue
Stop-Process -Name "com.docker.*" -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 30

# Verify stopped
$processes = Get-Process | Where-Object {$_.Name -like "*docker*"}
if ($processes) {
    Write-Host "Force stopping remaining processes..." -ForegroundColor Yellow
    $processes | ForEach-Object { Stop-Process -Id $_.Id -Force }
}

# Start Docker
Write-Host "`nStarting Docker Desktop..." -ForegroundColor Cyan
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
Write-Host "Waiting 60 seconds for full startup..." -ForegroundColor Cyan
Start-Sleep -Seconds 60

# Verify
Write-Host "`nVerifying Docker..." -ForegroundColor Yellow
try {
    $version = docker version --format "{{.Server.Version}}"
    Write-Host "Docker Version: $version" -ForegroundColor Green
} catch {
    Write-Host "ERROR: Docker still not responding!" -ForegroundColor Red
    Write-Host "Please restart computer and try again." -ForegroundColor Yellow
    exit 1
}

# Check containers
Write-Host "`nChecking containers..." -ForegroundColor Yellow
try {
    $containers = docker ps --format "table {{.Names}}\t{{.Status}}"
    if ($containers) {
        Write-Host "Containers running:" -ForegroundColor Green
        Write-Host $containers
    } else {
        Write-Host "No containers running" -ForegroundColor Yellow
        Write-Host "Starting monitoring stack..." -ForegroundColor Cyan
        docker-compose -f docker-compose.monitoring.yml up -d
    }
} catch {
    Write-Host "WARNING: Cannot check containers" -ForegroundColor Yellow
}

# Verify services
Write-Host "`nVerifying services..." -ForegroundColor Yellow

# Prometheus
try {
    $response = curl.exe -s http://localhost:9090/-/healthy
    if ($response -match "Prometheus Server is Healthy") {
        Write-Host "Prometheus: HEALTHY" -ForegroundColor Green
    } else {
        Write-Host "Prometheus: NOT RESPONDING" -ForegroundColor Yellow
    }
} catch {
    Write-Host "Prometheus: NOT ACCESSIBLE" -ForegroundColor Yellow
}

# Grafana
try {
    $response = curl.exe -s http://localhost:3000/api/health
    if ($response -match "ok") {
        Write-Host "Grafana: HEALTHY" -ForegroundColor Green
    } else {
        Write-Host "Grafana: NOT RESPONDING" -ForegroundColor Yellow
    }
} catch {
    Write-Host "Grafana: NOT ACCESSIBLE" -ForegroundColor Yellow
}

Write-Host "`n=== Recovery Complete ===" -ForegroundColor Cyan
Write-Host "If services are still not working, check logs:" -ForegroundColor White
Write-Host "docker-compose -f docker-compose.monitoring.yml logs" -ForegroundColor Cyan
```

---

## 📞 获取帮助

### 查看日志

```powershell
# Prometheus 日志
docker-compose -f docker-compose.monitoring.yml logs prometheus

# Grafana 日志
docker-compose -f docker-compose.monitoring.yml logs grafana

# 所有日志
docker-compose -f docker-compose.monitoring.yml logs > logs.txt
```

### 检查配置

```powershell
# 检查配置文件
Test-Path .\monitoring\prometheus.yml
Test-Path .\monitoring\alerts.yml

# 检查配置文件内容
Get-Content .\monitoring\prometheus.yml | Select-String "rule_files"
```

---

## 💡 预防措施

### 避免 Docker 崩溃的建议

1. **不要同时运行太多容器**
   - 限制同时运行的容器数量
   - 定期清理未使用的容器

2. **分配足够资源**
   - Docker Desktop Settings → Resources
   - 建议：4GB+ 内存，2+ CPU

3. **定期清理**
   ```powershell
   # 清理未使用的容器
   docker container prune
   
   # 清理未使用的镜像
   docker image prune
   
   # 清理卷
   docker volume prune
   ```

4. **监控资源使用**
   - 使用 Docker Desktop Dashboard
   - 监控 CPU 和内存使用

---

## 📊 故障统计

| 项目 | 状态 |
|------|------|
| Docker Desktop | ❌ 无响应 |
| docker-compose | ❌ 500 错误 |
| docker ps | ❌ 500 错误 |
| Prometheus | ⚠️ 可能停止 |
| Grafana | ⚠️ 可能停止 |
| 告警规则 | ❌ 未加载 |

---

## 🎯 下一步行动

### 立即执行

1. **运行自动化修复脚本**:
   ```powershell
   .\fix_docker_crash.ps1
   ```

2. **或手动重启 Docker Desktop**:
   - 完全停止所有 Docker 进程
   - 等待 30 秒
   - 重新启动 Docker Desktop
   - 等待 60 秒

3. **验证服务恢复**:
   ```powershell
   docker version
   docker ps
   curl http://localhost:9090/-/healthy
   ```

### 如果仍然失败

1. **重启计算机**
2. **检查 Docker Desktop 日志**
3. **重新安装 Docker Desktop**

---

**文档版本**: 1.0  
**创建时间**: 2026-06-09 17:07  
**建议**: 立即运行 `.\fix_docker_crash.ps1` 恢复 Docker
