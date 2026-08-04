<#
.SYNOPSIS
    Publish tlm-hook-failsafe to PowerShell Gallery (PSGallery).

.DESCRIPTION
    Design (three-yi verification):
    - Invariant: source of truth = scripts/dev/hook_fail_safe.psm1;
                  version comes from .psd1 (no magic bump in CI);
                  same-version re-publish blocked (PSGallery rejects)
    - Adaptable: -DryRun generates .nupkg without push; -SkipVersionCheck bypasses pre-check
    - Simple: reuses nuget.exe + .nuspec generation logic from publish-to-local-repo.ps1

    Why nuget.exe (not Publish-Module):
    - Consistency with publish-to-local-repo.ps1 (same nuspec template, no metadata drift)
    - nuget push is more stable than PowerShellGet v3 API for PSGallery

    Flow:
    1. Sync source .psm1 -> package snapshot
    2. Read version from .psd1
    3. Pre-check: PSGallery already has this version? (skip with -SkipVersionCheck)
    4. Generate .nuspec from .psd1 metadata
    5. nuget pack -> .nupkg (to package dir, same as local repo)
    6. If -DryRun: stop here, report .nupkg path
    7. Else: nuget push to PSGallery with API key

.PARAMETER NuGetApiKey
    PSGallery NuGet API key (from GitHub Secrets in CI).

.PARAMETER DryRun
    Generate .nupkg but do not push to PSGallery.

.PARAMETER SkipVersionCheck
    Skip the "same version already on PSGallery" pre-check.

.EXAMPLE
    # Dry-run (CI validate job)
    .\publish-to-psgallery.ps1 -NuGetApiKey 'dummy' -DryRun

    # Real publish (CI publish job, with secrets.PSGALLERY_API_KEY)
    .\publish-to-psgallery.ps1 -NuGetApiKey $env:PSGALLERY_API_KEY
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$NuGetApiKey,
    [switch]$DryRun,
    [switch]$SkipVersionCheck
)

$ErrorActionPreference = "Stop"
$packageDir = $PSScriptRoot
$psd1Path   = Join-Path $packageDir 'tlm-hook-failsafe.psd1'
$nuspecPath = Join-Path $packageDir 'tlm-hook-failsafe.nuspec'

