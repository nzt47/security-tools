<#
.SYNOPSIS
    模拟批量同步场景：在 D:\code 下新建 3 个空仓库并运行 sync 脚本验证

.DESCRIPTION
    验证步骤：
    1. 在 D:\code 下创建 3 个空 git 仓库（repo-alpha / repo-beta / repo-gamma）
    2. 运行 sync_precommit_hook.ps1 -Sync -ScanRoot D:\code
    3. 验证每个仓库的 hook 是否正确部署（存在/无 BOM/marker/阈值）
    4. 在 repo-alpha 中真实触发一次 hook 阻塞测试
    5. 运行 -Status 查看汇总
    6. 询问是否保留测试仓库（默认清理）

.PARAMETER KeepRepos
    保留测试仓库不清理（便于人工检查）

.EXAMPLE
    .\scripts\dev\simulate_batch_sync.ps1
    .\scripts\dev\simulate_batch_sync.ps1 -KeepRepos
#>
[CmdletBinding()]
param(
    [switch]$KeepRepos
)

$ErrorActionPreference = "Continue"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $ProjectRoot

$testRoot = "D:\code"
$repos = @("repo-alpha", "repo-beta", "repo-gamma")
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$utf8Bom = New-Object System.Text.UTF8Encoding($true)

Write-Host "=== 批量同步模拟测试 ===" -ForegroundColor Cyan
Write-Host "  测试根目录: $testRoot"
Write-Host "  测试仓库: $($repos -join ', ')"
Write-Host "  保留仓库: $KeepRepos"

# --- 0. 预检查：D:\code 是否已存在 ---
if (Test-Path $testRoot) {
    $existing = Get-ChildItem $testRoot -Force -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "`n[ERROR] $testRoot 已存在且非空，请先清理后重试" -ForegroundColor Red
        Write-Host "  现有内容: $($existing.Name -join ', ')"
        exit 1
    }
}

# --- 1. 创建 3 个空 git 仓库 ---
Write-Host "`n[1/5] 创建 3 个空 git 仓库..." -ForegroundColor Yellow
New-Item -ItemType Directory -Path $testRoot -Force | Out-Null
foreach ($r in $repos) {
    $p = Join-Path $testRoot $r
    New-Item -ItemType Directory -Path $p -Force | Out-Null
    git -C $p init 2>&1 | Out-Null
    # 必须配置 user.email/name，否则 commit 会失败
    git -C $p config user.email "test@example.com"
    git -C $p config user.name "Batch Sync Test"
    Write-Host "  [OK] $p" -ForegroundColor Green
}

# --- 2. 运行 sync 脚本 ---
Write-Host "`n[2/5] 运行 sync_precommit_hook.ps1 -Sync..." -ForegroundColor Yellow
& powershell -ExecutionPolicy Bypass -File scripts\dev\sync_precommit_hook.ps1 -Sync -ScanRoot $testRoot

