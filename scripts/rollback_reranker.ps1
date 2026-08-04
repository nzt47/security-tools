<#
.SYNOPSIS
    Reranker 热重载紧急回滚脚本（对应部署清单第 5 节回滚预案）
.DESCRIPTION
    提供三类回滚动作：
      status  - 查看当前 ONNX variant 配置、热重载日志回滚记录
      disable - 紧急禁用热重载（等效 SKILL_RERANKER_HOT_RELOAD_INTERVAL=999999，
                同时恢复 .env 中已知可用 variant 并重启服务）
      restore - 恢复到指定可用 variant（默认 model_quantized.onnx），重启服务
      revert  - 回滚服务代码到 git 稳定 commit（git revert HEAD），重启 Docker 容器

    机制说明（对齐 reranker.py 热重载实现）：
      - 热重载由 reranker 每次调用时惰性检查 .env 中的 SKILL_RERANKER_ONNX_VARIANT 触发
      - 设置 SKILL_RERANKER_HOT_RELOAD_INTERVAL=999999 等效禁用热重载（清单 5.3）
      - 修改 .env 后必须重启进程/容器才生效（进程启动时一次性读取）
.PARAMETER Action
    动作: status / disable / restore / revert（默认 status）
.PARAMETER Variant
    restore 动作使用的 ONNX variant（默认 model_quantized.onnx）
.PARAMETER RestartService
    disable/restore/revert 后是否自动重启服务（默认 true）
.PARAMETER Force
    跳过确认提示，直接执行
.EXAMPLE
    .\scripts\rollback_reranker.ps1
    .\scripts\rollback_reranker.ps1 -Action status
    .\scripts\rollback_reranker.ps1 -Action disable -Force
    .\scripts\rollback_reranker.ps1 -Action restore -Variant model_quantized.onnx
    .\scripts\rollback_reranker.ps1 -Action revert -Force
.NOTES
    前置条件:
      1. PowerShell 5.1+（推荐 7+）
      2. 需在项目根目录或其子目录执行
      3. revert 动作需要 git 已安装且当前分支有可回滚的 HEAD
    日志: logs/rollback_reranker_*.log
#>

param(
    [ValidateSet("status", "disable", "restore", "revert")]
    [string]$Action = "status",

    [string]$Variant = "model_quantized.onnx",

    [bool]$RestartService = $true,

    [switch]$Force
)

$ErrorActionPreference = "Stop"

# ════════════════════════════════════════════════════════════════
#  配置
# ════════════════════════════════════════════════════════════════
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $ProjectRoot ".env"
$LogDir = Join-Path $ProjectRoot "logs"
$LogFile = Join-Path $LogDir "rollback_reranker_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"
$AppLog = Join-Path $LogDir "app.log"
$ServerUrl = "http://127.0.0.1:5678"

# 已知可用 variant（验收通过的默认值）
$KNOWN_GOOD_VARIANT = "model_quantized.onnx"

# ════════════════════════════════════════════════════════════════
#  工具函数
# ════════════════════════════════════════════════════════════════
function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $entry = "[$timestamp] [$Level] $Message"
    Write-Host $entry
    Add-Content -Path $LogFile -Value $entry -Force
}

function Test-Preconditions {
    Write-Log "检查前置条件..."
    if (-not (Test-Path $EnvFile)) {
        Write-Log "❌ .env 文件不存在: $EnvFile" -Level "ERROR"
        exit 1
    }
    if (-not (Test-Path (Join-Path $ProjectRoot "docker-compose.yml")) -and
        -not (Test-Path (Join-Path $ProjectRoot "app_server.py"))) {
        Write-Log "❌ 项目结构异常（缺少 docker-compose.yml 或 app_server.py）" -Level "ERROR"
        exit 1
    }
    Write-Log "✅ 前置条件满足"
}

# 读取 .env 中某个键的当前值
function Get-EnvValue {
    param([string]$Key)
    $line = Select-String -Path $EnvFile -Pattern "^$Key=.*$" -ErrorAction SilentlyContinue |
        Select-Object -Last 1
    if ($line) {
        return ($line.Line -split "=", 2)[1].Trim()
    }
    return $null
}

# 设置 .env 中某个键的值（幂等：先删除旧行再追加）
function Set-EnvValue {
    param([string]$Key, [string]$Value, [string]$Reason)

    $content = Get-Content $EnvFile
    $pattern = "^$Key=.*$"
    $updated = @($content | Where-Object { $_ -notmatch $pattern })
    $updated += "# [rollback_reranker.ps1] $Reason"
    $updated += "$Key=$Value"
    Set-Content -Path $EnvFile -Value $updated -Encoding UTF8
    Write-Log "✅ 已设置 $Key=$Value（$Reason）"
}

