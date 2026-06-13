# Docker Mirror Fix Script - Simplified Version
# Auto-configure Docker registry mirrors for China

$ErrorActionPreference = "Continue"

Write-Host "`n=== Docker Mirror Auto Configuration ===" -ForegroundColor Cyan
Write-Host ""

# Step 1: Check Docker
Write-Host "[1/5] Checking Docker status..." -ForegroundColor Yellow
try {
    $dockerVersion = docker version --format "{{.Server.Version}}" 2>$null
    Write-Host "   OK: Docker $dockerVersion" -ForegroundColor Green
} catch {
    Write-Host "   ERROR: Docker not running!" -ForegroundColor Red
    exit 1
}

# Step 2: Create config directory
Write-Host "`n[2/5] Creating config directory..." -ForegroundColor Yellow
$dockerConfigDir = "$env:USERPROFILE\.docker"
if (-not (Test-Path $dockerConfigDir)) {
    New-Item -ItemType Directory -Path $dockerConfigDir -Force | Out-Null
    Write-Host "   Created: $dockerConfigDir" -ForegroundColor Green
} else {
    Write-Host "   Exists: $dockerConfigDir" -ForegroundColor Green
}

# Step 3: Backup old config
Write-Host "`n[3/5] Backing up old config..." -ForegroundColor Yellow
$dockerConfigPath = "$dockerConfigDir\daemon.json"
if (Test-Path $dockerConfigPath) {
    $backupPath = "$dockerConfigDir\daemon.json.backup.$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Copy-Item $dockerConfigPath $backupPath
    Write-Host "   Backed up to: $backupPath" -ForegroundColor Green
} else {
    Write-Host "   No existing config" -ForegroundColor Yellow
}

# Step 4: Create new config
Write-Host "`n[4/5] Creating new config..." -ForegroundColor Yellow

$configJson = @"
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://docker.1panel.live",
    "https://hub.rat.dev",
    "https://dhub.kubesre.xyz"
  ],
  "max-concurrent-downloads": 10,
  "log-level": "info"
}
"@

$configJson | Out-File -FilePath $dockerConfigPath -Encoding UTF8
Write-Host "   Created: $dockerConfigPath" -ForegroundColor Green
Write-Host ""
Write-Host "   Mirrors configured:" -ForegroundColor Cyan
Write-Host "   - docker.m.daocloud.io" -ForegroundColor White
Write-Host "   - docker.1panel.live" -ForegroundColor White
Write-Host "   - hub.rat.dev" -ForegroundColor White
Write-Host "   - dhub.kubesre.xyz" -ForegroundColor White

# Step 5: Restart Docker
Write-Host "`n[5/5] Restarting Docker Desktop..." -ForegroundColor Yellow
Write-Host "   Stopping Docker Desktop..." -ForegroundColor Cyan
Stop-Process -Name "Docker Desktop" -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 10

Write-Host "   Starting Docker Desktop..." -ForegroundColor Cyan
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
Write-Host "   Waiting for Docker to start (30 seconds)..." -ForegroundColor Cyan
Start-Sleep -Seconds 30

# Verify
Write-Host "`n=== Verification ===" -ForegroundColor Cyan
try {
    $mirrors = docker info --format '{{.RegistryConfig.Mirrors}}' 2>$null
    if ($mirrors) {
        Write-Host "   SUCCESS: Mirrors configured" -ForegroundColor Green
        Write-Host "   $mirrors" -ForegroundColor Cyan
    } else {
        Write-Host "   WARNING: Cannot verify mirrors" -ForegroundColor Yellow
    }
} catch {
    Write-Host "   WARNING: Verification failed" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== Next Steps ===" -ForegroundColor Cyan
Write-Host "1. Pull images:" -ForegroundColor White
Write-Host "   docker pull prom/prometheus:latest" -ForegroundColor Cyan
Write-Host "   docker pull grafana/grafana:latest" -ForegroundColor Cyan
Write-Host ""
Write-Host "2. Start monitoring stack:" -ForegroundColor White
Write-Host "   docker-compose -f docker-compose.monitoring.yml up -d" -ForegroundColor Cyan
Write-Host ""
Write-Host "3. Check status:" -ForegroundColor White
Write-Host "   docker-compose -f docker-compose.monitoring.yml ps" -ForegroundColor Cyan
Write-Host ""
