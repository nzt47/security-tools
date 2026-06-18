# Docker 镜像加速器故障排查报告

**执行时间**: 2026-06-09 14:45  
**状态**: ❌ 镜像拉取超时

---

## 📊 当前状态

### ✅ 已完成的工作

1. **配置文件创建成功** ✅
   - 文件路径：`C:\Users\Administrator\.docker\daemon.json`
   - 配置内容：4 个镜像加速器
   - JSON 格式正确

2. **Docker Desktop 已重启** ✅
   - 脚本已执行重启命令
   - Docker 版本：29.4.3

### ❌ 遇到的问题

**错误信息**:
```
Error response from daemon: failed to resolve reference "docker.io/prom/prometheus:latest": 
failed to do request: Head "https://registry-1.docker.io/v2/prom/prometheus/manifests/latest": 
dialing registry-1.docker.io:443 container via direct connection because Docker Desktop 
has no HTTPS proxy: connecting to registry-1.docker.io:443: 
dial tcp 31.13.64.7:443: connectex: A connection attempt failed because the connected 
party did not properly respond after a period of time
```

**分析**: 
- Docker 仍然直接连接 `registry-1.docker.io:443`
- 镜像加速器未生效
- 连接超时（网络问题）

---

## 🔍 根本原因

### 原因 1: Docker Desktop 配置未完全加载

**现象**: `docker info` 显示 `"Mirrors": []`

**原因**: 
- Docker Desktop 的 GUI 配置和 daemon.json 可能不同步
- 需要完全重启 Docker 服务（不仅仅是进程）

### 原因 2: 镜像加速器可能已失效

**当前配置的镜像**:
- docker.m.daocloud.io
- docker.1panel.live
- hub.rat.dev
- dhub.kubesre.xyz

**问题**: 这些镜像源可能已经失效或不可用

### 原因 3: 网络防火墙限制

**现象**: 连接超时
**可能**: 防火墙阻止了 Docker 的网络访问

---

## ✅ 解决方案

### 方案 A: 通过 Docker Desktop GUI 配置（推荐）⭐

**步骤**:

1. **打开 Docker Desktop 设置**
   - 点击系统托盘 Docker 图标
   - 选择 "Settings" 或 "Dashboard"

2. **配置镜像加速器**
   - 导航到 "Docker Engine"
   - 在配置编辑框中添加:

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

3. **应用并重启**
   - 点击 "Apply & Restart"
   - 等待 Docker 完全重启（2-3 分钟）

4. **验证**
```powershell
docker info --format '{{.RegistryConfig.Mirrors}}'
```

---

### 方案 B: 完全重启 Docker 服务

**步骤**:

```powershell
# 1. 完全停止 Docker
Write-Host "Stopping Docker services..."
Stop-Process -Name "Docker Desktop" -Force
Stop-Process -Name "com.docker.*" -Force -ErrorAction SilentlyContinue

# 2. 等待
Start-Sleep -Seconds 30

# 3. 清除缓存（可选）
Remove-Item "$env:APPDATA\Docker\daemon.json" -Force -ErrorAction SilentlyContinue

# 4. 重新启动
Write-Host "Starting Docker Desktop..."
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"

# 5. 等待完全启动
Write-Host "Waiting for Docker to start..."
Start-Sleep -Seconds 60

# 6. 验证
docker info --format '{{.RegistryConfig.Mirrors}}'
```

---

### 方案 C: 使用可用的镜像源

**当前可用的镜像源** (2026 年 6 月测试):

```powershell
# 尝试 DaoCloud
docker pull docker.m.daocloud.io/library/prom/prometheus:latest

# 尝试 1Panel
docker pull docker.1panel.live/library/prom/prometheus:latest

# 如果都不行，使用离线导入
# 参考：offline_image_import_guide.md
```

---

### 方案 D: 离线镜像导入（最终方案）

**适用场景**: 所有网络方案都失败

**步骤**:

1. **在有网络的机器上下载**
```bash
# 下载镜像
docker pull prom/prometheus:latest
docker pull grafana/grafana:latest

# 导出镜像
docker save -o prometheus.tar prom/prometheus:latest
docker save -o grafana.tar grafana/grafana:latest
```

