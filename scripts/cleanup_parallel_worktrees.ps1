#!/usr/bin/env pwsh
# =============================================================================
# cleanup_parallel_worktrees.ps1 — 安全终止并行会话 worktree 并释放资源
#
# 目标 worktree（C:/Windows/Temp 下）:
#   agent-b3   [master] 200+ staged 改动（疑似 index 损坏，大量 staged 删除）
#   agent-wip-ti [wip/test-isolation-fix] 6 个 contract 文件 unstaged 改动
#   pr634_fix  [detached] 干净
#   f6_push    [detached, locked] 本会话遗留的损坏 worktree（-IncludeStale 时清理）
#
# 设计原则（不易/变易/简易）:
#   - 不易: 绝不触碰主工作区（$PWD 校验）; 未备份的未提交改动绝不删除
#   - 变易: -DryRun 预演 / -Execute 执行; -Backup/-Kill/-Remove 可拆分组合
#   - 简易: 逐项 Y/N 确认, 输出清单供人工核对
#
# 用法（仓库根目录执行）:
#   预演:   ./scripts/cleanup_parallel_worktrees.ps1 -DryRun
#   备份:   ./scripts/cleanup_parallel_worktrees.ps1 -Backup
#   完整:   ./scripts/cleanup_parallel_worktrees.ps1 -Backup -Kill -Remove
#   含遗留: ./scripts/cleanup_parallel_worktrees.ps1 -Backup -Kill -Remove -IncludeStale
#
# 参数:
#   -DryRun       只打印将执行的操作, 不实际执行（默认）
#   -Execute      实际执行（默认仅预演）
#   -Backup       备份未提交改动到 backup/parallel_worktree_backup_<时间戳>/
#   -Kill         终止引用 worktree 路径的进程（默认仅列出不终止）
#   -Remove       移除 worktree 目录并从 git worktree 元数据注销
#   -IncludeStale 额外清理 locked 的损坏 worktree f6_push
#   -SkipConfirm  跳过逐项确认（自动化场景, 慎用）
# =============================================================================

param(
    [switch]$DryRun,
    [switch]$Execute,
    [switch]$Backup,
    [switch]$Kill,
    [switch]$Remove,
    [switch]$IncludeStale,
    [switch]$SkipConfirm
)

$ErrorActionPreference = 'Stop'

# 仅当未显式指定 -Execute 时默认进入预演模式
$Do = -not $DryRun -and $Execute

# ---------------------------------------------------------------------------
# 安全校验
# ---------------------------------------------------------------------------
if (-not (Test-Path (Join-Path $PWD '.git'))) {
    Write-Error "脚本必须从仓库根目录执行（当前 $PWD 无 .git）"
    exit 1
}
$RepoRoot = (Resolve-Path $PWD).Path

$Worktrees = @(
    @{ Name = 'agent-b3';   Path = 'C:/Windows/Temp/agent-b3' },
    @{ Name = 'agent-wip-ti'; Path = 'C:/Windows/Temp/agent-wip-ti' },
    @{ Name = 'pr634_fix'; Path = 'C:/Windows/Temp/pr634_fix' }
)
if ($IncludeStale) {
    $Worktrees += @{ Name = 'f6_push'; Path = 'C:/Windows/Temp/f6_push' }
}

$Stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$BackupRoot = Join-Path $RepoRoot "backup/parallel_worktree_backup_$Stamp"

function Invoke-ConfirmStep {
    param([string]$Msg)
    if ($SkipConfirm) { return $true }
    $r = Read-Host "$Msg  [y/N]"
    return $r -match '^[Yy]'
}

function Test-WorktreeBusy {
    # 是否有进程的可执行文件位于 worktree 内, 或命令行引用 worktree 路径
    param([string]$Path)
    $procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.ProcessId -ne $PID -and $_.Name -notmatch '^svchost|^System' -and (
                ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($Path, [System.StringComparison]::OrdinalIgnoreCase)) -or
                ($_.CommandLine -and $_.CommandLine.IndexOf($Path, [System.StringComparison]::OrdinalIgnoreCase) -ge 0)
            )
        }
    return $procs
}

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
Write-Host "`n=== 并行会话 worktree 清理 $(if ($Do) {'[执行]'} else {'[预演]'}) ==="
if ($Backup)  { Write-Host "  备份: $BackupRoot" }
if (-not $Do) { Write-Host "  提示: 加 -Execute 才会实际执行; 逐项会 Y/N 确认" }

