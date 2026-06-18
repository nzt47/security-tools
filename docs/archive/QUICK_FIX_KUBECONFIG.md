# kubeconfig "not found" 快速修复指南

## 问题诊断结果

```
✓ kubectl 已安装: v1.34.1
✗ kubeconfig 文件不存在
✗ KUBECONFIG 环境变量未设置
```

## 快速解决方案（5分钟内解决）

### 方案 1：使用自动化脚本（推荐）

```powershell
# 运行自动化配置脚本
.\setup-kubeconfig.ps1
```

脚本提供：
- ✓ 交互式菜单
- ✓ Azure AKS 配置
- ✓ AWS EKS 配置
- ✓ Google GKE 配置
- ✓ Minikube 配置
- ✓ Kind 配置
- ✓ 配置文件导入
- ✓ 配置验证

### 方案 2：手动快速配置

```powershell
# 1. 创建 .kube 目录
New-Item -ItemType Directory -Path "$env:USERPROFILE\.kube" -Force

# 2. 设置 KUBECONFIG 环境变量（永久）
[Environment]::SetEnvironmentVariable(
    "KUBECONFIG",
    "$env:USERPROFILE\.kube\config",
    "User"
)

# 3. 创建示例 kubeconfig（使用你自己的配置替换）
$config = @"
apiVersion: v1
kind: Config
clusters:
- cluster:
    certificate-authority-data: YOUR_CA_DATA
    server: https://YOUR_API_SERVER:6443
  name: my-cluster
contexts:
- context:
    cluster: my-cluster
    user: my-user
  name: my-cluster
current-context: my-cluster
users:
- name: my-user
  user:
    token: YOUR_TOKEN
"@

# 4. 保存配置
Set-Content -Path "$env:USERPROFILE\.kube\config" -Value $config

# 5. 验证配置
kubectl config get-contexts
```

### 方案 3：云服务商快速配置

#### Azure AKS
```powershell
# 安装 Azure CLI
choco install azure-cli

# 登录
az login

# 获取 AKS 凭证
az aks get-credentials `
    --resource-group <你的资源组> `
    --name <你的集群名> `
    --overwrite-existing

# 验证
kubectl config current-context
```

#### AWS EKS
```powershell
# 安装 AWS CLI
choco install awscli

# 配置 AWS
aws configure

# 获取 EKS 凭证
aws eks update-kubeconfig `
    --name <你的集群名> `
    --region <区域>

# 验证
kubectl config current-context
```

#### Google GKE
```powershell
# 安装 Google Cloud SDK
choco install googlecloudsdk

# 初始化
gcloud init

# 获取 GKE 凭证
gcloud container clusters get-credentials <集群名> --region <区域>

# 验证
kubectl config current-context
```

### 方案 4：本地开发环境

#### Docker Desktop
```powershell
# 1. 启用 Kubernetes
# Docker Desktop -> Settings -> Kubernetes -> Enable Kubernetes

# 2. 自动配置
# Docker Desktop 会自动配置 ~/.kube/config

# 3. 验证
kubectl config get-contexts
```

#### Minikube
```powershell
# 1. 安装
choco install minikube

# 2. 启动
minikube start

# 3. Minikube 自动配置 kubeconfig

# 4. 验证
kubectl config current-context
# 应该显示 minikube
```

#### Kind
```powershell
# 1. 安装
choco install kind

# 2. 创建集群
kind create cluster --name my-cluster

# 3. Kind 自动更新 kubeconfig

# 4. 验证
kubectl config current-context
# 应该显示 kind-my-cluster
```

## 验证配置（必须执行）

```powershell
# 1. 设置环境变量（当前会话）
$env:KUBECONFIG = "$env:USERPROFILE\.kube\config"

# 2. 验证上下文
kubectl config get-contexts

# 3. 测试集群连接
kubectl cluster-info

# 4. 查看节点
kubectl get nodes

# 5. 测试部署
kubectl run nginx --image=nginx
kubectl get pods
```

## 测试脚本

我已经为你创建了完整的测试脚本：

```powershell
# 运行验证测试
.\test-kubeconfig.ps1
```

测试脚本会检查：
- ✓ kubectl 安装状态
- ✓ kubeconfig 文件存在性
- ✓ kubectl 上下文配置
- ✓ 集群连接状态
- ✓ Python kubernetes 库
- ✓ 本地 Kubernetes 环境

## 常见问题

### Q: 仍然报 "kubeconfig not found" 错误？

A: 检查以下内容：
```powershell
# 1. 确认文件存在
Test-Path "$env:USERPROFILE\.kube\config"

# 2. 确认环境变量
$env:KUBECONFIG

# 3. 如果环境变量为空，设置它
$env:KUBECONFIG = "$env:USERPROFILE\.kube\config"

# 4. 再次测试
kubectl cluster-info
```

### Q: kubectl 命令不存在？

A: 安装 kubectl：
```powershell
# 方法 1: Chocolatey
choco install kubernetes-cli

# 方法 2: Scoop
scoop install kubectl

# 方法 3: 手动安装
# 下载: https://kubernetes.io/docs/tasks/tools/install-kubectl/
```

### Q: 集群连接失败？

A: 检查：
```powershell
# 1. API Server 地址是否正确
kubectl config view

# 2. 证书是否有效
# 3. 网络是否可达
Test-NetConnection api.your-cluster.com -Port 6443

# 4. 认证信息是否过期
# 5. 重新获取配置
```

## 资源链接

| 资源 | 链接/命令 |
|------|----------|
| 完整文档 | [KUBECONFIG_COMPLETE_SOLUTION.md](KUBECONFIG_COMPLETE_SOLUTION.md) |
| 配置脚本 | [setup-kubeconfig.ps1](setup-kubeconfig.ps1) |
| 测试脚本 | [test-kubeconfig.ps1](test-kubeconfig.ps1) |
| 示例配置 | [kubeconfig.example](kubeconfig.example) |
| 故障排查 | [KUBECONFIG_TROUBLESHOOTING.md](KUBECONFIG_TROUBLESHOOTING.md) |

## 下一步

1. ✓ 运行 `.\test-kubeconfig.ps1` 验证环境
2. ✓ 根据场景选择合适的配置方案
3. ✓ 运行 `.\setup-kubeconfig.ps1` 进行自动化配置
4. ✓ 测试 kubectl 命令

---

**快速修复总时间**: 5-15 分钟
**预期结果**: kubeconfig 正确配置，kubectl 正常工作
