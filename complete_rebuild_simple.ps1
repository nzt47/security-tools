Write-Host "`n=== Complete Rebuild Script ===" -ForegroundColor Cyan

Write-Host "`n[1/4] Stopping containers..." -ForegroundColor Yellow
docker-compose -f docker-compose.monitoring.yml down 2>$null

Write-Host "`n[2/4] Cleaning system..." -ForegroundColor Yellow
docker system prune -f 2>$null | Out-Null

Write-Host "`n[3/4] Starting services..." -ForegroundColor Yellow
docker-compose -f docker-compose.monitoring.yml up -d

Write-Host "`nWaiting 20 seconds..." -ForegroundColor Cyan
Start-Sleep -Seconds 20

Write-Host "`n[4/4] Verifying services..." -ForegroundColor Yellow

Write-Host "`nChecking Prometheus..." -ForegroundColor Cyan
try {
    $r = curl.exe -s http://localhost:9090/-/healthy
    if ($r -match "Healthy") { Write-Host "   Prometheus: HEALTHY" -ForegroundColor Green }
    else { Write-Host "   Prometheus: UNHEALTHY" -ForegroundColor Red }
} catch { Write-Host "   Prometheus: NOT ACCESSIBLE" -ForegroundColor Red }

Write-Host "`nChecking Grafana..." -ForegroundColor Cyan
try {
    $r = curl.exe -s http://localhost:3000/api/health
    if ($r -match "ok") { Write-Host "   Grafana: HEALTHY" -ForegroundColor Green }
    else { Write-Host "   Grafana: UNHEALTHY" -ForegroundColor Red }
} catch { Write-Host "   Grafana: NOT ACCESSIBLE" -ForegroundColor Red }

Write-Host "`nChecking alert rules..." -ForegroundColor Cyan
try {
    $rules = curl.exe -s "http://localhost:9090/api/v1/rules" 2>$null | ConvertFrom-Json
    if ($rules.data.groups) {
        $count = 0
        foreach ($g in $rules.data.groups) { $count += $g.rules.Count }
        Write-Host "   Rules loaded: $count" -ForegroundColor Green
        if ($count -ge 13) { Write-Host "   SUCCESS: All rules loaded!" -ForegroundColor Green }
        else { Write-Host "   WARNING: Expected 13+, got $count" -ForegroundColor Yellow }
    } else { Write-Host "   Rules: NOT LOADED" -ForegroundColor Red }
} catch { Write-Host "   Rules: CHECK FAILED" -ForegroundColor Red }

Write-Host "`n=== Rebuild Complete ===" -ForegroundColor Cyan
Write-Host "`nRun .\simple_verify.ps1 for detailed verification" -ForegroundColor White
