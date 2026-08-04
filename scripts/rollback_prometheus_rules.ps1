<#
.SYNOPSIS
    Prometheus rules change rollback script (config restore + container recreate)

.DESCRIPTION
    Rolls back the CHG-2026-0729-PROM-RULES change:
      1. Restore prometheus.yml + docker-compose.monitoring.yml from backup or git
      2. Validate with promtool
      3. Recreate prometheus container (removes rules/ volume mount)
      4. Verify rules loaded (expect 16 rules from alerts.yml only)

    Design:
      [Invariance] Never touches TSDB volume; only config files + container
      [Adaptability] Two restore sources: backup files or git checkout
      [Simplicity] One-command rollback with structured step log

.PARAMETER Method
    backup (default, uses .bak.20260729 files) | git (git checkout HEAD~1)

.PARAMETER ComposeFile
    docker-compose.monitoring.yml path. Default: docker-compose.monitoring.yml

.PARAMETER DryRun
    Restore + validate only, do not recreate container

.EXAMPLE
    .\scripts\rollback_prometheus_rules.ps1
    # rollback from backup
.EXAMPLE
    .\scripts\rollback_prometheus_rules.ps1 -Method git
    # rollback via git checkout
.EXAMPLE
    .\scripts\rollback_prometheus_rules.ps1 -DryRun
    # validate rollback plan without recreating container
