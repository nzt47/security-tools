#Requires -Version 5.1
<#
.SYNOPSIS
    创建 7 天后自动删除 .env 备份文件的计划任务
.DESCRIPTION
    在 Windows 任务计划程序中注册一次性任务，7 天后自动删除指定的 .env.backup 文件。
    目的：清理包含旧密码的备份文件，避免密码泄露风险。
    【不易】备份文件包含旧密码，长期保留有安全风险，需定期清理。
.PARAMETER BackupFile
    要删除的 .env.backup 文件路径（必填）
.PARAMETER DelayDays
    延迟删除天数，默认 7
.PARAMETER TaskName
    计划任务名称，默认 "DeleteEnvBackup_<时间戳>"
.EXAMPLE
    .\schedule_backup_cleanup.ps1 -BackupFile "c:\Users\Administrator\agent\.env.backup.20260722184348"
    7 天后自动删除指定备份文件
.EXAMPLE
    .\schedule_backup_cleanup.ps1 -BackupFile "C:\path\to\.env.backup" -DelayDays 14
    14 天后自动删除
.NOTES
    执行后会在 Windows 事件日志中记录删除结果（Application 日志，EventId 1）。
    如需提前取消，运行：Unregister-ScheduledTask -TaskName "DeleteEnvBackup_xxx" -Confirm:$false
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true, HelpMessage="要删除的 .env.backup 文件路径")]
    [string]$BackupFile,

    [int]$DelayDays = 7,

    [string]$TaskName = "DeleteEnvBackup_$(Get-Date -Format 'yyyyMMddHHmmss')"
)

$ErrorActionPreference = 'Stop'

# ── Stage 0: 预检查 ────────────────────────────────────────────
Write-Host "========== Stage 0: 预检查 ==========" -ForegroundColor Cyan

if (-not (Test-Path $BackupFile)) {
    Write-Host "[ERR ] 备份文件不存在: $BackupFile" -ForegroundColor Red
    exit 1
}

$fileInfo = Get-Item $BackupFile
Write-Host "[OK] 备份文件存在: $($fileInfo.FullName)" -ForegroundColor Green
Write-Host "     大小: $('{0:N2}' -f ($fileInfo.Length/1KB)) KB"
Write-Host "     修改时间: $($fileInfo.LastWriteTime)"

# ── Stage 1: 计算触发时间 ──────────────────────────────────────
Write-Host "`n========== Stage 1: 计算触发时间 ==========" -ForegroundColor Cyan

$triggerTime = (Get-Date).AddDays($DelayDays)
Write-Host "[INFO] 当前时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host "[INFO] 触发时间: $($triggerTime.ToString('yyyy-MM-dd HH:mm:ss'))（${DelayDays} 天后）"

# ── Stage 2: 创建计划任务 ──────────────────────────────────────
Write-Host "`n========== Stage 2: 创建计划任务 ==========" -ForegroundColor Cyan

# 任务动作：删除文件 + 写入事件日志
$deleteCommand = @"
`$ErrorActionPreference = 'SilentlyContinue'
Remove-Item '$BackupFile' -Force
`$exists = Test-Path '$BackupFile'
if (-not `$exists) {
    Write-EventLog -LogName Application -Source 'Application Error' -EventId 1 -EntryType Information -Message "DeleteEnvBackup: 已删除 $BackupFile"
} else {
    Write-EventLog -LogName Application -Source 'Application Error' -EventId 1 -EntryType Warning -Message "DeleteEnvBackup: 删除失败 $BackupFile"
}
"@

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-WindowStyle Hidden -NoProfile -Command `"$deleteCommand`""

$trigger = New-ScheduledTaskTrigger -Once -At $triggerTime

# 任务设置：执行后自动删除任务本身
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Description "${DelayDays}天后删除.env备份文件: $BackupFile" `
        -Force | Out-Null

    Write-Host "[OK] 计划任务已创建: $TaskName" -ForegroundColor Green
} catch {
    Write-Host "[ERR ] 创建计划任务失败: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "[INFO] 请以管理员身份运行 PowerShell 后重试" -ForegroundColor Yellow
    exit 1
}

# ── Stage 3: 验证任务 ──────────────────────────────────────────
Write-Host "`n========== Stage 3: 验证计划任务 ==========" -ForegroundColor Cyan

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    $taskInfo = $task | Get-ScheduledTaskInfo
    Write-Host "[OK] 任务名称: $($task.TaskName)" -ForegroundColor Green
    Write-Host "     任务状态: $($task.State)"
    Write-Host "     下次运行: $($taskInfo.NextRunTime)"
    Write-Host "     任务路径: \$($task.TaskPath)$($task.TaskName)"
} else {
    Write-Host "[ERR ] 任务验证失败，任务未找到" -ForegroundColor Red
    exit 1
}

# ── Stage 4: 汇总 ──────────────────────────────────────────────
Write-Host "`n========== Stage 4: 汇总 ==========" -ForegroundColor Cyan
Write-Host "  备份文件: $BackupFile" -ForegroundColor White
Write-Host "  删除时间: $($triggerTime.ToString('yyyy-MM-dd HH:mm:ss'))" -ForegroundColor White
Write-Host "  任务名称: $TaskName" -ForegroundColor White
Write-Host ""
Write-Host "  ── 管理命令 ──" -ForegroundColor Cyan
Write-Host "  查看任务: Get-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Gray
Write-Host "  立即执行: Start-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Gray
Write-Host "  取消任务: Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false" -ForegroundColor Gray
Write-Host ""
Write-Host "[OK] 计划任务创建完成，将在 ${DelayDays} 天后自动删除备份文件" -ForegroundColor Green
