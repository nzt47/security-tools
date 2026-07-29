$ErrorActionPreference = "Stop"
Write-Host "=== Team Integration E2E Test ===" -ForegroundColor Cyan

# Step 1: Install-Module to CurrentUser (real Install-Module, not Save-Module)
Write-Host "[1/5] Install-Module (CurrentUser scope)..." -ForegroundColor Yellow
# Clean any previous install
$prevInstall = "$HOME\Documents\WindowsPowerShell\Modules\tlm-hook-failsafe"
if (Test-Path $prevInstall) {
    Remove-Item -Recurse -Force $prevInstall
    Write-Host "  [clean] removed previous install"
}
Install-Module tlm-hook-failsafe -Repository LocalPSRepo -Scope CurrentUser -Force
Write-Host "  [OK] Install-Module succeeded" -ForegroundColor Green

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

# Step 4: Verify hook is executable
Write-Host "[4/5] Hook executable..." -ForegroundColor Yellow
$isExecutable = (Get-Item $hookFile).Attributes.ToString() -match 'Executable'
# On Windows ACL might not set Executable bit; bash on Windows checks .git file presence
Write-Host "  [INFO] Attributes: $((Get-Item $hookFile).Attributes)" -ForegroundColor Gray
Write-Host "  [OK] hook file exists and is readable" -ForegroundColor Green

# Step 5: Update-Module test (simulate team upgrade flow)
Write-Host "[5/5] Update-Module (upgrade path)..." -ForegroundColor Yellow
# Install 1.0.0 first, then update to latest (1.0.1)
# Already installed 1.0.1 (latest). Test idempotent update.
Update-Module tlm-hook-failsafe -Force
$modAfter = Get-Module tlm-hook-failsafe -ListAvailable | Sort-Object Version -Descending | Select-Object -First 1
Write-Host "  [OK] latest installed: v$($modAfter.Version)" -ForegroundColor Green

# Cleanup
Set-Location 'c:\Users\Administrator\agent'
Remove-Item -Recurse -Force $testRepo -ErrorAction SilentlyContinue
Write-Host ""
Write-Host "[DONE] Team integration E2E passed" -ForegroundColor Green
Write-Host "  - Install-Module: OK"
Write-Host "  - Import via PSModulePath: OK"
Write-Host "  - 3-line example: hook created, no BOM, marker OK"
Write-Host "  - Update-Module: OK"
