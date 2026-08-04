<#
.SYNOPSIS
    Config regression test: verify Reranker hot-reload + OMP settings still valid in Docker.

.DESCRIPTION
    Checks production-critical configs one by one. Any failure prints [FAIL] and exits non-zero,
    suitable for CI or Task Scheduler periodic runs.
    Coverage:
      1. .env key vars present & correct (ENABLED=true / USE_ONNX=true / OMP=4 / MKL=4 / INTERVAL=30)
      2. docker-compose.yml forwards SKILL_RERANKER_MODEL (container path, not Windows path)
      3. Container running & healthy
      4. Container env injected (OMP_NUM_THREADS=4)
      5. Container code has hot-reload impl (_hot_reload > 0)
      6. Container model has ONNX variant file (onnx/model_quantized.onnx)
      7. torch threads == OMP_NUM_THREADS (OMP actually effective)

.PARAMETER SkipHealth
    Skip health check (container may still be in start_period).

.PARAMETER Container
    Target container name (default agent-digital-life-1).

.EXAMPLE
    .\scripts\dev\verify_config_regression.ps1
    .\scripts\dev\verify_config_regression.ps1 -SkipHealth

.NOTES
    Docs: docs/CONFIG_ENV_REFERENCE.md / docs/RERANKER_HOT_RELOAD_GUIDE.md
    ASCII-only output for Windows PowerShell 5.1 compatibility (UTF-8 without BOM issue).
