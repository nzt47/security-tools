# Skills Check Branch Protection 紧急回滚工具

> 路径: `scripts/rollback-protection.ps1`
>
> 用途: 临时关闭/恢复 GitHub Branch Protection 的 required_status_checks，用于紧急修复 PR 合并场景。

## 一、前置条件

1. **PowerShell 7+** (脚本使用 `SupportsShouldProcess`，需 PS 5.1+，推荐 PS 7+)
2. **gh CLI 已安装并登录**
   ```powershell
   wingc install GitHub.cli
   gh auth login
   ```
3. **仓库 Admin 权限** (修改 Branch Protection 需要 admin 角色或更高)

## 二、支持的参数

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `-Action` | string | `status` | 三选一: `status` / `disable` / `enable` |
| `-Branch` | string | `master` | 目标分支名 |
| `-Force` | switch | $false | 跳过确认提示 (向后兼容别名) |
| `-Confirm` | switch | $true | ShouldProcess 标准参数，控制确认弹窗 |
| `-WhatIf` | switch | $false | ShouldProcess 标准参数，模拟执行不修改 |

> **注**: `-Confirm` 和 `-WhatIf` 由 PowerShell `SupportsShouldProcess` 自动提供，无需在脚本中显式声明。

## 三、三种动作

### 1. `status` — 只读查询

```powershell
.\scripts\rollback-protection.ps1 -Action status
.\scripts\rollback-protection.ps1 -Action status -Branch main
```

**行为**: 查询当前 Protection 配置 + 检查备份文件是否存在，不修改任何状态。

**输出示例**:
```
=== Branch Protection 状态 ===
  分支: master
  Required Status Checks:
    enabled: true
    contexts:
      - Skills Check / Skills Gate (汇总门禁)
  备份文件存在: .github/branch_protection_backup.json
```

### 2. `disable` — 临时关闭

```powershell
# 标准用法 (会弹确认窗)
.\scripts\rollback-protection.ps1 -Action disable

# 跳过确认 (CI 或脚本调用)
.\scripts\rollback-protection.ps1 -Action disable -Confirm:$false
.\scripts\rollback-protection.ps1 -Action disable -Force
```

**行为**:
1. 先备份当前 `required_status_checks.contexts` 到 `.github/branch_protection_backup.json`
2. 清空 contexts 列表 (使 PR 可在 skills-check 失败时也能合并)
3. 输出操作摘要

### 3. `enable` — 恢复配置

```powershell
# 标准用法 (会弹确认窗)
.\scripts\rollback-protection.ps1 -Action enable

# 跳过确认
.\scripts\rollback-protection.ps1 -Action enable -Confirm:$false
```

**行为**: 从 `.github/branch_protection_backup.json` 读取并恢复 contexts。

## 四、`-Confirm` 参数详解

### 4.1 工作原理

脚本声明 `[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]`:

- `ConfirmImpact = 'High'` → **危险级别操作默认触发确认弹窗**
- 弹窗提供 6 个选项: `[Y] Yes / [A] Yes to All / [N] No / [L] No to All / [S] Suspend / [?] Help`

### 4.2 确认弹窗示例

```
Confirm
Are you sure you want to perform this action?
Performing the operation "Disable (清空 contexts)" on target
"branch/master required_status_checks".
[Y] Yes  [A] Yes to All  [N] No  [L] No to All  [S] Suspend  [?] Help
(default is "Y"): n
  已取消 (用户拒绝或 -Confirm:$false)
```

### 4.3 使用场景

| 场景 | 推荐命令 | 原因 |
|---|---|---|
| 人工交互式操作 | `-Action disable` (默认) | ConfirmImpact=High 自动弹窗，防误操作 |
| CI/CD 自动化 | `-Action disable -Confirm:$false` | 非交互环境无法响应弹窗 |
| 批量脚本调用 | `-Action disable -Force` | 已批量确认，无需逐个弹窗 |
| 只读检查 | `-Action status` | 不触发 ShouldProcess，无弹窗 |

## 五、`-WhatIf` 参数详解

### 5.1 工作原理

`-WhatIf` 由 `SupportsShouldProcess` 自动提供，传入后 `ShouldProcess()` 返回 `$false`，脚本提前退出，**不执行任何实际修改**。

### 5.2 输出示例

```powershell
.\scripts\rollback-protection.ps1 -Action disable -WhatIf
```

```
=== 禁用 required_status_checks ===
What if: Performing the operation "Disable (清空 contexts)" on target
"branch/master required_status_checks".
  已取消 (用户拒绝或 -Confirm:$false)
```

**关键**: 输出 `What if: Performing the operation "..."` 后立即退出，**不调用 gh API，不创建备份，不清空 contexts**。

### 5.3 使用场景

| 场景 | 推荐命令 | 价值 |
|---|---|---|
| 首次使用前演练 | `-Action disable -WhatIf` | 确认脚本会做什么再正式执行 |
| CI 流水线预演 | 全流程加 `-WhatIf` | 在 dry-run 模式下验证脚本正确性 |
| 排查问题 | `-Action enable -WhatIf` | 查看是否已有备份可恢复 |
| 文档截图 | `-WhatIf` | 截图展示行为不影响真实配置 |