foreach ($wt in $Worktrees) {
    $name = $wt.Name
    $path = $wt.Path
    Write-Host "`n----- $name ($path) -----"

    if (-not (Test-Path $path)) {
        Write-Host "  [跳过] 目录不存在（可能已清理）"
        # 从 git worktree 元数据兜底注销（若仍被登记）
        if ($Do -and $Remove) {
            git worktree remove --force "$path" 2>$null
            Write-Host "  [执行] git worktree remove 兜底完成"
        }
        continue
    }

    # 1) 未提交改动清单
    $status = git -C $path status --porcelain 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [警告] git status 失败（index 可能损坏）: $status"
        $staged = -1; $unstaged = -1; $untracked = -1
    } else {
        $staged   = ($status | Where-Object { $_ -notmatch '^\?\?' -and $_ -match '^[^ ]' }).Count
        $unstaged = ($status | Where-Object { $_ -match '^ [^ ]' }).Count
        $untracked = ($status | Where-Object { $_ -match '^\?\?' }).Count
        Write-Host "  staged=$staged unstaged=$unstaged untracked=$untracked"
    }

    # 2) 进程检查 / 终止
    $busy = Test-WorktreeBusy -Path $path
    if ($busy) {
        Write-Host "  发现 $(@($busy).Count) 个引用该 worktree 的进程:"
        $busy | ForEach-Object { Write-Host "    PID $($_.ProcessId) $($_.Name)" }
        if ($Do -and $Kill -and (Invoke-ConfirmStep "终止以上进程?")) {
            $busy | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
            Write-Host "  [执行] 进程已终止"
        }
    } else {
        Write-Host "  无活跃进程引用"
    }

    # 3) 备份未提交改动
    $hasChanges = ($staged -ne 0) -or ($unstaged -ne 0) -or ($untracked -ne 0)
    if ($Backup -and $hasChanges) {
        $dst = Join-Path $BackupRoot $name
        if ($Do) {
            New-Item -ItemType Directory -Path $dst -Force | Out-Null
            if ($staged -ne 0) { git -C $path diff --cached 2>$null | Set-Content -Path (Join-Path $dst 'staged.patch') }
            if ($unstaged -ne 0) { git -C $path diff 2>$null | Set-Content -Path (Join-Path $dst 'worktree.patch') }
            if ($untracked -ne 0) {
                $uDst = Join-Path $dst 'untracked'
                git -C $path ls-files --others --exclude-standard 2>$null | ForEach-Object {
                    $src = Join-Path $path $_
                    if (Test-Path $src) {
                        $tgt = Join-Path $uDst $_
                        New-Item -ItemType Directory -Path (Split-Path $tgt -Parent) -Force | Out-Null
                        Copy-Item -Path $src -Destination $tgt -Force
                    }
                }
            }
            Write-Host "  [执行] 已备份到 $dst"
        } else {
            Write-Host "  [预演] 将备份到 $dst"
        }
    }

    # 4) 未备份改动的保护
    if ($Do -and $Remove -and $hasChanges -and -not $Backup) {
        Write-Host "  [中止] $name 存在未提交改动且未 -Backup，拒绝移除（保护数据）。请加 -Backup 先备份。"
        continue
    }

    # 5) 移除 worktree
    if ($Do -and $Remove) {
        if (Invoke-ConfirmStep "移除 worktree $name (删除目录 $path)?") {
            # 先尝试标准注销；locked 或损坏时解锁重试
            git worktree unlock "$path" 2>$null
            git worktree remove --force "$path" 2>$null
            if (Test-Path $path) {
                # 兜底: index/gitdir 损坏导致 git 无法注销时直接删目录
                Remove-Item -Path $path -Recurse -Force -ErrorAction SilentlyContinue
            }
            Write-Host "  [执行] worktree $name 已移除"
        }
    } elseif (-not $Do -and $Remove) {
        Write-Host "  [预演] 将移除 worktree $name"
    }
}

# ---------------------------------------------------------------------------
# 收尾验证
# ---------------------------------------------------------------------------
Write-Host "`n=== 剩余 worktree ==="
git worktree list

if ($Do) {
    Write-Host "`n完成。备份目录: $BackupRoot"
    if ($SkipConfirm) { Write-Host "提示: 本次使用 -SkipConfirm，备份仍在，如需恢复请查看 backup/ 下 patch 文件。" }
} else {
    Write-Host "`n预演结束（未做任何改动）。确认后加 -Execute 执行。"
}
