<#
.SYNOPSIS
    pre-commit hook 自动化部署与同步工具

.DESCRIPTION
    三义校验：
    - 不易：备份已有 hook 后覆盖；hook 阈值 0 不可绕过；hook 无 BOM / PS1 有 BOM
    - 变易：支持 install/sync/status/dryrun 多模式正交组合；核心 fail-safe 复用模块
    - 简易：单脚本一键运行；输出自解释

    fail-safe 核心逻辑已提取到 hook_fail_safe.psm1 模块，本脚本仅负责编排与 UI。

    三道 fail-safe 防护（hook 内）：
    1. TLM_HOOK_SOURCE_REPO 未设置 -> exit 1
    2. precheck_docs.ps1 不存在 -> exit 1
    3. powershell 调用失败 -> exit 1

.PARAMETER Install
    安装到指定目标仓库路径（缺省：当前仓库）

.PARAMETER Sync
    扫描 -ScanRoot 下所有 git 仓库批量同步

.PARAMETER ScanRoot
    sync 模式扫描根目录（默认 c:\Users\Administrator）

.PARAMETER Status
    显示所有已安装仓库的状态汇总

.PARAMETER DryRun
    预览模式：仅打印将要执行的动作，不写入、不备份、不改环境变量

.PARAMETER SourceRepo
    源仓库路径（默认自动检测为 $PSScriptRoot\..\..）
    写入 TLM_HOOK_SOURCE_REPO 环境变量（User 级 + 当前进程）

.EXAMPLE
    .\scripts\dev\sync_precommit_hook.ps1
    .\scripts\dev\sync_precommit_hook.ps1 -Install D:\code\other-repo
    .\scripts\dev\sync_precommit_hook.ps1 -Sync -ScanRoot D:\code
    .\scripts\dev\sync_precommit_hook.ps1 -Status
    .\scripts\dev\sync_precommit_hook.ps1 -Sync -DryRun
#>
[CmdletBinding()]
param(
    [switch]$Sync,
    [string]$Install,
    [switch]$Status,
    [switch]$DryRun,
    [string]$ScanRoot = "c:\Users\Administrator",
    [string]$SourceRepo
)

# Continue mode: avoid aborting on git stderr warnings (LF/CRLF etc.)
$ErrorActionPreference = "Continue"

# --- 导入 fail-safe 核心模块 ---
$modulePath = Join-Path $PSScriptRoot "hook_fail_safe.psm1"
if (-not (Test-Path $modulePath)) {
    Write-Host "[ERROR] fail-safe 模块不存在: $modulePath" -ForegroundColor Red
    exit 1
}
Import-Module $modulePath -Force

# --- 全局状态 ---
$script:ProjectRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
$script:SourceRepo  = if ($SourceRepo) { $SourceRepo } else { $script:ProjectRoot }
$script:Results      = @()   # 收集所有目标的状态，供汇总表使用

# --- 辅助：步骤标题 ---
function Write-Step {
    param([int]$Index, [int]$Total, [string]$Message)
    Write-Host "`n[$Index/$Total] $Message" -ForegroundColor Yellow
}

# --- 扫描根目录下所有 git 仓库 ---
function Find-GitRepos {
    param([string]$Root)

    if (-not (Test-Path $Root)) {
        Write-Host "[ERROR] 扫描根目录不存在: $Root" -ForegroundColor Red
        exit 1
    }

    # 跳过列表（不易：避免误改依赖包/虚拟环境/系统目录中的 git 仓库）
    $skipNames = @(
        'node_modules', '.venv', 'venv', '__pycache__', '.pytest_cache',
        '.cache', 'site-packages', 'dist', 'build', 'target',
        '.git', '.hg', '.svn',
        'AppData',
        '.vscode', '.vscode-shared',
        'OneDrive'
    )

    $found = @()
    # 只扫一层子目录（用户家目录下嵌套深的一般不是开发仓库）
    Get-ChildItem -Path $Root -Directory -Force -ErrorAction SilentlyContinue | ForEach-Object {
        $dir = $_
        if ($skipNames -contains $dir.Name) { return }

        # 命中 .git 目录/文件 -> 父目录是 git 仓库
        $gitChild = Join-Path $dir.FullName ".git"
        if (Test-Path $gitChild) {
            $found += $dir.FullName
            # 不再深入子目录（剪枝）
            return
        }
    }

    return $found
}

