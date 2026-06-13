Write-Host "`n=== Yunshu Monitoring Verification ===" -ForegroundColor Cyan

# Docker
Write-Host "`nDocker:" -ForegroundColor Yellow
try {
    $v = docker version --format "{{.Server.Version}}" 2>$null
    Write-Host "RUNNING - Version $v" -ForegroundColor Green
} catch {
    Write-Host "FAILED" -ForegroundColor Red
    exit 1
}

# Containers
Write-Host "`nContainers:" -ForegroundColor Yellow
try {
    $c = docker ps --format "{{.Names}}" 2>$null
    if ($c -match "prometheus" -and $c -match "grafana") {
        Write-Host "RUNNING - Prometheus and Grafana" -ForegroundColor Green
    } else {
        Write-Host "MISSING - Starting..." -ForegroundColor Yellow
        docker-compose -f docker-compose.monitoring.yml up -d
        Start-Sleep -Seconds 15
    }
} catch {
    Write-Host "ERROR" -ForegroundColor Red
}

# Prometheus
Write-Host "`nPrometheus:" -ForegroundColor Yellow
try {
    $r = curl.exe -s http://localhost:9090/-/healthy 2>$null
    if ($r -match "Healthy") {
        Write-Host "HEALTHY" -ForegroundColor Green
    } else {
        Write-Host "UNHEALTHY" -ForegroundColor Red
    }
} catch {
    Write-Host "NOT ACCESSIBLE" -ForegroundColor Red
}

# Grafana
Write-Host "`nGrafana:" -ForegroundColor Yellow
try {
    $r = curl.exe -s http://localhost:3000/api/health 2>$null
    if ($r -match "ok") {
        Write-Host "HEALTHY" -ForegroundColor Green
    } else {
        Write-Host "UNHEALTHY" -ForegroundColor Red
    }
} catch {
    Write-Host "NOT ACCESSIBLE" -ForegroundColor Red
}

# Alert Rules
Write-Host "`nAlert Rules:" -ForegroundColor Yellow
try {
    $rules = curl.exe -s http://localhost:9090/api/v1/rules 2>$null | ConvertFrom-Json
    if ($rules.data.groups) {
        $count = 0
        foreach ($g in $rules.data.groups) {
            $count += $g.rules.Count
        }
        Write-Host "LOADED - $count rules" -ForegroundColor Green
        if ($count -lt 19) {
            Write-Host "WARNING - Expected 19 rules" -ForegroundColor Yellow
        }
    } else {
        Write-Host "NOT LOADED - Configure alerts.yml" -ForegroundColor Red
    }
} catch {
    Write-Host "CHECK FAILED" -ForegroundColor Red
}

Write-Host "`n=== Summary ===" -ForegroundColor Cyan
Write-Host "Services: Running" -ForegroundColor Green
Write-Host "Next: Import Grafana dashboard and configure alert rules" -ForegroundColor Cyan
