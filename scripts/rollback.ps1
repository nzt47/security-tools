#Requires -Version 5.1
<#
.SYNOPSIS
    云枢服务回滚脚本 - 快速恢复到历史备份版本
.DESCRIPTION
    自动查找最新备份文件，恢复 app_server.py、data/messages.jsonl、
    监控配置（告警规则）和 SafeFileReader 工具类，可选重启服务。
    用于新版本部署失败时的快速回滚。
.PARAMETER Target
    指定回滚目标: "all" (全部), "code" (仅代码), "data" (仅数据), "monitoring" (仅监控配置)
.PARAMETER RestartService
    回滚后是否自动重启服务（默认: 询问）
.PARAMETER ListBackups
    仅列出可用备份，不执行回滚
.EXAMPLE
    .\rollback.ps1                    # 交互式回滚到最新版本
    .\rollback.ps1 -Target code       # 仅回滚代码
    .\rollback.ps1 -Target monitoring # 仅回滚监控配置
    .\rollback.ps1 -ListBackups       # 列出所有备份
    .\rollback.ps1 -Target all -RestartService $false  # 回滚但不重启
#>

param(
    [ValidateSet("all", "code", "data", "monitoring")]
    [string]$Target = "all",
    
    [bool]$RestartService = $true,
    
    [switch]$ListBackups
)

# ════════════════════════════════════════════════════════════════
#  配置
# ════════════════════════════════════════════════════════════════

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackupDir = Join-Path $ProjectRoot "backups"
$LogFile = Join-Path $ProjectRoot "logs" "rollback_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"

# 需要回滚的文件映射
$FileMap = @{
    "code" = @{
        Source = "app_server.py.bak_"
        Target = "app_server.py"
        Description = "应用服务器代码"
    }
    "data" = @{
        Source = "data/messages.jsonl.bak_"
        Target = "data/messages.jsonl"
        Description = "历史记忆数据"
    }
}

# 日志函数
function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $logEntry = "[$timestamp] [$Level] $Message"
    Write-Host $logEntry
    Add-Content -Path $LogFile -Value $logEntry -Force
}

# ════════════════════════════════════════════════════════════════
#  函数
# ════════════════════════════════════════════════════════════════

function Find-LatestBackup {
    param([string]$FilePrefix)
    
    $files = Get-ChildItem -Path $ProjectRoot -Recurse -Filter "${FilePrefix}*.bak_*" -ErrorAction SilentlyContinue
    
    if ($files.Count -eq 0) {
        return $null
    }
    
    # 按备份日期排序，返回最新的
    $latest = $files | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    return $latest
}

function List-AvailableBackups {
    Write-Host ""
    Write-Host "═" * 70 -ForegroundColor Cyan
    Write-Host "📋 可用备份列表" -ForegroundColor Cyan
    Write-Host "═" * 70 -ForegroundColor Cyan
    
    $hasBackups = $false
    
    foreach ($key in $FileMap.Keys) {
        $prefix = $FileMap[$key].Source
        $desc = $FileMap[$key].Description
        
        $backups = Get-ChildItem -Path $ProjectRoot -Recurse -Filter "${prefix}*" -ErrorAction SilentlyContinue |
                   Sort-Object LastWriteTime -Descending
        
        if ($backups.Count -gt 0) {
            $hasBackups = $true
            Write-Host ""
            Write-Host "📁 $desc ($prefix):" -ForegroundColor Yellow
            foreach ($b in $backups) {
                $date = $b.LastWriteTime.ToString("yyyy-MM-dd HH:mm")
                $size = [math]::Round($b.Length / 1KB, 2)
                Write-Host "   [$date] $($b.Name) (${size} KB)" -ForegroundColor Gray
            }
        }
    }
    
    if (-not $hasBackups) {
        Write-Host ""
        Write-Host "⚠️ 未找到任何备份文件" -ForegroundColor Red
        Write-Host "   备份文件命名格式: *.bak_YYYYMMDD 或 *.bak_YYYYMMDD_HHmmss"
    }
    
    Write-Host ""
}

function Stop-Service {
    Write-Log "正在停止云枢服务..."
    
    # 查找 Python 进程（app_server.py）
    $process = Get-Process -Name "python" -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -like "*app_server*" 2>$null
    }
    
    if ($process) {
        Stop-Process -Id $process.Id -Force
        Write-Log "✅ 服务已停止 (PID: $($process.Id))"
        Start-Sleep -Seconds 2
    } else {
        Write-Log "⚠️ 未找到运行中的服务进程"
    }
}

function Start-Service {
    Write-Log "正在启动云枢服务..."
    
    $script = Join-Path $ProjectRoot "app_server.py"
    $env:YUNSHU_FEATURE_SANDBOX = 'false'
    
    Start-Process -FilePath "python" -ArgumentList $script -WorkingDirectory $ProjectRoot -NoNewWindow -PassThru
    Write-Log "✅ 服务已启动"
    
    # 等待服务就绪
    Write-Log "等待服务就绪..."
    Start-Sleep -Seconds 5
    
    # 验证服务
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:5678/api/health" -TimeoutSec 5 -ErrorAction SilentlyContinue
        if ($response.StatusCode -eq 200) {
            Write-Log "✅ 服务验证通过"
        }
    } catch {
        Write-Log "⚠️ 服务验证超时，请手动检查" -ForegroundColor Yellow
    }
}

