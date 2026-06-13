Write-Host "`n=== Docker Verification ===" -ForegroundColor Cyan

# Check Docker
try {
    $v = docker version --format "{{.Server.Version}}" 2>$null
    Write-Host "Docker: $v" -ForegroundColor Green
} catch {
    Write-Host "ERROR: Docker not running" -ForegroundColor Red
    exit 1
}

# Check mirrors
Write-Host "`nMirrors:" -ForegroundColor Yellow
$m = docker info --format '{{.RegistryConfig.Mirrors}}' 2>$null
if ($m -and $m.Count -gt 0) {
    Write-Host "Configured: $m" -ForegroundColor Green
} else {
    Write-Host "NOT CONFIGURED" -ForegroundColor Red
    Write-Host "Configure in Docker Desktop Settings" -ForegroundColor Yellow
}

# Test pull
Write-Host "`nPulling prom/prometheus:latest..." -ForegroundColor Yellow
docker pull prom/prometheus:latest

if ($LASTEXITCODE -eq 0) {
    Write-Host "`nSUCCESS!" -ForegroundColor Green
    docker images prom/prometheus --format "table {{.Repository}}:{{.Tag}}`t{{.Size}}"
    Write-Host "`nNext: docker pull grafana/grafana:latest" -ForegroundColor Cyan
} else {
    Write-Host "`nFAILED" -ForegroundColor Red
    Write-Host "Use offline import: .\import_and_start.ps1" -ForegroundColor Yellow
}