function Restart-App {
    Write-Log "重启服务..."
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if ($docker -and (docker compose version 2>$null)) {
        try {
            docker compose down 2>&1 | Out-Host
            docker compose up -d 2>&1 | Out-Host
            Write-Log "✅ Docker 容器已重启"
        } catch {
            Write-Log "⚠️ Docker 重启失败，尝试直接运行 app_server.py" -Level "WARN"
            Start-Process -FilePath "python" -ArgumentList "app_server.py" `
                -WorkingDirectory $ProjectRoot -NoNewWindow
        }
    } else {
        Write-Log "未检测到 Docker，直接以 python 方式重启"
        $proc = Get-Process -Name "python" -ErrorAction SilentlyContinue | Where-Object {
            $_.CommandLine -like "*app_server*" 2>$null
        }
        if ($proc) {
            Stop-Process -Id $proc.Id -Force
            Write-Log "✅ 已停止旧进程 (PID: $($proc.Id))"
        }
        Start-Process -FilePath "python" -ArgumentList "app_server.py" `
            -WorkingDirectory $ProjectRoot -NoNewWindow
    }
    Start-Sleep -Seconds 5

    try {
        $resp = Invoke-WebRequest -Uri "$ServerUrl/api/health" -TimeoutSec 5 -ErrorAction Stop
        Write-Log "✅ 服务健康检查通过 (HTTP $($resp.StatusCode))"
    } catch {
        Write-Log "⚠️ 健康检查超时（可能服务端口不同），请手动确认" -Level "WARN"
    }
}

# ════════════════════════════════════════════════════════════════
#  动作: status —— 查看当前状态与回滚记录
# ════════════════════════════════════════════════════════════════
function Show-Status {
    Write-Host ""
    Write-Host ("═" * 60) -ForegroundColor Cyan
    Write-Host "📋 Reranker 热重载当前状态" -ForegroundColor Cyan
    Write-Host ("═" * 60) -ForegroundColor Cyan

    $enabled = Get-EnvValue "SKILL_RERANKER_ENABLED"
    $useOnnx = Get-EnvValue "SKILL_RERANKER_USE_ONNX"
    $variant = Get-EnvValue "SKILL_RERANKER_ONNX_VARIANT"
    $interval = Get-EnvValue "SKILL_RERANKER_HOT_RELOAD_INTERVAL"

    Write-Host ""
    Write-Host "  .env 当前配置:" -ForegroundColor Yellow
    Write-Host "    SKILL_RERANKER_ENABLED              = $enabled"
    Write-Host "    SKILL_RERANKER_USE_ONNX             = $useOnnx"
    Write-Host "    SKILL_RERANKER_ONNX_VARIANT         = $variant"
    Write-Host "    SKILL_RERANKER_HOT_RELOAD_INTERVAL  = $interval"

    $emergencyDisabled = $interval -eq "999999"
    $notEnabled = $enabled -ne "true"
    if ($emergencyDisabled) {
        $stateText = "🔴 禁用（紧急模式，检查间隔=999999）"
        $stateColor = "Red"
    } elseif ($notEnabled) {
        $stateText = "⚪ 禁用（SKILL_RERANKER_ENABLED=false）"
        $stateColor = "Yellow"
    } else {
        $stateText = "🟢 启用"
        $stateColor = "Green"
    }
    Write-Host ""
    Write-Host "  热重载状态: $stateText" -ForegroundColor $stateColor

    # 查看日志中的回滚记录
    Write-Host ""
    Write-Host "  日志回滚记录 (logs/app.log):" -ForegroundColor Yellow
    if (Test-Path $AppLog) {
        $rollbacks = Select-String -Path $AppLog -Pattern "hot_reload\.(failed_rollback|exception_rollback)" |
            Select-Object -Last 5
        if ($rollbacks) {
            foreach ($r in $rollbacks) {
                $line = $r.Line
                $action = if ($line -match 'hot_reload\.(failed_rollback|exception_rollback)') { $Matches[1] } else { "unknown" }
                $target = if ($line -match '"target_variant":\s*"([^"]+)"') { $Matches[1] } else { "?" }
                $kept = if ($line -match '"kept_variant":\s*"([^"]+)"') { $Matches[1] } else { "?" }
                Write-Host "    [$action] target=$target kept=$kept"
            }
        } else {
            Write-Host "    （无回滚记录）"
        }
    } else {
        Write-Host "    （app.log 不存在）"
    }

    # Docker 容器状态
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if ($docker) {
        Write-Host ""
        Write-Host "  Docker 容器:" -ForegroundColor Yellow
        docker ps --filter "name=digital-life" --format "table {{.Names}}\t{{.Status}}" 2>&1 | Out-Host
    }
    Write-Host ""
}

# ════════════════════════════════════════════════════════════════
#  动作: disable —— 紧急禁用热重载（清单 5.3）
# ════════════════════════════════════════════════════════════════
function Disable-HotReload {
    Write-Host ""
    Write-Host ("═" * 60) -ForegroundColor Red
    Write-Host "🔴 紧急禁用热重载" -ForegroundColor Red
    Write-Host ("═" * 60) -ForegroundColor Red

    $currentVariant = Get-EnvValue "SKILL_RERANKER_ONNX_VARIANT"
    Write-Log "当前 variant: $currentVariant"

    if (-not $Force) {
        $confirm = Read-Host "确认禁用热重载并恢复已知可用 variant ($KNOWN_GOOD_VARIANT)？(y/N)"
        if ($confirm -ne "y" -and $confirm -ne "Y") {
            Write-Log "已取消"
            exit 0
        }
    }

    # 备份当前 .env
    $backupEnv = "$EnvFile.rollback_backup_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
    Copy-Item -Path $EnvFile -Destination $backupEnv -Force
    Write-Log "📦 .env 已备份: $backupEnv"

    # 1. 恢复已知可用 variant
    if ($currentVariant -ne $KNOWN_GOOD_VARIANT) {
        Set-EnvValue -Key "SKILL_RERANKER_ONNX_VARIANT" -Value $KNOWN_GOOD_VARIANT `
            -Reason "回滚预案：从异常 variant ($currentVariant) 恢复"
    } else {
        Write-Log "⏭️ variant 已是已知可用值，跳过恢复"
    }

    # 2. 设置超大间隔等效禁用热重载
    Set-EnvValue -Key "SKILL_RERANKER_HOT_RELOAD_INTERVAL" -Value "999999" `
        -Reason "回滚预案：紧急禁用热重载"

    # 3. 重启使配置生效
    if ($RestartService) {
        Restart-App
    } else {
        Write-Log "⏭️ 跳过重启（-RestartService `$false），需手动重启使配置生效"
    }
    Write-Log "🎉 热重载已紧急禁用。恢复命令: .\scripts\rollback_reranker.ps1 -Action restore"
}

# ════════════════════════════════════════════════════════════════
#  动作: restore —— 恢复热重载到可用 variant
# ════════════════════════════════════════════════════════════════
function Restore-HotReload {
    Write-Host ""
    Write-Host ("═" * 60) -ForegroundColor Green
    Write-Host "🔄 恢复热重载配置" -ForegroundColor Green
    Write-Host ("═" * 60) -ForegroundColor Green

    if ($Variant -ne $KNOWN_GOOD_VARIANT) {
        Write-Log "⚠️ 指定 variant ($Variant) 非已知可用值，仍将设置；请确保文件存在" -Level "WARN"
    }

    if (-not $Force) {
        $confirm = Read-Host "确认恢复热重载到 variant=$Variant，检查间隔=30s？(y/N)"
        if ($confirm -ne "y" -and $confirm -ne "Y") {
            Write-Log "已取消"
            exit 0
        }
    }

    Set-EnvValue -Key "SKILL_RERANKER_ONNX_VARIANT" -Value $Variant `
        -Reason "回滚预案：恢复热重载"
    Set-EnvValue -Key "SKILL_RERANKER_HOT_RELOAD_INTERVAL" -Value "30" `
        -Reason "回滚预案：恢复默认检查间隔"

    if ($RestartService) {
        Restart-App
    } else {
        Write-Log "⏭️ 跳过重启（-RestartService `$false）"
    }
    Write-Log "🎉 热重载已恢复。验证: .\scripts\rollback_reranker.ps1 -Action status"
}

# ════════════════════════════════════════════════════════════════
#  动作: revert —— 回滚服务代码（清单 5.2）
# ════════════════════════════════════════════════════════════════
function Revert-Code {
    Write-Host ""
    Write-Host ("═" * 60) -ForegroundColor Yellow
    Write-Host "⏪ 回滚服务代码到 git 稳定 commit" -ForegroundColor Yellow
    Write-Host ("═" * 60) -ForegroundColor Yellow

    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Log "❌ 未安装 git" -Level "ERROR"
        exit 1
    }

    Write-Log "最近 5 个 commit:"
    git -C $ProjectRoot log --oneline -5 | ForEach-Object { Write-Host "    $_" }

    if (-not $Force) {
        $confirm = Read-Host "确认执行 git revert HEAD（保留提交历史）？(y/N)"
        if ($confirm -ne "y" -and $confirm -ne "Y") {
            Write-Log "已取消"
            exit 0
        }
    }

    git -C $ProjectRoot revert HEAD --no-edit 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-Log "❌ git revert 失败，请手动处理冲突" -Level "ERROR"
        exit 1
    }
    Write-Log "✅ git revert 完成"

    if ($RestartService) {
        Restart-App
    } else {
        Write-Log "⏭️ 跳过重启（-RestartService `$false）"
    }
    Write-Log "🎉 代码已回滚。建议: git log --oneline -3 确认结果"
}

# ════════════════════════════════════════════════════════════════
#  主入口
# ════════════════════════════════════════════════════════════════
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

Write-Host ""
Write-Host "╔══════════════════════════════════════════════════════════╗"
Write-Host "║  Reranker 热重载回滚工具（部署清单 §5 预案落地）         ║"
Write-Host "╚══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Log "动作: $Action | Variant: $Variant | RestartService: $RestartService"

Test-Preconditions

switch ($Action) {
    "status"  { Show-Status }
    "disable" { Disable-HotReload }
    "restore" { Restore-HotReload }
    "revert"  { Revert-Code }
}

Write-Log "执行完毕，日志: $LogFile"
Write-Host ""
