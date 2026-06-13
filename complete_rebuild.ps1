# Complete Rebuild Script for Yunshu Monitoring Stack

Write-Host "`n╔══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║     Yunshu Monitoring Stack Complete Rebuild             ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan

# Step 1: Stop and remove containers
Write-Host "`n[Step 1/5] Stopping and removing containers..." -ForegroundColor Yellow
try {
    docker-compose -f docker-compose.monitoring.yml down 2>$null
    Write-Host "   Containers stopped and removed" -ForegroundColor Green
} catch {
    Write-Host "   Warning: Some containers may not exist" -ForegroundColor Yellow
}

# Step 2: Remove volumes (optional - uncomment if needed)
Write-Host "`n[Step 2/5] Cleaning volumes (optional)..." -ForegroundColor Yellow
Write-Host "   Skipping volume removal to preserve data" -ForegroundColor Cyan
# Uncomment the following lines to remove volumes:
# docker volume rm agent_yunshu-prometheus_data 2>$null
# docker volume rm agent_yunshu-grafana_data 2>$null
# Write-Host "   Volumes removed" -ForegroundColor Green

# Step 3: Remove orphan images
Write-Host "`n[Step 3/5] Cleaning up..." -ForegroundColor Yellow
docker system prune -f 2>$null | Out-Null
Write-Host "   System cleaned" -ForegroundColor Green

# Step 4: Pull latest images
Write-Host "`n[Step 4/5] Pulling images..." -ForegroundColor Yellow
try {
    docker-compose -f docker-compose.monitoring.yml pull 2>$null
    Write-Host "   Images pulled successfully" -ForegroundColor Green
} catch {
    Write-Host "   Warning: Image pull failed, using cached images" -ForegroundColor Yellow
}

# Step 5: Start services
Write-Host "`n[Step 5/5] Starting services..." -ForegroundColor Yellow
docker-compose -f docker-compose.monitoring.yml up -d

Write-Host "`nWaiting 20 seconds for services to start..." -ForegroundColor Cyan
Start-Sleep -Seconds 20

# Verification
Write-Host "`n╔══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║     Verification                                           ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan

Write-Host "`nChecking services..." -ForegroundColor Yellow

# Check Prometheus
try {
    $response = curl.exe -s http://localhost:9090/-/healthy 2>$null
    if ($response -match "Healthy") {
        Write-Host "Prometheus: HEALTHY" -ForegroundColor Green
    } else {
        Write-Host "Prometheus: UNHEALTHY" -ForegroundColor Red
    }
} catch {
    Write-Host "Prometheus: NOT ACCESSIBLE" -ForegroundColor Red
}

# Check Grafana
try {
    $response = curl.exe -s http://localhost:3000/api/health 2>$null
    if ($response -match "ok") {
        Write-Host "Grafana: HEALTHY" -ForegroundColor Green
    } else {
        Write-Host "Grafana: UNHEALTHY" -ForegroundColor Red
    }
} catch {
    Write-Host "Grafana: NOT ACCESSIBLE" -ForegroundColor Red
}

# Check alert rules
Write-Host "`nChecking alert rules..." -ForegroundColor Yellow
try {
    $rules = curl.exe -s "http://localhost:9090/api/v1/rules" 2>$null | ConvertFrom-Json
    if ($rules.data.groups) {
        $count = 0
        foreach ($g in $rules.data.groups) { $count += $g.rules.Count }
        Write-Host "Alert rules loaded: $count" -ForegroundColor Green
        
        if ($count -ge 13) {
            Write-Host "SUCCESS: All alert rules loaded!" -ForegroundColor Green
        } else {
            Write-Host "WARNING: Expected 13+, got $count" -ForegroundColor Yellow
        }
    } else {
        Write-Host "Alert rules: NOT LOADED" -ForegroundColor Red
    }
} catch {
    Write-Host "Alert rules: CHECK FAILED" -ForegroundColor Red
}

Write-Host "`n╔══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║     Rebuild Complete!                                      ║" -ForegroundColor Green
Write-Host "╚══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan

Write-Host "`nNext steps:" -ForegroundColor Cyan
Write-Host "1. Run .\simple_verify.ps1 for detailed verification" -ForegroundColor White
Write-Host "2. Access Prometheus: http://localhost:9090" -ForegroundColor White
Write-Host "3. Access Grafana: http://localhost:3000" -ForegroundColor White
Write-Host ""
