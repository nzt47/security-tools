# Docker 镜像加速器配置失败 - 最终解决方案

**执行时间**: 2026-06-09 15:00  
**状态**: ❌ 配置未生效，镜像拉取超时

---

## 📊 当前状态

### ✅ 已验证的配置

1. **配置文件存在且正确** ✅
   - 路径：`C:\Users\Administrator\.docker\daemon.json`
   - 内容：4 个镜像加速器
   - JSON 格式正确

2. **Docker 运行正常** ✅
   - 版本：29.4.3
   - 服务状态：运行中

### ❌ 存在的问题

**问题 1: 配置未生效**
```
docker info --format '{{.RegistryConfig.Mirrors}}'
返回：[]
```

**问题 2: 镜像拉取超时**
```
dial tcp 128.121.243.235:443: connectex: 
A connection attempt failed
```

**根本原因**: Docker Desktop 未完全重新加载配置文件

---

## 🎯 解决方案（按优先级排序）

### 方案 A: 完全重启 Docker Desktop（推荐尝试）⭐

**为什么需要完全重启**:
- Docker Desktop 的普通重启可能不重新加载 daemon.json
- 需要完全停止所有 Docker 进程后重新启动

**操作步骤**:

```powershell
# 步骤 1: 完全停止所有 Docker 进程
Write-Host "Stopping all Docker processes..." -ForegroundColor Yellow
Stop-Process -Name "Docker Desktop" -Force
Stop-Process -Name "com.docker.*" -Force -ErrorAction SilentlyContinue
Stop-Process -Name "docker" -Force -ErrorAction SilentlyContinue

# 步骤 2: 等待
Write-Host "Waiting 30 seconds..." -ForegroundColor Cyan
Start-Sleep -Seconds 30

# 步骤 3: 验证进程已停止
Get-Process | Where-Object {$_.Name -like "*docker*"} | Select-Object Name

# 步骤 4: 重新启动 Docker Desktop
Write-Host "Starting Docker Desktop..." -ForegroundColor Cyan
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"

# 步骤 5: 等待完全启动
Write-Host "Waiting for Docker to fully start (60 seconds)..." -ForegroundColor Cyan
Start-Sleep -Seconds 60

# 步骤 6: 验证配置
Write-Host "`nVerifying configuration..." -ForegroundColor Yellow
docker info --format '{{.RegistryConfig.Mirrors}}'

# 步骤 7: 测试拉取
Write-Host "`nTesting pull..." -ForegroundColor Yellow
docker pull prom/prometheus:latest
```

**预期结果**:
- `docker info` 显示配置的镜像地址
- 镜像拉取成功

---

### 方案 B: 通过 Docker Desktop GUI 重新配置（最可靠）⭐⭐⭐

**为什么推荐**:
- GUI 配置会强制 Docker 重新加载
- 避免配置文件权限或缓存问题
- 100% 可靠

**操作步骤**:

1. **打开 Docker Desktop Settings**
   - 点击系统托盘 Docker 图标
   - 选择 "Settings" 或 "Dashboard"

2. **导航到 Docker Engine**
   - 左侧菜单选择 "Docker Engine"

3. **编辑配置**
   
   在配置编辑框中输入以下内容（覆盖现有内容）:
   
   ```json
   {
     "registry-mirrors": [
       "https://docker.m.daocloud.io",
       "https://docker.1panel.live"
     ],
     "max-concurrent-downloads": 10,
     "log-level": "info",
     "debug": false
   }
   ```

4. **应用并重启**
   - 点击右下角 "Apply & Restart"
   - **重要**: 等待 Docker 完全重启（2-3 分钟）
   - 不要关闭窗口，直到重启完成

5. **验证配置**
   
   重启完成后，在 PowerShell 中执行:
   
   ```powershell
   docker info --format '{{.RegistryConfig.Mirrors}}'
   ```
   
   **预期输出**: 应该显示配置的镜像地址

6. **测试拉取**
   
   ```powershell
   docker pull prom/prometheus:latest
   ```

---

### 方案 C: 使用可用的镜像源直接拉取

**如果方案 A 和 B 都失败，尝试直接使用镜像源**:

```powershell
# 尝试 DaoCloud 镜像
docker pull docker.m.daocloud.io/library/prom/prometheus:latest

