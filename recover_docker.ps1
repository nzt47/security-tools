# Docker Desktop Recovery Script

Write-Host "`n=== Docker Desktop Recovery ===" -ForegroundColor Cyan

Write-Host "`nStopping Docker processes..." -ForegroundColor Yellow
Stop-Process -Name "Docker Desktop" -Force -ErrorAction SilentlyContinue
Stop-Process -Name "com.docker.*" -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 30

Write-Host "Starting Docker Desktop..." -ForegroundColor Cyan
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
Write-Host "Waiting 60 seconds for startup..." -ForegroundColor Cyan
Start-Sleep -Seconds 60

Write-Host "`nVerifying Docker..." -ForegroundColor Yellow
try {
    $version = docker version 2>&1
    if ($version -match "Server") {
        Write-Host "Docker: RUNNING" -ForegroundColor Green
    } else {
        Write-Host "Docker: NOT RUNNING" -ForegroundColor Red
        Write-Host "Please restart your computer" -ForegroundColor Yellow
        exit 1
    }
} catch {
    Write-Host "Docker: ERROR" -ForegroundColor Red
    exit 1
}

Write-Host "`nChecking containers..." -ForegroundColor Yellow
docker ps --format "table {{.Names}}`t{{.Status}}"

Write-Host "`nVerifying services..." -ForegroundColor Yellow

# Prometheus
try {
    $response = curl.exe -s http://localhost:9090/-/healthy
    if ($response -match "Healthy") {
        Write-Host "Prometheus: HEALTHY" -ForegroundColor Green
    } else {
        Write-Host "Prometheus: UNHEALTHY" -ForegroundColor Yellow
    }
} catch {
    Write-Host "Prometheus: NOT ACCESSIBLE" -ForegroundColor Red
}

# Grafana
try {
    $response = curl.exe -s http://localhost:3000/api/health
    if ($response -match "ok") {
        Write-Host "Grafana: HEALTHY" -ForegroundColor Green
    } else {
        Write-Host "Grafana: UNHEALTHY" -ForegroundColor Yellow
    }
} catch {
    Write-Host "Grafana: NOT ACCESSIBLE" -ForegroundColor Red
}

Write-Host "`n=== Recovery Complete ===" -ForegroundColor Cyan
