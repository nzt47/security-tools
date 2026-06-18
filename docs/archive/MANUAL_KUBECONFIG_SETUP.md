# 手动设置 KUBECONFIG 环境变量 - 详细指南

## 为什么需要设置 KUBECONFIG？

KUBECONFIG 环境变量告诉 Kubernetes 工具（kubectl、helm等）在哪里找到集群配置文件。默认情况下，这些工具会查找 `~/.kube/config` 文件，但通过设置 KUBECONFIG 环境变量，你可以：

- 使用自定义位置的配置文件
- 快速切换不同的集群配置
- 合并多个集群配置
- 解决 "kubeconfig not found at" 错误

---

## 方法 1：临时设置（当前会话）

这种方法只对当前 PowerShell 会话有效，关闭会话后会丢失。

### PowerShell
```powershell
# 方式 1：设置环境变量
$env:KUBECONFIG = "$env:USERPROFILE\.kube\config"

# 验证设置
$env:KUBECONFIG

# 立即测试
kubectl config get-contexts
```

### 命令提示符 (CMD)
```cmd
set KUBECONFIG=%USERPROFILE%\.kube\config
```

### Bash/Linux/macOS
```bash
export KUBECONFIG=~/.kube/config
```

---

## 方法 2：永久设置（用户级别）

这种方法对当前用户的所有会话都有效。

### PowerShell（推荐）
```powershell
# 设置用户级别的环境变量
[Environment]::SetEnvironmentVariable(
    "KUBECONFIG",
    "$env:USERPROFILE\.kube\config",
    "User"
)

# 验证
[Environment]::GetEnvironmentVariable("KUBECONFIG", "User")

# 需要重新打开 PowerShell 窗口才能生效
```

### 命令提示符
```cmd
setx KUBECONFIG "%USERPROFILE%\.kube\config"
```

---

## 方法 3：永久设置（系统级别）

这种方法对所有用户都有效，需要管理员权限。

### PowerShell
```powershell
# 需要管理员权限
[Environment]::SetEnvironmentVariable(
    "KUBECONFIG",
    "$env:USERPROFILE\.kube\config",
    "Machine"
)

# 验证
[Environment]::GetEnvironmentVariable("KUBECONFIG", "Machine")
```

### 命令提示符（需要管理员）
```cmd
setx /M KUBECONFIG "%USERPROFILE%\.kube\config"
```

---

## 方法 4：PowerShell 配置文件（推荐）

在 PowerShell 启动时自动设置环境变量。

### 创建/编辑 PowerShell 配置文件
```powershell
# 检查是否存在配置文件
Test-Path $PROFILE

# 如果不存在，创建它
New-Item -ItemType File -Path $PROFILE -Force

# 编辑配置文件
notepad $PROFILE

# 添加以下内容
$env:KUBECONFIG = "$env:USERPROFILE\.kube\config"
Write-Host "KUBECONFIG set to: $env:KUBECONFIG" -ForegroundColor Green
```

### 配置文件位置
- PowerShell 5.x: `C:\Users\<用户名>\Documents\WindowsPowerShell\Microsoft.PowerShell_profile.ps1`
- PowerShell 7.x: `C:\Users\<用户名>\Documents\PowerShell\Microsoft.PowerShell_profile.ps1`

---

## 方法 5：使用符号链接

如果你的 kubeconfig 文件在其他位置，可以使用符号链接。

```powershell
# 创建 .kube 目录（如果不存在）
New-Item -ItemType Directory -Path "$env:USERPROFILE\.kube" -Force

# 创建符号链接
# 假设你的 kubeconfig 在 D:\MyConfigs\cluster-config.yaml
New-Item -ItemType SymbolicLink `
    -Path "$env:USERPROFILE\.kube\config" `
    -Target "D:\MyConfigs\cluster-config.yaml"

# 验证
Test-Path "$env:USERPROFILE\.kube\config"
```

---

## 方法 6：多配置文件合并

KUBECONFIG 可以包含多个路径（用冒号分隔），会自动合并。

### PowerShell
```powershell
# 合并多个配置文件
$env:KUBECONFIG = "$env:USERPROFILE\.kube\config:C:\Projects\dev-cluster\kubeconfig:$env:USERPROFILE\.kube\config-eks"

# Windows 上使用分号
$env:KUBECONFIG = "$env:USERPROFILE\.kube\config;C:\Projects\dev-cluster\kubeconfig"
```

### Linux/macOS
```bash
export KUBECONFIG=~/.kube/config:~/.kube/config-dev:~/.kube/config-prod
```

---

## 验证和测试

### 检查当前配置
```powershell
# 方法 1：检查环境变量
$env:KUBECONFIG

# 方法 2：检查文件是否存在
Test-Path "$env:USERPROFILE\.kube\config"

# 方法 3：使用 kubectl 查看
kubectl config view

# 方法 4：查看所有上下文
kubectl config get-contexts

# 方法 5：查看当前上下文
kubectl config current-context
```

### 测试连接
```powershell
# 设置环境变量
$env:KUBECONFIG = "$env:USERPROFILE\.kube\config"

# 测试集群连接
kubectl cluster-info

# 获取节点列表
kubectl get nodes

# 查看当前配置详情
kubectl config view --raw
```

---

## 常见问题和解决方案

### 问题 1：环境变量设置后不生效

**解决方案：**
```powershell
# 重新加载环境变量
$env:KUBECONFIG = "$env:USERPROFILE\.kube\config"

# 或者重新打开 PowerShell 窗口

# 或者重启计算机
```

