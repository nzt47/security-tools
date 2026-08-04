# tlm-hook-failsafe PSGallery 自动发布操作指南

> **适用范围**: tlm-hook-failsafe PowerShell 模块的 PSGallery 发布流程
> **工作流文件**: `.github/workflows/publish-psgallery.yml`
> **维护者**: agent-team
> **最后更新**: 2026-08-04

## 一、发布架构概览

```
开发者修改 .psd1 版本号
        │
        ▼
push 到 master/main
        │
        ▼
┌─────────────────────────────────┐
│  Job 0: auto-tag                │
│  读取 .psd1 ModuleVersion       │
│  检查远程 v* tag 是否存在        │
│  不存在 → gh api 创建 tag        │
└─────────────┬───────────────────┘
              │ tag 创建触发新的工作流运行
              ▼
┌─────────────────────────────────┐
│  Job 1: dry-run-validate         │
│  sync 源码 → pack → 验证 .nupkg  │
│  （always runs）                 │
└─────────────┬───────────────────┘
              │ tag push 时继续
              ▼
┌─────────────────────────────────┐
│  Job 2: publish-to-psgallery     │
│  仅 tag push 或 force_publish 时  │
│  发布到 PSGallery                 │
│  创建 GitHub Release              │
└─────────────────────────────────┘
```

## 二、三种发布场景

### 场景 1：日常发布（推荐方式）

**触发条件**: 修改 `.psd1` 的 `ModuleVersion` 后 push 到 master/main

**步骤**:

```powershell
# 1. 修改版本号
# 编辑 packages/tlm-hook-failsafe/tlm-hook-failsafe.psd1
# ModuleVersion = '1.2.0'  ← 改为目标版本

# 2. 更新 ReleaseNotes（可选但推荐）
# ReleaseNotes = 'v1.2.0: 新功能描述. v1.1.1: ...'

# 3. 同步源码（确保 .psm1 与 scripts/dev/hook_fail_safe.psm1 一致）
& packages/tlm-hook-failsafe/sync-from-source.ps1

# 4. 本地验证
Import-Module packages/tlm-hook-failsafe/tlm-hook-failsafe.psm1 -Force
Test-ModuleManifest packages/tlm-hook-failsafe/tlm-hook-failsafe.psd1

# 5. 提交并推送
git add packages/tlm-hook-failsafe/tlm-hook-failsafe.psd1
git commit -m "release(tlm-hook-failsafe): bump version to 1.2.0"
git push origin master

# 6. CI 自动完成（无需人工干预）:
#    - auto-tag 检测到 1.2.0，创建 v1.2.0 tag
#    - tag 触发 dry-run + 真实发布
#    - PSGallery 上线 1.2.0
#    - GitHub Release 自动创建
```

**监控命令**:

```bash
# 查看工作流运行
gh run list --workflow publish-psgallery.yml --repo nzt47/security-tools --limit 5

# 实时监控某次运行
gh run watch <run-id> --repo nzt47/security-tools

# 查看 Job 日志
gh run view <run-id> --repo nzt47/security-tools --log
```

### 场景 2：PR 验证（不发布）

**触发条件**: PR 修改了 `packages/tlm-hook-failsafe/**` 等路径

**行为**:
- `auto-tag` Job: **跳过**（仅 master/main 运行）
- `dry-run-validate` Job: **运行**（验证 sync + pack）
- `publish-to-psgallery` Job: **跳过**（不发布）

**关键配置**（[publish-psgallery.yml:181-183](../../.github/workflows/publish-psgallery.yml)）:

```yaml
dry-run-validate:
  needs: auto-tag
  # 不易：PR 时 auto-tag 被 if 跳过，needs 默认会跳过依赖 Job；
  #        但 dry-run 是 PR 的核心验证，必须运行 → 用 success() || skipped() 确保不被跳过
  if: ${{ success() || needs.auto-tag.result == 'skipped' }}
```

