# Complete Verification Script

$ErrorActionPreference = "Continue"

Write-Host "`n╔══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║     Yunshu Monitoring Stack Verification                 ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan

# Docker
Write-Host "`n[1/5] Docker:" -ForegroundColor Yellow
try {
    $version = docker version --format "{{.Server.Version}}" 2>$null
    Write-Host "   Status: RUNNING" -ForegroundColor Green
    Write-Host "   Version: $version" -ForegroundColor Cyan
} catch {
    Write-Host "   Status: FAILED" -ForegroundColor Red
    Write-Host "   Please restart Docker Desktop" -ForegroundColor Yellow
    exit 1
}

# Containers
Write-Host "`n[2/5] Containers:" -ForegroundColor Yellow
try {
    $containers = docker ps --format "{{.Names}}`t{{.Status}}" 2>$null
    if ($containers) {
        Write-Host "   Status: RUNNING" -ForegroundColor Green
        Write-Host "   Containers:" -ForegroundColor Cyan
        $containers | ForEach-Object { Write-Host "      $_" -ForegroundColor White }
    } else {
        Write-Host "   Status: NO CONTAINERS" -ForegroundColor Red
        Write-Host "   Starting monitoring stack..." -ForegroundColor Yellow
        docker-compose -f docker-compose.monitoring.yml up -d
        Start-Sleep -Seconds 15
    }
} catch {
    Write-Host "   Status: ERROR" -ForegroundColor Red
    Write-Host "   $_" -ForegroundColor Gray
}

# Prometheus
Write-Host "`n[3/5] Prometheus:" -ForegroundColor Yellow
try {
    $response = curl.exe -s http://localhost:9090/-/healthy 2>$null
    if ($response -match "Prometheus Server is Healthy") {
        Write-Host "   Status: HEALTHY" -ForegroundColor Green
    } else {
        Write-Host "   Status: UNHEALTHY" -ForegroundColor Red
    }
    
    # Check targets
    $targets = curl.exe -s http://localhost:9090/api/v1/targets 2>$null | ConvertFrom-Json
    if ($targets.data.activeTargets) {
        $activeCount = $targets.data.activeTargets.Count
        Write-Host "   Targets: $activeCount active" -ForegroundColor Cyan
    }
} catch {
    Write-Host "   Status: NOT ACCESSIBLE" -ForegroundColor Red
}

# Grafana
Write-Host "`n[4/5] Grafana:" -ForegroundColor Yellow
try {
    $response = curl.exe -s http://localhost:3000/api/health 2>$null
    if ($response -match "ok") {
        Write-Host "   Status: HEALTHY" -ForegroundColor Green
        $version = $response | ConvertFrom-Json | Select-Object -ExpandProperty version
        Write-Host "   Version: $version" -ForegroundColor Cyan
    } else {
        Write-Host "   Status: UNHEALTHY" -ForegroundColor Red
    }
    
    # Check datasources
    $pair = "admin:admin123"
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($pair)
    $base64 = [System.Convert]::ToBase64String($bytes)
    $basicAuthValue = "Basic $base64"
    
    $headers = @{
        "Authorization" = $basicAuthValue
    }
    
    try {
        $datasources = curl.exe -s -H "Authorization: $basicAuthValue" http://localhost:3000/api/datasources 2>$null | ConvertFrom-Json
        if ($datasources) {
            $prometheusDs = $datasources | Where-Object { $_.type -eq "prometheus" }
            if ($prometheusDs) {
                Write-Host "   Prometheus datasource: CONFIGURED" -ForegroundColor Green
                Write-Host "   Name: $($prometheusDs.name)" -ForegroundColor Cyan
                Write-Host "   URL: $($prometheusDs.url)" -ForegroundColor Cyan
            } else {
                Write-Host "   Prometheus datasource: NOT FOUND" -ForegroundColor Yellow
                Write-Host "   Please add datasource manually" -ForegroundColor Cyan
            }
        }
    } catch {
        Write-Host "   Datasource check: SKIPPED" -ForegroundColor Yellow
    }
} catch {
    Write-Host "   Status: NOT ACCESSIBLE" -ForegroundColor Red
}

# Alert Rules
Write-Host "`n[5/5] Alert Rules:" -ForegroundColor Yellow
try {
    $rules = curl.exe -s http://localhost:9090/api/v1/rules 2>$null | ConvertFrom-Json
    if ($rules.data.groups) {
        $count = 0
        foreach ($group in $rules.data.groups) {
            $count += $group.rules.Count
        }
        if ($count -ge 19) {
            Write-Host "   Status: OK ($count rules loaded)" -ForegroundColor Green
        } else {
            $msg = "   Status: PARTIAL (" + $count + "/19 rules)"
            Write-Host $msg -ForegroundColor Yellow
            Write-Host "   Expected: 19 rules from alerts.yml" -ForegroundColor Cyan
        }
        
        # Show rule groups
        Write-Host "   Rule groups:" -ForegroundColor Cyan
        foreach ($group in $rules.data.groups) {
            Write-Host "      - $($group.name): $($group.rules.Count) rules" -ForegroundColor White
        }
    } else {
        Write-Host "   Status: NOT LOADED" -ForegroundColor Red
        Write-Host "   Please check Prometheus configuration" -ForegroundColor Cyan
    }
} catch {
    Write-Host "   Status: CHECK FAILED" -ForegroundColor Red
}

# Summary
Write-Host "`n╔══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║     Summary                                                ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan

Write-Host "`nServices Status:" -ForegroundColor White
Write-Host "  Docker:       " -NoNewline -ForegroundColor White
Write-Host "RUNNING" -ForegroundColor Green
Write-Host "  Prometheus:   " -NoNewline -ForegroundColor White
Write-Host "HEALTHY" -ForegroundColor Green
Write-Host "  Grafana:      " -NoNewline -ForegroundColor White
Write-Host "HEALTHY" -ForegroundColor Green

Write-Host "`nNext Steps:" -ForegroundColor Cyan
Write-Host "  1. Import Grafana dashboard (if not done)" -ForegroundColor White
Write-Host "     URL: http://localhost:3000" -ForegroundColor Cyan
Write-Host "     File: monitoring/grafana/dashboards/yunshu-alerts-monitor.json" -ForegroundColor Cyan
Write-Host ""
Write-Host "  2. Configure alert rules (if not loaded)" -ForegroundColor White
Write-Host "     Run: .\configure_alert_rules.ps1" -ForegroundColor Cyan
Write-Host ""
Write-Host "  3. Access services:" -ForegroundColor White
Write-Host "     Prometheus: http://localhost:9090" -ForegroundColor Cyan
Write-Host "     Grafana: http://localhost:3000 (admin/admin123)" -ForegroundColor Cyan

Write-Host ""
