# 安装指南

## 前置条件

- PowerShell 5.1+（Windows）/ PowerShell 7+（Linux/macOS）
- 如使用 `-DownloadFromGit` 模式：需 `git` 在 PATH

## 三种安装方式

### 方式 1：自包含模式（默认，离线可用）

直接用包内已有的 `tlm-hook-failsafe.psm1` 快照。

```powershell
cd packages\tlm-hook-failsafe
.\install.ps1
```

### 方式 2：从本地仓库源安装

从仓库根目录取最新的 `scripts/dev/hook_fail_safe.psm1`。

```powershell
.\install.ps1 -SourcePath C:\Users\Administrator\agent
```

### 方式 3：从 Git URL 安装

```powershell
.\install.ps1 -DownloadFromGit https://github.com/user/repo.git
```

## 自定义安装路径

```powershell
.\install.ps1 -TargetPath D:\custom\path -Force
```

## 默认安装路径

| 平台 | 默认路径 |
|------|---------|
| Windows | `$HOME\Documents\PowerShell\Modules\tlm-hook-failsafe` |
| Linux | `$HOME/.local/share/powershell/Modules/tlm-hook-failsafe` |

## 验证安装

```powershell
# 1. 加载模块
Import-Module tlm-hook-failsafe

# 2. 验证导出函数数（预期 12）
(Get-Command -Module tlm-hook-failsafe).Count

# 3. 调用示例
Get-HookContent -SourceRepo "C:\test" | Select-String "TLM-HOOK v1"
```

## 卸载

```powershell
Remove-Item -Recurse -Force "$HOME\Documents\PowerShell\Modules\tlm-hook-failsafe"
# Linux: Remove-Item -Recurse -Force "$HOME/.local/share/powershell/Modules/tlm-hook-failsafe"
```

## 故障排查

### `Import-Module` 提示找不到模块

PowerShell 会话在安装前打开的，PSModulePath 未刷新。解决：

```powershell
# 显式指定路径
Import-Module "$HOME\Documents\PowerShell\Modules\tlm-hook-failsafe\tlm-hook-failsafe.psd1"
# 或重启 PowerShell 会话
```

### `导出函数数=N, 预期 12`

源 `hook_fail_safe.psm1` 被修改但未同步到包内。解决：

```powershell
.\sync-from-source.ps1
.\install.ps1 -SourcePath C:\Users\Administrator\agent -Force
```
