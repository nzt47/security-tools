<#
# enable_github_wiki.ps1 - Detect and enable GitHub repo Wiki (idempotent)
# ------------------------------------------------------
# Usage:
#   $env:GITHUB_TOKEN = "ghp_xxx"   # or use gh CLI auth
#   ./scripts/enable_github_wiki.ps1 -Owner nzt47 -Repo security-tools
#
# Behavior:
#   - If repo has_wiki is already true -> print "already enabled" and exit 0
#   - Otherwise PATCH has_wiki=true and verify
# Token: uses $env:GITHUB_TOKEN (or -Token param). Requires repo write scope.
#>
param(
    [string]$Owner = "nzt47",
    [string]$Repo  = "security-tools",
    [string]$Token = $env:GITHUB_TOKEN
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrEmpty($Token)) { throw "GITHUB_TOKEN is required (set env GITHUB_TOKEN or pass -Token)" }

$headers = @{
    Authorization = "Bearer $Token"
    Accept        = "application/vnd.github+json"
    "X-GitHub-Api-Version" = "2022-11-28"
}
$uri = "https://api.github.com/repos/$Owner/$Repo"

# 1) Detect current state
$repo = Invoke-RestMethod -Uri $uri -Headers $headers -Method Get
Write-Host "[wiki] repo=$Owner/$Repo has_wiki=$($repo.has_wiki)"

if ($repo.has_wiki) {
    Write-Host "[wiki] Wiki already enabled, skip."
    exit 0
}

# 2) Enable Wiki
Write-Host "[wiki] enabling Wiki..."
$null = Invoke-RestMethod -Uri $uri -Headers $headers -Method Patch -ContentType "application/json" -Body '{"has_wiki": true}'

# 3) Verify
$after = Invoke-RestMethod -Uri $uri -Headers $headers -Method Get
if ($after.has_wiki) {
    Write-Host "[wiki] Wiki enabled successfully."
} else {
    throw "Wiki enable failed (verify token scope: repo)."
}
