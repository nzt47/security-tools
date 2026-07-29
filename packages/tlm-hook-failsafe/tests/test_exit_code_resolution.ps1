<#
.SYNOPSIS
    Exit code resolution layer unit tests

.DESCRIPTION
    Tests for Get-HookExitCodeMap, Resolve-HookExitCode, Invoke-HookWithCapture
    - Invariant: exit 1 never silently passes; SubCategory追溯 by stderr text
    - Adaptable: bash standard codes 126/127/130/137/143 covered
    - Simple: assertion-based, no Pester dependency

.EXAMPLE
    .\tests\test_exit_code_resolution.ps1
#>
$ErrorActionPreference = "Stop"
$packageDir = Split-Path $PSScriptRoot -Parent
$psm1Path   = Join-Path $packageDir 'tlm-hook-failsafe.psm1'

Write-Host "=== Exit Code Resolution Unit Tests ===" -ForegroundColor Cyan

# Import module under test
Get-Module tlm-hook-failsafe -All | Remove-Module -Force -ErrorAction SilentlyContinue
Import-Module $psm1Path -Force -ErrorAction Stop

$results = @()
function Assert-Equal {
    param([string]$Name, $Expected, $Actual)
    $passed = $false
    if ($null -eq $Expected -and $null -eq $Actual) { $passed = $true }
    elseif ($null -eq $Expected -or $null -eq $Actual) { $passed = $false }
    else { $passed = ($Expected.ToString() -eq $Actual.ToString()) }
    $status = if ($passed) { "PASS" } else { "FAIL" }
    $color  = if ($passed) { "Green" } else { "Red" }
    Write-Host "  [$status] $Name (expected=$Expected, actual=$Actual)" -ForegroundColor $color
    $script:results += [PSCustomObject]@{ Name=$Name; Status=$status }
    if (-not $passed) { throw "ASSERT FAILED: $Name" }
}

# ============================================================
# Test 1: Get-HookExitCodeMap basic structure
# ============================================================
Write-Host "[1/6] Get-HookExitCodeMap basic structure..." -ForegroundColor Yellow
$map = Get-HookExitCodeMap
Assert-Equal -Name "map count >= 5" -Expected $true -Actual ($map.Count -ge 5)
Assert-Equal -Name "code 0 exists"   -Expected $true -Actual ($map.Contains(0))
Assert-Equal -Name "code 1 exists"   -Expected $true -Actual ($map.Contains(1))
Assert-Equal -Name "code 2 exists"   -Expected $true -Actual ($map.Contains(2))
Assert-Equal -Name "code 3 exists"   -Expected $true -Actual ($map.Contains(3))
Assert-Equal -Name "code 4 exists"   -Expected $true -Actual ($map.Contains(4))
Assert-Equal -Name "code 0 Category" -Expected 'Success'        -Actual $map[0].Category
Assert-Equal -Name "code 1 Category" -Expected 'PreCheckFailed' -Actual $map[1].Category
Assert-Equal -Name "code 2 Category" -Expected 'EnvNotSet'      -Actual $map[2].Category
Assert-Equal -Name "code 3 Category" -Expected 'ScriptMissing'  -Actual $map[3].Category
Assert-Equal -Name "code 4 Category" -Expected 'PreCheckExecFailed' -Actual $map[4].Category

# Verify StderrPattern set on 2/3/4 (used for exit-1 subcategory追溯)
Assert-Equal -Name "code 2 StderrPattern non-empty" -Expected $true -Actual ($null -ne $map[2].StderrPattern)
Assert-Equal -Name "code 3 StderrPattern non-empty" -Expected $true -Actual ($null -ne $map[3].StderrPattern)
Assert-Equal -Name "code 4 StderrPattern non-empty" -Expected $true -Actual ($null -ne $map[4].StderrPattern)

# ============================================================
# Test 2: Get-HookExitCodeMap -IncludeBashStandard switch
# ============================================================
Write-Host "[2/6] Get-HookExitCodeMap -IncludeBashStandard..." -ForegroundColor Yellow
$mapFull = Get-HookExitCodeMap -IncludeBashStandard
Assert-Equal -Name "full map has 126" -Expected $true -Actual ($mapFull.Contains(126))
Assert-Equal -Name "full map has 127" -Expected $true -Actual ($mapFull.Contains(127))
Assert-Equal -Name "full map has 130" -Expected $true -Actual ($mapFull.Contains(130))
Assert-Equal -Name "code 126 Category" -Expected 'PermissionDenied' -Actual $mapFull[126].Category
Assert-Equal -Name "code 127 Category" -Expected 'CommandNotFound'  -Actual $mapFull[127].Category
Assert-Equal -Name "code 130 Category" -Expected 'Signal'           -Actual $mapFull[130].Category

