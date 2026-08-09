﻿#Requires -Version 5.1
<#
保护核心源文件脚本（防止 IDE 自动还原导致修改丢失）

功能（三种模式）：
  1. check   : 比对源文件与 .fix_backups/ 最新备份的哈希，检测哪些文件被还原
  2. watch   : 持续轮询监控文件变化（默认每 2 秒），变化后自动备份到 .fix_backups/
  3. restore : 从 .fix_backups/ 的最新备份恢复源文件

用法示例：
  .\scripts\protect_source_files.ps1 -Action check
  .\scripts\protect_source_files.ps1 -Action watch -WatchIntervalSec 2
  .\scripts\protect_source_files.ps1 -Action restore

说明：
  - watch 使用轮询（LastWriteTime + 文件大小）而非 FileSystemWatcher，
    因为变化事件可能在 IDE 保存完成后才触发，轮询更可靠且不受事件丢失影响。
  - 备份文件命名：<源文件名>_<yyyyMMdd_HHmmss>.<ext>，保留历史版本。
#>
param(
    [ValidateSet("check", "watch", "restore")]
    [string]$Action = "check",

    # 被监控/保护的文件列表（相对项目根目录）
    [string[]]$Files = @(
        "agent\monitoring\tracing.py",
        "agent\error_handler.py",
        "agent\monitoring\metrics.py",
        "tests\unit\test_performance_alert.py"
    ),

    # watch 模式的轮询间隔（秒）
    [int]$WatchIntervalSec = 2,

    # 项目根目录
    [string]$RootDir = "C:\Users\Administrator\agent"
)

$ErrorActionPreference = "Stop"
$BackupDir = Join-Path $RootDir ".fix_backups"

# 结构化日志辅助函数（遵循可观测性规则：trace_id / module_name / action / duration_ms）
function Write-StructuredLog {
    param(
        [string]$Action,
        [string]$Message,
        [int]$DurationMs = 0,
        [string]$Level = "INFO"
    )
    $log = [ordered]@{
        trace_id    = [guid]::NewGuid().ToString("N").Substring(0, 16)
        module_name = "protect_source_files"
        action      = $Action
        duration_ms = $DurationMs
        message     = $Message
    }
    $json = $log | ConvertTo-Json -Compress
    if ($Level -eq "ERROR") { Write-Host "[ERROR] $json" -ForegroundColor Red }
    elseif ($Level -eq "WARN") { Write-Host "[WARN] $json" -ForegroundColor Yellow }
    else { Write-Host "[INFO] $json" }
}

function Get-BackupTimeStamp {
    return Get-Date -Format "yyyyMMdd_HHmmss"
}

function Backup-File {
    param(
        [string]$SourceFullPath
    )
    if (!(Test-Path $SourceFullPath)) { return $null }

    $name = [System.IO.Path]::GetFileNameWithoutExtension($SourceFullPath)
    $ext  = [System.IO.Path]::GetExtension($SourceFullPath)
    $backupName = "${name}_$(Get-BackupTimeStamp)${ext}"
    $backupPath = Join-Path $BackupDir $backupName

    Copy-Item $SourceFullPath $backupPath -Force
    return $backupPath
}

# 确保备份目录存在
if (!(Test-Path $BackupDir)) {
    New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
    Write-StructuredLog -Action "backup_dir.create" -Message "创建备份目录: $BackupDir"
}

# 获取文件的最新备份（按修改时间排序取最后一个）
function Get-LatestBackup {
    param([string]$BaseNameWithExt)
    $backups = Get-ChildItem $BackupDir -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "${BaseNameWithExt}*" -or $_.Name -eq ($BaseNameWithExt + ".fixed") } |
        Sort-Object LastWriteTime
    if ($backups.Count -eq 0) { return $null }
    return $backups[-1]
}