### 场景 3：手动强制发布（紧急修复）

**触发条件**: GitHub Actions UI 手动触发 `workflow_dispatch`

**步骤**:

1. 访问 `https://github.com/nzt47/security-tools/actions/workflows/publish-psgallery.yml`
2. 点击 "Run workflow"
3. 设置参数:
   - `force_publish`: `true`（必选，否则只 dry-run）
   - `skip_version_check`: `true`（首次发布或重发时使用）
4. 点击 "Run workflow"

**注意**: 手动触发不会自动创建 tag，需要先确保 tag 已存在:

```bash
# 检查 tag 是否存在
git ls-remote --tags origin 'v1.2.0'

# 不存在则手动创建
git tag -a v1.2.0 -m "tlm-hook-failsafe v1.2.0"
git push origin v1.2.0
```

## 三、auto-tag 工作原理

### 3.1 触发条件

```yaml
if: |
  github.event_name == 'push' &&
  startsWith(github.ref, 'refs/heads/') &&
  (github.ref == 'refs/heads/master' || github.ref == 'refs/heads/main')
```

- 仅 `push` 事件（非 PR、非 tag push）
- 仅 `master`/`main` 分支
- **排除 PR**: `event_name != 'pull_request'`
- **排除 tag push**: `!startsWith(github.ref, 'refs/tags/')`

### 3.2 tag 创建方式

```powershell
# 用 gh api 创建 lightweight tag（通过 API 创建的 tag 会触发工作流）
gh api repos/${{ github.repository }}/git/refs `
  --method POST `
  -f "ref=refs/tags/$tagName" `
  -f "sha=$commitSha"
