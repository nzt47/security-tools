# 团队快速集成指南

> 目标读者：在新仓库中需要接入 tlm-hook-failsafe 的开发者
> 预计接入时间：5 分钟

---

## 一、前置条件

- Windows PowerShell 5.1+ 或 PowerShell 7+（跨平台）
- 已注册本地私有仓库 `LocalPSRepo`（指向 `C:\PSRepo`）
  - 若未注册，请向发布者索取 `publish-to-local-repo.ps1` 并先执行一次
- 当前用户对模块安装目录有写权限（默认 `$HOME\Documents\PowerShell\Modules`）

---

## 二、一键安装

```powershell
# 安装（Trusted 仓库无需 -Force -Scope）
Install-Module tlm-hook-failsafe -Repository LocalPSRepo

# 验证
Import-Module tlm-hook-failsafe
(Get-Command -Module tlm-hook-failsafe).Count   # 预期 12
```

---

## 三、最小可用示例（3 行）

```powershell
Import-Module tlm-hook-failsafe
$content = Get-HookContent -SourceRepo "D:\code\my-project"
Invoke-SafeHookWrite -HookPath "D:\code\my-project\.git\hooks\pre-commit" -Content $content
```

执行后 `pre-commit` 已写入，含 `TLM-HOOK v1 source_repo=D:\code\my-project` marker 行，且权限已自动修复（Windows ACL / Unix chmod）。

---

## 四、与 sync_precommit_hook.ps1 配合（批量部署）

若需将 hook 批量部署到多个本地仓库：

```powershell
# 1. 安装模块（仅首次）
Install-Module tlm-hook-failsafe -Repository LocalPSRepo

# 2. 批量部署（扫描 c:\Users\Administrator 下一层 git 仓库）
powershell -ExecutionPolicy Bypass -File `
  C:\Users\Administrator\agent\scripts\dev\sync_precommit_hook.ps1 `
  -Sync -ScanRoot "c:\Users\Administrator" `
  -SourceRepo "C:\Users\Administrator\agent"
```

`sync_precommit_hook.ps1` 内部已 `Import-Module` 本模块，自动调用 12 个函数完成：备份 → 写入 → 权限修复 → 验证。

---

## 五、升级模块

当发布者发布新版本后：

```powershell
Update-Module tlm-hook-failsafe -Force

# 验证版本
Get-Module tlm-hook-failsafe -ListAvailable | Select-Object Name, Version
```

> 若 `Update-Module` 提示已是最新，但实际未更新：
> 1. 删除旧版本目录：`Remove-Item -Recurse -Force "$HOME\Documents\PowerShell\Modules\tlm-hook-failsafe"`
> 2. 重启 PowerShell 会话
> 3. 重新 `Install-Module tlm-hook-failsafe -Repository LocalPSRepo -Force`

---

## 六、故障排查

### 问题 1：`Install-Module` 提示找不到 LocalPSRepo 仓库

```powershell
# 检查仓库是否注册
Get-PSRepository

# 若无 LocalPSRepo，请发布者运行：
# .\publish-to-local-repo.ps1
```

### 问题 2：`Import-Module` 找不到模块（PSModulePath 未刷新）

PowerShell 会话在安装前打开的，PSModulePath 未刷新。解决：

```powershell
# 方案 A：显式指定路径
Import-Module "$HOME\Documents\PowerShell\Modules\tlm-hook-failsafe\tlm-hook-failsafe.psd1"

# 方案 B：重启 PowerShell 会话
```

### 问题 3：版本冲突（旧版本未卸载）

```powershell
# 卸载所有旧版本
Get-Module tlm-hook-failsafe -ListAvailable |
  ForEach-Object { Remove-Item -Recurse -Force $_.ModuleBase }

# 重新安装
Install-Module tlm-hook-failsafe -Repository LocalPSRepo -Force
```

### 问题 4：`Find-Module` 命中但 `Install-Module` 失败

检查 `LocalPSRepo` 的 `InstallationPolicy` 是否为 `Trusted`：

```powershell
Get-PSRepository -Name LocalPSRepo | Select-Object Name, InstallationPolicy

# 若为 Untrusted：
Set-PSRepository -Name LocalPSRepo -InstallationPolicy Trusted
```

### 问题 5：导出函数数 ≠ 12

源 `hook_fail_safe.psm1` 被修改但未同步到包。联系发布者运行：

```powershell
.\sync-from-source.ps1
.\publish-to-local-repo.ps1 -BumpVersion
```

---

## 七、卸载

```powershell
Remove-Item -Recurse -Force "$HOME\Documents\PowerShell\Modules\tlm-hook-failsafe"
```

---

## 八、相关文档

- [INSTALL.md](INSTALL.md) - 三种本地安装方式（自包含 / -SourcePath / -DownloadFromGit）
- [README.md](README.md) - 模块功能与 12 个导出函数说明
