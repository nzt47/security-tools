# kubeconfig 配置指南

## 问题说明
"kubeconfig not found at" 错误表示 Kubernetes 客户端工具（kubectl、helm、k9s等）无法找到kubeconfig配置文件。

## kubeconfig 文件位置

### 默认位置（按优先级）
1. **环境变量**: `$KUBECONFIG` （优先级最高）
2. **用户目录**: `$HOME/.kube/config` (Linux/Mac) 或 `$USERPROFILE\.kube\config` (Windows)
3. **当前目录**: `./kubeconfig`

### Windows 典型路径
```
C:\Users\<用户名>\.kube\config
```

## 解决方案

### 1. 确认 kubeconfig 文件存在
```powershell
# 检查文件是否存在
Test-Path "$env:USERPROFILE\.kube\config"

# 查看 .kube 目录内容
Get-ChildItem "$env:USERPROFILE\.kube"
```

### 2. 设置 KUBECONFIG 环境变量
```powershell
# 临时设置（当前终端会话）
$env:KUBECONFIG = "C:\path\to\your\kubeconfig"

# 永久设置（用户级别）
[Environment]::SetEnvironmentVariable(
    "KUBECONFIG",
    "C:\path\to\your\kubeconfig",
    "User"
)

# 永久设置（系统级别，需要管理员权限）
[Environment]::SetEnvironmentVariable(
    "KUBECONFIG",
    "C:\path\to\your\kubeconfig",
    "Machine"
)
```

### 3. 从 Kubernetes 集群获取配置
```powershell
# 如果你能访问 master 节点
kubectl config view --raw > $env:USERPROFILE\.kube\config

# 或者指定集群
kubectl config get-contexts
kubectl config use-context <context-name>
```

### 4. 使用云提供商的配置
```powershell
# Azure AKS
az aks get-credentials --resource-group <RG_NAME> --name <AKS_NAME>

# AWS EKS
aws eks update-kubeconfig --name <CLUSTER_NAME> --region <REGION>

# Google GKE
gcloud container clusters get-credentials <CLUSTER_NAME> --region <REGION>
```

## kubeconfig 文件格式

标准的 kubeconfig 文件包含以下部分：

```yaml
apiVersion: v1
kind: Config
clusters:
  - name: my-cluster
    cluster:
      server: https://api.mycluster.com:6443
      certificate-authority-data: <BASE64_CA_DATA>
contexts:
  - name: my-context
    context:
      cluster: my-cluster
      user: my-user
current-context: my-context
users:
  - name: my-user
    user:
      token: <BEARER_TOKEN>
```

## 验证配置

```powershell
# 验证 kubectl 配置
kubectl config current-context
kubectl cluster-info

# 测试连接
kubectl get nodes
```

## 多集群配置

kubeconfig 支持多个集群配置：

```yaml
apiVersion: v1
kind: Config
clusters:
  - name: prod-cluster
    cluster:
      server: https://api.prod.com:6443
      certificate-authority-data: <PROD_CA>
  - name: dev-cluster
    cluster:
      server: https://api.dev.com:6443
      certificate-authority-data: <DEV_CA>
contexts:
  - name: prod
    context:
      cluster: prod-cluster
      user: prod-user
  - name: dev
    context:
      cluster: dev-cluster
      user: dev-user
current-context: prod
users:
  - name: prod-user
    user:
      token: <PROD_TOKEN>
  - name: dev-user
    user:
      token: <DEV_TOKEN>
```

## 常见错误

| 错误信息 | 原因 | 解决方案 |
|---------|------|---------|
| `kubeconfig not found at ~/.kube/config` | 文件不存在 | 创建或获取 kubeconfig 文件 |
| `error: no configuration has been provided` | 未设置配置 | 设置 KUBECONFIG 环境变量 |
| `Unable to connect to the server: x509` | 证书错误 | 检查或更新证书数据 |
| `error: user "xxx" was not found in the context` | 用户配置错误 | 检查 users 和 contexts 配置 |
| `The connection to the server was refused` | API Server 未运行 | 检查集群状态 |

## 安全建议

1. **不要提交 kubeconfig 到 Git** - 添加到 .gitignore
2. **使用令牌而非密码** - 更安全
3. **定期轮换证书** - 最佳安全实践
4. **限制文件权限** - 仅管理员可读写

## PowerShell 快捷命令

```powershell
# 快速设置 KUBECONFIG
function Set-KubeConfig {
    param([string]$Path = "$env:USERPROFILE\.kube\config")
    $env:KUBECONFIG = $Path
    Write-Host "KUBECONFIG set to: $Path" -ForegroundColor Green
}

# 快速切换上下文
function Use-KubeContext {
    param([string]$Context)
    kubectl config use-context $Context
    Write-Host "Switched to context: $Context" -ForegroundColor Green
}
```
