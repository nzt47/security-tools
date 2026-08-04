<#
.SYNOPSIS
    CI/CD 指标推送紧急回滚脚本

.DESCRIPTION
    [不易] 紧急情况下快速禁用 CI/CD 指标推送，不影响流水线正常交付。
    [变易] 支持两种回滚方案 + DryRun + Restore，可按需选择。
    [简易] 一键执行：.\scripts\rollback_cicd_metrics.ps1 -Mode A

.PARAMETER Mode
    A       - 注释 ci-cd.yml 中所有 cicd_metrics_push.py 调用（推荐：完全停止推送）
    B       - 将 PUSHGATEWAY_URL 改为无效地址（推送仍执行但必失败，不阻塞流水线）
    Restore - 从最新备份恢复 ci-cd.yml

.PARAMETER DryRun
    仅显示将要做的修改，不实际执行

.EXAMPLE
    .\scripts\rollback_cicd_metrics.ps1 -Mode A
    .\scripts\rollback_cicd_metrics.ps1 -Mode B -DryRun
    .\scripts\rollback_cicd_metrics.ps1 -Mode Restore
#>

param(
    [Parameter(Mandatory=$true)]
    [ValidateSet("A","B","Restore")]
    [string]$Mode,

    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

# [不易] 路径定义（不硬编码，从脚本位置推导）
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$CiCdYml = Join-Path $ProjectRoot ".github\workflows\ci-cd.yml"
$BackupDir = Join-Path $ProjectRoot ".github\workflows\backups"

# [不易] 操作日志
$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogFile = Join-Path $BackupDir "rollback_log_$Timestamp.txt"

function Write-Info  { param([string]$Msg) Write-Host "[INFO]  $Msg" -ForegroundColor Cyan }
function Write-Ok    { param([string]$Msg) Write-Host "[OK]    $Msg" -ForegroundColor Green }
function Write-Warn  { param([string]$Msg) Write-Host "[WARN]  $Msg" -ForegroundColor Yellow }
function Write-Err   { param([string]$Msg) Write-Host "[ERROR] $Msg" -ForegroundColor Red }
function Log-Write   { param([string]$Msg) Add-Content -Path $LogFile -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Msg" }

# [不易] 创建备份目录
if (-not (Test-Path $BackupDir)) {
    New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
}

# [不易] 备份 ci-cd.yml
function Backup-CiCdYml {
    if (-not (Test-Path $CiCdYml)) {
        Write-Err "ci-cd.yml 不存在: $CiCdYml"
        exit 1
    }
    $BackupFile = Join-Path $BackupDir "ci-cd.yml.backup_$Timestamp.yml"
    if (-not $DryRun) {
        Copy-Item $CiCdYml $BackupFile -Force
        Write-Ok "已备份 → $BackupFile"
        Log-Write "BACKUP: $CiCdYml → $BackupFile"
    } else {
        Write-Warn "DryRun: 未实际备份（将备份到 $BackupFile）"
    }
    return $BackupFile
}

# [不易] YAML 语法校验
function Validate-Yaml {
    Write-Info "校验 YAML 语法..."
    # [修复] 用 Here-String 避免 PowerShell 双引号中单引号冲突
    $pyScript = @"
import yaml
yaml.safe_load(open(r'$CiCdYml', encoding='utf-8'))
print('YAML OK')
"@
    $result = python -c $pyScript 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "YAML 语法正确"
        Log-Write "VALIDATE: YAML OK"
        return $true
    } else {
        Write-Err "YAML 语法错误: $result"
        Log-Write "VALIDATE: YAML FAILED - $result"
        return $false
    }
}

# [变易] 方案 A：注释所有 cicd_metrics_push.py 调用行
function Execute-ModeA {
    Write-Info "=== 方案 A：注释 cicd_metrics_push.py 调用 ==="
    Write-Info "效果：完全停止指标推送，埋点 step 仍存在但跳过推送命令"

    $content = Get-Content $CiCdYml -Raw -Encoding UTF8
    # [修复] 用双引号字符串 + 反引号转义 $，避免 PowerShell 单引号中 $' 解析问题
    $pattern = "(?m)^(\s+)(python scripts/cicd_metrics_push\.py)"
    $matches = [regex]::Matches($content, $pattern)

    if ($matches.Count -eq 0) {
        Write-Warn "未找到 cicd_metrics_push.py 调用（可能已被注释）"
        return
    }

    Write-Info "找到 $($matches.Count) 处 cicd_metrics_push.py 调用，将注释："
    foreach ($m in $matches) {
        $lineNum = ($content.Substring(0, $m.Index) -split "`n").Count
        Write-Host "  L${lineNum}: $($m.Value.Trim())"
    }

    if ($DryRun) {
        Write-Warn "DryRun: 仅显示，不修改文件"
        return
    }

    # 执行替换：在 python 前加 # （保持缩进）
    $newContent = [regex]::Replace($content, $pattern, '$1# [rollback] $2')
    Set-Content -Path $CiCdYml -Value $newContent -Encoding UTF8 -NoNewline

    Write-Ok "已注释 $($matches.Count) 处 cicd_metrics_push.py 调用"
    Log-Write "MODE_A: 注释 $($matches.Count) 处推送调用"

    # 验证
    $remaining = (Select-String -Path $CiCdYml -Pattern '^\s+python scripts/cicd_metrics_push' | Measure-Object).Count
    Write-Info "验证：剩余未注释的推送调用 = $remaining（预期 0）"
}

# [变易] 方案 B：将 PUSHGATEWAY_URL 改为无效地址
function Execute-ModeB {
    Write-Info "=== 方案 B：设置无效 PUSHGATEWAY_URL ==="
    Write-Info "效果：推送仍执行但因地址不可达必失败（不阻塞流水线），保留埋点逻辑"

    $content = Get-Content $CiCdYml -Raw -Encoding UTF8
    # [修复] 用双引号字符串 + 反引号转义行尾 $，避免 PowerShell 单引号解析问题
    $pattern = "(?m)^(  PUSHGATEWAY_URL:\s*)(http[s]?://[^\s]+)\s*`$"
    $matches = [regex]::Matches($content, $pattern)

    if ($matches.Count -eq 0) {
        Write-Warn "未找到全局 PUSHGATEWAY_URL 配置（可能已被修改）"
        return
    }

    $originalUrl = $matches[0].Groups[2].Value
    $disabledUrl = "http://127.0.0.1:1/disabled-by-rollback"
    Write-Info "原地址: $originalUrl"
    Write-Info "新地址: $disabledUrl"

    if ($DryRun) {
        Write-Warn "DryRun: 仅显示，不修改文件"
        return
    }

    # 执行替换
    $replacement = "`${1}$disabledUrl  # [rollback] 原地址 $originalUrl"
    $newContent = [regex]::Replace($content, $pattern, $replacement)
    Set-Content -Path $CiCdYml -Value $newContent -Encoding UTF8 -NoNewline

    Write-Ok "已将 PUSHGATEWAY_URL 改为无效地址"
    Log-Write "MODE_B: PUSHGATEWAY_URL $originalUrl → $disabledUrl"
}

# [变易] Restore：从最新备份恢复
function Execute-Restore {
    Write-Info "=== Restore：从备份恢复 ci-cd.yml ==="

    $backups = Get-ChildItem -Path $BackupDir -Filter "ci-cd.yml.backup_*.yml" | Sort-Object LastWriteTime -Descending
    if ($backups.Count -eq 0) {
        Write-Err "未找到备份文件（查找目录: $BackupDir）"
        exit 1
    }

    $latestBackup = $backups[0].FullName
    Write-Info "最新备份: $latestBackup ($($backups[0].LastWriteTime))"

    if ($DryRun) {
        Write-Warn "DryRun: 仅显示，不恢复"
        return
    }

    Copy-Item $latestBackup $CiCdYml -Force
    Write-Ok "已从备份恢复 ci-cd.yml"
    Log-Write "RESTORE: $latestBackup → $CiCdYml"
}

# ===== 主流程 =====

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  CI/CD 指标推送紧急回滚脚本" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  模式: $Mode"
Write-Host "  DryRun: $DryRun"
Write-Host "  目标: $CiCdYml"
Write-Host ""

Log-Write "==== 回滚操作开始 Mode=$Mode DryRun=$DryRun ===="

# 备份（Restore 模式不需要备份当前文件）
if ($Mode -ne "Restore") {
    Backup-CiCdYml | Out-Null
}

# 执行回滚
switch ($Mode) {
    "A"       { Execute-ModeA }
    "B"       { Execute-ModeB }
    "Restore" { Execute-Restore }
}

# YAML 语法校验（DryRun 模式跳过，因为文件未修改）
if (-not $DryRun -and $Mode -ne "Restore") {
    if (-not (Validate-Yaml)) {
        Write-Warn "YAML 语法校验失败，建议执行 -Mode Restore 恢复"
    }
}

# 操作摘要
Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  操作摘要" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  模式: $Mode"
Write-Host "  DryRun: $DryRun"
Write-Host "  日志: $LogFile"
Write-Host ""

if ($Mode -eq "A") {
    Write-Host "  效果: 所有 cicd_metrics_push.py 调用已被注释" -ForegroundColor Green
    Write-Host "  恢复: .\scripts\rollback_cicd_metrics.ps1 -Mode Restore" -ForegroundColor Yellow
} elseif ($Mode -eq "B") {
    Write-Host "  效果: PUSHGATEWAY_URL 已改为无效地址" -ForegroundColor Green
    Write-Host "  推送仍会执行但必失败，不阻塞流水线" -ForegroundColor Green
    Write-Host "  恢复: .\scripts\rollback_cicd_metrics.ps1 -Mode Restore" -ForegroundColor Yellow
} else {
    Write-Host "  效果: ci-cd.yml 已从备份恢复" -ForegroundColor Green
}

Write-Host ""
Log-Write "==== 回滚操作结束 ===="
