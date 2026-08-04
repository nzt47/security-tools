<#
.SYNOPSIS
    定期清理 WMI 性能库缓存与 Python 文件系统扫描缓存，防止全量测试因 IO 超时卡死

.DESCRIPTION
    针对 2026-08-02 全量测试在 test_digital_life.py（WMI Win32_Service 查询超时）
    与 test_utils_index_manager.py（importlib _fill_cache 文件系统扫描卡顿）两处 IO 超时，
    提供三类清理：
      1. Python 字节码/测试缓存：__pycache__ / .pytest_cache / .mypy_cache
      2. WMI ADAP 性能库缓存：winmgmt /clearadap（重新编译性能库，不改动仓库数据）
      3. 可选：验证 WMI 仓库完整性（-VerifyWmi，只读不清理）

.PARAMETER DryRun
    仅显示将要清理的内容，不实际执行（推荐先预演）

.PARAMETER RestartWmi
    清理完成后重启 Winmgmt 服务。
    高风险：会中断所有依赖 WMI 的进程（含任务计划程序、性能监视器等），默认关闭。

.PARAMETER VerifyWmi
    仅验证 WMI 仓库完整性（winmgmt /verifyrepository），不执行清理。

.PARAMETER RegisterTask
    注册每日计划任务（schtasks，每日 02:00，SYSTEM 最高权限）。

.EXAMPLE
    # 预演（推荐先执行）
    pwsh -File .\scripts\cleanup_io_cache.ps1 -DryRun

.EXAMPLE
    # 正式执行
    pwsh -File .\scripts\cleanup_io_cache.ps1

.EXAMPLE
    # 注册每日自动清理计划任务
    pwsh -File .\scripts\cleanup_io_cache.ps1 -RegisterTask

.NOTES
    关联文档：docs/CI_GIT_PULL_REBASE_FIX.md（全量测试 IO 超时根因记录）
    前置条件：WMI 相关操作需要管理员权限（脚本会自动检测并在无权限时跳过 WMI 部分）
    删除计划任务：schtasks /Delete /TN YunshuIOCacheCleanup /F
#>

[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$RestartWmi,
    [switch]$VerifyWmi,
    [switch]$RegisterTask
)

$ErrorActionPreference = "Continue"

# ========== 项目根目录 ==========
$ProjectRoot = Split-Path -Parent $PSScriptRoot

# ========== 统计 ==========
$script:Stats = @{
    PyCacheFound   = 0
    PyCacheDeleted = 0
    PyCacheSize    = 0
    Failed         = 0
    StartTime      = Get-Date
}

# ========== 工具函数 ==========
function Write-Section {
    param([string]$Title)
    Write-Host ""
    Write-Host ("=" * 60) -ForegroundColor Cyan
    Write-Host "  $Title" -ForegroundColor Cyan
    Write-Host ("=" * 60) -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Msg)
    Write-Host "  [OK]   $Msg" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Msg)
    Write-Host "  [WARN] $Msg" -ForegroundColor DarkYellow
}

