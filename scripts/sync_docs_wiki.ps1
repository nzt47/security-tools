<#
# sync_docs_wiki.ps1 - Sync docs/wiki markdown files to GitHub Wiki
# ------------------------------------------------------
# Prereq: GitHub repo must have Wiki enabled. The wiki is a separate
#         git repo: <owner>/<repo>.wiki, default branch "master".
#
# Usage (Windows PowerShell):
#   ./scripts/sync_docs_wiki.ps1                         # clone to .wiki and push
#   ./scripts/sync_docs_wiki.ps1 -NoPush                 # commit only, no push
#   ./scripts/sync_docs_wiki.ps1 -RepoUrl "git@github.com:nzt47/security-tools.wiki.git"
#
# Notes:
#   - GitHub wiki page name = relative file path without .md extension
#   - Idempotent: overwrite same-name pages on each run
#   - Removed pages are NOT deleted automatically (manual git rm, see end)
#   - Pushes to "master" branch (fixed branch name for GitHub Wiki)
#>
param(
    [string]$SourceDir = "$PSScriptRoot\..\docs\wiki",
    [string]$WikiDir   = "$PSScriptRoot\..\.wiki",
    [string]$RepoUrl   = "https://github.com/nzt47/security-tools.wiki.git",
    [string]$Branch    = "master",
    [switch]$NoPush
)

$ErrorActionPreference = "Stop"
$SourceDir = (Resolve-Path $SourceDir).Path

# 1) Ensure wiki repo is cloned
if (-not (Test-Path "$WikiDir\.git")) {
    Write-Host "[wiki] cloning wiki repo: $RepoUrl"
    git clone $RepoUrl $WikiDir
    if ($LASTEXITCODE -ne 0) { throw "wiki clone failed (is Wiki enabled and credentials valid?)" }
}

# 2) Copy all .md from docs/wiki, keeping relative path as wiki page structure
$copied = 0
Get-ChildItem $SourceDir -Filter "*.md" -Recurse | ForEach-Object {
    $rel = $_.FullName.Substring($SourceDir.Length).TrimStart('\', '/')
    $dest = Join-Path $WikiDir $rel
    New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
    Copy-Item $_.FullName $dest -Force
    Write-Host "[wiki] synced: $rel"
    $copied++
}
Write-Host "[wiki] copied $copied markdown file(s)"

# 3) Commit and push (GitHub Wiki default branch: master)
git -C $WikiDir add -A
if ($LASTEXITCODE -ne 0) { throw "git add failed" }
git -C $WikiDir commit -m "docs(wiki): sync docs/wiki $(Get-Date -Format 'yyyy-MM-dd HH:mm')" | Out-Null

if ($NoPush) {
    Write-Host "[wiki] (-NoPush) committed, not pushed. Run: git -C $WikiDir push origin $Branch"
} else {
    git -C $WikiDir push origin $Branch
    if ($LASTEXITCODE -ne 0) { throw "push failed" }
    Write-Host "[wiki] pushed to GitHub Wiki (master)"
}

# To delete pages no longer present in docs/wiki (manual, non-destructive by design):
#   git -C $WikiDir rm <page>.md ; git -C $WikiDir commit -m "remove page" ; git -C $WikiDir push origin master
