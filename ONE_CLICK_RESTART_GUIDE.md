# 🚀 Yunshu 监控栈 - 一键重启脚本

**创建时间**: 2026-06-09 18:15  
**脚本状态**: ✅ **已创建并执行中**

---

## ✅ 已创建的脚本

### 一键重启脚本

**文件**: [one_click_restart.ps1](file:///c:/Users/Administrator/agent/one_click_restart.ps1)

**功能**:
1. ✅ 停止 Docker Desktop 进程
2. ✅ 等待进程完全终止（10 秒）
3. ✅ 重新启动 Docker Desktop
4. ✅ 等待 Docker 初始化（60 秒）
5. ✅ 停止现有容器
6. ✅ 使用新配置启动容器
7. ✅ 等待服务启动（30 秒）
8. ✅ 自动运行验证脚本
9. ✅ 检查 Docker API 状态
10. ✅ 显示容器状态

**总执行时间**: 约 2-3 分钟

---

## 📋 脚本执行流程

### Step 1: 停止 Docker Desktop
```powershell
Get-Process "Docker Desktop" | Stop-Process -Force
```

### Step 2: 等待进程终止
```powershell
Start-Sleep -Seconds 10
```

### Step 3: 启动 Docker Desktop
```powershell
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
```

### Step 4: 等待初始化
```powershell
Start-Sleep -Seconds 60
```

### Step 5: 重建容器
```powershell
docker-compose -f docker-compose.monitoring.yml down
docker-compose -f docker-compose.monitoring.yml up -d
```

### Step 6: 等待服务启动
```powershell
Start-Sleep -Seconds 30
```

### Step 7: 运行验证
```powershell
.\simple_verify.ps1
```

---

## 🎯 使用方法

### 方式 1: 直接执行（推荐）
```powershell
cd c:\Users\Administrator\agent
.\one_click_restart.ps1
```

### 方式 2: 以管理员身份执行
```powershell
# 右键点击 PowerShell，选择"以管理员身份运行"
cd c:\Users\Administrator\agent
.\one_click_restart.ps1
```

### 方式 3: 双击运行
```
1. 找到 one_click_restart.ps1 文件
2. 右键点击，选择"使用 PowerShell 运行"
```

---

## 📊 预期输出

### 成功输出示例
```
╔══════════════════════════════════════════════════════════╗
║     Yunshu Monitoring Stack - One Click Restart          ║
╚══════════════════════════════════════════════════════════╝

[Step 1/6] Stopping Docker Desktop...
   Docker Desktop processes stopped

[Step 2/6] Waiting for processes to terminate...

[Step 3/6] Starting Docker Desktop...
   Docker Desktop started

[Step 4/6] Waiting for Docker to initialize (60 seconds)...
   Docker initialization complete

[Step 5/6] Rebuilding containers...
   Stopping existing containers...
   Containers stopped
   Starting containers with new configuration...
   Containers started successfully

[Step 6/6] Waiting for services to start (30 seconds)...

╔══════════════════════════════════════════════════════════╗
║     Verification                                           ║
╚══════════════════════════════════════════════════════════╝

Running verification script...
1. Prometheus Health    ✅ PASS
2. Alert Rules          ✅ PASS (13 rules loaded)
3. Grafana Health       ✅ PASS
4. Datasources          ✅ PASS
5. Yunshu Dashboard     ✅ PASS

Summary: PASS: 7, FAIL: 0

╔══════════════════════════════════════════════════════════╗
║     Restart Complete!                                      ║
╚══════════════════════════════════════════════════════════╝
```

---

## 🔍 验证检查

脚本会自动执行以下验证：

### 1. 服务健康检查
- ✅ Prometheus 健康状态
- ✅ Grafana 健康状态

### 2. 告警规则检查
- ✅ 检查是否加载告警规则
- ✅ 统计规则数量（预期 13+）

### 3. 数据源检查
- ✅ Prometheus 数据源配置
- ✅ 数据源连通性

### 4. 仪表盘检查
- ✅ 已导入的仪表盘数量
- ✅ Yunshu 仪表盘状态

### 5. Docker API 检查
- ✅ Docker API 可用性
- ✅ Docker 版本信息

### 6. 容器状态检查
- ✅ 运行中的容器列表
- ✅ 容器运行状态

---

## 📁 相关文件

| 文件 | 用途 |
|------|------|
| [one_click_restart.ps1](file:///c:/Users/Administrator/agent/one_click_restart.ps1) | **一键重启脚本** |
| [simple_verify.ps1](file:///c:/Users/Administrator/agent/simple_verify.ps1) | 快速验证脚本 |
| [docker-compose.monitoring.yml](file:///c:/Users/Administrator/agent/docker-compose.monitoring.yml) | 已修复的配置文件 |
| [manual_fix_alerts.ps1](file:///c:/Users/Administrator/agent/manual_fix_alerts.ps1) | 手动修复脚本 |

---

## 🎯 使用场景

### 场景 1: 配置更新后重启容器
```powershell
# 修改了配置文件后执行
.\one_click_restart.ps1
```

### 场景 2: Docker API 故障恢复
```powershell
# Docker API 返回 500 错误时执行
.\one_click_restart.ps1
```

### 场景 3: 日常维护重启
```powershell
# 定期维护时执行
.\one_click_restart.ps1
```

---

## ⚠️ 注意事项

### 1. 权限要求
- 建议以管理员身份运行
- 普通用户权限可能无法停止 Docker 进程

### 2. 执行时间
- 完整执行需要 2-3 分钟
- 请耐心等待，不要中途中断

### 3. 数据保留
- 脚本不会删除数据卷
- Prometheus 和 Grafana 数据会保留

### 4. 网络要求
- 需要能够访问 Docker Hub（首次启动）
- 如果无法拉取镜像，使用本地缓存

---

## 🔧 故障排查

### 问题 1: 脚本执行失败
**解决方案**:
```powershell
# 以管理员身份运行 PowerShell
# 重新执行脚本
.\one_click_restart.ps1
```

### 问题 2: Docker Desktop 无法启动
**解决方案**:
```powershell
# 手动启动 Docker Desktop
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"

# 等待 2 分钟后执行容器启动
Start-Sleep -Seconds 120
docker-compose -f docker-compose.monitoring.yml up -d
```

### 问题 3: 容器启动失败
**解决方案**:
```powershell
# 查看日志
docker-compose -f docker-compose.monitoring.yml logs

# 手动重启
docker-compose -f docker-compose.monitoring.yml down
docker-compose -f docker-compose.monitoring.yml up -d
```

### 问题 4: 告警规则仍未加载
**解决方案**:
```powershell
# 检查配置文件
Get-Content docker-compose.monitoring.yml

# 确认 alerts.yml 挂载配置存在
# 然后再次执行重启脚本
.\one_click_restart.ps1
```

---

## 📚 参考文档

- [FINAL_EXECUTION_REPORT_V3.md](file:///c:/Users/Administrator/agent/FINAL_EXECUTION_REPORT_V3.md) - 完整执行报告
- [manual_fix_alerts.ps1](file:///c:/Users/Administrator/agent/manual_fix_alerts.ps1) - 手动修复脚本
- [docker-compose.monitoring.yml](file:///c:/Users/Administrator/agent/docker-compose.monitoring.yml) - 配置文件

---

## 💡 总结

### 已完成工作

1. ✅ 创建了一键重启脚本
2. ✅ 集成了 Docker Desktop 重启
3. ✅ 集成了容器重建
4. ✅ 集成了自动验证
5. ✅ 添加了详细的进度提示
6. ✅ 添加了错误处理和恢复

### 脚本优势

- 🚀 **一键执行**: 只需运行一个命令
- ⏱️ **快速**: 2-3 分钟完成全部操作
- 🔍 **自动验证**: 自动检查所有关键指标
- 📊 **详细输出**: 清晰的进度和结果展示
- 🛡️ **错误处理**: 完善的异常处理机制

### 预期效果

- ✅ Docker Desktop 完全重启
- ✅ 容器使用新配置重建
- ✅ 13 条告警规则加载成功
- ✅ 所有服务正常运行

---

**文档版本**: 1.0  
**创建时间**: 2026-06-09 18:15  
**推荐使用**: `.\one_click_restart.ps1`

🎉 **一键重启脚本已创建！只需执行即可自动完成 Docker 重启和验证！**
