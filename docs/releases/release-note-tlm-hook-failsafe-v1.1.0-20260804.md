# tlm-hook-failsafe v1.1.0 发布故障复盘与 CI/CD 修复记录

> **发布日期**: 2026-08-04
> **发布版本**: v1.1.0（首次公开发布）
> **PSGallery**: https://www.powershellgallery.com/packages/tlm-hook-failsafe/1.1.0
> **GitHub Release**: https://github.com/nzt47/security-tools/releases/tag/v1.1.0
> **工作流文件**: `.github/workflows/publish-psgallery.yml`

## 一、发布结果

| 项目 | 状态 | 说明 |
|------|------|------|
| PSGallery 发布 | ✅ 成功 | v1.1.0 已上线，可 `Install-Module` 安装 |
| GitHub Release | ✅ 已手动补建 | CI 自动创建失败后用 `gh release create` 补建 |
| Git Tag v1.1.0 | ✅ 已推送 | annotated tag，指向 commit cb7a523c |

## 二、遇到的 4 个技术问题及修复

### 问题 1：tag 推送未触发真实发布 Job（误判）

**现象**：首次打 tag 推送后，观察工作流运行列表发现 `event: push`，最初误判为「branches 过滤屏蔽了 tags」。

**排查过程**：
1. 最初怀疑 `on.push` 中同时声明 `branches` 和 `tags` 是互斥的，误改为两个独立 `push` 块
2. 查阅 [GitHub 官方文档](https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows#push) 后确认：
   - `branches` 和 `tags` 同时定义时为 **OR 关系**（branch 推送或 tag 推送任一满足即触发）
   - 但 `branches/tags` 与 `paths` 为 **AND 关系**（必须同时满足）
   - tag 推送时 `paths` 默认匹配所有文件（tag 不带 diff），故 `v*` tag 总会触发

**根因**：误判。实际 tag 推送**确实触发了**工作流，只是后续 Job 因其他原因失败。

**修复**：恢复 `branches` 和 `tags` 在同一个 `push` 下的声明（[publish-psgallery.yml:28-39](../../.github/workflows/publish-psgallery.yml)）：

```yaml
on:
  push:
    branches: [master, main]
    paths:
      - 'packages/tlm-hook-failsafe/**'
      - 'scripts/dev/hook_fail_safe.psm1'
      - '.github/workflows/publish-psgallery.yml'
    tags:
      - 'v*'
```

**教训**：YAML 中同一个 key（`push`）出现两次，后者会覆盖前者——不能拆成两个 `push` 块。

---

### 问题 2：`& @args` 报 CommandNotFoundException（$args 自动变量陷阱）

**现象**：发布 Job 执行 `& @args` 调用发布脚本时，报：

```
& : The term '.\publish-to-psgallery.ps1 -NuGetApiKey ***' is not recognized
as the name of a cmdlet, function, script file, or operable program.
```

整个命令字符串（含空格和参数）被当作**单一命令名**查找。

**根因**：`$args` 是 PowerShell 的**自动变量（automatic variable）**，代表传入函数的参数数组。在脚本作用域中显式赋值 `$args = @(...)` 会被静默忽略或行为异常，导致 `& @args` 实际调用的是一个未定义的命令。

**CI 日志证据**（[run 30906364611](https://github.com/nzt47/security-tools/actions/runs/30906364611)）：

```
发布到 PSGallery        2026-08-04T11:49:46.7028220Z & : The term '.\publish-to-psgallery.ps1 -NuGetApiKey ***' is not
发布到 PSGallery        2026-08-04T11:49:46.7029352Z recognized as the name of a cmdlet, function, script file, or operable program.
```

**修复**：将 `$args` 改为普通变量名 `$publishArgs`（[publish-psgallery.yml:196-199](../../.github/workflows/publish-psgallery.yml)）：

```powershell
# 修复前（错误）
$args = @('.\publish-to-psgallery.ps1', '-NuGetApiKey', $env:PSGALLERY_API_KEY)
& @args

# 修复后（变量名改为 $publishArgs）
$publishArgs = @('.\publish-to-psgallery.ps1', '-NuGetApiKey', $env:PSGALLERY_API_KEY)
& $publishArgs
```

**教训**：PowerShell 自动变量（`$args`、`$_`、`$PSItem`、`$HOME`、`$PID` 等）不可显式赋值。

---

### 问题 3：`& $publishArgs` 仍报 CommandNotFoundException（PS 5.1 数组 splatting 陷阱）

**现象**：修复问题 2 后（`$args` → `$publishArgs`），**错误依然存在**！CI 日志显示完全相同的错误。

**根因**：PowerShell 5.1 的 call operator `&` 对**数组变量**不会自动 splat（展开）。当 `$publishArgs` 是数组 `@('script.ps1', '-arg', 'val')` 时，`& $publishArgs` 会把整个数组当作**单一命令名**查找，而非「第一个元素当命令，其余当参数」。

**CI 日志证据**（[run 30907087903](https://github.com/nzt47/security-tools/actions/runs/30907087903)）：

```
发布到 PSGallery        2026-08-04T12:00:02.7875476Z & : The term '.\publish-to-psgallery.ps1 -NuGetApiKey ***' is not
发布到 PSGallery        2026-08-04T12:00:02.7881673Z     + CategoryInfo          : ObjectNotFound: (.\publish-to-ps...edn644ye7le3qea:String) [], ParentContainsErrorRecord
```

注意：错误信息中 `.\publish-to-psgallery.ps1 -NuGetApiKey ***` 仍被当作单一字符串。

**修复**：放弃数组 splatting，改用 `if/else` 直接显式调用脚本（[publish-psgallery.yml:194-206](../../.github/workflows/publish-psgallery.yml)）：

```powershell
# 修复前（PS 5.1 不 splat）
$publishArgs = @('.\publish-to-psgallery.ps1', '-NuGetApiKey', $env:PSGALLERY_API_KEY)
if ($skipCheck) { $publishArgs += '-SkipVersionCheck' }
& $publishArgs  # ❌ 整个数组被当作单命令名

# 修复后（直接显式调用，最直白可读）
if ($skipCheck) {
    & .\publish-to-psgallery.ps1 -NuGetApiKey $env:PSGALLERY_API_KEY -SkipVersionCheck
} else {
    & .\publish-to-psgallery.ps1 -NuGetApiKey $env:PSGALLERY_API_KEY
}
```

**教训**：
- PS 5.1 的 `& $arrayVar` 不等于 `& $arrayVar[0] $arrayVar[1] ...`，而是把整个数组转为字符串当命令名
- 如需动态拼装参数，用 `Invoke-Expression` 或 `& $cmd $params[0] $params[1]`（显式索引）
- 最简方案：**直接显式调用**，避免 splatting 陷阱（符合【简易】原则）

---

### 问题 4：GitHub Release 创建报 403（GITHUB_TOKEN 权限不足）

**现象**：PSGallery 发布成功后，`softprops/action-gh-release@v2` 创建 GitHub Release 时报：

```
⚠️ GitHub release failed with status: 403
{"message":"Resource not accessible by integration","documentation_url":"https://docs.github.com/rest/releases/releases#create-a-release","status":"403"}
Skip retry — your GitHub token/PAT does not have the required permission to create a release
```

**根因**：`GITHUB_TOKEN` 默认只有 `contents: read` 权限，无法创建 Release。需要在工作流中显式声明 `permissions: contents: write`。

**CI 日志证据**（[run 30907546224](https://github.com/nzt47/security-tools/actions/runs/30907546224)）：

```
发布到 PSGallery        创建 GitHub Release（tag 触发时）       2026-08-04T12:07:02.5259198Z ⚠️ GitHub release failed with status: 403
发布到 PSGallery        创建 GitHub Release（tag 触发时）       2026-08-04T12:07:02.5260351Z {"message":"Resource not accessible by integration","documentation_url":"https://docs.github.com/rest/releases/releases#create-a-release","status":"403"}
```

**修复**：在工作流顶层添加 `permissions: contents: write`（[publish-psgallery.yml:62-64](../../.github/workflows/publish-psgallery.yml)）：

```yaml
# 不易：GITHUB_TOKEN 默认 contents:read，无法创建 Release；显式声明 contents:write
permissions:
  contents: write
```

**临时补救**：v1.1.0 的 GitHub Release 用 `gh release create` 手动补建：

```bash
gh release create v1.1.0 --repo nzt47/security-tools \
  --title "tlm-hook-failsafe v1.1.0" --notes "..."
```

**教训**：GitHub Actions 的 `GITHUB_TOKEN` 默认权限是只读的，涉及写操作（Release、Issue 评论、PR 标签等）时必须显式声明 `permissions`。

---

## 三、完整的修复提交历史

| Commit | 说明 |
|--------|------|
| `88b1448f` | feat: 错误码解释层 + PSGallery CI/CD + E2E GitHub Actions 集成（初始版本） |
| `b3183482` | fix: publish job 用 `$publishArgs` 替代自动变量 `$args`（问题 2 修复） |
| `cb7a523c` | fix: publish job 直接调用脚本避免 PS 5.1 数组 splatting 陷阱（问题 3 修复） |
| `0b3df5fd` | fix: 添加 `permissions: contents: write` 允许创建 GitHub Release（问题 4 修复） |

## 四、关键经验总结

### 4.1 PowerShell 自动变量清单（不可显式赋值）

| 变量 | 含义 |
|------|------|
| `$args` | 传入函数的参数数组 |
| `$_` / `$PSItem` | 当前管道对象 |
| `$HOME` | 用户主目录 |
| `$PID` | 当前进程 ID |
| `$HOST` | 主机信息 |
| `$PSVersionTable` | PS 版本信息 |
| `$Error` | 错误记录数组 |
| `$null` | 空值 |

### 4.2 PS 5.1 vs PS 7 的 call operator 差异

| 行为 | PS 5.1 | PS 7 |
|------|--------|------|
| `& $arrayVar` | 整个数组当单命令名 ❌ | 正确 splat ✅ |
| 中文 UTF-8 解码 | Windows-1252（引号陷阱） | UTF-8 ✅ |
| `$IsWindows` | 不存在（可自定义） | 只读自动变量 ❌ |

### 4.3 GitHub Actions 触发器规则

| 过滤器组合 | 关系 | 说明 |
|-----------|------|------|
| `branches` + `tags` | OR | 任一满足即触发 |
| `branches`/`tags` + `paths` | AND | 必须同时满足 |
| `branches` + `branches-ignore` | 互斥 | 不可同时使用 |
| `paths` + `paths-ignore` | 互斥 | 不可同时使用 |

### 4.4 GitHub Actions 权限速查

| 操作 | 所需权限 |
|------|---------|
| 创建 Release | `contents: write` |
| 创建 Issue 评论 | `issues: write` 或 `pull-requests: write` |
| 添加 PR 标签 | `pull-requests: write` |
| 推送代码 | `contents: write` |
| 部署 Pages | `pages: write` |

## 五、本地验证

```powershell
# 安装模块
Install-Module tlm-hook-failsafe -Repository PSGallery -Scope CurrentUser

# 验证版本
Get-Module -ListAvailable tlm-hook-failsafe
# Name              Version  ModuleBase
# ----              -------  ----------
# tlm-hook-failsafe 1.1.0    C:\Users\...\PowerShell\Modules\tlm-hook-failsafe\1.1.0

# 验证导出函数（应为 15 个）
Import-Module tlm-hook-failsafe
(Get-Command -Module tlm-hook-failsafe).Count  # 15

# 测试错误码解释层
$map = Get-HookExitCodeMap
$map.Count  # 10（5 自定义 + 5 bash 标准）

Resolve-HookExitCode -ExitCode 2
# Category  : EnvNotSet
# Meaning   : TLM_HOOK_SOURCE_REPO 未设置
```
