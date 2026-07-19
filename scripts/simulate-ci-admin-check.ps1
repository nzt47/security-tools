<#PSScriptInfo
.VERSION 1.0.0
.AUTHOR agent-team
.GUID 5d2e8f7a-3b6c-4d9e-a1f2-8c3b4d5e6f7a
.TAGS admin, security, ci, simulation
.LICENSEURI
.PROJECTURI
.ICONURI
.EXTERNALMODULEDEPENDENCIES
.REQUIREDSCRIPTS
.EXTERNALSCRIPTDEPENDENCIES
.RELEASENOTES
#>
<#
.SYNOPSIS
    本地模拟 CI 环境的非管理员兼容性检查 (复用 AdminDependencyChecker 模块)

.DESCRIPTION
    通过设置 GITHUB_ACTIONS=true 环境变量, 模拟 GitHub Actions CI 环境,
    调用 scripts/AdminDependencyChecker.psm1 模块, 验证非管理员兼容性检查能否:
      1. 正确识别合规脚本 (无违规 → exit 0)
      2. 正确识别违规代码 (有 admin 依赖 → exit 1 + ::error:: workflow command)

    用途:
      - PR 提交前本地预演 CI 非管理员检查 step
      - 调试 ::error:: / ::notice:: 输出格式
      - 验证 AdminDependencyChecker 模块功能

.PARAMETER TargetScripts
    可选: 指定要扫描的脚本路径数组. 默认扫描 CI 中检查的 3 个核心脚本:
      - scripts/rollback-protection.ps1
      - scripts/test-rollback-params.ps1
      - scripts/simulate-ci-rollback-test.ps1

.PARAMETER CI
    模拟 CI 环境 (设置 GITHUB_ACTIONS=true 触发 workflow command 输出). 默认开启.

.PARAMETER NoCI
    用本地颜色输出模式运行 (不设置 GITHUB_ACTIONS).

.PARAMETER SelfTest
    自测模式: 创建临时违规脚本验证检测能力, 验证后清理. 用于回归测试.

.PARAMETER AsTest
    CI 回归断言模式: 反转退出码语义, 适用于"模块自测"步骤.
    默认语义: 有违规 → exit 1 (阻断, 与 CI 非管理员检查一致)
    -AsTest 语义: 有违规 → exit 0 (模块正常, 检出能力 OK), 无违规 → exit 1 (退化, 漏检!)
    必须与 -SelfTest 配合使用 (注入已知违规做断言).

.EXAMPLE
    .\scripts\simulate-ci-admin-check.ps1
    # 默认: CI 模式 + 扫描 3 个核心脚本

.EXAMPLE
    .\scripts\simulate-ci-admin-check.ps1 -NoCI
    # 本地模式: 颜色 + emoji 输出

.EXAMPLE
    .\scripts\simulate-ci-admin-check.ps1 -TargetScripts .\scripts\rollback.ps1
    # 指定单个文件扫描

.EXAMPLE
    .\scripts\simulate-ci-admin-check.ps1 -SelfTest
    # 自测: 验证模块能否识别 #Requires -RunAsAdministrator

.EXAMPLE
    .\scripts\simulate-ci-admin-check.ps1 -SelfTest -AsTest -CI
    # CI 自测回归: 注入 4 种违规, 漏检则失败
#>
[CmdletBinding()]
param(
    [string[]]$TargetScripts = @(
        'scripts/rollback-protection.ps1',
        'scripts/test-rollback-params.ps1',
        'scripts/simulate-ci-rollback-test.ps1'
    ),

    [switch]$CI,

    [switch]$NoCI,

    [switch]$SelfTest,

    [switch]$AsTest
)

$ErrorActionPreference = 'Stop'
$script:modulePath = Join-Path $PSScriptRoot 'AdminDependencyChecker.psm1'
$script:tempFile = $null

# ─── 参数互斥校验 ─────────────────────────────────────────────
# -AsTest 必须与 -SelfTest 配合 (注入违规做断言才有意义)
if ($AsTest -and -not $SelfTest) {
    Write-Host "❌ -AsTest 必须与 -SelfTest 配合使用 (注入违规做断言)" -ForegroundColor Red
    Write-Host "   示例: .\scripts\simulate-ci-admin-check.ps1 -SelfTest -AsTest -CI" -ForegroundColor Yellow
    exit 2
}

# ─── 前置检查 ──────────────────────────────────────────────────
if (-not (Test-Path $script:modulePath)) {
    Write-Host "❌ 找不到模块: $($script:modulePath)" -ForegroundColor Red
    Write-Host "   请确认 AdminDependencyChecker.psm1 与本脚本位于同一目录"
    exit 2
}

# 互斥参数检查
if ($CI -and $NoCI) {
    Write-Host "❌ -CI 和 -NoCI 互斥, 请只选一个" -ForegroundColor Red
    exit 2
}