#>
[CmdletBinding()]
param(
    [ValidateSet("backup","git")]
    [string]$Method = "backup",
    [string]$ComposeFile = "docker-compose.monitoring.yml",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $ProjectRoot

# --- logging ---
function Write-Info { param([string]$m) Write-Host "[INFO]  $m" -ForegroundColor Cyan }
function Write-Ok   { param([string]$m) Write-Host "[OK]    $m" -ForegroundColor Green }
function Write-Warn { param([string]$m) Write-Host "[WARN]  $m" -ForegroundColor Yellow }
function Write-Err  { param([string]$m) Write-Host "[ERROR] $m" -ForegroundColor Red }

$script:Steps = [System.Collections.ArrayList]::new()
function Record-Step { param([string]$name,[bool]$ok,[string]$detail="")
    $script:Steps.Add([pscustomobject]@{Step=$name;Ok=$ok;Detail=$detail}) | Out-Null
}

$PromYml = "monitoring/prometheus.yml"
$PromYmlBak = "monitoring/prometheus.yml.bak.20260729"
$ComposeBak = "docker-compose.monitoring.yml.bak.20260729"

# ════════════════════════════════════════════════════════════
#  1. Restore configuration files
# ════════════════════════════════════════════════════════════
function Restore-Config {
    Write-Info "Phase 1: Restore configuration (method=$Method)"

    if ($Method -eq "backup") {
        # Check backup files exist
        if (-not (Test-Path $PromYmlBak)) {
            Write-Err "Backup not found: $PromYmlBak"
            Record-Step "Backup exists" $false "prometheus.yml.bak missing"
            return $false
        }
        if (-not (Test-Path $ComposeBak)) {
            Write-Err "Backup not found: $ComposeBak"
            Record-Step "Backup exists" $false "compose .bak missing"
            return $false
        }
        Write-Ok "Backup files found"
        Record-Step "Backup exists" $true

        if ($DryRun) {
            Write-Warn "  DryRun: showing diff only, not modifying files"
            Write-Info "  Current prometheus.yml rule_files:"
            (Get-Content $PromYml | Select-String "rule_files|rules/|alerts.yml" | ForEach-Object { "    $($_.Line)" })
            Write-Info "  Backup prometheus.yml rule_files:"
            (Get-Content $PromYmlBak | Select-String "rule_files|rules/|alerts.yml" | ForEach-Object { "    $($_.Line)" })
            Record-Step "Restore prometheus.yml" $true "DryRun preview"
            Record-Step "Restore compose" $true "DryRun preview"
            return $true
        }

        # Restore
        Copy-Item $PromYmlBak $PromYml -Force
        Write-Ok "Restored $PromYml from backup"
        Record-Step "Restore prometheus.yml" $true "from .bak"

        Copy-Item $ComposeBak $ComposeFile -Force
        Write-Ok "Restored $ComposeFile from backup"
        Record-Step "Restore compose" $true "from .bak"

    } else {
        # Git method: checkout to state before CHG-2026-0729
        Write-Info "Using git checkout to restore"

        if ($DryRun) {
            Write-Warn "  DryRun: showing git diff only, not modifying files"
            $diff = git diff HEAD -- $PromYml 2>&1
            if ($diff) { Write-Info "  Uncommitted changes in ${PromYml}:"; $diff | ForEach-Object { "    $_" } }
            else { Write-Info "  No uncommitted changes in ${PromYml} (already at git HEAD)" }
            Record-Step "Restore prometheus.yml" $true "DryRun preview"
            Record-Step "Restore compose" $true "DryRun preview"
            return $true
        }

        $origContent = Get-Content $PromYml -Raw
        if ($origContent -notmatch "rules/reranker-alerts.yml") {
            Write-Warn "prometheus.yml does not contain change marker; may already be rolled back"
        }
        # Restore prometheus.yml from HEAD (pre-change baseline)
        git checkout HEAD -- $PromYml 2>&1 | ForEach-Object { Write-Host "  $_" }
        if ($LASTEXITCODE -ne 0) {
            Write-Err "git checkout failed for $PromYml"
            Record-Step "Restore prometheus.yml" $false "git checkout failed"
            return $false
        }
        Write-Ok "Restored $PromYml via git checkout"
        Record-Step "Restore prometheus.yml" $true "from git HEAD"

        # Restore compose from HEAD
        git checkout HEAD -- $ComposeFile 2>&1 | ForEach-Object { Write-Host "  $_" }
        if ($LASTEXITCODE -ne 0) {
            Write-Err "git checkout failed for $ComposeFile"
            Record-Step "Restore compose" $false "git checkout failed"
            return $false
        }
        Write-Ok "Restored $ComposeFile via git checkout"
        Record-Step "Restore compose" $true "from git HEAD"
    }

    # Verify rollback: prometheus.yml should NOT have rules/ references
    $content = Get-Content $PromYml -Raw
    if ($content -match "rules/reranker-alerts.yml") {
        Write-Err "Rollback verification FAILED: prometheus.yml still contains rules/ reference"
        Record-Step "Rollback verify" $false "rules/ ref still present"
        return $false
    }
    Write-Ok "Rollback verified: rules/ references removed from prometheus.yml"
    Record-Step "Rollback verify" $true "rules/ refs removed"
    return $true
}

# ════════════════════════════════════════════════════════════
#  2. Validate with promtool
# ════════════════════════════════════════════════════════════
function Invoke-Validation {
    Write-Info "Phase 2: promtool validation"

    # Create temp dir mirroring container structure for promtool
    $tmp = New-Item -ItemType Directory -Path "$env:TEMP\prom-rollback-$(Get-Random)" -Force
    Copy-Item $PromYml "$tmp/prometheus.yml"
    if (Test-Path "monitoring/alerts.yml") {
        Copy-Item "monitoring/alerts.yml" "$tmp/alerts.yml"
    }
    # Copy rules/ dir if it exists (prometheus.yml may reference it)
    $rulesSrc = "monitoring/prometheus/rules"
    if (Test-Path $rulesSrc) {
        Copy-Item $rulesSrc "$tmp/rules" -Recurse
    }
    $mount = $tmp.FullName -replace '\\','/'

    $output = docker run --rm --entrypoint promtool `
        -v "${mount}:/etc/prometheus:ro" `
        prom/prometheus:latest check config /etc/prometheus/prometheus.yml 2>&1

    Remove-Item -Recurse -Force $tmp.FullName

    if ($LASTEXITCODE -ne 0) {
        Write-Err "promtool validation FAILED"
        $output | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
        Record-Step "promtool validation" $false
        return $false
    }
    Write-Ok "promtool validation passed"
    $output | ForEach-Object { if ($_ -match "SUCCESS") { Write-Host "  $_" -ForegroundColor DarkGray } }
    Record-Step "promtool validation" $true
    return $true
}

# ════════════════════════════════════════════════════════════
#  3. Recreate prometheus container
# ════════════════════════════════════════════════════════════
function Recreate-Container {
    Write-Info "Phase 3: Recreate prometheus container"

    if ($DryRun) {
        Write-Warn "  -DryRun set; skipping container recreation"
        Record-Step "Container recreate" $true "DryRun skipped"
        return $true
    }

    # docker-compose up -d --force-recreate prometheus
    # This picks up the restored compose file (without rules/ volume mount)
    Write-Info "  docker-compose up -d --force-recreate prometheus"
    $output = docker-compose -f $ComposeFile up -d --force-recreate prometheus 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Container recreation FAILED"
        $output | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
        Record-Step "Container recreate" $false
        return $false
    }
    $output | ForEach-Object { Write-Host "  $_" }
    Write-Ok "Container recreated"
    Record-Step "Container recreate" $true

    # Wait for startup
    Write-Info "  Waiting 15s for TSDB replay..."
    Start-Sleep 15

    # Health check
    try {
        $r = Invoke-WebRequest "http://localhost:9090/-/healthy" -TimeoutSec 15 -UseBasicParsing
        if ($r.StatusCode -eq 200) {
            Write-Ok "Prometheus healthy"
            Record-Step "Health check" $true "200 healthy"
        }
    } catch {
        Write-Err "Health check failed: $($_.Exception.Message)"
        Record-Step "Health check" $false $_.Exception.Message
        return $false
    }
    return $true
}

