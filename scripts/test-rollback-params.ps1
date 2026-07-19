<#
.SYNOPSIS
    验证 rollback-protection.ps1 的 -Confirm 和 -WhatIf 参数组合行为

.DESCRIPTION
    测试矩阵覆盖文档中定义的优先级规则:
      -WhatIf > -Confirm > -Force

    所有测试用例都用 -WhatIf 或只读 status, 不修改真实 Branch Protection 配置.

.EXAMPLE
    .\scripts\test-rollback-params.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$script:scriptPath = Join-Path $PSScriptRoot 'rollback-protection.ps1'

# CI 环境检测: GitHub Actions 会设置 GITHUB_ACTIONS=true
$script:isCI = $env:GITHUB_ACTIONS -eq 'true'

# ─── 辅助: GitHub Actions workflow command 输出 ───────────────
# CI 环境用 ::error::/::warning:: 格式, GitHub 会渲染为 annotation
# 本地环境用 Write-Host + 颜色
function Write-CiError {
    param([string]$Message, [string]$File = '', [string]$Line = '')
    if ($script:isCI) {
        $loc = ''
        if ($File) { $loc += "file=$File"
            if ($Line) { $loc += ",line=$Line" }
            $loc += '::'
        }
        Write-Host "::error ${loc}$Message"
    } else {
        Write-Host "  ❌ $Message" -ForegroundColor Red
    }
}

function Write-CiWarning {
    param([string]$Message)
    if ($script:isCI) {
        Write-Host "::warning::$Message"
    } else {
        Write-Host "  ⚠️  $Message" -ForegroundColor Yellow
    }
}

function Write-CiNotice {
    param([string]$Message)
    if ($script:isCI) {
        Write-Host "::notice::$Message"
    } else {
        Write-Host "  ℹ️  $Message" -ForegroundColor Cyan
    }
}

# ─── 辅助: GitHub Actions group (折叠输出) ────────────────────
function Enter-CiGroup {
    param([string]$Name)
    if ($script:isCI) {
        Write-Host "::group::$Name"
    } else {
        Write-Host $Name -ForegroundColor White
    }
}

function Exit-CiGroup {
    if ($script:isCI) {
        Write-Host "::endgroup::"
    }
}

if (-not (Test-Path $script:scriptPath)) {
    Write-CiError "找不到被测脚本: $script:scriptPath"
    exit 1
}

# ─── 测试用例定义 ──────────────────────────────────────────────
# ShouldContain: 输出中必须包含的关键字
# ShouldNotContain: 输出中不应包含的关键字
$testCases = @(
    # === status: 只读, 不触发 ShouldProcess ===
    @{
        Name = 'status 无参数 → 只读执行, 无 What if / 已取消'
        Params = @{ Action = 'status'; Branch = 'master' }
        ShouldContain = @('=== Branch Protection 状态 ===')
        ShouldNotContain = @('What if:', '已取消')
    }
    @{
        Name = 'status -WhatIf → 只读, WhatIf 不影响 status'
        Params = @{ Action = 'status'; Branch = 'master'; WhatIf = $true }
        ShouldContain = @('=== Branch Protection 状态 ===')
        ShouldNotContain = @('What if:', '已取消')
    }
    @{
        Name = 'status -Verbose → 只读 + 调试日志'
        Params = @{ Action = 'status'; Branch = 'master'; Verbose = $true }
        ShouldContain = @('=== Branch Protection 状态 ===', '[verbose]')
        ShouldNotContain = @('What if:', '已取消')
    }

    # === disable -WhatIf: ShouldProcess 返回 $false ===
    @{
        Name = 'disable -WhatIf → What if 模拟, 不执行'
        Params = @{ Action = 'disable'; Branch = 'master'; WhatIf = $true }
        ShouldContain = @('What if:', 'Disable (清空 contexts)', '已取消')
        ShouldNotContain = @('required_status_checks 已临时关闭', '已备份 contexts')
    }
    @{
        Name = 'disable -Confirm:$false -WhatIf → WhatIf 优先 (覆盖 -Confirm:$false)'
        Params = @{ Action = 'disable'; Branch = 'master'; Confirm = $false; WhatIf = $true }
        ShouldContain = @('What if:', '已取消')
        ShouldNotContain = @('required_status_checks 已临时关闭')
    }
    @{
        Name = 'disable -Force -WhatIf → WhatIf 优先 (覆盖 -Force)'
        Params = @{ Action = 'disable'; Branch = 'master'; Force = $true; WhatIf = $true }
        ShouldContain = @('What if:', '已取消')
        ShouldNotContain = @('required_status_checks 已临时关闭', '[Force] 跳过确认')
    }

    # === enable -WhatIf: ShouldProcess 返回 $false ===
    @{
        Name = 'enable -WhatIf → What if 模拟, 不执行'
        Params = @{ Action = 'enable'; Branch = 'master'; WhatIf = $true }
        ShouldContain = @('What if:', 'Enable (恢复 contexts)', '已取消')
        ShouldNotContain = @('required_status_checks 已恢复')
    }
    @{
        Name = 'enable -Confirm:$false -WhatIf → WhatIf 优先 (覆盖 -Confirm:$false)'
        Params = @{ Action = 'enable'; Branch = 'master'; Confirm = $false; WhatIf = $true }
        ShouldContain = @('What if:', '已取消')
        ShouldNotContain = @('required_status_checks 已恢复')
    }
    @{
        Name = 'enable -Force -WhatIf → WhatIf 优先 (覆盖 -Force)'
        Params = @{ Action = 'enable'; Branch = 'master'; Force = $true; WhatIf = $true }
        ShouldContain = @('What if:', '已取消')
        ShouldNotContain = @('required_status_checks 已恢复', '[Force] 跳过确认')
    }
)

