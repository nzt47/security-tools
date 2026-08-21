<#
# schedule_wiki_sync.ps1 - Register a daily Windows Task to sync docs/wiki to GitHub Wiki
# ------------------------------------------------------
# Creates a scheduled task that runs sync_docs_wiki.ps1 every day at a given time.
#
# Usage (run as Administrator, or CurrentUser tasks work without elevation):
#   ./scripts/schedule_wiki_sync.ps1                     # default: task "YunshuSyncDocsWiki" at 01:00
#   ./scripts/schedule_wiki_sync.ps1 -Time "02:30"       # custom time
#   ./scripts/schedule_wiki_sync.ps1 -TaskName "MyWikiSync"
#
# Useful commands:
#   schtasks /Query /TN YunshuSyncDocsWiki              # show task config
#   schtasks /Run /TN YunshuSyncDocsWiki                # run now (test)
#   schtasks /Delete /TN YunshuSyncDocsWiki /F          # remove task
#>
param(
    [string]$TaskName   = "YunshuSyncDocsWiki",
    [string]$Time       = "01:00",
    [string]$ScriptPath = "$PSScriptRoot\sync_docs_wiki.ps1"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path $ScriptPath)) { throw "sync script not found: $ScriptPath" }

# Quote the inner script path for the schtasks /TR argument
$cmd = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + (Resolve-Path $ScriptPath).Path + '"'

Write-Host "[task] registering '$TaskName' daily at $Time"
schtasks /Create /TN $TaskName /SC DAILY /ST $Time /TR $cmd /F
if ($LASTEXITCODE -ne 0) { throw "schtasks /Create failed" }

Write-Host "[task] verifying..."
schtasks /Query /TN $TaskName

Write-Host ""
Write-Host "Done. The sync runs daily at $Time (task: $TaskName)."
Write-Host "Test now:   schtasks /Run /TN $TaskName"
Write-Host "Remove:     schtasks /Delete /TN $TaskName /F"
