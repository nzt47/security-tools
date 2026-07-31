<#
.SYNOPSIS
    Prometheus rules hot-reload automation (config validation + reload)

.DESCRIPTION
    Two-phase pipeline:
      1. Config validation (gate): promtool check config + check rules; abort on any failure
      2. Reload execution: HTTP POST /-/reload (preferred) or docker exec SIGHUP (fallback)

    Design:
      [Invariance] Never auto-edit prometheus.yml; only warn on missing rule_files refs
                    unless -FixRuleRefs explicitly authorizes the fix
      [Adaptability] Reload method selectable (auto/http/docker) for bare-metal & container
      [Simplicity] Validation failure aborts before reload; structured step log

.PARAMETER ConfigDir
    Prometheus config dir (contains prometheus.yml + rule files). Default: monitoring/prometheus

.PARAMETER ReloadMethod
    auto (default, HTTP then docker) | http | docker

.PARAMETER PrometheusUrl
    Prometheus HTTP endpoint for HTTP reload. Default: http://localhost:9090

.PARAMETER ContainerName
    Prometheus container name for docker reload. Default: Yunshu-prometheus

.PARAMETER DryRun
    Validate only, skip reload

.PARAMETER FixRuleRefs
    Authorize auto-fix of missing rule_files references in prometheus.yml (default: warn only)

.EXAMPLE
    .\scripts\reload_prometheus_rules.ps1
    # validate + auto reload
.EXAMPLE
    .\scripts\reload_prometheus_rules.ps1 -DryRun
    # validate only
.EXAMPLE
    .\scripts\reload_prometheus_rules.ps1 -FixRuleRefs
    # validate + fix rule_files refs + reload
#>
[CmdletBinding()]
param(
    [string]$ConfigDir      = "",
    [ValidateSet("auto","http","docker")]
    [string]$ReloadMethod   = "auto",
    [string]$PrometheusUrl  = "http://localhost:9090",
    [string]$ContainerName  = "Yunshu-prometheus",
    [switch]$DryRun,
    [switch]$FixRuleRefs
)

$ErrorActionPreference = "Stop"

# Resolve default ConfigDir from script location ($PSScriptRoot not available in param defaults)
if (-not $ConfigDir) {
    $ConfigDir = Join-Path $PSScriptRoot "..\monitoring\prometheus"
}

# --- logging helpers ---
function Write-Info    { param([string]$m) Write-Host "[INFO]  $m" -ForegroundColor Cyan }
function Write-Ok      { param([string]$m) Write-Host "[OK]    $m" -ForegroundColor Green }
function Write-WarnMsg { param([string]$m) Write-Host "[WARN]  $m" -ForegroundColor Yellow }
function Write-Err     { param([string]$m) Write-Host "[ERROR] $m" -ForegroundColor Red }

$script:StepResults = [System.Collections.ArrayList]::new()
function Record-Step { param([string]$name,[bool]$ok,[string]$detail="")
    $script:StepResults.Add([pscustomobject]@{Step=$name;Ok=$ok;Detail=$detail}) | Out-Null
}

# --- 1. prerequisites ---
function Test-Prerequisites {
    Write-Info "Prerequisites: Docker + config dir"
    $dockerOk = $false
    try { docker info *> $null; if ($LASTEXITCODE -eq 0) { $dockerOk = $true } } catch {}
    if (-not $dockerOk) {
        Write-Err "Docker daemon unavailable; cannot run promtool"
        Record-Step "Docker available" $false "daemon not running"
        return $false
    }
    Write-Ok "Docker daemon available"
    Record-Step "Docker available" $true

    $absConfigDir = (Resolve-Path $ConfigDir -ErrorAction SilentlyContinue).Path
    $promYml = Join-Path $absConfigDir "prometheus.yml"
    if (-not $absConfigDir -or -not (Test-Path $promYml)) {
        Write-Err "Config dir invalid or missing prometheus.yml: $ConfigDir"
        Record-Step "Config dir" $false "no prometheus.yml"
        return $false
    }
    Write-Ok "Config dir: $absConfigDir"
    Record-Step "Config dir" $true $absConfigDir
    return $true
}