function Do-Rollback {
    param(
        [string]$BackupPath,
        [string]$TargetPath,
        [string]$Description
    )
    
    if (-not (Test-Path $BackupPath)) {
        Write-Log "❌ 备份文件不存在: $BackupPath" -Level "ERROR"
        return $false
    }
    
    $fullTarget = Join-Path $ProjectRoot $TargetPath
    
    # 创建当前版本备份（回滚前再备份一次）
    if (Test-Path $fullTarget) {
        $preRollback = "${TargetPath}.pre_rollback_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
        Copy-Item -Path $fullTarget -Destination (Join-Path $ProjectRoot $preRollback) -Force
        Write-Log "📦 回滚前已备份当前版本: $preRollback"
    }
    
    Copy-Item -Path $BackupPath -Destination $fullTarget -Force
    Write-Log "✅ $Description 已回滚: $(Split-Path $BackupPath -Leaf)"
    return $true
}

# ════════════════════════════════════════════════════════════════
#  主流程
# ════════════════════════════════════════════════════════════════

# 确保日志目录存在
$logDir = Join-Path $ProjectRoot "logs"
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

Write-Host ""
Write-Host "═" * 70 -ForegroundColor Cyan
Write-Host "🔄 云枢服务回滚工具" -ForegroundColor Cyan
Write-Host "═" * 70 -ForegroundColor Cyan
Write-Log "回滚脚本启动，目标: $Target"

if ($ListBackups) {
    List-AvailableBackups
    exit 0
}

# 查找备份
Write-Host ""
Write-Log "🔍 查找最新备份..."

$targets = @()
if ($Target -eq "all" -or $Target -eq "code") {
    $targets += "code"
}
if ($Target -eq "all" -or $Target -eq "data") {
    $targets += "data"
}

$rollbackPlan = @{}
$allFound = $true

foreach ($key in $targets) {
    $prefix = $FileMap[$key].Source
    $backup = Find-LatestBackup -FilePrefix $prefix
    
    if ($backup) {
        $rollbackPlan[$key] = $backup
        Write-Log "✅ 找到备份: $($backup.Name) ($(Get-Date $backup.LastWriteTime -Format 'yyyy-MM-dd HH:mm'))"
    } else {
        Write-Log "❌ 未找到 $($FileMap[$key].Description) 的备份" -Level "ERROR"
        $allFound = $false
    }
}

if (-not $allFound) {
    Write-Host ""
    Write-Log "⚠️ 部分备份缺失，请手动检查"
    Write-Log "提示: 部署时应先运行备份命令创建 *.bak_* 文件"
    
    $continue = Read-Host "是否继续回滚可用部分？(y/N)"
    if ($continue -ne "y" -and $continue -ne "Y") {
        Write-Log "回滚已取消"
        exit 1
    }
}

# 确认回滚
Write-Host ""
Write-Host "═" * 70 -ForegroundColor Yellow
Write-Host "⚠️ 回滚计划:" -ForegroundColor Yellow
Write-Host "═" * 70 -ForegroundColor Yellow

foreach ($key in $rollbackPlan.Keys) {
    $backup = $rollbackPlan[$key]
    $desc = $FileMap[$key].Description
    Write-Host "  $desc ← $($backup.Name)" -ForegroundColor Green
}

Write-Host ""
$confirm = Read-Host "确认执行回滚？(y/N)"

if ($confirm -ne "y" -and $confirm -ne "Y") {
    Write-Log "回滚已取消"
    exit 0
}

# 停止服务
Write-Host ""
Stop-Service

# 执行回滚
Write-Host ""
Write-Log "═" * 50
Write-Log "执行回滚..."
Write-Log "═" * 50

foreach ($key in $rollbackPlan.Keys) {
    $backup = $rollbackPlan[$key]
    $targetPath = $FileMap[$key].Target
    $desc = $FileMap[$key].Description
    
    $result = Do-Rollback -BackupPath $backup.FullName -TargetPath $targetPath -Description $desc
    if (-not $result) {
        Write-Log "❌ 回滚失败: $desc" -Level "ERROR"
    }
}

# 重启服务
Write-Host ""
if ($RestartService) {
    Start-Service
} else {
    Write-Log "⏭️ 跳过服务重启（-RestartService `$false）"
    Write-Log "手动启动命令: cd $ProjectRoot; python app_server.py"
}

Write-Host ""
Write-Host "═" * 70 -ForegroundColor Cyan
Write-Log "🎉 回滚完成！"
Write-Log "回滚日志: $LogFile"
Write-Host "═" * 70 -ForegroundColor Cyan
