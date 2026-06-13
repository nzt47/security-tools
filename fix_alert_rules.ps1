Write-Host "`n=== Alert Rules Fix Script ===" -ForegroundColor Cyan

# Step 1: Recover Docker
Write-Host "`nStep 1: Recovering Docker..." -ForegroundColor Yellow
.\recover_docker.ps1

# Step 2: Wait for Docker to fully start
Write-Host "`nWaiting 30 seconds..." -ForegroundColor Cyan
Start-Sleep -Seconds 30

# Step 3: Restart Prometheus
Write-Host "`nStep 2: Restarting Prometheus..." -ForegroundColor Yellow
try {
    docker-compose -f docker-compose.monitoring.yml restart prometheus 2>$null
    Write-Host "   Restart: SUCCESS" -ForegroundColor Green
} catch {
    Write-Host "   Restart: FAILED" -ForegroundColor Red
    Write-Host "   Trying docker restart..." -ForegroundColor Yellow
    docker restart yunshu-prometheus
}

# Step 4: Wait for startup
Write-Host "`nWaiting 15 seconds for Prometheus to start..." -ForegroundColor Cyan
Start-Sleep -Seconds 15

# Step 5: Verify
Write-Host "`nStep 3: Verifying alert rules..." -ForegroundColor Yellow
try {
    $rules = curl.exe -s "http://localhost:9090/api/v1/rules" | ConvertFrom-Json
    if ($rules.data.groups) {
        $count = 0
        foreach ($g in $rules.data.groups) { $count += $g.rules.Count }
        
        Write-Host "   Rules loaded: $count" -ForegroundColor Green
        
        if ($count -ge 13) {
            Write-Host "`nSUCCESS: All alert rules loaded!" -ForegroundColor Green
        } else {
            Write-Host "`nPARTIAL: Expected 13, got $count" -ForegroundColor Yellow
        }
    } else {
        Write-Host "`nFAILED: No rules loaded" -ForegroundColor Red
    }
} catch {
    Write-Host "`nFAILED: Cannot verify" -ForegroundColor Red
}

Write-Host "`nDone!" -ForegroundColor Cyan
