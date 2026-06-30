$taskName = "YunshuDailyQualityReport"
$scriptPath = "C:\Users\Administrator\agent\scripts\run_daily_report.bat"
$logPath = "C:\Users\Administrator\agent\test_reports\scheduler.log"

schtasks /Create `
    /TN "$taskName" `
    /TR "$scriptPath" `
    /SC DAILY `
    /ST 02:00 `
    /RL HIGHEST `
    /F `
    /V1 `
    /SD 2026-06-25 `
    /RU SYSTEM

Write-Host "Scheduled task created successfully!"
Write-Host "Task Name: $taskName"
Write-Host "Trigger: Daily at 2:00 AM"
Write-Host "Script: $scriptPath"
Write-Host "Log: $logPath"

schtasks /Query /TN "$taskName" /FO LIST