#>
[CmdletBinding()]
param(
    [switch]$SkipHealth,
    [string]$Container = 'agent-digital-life-1'
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $ProjectRoot

$pass = 0
$fail = 0

function Assert-Check {
    param([string]$Name, [bool]$Ok, [string]$Detail = '')
    if ($Ok) {
        $script:pass++
        Write-Host "  [PASS] $Name" -ForegroundColor Green
        if ($Detail) { Write-Host "         $Detail" -ForegroundColor DarkGray }
    } else {
        $script:fail++
        Write-Host "  [FAIL] $Name" -ForegroundColor Red
        if ($Detail) { Write-Host "         $Detail" -ForegroundColor Yellow }
    }
}

Write-Host '=== Config Regression Test (Reranker Hot-Reload + OMP) ===' -ForegroundColor Cyan
Write-Host "Container: $Container   Time: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')`n"

# ---------- 1. .env key vars ----------
Write-Host '[1/7] .env key vars' -ForegroundColor Yellow
$envContent = Get-Content .env -Raw -Encoding UTF8

$envChecks = @{
    'SKILL_RERANKER_ENABLED'       = 'true'
    'SKILL_RERANKER_USE_ONNX'      = 'true'
    'SKILL_RERANKER_ONNX_VARIANT'  = 'model_quantized.onnx'
    'SKILL_RERANKER_HOT_RELOAD_INTERVAL' = '30'
    'OMP_NUM_THREADS'              = '4'
    'MKL_NUM_THREADS'              = '4'
}
foreach ($k in $envChecks.Keys) {
    $m = [regex]::Match($envContent, "^$k=([^\r\n]+)", [System.Text.RegularExpressions.RegexOptions]::Multiline)
    $val = if ($m.Success) { $m.Groups[1].Value.Trim() } else { '' }
    $ok = ($val -eq $envChecks[$k])
    $detail = if ($m.Success) { "current: $val" } else { 'not found' }
    Assert-Check "$k=$($envChecks[$k])" $ok $detail
}

# ---------- 2. docker-compose.yml model path forwarding ----------
Write-Host '[2/7] docker-compose.yml model path' -ForegroundColor Yellow
$composeContent = Get-Content docker-compose.yml -Raw -Encoding UTF8
$modelMatch = [regex]::Match($composeContent, 'SKILL_RERANKER_MODEL=([^\s]+)')
$modelPath = if ($modelMatch.Success) { $modelMatch.Groups[1].Value.Trim() } else { '' }
$expectedModel = '/root/.cache/huggingface/hub/models--jinaai--jina-reranker-v2-base-multilingual'
$modelOk = ($modelPath -eq $expectedModel)
Assert-Check 'SKILL_RERANKER_MODEL = container path' $modelOk $(if ($modelPath) { "current: $modelPath" } else { 'not forwarded' })
$winPathBad = $modelPath -like 'C:/*'
Assert-Check 'model path is not Windows path' (-not $winPathBad) $(if ($winPathBad) { 'WARNING: points to Windows path, load will fail' } else { 'OK' })

# ---------- 3. container running ----------
Write-Host '[3/7] container status' -ForegroundColor Yellow
$psOut = docker ps --filter name=$Container --format '{{.Status}}' 2>&1
$running = [bool]($psOut -match 'Up')
Assert-Check "container running ($Container)" $running "status: $($psOut -join ' ')"
if (-not $SkipHealth) {
    # avoid matching "unhealthy" substring: require explicit "(healthy)"
    $healthy = [bool]($psOut -match '\(healthy\)')
    Assert-Check 'container healthy' $healthy "status: $($psOut -join ' ')"
}

# ---------- 4. container env ----------
Write-Host '[4/7] container OMP env' -ForegroundColor Yellow
if ($running) {
    $ompIn = (docker exec $Container sh -c 'echo $OMP_NUM_THREADS' 2>&1 | Out-String).Trim()
    $mklIn = (docker exec $Container sh -c 'echo $MKL_NUM_THREADS' 2>&1 | Out-String).Trim()
    Assert-Check 'container OMP_NUM_THREADS=4' ($ompIn -eq '4') "current: $ompIn"
    Assert-Check 'container MKL_NUM_THREADS=4' ($mklIn -eq '4') "current: $mklIn"
} else {
    Assert-Check 'container OMP_NUM_THREADS=4' $false 'container not running'
}

# ---------- 5. hot-reload code in container ----------
Write-Host '[5/7] hot-reload code' -ForegroundColor Yellow
if ($running) {
    $hotCount = docker exec $Container grep -c '_hot_reload' /app/agent/skills_mgmt/reranker.py 2>&1
    $hotOk = ($hotCount -match '^\d+$') -and ([int]$hotCount -gt 0)
    Assert-Check 'reranker.py has _hot_reload' $hotOk "match count: $hotCount"
} else {
    Assert-Check 'reranker.py has _hot_reload' $false 'container not running'
}

# ---------- 6. ONNX model in container ----------
Write-Host '[6/7] ONNX model' -ForegroundColor Yellow
if ($running) {
    $onnxFound = docker exec $Container sh -c 'ls /root/.cache/huggingface/hub/models--jinaai--jina-reranker-v2-base-multilingual/onnx/model_quantized.onnx 2>/dev/null' 2>&1
    $onnxOk = $onnxFound -match 'model_quantized.onnx'
    Assert-Check 'model_quantized.onnx exists' $onnxOk $(if ($onnxOk) { 'OK' } else { 'model file missing (hot-reload unavailable)' })
    $variantCount = docker exec $Container sh -c 'ls /root/.cache/huggingface/hub/models--jinaai--jina-reranker-v2-base-multilingual/onnx/*.onnx 2>/dev/null | wc -l' 2>&1
    $vc = ($variantCount | Out-String).Trim()
    $vcOk = ($vc -match '^\d+$') -and ([int]$vc -ge 2)
    Assert-Check 'ONNX variants >= 2 (switchable)' $vcOk "variants: $vc"
} else {
    Assert-Check 'model_quantized.onnx exists' $false 'container not running'
}

# ---------- 7. OMP actually effective (torch threads) ----------
Write-Host '[7/7] OMP effective (torch threads)' -ForegroundColor Yellow
if ($running) {
    # torch may print pynvml FutureWarning to stderr. Under EAP=Stop, PS 5.1 turns external
    # command stderr into NativeCommandError; use try/catch + robust numeric-line extraction.
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $raw = docker exec $Container python -c 'import torch; print(torch.get_num_threads())' 2>&1
    } catch {
        $raw = $null
    }
    $ErrorActionPreference = $prevEap
    $tVal = @($raw | ForEach-Object { "$_" } | Where-Object { $_ -match '^\s*\d+\s*$' } | Select-Object -Last 1)
    if ($tVal.Count -gt 0) { $tVal = $tVal[0].Trim() } else { $tVal = '' }
    Assert-Check 'torch.get_num_threads() == 4' ($tVal -eq '4') "torch threads: $tVal"
} else {
    Assert-Check 'torch.get_num_threads() == 4' $false 'container not running'
}

# ---------- summary ----------
Write-Host ''
Write-Host '=== Summary ===' -ForegroundColor Cyan
Write-Host "  PASS: $pass  FAIL: $fail" -ForegroundColor $(if ($fail -eq 0) { 'Green' } else { 'Red' })
if ($fail -eq 0) {
    Write-Host '  Result: ALL PASS - config valid' -ForegroundColor Green
    Write-Host '  Covered: .env / compose forwarding / container state / env / hot-reload code / ONNX model / OMP effective' -ForegroundColor DarkGray
    exit 0
} else {
    Write-Host '  Result: FAILURES - check docs/CONFIG_ENV_REFERENCE.md' -ForegroundColor Red
    exit 1
}
