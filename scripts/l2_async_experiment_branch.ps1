<#
.SYNOPSIS
    L2 异步方案实验分支 git 操作指令（PowerShell 版）

.DESCRIPTION
    用于在本地创建临时分支，安全实验异步方案可行性，不污染 master。
    所有操作均可在实验失败时安全回滚。

.NOTES
    【不易】不强制推送、不修改 master、不修改 git config
    【变易】实验分支可丢弃可合并，master 始终保持同步方案
    【简易】单文件脚本，参数化操作（create/verify/merge/abort/cleanup）
#>

param(
    [Parameter(Position=0)]
    [ValidateSet("create","verify","status","merge","abort","cleanup","help")]
    [string]$Action = "help",

    [string]$BranchName = "feature/l2-async-io-experiment"
)

$ErrorActionPreference = "Stop"
$RepoRoot = "c:\Users\Administrator\agent"

function Invoke-Git {
    param([string]$Cmd)
    Write-Host "PS> git $Cmd" -ForegroundColor DarkGray
    & git -C $RepoRoot @($Cmd -split ' ')
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[!] git 命令失败 (exit=$LASTEXITCODE): git $Cmd" -ForegroundColor Yellow
    }
}

function Test-WorkingTreeClean {
    $status = & git -C $RepoRoot status --porcelain
    if ($status) {
        Write-Host "[!] 工作区不干净，请先提交或 stash 当前变更:" -ForegroundColor Yellow
        $status | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
        return $false
    }
    return $true
}

function Action-Help {
    Write-Host @"
=== L2 异步方案实验分支操作 ===

用法:
    .\scripts\l2_async_experiment_branch.ps1 create    # 创建实验分支 + 同步基线 tag
    .\scripts\l2_async_experiment_branch.ps1 verify    # 跑一致性校验 + 性能基线
    .\scripts\l2_async_experiment_branch.ps1 status    # 查看实验分支状态
    .\scripts\l2_async_experiment_branch.ps1 merge     # 合并实验分支到 master
    .\scripts\l2_async_experiment_branch.ps1 abort     # 丢弃实验分支，回到 master
    .\scripts\l2_async_experiment_branch.ps1 cleanup   # 删除已合并的实验分支
    .\scripts\l2_async_experiment_branch.ps1 help      # 显示此帮助

实验流程（推荐顺序）:
    1. create   → 创建分支 + 基线 tag
    2. verify   → 跑同步基线压测（对照用）
    3. [手工修改代码：read_fragment 异步化 + _build_l2 并发化]
    4. verify   → 跑异步压测 + 一致性校验
    5. status   → 检查变更
    6. merge    → 性能改善则合并 / abort → 性能恶化则丢弃
    7. cleanup  → 合并后清理分支

分支名: $BranchName (可用 -BranchName 自定义)
"@ -ForegroundColor White
}

function Action-Create {
    Write-Host "`n=== [1/3] 前置检查 ===" -ForegroundColor Cyan
    if (-not (Test-WorkingTreeClean)) {
        Write-Host "[x] 工作区不干净，请先处理后再创建实验分支" -ForegroundColor Red
        return
    }

    $currentBranch = & git -C $RepoRoot rev-parse --abbrev-ref HEAD
    if ($currentBranch -ne "master") {
        Write-Host "[x] 当前不在 master 分支（当前: $currentBranch），请先 git checkout master" -ForegroundColor Red
        return
    }

    Write-Host "`n=== [2/3] 拉取 master 最新状态 ===" -ForegroundColor Cyan
    Invoke-Git "pull --rebase origin master"

    Write-Host "`n=== [3/3] 创建实验分支 + 同步基线 tag ===" -ForegroundColor Cyan
    $tagDate = Get-Date -Format "yyyyMMdd"
    $tagName = "l2-sync-baseline-$tagDate"

    # 检查 tag 是否已存在
    $tagExists = & git -C $RepoRoot tag -l $tagName
    if ($tagExists) {
        Write-Host "[!] tag $tagName 已存在，跳过创建" -ForegroundColor Yellow
    } else {
        Invoke-Git "tag $tagName"
        Write-Host "[✓] 同步基线 tag 已创建: $tagName" -ForegroundColor Green
    }

    # 检查分支是否已存在
    $branchExists = & git -C $RepoRoot branch -l $BranchName
    if ($branchExists) {
        Write-Host "[!] 分支 $BranchName 已存在，切换过去" -ForegroundColor Yellow
        Invoke-Git "checkout $BranchName"
    } else {
        Invoke-Git "checkout -b $BranchName"
        Write-Host "[✓] 实验分支已创建并切换: $BranchName" -ForegroundColor Green
    }

    Write-Host @"
`n=== 下一步操作 ===
1. 跑同步基线压测（对照用）:
    `$env:PYTHONIOENCODING="utf-8"
    python scripts\bench_l2_stress.py > bench_sync_baseline.log 2>&1

2. 修改代码（详见 docs\changelogs\l2-async-switch-checklist.md Phase 2）:
    - agent\memory\markdown_syncer.py: read_fragment 异步化
    - agent\memory\context_assembler.py: _build_l2 并发化

3. 跑一致性校验 + 异步压测:
    .\scripts\l2_async_experiment_branch.ps1 verify
"@ -ForegroundColor White
}