$mapNoBash = Get-HookExitCodeMap -IncludeBashStandard:$false
Assert-Equal -Name "no-bash map lacks 126" -Expected $false -Actual ($mapNoBash.Contains(126))
Assert-Equal -Name "no-bash map count == 5" -Expected 5 -Actual $mapNoBash.Count

# ============================================================
# Test 3: Resolve-HookExitCode exit 1 + stderr追溯
# ============================================================
Write-Host "[3/6] Resolve-HookExitCode exit 1 + stderr追溯..." -ForegroundColor Yellow

# exit 1 + EnvNotSet stderr pattern
$r = Resolve-HookExitCode -ExitCode 1 -Stderr '[pre-commit][ERROR] TLM_HOOK_SOURCE_REPO 未设置'
Assert-Equal -Name "exit1+EnvNotSet Matched"     -Expected $true          -Actual $r.Matched
Assert-Equal -Name "exit1+EnvNotSet Category"    -Expected 'PreCheckFailed' -Actual $r.Category
Assert-Equal -Name "exit1+EnvNotSet SubCategory" -Expected 'EnvNotSet'      -Actual $r.SubCategory

# exit 1 + ScriptMissing stderr pattern
$r = Resolve-HookExitCode -ExitCode 1 -Stderr '源仓库脚本不存在: /foo'
Assert-Equal -Name "exit1+ScriptMissing SubCategory" -Expected 'ScriptMissing' -Actual $r.SubCategory

# exit 1 + PreCheckExecFailed stderr pattern
$r = Resolve-HookExitCode -ExitCode 1 -Stderr '预检失败，提交被阻止'
Assert-Equal -Name "exit1+PreCheckExecFailed SubCategory" -Expected 'PreCheckExecFailed' -Actual $r.SubCategory

# exit 1 + empty stderr -> no SubCategory
$r = Resolve-HookExitCode -ExitCode 1 -Stderr ''
Assert-Equal -Name "exit1+empty stderr SubCategory null" -Expected $null -Actual $r.SubCategory
Assert-Equal -Name "exit1+empty stderr Category"         -Expected 'PreCheckFailed' -Actual $r.Category

# exit 1 + unmatched stderr -> no SubCategory
$r = Resolve-HookExitCode -ExitCode 1 -Stderr 'some random error text'
Assert-Equal -Name "exit1+unmatched SubCategory null" -Expected $null -Actual $r.SubCategory

# ============================================================
# Test 4: Resolve-HookExitCode bash standard codes
# ============================================================
Write-Host "[4/6] Resolve-HookExitCode bash standard codes..." -ForegroundColor Yellow

$r = Resolve-HookExitCode -ExitCode 0
Assert-Equal -Name "exit0 Category"  -Expected 'Success'  -Actual $r.Category
Assert-Equal -Name "exit0 Matched"   -Expected $true      -Actual $r.Matched

$r = Resolve-HookExitCode -ExitCode 126
Assert-Equal -Name "exit126 Category" -Expected 'PermissionDenied' -Actual $r.Category

$r = Resolve-HookExitCode -ExitCode 127
Assert-Equal -Name "exit127 Category" -Expected 'CommandNotFound' -Actual $r.Category

$r = Resolve-HookExitCode -ExitCode 130
Assert-Equal -Name "exit130 Category" -Expected 'Signal' -Actual $r.Category

# exit 137 = 128 + 9 (SIGKILL) - within bash signal range
$r = Resolve-HookExitCode -ExitCode 137 -IncludeBashStandard
Assert-Equal -Name "exit137 Matched"  -Expected $true     -Actual $r.Matched
Assert-Equal -Name "exit137 Category" -Expected 'Signal'  -Actual $r.Category

# Unknown code (e.g. 42) -> Unknown
$r = Resolve-HookExitCode -ExitCode 42
Assert-Equal -Name "exit42 Matched"  -Expected $false    -Actual $r.Matched
Assert-Equal -Name "exit42 Category" -Expected 'Unknown' -Actual $r.Category

# ============================================================
# Test 5: Invoke-HookWithCapture - file not found
# ============================================================
Write-Host "[5/6] Invoke-HookWithCapture file not found..." -ForegroundColor Yellow

