# 🔍 Docker 监控栈启动失败故障排查报告

**报告日期**: 2026-06-09  
**启动命令**: `docker-compose -f docker-compose.monitoring.aliyun.yml up -d`  
**最终状态**: ❌ 启动失败

---

## 📊 故障现象汇总

### 尝试 1: 阿里云镜像

**镜像地址**: `registry.cn-hangzhou.aliyuncs.com/prometheus/prometheus:v2.45.0`

**错误信息**:
```
failed to resolve reference "registry.cn-hangzhou.aliyuncs.com/prometheus/prometheus:v2.45.0": 
registry.cn-hangzhou.aliyuncs.com/prometheus/prometheus:v2.45.0: not found
```

**分析**: 阿里云镜像仓库中不存在该镜像或路径错误

---

### 尝试 2: 网易镜像

**镜像地址**: `hub-mirror.c.163.com/prom/prometheus:latest`

**错误信息**:
```
failed to resolve reference "hub-mirror.c.163.com/grafana/grafana:latest": 
failed to do request: Head "https://hub-mirror.c.163.com/v2/grafana/grafana/manifests/latest": 
dialing hub-mirror.c.163.com:443 container via direct connection because Docker Desktop 
has no HTTPS proxy: connecting to hub-mirror.c.163.com:443: 
dial tcp: lookup hub-mirror.c.163.com: no such host
```

**分析**: DNS 解析失败，无法解析 `hub-mirror.c.163.com`

---

### Docker 配置检查

**当前配置**:
```json
{
  "Mirrors": []
}
```

**问题**: 未配置任何镜像加速器

---

## 🎯 根本原因分析

### 主要原因

1. **网络限制** 🔴
   - 无法访问 Docker Hub (docker.io)
   - 无法访问国内镜像源（阿里云、网易）
   - DNS 解析可能受限

2. **镜像加速器未配置** 🟡
   - Docker daemon.json 中无 mirror 配置
   - 需要手动配置镜像加速器

3. **镜像源不可用** 🟡
   - 阿里云镜像路径可能已变更
   - 网易镜像源 DNS 解析失败

---

## ✅ 解决方案

### 方案一：手动配置 Docker 镜像加速器（推荐）⭐

#### 步骤 1: 创建 Docker 配置文件

**文件路径**: `C:\Users\Administrator\.docker\daemon.json`

**配置内容**:
```json
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://docker.1panel.live",
    "https://hub.rat.dev",
    "https://dhub.kubesre.xyz",
    "https://docker.fxxk.dedyn.io"
  ],
  "max-concurrent-downloads": 10,
  "log-level": "info"
}
```

**PowerShell 一键创建**:
```powershell
# 创建配置目录
New-Item -ItemType Directory -Path "$env:USERPROFILE\.docker" -Force

# 创建配置文件
@'
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://docker.1panel.live",
    "https://hub.rat.dev",
    "https://dhub.kubesre.xyz",
    "https://docker.fxxk.dedyn.io"
  ],
  "max-concurrent-downloads": 10,
  "log-level": "info"
}
'@ | Out-File -FilePath "$env:USERPROFILE\.docker\daemon.json" -Encoding UTF8

# 验证配置
Get-Content "$env:USERPROFILE\.docker\daemon.json"
```

#### 步骤 2: 重启 Docker Desktop

**方法 1: 通过界面重启**
1. 右键点击系统托盘 Docker 图标
2. 选择 "Quit Docker Desktop"
3. 等待 10 秒后重新启动

**方法 2: 通过 PowerShell 重启**
```powershell
# 停止 Docker
Stop-Process -Name "Docker Desktop" -Force

# 等待 10 秒
Start-Sleep -Seconds 10

# 启动 Docker
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"

# 等待完全启动
Start-Sleep -Seconds 30
```

#### 步骤 3: 验证配置

```powershell
# 检查镜像加速器配置
docker info --format '{{.RegistryConfig.Mirrors}}'

# 应该显示配置的镜像地址
```

#### 步骤 4: 测试镜像拉取

```powershell
# 测试拉取 Prometheus
docker pull prom/prometheus:latest

# 测试拉取 Grafana
docker pull grafana/grafana:latest
```

#### 步骤 5: 启动监控栈

```powershell
# 使用官方镜像启动
docker-compose -f docker-compose.monitoring.yml up -d
```

---

