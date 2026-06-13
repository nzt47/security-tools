# Kubernetes kubeconfig 解决方案

## 快速开始

### 1. 运行诊断
```powershell
.\test-kubeconfig.ps1
```

### 2. 运行演示
```powershell
.\demo-kubeconfig-fix.ps1
```

### 3. 交互式配置
```powershell
.\setup-kubeconfig.ps1
```

---

## 脚本说明

| 脚本 | 用途 |
|------|------|
| test-kubeconfig.ps1 | 诊断当前环境 |
| setup-kubeconfig.ps1 | 自动化配置 |
| demo-kubeconfig-fix.ps1 | 修复演示 |

---

## 文档列表

- **KUBECONFIG_COMPLETE_SOLUTION.md** - 完整解决方案
- **MANUAL_KUBECONFIG_SETUP.md** - 手动设置指南
- **QUICK_FIX_KUBECONFIG.md** - 快速修复
- **KUBECONFIG_TROUBLESHOOTING.md** - 故障排查
- **KUBECONFIG_SUMMARY.md** - 本次操作总结

---

## 快速命令

### 临时设置
```powershell
$env:KUBECONFIG = "$env:USERPROFILE\.kube\config"
```

### 永久设置
```powershell
[Environment]::SetEnvironmentVariable("KUBECONFIG", "$env:USERPROFILE\.kube\config", "User")
```

### 验证
```powershell
kubectl config get-contexts
```

---

## 当前状态

- ✅ kubectl 已安装 (v1.34.1)
- ✅ kubeconfig 文件已创建
- ✅ 环境变量已设置
- ⚠ 需要真实集群凭证

---

更多信息请查看 `KUBECONFIG_SUMMARY.md`