# --- 2. config validation (promtool) ---
function Invoke-ConfigValidation {
    param([string]$AbsConfigDir)
    Write-Info "Phase 1: config validation (promtool)"
    $mountSrc = $AbsConfigDir -replace '\\','/'
    $allOk = $true

    # 2.1 main config
    Write-Info "  promtool check config prometheus.yml"
    $output = docker run --rm --entrypoint promtool `
        -v "${mountSrc}:/etc/prometheus:ro" `
        prom/prometheus:latest check config /etc/prometheus/prometheus.yml 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Err "  Main config validation FAILED"
        $output | ForEach-Object { Write-Host "    $_" -ForegroundColor Red }
        Record-Step "Main config validation" $false "promtool check config failed"
        return $false
    }
    Write-Ok "  Main config syntax OK"
    $output | ForEach-Object { if($_ -match "SUCCESS"){ Write-Host "    $_" -ForegroundColor DarkGray } }
    Record-Step "Main config validation" $true

    # 2.2 all rule files (including rules/ subdir)
    $ruleFiles = Get-ChildItem -Path $AbsConfigDir -Recurse -Filter "*.yml" |
        Where-Object { $_.Name -ne "prometheus.yml" }
    foreach ($rf in $ruleFiles) {
        $relPath = $rf.FullName.Substring($AbsConfigDir.Length).TrimStart('\','/') -replace '\\','/'
        Write-Info "  promtool check rules $relPath"
        $output = docker run --rm --entrypoint promtool `
            -v "${mountSrc}:/etc/prometheus:ro" `
            prom/prometheus:latest check rules "/etc/prometheus/$relPath" 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Err "  Rule file FAILED: $relPath"
            $output | ForEach-Object { Write-Host "    $_" -ForegroundColor Red }
            Record-Step "Rules: $relPath" $false
            $allOk = $false
        } else {
            $ruleCount = ""
            if ($output | Select-String "(\d+) rules? found" | ForEach-Object { $ruleCount = $_.Matches.Groups[1].Value }) {}
            Write-Ok "  $relPath ($ruleCount rules)"
            Record-Step "Rules: $relPath" $true "$ruleCount rules"
        }
    }
    return $allOk
}

# --- 3. rule_files reference consistency ---
function Test-RuleFileReferences {
    param([string]$AbsConfigDir, [bool]$AllowFix)
    Write-Info "Phase 1.5: rule_files reference consistency"

    $promYmlPath = Join-Path $AbsConfigDir "prometheus.yml"
    $content = Get-Content $promYmlPath -Raw

    # Extract rule_files block via regex (single-quoted string, '' => literal ')
    $referenced = [System.Collections.Generic.HashSet[string]]::new()
    if ($content -match '(?ms)^rule_files:\s*\n((?:\s*-\s*.+\s*\n?)+)') {
        $ruleBlock = $Matches[1]
        foreach ($line in ($ruleBlock -split "`n")) {
            if ($line -match '^\s*-\s*[''"]?(.+?)[''"]?\s*$') {
                [void]$referenced.Add($Matches[1])
            }
        }
    }

    # All rule files on disk (relative POSIX path)
    $onDisk = Get-ChildItem -Path $AbsConfigDir -Recurse -Filter "*.yml" |
        Where-Object { $_.Name -ne "prometheus.yml" } |
        ForEach-Object { $_.FullName.Substring($AbsConfigDir.Length).TrimStart('\','/') -replace '\\','/' }

    $missing = $onDisk | Where-Object { $_ -notin $referenced }
    if ($missing.Count -eq 0) {
        Write-Ok "  All rule files referenced by prometheus.yml"
        Record-Step "rule_files refs" $true
        return $true
    }

    Write-WarnMsg "  Rule files NOT referenced in prometheus.yml (Prometheus will not load):"
    $missing | ForEach-Object { Write-Host "    - $_" -ForegroundColor Yellow }
    Record-Step "rule_files refs" $false "missing: $($missing -join ', ')"

    if ($AllowFix) {
        Write-WarnMsg "  -FixRuleRefs authorized; appending references..."
        $appendLines = @("")
        foreach ($m in $missing) { $appendLines += "  - '$m'" }
        Add-Content -Path $promYmlPath -Value $appendLines -Encoding UTF8
        Write-Ok "  Appended $($missing.Count) rule_files refs (re-validating)"
        $mountSrc = $AbsConfigDir -replace '\\','/'
        $output = docker run --rm --entrypoint promtool `
            -v "${mountSrc}:/etc/prometheus:ro" `
            prom/prometheus:latest check config /etc/prometheus/prometheus.yml 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "  Fixed config validation OK"
            return $true
        } else {
            Write-Err "  Fixed config validation FAILED; inspect prometheus.yml manually"
            return $false
        }
    } else {
        Write-WarnMsg "  Tip: use -FixRuleRefs to auto-append, or edit prometheus.yml manually"
        # Missing refs do not block reload (already-referenced rules still load); warn only
        return $true
    }
}

# --- 4. reload execution ---
function Invoke-Reload {
    Write-Info "Phase 2: reload execution (method=$ReloadMethod)"

    if ($DryRun) {
        Write-WarnMsg "  -DryRun set; skipping reload"
        Record-Step "reload execution" $true "DryRun skipped"
        return $true
    }

    $reloadOk = $false
    $detail = ""

    # 4.1 HTTP POST /-/reload
    if ($ReloadMethod -in @("auto","http")) {
        Write-Info "  Trying HTTP POST $PrometheusUrl/-/reload"
        try {
            $resp = Invoke-WebRequest -Uri "$PrometheusUrl/-/reload" -Method POST -TimeoutSec 10 -UseBasicParsing -ErrorAction Stop
            if ($resp.StatusCode -eq 200) {
                Write-Ok "  HTTP reload OK (200)"
                $reloadOk = $true
                $detail = "HTTP 200"
            }
        } catch {
            $msg = $_.Exception.Message
            Write-WarnMsg "  HTTP reload failed: $msg"
            if ($ReloadMethod -eq "http") {
                Record-Step "reload execution" $false "HTTP: $msg"
                return $false
            }
        }
    }

    # 4.2 docker exec kill -HUP 1
    if (-not $reloadOk -and $ReloadMethod -in @("auto","docker")) {
        Write-Info "  Trying docker exec $ContainerName kill -HUP 1"
        $containerState = docker inspect -f "{{.State.Running}}" $ContainerName 2>&1
        if ($LASTEXITCODE -ne 0 -or "$containerState".Trim() -ne "true") {
            Write-Err "  Container $ContainerName not found or not running"
            Record-Step "reload execution" $false "container down: $ContainerName"
            return $false
        }
        $output = docker exec $ContainerName kill -HUP 1 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "  docker SIGHUP reload OK"
            $reloadOk = $true
            $detail = "docker SIGHUP"
        } else {
            Write-Err "  docker SIGHUP failed: $output"
            Record-Step "reload execution" $false "SIGHUP: $output"
            return $false
        }
    }

    if (-not $reloadOk) {
        Write-Err "  All reload methods failed"
        Record-Step "reload execution" $false "no method available"
        return $false
    }

    # 4.3 verify reload (status/config reachable)
    Start-Sleep -Seconds 2
    try {
        $status = Invoke-RestMethod -Uri "$PrometheusUrl/api/v1/status/config" -TimeoutSec 5 -ErrorAction Stop
        if ($status.status -eq "success") {
            Write-Ok "  Reload verified: status/config reachable"
            Record-Step "reload verify" $true $detail
        }
    } catch {
        Write-WarnMsg "  Post-reload status query failed (non-fatal): $($_.Exception.Message)"
        Record-Step "reload verify" $true "$detail (status query failed)"
    }
    return $true
}

