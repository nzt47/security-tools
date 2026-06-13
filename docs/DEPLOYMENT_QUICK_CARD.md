# 🔄 云枢服务回滚 - 快速操作卡

> 打印此卡片贴于运维工位，紧急情况下 30 秒内完成回滚

---

## ⚠️ 何时需要回滚

| 现象 | 严重级别 | 动作 |
|------|----------|------|
| 服务启动后立即崩溃 | 🔴 Critical | 立即回滚全部 |
| 历史记忆全部丢失 | 🔴 Critical | 回滚数据 |
| API 大面积 500 错误 | 🔴 Critical | 立即回滚全部 |
| 新功能表现异常 | 🟡 Warning | 评估后回滚代码 |

---

## 🚀 一键回滚命令

```powershell
# 完整回滚（代码 + 数据）+ 自动重启
.\scripts\rollback.ps1

# 仅回滚代码（不碰数据）
.\scripts\rollback.ps1 -Target code

# 仅回滚数据（不碰代码）
.\scripts\rollback.ps1 -Target data

# 查看有哪些备份可用
.\scripts\rollback.ps1 -ListBackups
```

---

## 📋 手动回滚步骤（脚本不可用时）

### 第 1 步：停止服务
```powershell
# 找到并停止 Python 进程
Get-Process python | Where-Object { $_.CommandLine -like "*app_server*" } | Stop-Process -Force
```

### 第 2 步：恢复文件
```powershell
# 恢复代码
Copy-Item app_server.py.bak_* app_server.py -Force

# 恢复数据（如需要）
Copy-Item data\messages.jsonl.bak_* data\messages.jsonl -Force
```

### 第 3 步：重启服务
```powershell
$env:YUNSHU_FEATURE_SANDBOX='false'
python app_server.py
```

### 第 4 步：验证
```powershell
# 检查服务是否正常
curl http://localhost:5678/api/health

# 检查历史是否恢复
curl http://localhost:5678/api/history
```

---

## 📞 紧急联系

| 角色 | 联系方式 |
|------|----------|
| 运维值班 | 内部通讯工具 #yunshu-ops |
| 开发支持 | 内部通讯工具 #yunshu-dev |
| 回滚日志 | `logs/rollback_*.log` |

---

## 💡 注意事项

1. **回滚前脚本会自动备份当前版本**（`.pre_rollback_*`），不会丢失当前状态
2. **默认会交互式确认**，加 `-RestartService $false` 可跳过重启
3. **备份文件命名格式**：`*.bak_YYYYMMDD` 或 `*.bak_YYYYMMDD_HHmmss`
4. **如果没有任何备份**，回滚脚本会提示并允许取消

---

*最后更新: 2026-06-09 | 版本: 1.0*
