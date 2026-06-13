# 快速替换示例配置 - 连接真实 Kubernetes 集群

## 🚀 最快方法（推荐）

### 如果你有云服务商集群

#### Azure AKS
```powershell
az aks get-credentials --resource-group <资源组> --name <集群名> --overwrite-existing
```

#### AWS EKS
```powershell
aws eks update-kubeconfig --name <集群名> --region <区域>
```

#### Google GKE
```powershell
gcloud container clusters get-credentials <集群名> --region <区域>
```

---

## 📋 如果你有现成的 kubeconfig 文件

### 使用自动化脚本
```powershell
# 1. 运行更新脚本
.\update-kubeconfig.ps1 -From "C:\path\to\your\real\kubeconfig" -Test
```

### 或者手动复制
```powershell
# 1. 备份当前配置
Copy-Item "C:\Users\Administrator\.kube\config" "C:\Users\Administrator\.kube\config.demo"

# 2. 复制你的真实配置
Copy-Item "C:\path\to\your\real\kubeconfig" "C:\Users\Administrator\.kube\config"

# 3. 验证
kubectl config get-contexts
kubectl cluster-info
```

---

## 🔧 如果你需要手动编辑配置

### 1. 先备份！
```powershell
Copy-Item "C:\Users\Administrator\.kube\config" "C:\Users\Administrator\.kube\config.demo"
```

### 2. 获取需要的信息
你需要从集群管理员处获取：
- API 服务器地址
- CA 证书（Base64 编码）
- 访问令牌

### 3. 编辑配置文件
打开 [C:\Users\Administrator\.kube\config](file:///C:/Users/Administrator/.kube/config) 并替换：

```yaml
apiVersion: v1
kind: Config
clusters:
- cluster:
    certificate-authority-data: <你的CA证书Base64>
    server: <你的API服务器地址>
  name: my-real-cluster
contexts:
- context:
    cluster: my-real-cluster
    namespace: default
    user: my-real-user
  name: my-real-context
current-context: my-real-context
users:
- name: my-real-user
  user:
    token: <你的访问令牌>
```

### 4. 测试连接
```powershell
kubectl cluster-info
kubectl get nodes
```

---

## 🛠️ 使用更新脚本（推荐）

我们创建了 `update-kubeconfig.ps1` 来简化这个过程：

### 运行脚本
```powershell
# 交互式菜单
.\update-kubeconfig.ps1

# 从文件更新
.\update-kubeconfig.ps1 -From "C:\path\to\kubeconfig" -Test

# 手动输入凭证
.\update-kubeconfig.ps1 -Server "https://api.my-cluster.com:6443" `
    -CAData "你的CA证书Base64" `
    -Token "你的访问令牌" `
    -Test
```

### 脚本功能
- ✅ 自动备份当前配置
- ✅ 从文件导入配置
- ✅ 手动输入凭证
- ✅ 测试集群连接
- ✅ 备份和恢复功能

---

## 📊 验证配置成功

运行以下命令确认连接：

```powershell
# 1. 查看上下文
kubectl config get-contexts

# 2. 测试集群信息
kubectl cluster-info

# 3. 查看节点
kubectl get nodes

# 4. 查看命名空间
kubectl get namespaces
```

---

## ⚠️ 常见问题

### 问题 1：连接失败
```
Error: unable to connect to the server: x509: certificate signed by unknown authority
```
**解决**：CA 证书不正确，请检查 certificate-authority-data

### 问题 2：认证失败
```
Error: Unauthorized
```
**解决**：令牌无效或已过期，获取新的令牌

### 问题 3：找不到服务器
```
Error: couldn't reach server
```
**解决**：检查网络连接和 API 服务器地址是否正确

---

## 📚 更多文档

- [REAL_KUBECONFIG_GUIDE.md](file:///c:/Users/Administrator/agent/REAL_KUBECONFIG_GUIDE.md) - 完整指南
- [update-kubeconfig.ps1](file:///c:/Users/Administrator/agent/update-kubeconfig.ps1) - 更新脚本
- [KUBECONFIG_COMPLETE_SOLUTION.md](file:///c:/Users/Administrator/agent/KUBECONFIG_COMPLETE_SOLUTION.md) - 完整解决方案

---

## 🎯 下一步

1. 获取真实集群的凭证
2. 使用 `update-kubeconfig.ps1` 更新配置
3. 运行 `kubectl cluster-info` 验证连接

**有问题？查看 `REAL_KUBECONFIG_GUIDE.md` 获取详细说明！**
