<#
.SYNOPSIS
    Publish tlm-hook-failsafe to local private PSRepository using nuget.exe.

.DESCRIPTION
    Design (three-yi verification):
    - Invariant: 12 exported functions contract unchanged; GUID unchanged;
                  source of truth = scripts/dev/hook_fail_safe.psm1
    - Adaptable: -BumpVersion (patch bump) and -Force (delete old .nupkg) mutually exclusive
    - Simple: 7-step flow, each step prints [N/7]

    Why nuget.exe (not Publish-Module):
    - PowerShellGet v2.2.5 invokes `dotnet.exe pack` which requires .NET SDK.
    - This machine has only .NET Runtime, no SDK -> dotnet pack fails silently
      with exit code -2147450735 (0x80008031).
    - nuget.exe is a single zero-dependency binary; local folder repo accepts
      raw .nupkg files directly. Most minimal path.

    7 steps:
    1. Ensure nuget.exe is available
    2. Register / re-register LocalPSRepo
    3. Sync source (.psm1) -> package snapshot
    4. If -BumpVersion: bump patch in .psd1
    5. Generate .nuspec from .psd1 metadata
    6. nuget pack -> .nupkg into repo folder
    7. Find-Module + Save-Module verify 12 exports

.PARAMETER RepoPath
    Local folder repository path (default C:\PSRepo)

.PARAMETER RepoName
    PSRepository name (default LocalPSRepo)

.PARAMETER BumpVersion
    Bump patch version (1.0.0 -> 1.0.1) before publishing.
    Mutually exclusive with -Force.

.PARAMETER Force
    Delete existing .nupkg of same version before re-publishing.
    Mutually exclusive with -BumpVersion.

.EXAMPLE
    .\publish-to-local-repo.ps1
    .\publish-to-local-repo.ps1 -BumpVersion
    .\publish-to-local-repo.ps1 -Force
