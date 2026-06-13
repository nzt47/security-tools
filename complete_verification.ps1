# Yunshu Monitoring Stack Complete Verification Script

param(
    [switch]$Detailed,
    [switch]$Fix
)

$ErrorActionPreference = "Continue"

# Configuration
$PrometheusUrl = "http://localhost:9090"
$GrafanaUrl = "http://localhost:3000"
$GrafanaUser = "admin"
$GrafanaPass = "admin123"

# Counters
$PassCount = 0
$WarnCount = 0
$FailCount = 0

# Results
$Results = @{
    Pass = @()
    Warn = @()
    Fail = @()
}

function Write-Result {
    param(
        [string]$Category,
        [string]$Check,
        [string]$Status,
        [string]$Message = ""
    )
    
    $color = switch ($Status) {
        "PASS" { "Green"; $script:PassCount++; $script:Results.Pass += "$Category - $Check" }
        "WARN" { "Yellow"; $script:WarnCount++; $script:Results.Warn += "$Category - $Check" }
        "FAIL" { "Red"; $script:FailCount++; $script:Results.Fail += "$Category - $Check" }
    }
    
    Write-Host "  [$Status] " -NoNewline -ForegroundColor $color
    Write-Host "$Check" -NoNewline
    if ($Message) {
        Write-Host " - $Message" -ForegroundColor Gray
    } else {
        Write-Host ""
    }
}

function Get-AuthHeader {
    $pair = "$GrafanaUser:$GrafanaPass"
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($pair)
    $base64 = [System.Convert]::ToBase64String($bytes)
    return "Basic $base64"
}

Clear-Host
Write-Host "`n╔══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║     Yunshu Monitoring Stack Verification                 ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# ============================================
# 1. Docker Status Check
# ============================================
Write-Host "[1/7] Docker Status" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor DarkGray

try {
    $dockerVersion = docker version --format "{{.Server.Version}}" 2>$null
    if ($dockerVersion) {
        Write-Result "Docker" "Docker Server Running" "PASS" "Version: $dockerVersion"
    } else {
        Write-Result "Docker" "Docker Server Running" "FAIL" "Docker Desktop not responding"
        Write-Host "`n  Suggestion: Run .\recover_docker.ps1 to restart Docker Desktop" -ForegroundColor Yellow
    }
} catch {
    Write-Result "Docker" "Docker Server Running" "FAIL" $_.Exception.Message
    Write-Host "`n  Suggestion: Restart Docker Desktop manually" -ForegroundColor Yellow
}

# Check containers
try {
    $containers = docker ps --format "{{.Names}}" 2>$null
    if ($containers -match "prometheus") {
        Write-Result "Docker" "Prometheus Container" "PASS"
    } else {
        Write-Result "Docker" "Prometheus Container" "FAIL" "Container not running"
        Write-Host "  Suggestion: docker-compose -f docker-compose.monitoring.yml up -d" -ForegroundColor Yellow
    }
    
    if ($containers -match "grafana") {
        Write-Result "Docker" "Grafana Container" "PASS"
    } else {
        Write-Result "Docker" "Grafana Container" "FAIL" "Container not running"
    }
} catch {
    Write-Result "Docker" "Container Status" "FAIL" "Cannot check containers"
}

# ============================================
# 2. Prometheus Health Check
# ============================================
Write-Host "`n[2/7] Prometheus Health" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor DarkGray

try {
    $response = curl.exe -s "$PrometheusUrl/-/healthy" 2>$null
    if ($response -match "Prometheus Server is Healthy") {
        Write-Result "Prometheus" "Health Endpoint" "PASS"
    } else {
        Write-Result "Prometheus" "Health Endpoint" "FAIL" "Not healthy"
    }
} catch {
    Write-Result "Prometheus" "Health Endpoint" "FAIL" "Not accessible"
}

try {
    $response = curl.exe -s "$PrometheusUrl/-/ready" 2>$null
    if ($response -match "Prometheus Server is Ready") {
        Write-Result "Prometheus" "Ready Endpoint" "PASS"
    } else {
        Write-Result "Prometheus" "Ready Endpoint" "WARN" "Not ready"
    }
} catch {
    Write-Result "Prometheus" "Ready Endpoint" "WARN" "Not accessible"
}

# ============================================
# 3. Prometheus Alert Rules Check
# ============================================
Write-Host "`n[3/7] Alert Rules Status" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor DarkGray

