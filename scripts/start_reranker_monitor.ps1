<#
.SYNOPSIS
    Reranker 实时监控一键启动脚本

【不易】不修改 reranker.py / monitor_reranker_neg_sample.py / app_server.py 源码
       仅做进程编排 + 环境变量加载 + 日志重定向
【变易】支持多种运行模式：full / monitor-only / report-only
       .env 文件 mtime 变化时自动重载（与热重载机制配合）
【简易】单脚本自包含，三段式流程清晰可读

.DESCRIPTION
    一键启动 reranker 监控栈：
    1. 解析 .env 文件，将 SKILL_RERANKER_* 配置注入当前进程环境变量
    2. 后台启动 app_server.py，将 stderr 重定向到 reranker.log（observability JSON 日志）
    3. 前台启动 monitor_reranker_neg_sample.py，实时显示告警
    4. Ctrl+C 优雅退出，自动清理 app_server 子进程

.PARAMETER LogFile
    reranker 日志文件路径（app_server stderr 重定向目标）
    默认：.\reranker.log

.PARAMETER Mode
    运行模式：
    - full         启动 app_server + 实时监控（默认）
    - monitor     仅启动实时监控（app_server 已独立运行时使用）
    - report      一次性分析历史日志后退出

.PARAMETER Port
    app_server 监听端口（默认 5678）

.PARAMETER MonitorArgs
    传递给 monitor_reranker_neg_sample.py 的额外参数
    示例：-MonitorArgs @("--window", "200", "--warn", "0.3", "--critical", "0.5")

.EXAMPLE
    .\scripts\start_reranker_monitor.ps1
    # 默认全栈启动：加载 .env → app_server 后台 → 实时监控前台

.EXAMPLE
    .\scripts\start_reranker_monitor.ps1 -Mode report
    # 仅分析历史日志，不启动 app_server

.EXAMPLE
    .\scripts\start_reranker_monitor.ps1 -MonitorArgs @("--p99-slo", "300")
    # 自定义 P99 SLO 阈值为 300ms
#>
[CmdletBinding()]
param(
    [string]$LogFile = ".\reranker.log",
    [ValidateSet("full", "monitor", "report")]
    [string]$Mode = "full",
    [int]$Port = 5678,
    [string[]]$MonitorArgs = @()
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Resolve-Path "$PSScriptRoot\.."
Set-Location $ProjectRoot

# 子进程句柄（用于优雅退出清理）
$script:AppServerProcess = $null

# ──────────────────────────────────────────────
# 1. 辅助函数：彩色日志输出
# ──────────────────────────────────────────────

function Write-Step {
    param([string]$Message)
    Write-Host "[Step] $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "  ✅ $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "  ⚠️  $Message" -ForegroundColor Yellow
}

function Write-Err {
    param([string]$Message)
    Write-Host "  ❌ $Message" -ForegroundColor Red
}

# ──────────────────────────────────────────────
# 2. .env 加载：解析 KEY=VALUE 并注入环境变量
# ──────────────────────────────────────────────
# 【不易】不依赖 python-dotenv 等第三方库，PowerShell 原生解析
# 【变易】仅覆盖当前进程环境变量，不影响系统级配置
# 【简易】单行正则解析，跳过注释和空行

function Import-DotEnv {
    param([string]$EnvFile)

    if (-not (Test-Path $EnvFile)) {
        Write-Warn ".env 文件不存在：$EnvFile（将使用 reranker.py 内置默认值）"
        return
    }

    $loaded = 0
    Get-Content $EnvFile -Encoding UTF8 | ForEach-Object {
        $line = $_.Trim()
        # 跳过空行和注释
        if (-not $line -or $line.StartsWith("#")) { return }
        # 仅匹配 KEY=VALUE 格式（KEY 必须以字母或下划线开头）
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            $key = $matches[1]
            $value = $matches[2].Trim('"').Trim("'")
            # 仅设置未在当前进程显式定义的变量（避免覆盖 shell 显式传入的配置）
            # Why: shell 显式传入优先级最高，符合 12-factor app 配置层级
            if (-not [Environment]::GetEnvironmentVariable($key, "Process")) {
                [Environment]::SetEnvironmentVariable($key, $value, "Process")
                $loaded++
            }
        }
    }
    Write-Ok "已从 $EnvFile 加载 $loaded 个环境变量到当前进程"
}