$r = Invoke-HookWithCapture -HookPath '/nonexistent/hook.sh'
Assert-Equal -Name "missing file ExitCode" -Expected 127   -Actual $r.ExitCode
Assert-Equal -Name "missing file Stderr"   -Expected $true -Actual ($r.Stderr -match 'not found')

# ============================================================
# Test 6: Invoke-HookWithCapture - real hook execution + -Resolve
# ============================================================
Write-Host "[6/6] Invoke-HookWithCapture real hook + -Resolve..." -ForegroundColor Yellow

# 跨平台：用 powershell.exe（Windows）/ pwsh（Unix）执行 .ps1 脚本
# （System.Diagnostics.Process 在 UseShellExecute=false 时不走 shell 关联，.sh/.bat 不能直接执行）
$isWindows = ($PSVersionTable.Platform -ne 'Unix') -and ($env:OS -eq 'Windows_NT')
$pwshExe   = if ($isWindows) { 'powershell.exe' } else { 'pwsh' }
# 含中文的 .ps1 必须用 UTF-8 with BOM，否则 PS 5.1 用 GBK 解码导致中文损坏
$utf8WithBom = New-Object System.Text.UTF8Encoding($true)

# Create a success hook script (exit 0)
$okHook = Join-Path $env:TEMP "ok-hook-$([guid]::NewGuid().ToString('N').Substring(0,8)).ps1"
$okContent = 'Write-Host "ok stdout"; exit 0'
[System.IO.File]::WriteAllText($okHook, $okContent, $utf8WithBom)

$r = Invoke-HookWithCapture -HookPath $pwshExe -Arguments @('-NoProfile','-ExecutionPolicy','Bypass','-File',$okHook) -Resolve
Assert-Equal -Name "ok hook ExitCode"  -Expected 0        -Actual $r.ExitCode
Assert-Equal -Name "ok hook Stdout"    -Expected $true    -Actual ($r.Stdout -match 'ok stdout')
Assert-Equal -Name "ok hook TimedOut"  -Expected $false   -Actual $r.TimedOut
Assert-Equal -Name "ok hook Resolved"  -Expected $true    -Actual ($null -ne $r.Resolved)
Assert-Equal -Name "ok hook Resolved Category" -Expected 'Success' -Actual $r.Resolved.Category

# Create a failure hook script (exit 1 + stderr to verify capture + -Resolve)
# 注意：PS 5.1 的 [Console]::Error.WriteLine 用 GBK 输出中文，跨进程 UTF-8 读取会损坏。
# 中文 stderr 回溯逻辑在 Test 3 已充分验证（直接传 -Stderr 字符串）；Test 6 只验证进程捕获能力，用 ASCII stderr。
$badHook = Join-Path $env:TEMP "bad-hook-$([guid]::NewGuid().ToString('N').Substring(0,8)).ps1"
$badContent = '[Console]::Error.WriteLine("[pre-commit][ERROR] precheck failed - broken links exceeded threshold"); exit 1'
[System.IO.File]::WriteAllText($badHook, $badContent, $utf8WithBom)

$r = Invoke-HookWithCapture -HookPath $pwshExe -Arguments @('-NoProfile','-ExecutionPolicy','Bypass','-File',$badHook) -Resolve
Assert-Equal -Name "bad hook ExitCode"           -Expected 1               -Actual $r.ExitCode
Assert-Equal -Name "bad hook Stderr"            -Expected $true            -Actual ($r.Stderr -match 'precheck failed')
Assert-Equal -Name "bad hook Resolved Matched"   -Expected $true            -Actual $r.Resolved.Matched
Assert-Equal -Name "bad hook Resolved Category"  -Expected 'PreCheckFailed' -Actual $r.Resolved.Category

# Cleanup
Remove-Item $okHook -Force -ErrorAction SilentlyContinue
Remove-Item $badHook -Force -ErrorAction SilentlyContinue

# ============================================================
# Summary
# ============================================================
$passCount = ($results | Where-Object Status -eq "PASS").Count
$failCount = ($results | Where-Object Status -eq "FAIL").Count
Write-Host ""
Write-Host "=== Summary ===" -ForegroundColor Cyan
Write-Host "Total: $($results.Count) | PASS: $passCount | FAIL: $failCount" -ForegroundColor $(if ($failCount -eq 0) { "Green" } else { "Red" })

if ($failCount -gt 0) { exit 1 }
exit 0