try {
    $rules = curl.exe -s "$PrometheusUrl/api/v1/rules" 2>$null | ConvertFrom-Json
    
    if ($rules.data.groups) {
        $totalRules = 0
        $groupCount = $rules.data.groups.Count
        
        foreach ($group in $rules.data.groups) {
            $totalRules += $group.rules.Count
        }
        
        Write-Result "Alert Rules" "Rule Groups Loaded" "PASS" "$groupCount groups"
        Write-Result "Alert Rules" "Total Rules" "PASS" "$totalRules rules"
        
        if ($totalRules -ge 19) {
            Write-Result "Alert Rules" "Expected Rules (19+)" "PASS"
        } else {
            Write-Result "Alert Rules" "Expected Rules (19+)" "WARN" "Only $totalRules rules loaded"
            Write-Host "  Suggestion: Check monitoring/alerts.yml configuration" -ForegroundColor Yellow
            Write-Host "  Suggestion: Verify prometheus.yml rule_files section" -ForegroundColor Yellow
        }
        
        if ($Detailed) {
            Write-Host "`n  Rule Groups:" -ForegroundColor Cyan
            foreach ($group in $rules.data.groups) {
                Write-Host "    - $($group.name): $($group.rules.Count) rules" -ForegroundColor Gray
            }
        }
    } else {
        Write-Result "Alert Rules" "Rule Groups" "FAIL" "No rules loaded"
        Write-Host "  Suggestion: Check monitoring/alerts.yml file exists" -ForegroundColor Yellow
        Write-Host "  Suggestion: Verify prometheus.yml contains 'rule_files: [alerts.yml]'" -ForegroundColor Yellow
        Write-Host "  Suggestion: Restart Prometheus container" -ForegroundColor Yellow
    }
} catch {
    Write-Result "Alert Rules" "Fetch Rules" "FAIL" $_.Exception.Message
}

# ============================================
# 4. Prometheus Targets Check
# ============================================
Write-Host "`n[4/7] Scrape Targets" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor DarkGray

try {
    $targets = curl.exe -s "$PrometheusUrl/api/v1/targets" 2>$null | ConvertFrom-Json
    
    if ($targets.data.activeTargets) {
        $activeCount = $targets.data.activeTargets.Count
        $upCount = ($targets.data.activeTargets | Where-Object { $_.health -eq "up" }).Count
        $downCount = $activeCount - $upCount
        
        Write-Result "Targets" "Active Targets" "PASS" "$activeCount targets"
        Write-Result "Targets" "Healthy Targets" "PASS" "$upCount up"
        
        if ($downCount -gt 0) {
            Write-Result "Targets" "All Targets Healthy" "WARN" "$downCount down"
        } else {
            Write-Result "Targets" "All Targets Healthy" "PASS"
        }
        
        if ($Detailed) {
            Write-Host "`n  Target Status:" -ForegroundColor Cyan
            foreach ($target in $targets.data.activeTargets) {
                $status = if ($target.health -eq "up") { "✓" } else { "✗" }
                Write-Host "    $status $($target.labels.job)" -ForegroundColor Gray
            }
        }
    } else {
        Write-Result "Targets" "Active Targets" "FAIL" "No targets found"
    }
} catch {
    Write-Result "Targets" "Fetch Targets" "FAIL" $_.Exception.Message
}

# ============================================
# 5. Grafana Health Check
# ============================================
Write-Host "`n[5/7] Grafana Health" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor DarkGray

try {
    $response = curl.exe -s "$GrafanaUrl/api/health" 2>$null
    if ($response -match "ok") {
        $info = $response | ConvertFrom-Json
        Write-Result "Grafana" "Health Endpoint" "PASS" "Version: $($info.version)"
    } else {
        Write-Result "Grafana" "Health Endpoint" "FAIL" "Not healthy"
    }
} catch {
    Write-Result "Grafana" "Health Endpoint" "FAIL" "Not accessible"
}

# ============================================
# 6. Grafana Datasources Check
# ============================================
Write-Host "`n[6/7] Grafana Datasources" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor DarkGray

