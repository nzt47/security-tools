# tlm-hook-failsafe

Pre-commit Hook Fail-Safe 核心模块（可复用核心能力）。

> ⚠️ **真相源警告**
>
> 本目录的 `tlm-hook-failsafe.psm1` 是从 `scripts/dev/hook_fail_safe.psm1` 同步的快照副本。
>
> **修改请到** `scripts/dev/hook_fail_safe.psm1`，然后运行：
> ```powershell
> .\sync-from-source.ps1
> ```

## 功能

- BOM 编码契约：hook 无 BOM（bash 兼容）/ PS1 有 BOM（PS 5.1 中文兼容）
- 备份已有 hook 到时间戳文件（不破坏现有配置）
- 幂等检测：已是最新版本则跳过
- `.git` 路径解析（兼容 worktree/submodule）
- **权限冲突自动修复**（Windows ACL + Unix chmod）
- 跨平台（Windows PS 5.1 / PS 7 / Linux PS 7）

## 导出函数（12 个）

| 函数 | 职责 |
|------|------|
| `Get-HookContent` | 生成 hook bash 内容（含 marker 行） |
| `Write-HookNoBom` | 写 hook 文件（UTF-8 无 BOM） |
| `Write-FileWithBom` | 写 PS1 脚本（UTF-8 with BOM） |
| `Backup-ExistingHook` | 备份已有 hook 到时间戳文件 |
| `Test-HookUpToDate` | 幂等检测（内容比对） |
| `Set-SourceRepoEnv` | 设置 TLM_HOOK_SOURCE_REPO 环境变量 |
| `Test-SourceRepoEnv` | 验证环境变量是否有效 |
| `Resolve-GitDir` | 解析 .git 真实路径（worktree/submodule 兼容） |
| `Test-HookMarker` | 检测 hook 是否为本工具生成 |
| `Test-HookExecutable` | 检测 hook 可执行性（跨平台） |
| `Repair-HookPermission` | 自动修复权限冲突 |
| `Invoke-SafeHookWrite` | 安全写入（写入→检测→修复→验证） |

## 快速开始

```powershell
# 安装到默认路径（自包含模式）
.\install.ps1

# 使用
Import-Module tlm-hook-failsafe
Get-HookContent -SourceRepo "C:\path\to\repo"
```

详见 [INSTALL.md](INSTALL.md)。

## 开发

```powershell
# 修改源后同步到包内
.\sync-from-source.ps1

# 运行安装冒烟测试
.\tests\test_install.ps1
```

## 许可证

MIT
