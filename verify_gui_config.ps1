# Post-GUI Configuration Verification Script

$ErrorActionPreference = "Continue"

Write-Host "`n" -NoNewline
Write-Host "╔══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║     Docker GUI Configuration Verification                ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# Step 1: Check Docker
Write-Host "[1/4] Checking Docker status..." -ForegroundColor Yellow
try {
    $dockerVersion = docker version --format "{{.Server.Version}}" 2>$null
    Write-Host "   OK: Docker $dockerVersion" -ForegroundColor Green
} catch {
    Write-Host "   ERROR: Docker not running!" -ForegroundColor Red
    Write-Host "   Please restart Docker Desktop." -ForegroundColor Yellow
    exit 1
}

# Step 2: Verify mirrors
Write-Host "`n[2/4] Verifying mirror configuration..." -ForegroundColor Yellow

try {
    $registryConfig = docker info --format '{{json .RegistryConfig}}' 2>$null | ConvertFrom-Json
    $mirrors = $registryConfig.Mirrors
    
    if ($mirrors -and $mirrors.Count -gt 0) {
        Write-Host "   SUCCESS: Mirrors configured!" -ForegroundColor Green
        Write-Host "   Configured mirrors:" -ForegroundColor Cyan
        foreach ($mirror in $mirrors) {
            Write-Host "      - $mirror" -ForegroundColor White
        }
        
        Write-Host "`n   Configuration is VALID" -ForegroundColor Green
    } else {
        Write-Host "   WARNING: No mirrors configured!" -ForegroundColor Yellow
        Write-Host "   GUI configuration may not have been applied." -ForegroundColor Red
        Write-Host "   Please try again:" -ForegroundColor Cyan
        Write-Host "   1. Docker Desktop Settings → Docker Engine" -ForegroundColor White
        Write-Host "   2. Add registry-mirrors configuration" -ForegroundColor White
        Write-Host "   3. Click Apply & Restart" -ForegroundColor White
        Write-Host "   4. Wait 2-3 minutes for complete restart" -ForegroundColor White
        exit 1
    }
} catch {
    Write-Host "   ERROR: Failed to verify configuration" -ForegroundColor Red
    Write-Host "   $_" -ForegroundColor Gray
    exit 1
}

# Step 3: Test pull
Write-Host "`n[3/4] Testing image pull..." -ForegroundColor Yellow

$testImage = "prom/prometheus:latest"
Write-Host "   Pulling: $testImage" -ForegroundColor Cyan
Write-Host "   (This may take 5-30 minutes depending on network)" -ForegroundColor Yellow
Write-Host "   (Progress will be shown below)" -ForegroundColor Cyan
Write-Host ""

try {
    $pullStart = Get-Date
    $lastProgress = ""
    
    # Pull with progress display
    & docker pull $testImage 2>&1 | ForEach-Object {
        if ($_ -match "Pulling from|Downloading|Extracting|Download complete|Pull complete") {
            Write-Host "   $_" -ForegroundColor Gray
        }
    }
    
    $pullEnd = Get-Date
    $duration = New-TimeSpan -Start $pullStart -End $pullEnd
    
    Write-Host ""
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "   SUCCESS: Image pulled!" -ForegroundColor Green
        Write-Host "   Duration: $($duration.Minutes)m $($duration.Seconds)s" -ForegroundColor Cyan
        
        # Verify image
        Write-Host "`n   Verifying image..." -ForegroundColor Cyan
        $imageInfo = docker images prom/prometheus --format "{{.Repository}}:{{.Tag}} | Size: {{.Size}} | Created: {{.CreatedSince}}"
        Write-Host "   $imageInfo" -ForegroundColor White
        
        Write-Host "`n   Image is READY" -ForegroundColor Green
    } else {
        Write-Host ""
        Write-Host "   FAILED: Image pull failed!" -ForegroundColor Red
        Write-Host ""
        Write-Host "   Despite mirrors being configured, pull failed." -ForegroundColor Yellow
        Write-Host "   This indicates:" -ForegroundColor Cyan
        Write-Host "   1. Mirrors may be temporarily unavailable" -ForegroundColor White
        Write-Host "   2. Network connectivity issues" -ForegroundColor White
        Write-Host "   3. Firewall blocking Docker" -ForegroundColor White
        Write-Host ""
        Write-Host "   Recommended actions:" -ForegroundColor Cyan
        Write-Host "   1. Try pulling again in a few minutes" -ForegroundColor White
        Write-Host "   2. Try alternative mirrors" -ForegroundColor White
        Write-Host "   3. Use OFFLINE IMPORT method (100% reliable)" -ForegroundColor Green
        Write-Host ""
        Write-Host "   For offline import, see:" -ForegroundColor Cyan
        Write-Host "   offline_image_import_complete.md" -ForegroundColor White
    }
} catch {
    Write-Host ""
    Write-Host "   ERROR: Pull failed with exception" -ForegroundColor Red
    Write-Host "   $_" -ForegroundColor Gray
}