# ──────────────────────────────────────────────
# 3. 配置摘要：打印当前生效的 SKILL_RERANKER_* 配置
# ──────────────────────────────────────────────

function Show-RerankerConfig {
    Write-Host ""
    Write-Host "┌─ 当前生效的 Reranker 配置 ─────────────────────────┐" -ForegroundColor Magenta
    $configKeys = @(
        "SKILL_RERANKER_ENABLED",
        "SKILL_RERANKER_MODEL",
        "SKILL_RERANKER_USE_ONNX",
        "SKILL_RERANKER_ONNX_VARIANT",
        "SKILL_RERANKER_RERANK_TIMEOUT",
        "SKILL_RERANKER_MIN_SCORE"
    )
    foreach ($key in $configKeys) {
        $value = [Environment]::GetEnvironmentVariable($key, "Process")
        if (-not $value) {
            $value = "<未设置，使用默认>"
        }
        Write-Host ("│ {0,-38} = {1}" -f $key, $value) -ForegroundColor Magenta
    }
    Write-Host "└────────────────────────────────────────────────────┘" -ForegroundColor Magenta
    Write-Host ""
}

# ──────────────────────────────────────────────
# 4. 后台启动 app_server：stderr 重定向到日志文件
# ──────────────────────────────────────────────
# 【不易】不修改 app_server.py，仅做 stderr 重定向
# 【变易】日志文件滚动覆盖（每次启动清空，避免历史数据干扰监控窗口）
# 【简易】Start-Process + RedirectStandardError，进程句柄保存供清理

