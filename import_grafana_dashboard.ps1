# Grafana Dashboard Import Script

$ErrorActionPreference = "Continue"

Write-Host "`n╔══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║     Yunshu Grafana Dashboard Import                      ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan

# Configuration
$grafanaUrl = "http://localhost:3000"
$dashboardPath = "monitoring\grafana\dashboards\yunshu-alerts-monitor.json"
$dashboardName = "Yunshu Alerts Monitor"

Write-Host "`n[1/4] Checking Grafana availability..." -ForegroundColor Yellow

try {
    $response = Invoke-RestMethod -Uri "$grafanaUrl/api/health" -Method Get -ErrorAction Stop
    Write-Host "   Grafana Status: $($response.database)" -ForegroundColor Green
    Write-Host "   Version: $($response.version)" -ForegroundColor Cyan
} catch {
    Write-Host "   ERROR: Grafana not accessible at $grafanaUrl" -ForegroundColor Red
    Write-Host "   Please check if Grafana container is running" -ForegroundColor Yellow
    exit 1
}

Write-Host "`n[2/4] Verifying dashboard file..." -ForegroundColor Yellow

if (Test-Path $dashboardPath) {
    $dashboardContent = Get-Content $dashboardPath -Raw
    Write-Host "   Dashboard file: OK" -ForegroundColor Green
    Write-Host "   Path: $dashboardPath" -ForegroundColor Cyan
    
    # Get dashboard info
    $dashboardJson = $dashboardContent | ConvertFrom-Json
    Write-Host "   Dashboard title: $($dashboardJson.title)" -ForegroundColor Cyan
    Write-Host "   Panels count: $($dashboardJson.panels.Count)" -ForegroundColor Cyan
} else {
    Write-Host "   ERROR: Dashboard file not found!" -ForegroundColor Red
    Write-Host "   Expected: $dashboardPath" -ForegroundColor Yellow
    exit 1
}

Write-Host "`n[3/4] Importing dashboard to Grafana..." -ForegroundColor Yellow

try {
    # Create basic auth header
    $pair = "admin:admin123"
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($pair)
    $base64 = [System.Convert]::ToBase64String($bytes)
    $basicAuthValue = "Basic $base64"
    
    $headers = @{
        "Authorization" = $basicAuthValue
        "Content-Type" = "application/json"
    }
    
    # Import dashboard
    $body = @{
        dashboard = ($dashboardContent | ConvertFrom-Json)
        overwrite = $true
        message = "Imported via PowerShell script"
    } | ConvertTo-Json -Depth 100
    
    $importResponse = Invoke-RestMethod -Uri "$grafanaUrl/api/dashboards/db" -Method Post -Headers $headers -Body $body
    
    Write-Host "   Import Status: SUCCESS" -ForegroundColor Green
    Write-Host "   Dashboard UID: $($importResponse.uid)" -ForegroundColor Cyan
    Write-Host "   Dashboard URL: $($importResponse.url)" -ForegroundColor Cyan
    Write-Host "   Version: $($importResponse.version)" -ForegroundColor Cyan
    
    # Open in browser
    Write-Host "`n   Opening dashboard in browser..." -ForegroundColor Cyan
    Start-Process "$grafanaUrl$($importResponse.url)"
    
} catch {
    Write-Host "   ERROR: Failed to import dashboard" -ForegroundColor Red
    Write-Host "   $_" -ForegroundColor Gray
    
    if ($_.Exception.Response.StatusCode -eq 401) {
        Write-Host "   Authentication failed. Please check credentials." -ForegroundColor Yellow
    } elseif ($_.Exception.Response.StatusCode -eq 400) {
        Write-Host "   Bad request. Dashboard may already exist or have invalid format." -ForegroundColor Yellow
    }
    
    Write-Host "`n   Manual import steps:" -ForegroundColor Cyan
    Write-Host "   1. Open Grafana: $grafanaUrl" -ForegroundColor White
    Write-Host "   2. Login: admin / admin123" -ForegroundColor White
    Write-Host "   3. Click: Dashboards → Import" -ForegroundColor White
    Write-Host "   4. Upload file: $dashboardPath" -ForegroundColor White
    Write-Host "   5. Select Prometheus datasource" -ForegroundColor White
    Write-Host "   6. Click Import" -ForegroundColor White
    
    exit 1
}

Write-Host "`n[4/4] Verifying Prometheus datasource..." -ForegroundColor Yellow

try {
    $headers = @{
        "Authorization" = $basicAuthValue
    }
    
    $datasources = Invoke-RestMethod -Uri "$grafanaUrl/api/datasources" -Method Get -Headers $headers
    
    $prometheusDs = $datasources | Where-Object { $_.type -eq "prometheus" }
    
    if ($prometheusDs) {
        Write-Host "   Prometheus datasource: FOUND" -ForegroundColor Green
        Write-Host "   Name: $($prometheusDs.name)" -ForegroundColor Cyan
        Write-Host "   URL: $($prometheusDs.url)" -ForegroundColor Cyan
        Write-Host "   Access: $($prometheusDs.access)" -ForegroundColor Cyan
        
        # Test datasource
        Write-Host "   Testing datasource connection..." -ForegroundColor Cyan
        $testResponse = Invoke-RestMethod -Uri "$grafanaUrl/api/datasources/uid/$($prometheusDs.uid)/health" -Method Get -Headers $headers
        
        if ($testResponse.status -eq "OK") {
            Write-Host "   Connection: SUCCESS" -ForegroundColor Green
        } else {
            Write-Host "   Connection: UNKNOWN" -ForegroundColor Yellow
        }
    } else {
        Write-Host "   WARNING: Prometheus datasource not found!" -ForegroundColor Yellow
        Write-Host "   Please add Prometheus datasource manually:" -ForegroundColor Cyan
        Write-Host "   1. Configuration → Data Sources → Add data source" -ForegroundColor White
        Write-Host "   2. Select Prometheus" -ForegroundColor White
        Write-Host "   3. URL: http://prometheus:9090" -ForegroundColor White
        Write-Host "   4. Click Save and Test" -ForegroundColor White
    }
} catch {
    Write-Host "   WARNING: Could not verify datasource" -ForegroundColor Yellow
    Write-Host "   $_" -ForegroundColor Gray
}

Write-Host "`n╔══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║     Import Complete!                                       ║" -ForegroundColor Green
Write-Host "╚══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan

Write-Host "`nDashboard is now available at:" -ForegroundColor Cyan
Write-Host "$grafanaUrl/d/yunshu-alerts-monitor/yunshu-alerts-monitor" -ForegroundColor White

Write-Host "`nNext steps:" -ForegroundColor Cyan
Write-Host "1. Verify 19 alert rules in Prometheus" -ForegroundColor White
Write-Host "   URL: http://localhost:9090/status/rules" -ForegroundColor Cyan
Write-Host ""
Write-Host "2. View dashboard panels in Grafana" -ForegroundColor White
Write-Host "   URL: http://localhost:3000/d/yunshu-alerts-monitor" -ForegroundColor Cyan
Write-Host ""
Write-Host "3. Customize panels or add new alerts" -ForegroundColor White
Write-Host ""

Write-Host "Done!" -ForegroundColor Green
Write-Host ""