function Action-Verify {
    Write-Host "`n=== [1/3] 一致性校验 ===" -ForegroundColor Cyan
    $env:PYTHONIOENCODING = "utf-8"
    & python "$RepoRoot\scripts\simulate_l2_async_switch.py" --check
    $consistencyExit = $LASTEXITCODE
    if ($consistencyExit -eq 0) {
        Write-Host "[✓] 标记与实现一致" -ForegroundColor Green
    } else {
        Write-Host "[!] 标记与实现不一致（exit=$consistencyExit）—— 若正在切换中，属正常；若已完成切换，需修复" -ForegroundColor Yellow
    }

    Write-Host "`n=== [2/3] 性能回归测试 ===" -ForegroundColor Cyan
    & python -m pytest "$RepoRoot\tests\performance\test_l2_perf_regression.py" -v -m performance --timeout=120
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[✓] L2 性能护栏全部通过" -ForegroundColor Green
    } else {
        Write-Host "[!] 性能护栏有失败，请检查是否性能恶化" -ForegroundColor Yellow
    }

    Write-Host "`n=== [3/3] 提示：跑压测对照 ===" -ForegroundColor Cyan
    Write-Host @"
若已完成异步代码修改，跑异步压测:
    python scripts\bench_l2_stress.py > bench_async.log 2>&1
    python scripts\parse_ci_l2_report.py --bench-log bench_async.log --output bench_async_report.png

对照基线 bench_sync_baseline.log，确认 P50 改善后再合并。
"@ -ForegroundColor White
}

function Action-Status {
    Write-Host "`n=== 当前分支状态 ===" -ForegroundColor Cyan
    $currentBranch = & git -C $RepoRoot rev-parse --abbrev-ref HEAD
    Write-Host "当前分支: $currentBranch"

    Write-Host "`n=== 与 master 的差异 ===" -ForegroundColor Cyan
    Invoke-Git "log master..HEAD --oneline"

    Write-Host "`n=== 未提交的变更 ===" -ForegroundColor Cyan
    Invoke-Git "status --short"

    Write-Host "`n=== 同步基线 tag ===" -ForegroundColor Cyan
    & git -C $RepoRoot tag -l "l2-sync-baseline-*"
}

function Action-Merge {
    Write-Host "`n=== [1/4] 切换到 master ===" -ForegroundColor Cyan
    Invoke-Git "checkout master"

    Write-Host "`n=== [2/4] 拉取 master 最新 ===" -ForegroundColor Cyan
    Invoke-Git "pull --rebase origin master"

    Write-Host "`n=== [3/4] 合并实验分支（--no-ff 保留分支历史）===" -ForegroundColor Cyan
    Invoke-Git "merge --no-ff $BranchName -m 'perf(l2): 切换 read_fragment 到异步 IO 方案（实验验证通过）'"

    Write-Host "`n=== [4/4] 一致性最终校验 ===" -ForegroundColor Cyan
    $env:PYTHONIOENCODING = "utf-8"
    & python "$RepoRoot\scripts\simulate_l2_async_switch.py" --check
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[✓] 合并后标记与实现一致，可推送" -ForegroundColor Green
        Write-Host "`n下一步: git push origin master" -ForegroundColor White
    } else {
        Write-Host "[!] 合并后不一致，请检查后再推送" -ForegroundColor Red
    }
}

function Action-Abort {
    Write-Host "`n=== [1/3] 丢弃实验分支所有未提交变更 ===" -ForegroundColor Cyan
    $currentBranch = & git -C $RepoRoot rev-parse --abbrev-ref HEAD
    if ($currentBranch -eq $BranchName) {
        Invoke-Git "restore ."
    }

    Write-Host "`n=== [2/3] 切换回 master ===" -ForegroundColor Cyan
    Invoke-Git "checkout master"

    Write-Host "`n=== [3/3] 删除实验分支 ===" -ForegroundColor Cyan
    Invoke-Git "branch -D $BranchName"

    Write-Host "`n=== 回滚到同步基线 tag（可选）===" -ForegroundColor Cyan
    $tags = & git -C $RepoRoot tag -l "l2-sync-baseline-*" | Sort-Object -Descending | Select-Object -First 1
    if ($tags) {
        Write-Host "若需重置到同步基线: git -C $RepoRoot reset --hard $tags" -ForegroundColor White
        Write-Host "（仅当 master 也被污染时执行，否则无需 reset）" -ForegroundColor DarkGray
    }

    Write-Host "`n[✓] 实验已丢弃，回到 master 同步方案" -ForegroundColor Green

    Write-Host "`n=== 验证回到同步方案 ===" -ForegroundColor Cyan
    $env:PYTHONIOENCODING = "utf-8"
    & python "$RepoRoot\scripts\simulate_l2_async_switch.py" --check
}

function Action-Cleanup {
    Write-Host "`n=== [1/2] 检查实验分支是否已合并 ===" -ForegroundColor Cyan
    $merged = & git -C $RepoRoot branch --merged master | Select-String $BranchName
    if (-not $merged) {
        Write-Host "[!] 分支 $BranchName 未合并到 master，拒绝清理" -ForegroundColor Red
        Write-Host "    若确认要丢弃，请用: .\scripts\l2_async_experiment_branch.ps1 abort" -ForegroundColor Yellow
        return
    }

    Write-Host "`n=== [2/2] 删除本地分支 ===" -ForegroundColor Cyan
    Invoke-Git "branch -d $BranchName"

    Write-Host "`n=== 删除远程分支（若存在）===" -ForegroundColor Cyan
    $remoteExists = & git -C $RepoRoot ls-remote --heads origin $BranchName
    if ($remoteExists) {
        Invoke-Git "push origin --delete $BranchName"
    } else {
        Write-Host "[i] 远程分支不存在，跳过" -ForegroundColor DarkGray
    }

    Write-Host "`n[✓] 实验分支已清理" -ForegroundColor Green
}

# ── 主分发 ──
switch ($Action) {
    "create"  { Action-Create }
    "verify"  { Action-Verify }
    "status"  { Action-Status }
    "merge"   { Action-Merge }
    "abort"   { Action-Abort }
    "cleanup" { Action-Cleanup }
    default   { Action-Help }
}