function Start-AppServer {
    param([string]$LogPath, [int]$ListenPort)

    # 日志文件目录确保存在
    $logDir = Split-Path $LogPath -Parent
    if ($logDir -and -not (Test-Path $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }

    # 清空旧日志（避免 monitor 读到上次运行的过期数据污染窗口）
    if (Test-Path $LogPath) {
        Clear-Content $LogPath -Force
    }

    $pythonExe = (Get-Command python).Source
    $appServerPath = Join-Path $ProjectRoot "app_server.py"

    if (-not (Test-Path $appServerPath)) {
        Write-Err "app_server.py 不存在：$appServerPath"
        throw "app_server.py missing"
    }

    # 设置环境变量（app_server 通过 os.environ 读取）
    [Environment]::SetEnvironmentVariable("PORT", $ListenPort.ToString(), "Process")

    Write-Step "后台启动 app_server（stderr → $LogPath）"
    $script:AppServerProcess = Start-Process -FilePath $pythonExe `
        -ArgumentList $appServerPath `
        -RedirectStandardError $LogPath `
        -RedirectStandardOutput "app_server.stdout.log" `
        -WindowStyle Hidden `
        -PassThru

    # 等待进程就绪（最多 10s）
    $ready = $false
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 500
        if ($script:AppServerProcess.HasExited) {
            Write-Err "app_server 启动失败，退出码：$($script:AppServerProcess.ExitCode)"
            Write-Warn "查看 app_server.stdout.log 排查错误"
            throw "app_server failed to start"
        }
        # 检查端口是否监听
        $conn = Get-NetTCPConnection -LocalPort $ListenPort -State Listen -ErrorAction SilentlyContinue
        if ($conn) {
            $ready = $true
            break
        }
    }

    if ($ready) {
        Write-Ok "app_server 已启动（PID: $($script:AppServerProcess.Id), 端口: $ListenPort）"
        Write-Host "       访问 http://127.0.0.1:$ListenPort 查看 Web 界面" -ForegroundColor DarkGray
        Write-Host "       访问 http://127.0.0.1:$ListenPort/metrics 获取 Prometheus 指标" -ForegroundColor DarkGray
    } else {
        Write-Warn "app_server 启动超过 10s 仍未监听端口 $ListenPort，监控将开始但可能无数据"
    }
}

# ──────────────────────────────────────────────
# 5. 前台运行 monitor：实时显示告警
# ──────────────────────────────────────────────

function Start-Monitor {
    param(
        [string]$LogPath,
        [bool]$ReportMode
    )

    $monitorScript = Join-Path $ProjectRoot "scripts\monitor_reranker_neg_sample.py"
    if (-not (Test-Path $monitorScript)) {
        Write-Err "monitor 脚本不存在：$monitorScript"
        throw "monitor script missing"
    }

    $pythonExe = (Get-Command python).Source
    $args = @($monitorScript, "--log", $LogPath) + $MonitorArgs
    if ($ReportMode) {
        $args += "--report"
    }

    Write-Step "启动 monitor（模式: $(if ($ReportMode) { 'report' } else { 'realtime' })）"
    Write-Host "  日志文件: $LogPath" -ForegroundColor DarkGray
    Write-Host "  按 Ctrl+C 退出（将自动清理 app_server 子进程）" -ForegroundColor DarkGray
    Write-Host ""

    # 前台运行 monitor，让用户直接看到实时告警
    & $pythonExe $args
    return $LASTEXITCODE
}

# ──────────────────────────────────────────────
# 6. 优雅退出：清理 app_server 子进程
# ──────────────────────────────────────────────

function Stop-AppServer {
    if ($script:AppServerProcess -and -not $script:AppServerProcess.HasExited) {
        Write-Host ""
        Write-Step "正在停止 app_server（PID: $($script:AppServerProcess.Id)）..."
        try {
            # 优雅终止（SIGTERM 等价）
            $script:AppServerProcess.CloseMainWindow() | Out-Null
            if (-not $script:AppServerProcess.WaitForExit(3000)) {
                # 3s 内未退出则强制终止
                Stop-Process -Id $script:AppServerProcess.Id -Force -ErrorAction SilentlyContinue
            }
            Write-Ok "app_server 已停止"
        } catch {
            Write-Warn "停止 app_server 时异常：$($_.Exception.Message)"
        }
    }
}

# ──────────────────────────────────────────────
# 7. 主流程
# ──────────────────────────────────────────────

# 注册 Ctrl+C 处理器（确保子进程被清理）
$null = Register-EngineEvent -SourceIdentifier PowerShell.Exiting -Action {
    Stop-AppServer
}

try {
    Write-Host ""
    Write-Host "╔══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
    Write-Host "║   🔍  Reranker 实时监控一键启动                          ║" -ForegroundColor Cyan
    Write-Host "╚══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
    Write-Host ""

    # Step 1: 加载 .env
    Write-Step "[1/3] 加载 .env 环境变量"
    $envFile = Join-Path $ProjectRoot ".env"
    Import-DotEnv -EnvFile $envFile

    # Step 2: 显示当前配置
    Write-Step "[2/3] 当前 Reranker 配置摘要"
    Show-RerankerConfig

    # Step 3: 按模式启动
    Write-Step "[3/3] 启动监控栈（模式: $Mode）"

    $logPath = if ([System.IO.Path]::IsPathRooted($LogFile)) {
        $LogFile
    } else {
        Join-Path $ProjectRoot $LogFile
    }

    if ($Mode -eq "full") {
        Start-AppServer -LogPath $logPath -ListenPort $Port
        $exitCode = Start-Monitor -LogPath $logPath -ReportMode $false
    } elseif ($Mode -eq "monitor") {
        Write-Warn "monitor-only 模式：假设 app_server 已独立运行"
        if (-not (Test-Path $logPath)) {
            Write-Err "日志文件不存在：$logPath"
            Write-Host "   请先用 full 模式启动，或确认 app_server 已重定向 stderr 到此文件" -ForegroundColor Yellow
            throw "log file missing"
        }
        $exitCode = Start-Monitor -LogPath $logPath -ReportMode $false
    } else {
        # report 模式：一次性分析
        $exitCode = Start-Monitor -LogPath $logPath -ReportMode $true
    }

    if ($exitCode -ne 0) {
        Write-Warn "monitor 退出码：$exitCode"
    }
}
finally {
    # 无论正常退出还是异常，都清理子进程
    Stop-AppServer
    Write-Host ""
    Write-Host "👋 监控栈已退出" -ForegroundColor Cyan
}
