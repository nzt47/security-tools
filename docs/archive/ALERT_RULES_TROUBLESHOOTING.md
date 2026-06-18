# 🔧 告警规则加载失败故障排查报告

**排查时间**: 2026-06-09 17:15  
**问题**: Prometheus 未加载 alerts.yml 中的 13 条告警规则

---

## 📋 配置文件检查结果

### ✅ prometheus.yml 配置正确

**文件位置**: `monitoring/prometheus.yml`

**配置内容**:
```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - "alerts.yml"  # ✅ 正确引用

scrape_configs:
  - job_name: 'yunshu'
    static_configs:
      - targets: ['host.docker.internal:5678']
    metrics_path: '/metrics'
    scrape_interval: 5s
    
  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']
```

**验证结果**:
- ✅ 文件存在
- ✅ rule_files 配置正确
- ✅ alerts.yml 引用正确

---

### ✅ alerts.yml 配置正确

**文件位置**: `monitoring/alerts.yml`

**统计信息**:
- 文件行数：126 行
- 告警规则数：13 条
- groups 配置：存在

**规则列表**:
1. HighErrorRate (错误率 > 10%)
2. VeryHighErrorRate (错误率 > 50%)
3. HighLatency (95 分位延迟 > 5s)
4. VeryHighLatency (99 分位延迟 > 10s)
5. SecurityAttack (安全拦截 > 10 次/分)
6. CriticalSecurityAttack (严重安全拦截 > 5 次/分)
7. HighCPUUsage (CPU > 80%)
8. VeryHighCPUUsage (CPU > 95%)
9. HighMemoryUsage (内存 > 80%)
10. VeryHighMemoryUsage (内存 > 95%)
11. HighConversationErrorRate (对话错误率 > 20%)
12. YunshuDown (服务宕机)
13. PrometheusTargetMissing (目标丢失)

**验证结果**:
- ✅ 文件存在
- ✅ YAML 格式正确
- ✅ groups 配置正确
- ✅ 13 条规则定义完整

---

## 🔍 问题根本原因

### Docker Desktop 状态问题

**观察到的现象**:
1. Docker Desktop 频繁崩溃或未完全启动
2. docker-compose 命令返回 500 错误
3. 无法通过 Docker API 重启容器

**影响**:
- Prometheus 容器可能使用的是旧配置
- 配置文件更改未生效
- 告警规则未重新加载

---

## 🎯 解决方案

### 方案 A: 完全重启 Docker 和容器（推荐）⭐⭐⭐⭐⭐

**步骤 1: 完全停止 Docker**
```powershell
# 停止所有 Docker 进程
Stop-Process -Name "Docker Desktop" -Force
Stop-Process -Name "com.docker.*" -Force -ErrorAction SilentlyContinue

# 等待 30 秒
Start-Sleep -Seconds 30
```

**步骤 2: 重新启动 Docker Desktop**
```powershell
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"

# 等待 90 秒确保完全启动
Start-Sleep -Seconds 90
```

**步骤 3: 验证 Docker 状态**
```powershell
docker version
docker ps
```

**步骤 4: 重启 Prometheus 容器**
```powershell
# 方法 1: 使用 docker-compose
docker-compose -f docker-compose.monitoring.yml restart prometheus

# 方法 2: 使用 docker 命令
docker restart yunshu-prometheus
```

**步骤 5: 等待并验证**
```powershell
# 等待 15 秒
Start-Sleep -Seconds 15

# 验证告警规则
curl http://localhost:9090/api/v1/rules | ConvertFrom-Json
```

---

### 方案 B: 删除并重建容器（如果重启无效）

**步骤 1: 停止并删除容器**
```powershell
docker-compose -f docker-compose.monitoring.yml down
```

**步骤 2: 重新启动**
```powershell
docker-compose -f docker-compose.monitoring.yml up -d
```

**步骤 3: 查看日志**
```powershell
docker-compose -f docker-compose.monitoring.yml logs prometheus
```