# Invariant: TLS 1.2 for any web request (PS 5.1 defaults to TLS 1.0, deprecated)
try {
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch { }

Write-Host "=== publish-to-psgallery (nuget.exe route) ===" -ForegroundColor Cyan
Write-Host "  DryRun:          $DryRun"
Write-Host "  SkipVersionCheck: $SkipVersionCheck"

# Step [1/7] Ensure nuget.exe (reuse local-repo's download location)
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
Write-Host "  [OK] $nugetExe" -ForegroundColor Green

# Step [2/7] Sync source .psm1 -> package snapshot
Write-Host "[2/7] Sync source..." -ForegroundColor Yellow
$syncScript = Join-Path $packageDir 'sync-from-source.ps1'
if (Test-Path $syncScript) {
    & $syncScript
    if ($LASTEXITCODE -ne 0) { throw "sync-from-source.ps1 failed: exit $LASTEXITCODE" }
} else {
    Write-Host "  [SKIP] sync script not found" -ForegroundColor DarkGray
}

# Step [3/7] Read version from .psd1
Write-Host "[3/7] Read version..." -ForegroundColor Yellow
$manifest = Test-ModuleManifest -Path $psd1Path
$version = $manifest.Version.ToString()
Write-Host "  [OK] version = $version" -ForegroundColor Green

# Step [4/7] Pre-check: PSGallery already has this version?
if (-not $SkipVersionCheck) {
    Write-Host "[4/7] Pre-check PSGallery existing version..." -ForegroundColor Yellow
    try {
        $existing = Find-Module -Name tlm-hook-failsafe -Repository PSGallery -ErrorAction Stop
        if ($existing.Version -eq $manifest.Version) {
            throw "version $version already on PSGallery; bump .psd1 first (PSGallery rejects same-version re-publish)"
        }
        Write-Host "  [OK] PSGallery has v$($existing.Version), publishing v$version" -ForegroundColor Green
    } catch {
        # Find-Module fails when module not yet on PSGallery (first publish) - that's OK
        if ($_.Exception.Message -match 'No match was found') {
            Write-Host "  [OK] module not yet on PSGallery (first publish)" -ForegroundColor Green
        } else {
            # Re-throw if it's the "same version" error we raised above
            if ($_.Exception.Message -match 'already on PSGallery') { throw }
            # Other errors (network, auth) - warn but continue (DryRun can still proceed)
            Write-Host "  [WARN] pre-check failed: $($_.Exception.Message)" -ForegroundColor Yellow
            Write-Host "        continuing (DryRun=$DryRun)" -ForegroundColor Gray
        }
    }
} else {
    Write-Host "[4/7] Skip version pre-check (-SkipVersionCheck)" -ForegroundColor DarkGray
}

# Step [5/7] Generate .nuspec from .psd1 metadata
Write-Host "[5/7] Generate .nuspec..." -ForegroundColor Yellow
$description   = $manifest.Description
$author        = $manifest.Author
$releaseNotes  = $manifest.PrivateData.PSData.ReleaseNotes
$tags          = ($manifest.PrivateData.PSData.Tags) -join ' '
# Ensure PSModule tag is present so Find-Module recognizes it as a PS module
if ($tags -notmatch '\bPSModule\b') { $tags = "PSModule $tags" }
# 不易：使用 <license type="expression">MIT</license> 替代 <licenseUrl>（NuGet 4.9.2+ 推荐）
#       两者不能共存（NuGet 报 licenseUrl and license elements cannot be used together）
#       LicenseUri 仍保留在 .psd1（PSGallery UI 显示用），但不写入 .nuspec
$licenseUri    = $manifest.PrivateData.PSData.LicenseUri
if (-not $licenseUri) {
    Write-Host "  [WARN] .psd1 LicenseUri is empty; PSGallery UI license link will be missing" -ForegroundColor Yellow
}
# 不易：XML 转义 releaseNotes/description，避免 < > & 等字符破坏 .nuspec 的 XML 结构
#       （ReleaseNotes 中如包含 <license> 等文本会被 XML 解析器误认为标签）
$releaseNotes  = $releaseNotes -replace '&', '&amp;' -replace '<', '&lt;' -replace '>', '&gt;'
$description   = $description  -replace '&', '&amp;' -replace '<', '&lt;' -replace '>', '&gt;'

$nuspec = @"
<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://schemas.microsoft.com/packaging/2011/08/nuspec.xsd">
  <metadata>
    <id>tlm-hook-failsafe</id>
    <version>$version</version>
    <authors>$author</authors>
    <owners>$author</owners>
    <license type="expression">MIT</license>
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

# Step [6/7] nuget pack -> .nupkg to package dir
Write-Host "[6/7] nuget pack..." -ForegroundColor Yellow
$packArgs = @('pack', $nuspecPath, '-OutputDirectory', $packageDir, '-NoDefaultExcludes', '-NonInteractive')
& $nugetExe @packArgs | Out-Host
if ($LASTEXITCODE -ne 0) { throw "nuget pack failed: exit $LASTEXITCODE" }

$expectedNupkg = Join-Path $packageDir "tlm-hook-failsafe.$version.nupkg"
if (-not (Test-Path $expectedNupkg)) {
    throw "expected .nupkg not found: $expectedNupkg"
}
$nupkgSize = (Get-Item $expectedNupkg).Length
Write-Host "  [OK] $expectedNupkg ($nupkgSize bytes)" -ForegroundColor Green

# Step [7/7] Push to PSGallery (or stop for DryRun)
if ($DryRun) {
    Write-Host "[7/7] [DryRun] skip push to PSGallery" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "[DONE] DryRun complete" -ForegroundColor Green
    Write-Host "  .nupkg: $expectedNupkg" -ForegroundColor Cyan
    Write-Host "  To publish: remove -DryRun and provide real NuGetApiKey" -ForegroundColor Gray
    exit 0
}

Write-Host "[7/7] Push to PSGallery..." -ForegroundColor Yellow
$psgallerySource = 'https://www.powershellgallery.com/api/v2/package'
$pushArgs = @('push', $expectedNupkg, '-Source', $psgallerySource, '-ApiKey', $NuGetApiKey, '-NonInteractive', '-Timeout', '300')
& $nugetExe @pushArgs | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "nuget push failed: exit $LASTEXITCODE (check API key / network / PSGallery rate limit)"
}

# Verify publish
Write-Host "  Verifying..." -ForegroundColor Gray
Start-Sleep -Seconds 5  # PSGallery indexing delay
try {
    $published = Find-Module -Name tlm-hook-failsafe -Repository PSGallery -ErrorAction Stop
    if ($published.Version -eq $manifest.Version) {
        Write-Host "  [OK] PSGallery now has v$($published.Version)" -ForegroundColor Green
    } else {
        Write-Host "  [WARN] PSGallery version = $($published.Version), expected $version (indexing delay?)" -ForegroundColor Yellow
    }
} catch {
    Write-Host "  [WARN] post-publish verification failed: $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "        (indexing delay; check manually: Find-Module tlm-hook-failsafe -Repository PSGallery)" -ForegroundColor Gray
}

Write-Host ""
Write-Host "[DONE] published to PSGallery" -ForegroundColor Green
Write-Host "  Install: Install-Module tlm-hook-failsafe -Repository PSGallery" -ForegroundColor Cyan
exit 0