### 方案二：使用本地离线镜像（备选）

如果方案一仍然失败，使用离线镜像导入方式。

#### 步骤 1: 在有网络的机器上下载镜像

```bash
# 在可以访问外网的机器上执行
docker pull prom/prometheus:latest
docker pull grafana/grafana:latest

# 导出镜像
docker save -o prometheus.tar prom/prometheus:latest
docker save -o grafana.tar grafana/grafana:latest

# 传输到目标机器（使用 U 盘、网络等）
```

#### 步骤 2: 导入镜像

```powershell
# 导入 Prometheus
docker load -i prometheus.tar

# 导入 Grafana
docker load -i grafana.tar

# 验证
docker images | Select-String "prometheus|grafana"
```

#### 步骤 3: 启动监控栈

```powershell
docker-compose -f docker-compose.monitoring.yml up -d
```

---

### 方案三：使用其他可用的镜像源

#### 可用的镜像源列表

**国内镜像** (优先级从高到低):
1. DaoCloud: `docker.m.daocloud.io`
2. 1Panel: `docker.1panel.live`
3. Rat: `hub.rat.dev`
4. Kubesre: `dhub.kubesre.xyz`
5. Dedyn: `docker.fxxk.dedyn.io`

**使用方法**:
```powershell
# 使用 DaoCloud 镜像
docker pull docker.m.daocloud.io/library/prom/prometheus:latest
docker pull docker.m.daocloud.io/library/grafana/grafana:latest

# 标记为官方名称
docker tag docker.m.daocloud.io/library/prom/prometheus:latest prom/prometheus:latest
docker tag docker.m.daocloud.io/library/grafana/grafana:latest grafana/grafana:latest

# 启动
docker-compose -f docker-compose.monitoring.yml up -d
```

---

## 🔧 常见错误排查清单

### 错误 1: 镜像拉取超时

**错误信息**:
```
Get "https://registry-1.docker.io/v2/...": net/http: request canceled while waiting for connection
```

**解决方案**:
```powershell
# 1. 配置镜像加速器（见方案一）

# 2. 检查网络连接
ping registry-1.docker.io

# 3. 检查 DNS 配置
Get-DnsClientServerAddress | Select-Object ServerAddresses

# 4. 修改 DNS 为 8.8.8.8 或 1.1.1.1
Set-DnsClientServerAddress -InterfaceIndex <索引> -ServerAddresses ("8.8.8.8","1.1.1.1")
```

---

### 错误 2: DNS 解析失败

**错误信息**:
```
dial tcp: lookup hub-mirror.c.163.com: no such host
```

**解决方案**:
```powershell
# 1. 清除 DNS 缓存
ipconfig /flushdns

# 2. 重启 DNS 客户端服务
Restart-Service Dnscache -Force

# 3. 修改 DNS 服务器
# 网络和共享中心 → 更改适配器设置 → 右键网卡 → 属性
# → IPv4 → 使用以下 DNS 服务器地址
# 首选：8.8.8.8
# 备用：1.1.1.1

# 4. 验证 DNS
nslookup hub-mirror.c.163.com
```

---

### 错误 3: 证书验证失败

**错误信息**:
```
x509: certificate signed by unknown authority
```

**解决方案**:
```powershell
# 1. 配置 insecure registry（仅用于测试）
# 编辑 daemon.json
{
  "insecure-registries": [
    "hub-mirror.c.163.com",
    "registry.cn-hangzhou.aliyuncs.com"
  ]
}

# 2. 重启 Docker Desktop

# 3. 或者使用 HTTP 而非 HTTPS
docker pull http://hub-mirror.c.163.com/prom/prometheus:latest
```

---

### 错误 4: 端口被占用

**错误信息**:
```
Error starting userland proxy: listen tcp4 0.0.0.0:9090: bind: address already in use
```

**解决方案**:
```powershell
# 1. 查找占用进程
netstat -ano | findstr :9090
netstat -ano | findstr :3000

# 2. 停止进程
taskkill /PID <进程 ID> /F

# 3. 或修改 docker-compose.yml 中的端口映射
# ports:
#   - "9091:9090"  # 改为 9091
#   - "3001:3000"  # 改为 3001
```

---

### 错误 5: 配置文件路径错误

**错误信息**:
```
error loading config file: open /etc/prometheus/prometheus.yml: no such file or directory
```

