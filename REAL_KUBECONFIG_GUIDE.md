# 替换示例配置以连接真实 Kubernetes 集群

## 概述

当前你有一个示例 kubeconfig 文件位于 [C:\Users\Administrator\.kube\config](file:///C:/Users/Administrator/.kube/config)，但需要替换为真实集群的凭证才能连接。

---

## 你需要获取的信息

### 1. API 服务器地址
- `server`: Kubernetes API 服务器的 HTTPS URL
- 例如：`https://api.my-cluster.com:6443` 或 `https://192.168.1.100:6443`

### 2. CA 证书数据
- `certificate-authority-data`: CA 证书的 Base64 编码
- 这是验证集群身份的根证书

### 3. 认证令牌或客户端证书
- **令牌认证**（更常见）：
  - `token`: 认证令牌（Bearer Token）
- **客户端证书认证**：
  - `client-certificate-data`: 客户端证书的 Base64 编码
  - `client-key-data`: 客户端私钥的 Base64 编码

---

## 方法 1：从云服务商获取（推荐）

### Azure AKS
```powershell
# 1. 登录 Azure
az login

# 2. 获取集群凭证（自动配置 kubeconfig）
az aks get-credentials `
    --resource-group <你的资源组> `
    --name <你的集群名称> `
    --overwrite-existing
```

### AWS EKS
```powershell
# 1. 配置 AWS CLI
aws configure

# 2. 更新 kubeconfig
aws eks update-kubeconfig `
    --name <你的集群名称> `
    --region <区域>
```

### Google GKE
```powershell
# 1. 登录 Google Cloud
gcloud auth login

# 2. 获取凭证
gcloud container clusters get-credentials <集群名称> `
    --region <区域> `
    --project <项目ID>
```

---

## 方法 2：从现有 kubeconfig 文件复制

如果你已经有其他地方的 kubeconfig，可以直接替换：

```powershell
# 1. 备份当前配置
Copy-Item "C:\Users\Administrator\.kube\config" "C:\Users\Administrator\.kube\config.backup"

# 2. 复制新配置
Copy-Item "C:\path\to\your\real\kubeconfig" "C:\Users\Administrator\.kube\config"

# 3. 验证
kubectl config get-contexts
```

---

## 方法 3：手动编辑配置

### 步骤 1：获取真实凭证

#### 从现有集群导出（有 kubectl 访问权限）
```powershell
# 查看当前配置
kubectl config view --raw
```

#### 从管理员处获取
要求集群管理员提供：
- API 服务器地址
- CA 证书
- 访问令牌

### 步骤 2：创建新配置

复制以下模板到新文件 `real-kubeconfig.yaml`：

```yaml
apiVersion: v1
kind: Config
clusters:
- cluster:
    certificate-authority-data: <替换为你的CA证书Base64编码>
    server: <替换为API服务器地址>
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
    token: <替换为你的令牌>
```

### 步骤 3：替换占位符

| 占位符 | 替换为 | 示例 |
|--------|--------|------|
| `<替换为你的CA证书Base64编码>` | CA 证书的 Base64 字符串 | `LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0t...` |
| `<替换为API服务器地址>` | API 服务器 URL | `https://api.my-cluster.com:6443` |
| `<替换为你的令牌>` | 认证令牌 | `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...` |

### 步骤 4：应用配置

```powershell
# 1. 备份当前配置
Copy-Item "C:\Users\Administrator\.kube\config" "C:\Users\Administrator\.kube\config.demo"

# 2. 替换配置
Copy-Item "real-kubeconfig.yaml" "C:\Users\Administrator\.kube\config"

# 3. 验证
kubectl config get-contexts
```

---

## 方法 4：使用 ServiceAccount 创建 Token（如果你能访问集群）

### 如果你可以访问集群，通过以下步骤创建新的访问令牌：

```yaml
# 1. 创建 ServiceAccount
apiVersion: v1
kind: ServiceAccount
metadata:
  name: my-admin-user
  namespace: kube-system

---

# 2. 绑定 ClusterRole
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: my-admin-binding
subjects:
- kind: ServiceAccount
  name: my-admin-user
  namespace: kube-system
roleRef:
  kind: ClusterRole
  name: cluster-admin
  apiGroup: rbac.authorization.k8s.io
```

```powershell
# 应用配置
kubectl apply -f service-account.yaml

# 获取 token（K8s 1.24+ 需要手动创建 Secret）
kubectl create token my-admin-user -n kube-system --duration=8760h
```

---

## 方法 5：从 kubeadm 集群获取

如果你是通过 kubeadm 部署的集群：

```powershell
# 在 master 节点上执行
ssh root@master-node

# 查看 admin.conf
cat /etc/kubernetes/admin.conf

# 复制到本地
scp root@master-node:/etc/kubernetes/admin.conf ~/.kube/config
```

---

## 自动化脚本

我为你创建了一个自动化脚本 `update-kubeconfig.ps1`，可以帮助你：

1. 从不同来源获取配置
2. 验证和测试连接
3. 备份和恢复配置

让我为你创建这个脚本：
