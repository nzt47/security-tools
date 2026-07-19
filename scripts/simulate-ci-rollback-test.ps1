<#
.SYNOPSIS
    本地模拟 GitHub Actions 的 CI 环境, 运行 test-rollback-params.ps1 验证参数优先级

.DESCRIPTION
    通过设置 GITHUB_ACTIONS=true 环境变量, 模拟 CI 环境, 让 test-rollback-params.ps1
    输出 GitHub Actions 标准的 ::error:: / ::warning:: / ::notice:: workflow command 格式.

    用途:
      1. 本地预览 CI 环境下 test-rollback-params.ps1 的实际输出
      2. 验证 ::error:: / ::warning:: 格式是否正确
      3. 确认 PR 提交前参数优先级逻辑正确, 不会在 CI 中失败

.PARAMETER Normal
    不模拟 CI 环境, 用本地颜色输出模式运行 (默认是 CI 模式)

.EXAMPLE
    .\scripts\simulate-ci-rollback-test.ps1
    # 模拟 CI 环境 (输出 ::error:: 等格式)

.EXAMPLE
    .\scripts\simulate-ci-rollback-test.ps1 -Normal
    # 用本地模式运行 (输出颜色 + emoji)
#>
[CmdletBinding()]
param(
    [switch]$Normal
)

$ErrorActionPreference = 'Stop'
$script:testScript = Join-Path $PSScriptRoot 'test-rollback-params.ps1'

if (-not (Test-Path $script:testScript)) {
    Write-Host "❌ 找不到测试脚本: $script:testScript" -ForegroundColor Red
    exit 1
}

if (-not $Normal) {
    # 模拟 GitHub Actions 环境
    # GITHUB_ACTIONS=true 触发 test-rollback-params.ps1 的 CI 输出模式
    # GH_TOKEN 避免 Test-Prerequisites 中 gh auth status 在无 token 环境报错
    Write-Host "======================================================================" -ForegroundColor Cyan
    Write-Host "  本地 CI 环境模拟" -ForegroundColor Cyan
    Write-Host "  模拟设置: GITHUB_ACTIONS=true (触发 ::error:: 格式输出)" -ForegroundColor Cyan
    Write-Host "======================================================================" -ForegroundColor Cyan
    Write-Host ""

    $env:GITHUB_ACTIONS = 'true'
    try {
        & $script:testScript
        $exitCode = $LASTEXITCODE
    } finally {
        # 清理环境变量, 避免污染后续 PowerShell 会话
        Remove-Item Env:GITHUB_ACTIONS -ErrorAction SilentlyContinue
    }
} else {
    Write-Host "======================================================================" -ForegroundColor Cyan
    Write-Host "  本地模式运行 (无 CI 环境变量)" -ForegroundColor Cyan
    Write-Host "======================================================================" -ForegroundColor Cyan
    Write-Host ""

    & $script:testScript
    $exitCode = $LASTEXITCODE
}

Write-Host ""
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "  模拟完成, 退出码: $exitCode" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan

if ($exitCode -eq 0) {
    Write-Host "✅ 参数优先级逻辑正确, CI 中将 PASS" -ForegroundColor Green
} else {
    Write-Host "❌ 参数优先级逻辑有误, CI 中将 FAIL 并阻断 PR 合并" -ForegroundColor Red
}

exit $exitCode