**解决方案**:
```powershell
# 1. 检查文件是否存在
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

---

## 📋 快速修复步骤（推荐顺序）

### 第一步：配置镜像加速器（5 分钟）

```powershell
# 1. 创建配置文件
$dockerConfig = @"
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://docker.1panel.live",
    "https://hub.rat.dev"
  ],
  "max-concurrent-downloads": 10
}
"@

New-Item -ItemType Directory -Path "$env:USERPROFILE\.docker" -Force
$dockerConfig | Out-File -FilePath "$env:USERPROFILE\.docker\daemon.json" -Encoding UTF8

# 2. 重启 Docker
Stop-Process -Name "Docker Desktop" -Force
Start-Sleep -Seconds 10
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
Start-Sleep -Seconds 30

# 3. 验证配置
docker info --format '{{.RegistryConfig.Mirrors}}'
```

### 第二步：拉取镜像（10-30 分钟）

```powershell
# 拉取 Prometheus
docker pull prom/prometheus:latest

# 拉取 Grafana
docker pull grafana/grafana:latest
```

### 第三步：启动监控栈（2 分钟）

```powershell
# 启动
docker-compose -f docker-compose.monitoring.yml up -d

# 查看状态
docker-compose -f docker-compose.monitoring.yml ps

# 查看日志
docker-compose -f docker-compose.monitoring.yml logs -f
```

---

## 🎯 验证成功标准

### 容器状态

```powershell
docker-compose -f docker-compose.monitoring.yml ps
```

**预期输出**:
```
NAME                   STATUS          PORTS
yunshu-prometheus      Up (healthy)    0.0.0.0:9090->9090/tcp
yunshu-grafana         Up (healthy)    0.0.0.0:3000->3000/tcp
```

### Prometheus 验证

```powershell
# 访问健康检查
curl http://localhost:9090/-/healthy

# 应该返回：Prometheus Server is Healthy.
```

**浏览器访问**: http://localhost:9090

**检查项**:
- [ ] Status → Rules 显示 19 个告警规则
- [ ] Targets 页面显示 yunshu 为 UP

### Grafana 验证

```powershell
# 访问健康检查
curl http://localhost:3000/api/health
```

**浏览器访问**: http://localhost:3000

**检查项**:
- [ ] 可以使用 admin/admin123 登录
- [ ] 可以导入仪表盘
- [ ] 仪表盘显示 11 个面板

---

## 📞 获取帮助

### 日志收集

```powershell
# Docker 日志
docker-compose -f docker-compose.monitoring.yml logs > docker_logs.txt

# Docker 信息
docker info > docker_info.txt

# 网络诊断
ping registry-1.docker.io > network_test.txt
nslookup registry-1.docker.io >> network_test.txt
```

### 支持渠道

1. **Docker Desktop 日志**
   - 位置：`%APPDATA%\Docker\log\`
   - 查看最新日志：`Get-Content %APPDATA%\Docker\log\vm\docker.log -Tail 50`

2. **Windows 事件查看器**
   - 事件查看器 → Windows 日志 → 应用程序
   - 筛选来源：Docker

3. **社区支持**
   - Docker 官方论坛：https://forums.docker.com
   - Stack Overflow: https://stackoverflow.com/questions/tagged/docker

---

## 📊 故障排查清单

### 网络检查

- [ ] 可以访问外网
- [ ] DNS 解析正常
- [ ] 防火墙未阻止 Docker
- [ ] 代理配置正确（如有）

### Docker 检查

- [ ] Docker Desktop 运行正常
- [ ] 镜像加速器已配置
- [ ] 磁盘空间充足
- [ ] 内存充足（至少 2GB 可用）

### 配置文件检查

- [ ] daemon.json 存在且格式正确
- [ ] docker-compose.yml 语法正确
- [ ] prometheus.yml 存在
- [ ] alerts.yml 存在
- [ ] Grafana 仪表盘 JSON 存在

### 镜像检查

- [ ] prom/prometheus:latest 已下载
- [ ] grafana/grafana:latest 已下载
- [ ] 镜像完整性验证通过

### 容器检查

- [ ] 容器启动成功
- [ ] 健康检查通过
- [ ] 端口映射正确
- [ ] 卷挂载正确

---

**下一步**: 按照"快速修复步骤"执行，如果仍然失败，请参考"常见错误排查清单"。

**文档版本**: 1.0  
**更新日期**: 2026-06-09