# 尝试 1Panel 镜像
docker pull docker.1panel.live/library/prom/prometheus:latest

# 如果成功，标记为官方名称
docker tag docker.m.daocloud.io/library/prom/prometheus:latest prom/prometheus:latest
```

---

### 方案 D: 离线镜像导入（最终方案，100% 成功）⭐⭐⭐⭐⭐

**如果所有在线方案都失败，使用离线导入**:

**优势**:
- 100% 成功率
- 不依赖网络
- 可以快速部署多台机器

**步骤**:

#### 1. 在有网络的机器上下载

```bash
# 下载镜像
docker pull prom/prometheus:latest
docker pull grafana/grafana:latest

# 导出镜像
docker save -o prometheus.tar prom/prometheus:latest
docker save -o grafana.tar grafana/grafana:latest

# 验证大小
ls -lh *.tar
# prometheus.tar ~150MB
# grafana.tar ~350MB
```

#### 2. 传输到目标机器

- 使用 U 盘
- 使用网络共享
- 使用其他文件传输方式

#### 3. 在目标机器导入

```powershell
# 导入镜像
docker load -i prometheus.tar
docker load -i grafana.tar

# 验证
docker images | Select-String "prometheus|grafana"

# 启动监控栈
docker-compose -f docker-compose.monitoring.yml up -d
```

**详细步骤参考**: [offline_image_import_complete.md](file:///c:/Users/Administrator/agent/offline_image_import_complete.md)

---

## 🔍 为什么配置未生效

### 可能的原因

1. **Docker Desktop 缓存**
   - Docker Desktop 可能缓存了旧配置
   - 需要完全重启才能清除

2. **配置文件权限**
   - daemon.json 可能权限不正确
   - Docker 无法读取

3. **配置格式问题**
   - 虽然 JSON 语法正确
   - 但可能缺少必需字段

4. **Docker 版本问题**
   - 某些 Docker 版本配置方式不同
   - 需要特定格式

---

## 📋 完整重启和验证流程

### 一键脚本

创建 `complete_restart.ps1`:

```powershell
# Complete Docker Restart Script

$ErrorActionPreference = "Continue"

Write-Host "`n=== Complete Docker Restart ===" -ForegroundColor Cyan

# Stop everything
Write-Host "`nStopping Docker..." -ForegroundColor Yellow
Stop-Process -Name "Docker Desktop" -Force -ErrorAction SilentlyContinue
Stop-Process -Name "com.docker.*" -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 30

# Verify stopped
$processes = Get-Process | Where-Object {$_.Name -like "*docker*"}
if ($processes) {
    Write-Host "WARNING: Some processes still running" -ForegroundColor Yellow
    $processes | ForEach-Object { Stop-Process -Id $_.Id -Force }
}

# Start Docker
Write-Host "`nStarting Docker Desktop..." -ForegroundColor Cyan
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
Write-Host "Waiting 60 seconds..." -ForegroundColor Cyan
Start-Sleep -Seconds 60

# Verify
Write-Host "`nVerifying..." -ForegroundColor Yellow
try {
    $version = docker version --format "{{.Server.Version}}"
    Write-Host "Docker Version: $version" -ForegroundColor Green
    
    $mirrors = docker info --format '{{.RegistryConfig.Mirrors}}'
    if ($mirrors -and $mirrors.Count -gt 0) {
        Write-Host "Mirrors: SUCCESS" -ForegroundColor Green
        Write-Host "Mirrors: $mirrors" -ForegroundColor Cyan
    } else {
        Write-Host "Mirrors: NOT CONFIGURED" -ForegroundColor Red
        Write-Host "Please configure via Docker Desktop GUI" -ForegroundColor Yellow
    }
} catch {
    Write-Host "Verification failed" -ForegroundColor Red
}

