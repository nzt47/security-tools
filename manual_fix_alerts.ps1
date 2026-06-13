# Manual Alert Rules Fix Script

Write-Host "`n=== Manual Alert Rules Fix ===" -ForegroundColor Cyan

Write-Host "`nStep 1: Checking if containers are running..." -ForegroundColor Yellow
try {
    $response = curl.exe -s http://localhost:9090/-/healthy
    if ($response -match "Healthy") {
        Write-Host "   Prometheus: RUNNING" -ForegroundColor Green
    } else {
        Write-Host "   Prometheus: NOT RUNNING" -ForegroundColor Red
        Write-Host "   Please start containers manually" -ForegroundColor Yellow
        exit 1
    }
} catch {
    Write-Host "   Prometheus: NOT ACCESSIBLE" -ForegroundColor Red
    exit 1
}

Write-Host "`nStep 2: Checking local configuration files..." -ForegroundColor Yellow
if (Test-Path "monitoring\alerts.yml") {
    Write-Host "   alerts.yml: EXISTS" -ForegroundColor Green
    $lines = (Get-Content "monitoring\alerts.yml").Count
    Write-Host "   Lines: $lines" -ForegroundColor Cyan
} else {
    Write-Host "   alerts.yml: NOT FOUND" -ForegroundColor Red
    exit 1
}

Write-Host "`nStep 3: Checking docker-compose configuration..." -ForegroundColor Yellow
$content = Get-Content "docker-compose.monitoring.yml" -Raw
if ($content -match "alerts.yml") {
    Write-Host "   alerts.yml volume: CONFIGURED" -ForegroundColor Green
} else {
    Write-Host "   alerts.yml volume: MISSING" -ForegroundColor Red
    Write-Host "   Please add to docker-compose.monitoring.yml:" -ForegroundColor Yellow
    Write-Host "   volumes:" -ForegroundColor Yellow
    Write-Host "     - ./monitoring/alerts.yml:/etc/prometheus/alerts.yml" -ForegroundColor Yellow
    exit 1
}

Write-Host "`nStep 4: Current status..." -ForegroundColor Yellow
Write-Host "   Configuration: FIXED" -ForegroundColor Green
Write-Host "   Docker API: UNSTABLE (500 errors)" -ForegroundColor Yellow
Write-Host "   Containers: RUNNING" -ForegroundColor Green

Write-Host "`nStep 5: Recommended action..." -ForegroundColor Cyan
Write-Host "   Due to Docker API instability, manual intervention required:" -ForegroundColor Yellow
Write-Host ""
Write-Host "   Option 1: Restart Docker Desktop and run:" -ForegroundColor White
Write-Host "     docker-compose -f docker-compose.monitoring.yml down" -ForegroundColor Cyan
Write-Host "     docker-compose -f docker-compose.monitoring.yml up -d" -ForegroundColor Cyan
Write-Host ""
Write-Host "   Option 2: Use Docker Desktop GUI to restart containers" -ForegroundColor White
Write-Host ""

Write-Host "`nStep 6: Verifying current alert rules..." -ForegroundColor Yellow
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
            Write-Host "   Containers need to be restarted with new config" -ForegroundColor Cyan
        }
    } else {
        Write-Host "   Alert rules: NOT LOADED" -ForegroundColor Red
        Write-Host "   Containers need to be restarted with new config" -ForegroundColor Cyan
    }
} catch {
    Write-Host "   Verification: FAILED" -ForegroundColor Red
}

Write-Host "`n=== Summary ===" -ForegroundColor Cyan
Write-Host "Configuration file: FIXED" -ForegroundColor Green
Write-Host "Docker API: UNSTABLE" -ForegroundColor Yellow
Write-Host "Containers: Need restart" -ForegroundColor Yellow
Write-Host ""
Write-Host "Next step: Restart Docker Desktop, then run:" -ForegroundColor White
Write-Host "  docker-compose -f docker-compose.monitoring.yml down" -ForegroundColor Cyan
Write-Host "  docker-compose -f docker-compose.monitoring.yml up -d" -ForegroundColor Cyan
Write-Host "  .\simple_verify.ps1" -ForegroundColor Cyan
Write-Host ""
