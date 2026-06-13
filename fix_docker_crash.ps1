# Docker Crash Recovery Script

$ErrorActionPreference = "Continue"

Write-Host "`n=== Docker Crash Recovery ===" -ForegroundColor Cyan

# Stop Docker
Write-Host "`nStopping Docker..." -ForegroundColor Yellow
Stop-Process -Name "Docker Desktop" -Force -ErrorAction SilentlyContinue
Stop-Process -Name "com.docker.*" -Force -ErrorAction SilentlyContinue
Stop-Process -Name "docker" -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 30

# Verify stopped
$processes = Get-Process | Where-Object {$_.Name -like "*docker*"}
if ($processes) {
    Write-Host "Force stopping remaining processes..." -ForegroundColor Yellow
    $processes | ForEach-Object { Stop-Process -Id $_.Id -Force }
    Start-Sleep -Seconds 10
}

# Start Docker
Write-Host "`nStarting Docker Desktop..." -ForegroundColor Cyan
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
Write-Host "Waiting 60 seconds for full startup..." -ForegroundColor Cyan
Start-Sleep -Seconds 60

# Verify
Write-Host "`nVerifying Docker..." -ForegroundColor Yellow
try {
    $version = docker version 2>&1
    if ($version -match "Server") {
        Write-Host "Docker: RUNNING" -ForegroundColor Green
    } else {
        Write-Host "ERROR: Docker still not responding!" -ForegroundColor Red
        Write-Host "Please restart computer and try again." -ForegroundColor Yellow
        exit 1
    }
} catch {
    Write-Host "ERROR: Docker version check failed" -ForegroundColor Red
    Write-Host "Please restart computer and try again." -ForegroundColor Yellow
    exit 1
}

# Check containers
Write-Host "`nChecking containers..." -ForegroundColor Yellow
try {
    $containers = docker ps --format "{{.Names}}" 2>&1
    if ($containers -match "prometheus") {
        Write-Host "Containers: RUNNING" -ForegroundColor Green
        docker ps --format "table {{.Names}}`t{{.Status}}"
    } else {
        Write-Host "No containers running" -ForegroundColor Yellow
        Write-Host "Starting monitoring stack..." -ForegroundColor Cyan
        docker-compose -f docker-compose.monitoring.yml up -d
        Start-Sleep -Seconds 15
    }
} catch {
    Write-Host "WARNING: Cannot check containers" -ForegroundColor Yellow
}

# Verify services
Write-Host "`nVerifying services..." -ForegroundColor Yellow

# Prometheus
try {
    $response = curl.exe -s http://localhost:9090/-/healthy 2>$null
    if ($response -match "Prometheus Server is Healthy") {
        Write-Host "Prometheus: HEALTHY" -ForegroundColor Green
    } else {
        Write-Host "Prometheus: NOT RESPONDING" -ForegroundColor Yellow
    }
} catch {
    Write-Host "Prometheus: NOT ACCESSIBLE" -ForegroundColor Yellow
}

# Grafana
try {
    $response = curl.exe -s http://localhost:3000/api/health 2>$null
    if ($response -match "ok") {
        Write-Host "Grafana: HEALTHY" -ForegroundColor Green
    } else {
        Write-Host "Grafana: NOT RESPONDING" -ForegroundColor Yellow
    }
} catch {
    Write-Host "Grafana: NOT ACCESSIBLE" -ForegroundColor Yellow
}

# Check alert rules
Write-Host "`nChecking alert rules..." -ForegroundColor Yellow
try {
    $rules = curl.exe -s http://localhost:9090/api/v1/rules 2>$null | ConvertFrom-Json
    if ($rules.data.groups) {
        $totalRules = 0
        foreach ($group in $rules.data.groups) {
            $totalRules += $group.rules.Count
        }
        if ($totalRules -ge 19) {
            Write-Host "Alert rules: $totalRules rules loaded" -ForegroundColor Green
        } else {
            Write-Host "Alert rules: $totalRules rules (expected 19)" -ForegroundColor Yellow
        }
    } else {
        Write-Host "Alert rules: NOT LOADED" -ForegroundColor Yellow
    }
} catch {
    Write-Host "Alert rules: CHECK FAILED" -ForegroundColor Yellow
}

Write-Host "`n=== Recovery Complete ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "If services are still not working:" -ForegroundColor White
Write-Host "1. Restart your computer" -ForegroundColor Cyan
Write-Host "2. Check logs: docker-compose -f docker-compose.monitoring.yml logs" -ForegroundColor Cyan
Write-Host "3. See: docker_crash_recovery.md for detailed troubleshooting" -ForegroundColor Cyan
Write-Host ""