#>
[CmdletBinding()]
param(
    [string]$RepoPath = 'C:\PSRepo',
    [string]$RepoName = 'LocalPSRepo',
    [switch]$BumpVersion,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$packageDir = $PSScriptRoot
$psd1Path   = Join-Path $packageDir 'tlm-hook-failsafe.psd1'
$psm1Path   = Join-Path $packageDir 'tlm-hook-failsafe.psm1'
$nuspecPath = Join-Path $packageDir 'tlm-hook-failsafe.nuspec'

# Invariant: mutual exclusion
if ($BumpVersion -and $Force) {
    throw "-BumpVersion and -Force are mutually exclusive: pick bump or republish"
}

# Invariant: TLS 1.2 for any web request (PS 5.1 defaults to TLS 1.0, deprecated)
try {
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch { }

Write-Host "=== publish-to-local-repo (nuget.exe route) ===" -ForegroundColor Cyan
Write-Host "  RepoPath:    $RepoPath"
Write-Host "  RepoName:    $RepoName"
Write-Host "  BumpVersion: $BumpVersion"
Write-Host "  Force:       $Force"

# Step [1/7] Ensure nuget.exe
Write-Host "[1/7] Ensure nuget.exe..." -ForegroundColor Yellow
$nugetExe = Join-Path $env:LOCALAPPDATA 'PackageManagement\ProviderAssemblies\nuget.exe'
if (-not (Test-Path $nugetExe)) {
    $nugetDir = Split-Path $nugetExe -Parent
    if (-not (Test-Path $nugetDir)) {
        New-Item -ItemType Directory -Path $nugetDir -Force | Out-Null
    }
    Write-Host "  Downloading nuget.exe..." -ForegroundColor Gray
    Invoke-WebRequest -Uri 'https://dist.nuget.org/win-x86-commandline/latest/nuget.exe' `
        -OutFile $nugetExe -UseBasicParsing -ErrorAction Stop
}
$nugetVersion = (& $nugetExe help 2>&1 | Select-Object -First 1)
Write-Host "  [OK] $nugetExe" -ForegroundColor Green
Write-Host "       $nugetVersion" -ForegroundColor Gray

# Step [2/7] Register / re-register LocalPSRepo
Write-Host "[2/7] Register $RepoName..." -ForegroundColor Yellow
if (-not (Test-Path $RepoPath)) {
    New-Item -ItemType Directory -Path $RepoPath -Force | Out-Null
}
if (Get-PSRepository -Name $RepoName -ErrorAction SilentlyContinue) {
    Unregister-PSRepository -Name $RepoName
}
Register-PSRepository -Name $RepoName -SourceLocation $RepoPath `
    -PublishLocation $RepoPath -ScriptSourceLocation $RepoPath `
    -InstallationPolicy Trusted
Write-Host "  [OK] $RepoName -> $RepoPath" -ForegroundColor Green

# Step [3/7] Sync source .psm1 -> package snapshot (skip during -Force republish of same version)
Write-Host "[3/7] Sync source..." -ForegroundColor Yellow
$syncScript = Join-Path $packageDir 'sync-from-source.ps1'
if (Test-Path $syncScript) {
    & $syncScript
    if ($LASTEXITCODE -ne 0) { throw "sync-from-source.ps1 failed: exit $LASTEXITCODE" }
} else {
    Write-Host "  [SKIP] sync script not found" -ForegroundColor DarkGray
}

# Step [4/7] Bump version if requested
if ($BumpVersion) {
    Write-Host "[4/7] Bump patch version..." -ForegroundColor Yellow
    $manifest = Test-ModuleManifest -Path $psd1Path
    $current = [version]$manifest.Version
    $newVersion = [version]::new($current.Major, $current.Minor, $current.Build + 1)
    Update-ModuleManifest -Path $psd1Path -ModuleVersion $newVersion
    Write-Host "  [OK] $($current.ToString()) -> $($newVersion.ToString())" -ForegroundColor Green

    # Invariant: Update-ModuleManifest may drop BOM -> restore single UTF-8 BOM
    $bytes = [System.IO.File]::ReadAllBytes($psd1Path)
    $hasBom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
    if (-not $hasBom) {
        Write-Host "  [INFO] restore BOM..." -ForegroundColor Yellow
        $content = [System.IO.File]::ReadAllText($psd1Path, [System.Text.Encoding]::UTF8)
        $utf8Bom = New-Object System.Text.UTF8Encoding $true
        [System.IO.File]::WriteAllText($psd1Path, $content, $utf8Bom)
    }
} else {
    Write-Host "[4/7] Skip version bump" -ForegroundColor DarkGray
}

# Step [5/7] Generate .nuspec from .psd1 metadata
Write-Host "[5/7] Generate .nuspec..." -ForegroundColor Yellow
$manifest = Test-ModuleManifest -Path $psd1Path
$version = $manifest.Version.ToString()
$description = $manifest.Description
$author = $manifest.Author
$releaseNotes = $manifest.PrivateData.PSData.ReleaseNotes
$tags = ($manifest.PrivateData.PSData.Tags) -join ' '
# Ensure PSModule tag is present so Find-Module recognizes it as a PS module
if ($tags -notmatch '\bPSModule\b') { $tags = "PSModule $tags" }

$nuspec = @"
<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://schemas.microsoft.com/packaging/2011/08/nuspec.xsd">
  <metadata>
    <id>tlm-hook-failsafe</id>
    <version>$version</version>
    <authors>$author</authors>
    <owners>$author</owners>
    <description>$description</description>
    <releaseNotes>$releaseNotes</releaseNotes>
    <tags>$tags</tags>
    <requireLicenseAcceptance>false</requireLicenseAcceptance>
    <copyright>(c) $author. MIT license.</copyright>
    <dependencies></dependencies>
  </metadata>
  <files>
    <file src="tlm-hook-failsafe.psd1" target="" />
    <file src="tlm-hook-failsafe.psm1" target="" />
  </files>
</package>
"@
# nuspec as UTF-8 no BOM (nuget prefers no BOM for XML)
[System.IO.File]::WriteAllText($nuspecPath, $nuspec, (New-Object System.Text.UTF8Encoding $false))
Write-Host "  [OK] $nuspecPath (v$version)" -ForegroundColor Green

# Step [6/7] nuget pack
Write-Host "[6/7] nuget pack..." -ForegroundColor Yellow
if ($Force) {
    $oldPackages = Get-ChildItem -Path $RepoPath -Filter "tlm-hook-failsafe.*.nupkg" -ErrorAction SilentlyContinue
    foreach ($pkg in $oldPackages) {
        Remove-Item $pkg.FullName -Force
        Write-Host "  [DEL] $($pkg.Name)" -ForegroundColor Gray
    }
}
$packArgs = @('pack', $nuspecPath, '-OutputDirectory', $RepoPath, '-NoDefaultExcludes', '-NonInteractive')
& $nugetExe @packArgs | Out-Host
if ($LASTEXITCODE -ne 0) { throw "nuget pack failed: exit $LASTEXITCODE" }

# Verify the expected .nupkg exists
$expectedNupkg = Join-Path $RepoPath "tlm-hook-failsafe.$version.nupkg"
if (-not (Test-Path $expectedNupkg)) {
    throw "expected .nupkg not found: $expectedNupkg"
}
Write-Host "  [OK] $expectedNupkg" -ForegroundColor Green

# Step [7/7] Find-Module + Save-Module verify 12 exports
Write-Host "[7/7] Verify Find-Module + 12 exports..." -ForegroundColor Yellow
$found = Find-Module -Name tlm-hook-failsafe -Repository $RepoName -ErrorAction SilentlyContinue
if (-not $found) { throw "Find-Module miss after publish" }
Write-Host "  [OK] Find-Module: $($found.Name) v$($found.Version)" -ForegroundColor Green

$tempInstall = Join-Path $env:TEMP "tlm-publish-verify-$([guid]::NewGuid().ToString('N').Substring(0,8))"
New-Item -ItemType Directory -Path $tempInstall -Force | Out-Null
try {
    Save-Module -Name tlm-hook-failsafe -Repository $RepoName -Path $tempInstall -Force
    $psd1Installed = Get-ChildItem $tempInstall -Recurse -Filter 'tlm-hook-failsafe.psd1' | Select-Object -First 1
    if (-not $psd1Installed) { throw "Save-Module did not produce .psd1" }
    Import-Module $psd1Installed.FullName -Force -ErrorAction Stop
    $mod = Get-Module tlm-hook-failsafe
    $expected = @(
        'Get-HookContent','Write-HookNoBom','Write-FileWithBom',
        'Backup-ExistingHook','Test-HookUpToDate',
        'Set-SourceRepoEnv','Test-SourceRepoEnv',
        'Resolve-GitDir','Test-HookMarker',
        'Test-HookExecutable','Repair-HookPermission','Invoke-SafeHookWrite'
    )
    $missing = $expected | Where-Object { $_ -notin $mod.ExportedCommands.Keys }
    if ($missing) { throw "missing exported functions: $($missing -join ', ')" }
    Write-Host "  [OK] 12 exported functions verified" -ForegroundColor Green
    Remove-Module tlm-hook-failsafe -Force -ErrorAction SilentlyContinue
} finally {
    Remove-Item -Recurse -Force $tempInstall -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "[DONE] published to $RepoName" -ForegroundColor Green
Write-Host "  Install: Install-Module tlm-hook-failsafe -Repository $RepoName" -ForegroundColor Cyan
exit 0
