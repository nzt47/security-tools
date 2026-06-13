# Kubernetes kubeconfig "not found" 完整解决方案

## 问题概述

"kubeconfig not found at" 错误是 Kubernetes 环境中非常常见的问题，通常发生在：

- kubectl、helm、k9s 等 CLI 工具无法找到配置文件
- 应用程序使用 Kubernetes 客户端库时缺少配置
- 多集群切换时配置路径错误
- 新安装的 Kubernetes 工具尚未配置

---

## 目录

1. [快速诊断](#快速诊断)
2. [kubectlhelm-场景](#kubectlhelm-场景)
3. [应用程序场景](#应用程序场景)
4. [Kubernetes-Operator场景](#kubernetes-operator场景)
5. [本地开发环境](#本地开发环境)
6. [自动化配置脚本](#自动化配置脚本)
7. [高级配置](#高级配置)
8. [故障排查](#故障排查)

---

## 快速诊断

运行以下命令快速诊断问题：

```powershell
# 检查 kubeconfig 是否存在
Test-Path "$env:USERPROFILE\.kube\config"

# 检查 KUBECONFIG 环境变量
$env:KUBECONFIG

# 检查 kubectl 是否安装
kubectl version --client

# 查看所有可能的 kubeconfig 位置
$paths = @(
    $env:KUBECONFIG,
    "$env:USERPROFILE\.kube\config",
    "$env:HOME\.kube\config",
    ".\kubeconfig",
    "$env:USERPROFILE\.kube\config-eks",
    "$env:USERPROFILE\.kube\config-aks"
)

foreach ($p in $paths) {
    if ($p -and (Test-Path $p)) {
        Write-Host "✓ Found: $p" -ForegroundColor Green
    }
}
```

---

## kubectl/helm 场景

### 问题描述
kubectl 或 helm 命令执行时报错 "kubeconfig not found at ~/.kube/config"

### 解决方案

#### 方案 1：手动创建 kubeconfig

如果已经有集群访问凭证，手动创建配置文件：

```powershell
# 1. 创建 .kube 目录
New-Item -ItemType Directory -Path "$env:USERPROFILE\.kube" -Force

# 2. 创建 kubeconfig 文件
$configPath = "$env:USERPROFILE\.kube\config"

$config = @"
apiVersion: v1
kind: Config
clusters:
- cluster:
    certificate-authority-data: <YOUR_CA_DATA>
    server: https://<YOUR_API_SERVER>:<PORT>
  name: my-cluster
contexts:
- context:
    cluster: my-cluster
    namespace: default
    user: my-user
  name: my-cluster
current-context: my-cluster
users:
- name: my-user
  user:
    token: <YOUR_TOKEN>
"@

Set-Content -Path $configPath -Value $config

# 3. 设置环境变量（永久）
[Environment]::SetEnvironmentVariable(
    "KUBECONFIG",
    $configPath,
    "User"
)

# 4. 验证
kubectl config get-contexts
```

#### 方案 2：从主节点导出配置

如果你有 Kubernetes master 节点的 SSH 访问权限：

```powershell
# 在 master 节点上执行
ssh user@master-node
sudo cat /etc/kubernetes/admin.conf

# 或直接导出
kubectl config view --raw > ~/kubeconfig
```

#### 方案 3：使用 kubeadm 重新生成（高可用集群）

```powershell
# 在 master 节点上
kubeadm init phase kubeconfig admin --kubeconfig-dir /etc/kubernetes/

# 复制配置到本地
scp user@master:/etc/kubernetes/admin.conf $env:USERPROFILE\.kube\config
```

### Helm 特定配置

```powershell
# 方法 1：使用 KUBECONFIG 环境变量
$env:KUBECONFIG = "$env:USERPROFILE\.kube\config"

# 方法 2：命令行指定
helm list --kubeconfig "$env:USERPROFILE\.kube\config"

# 方法 3：Helm 3 自动使用 kubectl 配置
helm repo update  # 会自动使用当前 kubectl 配置
```

---

## 应用程序场景

### Python 应用（kubernetes 库）

#### 问题
Python 程序使用 `kubernetes` 库时无法连接集群

#### 解决方案

```python
# 方法 1：使用 kubernetes.client.Configuration
from kubernetes import client, config

# 加载 kubeconfig
config.load_kube_config(config_file="/path/to/kubeconfig")

# 或使用默认位置
config.load_kube_config()  # 自动查找 ~/.kube/config 或 KUBECONFIG

# 使用配置
v1 = client.CoreV1Api()
print(v1.list_namespace())

# 方法 2：使用环境变量
import os
os.environ['KUBECONFIG'] = '/path/to/your/kubeconfig'

# 方法 3：在集群内运行时使用 ServiceAccount
config.load_incluster_config()  # 自动使用 /var/run/secrets/kubernetes.io/serviceaccount/token
```

#### 完整示例

```python
#!/usr/bin/env python3
"""
Kubernetes Python 客户端完整示例
处理 kubeconfig not found 错误
"""

import os
import sys
from kubernetes import client, config
from kubernetes.client.rest import ApiException

def setup_kubernetes_client():
    """智能加载 Kubernetes 配置"""

    # 优先级 1：环境变量
    kubeconfig_path = os.environ.get('KUBECONFIG')

    # 优先级 2：默认位置
    default_paths = [
        os.path.expanduser('~/.kube/config'),
        os.path.join(os.getcwd(), 'kubeconfig'),
        '/etc/kubernetes/admin.conf'
    ]

    # 查找可用配置
    if not kubeconfig_path:
        for path in default_paths:
            if os.path.exists(path):
                kubeconfig_path = path
                break

    if not kubeconfig_path:
        print("错误：未找到 kubeconfig 文件")
        print("请设置 KUBECONFIG 环境变量或确保 ~/.kube/config 存在")
        sys.exit(1)

    try:
        # 尝试加载 kubeconfig
        config.load_kube_config(config_file=kubeconfig_path)
        print(f"✓ 成功加载 kubeconfig: {kubeconfig_path}")
        return True
    except Exception as e:
        print(f"✗ kubeconfig 加载失败: {e}")
        return False

def get_current_context():
    """获取当前上下文"""
    contexts, active_context = config.list_kube_config_contexts()
    if active_context:
        print(f"当前上下文: {active_context['name']}")
        print(f"集群: {active_context['context']['cluster']}")
        print(f"用户: {active_context['context']['user']}")
        return active_context['name']
    return None

def list_namespaces():
    """列出所有命名空间"""
    v1 = client.CoreV1Api()
    try:
        namespaces = v1.list_namespace()
        print("\n命名空间列表:")
        for ns in namespaces.items:
            print(f"  - {ns.metadata.name}")
        return namespaces
    except ApiException as e:
        print(f"错误：无法获取命名空间: {e}")
        return None

def list_pods(namespace='default'):
    """列出指定命名空间的 Pod"""
    v1 = client.CoreV1Api()
    try:
        pods = v1.list_namespaced_pod(namespace)
        print(f"\n命名空间 {namespace} 中的 Pod:")
        for pod in pods.items:
            print(f"  - {pod.metadata.name} ({pod.status.phase})")
        return pods
    except ApiException as e:
        print(f"错误：无法获取 Pod 列表: {e}")
        return None

if __name__ == '__main__':
    # 1. 设置客户端
    if not setup_kubernetes_client():
        sys.exit(1)

    # 2. 获取当前上下文
    get_current_context()

    # 3. 测试 API
    list_namespaces()
    list_pods()
```

### Java 应用（Maven/Gradle）

#### Maven 配置

```xml
<!-- pom.xml -->
<dependencies>
    <dependency>
        <groupId>io.kubernetes</groupId>
        <artifactId>client-java</artifactId>
        <version>14.0.0</version>
    </dependency>
</dependencies>
```

```java
// KubernetesClientExample.java
package com.example;

import io.kubernetes.client.openapi.ApiClient;
import io.kubernetes.client.openapi.Configuration;
import io.kubernetes.client.openapi.apis.CoreV1Api;
import io.kubernetes.client.openapi.models.V1NamespaceList;
import io.kubernetes.client.util.Config;

import java.io.File;
import java.io.IOException;

public class KubernetesClientExample {

    public static ApiClient createClient() throws IOException {
        // 方法 1：从 kubeconfig 文件加载
        String kubeconfigPath = System.getenv("KUBECONFIG");
        if (kubeconfigPath == null) {
            kubeconfigPath = System.getProperty("user.home") + "/.kube/config";
        }

        File kubeconfig = new File(kubeconfigPath);
        if (!kubeconfig.exists()) {
            throw new IOException("Kubeconfig not found at: " + kubeconfigPath);
        }

        ApiClient client = Config.fromConfig(kubeconfig);
        Configuration.setDefaultApiClient(client);
        return client;
    }

    public static void main(String[] args) throws IOException {
        ApiClient client = createClient();
        CoreV1Api api = new CoreV1Api(client);

        // 列出所有命名空间
        V1NamespaceList namespaceList = api.listNamespace();
        System.out.println("命名空间列表:");
        namespaceList.getItems().forEach(ns ->
            System.out.println("  - " + ns.getMetadata().getName())
        );
    }
}
```

#### Spring Boot 集成

```yaml
# application.yml
kubernetes:
  master-url: ${KUBERNETES_SERVICE_HOST}:${KUBERNETES_SERVICE_PORT}
  ca-cert-path: /var/run/secrets/kubernetes.io/serviceaccount/ca.crt
  namespace-path: /var/run/secrets/kubernetes.io/serviceaccount/namespace
  oAuth-token-path: /var/run/secrets/kubernetes.io/serviceaccount/token
```

### Go 应用

```go
package main

import (
    "fmt"
    "os"

    "k8s.io/client-go/kubernetes"
    "k8s.io/client-go/tools/clientcmd"
)

func main() {
    // 获取 kubeconfig 路径
    kubeconfig := os.Getenv("KUBECONFIG")
    if kubeconfig == "" {
        kubeconfig = os.Getenv("HOME") + "/.kube/config"
    }

    // 加载配置
    config, err := clientcmd.BuildConfigFromFlags("", kubeconfig)
    if err != nil {
        fmt.Printf("Error loading kubeconfig: %v\n", err)
        os.Exit(1)
    }

    // 创建客户端
    clientset, err := kubernetes.NewForConfig(config)
    if err != nil {
        fmt.Printf("Error creating client: %v\n", err)
        os.Exit(1)
    }

    // 列出命名空间
    namespaces, err := clientset.CoreV1().Namespaces().List(nil)
    if err != nil {
        fmt.Printf("Error listing namespaces: %v\n", err)
        os.Exit(1)
    }

    fmt.Println("命名空间列表:")
    for _, ns := range namespaces.Items {
        fmt.Printf("  - %s\n", ns.Name)
    }
}
```

---

## Kubernetes Operator 场景

### 问题描述
Operator 或 Controller 无法访问集群，报错 kubeconfig not found

### 解决方案

#### 方案 1：使用 ServiceAccount（推荐）

```yaml
# 创建 ServiceAccount
apiVersion: v1
kind: ServiceAccount
metadata:
  name: my-operator
  namespace: operators

---
# 创建 Role
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: my-operator
  namespace: operators
rules:
- apiGroups: [""]
  resources: ["pods", "services", "configmaps"]
  verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]

---
# 绑定 Role
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: my-operator
  namespace: operators
subjects:
- kind: ServiceAccount
  name: my-operator
  namespace: operators
roleRef:
  kind: Role
  name: my-operator
  apiGroup: rbac.authorization.k8s.io
```

#### 方案 2：使用外部 kubeconfig

```yaml
# ConfigMap 存储 kubeconfig
apiVersion: v1
kind: ConfigMap
metadata:
  name: kubeconfig
  namespace: operators
data:
  config: |
    apiVersion: v1
    kind: Config
    clusters:
    - cluster:
        certificate-authority-data: <CA_DATA>
        server: https://api.cluster.com:6443
      name: prod-cluster
    contexts:
    - context:
        cluster: prod-cluster
        namespace: operators
        user: operator-user
      name: operator-context
    current-context: operator-context
    users:
    - name: operator-user
      user:
        token: <TOKEN>
```

#### Python Operator 示例

```python
from kubernetes import client, config
from kubernetes.client.rest import ApiException
import os

class OperatorClient:
    def __init__(self, use_incluster=True):
        if use_incluster and os.path.exists('/var/run/secrets/kubernetes.io/serviceaccount'):
            # 在集群内运行
            config.load_incluster_config()
        else:
            # 外部运行，使用 kubeconfig
            kubeconfig = os.getenv('KUBECONFIG') or os.path.expanduser('~/.kube/config')
            if not os.path.exists(kubeconfig):
                raise FileNotFoundError(f"Kubeconfig not found at: {kubeconfig}")
            config.load_kube_config(config_file=kubeconfig)

        self.core_api = client.CoreV1Api()
        self.apps_api = client.AppsV1Api()

    def watch_pods(self, namespace='default'):
        """监控 Pod 变化"""
        try:
            stream = watch.stream(
                self.core_api.list_namespaced_pod,
                namespace=namespace,
                timeout_seconds=300
            )
            for event in stream:
                print(f"事件: {event['type']}, Pod: {event['object'].metadata.name}")
        except ApiException as e:
            print(f"API 错误: {e}")
```

---

## 本地开发环境

### Docker Desktop (Windows)

```powershell
# 1. 启用 Kubernetes
# 设置 -> Kubernetes -> 启用 Kubernetes

# 2. 自动配置（Docker Desktop 会自动设置）
# 验证
kubectl config get-contexts

# 应该看到 docker-desktop 上下文

# 3. 如果配置丢失，手动恢复
$env:KUBECONFIG = "$env:USERPROFILE\.kube\config"
if (-not (Test-Path $env:KUBECONFIG)) {
    docker-desktop get-kubeconfig > $env:KUBECONFIG
}
```

### Minikube

```powershell
# 1. 安装 Minikube
choco install minikube

# 2. 启动 Minikube
minikube start

# 3. Minikube 自动配置 ~/.kube/config
# 验证
kubectl config current-context
# 应该显示 minikube

# 4. 如果需要手动配置
minikube update-context

# 5. 常用命令
minikube status
minikube dashboard    # 打开 Kubernetes Dashboard
minikube kubectl -- get pods
```

### Kind (Kubernetes in Docker)

```powershell
# 1. 安装 Kind
choco install kind

# 2. 创建集群
kind create cluster --name my-cluster

# 3. Kind 自动更新 kubeconfig
kubectl config get-contexts
# 应该看到 kind-my-cluster

# 4. 多集群配置
kind create cluster --name prod
kind create cluster --name dev

# 切换上下文
kubectl config use-context kind-prod

# 5. 删除集群
kind delete cluster --name my-cluster
```

### K3s (轻量级 Kubernetes)

```powershell
# 1. 安装 K3s
curl -sfL https://get.k3s.io | sh -

# 2. K3s 自动配置 /etc/rancher/k3s/k3s.yaml
# 复制到用户目录
Copy-Item /etc/rancher/k3s/k3s.yaml "$env:USERPROFILE\.kube\config"

# 3. 设置权限
chmod 600 "$env:USERPROFILE\.kube\config"

# 4. 验证
kubectl get nodes
```

---

## 自动化配置脚本

### 快速配置脚本

我已经为你创建了 `setup-kubeconfig.ps1`，支持以下功能：

```powershell
# 交互式模式
.\setup-kubeconfig.ps1

# 非交互式模式
.\setup-kubeconfig.ps1 -Source file -ClusterName "C:\path\to\kubeconfig"
.\setup-kubeconfig.ps1 -Source aks -ClusterName "my-cluster"
.\setup-kubeconfig.ps1 -Source eks -ClusterName "my-cluster"
.\setup-kubeconfig.ps1 -Source gke -ClusterName "my-cluster"
.\setup-kubeconfig.ps1 -Source minikube
.\setup-kubeconfig.ps1 -Source kind

# 仅验证
.\setup-kubeconfig.ps1 -VerifyOnly
```

### 多集群快速切换

```powershell
# 快速切换上下文
function kswitch {
    param([string]$context)
    kubectl config use-context $context
    Write-Host "切换到: $context" -ForegroundColor Green
    kubectl config current-context
}

# 使用
kswitch docker-desktop
kswitch kind-prod
kswitch my-aks-cluster
```

### 环境检测和自动配置

```powershell
function Initialize-KubeConfig {
    <#
    .SYNOPSIS
        智能初始化 Kubernetes 配置
    #>

    # 检查 kubectl
    if (-not (Get-Command kubectl -ErrorAction SilentlyContinue)) {
        Write-Warning "kubectl 未安装"
        Write-Host "安装 kubectl: choco install kubernetes-cli"
        return
    }

    # 检查 kubeconfig
    $kubeconfig = $env:KUBECONFIG
    if (-not $kubeconfig) {
        $kubeconfig = "$env:USERPROFILE\.kube\config"
    }

    if (-not (Test-Path $kubeconfig)) {
        Write-Warning "kubeconfig 未找到: $kubeconfig"

        # 自动检测并配置
        if (Get-Command minikube -ErrorAction SilentlyContinue) {
            Write-Host "检测到 Minikube，正在配置..." -ForegroundColor Yellow
            minikube update-context
        }

        if (Get-Command kind -ErrorAction SilentlyContinue) {
            Write-Host "检测到 Kind，正在配置..." -ForegroundColor Yellow
            kind get clusters | ForEach-Object {
                Write-Host "  - $_"
            }
        }
    } else {
        Write-Success "kubeconfig 已配置: $kubeconfig"
    }

    # 验证
    $env:KUBECONFIG = $kubeconfig
    try {
        kubectl cluster-info
        kubectl config current-context
    } catch {
        Write-Error "Kubernetes 连接失败"
    }
}
```

---

## 高级配置

### 多集群配置

```yaml
# ~/.kube/config
apiVersion: v1
kind: Config
clusters:
  - name: production
    cluster:
      server: https://api.production.com:6443
      certificate-authority-data: <PROD_CA>
  - name: staging
    cluster:
      server: https://api.staging.com:6443
      certificate-authority-data: <STAGING_CA>
  - name: development
    cluster:
      server: https://api.dev.com:6443
      certificate-authority-data: <DEV_CA>
contexts:
  - name: prod-admin
    context:
      cluster: production
      user: prod-admin
      namespace: production
  - name: staging-dev
    context:
      cluster: staging
      user: staging-dev
      namespace: default
  - name: dev-local
    context:
      cluster: development
      user: developer
      namespace: development
current-context: prod-admin
users:
  - name: prod-admin
    user:
      token: <PROD_TOKEN>
  - name: staging-dev
    user:
      token: <STAGING_TOKEN>
  - name: developer
    user:
      token: <DEV_TOKEN>
```

### 配置片段合并

```powershell
# 合并多个 kubeconfig
$KUBECONFIG_paths = @(
    "$env:USERPROFILE\.kube\config",
    "$env:USERPROFILE\.kube\config-eks",
    "$env:USERPROFILE\.kube\config-gke"
)

$merged = @()
foreach ($path in $KUBECONFIG_paths) {
    if (Test-Path $path) {
        $content = Get-Content $path -Raw
        $merged += $content
    }
}

$merged | Set-Content "$env:USERPROFILE\.kube\config-merged"

# 使用合并配置
kubectl config get-contexts --kubeconfig="$env:USERPROFILE\.kube\config-merged"
```

### 安全配置

#### 限制 kubeconfig 权限

```powershell
# Windows
icacls "$env:USERPROFILE\.kube\config" /inheritance:r /grant:r "$env:USERNAME:R"

# Linux/Mac
chmod 600 ~/.kube/config
```

#### 使用证书而非令牌

```yaml
users:
  - name: my-user
    user:
      client-certificate-data: <BASE64_ENCODED_CERT>
      client-key-data: <BASE64_ENCODED_KEY>
```

---

## 故障排查

### 常见错误及解决方案

#### 错误 1: "no configuration has been provided"

```powershell
# 原因：未设置 KUBECONFIG
$env:KUBECONFIG = "$env:USERPROFILE\.kube\config"
```

#### 错误 2: "Unable to connect to the server"

```powershell
# 原因：API Server 不可达
kubectl cluster-info
# 检查 server 地址是否正确
# 检查网络连接
Test-NetConnection -ComputerName api.cluster.com -Port 6443
```

#### 错误 3: "certificate is not valid"

```powershell
# 原因：证书过期或无效
# 更新证书
kubectl config view --raw
# 检查 certificate-authority-data
```

#### 错误 4: "token is not valid"

```powershell
# 原因：认证令牌过期
# 重新获取令牌
# 从 kubeconfig 中获取新令牌
```

#### 错误 5: "context was not found"

```powershell
# 原因：引用的上下文不存在
kubectl config get-contexts
# 检查上下文名称是否正确
kubectl config use-context <correct-context>
```

### 完整诊断流程

```powershell
# 诊断脚本
function Debug-KubeConfig {
    Write-Host "`n========== Kubernetes 配置诊断 ==========" -ForegroundColor Cyan

    # 1. 检查 kubectl
    Write-Host "`n1. kubectl 安装状态:"
    if (Get-Command kubectl -ErrorAction SilentlyContinue) {
        kubectl version --client
        Write-Host "✓ kubectl 已安装" -ForegroundColor Green
    } else {
        Write-Host "✗ kubectl 未安装" -ForegroundColor Red
    }

    # 2. 检查 kubeconfig 文件
    Write-Host "`n2. kubeconfig 文件位置:"
    $locations = @(
        $env:KUBECONFIG,
        "$env:USERPROFILE\.kube\config",
        "$env:HOME\.kube\config",
        ".\kubeconfig"
    )

    foreach ($loc in $locations) {
        if ($loc) {
            if (Test-Path $loc) {
                Write-Host "  ✓ $loc" -ForegroundColor Green
            } else {
                Write-Host "  ✗ $loc" -ForegroundColor Red
            }
        }
    }

    # 3. 检查上下文
    Write-Host "`n3. kubectl 上下文:"
    try {
        kubectl config get-contexts
    } catch {
        Write-Host "  无法获取上下文" -ForegroundColor Red
    }

    # 4. 测试连接
    Write-Host "`n4. 集群连接测试:"
    try {
        kubectl cluster-info
        Write-Host "✓ 集群可达" -ForegroundColor Green
    } catch {
        Write-Host "✗ 集群不可达: $_" -ForegroundColor Red
    }

    # 5. 显示当前配置
    Write-Host "`n5. 当前配置内容:"
    try {
        kubectl config view --flatten
    } catch {
        Write-Host "  无法显示配置" -ForegroundColor Yellow
    }

    Write-Host "`n========== 诊断完成 ==========" -ForegroundColor Cyan
}

# 运行诊断
Debug-KubeConfig
```

---

## 最佳实践

1. **始终设置 KUBECONFIG 环境变量** - 避免依赖默认位置
2. **使用多个配置文件** - 按环境分离（dev、staging、prod）
3. **定期轮换令牌/证书** - 提高安全性
4. **不要提交 kubeconfig 到 Git** - 添加到 .gitignore
5. **使用 Secret 存储敏感信息** - 在 Operator 中
6. **测试连接后再使用** - `kubectl cluster-info`

---

## 参考资源

- [Kubernetes 官方文档](https://kubernetes.io/docs/concepts/configuration/organize-cluster-access-kubeconfig/)
- [kubectl 配置指南](https://kubernetes.io/docs/reference/generated/kubectl/kubectl-commands#config)
- [client-go 配置](https://pkg.go.dev/k8s.io/client-go/tools/clientcmd)
- [Python kubernetes 客户端](https://github.com/kubernetes-client/python)
- [Java 客户端](https://github.com/kubernetes-client/java)

---

**创建时间**: 2026-06-01
**版本**: 1.0
**维护**: Kubernetes 配置自动化团队