# --- 3. 逐仓库验证 hook 部署 ---
Write-Host "`n[3/5] 验证 hook 部署质量..." -ForegroundColor Yellow
$tests = @()
foreach ($r in $repos) {
    $hookPath = "$testRoot\$r\.git\hooks\pre-commit"

    $exists = Test-Path $hookPath
    $tests += [PSCustomObject]@{ Repo = $r; Check = 'hook 存在'; Expected = $true; Actual = $exists }

    if ($exists) {
        $bytes = [System.IO.File]::ReadAllBytes($hookPath)
        $noBom = -not ($bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
        $tests += [PSCustomObject]@{ Repo = $r; Check = '无 BOM (bash 兼容)'; Expected = $true; Actual = $noBom }

        $content = [System.IO.File]::ReadAllText($hookPath, [System.Text.Encoding]::UTF8)
        $tests += [PSCustomObject]@{ Repo = $r; Check = 'marker TLM-HOOK v1'; Expected = $true; Actual = ($content -match 'TLM-HOOK v1') }
        $tests += [PSCustomObject]@{ Repo = $r; Check = '阈值 AllowBroken 0'; Expected = $true; Actual = ($content -match '-AllowBroken 0') }
        $tests += [PSCustomObject]@{ Repo = $r; Check = '使用 $TLM_HOOK_SOURCE_REPO'; Expected = $true; Actual = ($content -match '\$TLM_HOOK_SOURCE_REPO') }
    }
}

# 打印验证结果
$pass = 0
$fail = 0
foreach ($t in $tests) {
    $ok = $t.Expected -eq $t.Actual
    $color = if ($ok) { 'Green' } else { 'Red' }
    $status = if ($ok) { 'PASS' } else { 'FAIL' }
    Write-Host "  [$status] $($t.Repo) | $($t.Check)" -ForegroundColor $color
    if ($ok) { $pass++ } else { $fail++ }
}
Write-Host "  部署质量验证: $pass/$($tests.Count) passed" -ForegroundColor $(if ($fail -eq 0) { 'Green' } else { 'Red' })
$qualityFail = $fail

# --- 4. 在 repo-alpha 真实触发一次 hook 阻塞测试 ---
Write-Host "`n[4/5] repo-alpha 真实 hook 阻塞测试..." -ForegroundColor Yellow
$env:TLM_HOOK_SOURCE_REPO = $ProjectRoot
$alphaDocs = "$testRoot\repo-alpha\docs"
New-Item -ItemType Directory -Path $alphaDocs -Force | Out-Null
$brokenMd = "$alphaDocs\test.md"
'# Test' + "`n`n" + '- [broken-link](nonexistent.md)' + "`n" | Set-Content $brokenMd -Encoding utf8

git -C "$testRoot\repo-alpha" add . 2>&1 | Out-Null
$commitResult = git -C "$testRoot\repo-alpha" commit -m "test broken" 2>&1 | Out-String
$commitExit = $LASTEXITCODE

# git commit 输出会被 stderr 触发 RemoteException，但 exit code 可用
if ($commitExit -ne 0 -and $commitResult -match 'BLOCK') {
    Write-Host "  [PASS] hook 正确阻塞了含失效链接的提交" -ForegroundColor Green
    $hookBlockPass = $true
} else {
    Write-Host "  [FAIL] hook 未阻塞。exit=$commitExit" -ForegroundColor Red
    Write-Host $commitResult
    $hookBlockPass = $false
}

# 还原 repo-alpha 到测试前状态
git -C "$testRoot\repo-alpha" reset --mixed HEAD 2>&1 | Out-Null
Remove-Item $brokenMd -Force -ErrorAction SilentlyContinue

# --- 5. 运行 -Status 查看汇总 ---
Write-Host "`n[5/5] 运行 -Status 查看汇总..." -ForegroundColor Yellow
& powershell -ExecutionPolicy Bypass -File scripts\dev\sync_precommit_hook.ps1 -Status -ScanRoot $testRoot

# --- 总结 ---
Write-Host "`n=== 总结 ===" -ForegroundColor Cyan
Write-Host "  部署质量验证: $pass/$($tests.Count) passed"
Write-Host "  hook 阻塞测试: $(if ($hookBlockPass) { 'PASS' } else { 'FAIL' })"
$totalPass = if ($qualityFail -eq 0 -and $hookBlockPass) { 'ALL PASS' } else { 'HAS FAIL' }
Write-Host "  总体: $totalPass" -ForegroundColor $(if ($totalPass -eq 'ALL PASS') { 'Green' } else { 'Red' })

# --- 清理 ---
if ($KeepRepos) {
    Write-Host "`n[KeepRepos] 测试仓库保留在 $testRoot" -ForegroundColor Yellow
} else {
    Write-Host "`n[Cleanup] 删除测试仓库 $testRoot..." -ForegroundColor Gray
    Remove-Item -Recurse -Force $testRoot -ErrorAction SilentlyContinue
    if (-not (Test-Path $testRoot)) {
        Write-Host "  [OK] 已清理" -ForegroundColor Green
    } else {
        Write-Host "  [WARN] 清理失败，请手动删除 $testRoot" -ForegroundColor Yellow
    }
}

if ($qualityFail -gt 0 -or -not $hookBlockPass) { exit 1 }
exit 0