```

**为什么不用 `git push origin v1.x.x`?**
- GitHub Actions 的 `GITHUB_TOKEN` 推送代码不会触发其他工作流（防循环）
- 通过 API 创建的 tag **会触发**工作流，实现「auto-tag → 触发发布」链路

### 3.3 幂等性保证

```powershell
# 检查远程是否已存在该 tag
$exists = git ls-remote --tags origin "refs/tags/$tagName"
if ([string]::IsNullOrWhiteSpace($exists)) {
    # 不存在 → 创建
} else {
    # 已存在 → 跳过（幂等）
}
```

**CI 验证记录**（[run 30911427082](https://github.com/nzt47/security-tools/actions/runs/30911427082)）:
- 读取版本: 1.1.0
- 检查 tag: v1.1.0 已存在
- 创建 tag: skipped（幂等跳过）✅

## 四、publish-to-psgallery 门控条件

```yaml
if: |
  (github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')) ||
  (github.event_name == 'workflow_dispatch' && github.event.inputs.force_publish == 'true')
```

| 场景 | event_name | github.ref | 匹配 | 发布? |
|------|-----------|-----------|------|-------|
| master push | push | refs/heads/master | ❌ | 跳过 |
| tag push | push | refs/tags/v1.2.0 | ✅ | **发布** |
| PR | pull_request | refs/pull/*/merge | ❌ | 跳过 |
| 手动 force_publish | workflow_dispatch | - | ✅ | **发布** |
| 手动非 force | workflow_dispatch | - | ❌ | 跳过 |

## 五、必需的 GitHub Secrets

### PSGALLERY_API_KEY

**用途**: 发布到 PSGallery 的 NuGet API key

**配置方式**:

```bash
# 获取 API key
# 访问 https://www.powershellgallery.com/account/api-keys
# 创建 key，Scope 选 "Push new packages and new package versions"

# 配置 GitHub Secret
gh secret set PSGALLERY_API_KEY --repo nzt47/security-tools
# 粘贴 key，按回车

# 验证
gh secret list --repo nzt47/security-tools
```

**权限要求**:
- `contents: write`（创建 GitHub Release）— 已在工作流顶层声明
- `PSGALLERY_API_KEY` secret（发布到 PSGallery）

## 六、故障排查

### 6.1 auto-tag 未创建 tag

**检查清单**:

```bash
# 1. 确认是 master/main 分支 push
git branch --show-current  # 应为 master 或 main

# 2. 确认 .psd1 版本号已修改
Select-String -Path packages/tlm-hook-failsafe/tlm-hook-failsafe.psd1 -Pattern 'ModuleVersion'

# 3. 确认远程无对应 tag
git ls-remote --tags origin "v<版本号>"

# 4. 查看 auto-tag Job 日志
gh run view <run-id> --repo nzt47/security-tools --log | grep -A 5 "auto-tag"
```

### 6.2 publish Job 失败

**常见原因**:

| 错误信息 | 根因 | 解决方案 |
|---------|------|---------|
| `PSGALLERY_API_KEY secret not set` | 未配置 secret | `gh secret set PSGALLERY_API_KEY --repo nzt47/security-tools` |
| `version X.Y.Z already on PSGallery` | 同版本已发布 | 升级 `.psd1` 版本号 |
| `Resource not accessible by integration` (403) | GITHUB_TOKEN 权限不足 | 确认工作流有 `permissions: contents: write` |
| `CommandNotFoundException` | PS 5.1 数组 splatting 陷阱 | 用 `if/else` 直接调用脚本，不用 `& $array` |

### 6.3 GitHub Release 创建失败

**临时补救**:

```bash
# 用 gh CLI 手动创建
gh release create v<版本号> --repo nzt47/security-tools \
  --title "tlm-hook-failsafe v<版本号>" \
  --notes "已发布到 PSGallery: https://www.powershellgallery.com/packages/tlm-hook-failsafe/<版本号>"
```

## 七、版本号规范

遵循 [SemVer](https://semver.org/) 规范:

| 版本变化 | 场景 | 示例 |
|---------|------|------|
| MAJOR | 不兼容的 API 变更 | 1.x.x → 2.0.0 |
| MINOR | 向下兼容的功能新增 | 1.1.x → 1.2.0 |
| PATCH | 向下兼容的问题修复 | 1.1.0 → 1.1.1 |

**发布后不可删除**: PSGallery 不允许删除已发布版本，发布前务必确认版本号正确。

## 八、本地验证清单

发布前在本地完成以下验证:

```powershell
# 1. 验证 .psd1 清单
Test-ModuleManifest packages/tlm-hook-failsafe/tlm-hook-failsafe.psd1

# 2. 同步源码
& packages/tlm-hook-failsafe/sync-from-source.ps1

# 3. 导入测试
Import-Module packages/tlm-hook-failsafe/tlm-hook-failsafe.psm1 -Force

# 4. 验证导出函数数量（应为 15）
(Get-Command -Module tlm-hook-failsafe).Count

# 5. Dry-run 发布测试
& packages/tlm-hook-failsafe/publish-to-psgallery.ps1 -NuGetApiKey 'dummy' -DryRun -SkipVersionCheck

# 6. 验证 .nupkg 生成
Get-ChildItem packages/tlm-hook-failsafe/*.nupkg
```

## 九、相关文件

| 文件 | 说明 |
|------|------|
| [publish-psgallery.yml](../../.github/workflows/publish-psgallery.yml) | CI/CD 工作流定义 |
| [publish-to-psgallery.ps1](../../packages/tlm-hook-failsafe/publish-to-psgallery.ps1) | 发布脚本 |
| [sync-from-source.ps1](../../packages/tlm-hook-failsafe/sync-from-source.ps1) | 源码同步脚本 |
| [tlm-hook-failsafe.psd1](../../packages/tlm-hook-failsafe/tlm-hook-failsafe.psd1) | 模块清单（版本号真相源） |
| [hook_fail_safe.psm1](../../scripts/dev/hook_fail_safe.psm1) | 源码（真相源） |
| [release-note-v1.1.0](../releases/release-note-tlm-hook-failsafe-v1.1.0-20260804.md) | v1.1.0 发布故障复盘 |
