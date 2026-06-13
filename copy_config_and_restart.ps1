# Manual Configuration Copy and Restart Script

Write-Host "`n=== Manual Configuration Copy ===" -ForegroundColor Cyan

Write-Host "`nStep 1: Checking local files..." -ForegroundColor Yellow
if (Test-Path "monitoring\alerts.yml") {
    Write-Host "   alerts.yml: EXISTS" -ForegroundColor Green
    $lines = (Get-Content "monitoring\alerts.yml").Count
    Write-Host "   Lines: $lines" -ForegroundColor Cyan
} else {
    Write-Host "   alerts.yml: NOT FOUND" -ForegroundColor Red
    exit 1
}

if (Test-Path "monitoring\prometheus.yml") {
    Write-Host "   prometheus.yml: EXISTS" -ForegroundColor Green
} else {
    Write-Host "   prometheus.yml: NOT FOUND" -ForegroundColor Red
    exit 1
}

Write-Host "`nStep 2: Waiting for container to be ready..." -ForegroundColor Yellow
Write-Host "   Waiting 10 seconds..." -ForegroundColor Cyan
Start-Sleep -Seconds 10

Write-Host "`nStep 3: Copying configuration files..." -ForegroundColor Yellow

try {
    # Copy prometheus.yml
    Write-Host "   Copying prometheus.yml..." -ForegroundColor Cyan
    docker cp monitoring\prometheus.yml yunshu-prometheus:/etc/prometheus/prometheus.yml
    Write-Host "   prometheus.yml copied" -ForegroundColor Green
    
    # Copy alerts.yml
    Write-Host "   Copying alerts.yml..." -ForegroundColor Cyan
    docker cp monitoring\alerts.yml yunshu-prometheus:/etc/prometheus/alerts.yml
    Write-Host "   alerts.yml copied" -ForegroundColor Green
} catch {
    Write-Host "   Copy failed: $_" -ForegroundColor Red
    Write-Host "   Container may not be running" -ForegroundColor Yellow
    exit 1
}

Write-Host "`nStep 4: Restarting Prometheus..." -ForegroundColor Yellow
try {
    docker restart yunshu-prometheus
    Write-Host "   Prometheus restarted" -ForegroundColor Green
} catch {
    Write-Host "   Restart failed" -ForegroundColor Red
    exit 1
}

Write-Host "`nStep 5: Waiting for startup..." -ForegroundColor Yellow
Start-Sleep -Seconds 15

Write-Host "`nStep 6: Verifying alert rules..." -ForegroundColor Yellow
try {
    $rules = curl.exe -s "http://localhost:9090/api/v1/rules" 2>$null | ConvertFrom-Json
    if ($rules.data.groups) {
        $count = 0
        foreach ($g in $rules.data.groups) { $count += $g.rules.Count }
        Write-Host "   Alert rules loaded: $count" -ForegroundColor Green
        
        if ($count -ge 13) {
            Write-Host "`n   SUCCESS: All alert rules loaded!" -ForegroundColor Green
        } else {
            Write-Host "`n   WARNING: Expected 13+, got $count" -ForegroundColor Yellow
        }
    } else {
        Write-Host "   Alert rules: NOT LOADED" -ForegroundColor Red
    }
} catch {
    Write-Host "   Verification failed" -ForegroundColor Red
}

Write-Host "`nDone!" -ForegroundColor Cyan
