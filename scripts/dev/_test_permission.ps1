# Test permission repair logic (PS 5.1 compatible)
$ErrorActionPreference = "Continue"
Import-Module (Resolve-Path "scripts\dev\hook_fail_safe.psm1").Path -Force

$testRepo = Join-Path $env:TEMP "perm_test_$(Get-Random)"
$hookPath = "$testRepo\.git\hooks\pre-commit"
New-Item -ItemType Directory -Path (Split-Path $hookPath -Parent) -Force | Out-Null

function Show-Result($name, $pass) {
    $tag = if ($pass) { 'PASS' } else { 'FAIL' }
    $color = if ($pass) { 'Green' } else { 'Red' }
    Write-Host "  [$tag] $name" -ForegroundColor $color
}

Write-Host "=== Test 1: Test-HookExecutable on non-existent file ===" -ForegroundColor Cyan
$r = Test-HookExecutable -HookPath "C:\nonexistent\hook"
$pass1 = (-not $r.IsExecutable) -and ($r.Issues.Count -gt 0)
Show-Result "non-existent file detected" $pass1

Write-Host "`n=== Test 2: Write hook + check executable ===" -ForegroundColor Cyan
$content = Get-HookContent -SourceRepo "C:\test"
$writeResult = Invoke-SafeHookWrite -HookPath $hookPath -Content $content
$pass2 = $writeResult.Written -and $writeResult.PermissionOk
Show-Result "hook written and executable" $pass2

Write-Host "`n=== Test 3: Read-only hook repair ===" -ForegroundColor Cyan
$fileInfo = Get-Item $hookPath -Force
$fileInfo.IsReadOnly = $true
$permCheck = Test-HookExecutable -HookPath $hookPath
$pass3a = -not $permCheck.IsExecutable
Show-Result "read-only detected" $pass3a

$repair = Repair-HookPermission -HookPath $hookPath
$pass3b = $repair.Repaired -and ($repair.Actions -contains '移除只读属性')
Show-Result "read-only repaired" $pass3b

$permCheck2 = Test-HookExecutable -HookPath $hookPath
$pass3c = $permCheck2.IsExecutable
Show-Result "executable after repair" $pass3c

Write-Host "`n=== Test 4: Invoke-SafeHookWrite on read-only file ===" -ForegroundColor Cyan
$fileInfo = Get-Item $hookPath -Force
$fileInfo.IsReadOnly = $true
$newContent = Get-HookContent -SourceRepo "C:\updated"
$writeResult2 = Invoke-SafeHookWrite -HookPath $hookPath -Content $newContent
$pass4 = $writeResult2.Written -and $writeResult2.Repaired -and $writeResult2.PermissionOk
Show-Result "auto-repair on write" $pass4

$finalContent = [System.IO.File]::ReadAllText($hookPath, [System.Text.Encoding]::UTF8)
# 用 -like 避免 -match 正则中 \\ 的歧义（PS 5.1 单引号字符串解析问题）
$pass4b = $finalContent -like '*source_repo=C:\updated*'
Show-Result "content updated to new source_repo" $pass4b

Write-Host "`n=== Summary ===" -ForegroundColor Cyan
$tests = @($pass1, $pass2, $pass3a, $pass3b, $pass3c, $pass4, $pass4b)
$passCount = ($tests | Where-Object { $_ }).Count
Write-Host "  Total: $($tests.Count) | PASS: $passCount | FAIL: $($tests.Count - $passCount)"

Remove-Item -Recurse -Force $testRepo -ErrorAction SilentlyContinue
Write-Host "  [Cleanup] removed" -ForegroundColor Gray

if ($passCount -lt $tests.Count) { exit 1 }
exit 0
