<#
.SYNOPSIS
    Chaos regression test script (Windows PowerShell)
.DESCRIPTION
    Wraps tests/chaos/ with three modes: quick / full / ci.
    Generates JUnit XML report and structured logs.
.PARAMETER Mode
    quick | full | ci (default: quick)
.EXAMPLE
    .\scripts\run_chaos_regression.ps1 -Mode full
.NOTES
    Must run in repo root via & operator (not powershell -File subprocess).
#>

param(
    [ValidateSet("quick", "full", "ci")]
    [string]$Mode = "quick",
    [switch]$VerboseLog = $true
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001 > $null 2>&1

$RepoRoot = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $RepoRoot "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogFile = Join-Path $LogDir "chaos_regression_${Timestamp}.log"
$JUnitFile = Join-Path $LogDir "chaos_report_${Timestamp}.xml"

$StartTime = Get-Date

Write-Host "========== Chaos Regression Test =========="
Write-Host "Mode:        $Mode"
Write-Host "Start time:  $($StartTime.ToString('yyyy-MM-dd HH:mm:ss'))"
Write-Host "Log file:    $LogFile"
Write-Host "JUnit report: $JUnitFile"
Write-Host ""

# CRITICAL: --override-ini=testpaths= required, else pytest.ini testpaths=tests
# overrides the tests/chaos/ arg and collects the entire tests/ tree
$PytestArgs = @("tests/chaos/")

switch ($Mode) {
    "quick" {
        Write-Host "=== Mode: quick (core chaos tests only, ~15s) ==="
        $PytestArgs += @("-v", "--tb=short", "-p", "no:cacheprovider")
    }
    "full" {
        Write-Host "=== Mode: full (including slow tests, ~70s) ==="
        $PytestArgs += @("-v", "--tb=short", "--runslow", "-p", "no:cacheprovider")
    }
    "ci" {
        Write-Host "=== Mode: ci (simulate GitHub Actions chaos-tests job, scope=chaos-and-p2) ==="
        $PytestArgs += @("tests/unit/test_impact_analysis_cache.py", "-v", "--tb=short", "-m", "chaos or p2", "-p", "no:cacheprovider")
    }
}

$PytestArgs += @("--junitxml=$JUnitFile", "-o", "junit_logging=all", "--override-ini=testpaths=")

if ($VerboseLog) {
    Write-Host "=== Pytest args: $($PytestArgs -join ' ') ==="
    Write-Host ""
}

$ExitCode = 0
try {
    & python -m pytest @PytestArgs 2>&1 | Tee-Object -FilePath $LogFile
    $ExitCode = $LASTEXITCODE
} catch {
    Write-Host "FATAL: pytest execution failed: $_"
    $ExitCode = 1
}

$EndTime = Get-Date
$Duration = ($EndTime - $StartTime).TotalSeconds

Write-Host ""
Write-Host "========== Regression Test Summary =========="
Write-Host "Mode:        $Mode"
Write-Host "Duration:    $([math]::Round($Duration, 2))s"
Write-Host "Exit code:   $ExitCode"
Write-Host "Log file:    $LogFile"
Write-Host "JUnit report: $JUnitFile"
Write-Host "============================================="
Write-Host ""
Write-Host "Tip: Filter structured logs by module:"
Write-Host "  Select-String -Path '$LogFile' -Pattern '\[CB_CHAOS\]'      # Circuit Breaker"
Write-Host "  Select-String -Path '$LogFile' -Pattern '\[RL_CHAOS\]'      # Rate Limiter"
Write-Host "  Select-String -Path '$LogFile' -Pattern '\[DEGRADE_CHAOS\]' # Degradation"
Write-Host "  Select-String -Path '$LogFile' -Pattern '\[DR_CHAOS\]'      # Disaster Recovery"

exit $ExitCode