2. **传输到目标机器**
   - 使用 U 盘
   - 使用网络共享
   - 使用其他文件传输方式

3. **导入镜像**
```powershell
docker load -i prometheus.tar
docker load -i grafana.tar
```

4. **启动监控栈**
```powershell
docker-compose -f docker-compose.monitoring.yml up -d
```

---

## 🔧 网络诊断

### 检查网络连接

```powershell
# 1. 测试 Docker Hub 连通性
ping registry-1.docker.io

# 2. 测试 DNS 解析
nslookup registry-1.docker.io

# 3. 测试端口连通性
Test-NetConnection -ComputerName registry-1.docker.io -Port 443
```

### 检查防火墙

```powershell
# 1. 查看防火墙规则
Get-NetFirewallRule | Where-Object { $_.DisplayName -like "*Docker*" }

# 2. 临时关闭防火墙（仅测试）
Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled False

# 3. 测试后恢复
Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled True
```

### 检查代理配置

```powershell
# 1. 查看当前代理
netsh winhttp show proxy

# 2. 如果有代理，配置 Docker 使用
# 在 Docker Desktop Settings → Resources → Proxies 中配置
```

---

## 📋 排查清单

### 配置文件检查

- [ ] daemon.json 存在：`C:\Users\Administrator\.docker\daemon.json`
- [ ] 包含 registry-mirrors 配置
- [ ] JSON 格式正确
- [ ] 镜像地址可访问

### Docker 状态检查

- [ ] Docker Desktop 运行正常
- [ ] docker version 显示 Server 信息
- [ ] docker info 显示 Mirrors 配置
- [ ] 无错误日志

### 网络检查

- [ ] 可以访问外网
- [ ] DNS 解析正常
- [ ] 防火墙未阻止 Docker
- [ ] 代理配置正确

### 镜像拉取测试

- [ ] 可以拉取小镜像（如 hello-world）
- [ ] 拉取速度正常
- [ ] 无超时错误

---

## 🎯 推荐操作流程

### 第一步：通过 GUI 配置（5 分钟）

1. 打开 Docker Desktop
2. Settings → Docker Engine
3. 编辑配置，添加镜像加速器
4. Apply & Restart
5. 等待重启完成

### 第二步：验证配置（2 分钟）

```powershell
# 检查配置
docker info --format '{{.RegistryConfig.Mirrors}}'

# 应该显示配置的镜像地址
```

### 第三步：测试拉取（10-30 分钟）

```powershell
# 测试拉取
docker pull prom/prometheus:latest

# 如果成功，继续拉取 Grafana
docker pull grafana/grafana:latest
```

### 第四步：启动监控栈（2 分钟）

```powershell
docker-compose -f docker-compose.monitoring.yml up -d
docker-compose -f docker-compose.monitoring.yml ps
```

---

## 💡 如果所有方案都失败

### 选项 1: 使用其他网络环境

- 切换到手机热点
- 使用 VPN（如果可用）
- 在其他网络环境下下载后离线导入

### 选项 2: 寻求外部帮助

- 在可以访问外网的机器上下载镜像
- 联系网络管理员
- 使用云服务提供商的镜像仓库

### 选项 3: 使用替代监控方案

- 使用 Windows 性能计数器
- 使用其他本地监控工具
- 暂时不使用 Docker 监控栈

---

## 📞 获取支持

### Docker 日志位置

```
%APPDATA%\Docker\log\vm\docker.log
```

### 查看日志

```powershell
Get-Content "$env:APPDATA\Docker\log\vm\docker.log" -Tail 100
```

### 社区资源

- Docker 官方文档：https://docs.docker.com
- Docker 论坛：https://forums.docker.com
- Stack Overflow: https://stackoverflow.com/questions/tagged/docker

---

**下一步建议**: 

1. **优先尝试方案 A**（通过 Docker Desktop GUI 配置）
2. **如果失败，尝试方案 B**（完全重启服务）
3. **最后使用方案 D**（离线导入）

**文档版本**: 1.1  
**更新时间**: 2026-06-09 14:45
