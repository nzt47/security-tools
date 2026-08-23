# Local simulation of CI/CD test step (Windows PowerShell native version)
# Counterpart: .github/workflows/deploy.yml test job + scripts/dev/run_ci_test_local.sh
#
# Runs: route mount self-check (check_mounted_routes.py) + unit tests (A/B)
#
# Usage (PowerShell 5.1 / 7+):
#   .\run_ci_test_local.ps1                  # full (includes open_api endpoints, slower)
#   .\run_ci_test_local.ps1 -Quick           # audit alert tests only (fast)
#   .\run_ci_test_local.ps1 -Python py       # explicit interpreter
#
# Exit code: 0 = all passed; 1 = any failure
param(
    [switch]$Quick,          # only audit alert unit tests
    [string]$Python = ""     # python interpreter; auto-detect if empty
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

# Detect Python interpreter (python first: usually the one with dev deps installed;
# py launcher may resolve to a different install lacking pytest plugins)
if (-not $Python) {
    foreach ($c in @('python', 'py', 'python3')) {
        if (Get-Command $c -ErrorAction SilentlyContinue) { $Python = $c; break }
    }
}
if (-not $Python) {
    Write-Error "Python interpreter not found (pass -Python <path> explicitly)."
    exit 2
}

$failed = $false

Write-Host "== [test] route mount self-check =="
& $Python -X utf8 scripts/check_mounted_routes.py
if ($LASTEXITCODE -ne 0) { $failed = $true }

Write-Host ""
Write-Host "== [test] unit tests (pytest) =="
$testFiles = @(
    'tests/unit/test_audit_alert_analyze.py',    # A: alert logic + B: SMTP send
    'tests/unit/test_open_api_endpoints.py'      # open API auth (401/403/200)
)
if ($Quick) {
    $testFiles = @($testFiles[0])                # audit alert only (fast)
    Write-Host "( -Quick mode: only $($testFiles[0]) )"
}
& $Python -X utf8 -m pytest @testFiles -q
if ($LASTEXITCODE -ne 0) { $failed = $true }

Write-Host ""
if ($failed) {
    Write-Host "[FAIL] local CI test simulation failed (see output above)"
    exit 1
}
Write-Host "[OK] local CI test simulation passed"
exit 0
