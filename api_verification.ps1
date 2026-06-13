# Yunshu Monitoring API Verification Script

Write-Host "`n╔══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║     Yunshu Monitoring API Verification                   ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan

$PassCount = 0
$FailCount = 0

function Test-Endpoint {
    param($Url, $Name, $ExpectedPattern)
    
    try {
        $response = curl.exe -s $Url 2>$null
        if ($response -match $ExpectedPattern) {
            Write-Host "[PASS] $Name" -ForegroundColor Green
            $script:PassCount++
            return $true
        } else {
            Write-Host "[FAIL] $Name - Response: $response" -ForegroundColor Red
            $script:FailCount++
            return $false
        }
    } catch {
        Write-Host "[FAIL] $Name - $_" -ForegroundColor Red
        $script:FailCount++
        return $false
    }
}

Write-Host "`n[1/5] Prometheus Health Checks" -ForegroundColor Cyan
Write-Host "────────────────────────────────────────────────────────────" -ForegroundColor DarkGray

Test-Endpoint "http://localhost:9090/-/healthy" "Prometheus Health" "Prometheus Server is Healthy"
Test-Endpoint "http://localhost:9090/-/ready" "Prometheus Ready" "Prometheus Server is Ready"

Write-Host "`n[2/5] Prometheus Alert Rules" -ForegroundColor Cyan
Write-Host "────────────────────────────────────────────────────────────" -ForegroundColor DarkGray

try {
    $rules = curl.exe -s "http://localhost:9090/api/v1/rules" 2>$null | ConvertFrom-Json
    if ($rules.data.groups) {
        $count = 0
        foreach ($g in $rules.data.groups) { $count += $g.rules.Count }
        Write-Host "[PASS] Alert Rules: $count rules loaded" -ForegroundColor Green
        $PassCount++
        
        if ($count -lt 19) {
            Write-Host "[WARN] Expected 19+ rules, got $count" -ForegroundColor Yellow
            Write-Host "  → Check monitoring/alerts.yml" -ForegroundColor Cyan
            Write-Host "  → Verify prometheus.yml rule_files section" -ForegroundColor Cyan
        }
    } else {
        Write-Host "[FAIL] No alert rules loaded" -ForegroundColor Red
        $FailCount++
        Write-Host "  → Create monitoring/alerts.yml" -ForegroundColor Cyan
        Write-Host "  → Add 'rule_files: [alerts.yml]' to prometheus.yml" -ForegroundColor Cyan
    }
} catch {
    Write-Host "[FAIL] Cannot fetch alert rules" -ForegroundColor Red
    $FailCount++
}

Write-Host "`n[3/5] Prometheus Targets" -ForegroundColor Cyan
Write-Host "────────────────────────────────────────────────────────────" -ForegroundColor DarkGray

try {
    $targets = curl.exe -s "http://localhost:9090/api/v1/targets" 2>$null | ConvertFrom-Json
    if ($targets.data.activeTargets) {
        $up = ($targets.data.activeTargets | Where-Object { $_.health -eq "up" }).Count
        $total = $targets.data.activeTargets.Count
        Write-Host "[PASS] Targets: $up/$total healthy" -ForegroundColor Green
        $PassCount++
    } else {
        Write-Host "[FAIL] No targets found" -ForegroundColor Red
        $FailCount++
    }
} catch {
    Write-Host "[FAIL] Cannot fetch targets" -ForegroundColor Red
    $FailCount++
}

Write-Host "`n[4/5] Grafana Health Checks" -ForegroundColor Cyan
Write-Host "────────────────────────────────────────────────────────────" -ForegroundColor DarkGray

Test-Endpoint "http://localhost:3000/api/health" "Grafana Health" "ok"

Write-Host "`n[5/5] Grafana Datasources" -ForegroundColor Cyan
Write-Host "────────────────────────────────────────────────────────────" -ForegroundColor DarkGray

try {
    $pair = "admin:admin123"
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($pair)
    $base64 = [System.Convert]::ToBase64String($bytes)
    $auth = "Basic $base64"
    
    $ds = curl.exe -s -H "Authorization: $auth" "http://localhost:3000/api/datasources" 2>$null | ConvertFrom-Json
    
    if ($ds) {
        Write-Host "[PASS] Datasources: $($ds.Count) configured" -ForegroundColor Green
        $PassCount++
        
        $prom = $ds | Where-Object { $_.type -eq "prometheus" }
        if ($prom) {
            Write-Host "[PASS] Prometheus datasource: $($prom.name)" -ForegroundColor Green
            $PassCount++
        } else {
            Write-Host "[WARN] No Prometheus datasource" -ForegroundColor Yellow
            $script:WarnCount = 1
            Write-Host "  Add datasource: http://prometheus:9090" -ForegroundColor Cyan
        }
    } else {
        Write-Host "[FAIL] No datasources configured" -ForegroundColor Red
        $FailCount++
    }
} catch {
    Write-Host "[FAIL] Cannot fetch datasources" -ForegroundColor Red
    $FailCount++
}

Write-Host "`n[6/5] Grafana Dashboards" -ForegroundColor Cyan
Write-Host "────────────────────────────────────────────────────────────" -ForegroundColor DarkGray

try {
    $pair = "admin:admin123"
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($pair)
    $base64 = [System.Convert]::ToBase64String($bytes)
    $auth = "Basic $base64"
    
    $dbs = curl.exe -s -H "Authorization: $auth" "http://localhost:3000/api/search?type=dash-db" 2>$null | ConvertFrom-Json
    
    if ($dbs) {
        Write-Host "[PASS] Dashboards: $($dbs.Count) available" -ForegroundColor Green
        $PassCount++
        
        $yunshu = $dbs | Where-Object { $_.title -match "Yunshu|Alert" }
        if ($yunshu) {
            Write-Host "[PASS] Yunshu dashboard imported" -ForegroundColor Green
            Write-Host "       URL: http://localhost:3000/d/$($yunshu.uid)" -ForegroundColor Cyan
            $PassCount++
        } else {
            Write-Host "[WARN] Yunshu dashboard not imported" -ForegroundColor Yellow
            Write-Host "  Import: monitoring/grafana/dashboards/yunshu-alerts-monitor.json" -ForegroundColor Cyan
        }
    } else {
        Write-Host "[WARN] No dashboards found" -ForegroundColor Yellow
    }
} catch {
    Write-Host "[FAIL] Cannot fetch dashboards" -ForegroundColor Red
    $FailCount++
}

Write-Host "`n╔══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║     Summary                                                ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan

Write-Host "`nTotal Checks: $($PassCount + $FailCount)" -ForegroundColor White
Write-Host "  PASS: $PassCount" -ForegroundColor Green
Write-Host "  FAIL: $FailCount" -ForegroundColor Red

if ($FailCount -eq 0) {
    Write-Host "`n✓ All checks passed!" -ForegroundColor Green
} else {
    Write-Host "`n✗ $FailCount check(s) failed" -ForegroundColor Red
    Write-Host "`nTroubleshooting:" -ForegroundColor Yellow
    Write-Host "  1. Check Docker containers: docker ps" -ForegroundColor White
    Write-Host "  2. View Prometheus logs: docker logs yunshu-prometheus" -ForegroundColor White
    Write-Host "  3. View Grafana logs: docker logs yunshu-grafana" -ForegroundColor White
    Write-Host "  4. Restart services: docker-compose -f docker-compose.monitoring.yml restart" -ForegroundColor White
}

Write-Host ""