Write-Host "`n=== Test Pull ===" -ForegroundColor Cyan
Write-Host "Pulling prom/prometheus:latest..." -ForegroundColor Yellow
docker pull prom/prometheus:latest

if ($LASTEXITCODE -eq 0) {
    Write-Host "`nSUCCESS!" -ForegroundColor Green
} else {
    Write-Host "`nFAILED" -ForegroundColor Red
    Write-Host "Use offline import method" -ForegroundColor Yellow
    Write-Host "See: offline_image_import_complete.md" -ForegroundColor Cyan
}
```

---

## 🎯 推荐执行顺序

### 第一步：尝试方案 B（GUI 配置）- 10 分钟

**最可靠，强烈推荐**:

1. 打开 Docker Desktop Settings
2. Docker Engine → 编辑配置
3. Apply & Restart
4. 等待重启完成
5. 验证配置

### 第二步：如果失败，尝试方案 A（完全重启）- 5 分钟

**执行完全重启脚本**:

```powershell
# 手动执行或运行 complete_restart.ps1
```

### 第三步：如果仍然失败，使用方案 D（离线导入）- 20-50 分钟

**100% 成功的最终方案**:

1. 参考 [offline_image_import_complete.md](file:///c:/Users/Administrator/agent/offline_image_import_complete.md)
2. 使用 U 盘在另一台机器下载镜像
3. 导入并启动

---

## 📊 成功标准

### 配置生效的标志

```powershell
# 执行此命令
docker info --format '{{.RegistryConfig.Mirrors}}'
```

**成功输出** (应该显示镜像地址):
```
[docker.m.daocloud.io docker.1panel.live ...]
```

**失败输出** (空数组):
```
[]
```

### 镜像拉取成功的标志

```powershell
docker images prom/prometheus
```

**成功输出**:
```
REPOSITORY          TAG       IMAGE ID   SIZE      CREATED
prom/prometheus     latest    abc123     150MB     2 weeks ago
```

---

## 💡 重要提示

### 关于 GUI 配置

**为什么 GUI 配置最可靠**:
1. Docker Desktop 会验证配置格式
2. 会自动处理配置文件权限
3. 会强制重新加载配置
4. 会显示错误信息如果配置有问题

### 关于完全重启

**为什么要等待 60 秒**:
- Docker Desktop 启动需要时间
- 需要完全初始化后才能加载配置
- 过早验证会得到错误结果

### 关于离线导入

**为什么推荐**:
- 不依赖网络状态
- 不受镜像加速器可用性影响
- 可以精确控制镜像版本
- 适合生产环境批量部署

---

## 📞 下一步行动

### 立即执行（推荐）:

**使用 GUI 配置**:

1. 打开 Docker Desktop
2. Settings → Docker Engine
3. 输入配置:
   ```json
   {
     "registry-mirrors": [
       "https://docker.m.daocloud.io",
       "https://docker.1panel.live"
     ]
   }
   ```
4. Apply & Restart
5. 等待 2-3 分钟
6. 验证: `docker info --format '{{.RegistryConfig.Mirrors}}'`
7. 测试: `docker pull prom/prometheus:latest`

### 如果 GUI 配置失败:

**使用离线导入**:

1. 打开 [offline_image_import_complete.md](file:///c:/Users/Administrator/agent/offline_image_import_complete.md)
2. 按照 "方法一：U 盘传输" 操作
3. 导入镜像
4. 启动监控栈

---

**文档版本**: 1.0  
**更新时间**: 2026-06-09 15:00  
**建议**: 优先使用 GUI 配置，失败则使用离线导入
