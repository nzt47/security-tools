Write-Host "`n=== Prometheus Alert Rules Check ===" -ForegroundColor Cyan

# Check Prometheus
Write-Host "`nChecking Prometheus..." -ForegroundColor Yellow
try {
    $response = curl.exe -s http://localhost:9090/-/healthy
    if ($response -match "Prometheus Server is Healthy") {
        Write-Host "Prometheus: HEALTHY" -ForegroundColor Green
    } else {
        Write-Host "Prometheus: NOT RESPONDING" -ForegroundColor Red
        exit 1
    }
} catch {
    Write-Host "ERROR: Cannot reach Prometheus" -ForegroundColor Red
    exit 1
}

# Check alert rules
Write-Host "`nFetching alert rules..." -ForegroundColor Yellow
try {
    $rules = curl.exe -s http://localhost:9090/api/v1/rules | ConvertFrom-Json
    
    if ($rules.data.groups) {
        $totalRules = 0
        foreach ($group in $rules.data.groups) {
            $ruleCount = $group.rules.Count
            $totalRules += $ruleCount
            Write-Host "Group: $($group.name) - $ruleCount rules" -ForegroundColor Cyan
        }
        
        Write-Host "`nTotal alert rules: $totalRules" -ForegroundColor Green
        
        if ($totalRules -ge 19) {
            Write-Host "SUCCESS: All 19+ rules loaded!" -ForegroundColor Green
        } else {
            Write-Host "WARNING: Expected 19 rules, found $totalRules" -ForegroundColor Yellow
        }
    } else {
        Write-Host "No alert rules found" -ForegroundColor Yellow
    }
} catch {
    Write-Host "ERROR: Failed to fetch rules" -ForegroundColor Red
}

Write-Host "`nOpening Prometheus rules page..." -ForegroundColor Cyan
Start-Process "http://localhost:9090/rules"

Write-Host "`nDone!" -ForegroundColor Green
