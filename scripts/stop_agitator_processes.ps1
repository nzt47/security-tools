<#
.SYNOPSIS
    检测并终止本仓库后台干扰进程（自动提交劫持的根源）。

.DESCRIPTION
    针对 security-tools 仓库已知的后台自动进程（如 verify_bom_hook_stability.py
    的调度实例）进行检测与终止。这些进程会:
      - 自动 git add/commit/push（劫持提交消息、混入无关文件）
      - 修改工作区文件（如叠加 BOM: EF BB BF x2）
      - 与用户 git 操作竞争，导致 non-fast-forward / rebase 冲突 / 暂存区被清

    保守设计（守【不易】）:
      - 仅匹配 CommandLine 包含目标模式名的 python 进程，绝不误杀其他进程
      - 自动跳过当前进程自身
      - 默认 DryRun 仅报告; -Kill 才终止; -KillTree 才连带终止子进程（慎用）

.PARAMETER Kill
    终止匹配进程（默认仅报告，不终止）。

.PARAMETER KillTree
    同时终止匹配进程的直接子进程（git/pwsh 残留）。慎用：子进程可能属于其他任务。

.PARAMETER Pattern
    追加匹配模式（默认覆盖 verify_bom_hook_stability / simulate_workflow_closed_loop，
    即当前已确认的自动提交干扰源；历史观察名单见 docs/GIT_OPERATION_SAFETY_GUIDE.md）。

.PARAMETER AllPython
    列出所有 python 进程（诊断辅助，不匹配目标模式也显示）。

.PARAMETER Json
    stdout 仅输出单行 JSON（人类输出走 stderr），供脚本/CI 消费。

.EXAMPLE
    .\scripts\stop_agitator_processes.ps1              # 仅报告当前干扰进程
    .\scripts\stop_agitator_processes.ps1 -Kill        # 终止匹配进程
    .\scripts\stop_agitator_processes.ps1 -KillTree    # 终止匹配进程及其子进程
    .\scripts\stop_agitator_processes.ps1 -Pattern simulate_foo -Kill
    .\scripts\stop_agitator_processes.ps1 -AllPython -Json

退出码: 0 = 无匹配 / 匹配且已全部处理; 1 = 存在匹配但未终止（DryRun 或终止失败）
#>
[CmdletBinding()]
param(
    [switch]$Kill,
    [switch]$KillTree,
    [string[]]$Pattern = @('verify_bom_hook_stability', 'simulate_workflow_closed_loop'),
    [switch]$AllPython,
    [switch]$Json
)

$ErrorActionPreference = 'SilentlyContinue'

# --Json 强制安静: 人类可读进度走 stderr, stdout 仅 JSON（与仓库工具约定一致）
function Log-Human {
    param([string]$Msg)
    if (-not $Json) { Write-Host $Msg }
}

function Get-Agitators {
    param([string[]]$Patterns)

    $procs = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'python3.exe' OR Name = 'py.exe'"
    $selfPid = $PID
    $results = @()

    foreach ($p in $procs) {
        if ($null -eq $p.CommandLine) { continue }
        if ($p.ProcessId -eq $selfPid) { continue }

        $matched = $false
        $hitPattern = ''
        if ($AllPython) {
            $matched = $true
            $hitPattern = '(all-python)'
        } else {
            foreach ($pat in $Patterns) {
                if ($p.CommandLine -like "*$pat*") {
                    $matched = $true
                    $hitPattern = $pat
                    break
                }
            }
        }
        if ($matched) {
            $results += [PSCustomObject]@{
                Id          = $p.ProcessId
                ParentId    = $p.ParentProcessId
                Name        = $p.Name
                Pattern     = $hitPattern
                CommandLine = $p.CommandLine
            }
        }
    }
    return $results
}

function Get-DirectChildren {
    param([int[]]$ParentIds)
    $all = Get-CimInstance Win32_Process
    $children = @()
    foreach ($c in $all) {
        if ($ParentIds -contains $c.ParentProcessId) {
            $children += $c
        }
    }
    return $children
}

# ── 主流程 ──
$mode = if ($Kill) { 'kill' } else { 'report' }
Log-Human "=== 后台干扰进程检测 ==="
Log-Human "  模式: $mode | 匹配模式: $($Pattern -join ', ') | 当前 PID: $PID"

$found = @(Get-Agitators -Patterns $Pattern)
Log-Human "  发现匹配进程: $($found.Count)"

$killed = 0
$failed = 0
$killTreeList = @()

foreach ($f in $found) {
    $cmdShort = if ($f.CommandLine.Length -gt 120) { $f.CommandLine.Substring(0, 120) + '...' } else { $f.CommandLine }
    Log-Human ("  [{0}] PID={1} (parent={2}) {3} -> {4}" -f $f.Pattern, $f.Id, $f.ParentId, $f.Name, $cmdShort)

    if ($Kill) {
        try {
            Stop-Process -Id $f.Id -Force -ErrorAction Stop
            $killed++
            if ($KillTree) { $killTreeList += $f.Id }
        } catch {
            $failed++
            Log-Human "  [WARN] 终止 PID $($f.Id) 失败: $($_.Exception.Message)"
        }
    }
}

# -KillTree: 终止被终止进程的直接子进程（git/pwsh 残留，防其继续 git 操作）
if ($Kill -and $KillTree -and $killTreeList.Count -gt 0) {
    $children = @(Get-DirectChildren -ParentIds $killTreeList)
    foreach ($c in $children) {
        try {
            Stop-Process -Id $c.ProcessId -Force -ErrorAction Stop
            Log-Human "  [tree] 终止子进程 PID=$($c.ProcessId) ($($c.Name))"
            $killed++
        } catch {
            $failed++
        }
    }
}

# 结果
$report = [ordered]@{
    tool       = 'stop_agitator_processes'
    mode       = $mode
    patterns   = $Pattern
    found      = $found.Count
    killed     = $killed
    failed     = $failed
    detected   = @($found | ForEach-Object { @{ id = $_.Id; parent = $_.ParentId; name = $_.Name; pattern = $_.Pattern } })
    generated_at = (Get-Date).ToUniversalTime().ToString('o')
}

if ($Json) {
    $report | ConvertTo-Json -Compress -Depth 5 | Write-Output
} else {
    Log-Human ""
    Log-Human "=== 汇总 ==="
    Log-Human "  发现: $($found.Count) | 已终止: $killed | 失败: $failed"
    if ($found.Count -gt 0 -and -not $Kill) {
        Log-Human "  [HINT] 未终止任何进程（DryRun）。确认后运行: $($MyInvocation.MyCommand.Path) -Kill" -ForegroundColor Yellow
    }
    if ($failed -gt 0) {
        Log-Human "  [WARN] 存在终止失败，可能需管理员权限重试" -ForegroundColor Red
    }
}

# 退出码: 有匹配且(DryRun 或失败) -> 1; 否则 0
if ($found.Count -gt 0 -and ($killed -lt $found.Count -or -not $Kill)) { exit 1 }
exit 0