switch ($Action) {
    "check" {
        Write-StructuredLog -Action "check.start" -Message "开始检查 $($Files.Count) 个文件是否被还原"
        $issues = 0
        foreach ($rel in $Files) {
            $full = Join-Path $RootDir $rel
            if (!(Test-Path $full)) {
                Write-StructuredLog -Action "check.missing" -Level "WARN" -Message "源文件不存在: $rel"
                continue
            }
            $hash = (Get-FileHash $full -Algorithm SHA256).Hash
            $base = [System.IO.Path]::GetFileName($full)
            $latest = Get-LatestBackup -BaseNameWithExt $base
            if ($null -eq $latest) {
                Write-StructuredLog -Action "check.no_backup" -Level "WARN" -Message "无备份可比对: $rel"
                continue
            }
            $backupHash = (Get-FileHash $latest.FullName -Algorithm SHA256).Hash
            if ($hash -eq $backupHash) {
                Write-StructuredLog -Action "check.ok" -Message "一致: $rel (备份: $($latest.Name))"
            }
            else {
                $issues++
                Write-StructuredLog -Action "check.mismatch" -Level "WARN" -Message "内容不一致(可能被还原): $rel | 源哈希=$hash | 备份=$($latest.Name) 哈希=$backupHash"
            }
        }
        if ($issues -eq 0) {
            Write-StructuredLog -Action "check.done" -Message "检查完成，全部文件与备份一致"
        }
        else {
            Write-StructuredLog -Action "check.done" -Level "ERROR" -Message "检查完成，$issues 个文件与备份不一致，建议执行 restore 恢复"
        }
    }

    "watch" {
        Write-StructuredLog -Action "watch.start" -Message "开始监控 $($Files.Count) 个文件（间隔 ${WatchIntervalSec}s），Ctrl+C 退出"
        # 记录每个文件的初始状态 {LastWriteTime, Length}
        $state = @{}
        foreach ($rel in $Files) {
            $full = Join-Path $RootDir $rel
            if (Test-Path $full) {
                $item = Get-Item $full
                $state[$full] = @{ LastWrite = $item.LastWriteTime.Ticks; Length = $item.Length }
            }
        }
        while ($true) {
            Start-Sleep -Seconds $WatchIntervalSec
            foreach ($rel in $Files) {
                $full = Join-Path $RootDir $rel
                if (!(Test-Path $full)) {
                    Write-StructuredLog -Action "watch.missing" -Level "WARN" -Message "文件消失: $rel"
                    continue
                }
                $item = Get-Item $full
                $current = @{ LastWrite = $item.LastWriteTime.Ticks; Length = $item.Length }
                if ($state.ContainsKey($full)) {
                    $prev = $state[$full]
                    if ($prev.LastWrite -ne $current.LastWrite -or $prev.Length -ne $current.Length) {
                        $sw = [System.Diagnostics.Stopwatch]::StartNew()
                        $backupPath = Backup-File $full
                        $sw.Stop()
                        Write-StructuredLog -Action "watch.backup" -DurationMs $sw.ElapsedMilliseconds -Message "检测到变化，已备份: $rel -> $([System.IO.Path]::GetFileName($backupPath))"
                    }
                }
                $state[$full] = $current
            }
        }
    }

    "restore" {
        Write-StructuredLog -Action "restore.start" -Message "开始从备份恢复文件"
        $restored = 0
        foreach ($rel in $Files) {
            $full = Join-Path $RootDir $rel
            $base = [System.IO.Path]::GetFileName($full)
            $latest = Get-LatestBackup -BaseNameWithExt $base
            if ($null -eq $latest) {
                Write-StructuredLog -Action "restore.no_backup" -Level "WARN" -Message "无备份，跳过: $rel"
                continue
            }
            # 优先使用 .fixed 备份（手动修复的最终版本）
            $fixedBackup = Join-Path $BackupDir ($base + ".fixed")
            $source = if (Test-Path $fixedBackup) { $fixedBackup } else { $latest.FullName }
            Copy-Item $source $full -Force
            Write-StructuredLog -Action "restore.ok" -Message "已恢复: $rel <- $([System.IO.Path]::GetFileName($source))"
            $restored++
        }
        Write-StructuredLog -Action "restore.done" -Message "恢复完成，共 $restored 个文件"
    }
}
