<#
.SYNOPSIS
    重试逻辑测试运行包装脚本

.DESCRIPTION
    本地运行包装脚本，简化 test_retry_logic.ps1 的执行与日志收集：
    - 直接执行 test_retry_logic.ps1
    - 实时输出到控制台（保留原色）
    - 同时写入 logs/test_retry_<timestamp>.log
    - 透传退出码（0=全部通过 / 1=有失败用例）

.PARAMETER Verbose
    透传给 test_retry_logic.ps1，显示失败用例的详细错误信息

.EXAMPLE
    # 默认运行
    .\run_retry_tests.ps1

.EXAMPLE
    # 显示失败用例详情
    .\run_retry_tests.ps1 -Verbose

.NOTES
    退出码: 0=全部通过 / 1=有失败用例 / 2=测试脚本不存在
#>

param(
    [switch]$Verbose
)

# 控制台 UTF-8（避免中文乱码，PS 5.x 兼容）
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$scriptDir = $PSScriptRoot
if (-not $scriptDir) { $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
$testScript = Join-Path $scriptDir "test_retry_logic.ps1"

if (-not (Test-Path $testScript)) {
    Write-Host "[ERROR] 测试脚本不存在: $testScript" -ForegroundColor Red
    exit 2
}

# 创建 logs 目录（首次运行时）
$logsDir = Join-Path $scriptDir "logs"
if (-not (Test-Path $logsDir)) {
    New-Item -ItemType Directory -Path $logsDir -Force | Out-Null
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logFile = Join-Path $logsDir "test_retry_${timestamp}.log"

Write-Host ("=" * 60) -ForegroundColor DarkGray
Write-Host "  开始执行: test_retry_logic.ps1" -ForegroundColor White
Write-Host "  日志文件: $logFile" -ForegroundColor DarkGray
Write-Host ("=" * 60) -ForegroundColor DarkGray

$startTime = Get-Date

# 执行测试脚本，输出同时写入控制台和日志文件
# Tee-Object 在 PS 4.0+ 可用，保留原色控制台输出
& $testScript -Verbose:$Verbose 2>&1 | Tee-Object -FilePath $logFile
$exitCode = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 }

$duration = [Math]::Round(((Get-Date) - $startTime).TotalSeconds, 2)

Write-Host ""
Write-Host ("=" * 60) -ForegroundColor DarkGray
Write-Host "  执行完成" -ForegroundColor White
Write-Host ("  耗时: ${duration}s") -ForegroundColor DarkGray
Write-Host "  退出码: $exitCode" -ForegroundColor $(if ($exitCode -eq 0) { "Green" } else { "Red" })
Write-Host "  日志: $logFile" -ForegroundColor DarkGray
Write-Host ("=" * 60) -ForegroundColor DarkGray

exit $exitCode