function Write-Err {
    param([string]$Msg)
    Write-Host "  [FAIL] $Msg" -ForegroundColor Red
}

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# ========== 清理 Python 缓存（无需管理员权限） ==========
function Clear-PythonCache {
    param([string]$Root)
    $cacheDirs = @("__pycache__", ".pytest_cache", ".mypy_cache")
    $excludedTop = @("venv", "env", ".venv", "node_modules", ".git")

    # 收集所有缓存目录（顶层非递归 + 各子目录递归），避免清理时动态遍历导致遗漏
    # 【变易】从源头排除 venv/env/node_modules：递归遍历虚拟环境会扫描数万
    # 个 __pycache__（Windows 上可达分钟级），且这些目录本就不应被清理。
    # 【简易】顶层用非递归（只取 $Root 自身目录下的缓存），子目录再各自递归，
    # 避免"顶层递归 + 子目录递归"双重收集导致的重复删除（2026-08-02 实测
    # 曾重复收集导致 99 个 "Cannot find path" 误报）。
    $all = @()
    foreach ($dir in $cacheDirs) {
        # 顶层：$Root 自身目录下直接查找（非递归）
        $all += Get-ChildItem -Path $Root -Directory -Filter $dir -Force -ErrorAction SilentlyContinue
        # 子目录：排除 venv/env 等后逐个递归
        $subs = Get-ChildItem -Path $Root -Directory -Force -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -notin $excludedTop }
        foreach ($sub in $subs) {
            $all += Get-ChildItem -Path $sub.FullName -Directory -Filter $dir -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    foreach ($d in $all) {
        # 双保险：仍过滤掉可能嵌套在深层虚拟环境/第三方库内的缓存目录
        $rel = $d.FullName.Substring($Root.Length).TrimStart('\', '/')
        if ($rel -match '(^|\\)(venv|env|\.venv|node_modules)(\\|$)') {
            continue
        }
        $size = 0
        $files = Get-ChildItem -Path $d.FullName -File -Recurse -Force -ErrorAction SilentlyContinue
        $size = ($files | Measure-Object -Property Length -Sum).Sum

        $script:Stats.PyCacheFound++
        $script:Stats.PyCacheSize += $size
        Write-Host ("    {0,-12} {1,10:N1} KB  {2}" -f $d.Name, ($size / 1KB), $rel) -ForegroundColor DarkGray

        if (-not $DryRun) {
            try {
                Remove-Item -Path $d.FullName -Recurse -Force -ErrorAction Stop
                $script:Stats.PyCacheDeleted++
            } catch {
                $script:Stats.Failed++
                Write-Warn "      删除失败: $_"
            }
        }
    }
}

# ========== 清理 WMI 性能库缓存（需管理员权限） ==========
function Clear-WmiAdapterCache {
    if (-not (Test-Admin)) {
        Write-Warn "非管理员权限，跳过 WMI 清理。请以管理员身份运行以启用 WMI 部分。"
        return
    }
    if ($DryRun) {
        Write-Host "  [DRY] 将执行: winmgmt /resyncperf (重新注册系统性能库，清除 WMI 性能计数器缓存)" -ForegroundColor DarkYellow
        return
    }
    Write-Host "  [RUN] 执行 winmgmt /resyncperf ..." -ForegroundColor Cyan
    try {
        $out = & winmgmt /resyncperf 2>&1
        $code = $LASTEXITCODE
        if ($code -eq 0) {
            Write-Ok "WMI 性能库缓存已重新注册"
        } else {
            Write-Warn "winmgmt /resyncperf 返回码 $code : $out"
        }
    } catch {
        Write-Warn "winmgmt /resyncperf 执行异常: $_"
    }
}

# ========== 验证 WMI 仓库（只读） ==========
function Verify-WmiRepository {
    if (-not (Test-Admin)) {
        Write-Warn "非管理员权限，跳过 WMI 仓库验证。"
        return
    }
    Write-Host "  [RUN] 执行 winmgmt /verifyrepository ..." -ForegroundColor Cyan
    try {
        $out = & winmgmt /verifyrepository 2>&1
        $code = $LASTEXITCODE
        Write-Host "  $out" -ForegroundColor Gray
        if ($code -eq 0) {
            Write-Ok "WMI 仓库完整（返回码 $code）"
        } else {
            Write-Warn "WMI 仓库异常（返回码 $code）。可尝试: winmgmt /salvagerepository 或 winmgmt /resetrepository（高风险，需人工确认）"
        }
    } catch {
        Write-Warn "winmgmt /verifyrepository 执行异常: $_"
    }
}

# ========== 重启 WMI 服务（高风险，默认不启用） ==========
function Restart-WmiService {
    if (-not (Test-Admin)) {
        Write-Warn "非管理员权限，跳过 WMI 服务重启。"
        return
    }
    Write-Host "  [RUN] 重启 Winmgmt 服务 ..." -ForegroundColor Cyan
    try {
        Restart-Service -Name Winmgmt -Force -ErrorAction Stop
        Write-Ok "Winmgmt 服务已重启"
    } catch {
        Write-Err "重启 Winmgmt 失败: $_"
        $script:Stats.Failed++
    }
}

# ========== 注册每日计划任务 ==========
function Register-CleanupTask {
    $taskName = "YunshuIOCacheCleanup"
    $scriptPath = Join-Path $PSScriptRoot "cleanup_io_cache.ps1"

    # 已存在则先删除，保证 /F 幂等
    schtasks /Delete /TN "$taskName" /F 2>$null | Out-Null

    Write-Host "  [RUN] 注册计划任务 $taskName ..." -ForegroundColor Cyan
    schtasks /Create `
        /TN "$taskName" `
        /TR "powershell.exe -ExecutionPolicy Bypass -File `"$scriptPath`"" `
        /SC DAILY `
        /ST 02:00 `
        /RL HIGHEST `
        /F `
        /RU SYSTEM

    if ($LASTEXITCODE -eq 0) {
        Write-Ok "计划任务注册成功: 每日 02:00 (SYSTEM 权限)"
        schtasks /Query /TN "$taskName" /FO LIST | Out-Host
    } else {
        Write-Err "计划任务注册失败 (退出码 $LASTEXITCODE)"
        $script:Stats.Failed++
    }
}

# ========== 主流程 ==========
Write-Section "IO Cache Cleanup Script"
Write-Host "  ProjectRoot: $ProjectRoot"
Write-Host "  Mode:        $(if ($DryRun) { 'DRY-RUN (no changes)' } else { 'EXECUTE' })"

# -- 仅验证模式 --
if ($VerifyWmi) {
    Write-Section "Verify WMI Repository"
    Verify-WmiRepository
    exit 0
}

# -- 清理 Python 缓存 --
Write-Section "Stage 1: Python Cache (__pycache__ / .pytest_cache / .mypy_cache)"
if (-not (Test-Path $ProjectRoot)) {
    Write-Err "项目根目录不存在: $ProjectRoot"
    exit 1
}
Clear-PythonCache -Root $ProjectRoot

# -- 清理 WMI 缓存 --
Write-Section "Stage 2: WMI ADAP Performance Cache"
Clear-WmiAdapterCache

if ($RestartWmi) {
    Write-Section "Stage 3: Restart Winmgmt Service (高风险)"
    Write-Warn "重启 WMI 服务会中断依赖 WMI 的进程。正在执行..."
    Restart-WmiService
}

# -- 注册计划任务 --
if ($RegisterTask) {
    Write-Section "Stage 3: Register Daily Scheduled Task"
    Register-CleanupTask
}

# ========== 汇总 ==========
Write-Section "Cleanup Summary"
$elapsed = [math]::Round(((Get-Date) - $script:Stats.StartTime).TotalSeconds, 1)
$sizeMB = [math]::Round($script:Stats.PyCacheSize / 1MB, 2)
Write-Host "  Python 缓存目录:  找到 $($script:Stats.PyCacheFound) 个"
if (-not $DryRun) {
    Write-Host "  已删除:           $($script:Stats.PyCacheDeleted) 个"
    Write-Host "  释放空间:          $sizeMB MB"
}
Write-Host "  失败项:            $($script:Stats.Failed)"
Write-Host "  耗时:              $elapsed s"

if ($DryRun) {
    Write-Host ""
    Write-Warn ">>> DRY-RUN complete, nothing changed <<<"
    Write-Host "  正式执行: pwsh -File $($MyInvocation.MyCommand.Path)" -ForegroundColor Yellow
} else {
    Write-Host ""
    Write-Ok ">>> Cleanup complete <<<"
}

# ========== 验证（非 DryRun） ==========
if (-not $DryRun -and -not $RegisterTask) {
    Write-Section "Stage 4: Verify"
    $remaining = (Get-ChildItem -Path $ProjectRoot -Directory -Filter "__pycache__" -Recurse -Force -ErrorAction SilentlyContinue).Count
    if ($remaining -eq 0) {
        Write-Ok "所有 __pycache__ 已清除"
    } else {
        Write-Warn "仍有 $remaining 个 __pycache__（可能被占用）"
    }
}

Write-Host ""
