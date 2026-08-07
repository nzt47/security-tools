# 每日自动备份定时任务：归档昨日日志 + 运行 auto_backup_untracked
#
# 背景: auto_backup_untracked.ps1 已支持每次运行自动追加日志(backup/logs/)。
#   本脚本将其固化为每日凌晨任务: 先归档昨日日志到 archive/, 再执行备份。
#
# 设计(三义):
# - 不易: 备份逻辑零改动, 仅复用 auto_backup_untracked.ps1(单一事实源);
#        归档只移动日志文件, 不删除(可追溯)。
# - 变易: -Time 指定任务时间(默认 02:30) / -Register 注册计划任务 /
#        -Unregister 注销任务。归档保留最近 N 天(-KeepDays 默认 30)。
# - 简易: 直接执行=归档+备份; -Register 仅注册 Windows 计划任务, 不立即执行。
#
# 用法:
#   powershell -File scripts/dev/daily_backup_task.ps1 -Register       # 注册(每天 02:30 自动跑)
#   powershell -File scripts/dev/daily_backup_task.ps1 -Register -Time "03:00"
#   powershell -File scripts/dev/daily_backup_task.ps1                # 手动执行(归档+备份)
#   powershell -File scripts/dev/daily_backup_task.ps1 -Unregister    # 注销任务
#
# 注意: 计划任务以当前用户身份运行, 需保证登录环境可访问 git/python;
#   任务动作会调用 powershell -File 本脚本(不带参数=直接执行)。

[CmdletBinding()]
param(
    [switch]$Register,
    [switch]$Unregister,
    [string]$Time = "02:30",
    [int]$KeepDays = 30
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$taskName = "YunShu-DailyBackupUntracked"

# ── A. 注册 / 注销计划任务 ──
if ($Register -or $Unregister) {
    if ($Unregister) {
        & schtasks /Delete /TN $taskName /F 2>&1 | Out-Null
        Write-Host "[OK] 已注销计划任务: $taskName" -ForegroundColor Green
        exit 0
    }
    $action = "powershell -NoProfile -ExecutionPolicy Bypass -File `"$PSScriptRoot\daily_backup_task.ps1`""
    & schtasks /Create /TN $taskName /TR $action /SC DAILY /ST $Time /F 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[OK] 已注册计划任务: $taskName (每天 $Time 运行)" -ForegroundColor Green
        & schtasks /Query /TN $taskName /V /FO LIST 2>&1 | Select-String "任务名|Task To Run|开始时间|Schedule|Start Time|Task To Run" | Select-Object -First 5
    } else {
        Write-Error "计划任务注册失败 (schtasks exit $LASTEXITCODE)"
        exit 1
    }
    exit 0
}

# ── B. 直接执行: 归档昨日日志 → 运行备份 ──
Write-Host "=== 每日备份任务开始: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" -ForegroundColor Cyan

# B1. 归档日志: 非今日日志移入 archive/, 保留 KeepDays 天
$logDir = Join-Path $repoRoot "backup\logs"
$archiveDir = Join-Path $logDir "archive"
if (Test-Path $logDir) {
    $today = Get-Date -Format "yyyyMMdd"
    $stale = Get-ChildItem $logDir -File -Filter "backup_untracked_*.log" |
        Where-Object { $_.BaseName -notlike "*$today" }
    if ($stale) {
        New-Item -ItemType Directory -Force -Path $archiveDir | Out-Null
        $stale | ForEach-Object {
            Move-Item $_.FullName (Join-Path $archiveDir $_.Name) -Force
            Write-Host "[ARCHIVE] $($_.Name) -> archive/" -ForegroundColor Yellow
        }
    }
    # 清理超期归档(>KeepDays 天)
    $expired = Get-ChildItem $archiveDir -File -Filter "backup_untracked_*.log" -ErrorAction SilentlyContinue |
        Where-Object { (Get-Date) - $_.LastWriteTime -gt (New-TimeSpan -Days $KeepDays) }
    $expired | ForEach-Object {
        Remove-Item $_.FullName -Force
        Write-Host "[PURGE] 已清理超期归档: $($_.Name)" -ForegroundColor DarkYellow
    }
}

# B2. 运行备份(自动追加今日日志)
Write-Host "=== 执行 auto_backup_untracked ===" -ForegroundColor Cyan
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "auto_backup_untracked.ps1")
if ($LASTEXITCODE -ne 0) {
    Write-Error "备份失败 (exit $LASTEXITCODE)"
    exit $LASTEXITCODE
}
Write-Host "=== 每日备份任务完成 ===" -ForegroundColor Green