## 六、参数组合矩阵

| `-Action` | `-Confirm` | `-WhatIf` | `-Force` | 行为 |
|---|---|---|---|---|
| `status` | (忽略) | (忽略) | (忽略) | 只读查询，无弹窗 |
| `disable` | (默认 $true) | $false | $false | 弹窗 → 用户确认 → 执行 |
| `disable` | $false | $false | $false | 不弹窗 → 直接执行 |
| `disable` | $false | $true | $false | 打印 What if → 不执行 |
| `disable` | $true | $true | $false | WhatIf 优先级更高 → 不执行 |
| `disable` | (任意) | $false | $true | Force 跳过确认 → 直接执行 |
| `enable` | (默认 $true) | $false | $false | 弹窗 → 用户确认 → 执行 |

**优先级**: `-WhatIf` > `-Confirm` > `-Force`

- `-WhatIf` 为 $true 时，无论 -Confirm 如何，都不执行实际修改
- `-WhatIf` 为 $false 时，`-Confirm` 决定是否弹窗
- `-Confirm:$false` 或 `-Force` 跳过弹窗

## 七、最佳实践

### 7.1 紧急回滚标准流程

```powershell
# 步骤 1: 先用 -WhatIf 演练，确认脚本行为
.\scripts\rollback-protection.ps1 -Action disable -WhatIf

# 步骤 2: 正式执行 (弹窗确认)
.\scripts\rollback-protection.ps1 -Action disable

# 步骤 3: 在 GitHub 网页合并紧急 PR

# 步骤 4: 立即恢复 (弹窗确认)
.\scripts\rollback-protection.ps1 -Action enable

# 步骤 5: 确认恢复成功
.\scripts\rollback-protection.ps1 -Action status
```

### 7.2 CI/CD 集成最佳实践

```yaml
- name: 检查 Branch Protection 状态
  shell: pwsh
  env:
    GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
  run: |
    # status 为只读操作，无需 -Confirm:$false
    .\scripts\rollback-protection.ps1 -Action status
```

**注意事项**:
- CI 中只用 `status` (只读)，**禁止**在 CI 中执行 `disable` / `enable`
- 若必须自动化，务必加 `-Confirm:$false` 避免卡住
- 自动化 disable 前先用 `-WhatIf` 预演并记录日志

### 7.3 安全建议

1. **备份文件纳入 git 管理**: `.github/branch_protection_backup.json` 应提交到仓库，作为配置可追溯的凭证
2. **最小化禁用时间**: 紧急修复后**立即** enable，避免长时间暴露无保护状态
3. **审计日志**: 所有 disable/enable 操作都会在 GitHub Audit Log 中记录 actor 和时间
4. **权限控制**: 仓库 Admin 角色应限制为少数维护者，普通开发者无权执行回滚脚本

### 7.4 `-Confirm` vs `-Force` 选择

| 参数 | 适用场景 | 推荐度 |
|---|---|---|
| `-Confirm:$false` | PowerShell 标准，适合 CI 和所有自动化脚本 | ⭐⭐⭐⭐⭐ |
| `-Force` | 业务语义快捷别名，向后兼容 | ⭐⭐⭐ |

**推荐**: 新代码统一用 `-Confirm:$false`，与 PowerShell 社区惯例一致。`-Force` 仅为兼容旧调用方式保留。

## 八、故障排查

### 8.1 确认弹窗不出现

**原因**: 脚本被 `$ConfirmPreference` 全局覆盖。

**解决**: 显式指定 `-Confirm:$true` 强制弹窗。

### 8.2 CI 中脚本卡住

**原因**: 非交互环境遇到确认弹窗无响应。

**解决**: 加 `-Confirm:$false` 或改用 `-Action status` (只读无弹窗)。

### 8.3 `-WhatIf` 不生效

**原因**: 脚本未声明 `SupportsShouldProcess`，或 `ShouldProcess()` 未包裹实际操作。

**解决**: 确认脚本头部有 `[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]`。

### 8.4 enable 时提示 "备份文件不存在"

**原因**: 未执行过 disable，或备份文件被误删。

**解决**:
1. 用 `-Action status` 检查备份文件是否存在
2. 若丢失，手动在 GitHub 网页重新配置 required checks

## 九、CI/CD 集成说明

本脚本已集成到 `.github/workflows/skills-check.yml` 的 `skills-gate` Job:

- **触发时机**: PR 创建/更新、主分支推送
- **执行动作**: `status` (只读查询)
- **阻断行为**: status 不阻断合并 (仅显示状态)
- **Token**: 使用 workflow 内置 `${{ secrets.GITHUB_TOKEN }}`

**禁止在 CI 中执行 disable/enable**，这些动作必须人工触发，确保配置变更可追溯。

## 十、相关文档

- [Branch Protection 配置 README](skills_branch_protection_readme.md) — 如何在 GitHub 网页配置 required checks
- [CI/CD 集成文档](skills_ci_cd_integration.md) — 整体 skills-check workflow 设计说明