# --- main ---
Write-Host ""
Write-Host "===========================================================" -ForegroundColor DarkCyan
Write-Host "  Prometheus Rules Hot-Reload" -ForegroundColor Cyan
Write-Host "  ConfigDir: $ConfigDir" -ForegroundColor DarkGray
Write-Host "  Method: $ReloadMethod | DryRun: $DryRun | FixRuleRefs: $FixRuleRefs" -ForegroundColor DarkGray
Write-Host "===========================================================" -ForegroundColor DarkCyan
Write-Host ""

if (-not (Test-Prerequisites)) { exit 1 }

$absConfigDir = (Resolve-Path $ConfigDir).Path
if (-not (Invoke-ConfigValidation -AbsConfigDir $absConfigDir)) {
    Write-Err "Config validation FAILED; aborting reload (gate)"
    exit 2
}
if (-not (Test-RuleFileReferences -AbsConfigDir $absConfigDir -AllowFix $FixRuleRefs)) {
    Write-WarnMsg "rule_files ref check abnormal; continuing reload"
}
if (-not (Invoke-Reload)) {
    Write-Err "reload FAILED"
    exit 3
}

# summary
Write-Host ""
Write-Host "===========================================================" -ForegroundColor DarkCyan
Write-Host "  Summary" -ForegroundColor Cyan
Write-Host "===========================================================" -ForegroundColor DarkCyan
$script:StepResults | ForEach-Object {
    $icon = if ($_.Ok) { "OK" } else { "FAIL" }
    $color = if ($_.Ok) { "Green" } else { "Red" }
    Write-Host ("  [{0}] {1,-32} {2}" -f $icon, $_.Step, $_.Detail) -ForegroundColor $color
}
Write-Host ""
$failed = $script:StepResults | Where-Object { -not $_.Ok }
if ($failed.Count -eq 0) {
    Write-Ok "All steps succeeded"
    exit 0
} else {
    Write-WarnMsg "$($failed.Count) step(s) abnormal (non-fatal)"
    exit 0
}