# ─── 执行测试 ──────────────────────────────────────────────────
Write-Host ""
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "  rollback-protection.ps1 参数组合行为测试" -ForegroundColor Cyan
Write-Host "  优先级规则: -WhatIf > -Confirm > -Force" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host ""

$passCount = 0
$failCount = 0
$results = @()

for ($i = 0; $i -lt $testCases.Count; $i++) {
    $tc = $testCases[$i]
    $testNum = $i + 1
    $testName = "[$testNum/$($testCases.Count)] $($tc.Name)"
    Enter-CiGroup $testName

    # 构建参数字符串 (用于显示)
    $paramStr = ($tc.Params.GetEnumerator() | ForEach-Object {
        $v = if ($_.Value -is [bool]) { '$' + $_.Value } else { $_.Value }
        "-$($_.Key) $v"
    }) -join ' '
    Write-Host "  命令: rollback-protection.ps1 $paramStr" -ForegroundColor Gray

    # 执行被测脚本, 用 Start-Transcript 捕获所有 host 输出 (Write-Host 绕过文件重定向)
    $tempFile = [System.IO.Path]::GetTempFileName() + '.txt'
    try {
        $splat = $tc.Params
        Start-Transcript -Path $tempFile -Force -ErrorAction SilentlyContinue | Out-Null
        & $script:scriptPath @splat *> $null 2>&1
        $exitCode = $LASTEXITCODE
        Stop-Transcript -ErrorAction SilentlyContinue | Out-Null
        $output = Get-Content $tempFile -Raw -ErrorAction SilentlyContinue
        if (-not $output) { $output = '(无输出)' }
    } catch {
        $output = "异常: $($_.Exception.Message)"
        $exitCode = -1
        Stop-Transcript -ErrorAction SilentlyContinue | Out-Null
    } finally {
        Remove-Item $tempFile -Force -ErrorAction SilentlyContinue
    }

    # 检查 ShouldContain
    $missingContain = @()
    foreach ($expected in $tc.ShouldContain) {
        if ($output -notlike "*$expected*") {
            $missingContain += $expected
        }
    }

    # 检查 ShouldNotContain
    $unexpectedContain = @()
    foreach ($unexpected in $tc.ShouldNotContain) {
        if ($output -like "*$unexpected*") {
            $unexpectedContain += $unexpected
        }
    }

    # 判定
    $passed = ($missingContain.Count -eq 0) -and ($unexpectedContain.Count -eq 0)

    if ($passed) {
        Write-Host "  结果: ✅ PASS" -ForegroundColor Green
        $passCount++
    } else {
        Write-Host "  结果: ❌ FAIL" -ForegroundColor Red
        $failCount++
        # GitHub Actions error: 用 workflow command, CI 自动渲染为 annotation
        $errMsg = "测试 $testNum 失败: $($tc.Name)"
        Write-CiError -Message $errMsg -File 'scripts/rollback-protection.ps1'

        if ($missingContain.Count -gt 0) {
            $detail = "缺失关键字: $($missingContain -join ' | ')"
            Write-CiWarning -Message "$testName - $detail"
            Write-Host "    $detail" -ForegroundColor Red
        }
        if ($unexpectedContain.Count -gt 0) {
            $detail = "不应出现的关键字: $($unexpectedContain -join ' | ')"
            Write-CiWarning -Message "$testName - $detail"
            Write-Host "    $detail" -ForegroundColor Red
        }
        # 输出实际内容便于调试 (前 300 字符)
        $preview = $output.Substring(0, [Math]::Min(300, $output.Length)).Trim()
        Write-Host "    实际输出预览:" -ForegroundColor Gray
        foreach ($line in $preview -split "`n") {
            Write-Host "      $line" -ForegroundColor Gray
        }
    }

    $results += [PSCustomObject]@{
        Test = $tc.Name
        Passed = $passed
        ExitCode = $exitCode
    }
    Exit-CiGroup
    Write-Host ""
}

# ─── 汇总 ──────────────────────────────────────────────────────
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "  测试汇总: $($testCases.Count) 总计 / $passCount 通过 / $failCount 失败" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan

if ($failCount -gt 0) {
    Write-Host ""
    Write-Host "失败用例详情:" -ForegroundColor Red
    $results | Where-Object { -not $_.Passed } | ForEach-Object {
        Write-Host "  - $($_.Test)" -ForegroundColor Red
    }
    # GitHub Actions error: 总结性错误, 便于 PR Checks 页面定位
    $summaryErr = "$failCount 个测试失败, 参数优先级规则 -WhatIf > -Confirm > -Force 被破坏"
    Write-CiError -Message $summaryErr -File 'scripts/rollback-protection.ps1'
    Write-Host ""
    Write-Host "❌ $failCount 个测试失败, 参数组合行为不符合预期" -ForegroundColor Red
    exit 1
} else {
    # GitHub Actions notice: 成功提示 (绿色 annotation)
    Write-CiNotice -Message "所有 $($testCases.Count) 个测试通过, 参数优先级规则 -WhatIf > -Confirm > -Force 正确"
    Write-Host ""
    Write-Host "✅ 所有测试通过, -Confirm / -WhatIf / -Force 组合行为符合优先级规则" -ForegroundColor Green
    exit 0
}

