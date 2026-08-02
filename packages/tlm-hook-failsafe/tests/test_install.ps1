<#
.SYNOPSIS
    tlm-hook-failsafe 安装后冒烟测试

.DESCRIPTION
    验证：
    1. 模块可被 Import-Module 加载
    2. 导出 12 个函数
    3. 关键函数 Get-HookContent 可调用且输出含 marker
    4. 关键函数 Invoke-SafeHookWrite 可调用（dry-run）

.EXAMPLE
    .\tests\test_install.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Continue"
$packageDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$psd1Path = Join-Path $packageDir "tlm-hook-failsafe.psd1"

Write-Host "=== tlm-hook-failsafe 冒烟测试 ===" -ForegroundColor Cyan

$results = @()

# ── Test 1: Import-Module 加载 ──
Write-Host "`n[1/4] Import-Module 加载..." -ForegroundColor Yellow
try {
    Import-Module $psd1Path -Force -ErrorAction Stop
    $results += @{ Name = "Import-Module 加载"; Pass = $true }
    Write-Host "  [PASS]" -ForegroundColor Green
} catch {
    $results += @{ Name = "Import-Module 加载"; Pass = $false; Error = $_.Exception.Message }
    Write-Host "  [FAIL] $($_.Exception.Message)" -ForegroundColor Red
    Show-Summary $results
    exit 1
}

# ── Test 2: 导出 12 个函数 ──
Write-Host "`n[2/4] 导出函数数量..." -ForegroundColor Yellow
$exported = @(Get-Command -Module tlm-hook-failsafe -ErrorAction SilentlyContinue)
$pass2 = ($exported.Count -eq 12)
$results += @{ Name = "导出 12 函数 (实际 $($exported.Count))"; Pass = $pass2 }
if ($pass2) {
    Write-Host "  [PASS] $($exported.Count) 函数" -ForegroundColor Green
} else {
    Write-Host "  [FAIL] 实际 $($exported.Count), 预期 12" -ForegroundColor Red
    $exported | ForEach-Object { Write-Host "    - $($_.Name)" -ForegroundColor Gray }
}

# ── Test 3: Get-HookContent 可调用且输出含 marker ──
Write-Host "`n[3/4] Get-HookContent 调用..." -ForegroundColor Yellow
try {
    $content = Get-HookContent -SourceRepo "C:\test-repo"
    $hasMarker = $content -match 'TLM-HOOK v1 source_repo=C:\\test-repo'
    $pass3 = $hasMarker
    $results += @{ Name = "Get-HookContent 输出 marker"; Pass = $pass3 }
    if ($pass3) {
        Write-Host "  [PASS] marker 行存在" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] marker 行缺失" -ForegroundColor Red
        Write-Host "    内容: $content" -ForegroundColor Gray
    }
} catch {
    $results += @{ Name = "Get-HookContent 调用"; Pass = $false; Error = $_.Exception.Message }
    Write-Host "  [FAIL] $($_.Exception.Message)" -ForegroundColor Red
}

# ── Test 4: Invoke-SafeHookWrite dry-run（临时目录） ──
Write-Host "`n[4/4] Invoke-SafeHookWrite 写入..." -ForegroundColor Yellow
$testDir = Join-Path $env:TEMP "tlm-hfs-smoke-$(Get-Random)"
$hookPath = Join-Path $testDir "pre-commit"
New-Item -ItemType Directory -Path $testDir -Force | Out-Null
try {
    $content = Get-HookContent -SourceRepo "C:\smoke-test"
    $writeResult = Invoke-SafeHookWrite -HookPath $hookPath -Content $content
    $pass4 = $writeResult.Written -and $writeResult.PermissionOk
    $results += @{ Name = "Invoke-SafeHookWrite 写入"; Pass = $pass4 }
    if ($pass4) {
        Write-Host "  [PASS] Written=$($writeResult.Written) PermissionOk=$($writeResult.PermissionOk)" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] Written=$($writeResult.Written) PermissionOk=$($writeResult.PermissionOk) Error=$($writeResult.Error)" -ForegroundColor Red
    }
} catch {
    $results += @{ Name = "Invoke-SafeHookWrite 写入"; Pass = $false; Error = $_.Exception.Message }
    Write-Host "  [FAIL] $($_.Exception.Message)" -ForegroundColor Red
} finally {
    Remove-Item -Recurse -Force $testDir -ErrorAction SilentlyContinue
}

# ── 汇总 ──
function Show-Summary($r) {
    Write-Host "`n=== Summary ===" -ForegroundColor Cyan
    $pass = ($r | Where-Object { $_.Pass }).Count
    $fail = ($r | Where-Object { -not $_.Pass }).Count
    foreach ($item in $r) {
        $tag = if ($item.Pass) { 'PASS' } else { 'FAIL' }
        $color = if ($item.Pass) { 'Green' } else { 'Red' }
        Write-Host "  [$tag] $($item.Name)" -ForegroundColor $color
    }
    Write-Host ""
    Write-Host "  Total: $($r.Count) | PASS: $pass | FAIL: $fail" -ForegroundColor $(if ($fail -eq 0) { 'Green' } else { 'Red' })
    if ($fail -gt 0) { exit 1 }
}

Show-Summary $results
exit 0
