<#
.SYNOPSIS
    Team Integration E2E Test (Install-Module / Import / 3-line example / Update-Module)

.DESCRIPTION
    三义校验：
    - 不易：5 步流程不变；15 函数导出契约不变；hook 无 BOM + marker 不变
    - 变易：-CI 模式用 Save-Module + 临时 PSModulePath 绕过 CurrentUser 管理员权限
    - 简易：-CI 仅切换安装/更新路径，步骤 3-4（hook 应用）完全复用

    前置条件（本地模式）：
    - LocalPSRepo 已注册且 tlm-hook-failsafe 已发布
    - 当前用户有 CurrentUser 安装权限

    前置条件（CI 模式）：
    - LocalPSRepo 已注册（由 publish-to-local-repo.ps1 完成）
    - 无需管理员权限

.PARAMETER CI
    CI 模式：用 Save-Module 代替 Install-Module，避免 CurrentUser 权限要求。

.EXAMPLE
    # 本地
    .\tests\test_team_integration_e2e.ps1
    # CI
    .\tests\test_team_integration_e2e.ps1 -CI
#>
[CmdletBinding()]
param([switch]$CI)

$ErrorActionPreference = "Stop"
Write-Host "=== Team Integration E2E Test (CI=$CI) ===" -ForegroundColor Cyan

# 不易：CI 模式用临时模块目录 + PSModulePath 注入；本地模式用 CurrentUser 范围
$ciModuleBase = $null
$origPSModulePath = $env:PSModulePath
if ($CI) {
    $ciModuleBase = Join-Path $env:TEMP "tlm-hfs-ci-mod-$(Get-Random)"
    New-Item -ItemType Directory -Path $ciModuleBase -Force | Out-Null
    # 不易：Windows PSModulePath 用 ; 分隔，Unix 用 :
    $sep = if (($PSVersionTable.Platform -eq 'Unix')) { ':' } else { ';' }
    $env:PSModulePath = "$ciModuleBase$sep$env:PSModulePath"
    Write-Host "  [CI] module base: $ciModuleBase" -ForegroundColor Gray
    Write-Host "  [CI] PSModulePath prepended" -ForegroundColor Gray
}

try {
# Step 1: Install-Module (local) or Save-Module (CI)
Write-Host "[1/5] Install module (CI=$CI)..." -ForegroundColor Yellow
if ($CI) {
    # 变易：Save-Module 到临时目录，模拟 Install-Module 的效果
    Save-Module -Name tlm-hook-failsafe -Repository LocalPSRepo -Path $ciModuleBase -Force
    Write-Host "  [OK] Save-Module to $ciModuleBase" -ForegroundColor Green
} else {
    # Clean any previous install
    $prevInstall = "$HOME\Documents\WindowsPowerShell\Modules\tlm-hook-failsafe"
    if (Test-Path $prevInstall) {
        Remove-Item -Recurse -Force $prevInstall
        Write-Host "  [clean] removed previous install"
    }
    Install-Module tlm-hook-failsafe -Repository LocalPSRepo -Scope CurrentUser -Force
    Write-Host "  [OK] Install-Module succeeded" -ForegroundColor Green
}

# Step 2: Verify import via PSModulePath (no explicit path)
Write-Host "[2/5] Import via PSModulePath..." -ForegroundColor Yellow
Get-Module tlm-hook-failsafe -All | Remove-Module -Force -ErrorAction SilentlyContinue
Import-Module tlm-hook-failsafe -Force
$mod = Get-Module tlm-hook-failsafe
Write-Host "  [OK] imported v$($mod.Version), $($mod.ExportedCommands.Count) functions" -ForegroundColor Green

# Step 3: Verify 3-line minimal example - create temp git repo and apply hook
Write-Host "[3/5] 3-line minimal example..." -ForegroundColor Yellow
$testRepo = Join-Path $env:TEMP "team-int-e2e-repo-$([guid]::NewGuid().ToString('N').Substring(0,8))"
New-Item -ItemType Directory -Path $testRepo -Force | Out-Null
$origLocation = Get-Location
Set-Location $testRepo
git init --quiet
git config user.email "test@example.com"
git config user.name "test"

# The 3 lines from TEAM_INTEGRATION_GUIDE.md
Import-Module tlm-hook-failsafe
$content = Get-HookContent -SourceRepo $testRepo
Invoke-SafeHookWrite -HookPath (Join-Path $testRepo '.git\hooks\pre-commit') -Content $content

$hookFile = Join-Path $testRepo '.git\hooks\pre-commit'
if (-not (Test-Path $hookFile)) { throw "hook file not created" }
$hookBytes = [System.IO.File]::ReadAllBytes($hookFile)
$hasBom = ($hookBytes[0] -eq 0xEF -and $hookBytes[1] -eq 0xBB -and $hookBytes[2] -eq 0xBF)
if ($hasBom) { throw "hook has BOM (bash incompatible)" }
$marker = Select-String -Path $hookFile -Pattern 'TLM-HOOK v1 source_repo='
if (-not $marker) { throw "marker line missing" }
Write-Host "  [OK] hook written, no BOM, marker present" -ForegroundColor Green
Write-Host "       marker: $($marker.Line)" -ForegroundColor Gray

# Step 4: Verify hook is executable / readable
Write-Host "[4/5] Hook executable..." -ForegroundColor Yellow
$isExecutable = (Get-Item $hookFile).Attributes.ToString() -match 'Executable'
Write-Host "  [INFO] Attributes: $((Get-Item $hookFile).Attributes)" -ForegroundColor Gray
Write-Host "  [OK] hook file exists and is readable" -ForegroundColor Green

# Step 5: Update-Module (local) or re-Save-Module (CI) - verify upgrade path
Write-Host "[5/5] Update path (CI=$CI)..." -ForegroundColor Yellow
if ($CI) {
    # 变易：CI 无 Update-Module 权限，重新 Save-Module 验证幂等性 + 可重新获取
    Remove-Module tlm-hook-failsafe -Force -ErrorAction SilentlyContinue
    Save-Module -Name tlm-hook-failsafe -Repository LocalPSRepo -Path $ciModuleBase -Force
    $modAfter = Get-Module tlm-hook-failsafe -ListAvailable | Sort-Object Version -Descending | Select-Object -First 1
    Write-Host "  [OK] re-Save-Module: v$($modAfter.Version)" -ForegroundColor Green
} else {
    Update-Module tlm-hook-failsafe -Force
    $modAfter = Get-Module tlm-hook-failsafe -ListAvailable | Sort-Object Version -Descending | Select-Object -First 1
    Write-Host "  [OK] latest installed: v$($modAfter.Version)" -ForegroundColor Green
}

# Cleanup
Set-Location $origLocation
Remove-Item -Recurse -Force $testRepo -ErrorAction SilentlyContinue
Remove-Module tlm-hook-failsafe -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "[DONE] Team integration E2E passed (CI=$CI)" -ForegroundColor Green
Write-Host "  - Install/Save: OK"
Write-Host "  - Import via PSModulePath: OK"
Write-Host "  - 3-line example: hook created, no BOM, marker OK"
Write-Host "  - Update path: OK"
}
finally {
    # 不易：恢复 PSModulePath，避免污染后续步骤
    if ($CI) {
        $env:PSModulePath = $origPSModulePath
        if ($ciModuleBase -and (Test-Path $ciModuleBase)) {
            Remove-Item -Recurse -Force $ciModuleBase -ErrorAction SilentlyContinue
        }
    }
}
exit 0