# Step 4: Summary
Write-Host "`n[4/4] Summary" -ForegroundColor Yellow
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Cyan

try {
    $dockerInfo = docker info --format '{{json .RegistryConfig}}' 2>$null | ConvertFrom-Json
    $images = docker images prom/prometheus --format "{{.Repository}}:{{.Tag}}"
    
    Write-Host "Docker Version:     " -NoNewline -ForegroundColor White
    Write-Host $dockerVersion -ForegroundColor Green
    
    Write-Host "Mirrors Configured: " -NoNewline -ForegroundColor White
    if ($dockerInfo.Mirrors -and $dockerInfo.Mirrors.Count -gt 0) {
        Write-Host "Yes ($($dockerInfo.Mirrors.Count) mirrors)" -ForegroundColor Green
    } else {
        Write-Host "No" -ForegroundColor Red
    }
    
    Write-Host "Prometheus Image:   " -NoNewline -ForegroundColor White
    if ($images) {
        Write-Host "Downloaded" -ForegroundColor Green
        Write-Host "   Ready to pull Grafana and start stack" -ForegroundColor Cyan
    } else {
        Write-Host "Not downloaded" -ForegroundColor Yellow
        Write-Host "   Pull failed or still in progress" -ForegroundColor Cyan
    }
} catch {
    Write-Host "Summary generation failed" -ForegroundColor Red
}

Write-Host ""
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Cyan

if ($images) {
    Write-Host "`n🎉 SUCCESS! Ready to complete deployment." -ForegroundColor Green
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor Cyan
    Write-Host "1. Pull Grafana image:" -ForegroundColor White
    Write-Host "   docker pull grafana/grafana:latest" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "2. Start monitoring stack:" -ForegroundColor White
    Write-Host "   docker-compose -f docker-compose.monitoring.yml up -d" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "3. Check status:" -ForegroundColor White
    Write-Host "   docker-compose -f docker-compose.monitoring.yml ps" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "4. Access services:" -ForegroundColor White
    Write-Host "   Prometheus: http://localhost:9090" -ForegroundColor Cyan
    Write-Host "   Grafana: http://localhost:3000 (admin/admin123)" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "5. Import Grafana dashboard:" -ForegroundColor White
    Write-Host "   Upload: monitoring/grafana/dashboards/yunshu-alerts-monitor.json" -ForegroundColor Cyan
} else {
    Write-Host "`n⚠️  Image pull failed." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Options:" -ForegroundColor Cyan
    Write-Host "1. Wait a few minutes and try again" -ForegroundColor White
    Write-Host "2. Try different mirrors in Docker Desktop" -ForegroundColor White
    Write-Host "3. Use OFFLINE IMPORT (recommended for reliability)" -ForegroundColor Green
    Write-Host ""
    Write-Host "For offline import:" -ForegroundColor Cyan
    Write-Host "   See: offline_image_import_complete.md" -ForegroundColor White
    Write-Host "   Success rate: 100%" -ForegroundColor Green
    Write-Host "   Time required: 20-50 minutes" -ForegroundColor Green
}

Write-Host ""