# --- 单仓库部署编排（简易：编排清晰） ---
function Install-HookToRepo {
    param([string]$RepoPath, [int]$Index, [int]$Total)

    $repoName = Split-Path $RepoPath -Leaf
    # 调用模块函数解析 .git 真实路径
    $gitDir = Resolve-GitDir -RepoPath $RepoPath

    if (-not $gitDir) {
        Write-Host "  [$Index/$Total] SKIP $repoPath (非 git 仓库)" -ForegroundColor Gray
        $script:Results += [PSCustomObject]@{
            Repo = $repoName; Status = 'SKIP'; Backup = '-'; Threshold = '-'
        }
        return
    }

    $hookPath = Join-Path $gitDir "hooks\pre-commit"
    $hooksDir = Split-Path $hookPath -Parent
    if (-not (Test-Path $hooksDir)) {
        if (-not $DryRun) {
            New-Item -ItemType Directory -Path $hooksDir -Force | Out-Null
        }
    }

    # 调用模块函数生成 hook 内容
    $newContent = Get-HookContent -SourceRepo $script:SourceRepo

    # 幂等检测：已是最新则跳过
    if (Test-HookUpToDate -HookPath $hookPath -NewContent $newContent) {
        Write-Host "  [$Index/$Total] OK   $repoName (已是最新，跳过)" -ForegroundColor DarkGray
        $script:Results += [PSCustomObject]@{
            Repo = $repoName; Status = 'OK_LATEST'; Backup = '-'; Threshold = '0'
        }
        return
    }

    # 调用模块函数备份已有 hook
    $bakPath = Backup-ExistingHook -HookPath $hookPath -DryRun:$DryRun
    if ($bakPath) {
        Write-Host "  [$Index/$Total] BAK  $repoName -> $(Split-Path $bakPath -Leaf)" -ForegroundColor Yellow
    }

    if ($DryRun) {
        Write-Host "  [$Index/$Total] DRY  $repoName (将写入 hook，阈值 0)" -ForegroundColor Cyan
        $script:Results += [PSCustomObject]@{
            Repo = $repoName; Status = 'DRYRUN'; Backup = $(if ($bakPath) { Split-Path $bakPath -Leaf } else { '-' }); Threshold = '0'
        }
        return
    }

    # 调用模块函数写入 hook（无 BOM）
    Write-HookNoBom -Path $hookPath -Content $newContent

    Write-Host "  [$Index/$Total] DONE $repoName (hook 已部署，阈值 0)" -ForegroundColor Green
    $script:Results += [PSCustomObject]@{
        Repo = $repoName; Status = 'INSTALLED'; Backup = $(if ($bakPath) { Split-Path $bakPath -Leaf } else { '-' }); Threshold = '0'
    }
}

# --- 汇总表 ---
function Show-SummaryTable {
    Write-Host ""
    Write-Host "=== 部署汇总 ===" -ForegroundColor Cyan
    if ($script:Results.Count -eq 0) {
        Write-Host "  (无目标)" -ForegroundColor Gray
        return
    }
    $script:Results | Format-Table -AutoSize -Property Repo, Status, Backup, Threshold

    $okCount     = @($script:Results | Where-Object { $_.Status -eq 'INSTALLED' -or $_.Status -eq 'OK_LATEST' }).Count
    $skipCount   = @($script:Results | Where-Object { $_.Status -eq 'SKIP' }).Count
    $dryrunCount = @($script:Results | Where-Object { $_.Status -eq 'DRYRUN' }).Count
    $color = if ($skipCount -eq 0 -and $dryrunCount -eq 0) { 'Green' } else { 'Yellow' }
    Write-Host "  成功: $okCount | 跳过: $skipCount | DryRun: $dryrunCount" -ForegroundColor $color
}

