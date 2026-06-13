# Yunshu 监控栈 - Docker 配置验证和镜像拉取脚本

$ErrorActionPreference = "Continue"

Write-Host "`n" -NoNewline
Write-Host "╔══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║     🔍  Docker 配置验证工具                              ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# Step 1: Check Docker status
Write-Host "[1/4] Checking Docker status..." -ForegroundColor Yellow
try {
    $dockerVersion = docker version --format "{{.Server.Version}}" 2>$null
    Write-Host "   OK: Docker $dockerVersion" -ForegroundColor Green
} catch {
    Write-Host "   ERROR: Docker not running!" -ForegroundColor Red
    Write-Host "   Please start Docker Desktop and try again." -ForegroundColor Yellow
    exit 1
}

# Step 2: Verify mirror configuration
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
    } else {
        Write-Host "   WARNING: No mirrors configured!" -ForegroundColor Yellow
        Write-Host "   Please configure mirrors in Docker Desktop Settings → Docker Engine" -ForegroundColor Cyan
        Write-Host "   Example config:" -ForegroundColor Cyan
        Write-Host @"
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://docker.1panel.live"
  ]
}
"@ -ForegroundColor Gray
    }
} catch {
    Write-Host "   ERROR: Failed to verify configuration" -ForegroundColor Red
    Write-Host "   $_" -ForegroundColor Gray
}

# Step 3: Test image pull
Write-Host "`n[3/4] Testing image pull..." -ForegroundColor Yellow

$testImage = "prom/prometheus:latest"
Write-Host "   Pulling: $testImage" -ForegroundColor Cyan
Write-Host "   (This may take 5-30 minutes depending on network)" -ForegroundColor Yellow
Write-Host ""

try {
    $pullStart = Get-Date
    docker pull $testImage
    
    if ($LASTEXITCODE -eq 0) {
        $pullEnd = Get-Date
        $duration = New-TimeSpan -Start $pullStart -End $pullEnd
        
        Write-Host ""
        Write-Host "   SUCCESS: Image pulled!" -ForegroundColor Green
        Write-Host "   Duration: $($duration.Minutes)m $($duration.Seconds)s" -ForegroundColor Cyan
        
        # Verify image
        Write-Host "`n   Verifying image..." -ForegroundColor Cyan
        $image = docker images prom/prometheus --format "{{.Repository}}:{{.Tag}} - Size: {{.Size}} - Created: {{.CreatedSince}}"
        Write-Host "   $image" -ForegroundColor White
    } else {
        Write-Host ""
        Write-Host "   FAILED: Image pull failed!" -ForegroundColor Red
        Write-Host ""
        Write-Host "   Possible causes:" -ForegroundColor Yellow
        Write-Host "   1. Mirrors not configured correctly" -ForegroundColor Cyan
        Write-Host "   2. Mirrors are unavailable" -ForegroundColor Cyan
        Write-Host "   3. Network firewall blocking Docker" -ForegroundColor Cyan
        Write-Host "   4. DNS resolution issues" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "   Next steps:" -ForegroundColor Yellow
        Write-Host "   1. Restart Docker Desktop completely" -ForegroundColor Cyan
        Write-Host "   2. Try alternative mirrors" -ForegroundColor Cyan
        Write-Host "   3. Use offline import method" -ForegroundColor Cyan
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
    } else {
        Write-Host "Not downloaded" -ForegroundColor Yellow
    }
} catch {
    Write-Host "Summary generation failed" -ForegroundColor Red
}

Write-Host ""
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Cyan

if ($images) {
    Write-Host "`n🎉 SUCCESS! Ready to start monitoring stack." -ForegroundColor Green
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
} else {
    Write-Host "`n⚠️  Image pull failed. Please check the error above." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Recommended actions:" -ForegroundColor Cyan
    Write-Host "1. Verify Docker Desktop mirror configuration" -ForegroundColor White
    Write-Host "2. Restart Docker Desktop completely" -ForegroundColor White
    Write-Host "3. Try alternative mirrors" -ForegroundColor White
    Write-Host "4. Use offline import method" -ForegroundColor White
    Write-Host ""
    Write-Host "For detailed troubleshooting, see:" -ForegroundColor Cyan
    Write-Host "docker_mirror_troubleshooting_report.md" -ForegroundColor White
    Write-Host "offline_image_import_complete.md" -ForegroundColor White
}

Write-Host ""