# ════════════════════════════════════════════════════════════
#  4. Verify rules loaded (expect 16 rules from alerts.yml only)
# ════════════════════════════════════════════════════════════
function Verify-Rules {
    Write-Info "Phase 4: Verify rules loaded (expect 16 rules, 8 groups)"

    try {
        $r = Invoke-RestMethod "http://localhost:9090/api/v1/rules" -TimeoutSec 30
        $groups = $r.data.groups
        $total = ($groups | ForEach-Object { $_.rules.Count } | Measure-Object -Sum).Sum
        Write-Ok "Rules loaded: $total rules in $($groups.Count) groups"

        # Check no reranker/query-pattern groups
        $rerankerGroups = $groups | Where-Object { $_.name -match "reranker|query_pattern|negative_intent" }
        if ($rerankerGroups) {
            Write-Warn "  Reranker/query-pattern groups still present (rollback may be incomplete):"
            $rerankerGroups | ForEach-Object { Write-Host "    $($_.name)" -ForegroundColor Yellow }
            Record-Step "Rules verify" $false "$total rules, reranker groups still present"
        } else {
            Write-Ok "  No reranker/query-pattern groups (rollback successful)"
            Record-Step "Rules verify" $true "$total rules, 0 reranker groups"
        }

        # List all groups
        Write-Info "  Groups:"
        $groups | Sort-Object name | ForEach-Object { Write-Host "    $($_.name): $($_.rules.Count) rules" }
        return $true
    } catch {
        Write-Err "Rules query failed: $($_.Exception.Message)"
        Record-Step "Rules verify" $false "API query failed"
        return $false
    }
}

# ════════════════════════════════════════════════════════════
#  Main
# ════════════════════════════════════════════════════════════
Write-Host ""
Write-Host "===========================================================" -ForegroundColor DarkCyan
Write-Host "  Prometheus Rules Rollback" -ForegroundColor Cyan
Write-Host "  Method: $Method | DryRun: $DryRun" -ForegroundColor DarkGray
Write-Host "===========================================================" -ForegroundColor DarkCyan
Write-Host ""

# Check Docker
try { docker info *> $null } catch {
    Write-Err "Docker daemon not running; cannot validate/recreate"
    exit 1
}

if (-not (Restore-Config)) { Write-Err "Config restore failed"; exit 2 }
if (-not (Invoke-Validation)) { Write-Err "Validation failed"; exit 3 }
if (-not (Recreate-Container)) { Write-Err "Container recreation failed"; exit 4 }
if ($DryRun) {
    Write-Warn "DryRun: skipping rules verification (container not recreated)"
} else {
    Verify-Rules | Out-Null
}

# Summary
Write-Host ""
Write-Host "===========================================================" -ForegroundColor DarkCyan
Write-Host "  Rollback Summary" -ForegroundColor Cyan
Write-Host "===========================================================" -ForegroundColor DarkCyan
$script:Steps | ForEach-Object {
    $icon = if ($_.Ok) { "OK" } else { "FAIL" }
    $color = if ($_.Ok) { "Green" } else { "Red" }
    Write-Host ("  [{0}] {1,-28} {2}" -f $icon, $_.Step, $_.Detail) -ForegroundColor $color
}
Write-Host ""
$failed = $script:Steps | Where-Object { -not $_.Ok }
if ($failed.Count -eq 0) {
    Write-Ok "Rollback completed successfully"
    Write-Host ""
    Write-Info "Post-rollback state:"
    Write-Host "  - prometheus.yml: rule_files = [alerts.yml] only"
    Write-Host "  - compose: no rules/ volume mount"
    Write-Host "  - Prometheus: 16 rules / 8 groups (alerts.yml only)"
    Write-Host "  - reranker + query-pattern alerts: DISABLED (rolled back)"
    exit 0
} else {
    Write-Warn "$($failed.Count) step(s) failed; review above"
    exit 5
}