# --- 状态查询模式 ---
function Show-Status {
    Write-Host "=== Hook 安装状态 ===" -ForegroundColor Cyan

    # 调用模块函数验证环境变量
    $envCheck = Test-SourceRepoEnv
    $envColor = if ($envCheck.Valid) { 'Green' } else { 'Red' }
    Write-Host "  TLM_HOOK_SOURCE_REPO (User) = $(if ($envCheck.Value) { $envCheck.Value } else { '<未设置>' })" -ForegroundColor $envColor

    if (-not $envCheck.Valid) {
        Write-Host "  [ERROR] $($envCheck.Error)" -ForegroundColor Red
        Write-Host "  [HINT] 运行一次 install/sync 后此变量才会被设置" -ForegroundColor Gray
        return
    }

    # 扫描根目录下所有 git 仓库，检测 hook 状态
    Write-Host "`n[1/2] 扫描 $ScanRoot 下的 git 仓库..." -ForegroundColor Yellow
    $targets = Find-GitRepos -Root $ScanRoot
    Write-Host "  [OK] 发现 $($targets.Count) 个 git 仓库" -ForegroundColor Green

    Write-Host "`n[2/2] 检测 hook 状态..." -ForegroundColor Yellow
    foreach ($t in $targets) {
        $gitDir = Resolve-GitDir -RepoPath $t
        $repoName = Split-Path $t -Leaf

        if (-not $gitDir) {
            $script:Results += [PSCustomObject]@{ Repo = $repoName; Status = 'SKIP'; Backup = '-'; Threshold = '-' }
            continue
        }

        $hookPath = Join-Path $gitDir "hooks\pre-commit"
        if (-not (Test-Path $hookPath)) {
            $script:Results += [PSCustomObject]@{ Repo = $repoName; Status = 'NOT_INSTALLED'; Backup = '-'; Threshold = '-' }
            continue
        }

        # 调用模块函数检测 marker
        $marker = Test-HookMarker -HookPath $hookPath
        $threshold = if ($marker.Threshold) { $marker.Threshold } else { '?' }

        # 检测是否有备份
        $hooksDir = Split-Path $hookPath -Parent
        $bakFiles = @(Get-ChildItem $hooksDir -Filter 'pre-commit.bak.*' -ErrorAction SilentlyContinue)
        $hasBackup = if ($bakFiles.Count -gt 0) { "YES ($($bakFiles.Count))" } else { '-' }

        $status = if ($marker.IsOurs) { 'INSTALLED' } else { 'OTHER_HOOK' }
        $script:Results += [PSCustomObject]@{
            Repo = $repoName; Status = $status; Backup = $hasBackup; Threshold = $threshold
        }
    }
    Show-SummaryTable
}

# --- 入口分发 ---
Write-Host "=== Pre-commit Hook 自动化部署 ===" -ForegroundColor Cyan
Write-Host "  模式: $(if ($Status) { 'status' } elseif ($Sync) { "sync (root=$ScanRoot)" } elseif ($Install) { "install -> $Install" } else { 'install (current repo)' })"
Write-Host "  DryRun: $DryRun"
Write-Host "  SourceRepo: $($script:SourceRepo)"

if ($Status) {
    Show-Status
    exit 0
}

# --- sync / install 模式 ---
if ($Sync) {
    Write-Step 1 3 "扫描 $ScanRoot 下的 git 仓库..."
    $targets = Find-GitRepos -Root $ScanRoot
    Write-Host "  [OK] 发现 $($targets.Count) 个 git 仓库" -ForegroundColor Green
    if ($targets.Count -eq 0) {
        Write-Host "  [INFO] 未发现 git 仓库，退出" -ForegroundColor Gray
        exit 0
    }
} elseif ($Install) {
    $targets = @($Install)
} else {
    # 默认：安装到当前仓库
    $targets = @($script:ProjectRoot)
}

Write-Step 2 3 "设置环境变量 TLM_HOOK_SOURCE_REPO..."
# 调用模块函数设置环境变量
Set-SourceRepoEnv -Path $script:SourceRepo -DryRun:$DryRun

Write-Step 3 3 "部署 hook 到 $($targets.Count) 个仓库..."
for ($i = 0; $i -lt $targets.Count; $i++) {
    $idx = $i + 1
    Install-HookToRepo -RepoPath $targets[$i] -Index $idx -Total $targets.Count
}

Show-SummaryTable
