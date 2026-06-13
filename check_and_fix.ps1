Write-Host "`n=== Checking Configuration Files ===" -ForegroundColor Cyan

Write-Host "`n1. Checking prometheus.yml..." -ForegroundColor Yellow
if (Test-Path "monitoring\prometheus.yml") {
    Write-Host "   File exists: YES" -ForegroundColor Green
    $content = Get-Content "monitoring\prometheus.yml" -Raw
    if ($content -match "rule_files:") {
        Write-Host "   rule_files section: EXISTS" -ForegroundColor Green
        if ($content -match "alerts.yml") {
            Write-Host "   alerts.yml reference: FOUND" -ForegroundColor Green
        } else {
            Write-Host "   alerts.yml reference: MISSING" -ForegroundColor Red
        }
    } else {
        Write-Host "   rule_files section: MISSING" -ForegroundColor Red
    }
} else {
    Write-Host "   File exists: NO" -ForegroundColor Red
}

Write-Host "`n2. Checking alerts.yml..." -ForegroundColor Yellow
if (Test-Path "monitoring\alerts.yml") {
    Write-Host "   File exists: YES" -ForegroundColor Green
    $lines = (Get-Content "monitoring\alerts.yml").Count
    Write-Host "   Lines count: $lines" -ForegroundColor Green
    
    $content = Get-Content "monitoring\alerts.yml" -Raw
    if ($content -match "groups:") {
        Write-Host "   groups section: EXISTS" -ForegroundColor Green
    } else {
        Write-Host "   groups section: MISSING" -ForegroundColor Red
    }
    
    $ruleCount = ([regex]::Matches($content, "- alert:")).Count
    Write-Host "   Alert rules count: $ruleCount" -ForegroundColor Green
} else {
    Write-Host "   File exists: NO" -ForegroundColor Red
}

Write-Host "`n3. Checking Docker containers..." -ForegroundColor Yellow
try {
    $containers = docker ps --format "{{.Names}}"
    if ($containers -match "prometheus") {
        Write-Host "   Prometheus container: RUNNING" -ForegroundColor Green
    } else {
        Write-Host "   Prometheus container: NOT RUNNING" -ForegroundColor Red
    }
} catch {
    Write-Host "   Docker check: FAILED" -ForegroundColor Red
}

Write-Host "`n4. Restarting Prometheus..." -ForegroundColor Yellow
try {
    docker-compose -f docker-compose.monitoring.yml restart prometheus
    Write-Host "   Restart: SUCCESS" -ForegroundColor Green
    Write-Host "   Waiting 15 seconds for startup..." -ForegroundColor Cyan
    Start-Sleep -Seconds 15
} catch {
    Write-Host "   Restart: FAILED" -ForegroundColor Red
}

Write-Host "`n5. Verifying alert rules..." -ForegroundColor Yellow
try {
    $rules = curl.exe -s "http://localhost:9090/api/v1/rules" | ConvertFrom-Json
    if ($rules.data.groups) {
        $count = 0
        foreach ($g in $rules.data.groups) { $count += $g.rules.Count }
        Write-Host "   Rules loaded: $count" -ForegroundColor Green
        
        if ($count -ge 19) {
            Write-Host "   Status: SUCCESS - All rules loaded" -ForegroundColor Green
        } else {
            Write-Host "   Status: PARTIAL - Expected 19+, got $count" -ForegroundColor Yellow
        }
    } else {
        Write-Host "   Rules loaded: NONE" -ForegroundColor Red
        Write-Host "   Status: FAILED" -ForegroundColor Red
    }
} catch {
    Write-Host "   Verification: FAILED" -ForegroundColor Red
}

Write-Host "`nDone!" -ForegroundColor Cyan