### 问题 2：文件路径包含空格

**解决方案：**
```powershell
# 使用引号
$env:KUBECONFIG = "`"C:\Users\My User\.kube\config`""
# 或者
$env:KUBECONFIG = 'C:\Users\My User\.kube\config'
```

### 问题 3：多个配置文件冲突

**解决方案：**
```powershell
# 指定要使用的配置文件
kubectl config get-contexts --kubeconfig="C:\path\to\config"

# 或合并配置文件
$env:KUBECONFIG = "C:\config1:C:\config2"
```

### 问题 4：权限不足

**解决方案：**
```powershell
# 使用用户级别而非系统级别
[Environment]::SetEnvironmentVariable(
    "KUBECONFIG",
    "$env:USERPROFILE\.kube\config",
    "User"  # 而非 "Machine"
)
```

---

## 最佳实践

### 1. 使用相对安全的存储位置
```powershell
# 推荐位置
$env:USERPROFILE\.kube\config

# 避免
# C:\cluster-configs\prod-kubeconfig.yaml  # 容易被发现
```

### 2. 限制文件权限
```powershell
# Windows
icacls "$env:USERPROFILE\.kube\config" /inheritance:r /grant:r "$env:USERNAME:R"

# 只有你自己可以读取
```

### 3. 定期轮换配置
```powershell
# 定期检查配置过期时间
kubectl config view --raw | Select-String "token:"
```

### 4. 不要提交到 Git
```powershell
# 创建 .gitignore
".kube/config" | Out-File ".gitignore" -Append
```

### 5. 使用不同的配置文件管理不同环境
```powershell
# 生产环境
$env:KUBECONFIG = "$env:USERPROFILE\.kube\config-prod"

# 开发环境
$env:KUBECONFIG = "$env:USERPROFILE\.kube\config-dev"

# 测试环境
$env:KUBECONFIG = "$env:USERPROFILE\.kube\config-test"
```

---

## 快速设置脚本

以下是快速设置 KUBECONFIG 的 PowerShell 脚本：

```powershell
# 快速设置 KUBECONFIG 环境变量
function Set-MyKubeConfig {
    param(
        [Parameter(Mandatory=$false)]
        [string]$Path = "$env:USERPROFILE\.kube\config"
    )

    Write-Host "Setting KUBECONFIG..." -ForegroundColor Yellow

    # 创建目录（如果不存在）
    $kubeDir = Split-Path $Path -Parent
    if (-not (Test-Path $kubeDir)) {
        New-Item -ItemType Directory -Path $kubeDir -Force | Out-Null
        Write-Host "Created directory: $kubeDir" -ForegroundColor Green
    }

    # 设置临时环境变量
    $env:KUBECONFIG = $Path
    Write-Host "Temporary KUBECONFIG set to: $Path" -ForegroundColor Green

    # 设置永久环境变量
    [Environment]::SetEnvironmentVariable("KUBECONFIG", $Path, "User")
    Write-Host "Permanent KUBECONFIG set to: $Path" -ForegroundColor Green

    # 验证
    Write-Host "`nVerification:" -ForegroundColor Cyan
    Write-Host "  KUBECONFIG: $env:KUBECONFIG" -ForegroundColor White
    Write-Host "  File exists: $(Test-Path $Path)" -ForegroundColor White

    # 测试 kubectl
    if (Get-Command kubectl -ErrorAction SilentlyContinue) {
        Write-Host "`nTesting kubectl..." -ForegroundColor Cyan
        try {
            kubectl config get-contexts 2>&1 | Select-Object -First 3
            Write-Host "[OK] kubectl is working" -ForegroundColor Green
        } catch {
            Write-Host "[WARN] kubectl test failed: $_" -ForegroundColor Yellow
        }
    }
}

# 使用示例
# Set-MyKubeConfig  # 使用默认位置
# Set-MyKubeConfig -Path "D:\MyConfigs\cluster.yaml"  # 使用自定义位置
```

---

## 参考命令

### 查看当前配置
```powershell
# 查看环境变量
$env:KUBECONFIG

# 查看所有 kubectl 配置
kubectl config view

# 查看配置文件位置
kubectl config view --raw | Select-String "apiVersion"

# 查看当前上下文
kubectl config current-context

# 查看所有上下文
kubectl config get-contexts
```

### 管理和操作
```powershell
# 创建新上下文
kubectl config set-context my-context --cluster=my-cluster --user=my-user

# 切换上下文
kubectl config use-context my-context

# 删除上下文
kubectl config delete-context my-context

# 重命名上下文
kubectl config rename-context old-name new-name
```

---

## 总结

| 方法 | 作用域 | 需要权限 | 持久性 | 推荐度 |
|------|--------|---------|--------|--------|
| 临时设置 | 当前会话 | 否 | 否 | ★★★☆☆ |
| 用户级别 | 当前用户 | 否 | 是 | ★★★★☆ |
| 系统级别 | 所有用户 | 是 | 是 | ★★★☆☆ |
| PowerShell 配置 | 当前用户 | 否 | 是 | ★★★★★ |
| 符号链接 | N/A | 否 | 是 | ★★★★☆ |
| 多配置合并 | N/A | 否 | 是 | ★★★★☆ |

**推荐使用方法 4（PowerShell 配置文件）**，因为你每次打开 PowerShell 时都会自动设置，无需手动操作。

---

**创建时间**: 2026-06-01
**版本**: 1.0
