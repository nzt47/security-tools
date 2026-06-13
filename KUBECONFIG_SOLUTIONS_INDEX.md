# Kubernetes kubeconfig 解决方案索引

## 📚 文档总览

本目录包含完整的 Kubernetes kubeconfig "not found" 问题解决方案，适用于所有使用场景。

---

## 🚀 快速开始

### 立即解决？查看这里
- **[QUICK_FIX_KUBECONFIG.md](QUICK_FIX_KUBECONFIG.md)** ⭐ 推荐从这里开始
  - 5-15 分钟内解决问题
  - 包含所有快速配置方案
  - 提供云服务商快速配置命令

### 环境诊断
```powershell
# 先运行诊断
.\test-kubeconfig.ps1
```

---

## 📖 完整解决方案

### 按场景选择文档

| 你的场景 | 查看文档 |
|---------|---------|
| kubectl/helm 命令报错 | [KUBECONFIG_COMPLETE_SOLUTION.md](KUBECONFIG_COMPLETE_SOLUTION.md) |
| Python 应用连接 K8s | [KUBECONFIG_COMPLETE_SOLUTION.md](KUBECONFIG_COMPLETE_SOLUTION.md) |
| Java 应用连接 K8s | [KUBECONFIG_COMPLETE_SOLUTION.md](KUBECONFIG_COMPLETE_SOLUTION.md) |
| Go 应用连接 K8s | [KUBECONFIG_COMPLETE_SOLUTION.md](KUBECONFIG_COMPLETE_SOLUTION.md) |
| Kubernetes Operator | [KUBECONFIG_COMPLETE_SOLUTION.md](KUBECONFIG_COMPLETE_SOLUTION.md) |
| 本地开发环境 | [KUBECONFIG_COMPLETE_SOLUTION.md](KUBECONFIG_COMPLETE_SOLUTION.md) |

---

## 🛠️ 工具和脚本

### 自动化脚本

| 脚本 | 用途 | 使用场景 |
|------|------|---------|
| **[setup-kubeconfig.ps1](setup-kubeconfig.ps1)** | 自动化配置工具 | ⭐ 推荐使用 |
| **[test-kubeconfig.ps1](test-kubeconfig.ps1)** | 环境验证脚本 | 运行诊断和验证 |

### 示例文件

| 文件 | 用途 |
|------|------|
| **[kubeconfig.example](kubeconfig.example)** | kubeconfig 模板 | 了解配置格式 |
| **[KUBECONFIG_TROUBLESHOOTING.md](KUBECONFIG_TROUBLESHOOTING.md)** | 详细故障排查 | 深度故障排除 |

---

## 📋 内容目录

### [QUICK_FIX_KUBECONFIG.md](QUICK_FIX_KUBECONFIG.md)
**推荐：快速修复指南**
- ✅ 问题诊断结果
- ✅ 5分钟内解决方案
- ✅ 快速配置方案（Azure/AWS/GCP/Minikube/Kind）
- ✅ 验证和测试命令
- ✅ 常见问题解答

### [KUBECONFIG_COMPLETE_SOLUTION.md](KUBECONFIG_COMPLETE_SOLUTION.md)
**推荐：完整解决方案**
- ✅ kubectl/helm 场景
- ✅ 应用程序场景（Python/Java/Go）
- ✅ Kubernetes Operator 场景
- ✅ 本地开发环境（Docker Desktop/Minikube/Kind/K3s）
- ✅ 高级配置（多集群/安全配置）
- ✅ 完整故障排查流程
- ✅ 最佳实践

### [KUBECONFIG_TROUBLESHOOTING.md](KUBECONFIG_TROUBLESHOOTING.md)
**推荐：故障排查参考**
- ✅ kubeconfig 文件位置说明
- ✅ 环境变量配置
- ✅ PowerShell 快捷命令
- ✅ 常见错误和解决方案
- ✅ 多集群配置示例

---

## 🎯 使用建议

### 新手推荐流程
1. **先诊断**: 运行 `.\test-kubeconfig.ps1`
2. **快速修复**: 查看 [QUICK_FIX_KUBECONFIG.md](QUICK_FIX_KUBECONFIG.md)
3. **验证**: 使用测试脚本确认修复成功

### 开发者推荐流程
1. **完整了解**: 阅读 [KUBECONFIG_COMPLETE_SOLUTION.md](KUBECONFIG_COMPLETE_SOLUTION.md)
2. **选择场景**: 找到对应的解决方案
3. **应用代码示例**: 参考 Python/Java/Go 代码
4. **测试**: 使用测试脚本验证

### 运维推荐流程
1. **故障排查**: 查看 [KUBECONFIG_TROUBLESHOOTING.md](KUBECONFIG_TROUBLESHOOTING.md)
2. **自动化**: 使用 [setup-kubeconfig.ps1](setup-kubeconfig.ps1)
3. **配置多集群**: 参考高级配置部分
4. **安全加固**: 实施最佳实践

---

## 💡 快速命令参考

### 基本命令
```powershell
# 诊断
.\test-kubeconfig.ps1

# 配置
.\setup-kubeconfig.ps1

# 验证
kubectl config get-contexts
kubectl cluster-info

# 环境变量
$env:KUBECONFIG = "$env:USERPROFILE\.kube\config"
```

### 常用配置
```powershell
# Azure AKS
az aks get-credentials --resource-group <RG> --name <NAME> --overwrite

# AWS EKS
aws eks update-kubeconfig --name <NAME> --region <REGION>

# Google GKE
gcloud container clusters get-credentials <NAME> --region <REGION>

# Minikube
minikube start

# Kind
kind create cluster --name <NAME>
```

---

## 🔍 常见问题

### Q: kubeconfig 在哪里？
A: 默认位置：`$HOME/.kube/config`（Windows: `%USERPROFILE%\.kube\config`）

### Q: KUBECONFIG 环境变量是什么？
A: 指定 kubeconfig 文件路径的环境变量，优先级高于默认位置

### Q: 多个集群怎么办？
A: kubeconfig 支持多个集群配置，使用 `kubectl config use-context` 切换

### Q: kubeconfig 文件格式？
A: YAML 格式，包含 clusters、contexts、users 三部分

---

## 📞 获取帮助

1. **诊断**: 运行 `.\test-kubeconfig.ps1`
2. **快速修复**: 查看 [QUICK_FIX_KUBECONFIG.md](QUICK_FIX_KUBECONFIG.md)
3. **详细文档**: 阅读 [KUBECONFIG_COMPLETE_SOLUTION.md](KUBECONFIG_COMPLETE_SOLUTION.md)
4. **故障排查**: 参考 [KUBECONFIG_TROUBLESHOOTING.md](KUBECONFIG_TROUBLESHOOTING.md)

---

## 📊 环境检查结果

```
kubectl: ✅ v1.34.1 已安装
kubeconfig: ❌ 未找到
KUBECONFIG 环境变量: ❌ 未设置
Python kubernetes 库: ⚠️ 未安装
```

**建议操作**: 运行 `.\setup-kubeconfig.ps1` 开始配置

---

**创建日期**: 2026-06-01
**版本**: 1.0
**适用场景**: kubectl、helm、应用程序、本地开发、Kubernetes Operator
