# One-Click Docker Restart and Verification Script

Write-Host "`n╔══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║     Yunshu Monitoring Stack - One Click Restart          ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan

# Step 0: Check if running as administrator
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "`nWARNING: Not running as administrator" -ForegroundColor Yellow
    Write-Host "Some operations may fail due to permissions" -ForegroundColor Yellow
}

# Step 1: Stop Docker Desktop processes
Write-Host "`n[Step 1/6] Stopping Docker Desktop..." -ForegroundColor Yellow
try {
    Get-Process "Docker Desktop" -ErrorAction SilentlyContinue | Stop-Process -Force
    Write-Host "   Docker Desktop processes stopped" -ForegroundColor Green
} catch {
    Write-Host "   No Docker Desktop processes found" -ForegroundColor Cyan
}

# Step 2: Wait for processes to fully terminate
Write-Host "`n[Step 2/6] Waiting for processes to terminate..." -ForegroundColor Yellow
Start-Sleep -Seconds 10

# Step 3: Start Docker Desktop
Write-Host "`n[Step 3/6] Starting Docker Desktop..." -ForegroundColor Yellow
try {
    Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    Write-Host "   Docker Desktop started" -ForegroundColor Green
} catch {
    Write-Host "   Failed to start Docker Desktop: $_" -ForegroundColor Red
    exit 1
}

# Step 4: Wait for Docker to be ready
Write-Host "`n[Step 4/6] Waiting for Docker to initialize (60 seconds)..." -ForegroundColor Cyan
for ($i = 60; $i -gt 0; $i--) {
    Write-Host "   Waiting: $i seconds remaining..." -NoNewline
    Start-Sleep -Seconds 1
    Write-Host "`r" -NoNewline
}
Write-Host "   Docker initialization complete" -ForegroundColor Green

# Step 5: Rebuild containers
Write-Host "`n[Step 5/6] Rebuilding containers..." -ForegroundColor Yellow

# Try to stop containers
Write-Host "   Stopping existing containers..." -ForegroundColor Cyan
try {
    docker-compose -f docker-compose.monitoring.yml down 2>$null | Out-Null
    Write-Host "   Containers stopped" -ForegroundColor Green
} catch {
    Write-Host "   Warning: Could not stop containers gracefully" -ForegroundColor Yellow
}

# Start containers
Write-Host "   Starting containers with new configuration..." -ForegroundColor Cyan
try {
    docker-compose -f docker-compose.monitoring.yml up -d
    Write-Host "   Containers started successfully" -ForegroundColor Green
} catch {
    Write-Host "   Error starting containers: $_" -ForegroundColor Red
    Write-Host "   Attempting to continue..." -ForegroundColor Yellow
}

# Step 6: Wait for services to be ready
Write-Host "`n[Step 6/6] Waiting for services to start (30 seconds)..." -ForegroundColor Cyan
Start-Sleep -Seconds 30

# Verification
Write-Host "`n╔══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║     Verification                                           ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan

Write-Host "`nRunning verification script..." -ForegroundColor Yellow
try {
    & ".\simple_verify.ps1"
} catch {
    Write-Host "   Verification script failed: $_" -ForegroundColor Red
}

# Additional checks
Write-Host "`n╔══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║     Additional Checks                                      ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan

# Check Docker API
Write-Host "`nChecking Docker API..." -ForegroundColor Cyan
try {
    $dockerVersion = docker version --format "{{.Server.Version}}" 2>$null
    if ($dockerVersion) {
        Write-Host "   Docker API: WORKING (Version: $dockerVersion)" -ForegroundColor Green
    } else {
        Write-Host "   Docker API: UNSTABLE" -ForegroundColor Yellow
    }
} catch {
    Write-Host "   Docker API: NOT ACCESSIBLE" -ForegroundColor Red
}

# Check container status
Write-Host "`nChecking container status..." -ForegroundColor Cyan
try {
    $containers = docker ps --format "table {{.Names}}\t{{.Status}}" 2>$null
    if ($containers) {
        Write-Host "   Active containers:" -ForegroundColor Green
        Write-Host $containers -ForegroundColor Cyan
    } else {
        Write-Host "   No active containers found" -ForegroundColor Yellow
    }
} catch {
    Write-Host "   Could not check container status" -ForegroundColor Yellow
}

# Final summary
Write-Host "`n╔══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║     Restart Complete!                                      ║" -ForegroundColor Green
Write-Host "╚══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan

Write-Host "`nNext steps:" -ForegroundColor Cyan
Write-Host "1. Check the verification results above" -ForegroundColor White
Write-Host "2. Access Prometheus: http://localhost:9090" -ForegroundColor White
Write-Host "3. Access Grafana: http://localhost:3000 (admin/admin123)" -ForegroundColor White
Write-Host "4. If alert rules still not loaded, restart Docker Desktop manually" -ForegroundColor White
Write-Host ""