try {
    $authHeader = Get-AuthHeader
    $datasources = curl.exe -s -H "Authorization: $authHeader" "$GrafanaUrl/api/datasources" 2>$null | ConvertFrom-Json
    
    if ($datasources) {
        $dsCount = $datasources.Count
        Write-Result "Datasources" "Total Datasources" "PASS" "$dsCount configured"
        
        $prometheusDs = $datasources | Where-Object { $_.type -eq "prometheus" }
        
        if ($prometheusDs) {
            Write-Result "Datasources" "Prometheus Datasource" "PASS" "$($prometheusDs.name)"
            
            # Test datasource connection
            try {
                $testResponse = curl.exe -s -H "Authorization: $authHeader" "$GrafanaUrl/api/datasources/uid/$($prometheusDs.uid)/health" 2>$null | ConvertFrom-Json
                if ($testResponse.status -eq "OK") {
                    Write-Result "Datasources" "Datasource Connection" "PASS"
                } else {
                    Write-Result "Datasources" "Datasource Connection" "WARN" "Connection test failed"
                }
            } catch {
                Write-Result "Datasources" "Datasource Connection" "WARN" "Cannot test connection"
            }
        } else {
            Write-Result "Datasources" "Prometheus Datasource" "FAIL" "Not configured"
            Write-Host "  Suggestion: Add Prometheus datasource in Grafana UI" -ForegroundColor Yellow
            Write-Host "  URL: http://prometheus:9090 (from Grafana container)" -ForegroundColor Yellow
        }
        
        if ($Detailed) {
            Write-Host "`n  Configured Datasources:" -ForegroundColor Cyan
            foreach ($ds in $datasources) {
                Write-Host "    - $($ds.name) ($($ds.type))" -ForegroundColor Gray
            }
        }
    } else {
        Write-Result "Datasources" "Datasources Configured" "FAIL" "No datasources found"
    }
} catch {
    Write-Result "Datasources" "Fetch Datasources" "FAIL" $_.Exception.Message
}

# ============================================
# 7. Grafana Dashboards Check
# ============================================
Write-Host "`n[7/7] Grafana Dashboards" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor DarkGray

try {
    $authHeader = Get-AuthHeader
    $dashboards = curl.exe -s -H "Authorization: $authHeader" "$GrafanaUrl/api/search?type=dash-db" 2>$null | ConvertFrom-Json
    
    if ($dashboards) {
        $dbCount = $dashboards.Count
        Write-Result "Dashboards" "Total Dashboards" "PASS" "$dbCount dashboards"
        
        $yunshuDb = $dashboards | Where-Object { $_.title -match "Yunshu|Alert" }
        
        if ($yunshuDb) {
            Write-Result "Dashboards" "Yunshu Dashboard" "PASS" "$($yunshuDb.title)"
            Write-Host "  URL: $GrafanaUrl/d/$($yunshuDb.uid)" -ForegroundColor Cyan
        } else {
            Write-Result "Dashboards" "Yunshu Dashboard" "WARN" "Not imported"
            Write-Host "  Suggestion: Import monitoring/grafana/dashboards/yunshu-alerts-monitor.json" -ForegroundColor Yellow
        }
        
        if ($Detailed) {
            Write-Host "`n  Available Dashboards:" -ForegroundColor Cyan
            foreach ($db in $dashboards) {
                Write-Host "    - $($db.title)" -ForegroundColor Gray
            }
        }
    } else {
        Write-Result "Dashboards" "Dashboards" "WARN" "No dashboards found"
    }
} catch {
    Write-Result "Dashboards" "Fetch Dashboards" "FAIL" $_.Exception.Message
}

# ============================================
# Summary
# ============================================
Write-Host "`n╔══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║     Verification Summary                                   ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan

Write-Host "`nResults:" -ForegroundColor White
Write-Host "  PASS:  $PassCount" -ForegroundColor Green
Write-Host "  WARN:  $WarnCount" -ForegroundColor Yellow
Write-Host "  FAIL:  $FailCount" -ForegroundColor Red

if ($FailCount -eq 0 -and $WarnCount -eq 0) {
    Write-Host "`n✓ All checks passed!" -ForegroundColor Green
} elseif ($FailCount -eq 0) {
    Write-Host "`n⚠ $WarnCount warning(s) detected" -ForegroundColor Yellow
} else {
    Write-Host "`n✗ $FailCount failure(s) detected" -ForegroundColor Red
    Write-Host "`nRecommended actions:" -ForegroundColor Yellow
    Write-Host "  1. Review failed checks above" -ForegroundColor White
    Write-Host "  2. Run .\recover_docker.ps1 if Docker issues" -ForegroundColor White
    Write-Host "  3. Check configuration files" -ForegroundColor White
    Write-Host "  4. Restart services if needed" -ForegroundColor White
}

Write-Host ""

# Export results if requested
if ($Detailed) {
    $Results | ConvertTo-Json | Out-File "verification_results.json"
    Write-Host "Results exported to: verification_results.json" -ForegroundColor Cyan
}