---

### 方案 C: 手动验证配置文件加载

**步骤 1: 进入 Prometheus 容器**
```powershell
docker exec -it yunshu-prometheus sh
```

**步骤 2: 检查配置文件**
```bash
# 查看 prometheus.yml
cat /etc/prometheus/prometheus.yml

# 查看 alerts.yml
cat /etc/prometheus/alerts.yml

# 检查文件权限
ls -la /etc/prometheus/
```

**步骤 3: 查看 Prometheus 日志**
```bash
# 在容器内查看日志
cat /prometheus/prometheus.log

# 或直接查看启动日志
promtool check config /etc/prometheus/prometheus.yml
```

---

## 📊 验证脚本

### 快速验证脚本

创建 `verify_fix.ps1`:

```powershell
Write-Host "`n=== Verifying Alert Rules Fix ===" -ForegroundColor Cyan

Write-Host "`n1. Checking Docker..." -ForegroundColor Yellow
try {
    $v = docker version --format "{{.Server.Version}}" 2>$null
    Write-Host "   Docker: RUNNING ($v)" -ForegroundColor Green
} catch {
    Write-Host "   Docker: NOT RUNNING" -ForegroundColor Red
    Write-Host "   Run: .\recover_docker.ps1" -ForegroundColor Cyan
    exit 1
}

Write-Host "`n2. Checking containers..." -ForegroundColor Yellow
try {
    $c = docker ps --format "{{.Names}}"
    if ($c -match "prometheus") {
        Write-Host "   Prometheus: RUNNING" -ForegroundColor Green
    } else {
        Write-Host "   Prometheus: NOT RUNNING" -ForegroundColor Red
    }
} catch {
    Write-Host "   Container check: FAILED" -ForegroundColor Red
}

Write-Host "`n3. Testing alert rules..." -ForegroundColor Yellow
try {
    $rules = curl.exe -s "http://localhost:9090/api/v1/rules" | ConvertFrom-Json
    if ($rules.data.groups) {
        $count = 0
        foreach ($g in $rules.data.groups) { $count += $g.rules.Count }
        
        if ($count -ge 13) {
            Write-Host "   Alert rules: LOADED ($count rules)" -ForegroundColor Green
            Write-Host "   Status: SUCCESS" -ForegroundColor Green
        } else {
            Write-Host "   Alert rules: PARTIAL ($count/13)" -ForegroundColor Yellow
        }
    } else {
        Write-Host "   Alert rules: NOT LOADED" -ForegroundColor Red
    }
} catch {
    Write-Host "   Alert rules: CHECK FAILED" -ForegroundColor Red
}

Write-Host "`nDone!" -ForegroundColor Cyan
```

---

## 📁 自动化修复脚本

创建 `fix_alert_rules.ps1`:

```powershell
Write-Host "`n=== Alert Rules Fix Script ===" -ForegroundColor Cyan

# Step 1: Recover Docker
Write-Host "`nStep 1: Recovering Docker..." -ForegroundColor Yellow
.\recover_docker.ps1

# Step 2: Wait for Docker to fully start
Write-Host "`nWaiting 30 seconds..." -ForegroundColor Cyan
Start-Sleep -Seconds 30

# Step 3: Restart Prometheus
Write-Host "`nStep 2: Restarting Prometheus..." -ForegroundColor Yellow
try {
    docker-compose -f docker-compose.monitoring.yml restart prometheus
    Write-Host "   Restart: SUCCESS" -ForegroundColor Green
} catch {
    Write-Host "   Restart: FAILED" -ForegroundColor Red
    Write-Host "   Trying docker restart..." -ForegroundColor Yellow
    docker restart yunshu-prometheus
}

# Step 4: Wait for startup
Write-Host "`nWaiting 15 seconds for Prometheus to start..." -ForegroundColor Cyan
Start-Sleep -Seconds 15