# 决定是否模拟 CI 环境 (默认 $CI 优先, 否则看 -NoCI)
$simulateCI = $true
if ($NoCI) { $simulateCI = $false }
if ($CI)   { $simulateCI = $true }

# ─── 自测模式: 创建临时违规脚本 ────────────────────────────────
if ($SelfTest) {
    $script:tempFile = Join-Path $PSScriptRoot "_temp_selftest_admin_$(Get-Random).ps1"
    # 故意注入 3 种 admin 依赖, 验证检测能力
    $malicious = @(
        '#requires -RunAsAdministrator'
        '$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)'
        'Start-Process powershell -Verb RunAs'
        'New-Service -Name "foo" -BinaryPathName "bar"'
    ) -join "`n"
    Set-Content -Path $script:tempFile -Value $malicious -Encoding UTF8
    $TargetScripts = @($script:tempFile)
    Write-Host "======================================================================" -ForegroundColor Cyan
    Write-Host "  自测模式: 注入 4 种 admin 依赖到临时文件" -ForegroundColor Cyan
    Write-Host "  临时文件: $($script:tempFile)" -ForegroundColor Cyan
    Write-Host "======================================================================" -ForegroundColor Cyan
    Write-Host ""
}

# ─── 设置 CI 环境变量 (finally 中清理) ──────────────────────────
$prevGithubActions = $env:GITHUB_ACTIONS
try {
    if ($simulateCI) {
        $env:GITHUB_ACTIONS = 'true'
    } else {
        # 本地模式: 显式置空, 避免继承父 shell 已设置的 GITHUB_ACTIONS
        Remove-Item Env:GITHUB_ACTIONS -ErrorAction SilentlyContinue
    }

    Write-Host "======================================================================" -ForegroundColor Cyan
    Write-Host "  非管理员兼容性检查 ($($simulateCI ? 'CI 模拟模式' : '本地模式'))" -ForegroundColor Cyan
    Write-Host "  模块: AdminDependencyChecker.psm1" -ForegroundColor Cyan
    Write-Host "======================================================================" -ForegroundColor Cyan
    Write-Host ""

    # ─── 加载模块并执行扫描 ─────────────────────────────────────
    Import-Module $script:modulePath -Force

    $result = Test-AdminDependency -Path $TargetScripts

    Write-Host ""
    Publish-AdminDependencyResult -Result $result

    # ─── 退出码语义 ─────────────────────────────────────────────
    # 默认: 有违规 → exit 1 (与 CI 非管理员检查一致)
    # -AsTest: 反转语义, 用于 CI 自测回归 (注入违规做断言)
    #          检出违规 → exit 0 (模块正常); 漏检 → exit 1 (退化)
    if ($AsTest) {
        # 仅在 -SelfTest 模式下生效, 此时 TargetScripts 已是注入的违规文件
        if ($result.Passed) {
            Write-Host ""
            Write-Host "::error::自测失败 - 模块未检测到注入的 $($result.ScannedFiles) 个文件中的 admin 依赖, 检测规则退化" -ForegroundColor Red
            Write-Host "❌ 自测失败: 模块检测能力退化, 注入的违规被漏检" -ForegroundColor Red
            $exitCode = 1
        } else {
            Write-Host ""
            Write-Host "::notice::自测通过 - 检测到 $($result.Violations.Count) 个注入违规 (期望 >= 4)" -ForegroundColor Green
            Write-Host "✅ 自测通过: 模块检测能力稳定" -ForegroundColor Green
            $exitCode = 0
        }
    } else {
        # 默认退出码语义 (与 CI 非管理员检查 step 一致)
        if ($result.Passed) {
            Write-Host ""
            Write-Host "✅ 非管理员兼容性 OK, CI 中将 PASS" -ForegroundColor Green
            $exitCode = 0
        } else {
            Write-Host ""
            Write-Host "❌ 发现 $($result.Violations.Count) 处 admin-only 依赖, CI 中将 FAIL 并阻断 PR 合并" -ForegroundColor Red
            $exitCode = 1
        }
    }
}
catch {
    Write-Host ""
    Write-Host "❌ 执行异常: $($_.Exception.Message)" -ForegroundColor Red
    $exitCode = 3
}
finally {
    # 恢复环境变量 (避免污染后续 shell)
    if ($null -ne $prevGithubActions) {
        $env:GITHUB_ACTIONS = $prevGithubActions
    } else {
        Remove-Item Env:GITHUB_ACTIONS -ErrorAction SilentlyContinue
    }

    # 清理自测临时文件
    if ($script:tempFile -and (Test-Path $script:tempFile)) {
        Remove-Item $script:tempFile -Force -ErrorAction SilentlyContinue
        Write-Host ""
        Write-Host "  [cleanup] 临时文件已清理: $($script:tempFile)" -ForegroundColor Gray
    }
}

Write-Host ""
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "  模拟完成, 退出码: $exitCode" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan

exit $exitCode
