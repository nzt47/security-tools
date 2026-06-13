Write-Host "`n=== Yunshu Monitoring Verification ===" -ForegroundColor Cyan

$pass = 0
$fail = 0

Write-Host "`n1. Prometheus Health" -ForegroundColor Cyan
try {
    $r = curl.exe -s http://localhost:9090/-/healthy
    if ($r -match "Healthy") { Write-Host "   PASS: Prometheus healthy"; $pass++ }
    else { Write-Host "   FAIL: Not healthy"; $fail++ }
} catch { Write-Host "   FAIL: Not accessible"; $fail++ }

Write-Host "`n2. Alert Rules" -ForegroundColor Cyan
try {
    $rules = curl.exe -s http://localhost:9090/api/v1/rules | ConvertFrom-Json
    if ($rules.data.groups) {
        $c = 0; foreach ($g in $rules.data.groups) { $c += $g.rules.Count }
        Write-Host "   PASS: $c rules loaded"
        $pass++
        if ($c -lt 19) { Write-Host "   WARN: Expected 19 rules"; Write-Host "   Fix: Check monitoring/alerts.yml" }
    } else { Write-Host "   FAIL: No rules"; $fail++ }
} catch { Write-Host "   FAIL: Error"; $fail++ }

Write-Host "`n3. Grafana Health" -ForegroundColor Cyan
try {
    $r = curl.exe -s http://localhost:3000/api/health
    if ($r -match "ok") { Write-Host "   PASS: Grafana healthy"; $pass++ }
    else { Write-Host "   FAIL: Not healthy"; $fail++ }
} catch { Write-Host "   FAIL: Not accessible"; $fail++ }

Write-Host "`n4. Datasources" -ForegroundColor Cyan
try {
    $pair = "admin:admin123"
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($pair)
    $base64 = [System.Convert]::ToBase64String($bytes)
    $ds = curl.exe -s -H "Authorization: Basic $base64" http://localhost:3000/api/datasources | ConvertFrom-Json
    if ($ds) {
        Write-Host "   PASS: $($ds.Count) datasources"; $pass++
        $prom = $ds | Where-Object { $_.type -eq "prometheus" }
        if ($prom) { Write-Host "   PASS: Prometheus configured"; $pass++ }
        else { Write-Host "   WARN: No Prometheus"; Write-Host "   Fix: Add http://prometheus:9090" }
    } else { Write-Host "   FAIL: None"; $fail++ }
} catch { Write-Host "   FAIL: Error"; $fail++ }

Write-Host "`n5. Dashboards" -ForegroundColor Cyan
try {
    $pair = "admin:admin123"
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($pair)
    $base64 = [System.Convert]::ToBase64String($bytes)
    $dbs = curl.exe -s -H "Authorization: Basic $base64" "http://localhost:3000/api/search?type=dash-db" | ConvertFrom-Json
    if ($dbs) {
        Write-Host "   PASS: $($dbs.Count) dashboards"; $pass++
        $yunshu = $dbs | Where-Object { $_.title -match "Yunshu" }
        if ($yunshu) { Write-Host "   PASS: Yunshu imported"; $pass++ }
        else { Write-Host "   WARN: Not imported"; Write-Host "   Fix: Import yunshu-alerts-monitor.json" }
    }
} catch { Write-Host "   FAIL: Error"; $fail++ }

Write-Host "`n=== Summary ===" -ForegroundColor Cyan
Write-Host "PASS: $pass" -ForegroundColor Green
Write-Host "FAIL: $fail" -ForegroundColor Red

if ($fail -eq 0) { Write-Host "`nAll checks passed" -ForegroundColor Green }
else {
    Write-Host "`nFix suggestions:" -ForegroundColor Yellow
    Write-Host "1. docker ps" -ForegroundColor White
    Write-Host "2. docker logs yunshu-prometheus" -ForegroundColor White
    Write-Host "3. docker-compose -f docker-compose.monitoring.yml restart" -ForegroundColor White
}