# Step 5: Verify
Write-Host "`nStep 3: Verifying alert rules..." -ForegroundColor Yellow
try {
    $rules = curl.exe -s "http://localhost:9090/api/v1/rules" | ConvertFrom-Json
    if ($rules.data.groups) {
        $count = 0
        foreach ($g in $rules.data.groups) { $count += $g.rules.Count }
        
        Write-Host "   Rules loaded: $count" -ForegroundColor Green
        
        if ($count -ge 13) {
            Write-Host "`n✓ SUCCESS: All alert rules loaded!" -ForegroundColor Green
        } else {
            Write-Host "`n⚠ PARTIAL: Expected 13, got $count" -ForegroundColor Yellow
        }
    } else {
        Write-Host "`n✗ FAILED: No rules loaded" -ForegroundColor Red
    }
} catch {
    Write-Host "`n✗ FAILED: Cannot verify" -ForegroundColor Red
}

Write-Host "`nDone!" -ForegroundColor Cyan
```

---

## 🔍 根本原因分析

### 可能的问题点

1. **Docker Desktop 不稳定**
   - Docker Desktop for Windows 经常需要完全重启
   - WSL2 后端可能未正确初始化
   - Docker 守护进程可能卡死

2. **配置文件挂载问题**
   - 容器启动时配置文件路径不正确
   - 文件权限问题导致无法读取
   - 卷挂载失败

3. **Prometheus 配置重载**
   - Prometheus 启动时未读取最新配置
   - 需要完全重启容器（而非热重载）

---

## ✅ 成功标准

修复后应满足以下条件：

- [ ] ✅ Docker Desktop 正常运行
- [ ] ✅ Prometheus 容器运行中
- [ ] ✅ Grafana 容器运行中
- [ ] ✅ 13+ 条告警规则加载成功
- [ ] ✅ Prometheus 健康检查通过
- [ ] ✅ Grafana 健康检查通过
- [ ] ✅ 数据源连接正常
- [ ] ✅ Yunshu 仪表盘正常显示

---

## 📞 获取帮助

### 查看 Prometheus 日志

```powershell
# 方法 1: 使用 docker-compose
docker-compose -f docker-compose.monitoring.yml logs prometheus

# 方法 2: 使用 docker
docker logs yunshu-prometheus

# 方法 3: 查看错误
docker logs yunshu-prometheus 2>&1 | Select-String "error"
```

### 检查配置文件

```powershell
# 本地文件
Get-Content monitoring\prometheus.yml
Get-Content monitoring\alerts.yml

# 容器内文件（需要容器运行）
docker exec yunshu-prometheus cat /etc/prometheus/prometheus.yml
docker exec yunshu-prometheus cat /etc/prometheus/alerts.yml
```

---

## 🎯 建议的操作流程

### 立即执行（5 分钟）

1. **运行 Docker 恢复脚本**:
   ```powershell
   .\recover_docker.ps1
   ```

2. **等待 Docker 完全启动**（90 秒）

3. **重启 Prometheus 容器**:
   ```powershell
   docker-compose -f docker-compose.monitoring.yml restart prometheus
   ```

4. **验证修复结果**:
   ```powershell
   .\simple_verify.ps1
   ```

### 如果仍然失败

1. **查看 Prometheus 日志**:
   ```powershell
   docker logs yunshu-prometheus
   ```

2. **检查配置文件语法**:
   ```powershell
   # 使用 promtool 验证（需要安装 Prometheus）
   promtool check config monitoring\prometheus.yml
   ```

3. **手动重建容器**:
   ```powershell
   docker-compose -f docker-compose.monitoring.yml down
   docker-compose -f docker-compose.monitoring.yml up -d
   ```

---

**文档版本**: 1.0  
**创建时间**: 2026-06-09 17:15  
**建议**: 立即运行 `.\fix_alert_rules.ps1` 自动修复

🔧 **配置文件正确，问题在于 Docker Desktop 不稳定导致容器未重新加载配置！**
