Write-Host "`n=== Prometheus Alert Rules Configuration ===" -ForegroundColor Cyan

$alertsFile = "monitoring\alerts.yml"
$promConfig = "monitoring\prometheus.yml"

Write-Host "`nChecking files..." -ForegroundColor Yellow

if (Test-Path $alertsFile) {
    Write-Host "alerts.yml: EXISTS" -ForegroundColor Green
} else {
    Write-Host "alerts.yml: NOT FOUND" -ForegroundColor Yellow
    Write-Host "Please create alerts.yml manually" -ForegroundColor Cyan
}

if (Test-Path $promConfig) {
    Write-Host "prometheus.yml: EXISTS" -ForegroundColor Green
} else {
    Write-Host "prometheus.yml: NOT FOUND" -ForegroundColor Red
    exit 1
}

Write-Host "`nChecking prometheus.yml configuration..." -ForegroundColor Yellow

$content = Get-Content $promConfig -Raw

if ($content -match "rule_files:") {
    Write-Host "rule_files section: EXISTS" -ForegroundColor Green
    
    if ($content -match "alerts.yml") {
        Write-Host "alerts.yml referenced: YES" -ForegroundColor Green
    } else {
        Write-Host "alerts.yml referenced: NO" -ForegroundColor Yellow
    }
} else {
    Write-Host "rule_files section: MISSING" -ForegroundColor Red
    Write-Host "Please add manually:" -ForegroundColor Cyan
    Write-Host "rule_files:" -ForegroundColor White
    Write-Host "  - alerts.yml" -ForegroundColor White
}

Write-Host "`nRestarting Prometheus..." -ForegroundColor Yellow
docker-compose -f docker-compose.monitoring.yml restart prometheus
Start-Sleep -Seconds 10

Write-Host "`nVerifying rules..." -ForegroundColor Yellow

try {
    $rules = curl.exe -s http://localhost:9090/api/v1/rules | ConvertFrom-Json
    
    if ($rules.data.groups) {
        $count = 0
        foreach ($g in $rules.data.groups) {
            $count += $g.rules.Count
        }
        Write-Host "Alert rules loaded: $count" -ForegroundColor Green
    } else {
        Write-Host "Alert rules: NOT LOADED" -ForegroundColor Red
    }
} catch {
    Write-Host "Failed to check rules" -ForegroundColor Red
}

Write-Host "`nOpening Prometheus rules page..." -ForegroundColor Cyan
Start-Process "http://localhost:9090/rules"

Write-Host "`nDone!" -ForegroundColor Green
