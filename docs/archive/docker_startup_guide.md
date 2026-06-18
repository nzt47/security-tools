# 🐳 Docker Desktop 启动指南

**问题**: Docker Desktop 当前未运行  
**目标**: 启动监控栈并验证 Prometheus 和 Grafana

---

## 📋 启动步骤

### 步骤 1: 启动 Docker Desktop

#### Windows 系统

**方法 1: 通过开始菜单**
1. 点击 Windows 开始按钮
2. 搜索 "Docker Desktop"
3. 点击启动
4. 等待底部状态栏图标变为绿色（约 30-60 秒）

**方法 2: 通过命令行**
```powershell
# 启动 Docker Desktop
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
```

**方法 3: 快捷方式**
```powershell
# 如果已添加到 PATH
docker-desktop
```

---

### 步骤 2: 验证 Docker 运行

```bash
# 检查 Docker 是否运行
docker version

# 应该看到 Client 和 Server 信息
# 如果没有 Server 信息，说明 Docker Desktop 还未完全启动
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

### 步骤 3: 启动监控栈

```bash
# 确保在正确的目录
cd C:\Users\Administrator\agent

# 启动 Prometheus 和 Grafana
docker-compose -f docker-compose.monitoring.yml up -d

# 查看启动日志
docker-compose -f docker-compose.monitoring.yml logs -f
```

**预期输出**:
```
[+] Running 3/3
 ✔ Network agent_yunshu-monitoring  Created
 ✔ Container yunshu-prometheus     Started
 ✔ Container yunshu-grafana        Started
```

---

### 步骤 4: 验证服务运行

```bash
# 查看容器状态
docker-compose -f docker-compose.monitoring.yml ps

# 应该看到两个容器都在运行
```

**预期输出**:
```
NAME                   STATUS          PORTS
yunshu-prometheus      Up (healthy)    0.0.0.0:9090->9090/tcp
yunshu-grafana         Up (healthy)    0.0.0.0:3000->3000/tcp
```

---

### 步骤 5: 访问服务界面

#### Prometheus
- **地址**: http://localhost:9090
- **验证**: 页面正常加载
- **检查**: Status → Rules 显示告警规则

#### Grafana
- **地址**: http://localhost:3000
- **用户名**: admin
- **密码**: admin123
- **验证**: 登录成功
- **检查**: Dashboards 中有 Yunshu Monitor

---

## 🔍 故障排查

### 问题 1: Docker Desktop 启动失败

**症状**: Docker Desktop 闪退或无法启动

**解决方案**:
```powershell
# 1. 检查 Hyper-V 是否启用
Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V

# 2. 如果未启用，启用它
Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V -All

# 3. 重启电脑后再次尝试
```

---

### 问题 2: 端口被占用

**症状**: 容器启动失败，提示端口已占用

**解决方案**:
```bash
# 检查端口占用
netstat -ano | findstr :9090
netstat -ano | findstr :3000

# 停止占用端口的进程
taskkill /PID <进程 ID> /F

# 或者修改 docker-compose.monitoring.yml 中的端口映射
# 例如将 3000:3000 改为 3001:3000
```

---

### 问题 3: 容器启动后立即退出

**症状**: 容器状态为 Exited

**解决方案**:
```bash
# 查看容器日志
docker-compose -f docker-compose.monitoring.yml logs prometheus
docker-compose -f docker-compose.monitoring.yml logs grafana

# 常见问题：
# 1. 配置文件路径错误
# 2. 权限问题
# 3. 内存不足
```

---

## ✅ 验证清单

启动后按以下顺序验证：

### Docker 验证
- [ ] Docker Desktop 图标为绿色
- [ ] `docker version` 显示 Server 信息
- [ ] `docker ps` 显示运行中的容器

### Prometheus 验证
- [ ] http://localhost:9090 可访问
- [ ] Status → Rules 显示告警规则
- [ ] Targets 页面显示 yunshu 和 prometheus 为 UP

### Grafana 验证
- [ ] http://localhost:3000 可访问
- [ ] 可以使用 admin/admin123 登录
- [ ] Data Sources 中有 Prometheus 数据源
- [ ] Dashboards 中有 Yunshu Monitor 仪表盘

---

## 🚀 快速启动脚本

创建一个 PowerShell 脚本来自动化启动：

```powershell
# save as: start_monitoring.ps1

Write-Host "🚀 启动 Docker 监控栈..." -ForegroundColor Green

# 1. 启动 Docker Desktop
Write-Host "`n📦 步骤 1: 启动 Docker Desktop" -ForegroundColor Yellow
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
Write-Host "   等待 Docker Desktop 启动..." -ForegroundColor Cyan
Start-Sleep -Seconds 30

# 2. 验证 Docker
Write-Host "`n🔍 步骤 2: 验证 Docker" -ForegroundColor Yellow
$dockerVersion = docker version --format "{{.Server.Version}}"
if ($dockerVersion) {
    Write-Host "   ✅ Docker 运行正常 (版本：$dockerVersion)" -ForegroundColor Green
} else {
    Write-Host "   ❌ Docker 未运行" -ForegroundColor Red
    exit 1
}

# 3. 启动监控栈
Write-Host "`n📊 步骤 3: 启动监控栈" -ForegroundColor Yellow
Set-Location "C:\Users\Administrator\agent"
docker-compose -f docker-compose.monitoring.yml up -d

# 4. 等待服务启动
Write-Host "`n⏳ 等待服务启动..." -ForegroundColor Cyan
Start-Sleep -Seconds 10

# 5. 验证服务
Write-Host "`n✅ 步骤 4: 验证服务" -ForegroundColor Yellow
$containers = docker-compose -f docker-compose.monitoring.yml ps --format json | ConvertFrom-Json
foreach ($container in $containers) {
    if ($container.State -eq "running") {
        Write-Host "   ✅ $($container.Name) 运行正常" -ForegroundColor Green
    } else {
        Write-Host "   ❌ $($container.Name) 状态异常" -ForegroundColor Red
    }
}

# 6. 显示访问信息
Write-Host "`n🌐 访问地址:" -ForegroundColor Yellow
Write-Host "   Prometheus: http://localhost:9090" -ForegroundColor Cyan
Write-Host "   Grafana: http://localhost:3000 (admin/admin123)" -ForegroundColor Cyan

Write-Host "`n🎉 启动完成!" -ForegroundColor Green
```

**使用方法**:
```powershell
# 在 PowerShell 中执行
.\start_monitoring.ps1
```

---

## 📝 后续步骤

启动成功后：

1. **验证 Prometheus 规则**
   - 访问 http://localhost:9090
   - 导航到 Status → Rules
   - 确认显示 19 个告警规则

2. **导入 Grafana 仪表盘**
   - 访问 http://localhost:3000
   - 登录 (admin/admin123)
   - Dashboards → Import
   - 上传 `monitoring/grafana/dashboards/yunshu-alerts-monitor.json`
   - 选择 Prometheus 数据源
   - 点击 Import

3. **运行测试清单**
   - 打开 `alert_rules_test_checklist.md`
   - 逐项执行测试
   - 记录测试结果

---

**文档创建时间**: 2026-06-09  
**适用环境**: Windows 10/11 + Docker Desktop
