# Prometheus Alert Rules Configuration Script

$ErrorActionPreference = "Stop"

Write-Host "`n╔══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║     Prometheus Alert Rules Configuration                 ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan

# Configuration
$prometheusUrl = "http://localhost:9090"
$alertsFile = "monitoring\alerts.yml"
$prometheusConfig = "monitoring\prometheus.yml"

Write-Host "`n[1/5] Checking files..." -ForegroundColor Yellow

# Check if files exist
if (-not (Test-Path $alertsFile)) {
    Write-Host "   ERROR: $alertsFile not found!" -ForegroundColor Red
    Write-Host "   Creating alerts.yml..." -ForegroundColor Cyan
    # Create basic alerts file
    $alertsContent = @"
groups:
  - name: yunshu_alerts
    rules:
      # Error Rate Alerts
      - alert: HighErrorRate
        expr: sum(rate(http_requests_total{status=~"5.."}[5m])) / sum(rate(http_requests_total[5m])) > 0.05
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High error rate detected"
          description: "Error rate is above 5%"
"@
    Set-Content -Path $alertsFile -Value $alertsContent -Encoding UTF8
    Write-Host "   Created basic alerts.yml" -ForegroundColor Green
} else {
    Write-Host "   OK - $alertsFile exists" -ForegroundColor Green
}

if (-not (Test-Path $prometheusConfig)) {
    Write-Host "   ERROR: $prometheusConfig not found!" -ForegroundColor Red
    exit 1
} else {
    Write-Host "   OK - $prometheusConfig exists" -ForegroundColor Green
}

Write-Host "`n[2/5] Verifying Prometheus configuration..." -ForegroundColor Yellow

# Read prometheus config
$config = Get-Content $prometheusConfig -Raw

# Check if rule_files is configured
if ($config -match "rule_files:") {
    Write-Host "   OK - rule_files section exists" -ForegroundColor Green
    
    if ($config -match "alerts.yml") {
        Write-Host "   OK - alerts.yml referenced" -ForegroundColor Green
    } else {
        Write-Host "   WARNING - alerts.yml not referenced in config" -ForegroundColor Yellow
        Write-Host "   Adding alerts.yml to rule_files..." -ForegroundColor Cyan
        
        # Add alerts.yml to rule_files
        $lines = Get-Content $prometheusConfig
        $newLines = @()
        $added = $false
        
        foreach ($line in $lines) {
            $newLines += $line
            if (-not $added -and $line -match "rule_files:") {
                $newLines += "  - alerts.yml"
                $added = $true
            }
        }
        
        Set-Content -Path $prometheusConfig -Value $newLines -Encoding UTF8
        Write-Host "   Added alerts.yml to prometheus.yml" -ForegroundColor Green
    }
} else {
    Write-Host "   Adding rule_files section..." -ForegroundColor Cyan
    
    # Add rule_files section
    $newContent = @"
$config

rule_files:
  - alerts.yml
"@
    Set-Content -Path $prometheusConfig -Value $newContent -Encoding UTF8
    Write-Host "   Added rule_files to prometheus.yml" -ForegroundColor Green
}

Write-Host "`n[3/5] Restarting Prometheus..." -ForegroundColor Yellow

try {
    docker-compose -f docker-compose.monitoring.yml restart prometheus
    Write-Host "   Prometheus restarted" -ForegroundColor Green
    Write-Host "   Waiting 10 seconds for startup..." -ForegroundColor Cyan
    Start-Sleep -Seconds 10
} catch {
    Write-Host "   ERROR: Failed to restart Prometheus" -ForegroundColor Red
    Write-Host "   $_" -ForegroundColor Gray
    exit 1
}

Write-Host "`n[4/5] Verifying alert rules..." -ForegroundColor Yellow

try {
    $rules = curl.exe -s "$prometheusUrl/api/v1/rules" | ConvertFrom-Json
    
    if ($rules.data.groups) {
        $count = 0
        foreach ($group in $rules.data.groups) {
            $count += $group.rules.Count
        }
        
        if ($count -gt 0) {
            Write-Host "   SUCCESS - $count alert rules loaded" -ForegroundColor Green
            
            Write-Host "   Rule groups:" -ForegroundColor Cyan
            foreach ($group in $rules.data.groups) {
                Write-Host "      - $($group.name): $($group.rules.Count) rules" -ForegroundColor White
            }
        } else {
            Write-Host "   WARNING - No rules loaded" -ForegroundColor Yellow
        }
    } else {
        Write-Host "   WARNING - No rule groups found" -ForegroundColor Yellow
    }
} catch {
    Write-Host "   ERROR: Failed to fetch rules" -ForegroundColor Red
    Write-Host "   $_" -ForegroundColor Gray
}

Write-Host "`n[5/5] Opening Prometheus rules page..." -ForegroundColor Cyan

try {
    Start-Process "$prometheusUrl/rules"
    Write-Host "   Opened browser to Prometheus rules page" -ForegroundColor Green
} catch {
    Write-Host "   WARNING: Could not open browser" -ForegroundColor Yellow
}

Write-Host "`n╔══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║     Configuration Complete!                                ║" -ForegroundColor Green
Write-Host "╚══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan

Write-Host "`nAlert rules are now configured!" -ForegroundColor Cyan
Write-Host "View rules at: $prometheusUrl/rules" -ForegroundColor White
Write-Host ""
Write-Host "To customize alerts, edit: $alertsFile" -ForegroundColor Cyan
Write-Host ""
