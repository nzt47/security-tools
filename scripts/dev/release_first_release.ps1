<#
.SYNOPSIS
    交互式首次发布引导脚本 —— 引导新成员按 release_quickstart.md 五步完成首次发布，每步自动校验。

.DESCRIPTION
    对应 quickstart「首次发布五步走」，每步自动校验：
      Step 1 版本号格式（vX.Y.Z）
      Step 2 工作区与远端同步检查（未提交改动警告 / 落后于远端阻止）
      Step 3 tag 唯一性检查（远端已存在则阻止）+ 打 annotated tag
      Step 4 push tag + 远端确认
      Step 5 引导监控 Actions 与验证 Release

    每步执行前显示将运行的命令并请求确认；校验失败即中止并给出修复提示。

.PARAMETER Version
    待发布版本号（如 v1.1.0）。不填则交互输入。

.PARAMETER SkipPull
    跳过 Step 2 的 git pull --rebase（默认执行）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts/dev/release_first_release.ps1 -Version v1.1.0

.NOTES
    配套文档: docs/release_quickstart.md / docs/release_checklist.md
    安全设计: 所有 git 写操作（tag/push）前均有确认；仅检查项自动执行。
#>
param(
    [string]$Version = "",
    [switch]$SkipPull
)

$ErrorActionPreference = 'Stop'
$confirmHint = "输入 y 确认 / 任意键跳过此步（n 中止脚本）"

function Write-Step  { Write-Host "`n===== $args =====" -ForegroundColor Cyan }
function Write-OK    { Write-Host "  [OK] $args" -ForegroundColor Green }
function Write-Warn  { Write-Host "  [WARN] $args" -ForegroundColor Yellow }
function Write-Fail  { Write-Host "  [FAIL] $args" -ForegroundColor Red }

function Confirm-Step([string]$desc) {
    $ans = Read-Host "  将执行: $desc`n  $confirmHint"
    if ($ans -match '^[yY]$') { return $true }
    if ($ans -match '^[nN]$') { Write-Fail "已中止脚本"; exit 1 }
    Write-Warn "已跳过: $desc"
    return $false
}

# ============================================================================
# Step 1: 版本号
# ============================================================================
Write-Step "Step 1/5 版本号确认"
if (-not $Version) { $Version = Read-Host "  输入待发布版本号（如 v1.1.0）" }
if ($Version -notmatch '^v[0-9]+\.[0-9]+\.[0-9]+$') {
    Write-Fail "版本号格式错误: $Version（必须是 vX.Y.Z 语义化版本）"
    exit 1
}
Write-OK "版本号 $Version 格式正确"
Write-Host "  提示: tag commit message 不要以 release(pypi) 开头（否则 guard 拦截跳过发布）"

# ============================================================================
# Step 2: 工作区与远端同步
# ============================================================================
Write-Step "Step 2/5 工作区与远端同步检查"
$dirty = git status --porcelain
if ($dirty) {
    Write-Warn "工作区存在未提交改动（$((@($dirty)).Count) 条），tag 基于当前 HEAD；如需包含改动请先提交"
} else {
    Write-OK "工作区干净"
}
git fetch origin master 2>$null
$head = git rev-parse HEAD
$origin = git rev-parse origin/master 2>$null
if ($head -ne $origin) {
    if (-not $SkipPull) {
        if (Confirm-Step "git pull --rebase origin master（本地与远端不一致: $head vs $origin）") {
            git pull --rebase origin master
            $head = git rev-parse HEAD
            $origin = git rev-parse origin/master
        }
    }
    if ($head -ne $origin) {
        Write-Fail "本地 HEAD 与 origin/master 仍不一致，请先 git pull --rebase 后再发布"
        exit 1
    }
}
Write-OK "本地与 origin/master 同步 ($head)"

# ============================================================================
# Step 3: tag 唯一性 + 创建 annotated tag
# ============================================================================
Write-Step "Step 3/5 创建 annotated tag"
if (git ls-remote --exit-code origin "refs/tags/$Version" 2>$null) {
    Write-Fail "远端已存在 tag $Version —— 重复发布会触发 409/422 幂等失败，禁止再次发布"
    exit 1
}
Write-OK "远端无 $Version tag（唯一性通过）"
if (Confirm-Step "git tag -a $Version -m \"ci(release): 发布 $Version\"") {
    git tag -a $Version -m "ci(release): 发布 $Version"
    $localTag = git tag -l $Version
    if (-not $localTag) {
        Write-Fail "tag 创建失败（git tag -l 未找到 $Version）"
        exit 1
    }
    Write-OK "本地 tag $Version 已创建: $(git log -1 --format=%s $Version)"
} else {
    Write-Fail "未创建 tag，发布中止"
    exit 1
}

# ============================================================================
# Step 4: push tag + 远端确认
# ============================================================================
Write-Step "Step 4/5 推送 tag 触发发布"
if (Confirm-Step "git push origin $Version（触发自动发布工作流）") {
    git push origin $Version
    if (git ls-remote --exit-code origin "refs/tags/$Version" 2>$null) {
        Write-OK "远端已确认存在 tag $Version，发布已触发"
    } else {
        Write-Fail "推送后远端未找到 tag $Version，请检查网络/权限"
        exit 1
    }
} else {
    Write-Fail "未推送 tag，发布中止（本地 tag $Version 已创建，可用 git tag -d $Version 删除）"
    exit 1
}

# ============================================================================
# Step 5: 发布后引导
# ============================================================================
Write-Step "Step 5/5 发布后验证引导"
$repoPath = git config --get remote.origin.url
if ($repoPath) { $repoPath = $repoPath -replace '.*[(:/]([^:/]+/[^/.]+)(\.git)?$', '$1' } else { $repoPath = '<your-repo>' }
Write-Host "  1. 打开 Actions → 自动发布 → 本次运行，确认:"
Write-Host "     - guard job: skip=false（放行）"
Write-Host "     - auto-release: GitHub Release HTTP 201 + Gitee 同步成功"
Write-Host "     - 无 alert-on-failure 告警 Issue 触发"
Write-Host "  2. 验证 Release 页面:"
Write-Host "     GitHub: https://github.com/$repoPath/releases/tag/$Version"
Write-Host "     Gitee:  https://gitee.com/$repoPath/releases/$Version"
Write-Host "  3. 完整清单核对: docs/release_checklist.md（D/E 段人工确认）"

Write-Step "完成"
Write-OK "发布流程引导结束。如任一步失败，对照 docs/release_workflow_manual.md 排查。"
