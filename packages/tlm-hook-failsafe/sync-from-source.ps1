<#
.SYNOPSIS
    开发用同步脚本：从源 scripts/dev/hook_fail_safe.psm1 同步到包内快照

.DESCRIPTION
    三义校验：
    - 不易：真相源唯一 = scripts/dev/hook_fail_safe.psm1，本脚本只读复制
    - 变易：可重复运行（幂等），同步后反向校验 12 个导出函数
    - 简易：单职责，无参数

    何时运行：
    - 修改 scripts/dev/hook_fail_safe.psm1 后
    - 新增/删除导出函数后
    - 发布新版本前

.EXAMPLE
    .\sync-from-source.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$packageDir = $PSScriptRoot
$repoRoot   = (Resolve-Path (Join-Path $packageDir "..\..")).Path
$sourcePsm1 = Join-Path $repoRoot "scripts\dev\hook_fail_safe.psm1"
$targetPsm1 = Join-Path $packageDir "tlm-hook-failsafe.psm1"

Write-Host "=== sync-from-source ===" -ForegroundColor Cyan
Write-Host "  source: $sourcePsm1"
Write-Host "  target: $targetPsm1"

if (-not (Test-Path $sourcePsm1)) {
    throw "source file not found: $sourcePsm1"
}

# 1. copy (source .psm1 is UTF-8 with BOM, Copy-Item preserves encoding)
Copy-Item -Path $sourcePsm1 -Destination $targetPsm1 -Force
Write-Host "  [OK] copied" -ForegroundColor Green

# 1.1 copy LICENSE from repo root to package (PSGallery license metadata 要求)
#      不易：真相源 = 仓库根 LICENSE，sync 同步到包目录供 nuget pack 包含
$sourceLicense = Join-Path $repoRoot "LICENSE"
$targetLicense = Join-Path $packageDir "LICENSE"
if (Test-Path $sourceLicense) {
    Copy-Item -Path $sourceLicense -Destination $targetLicense -Force
    Write-Host "  [OK] LICENSE copied" -ForegroundColor Green
} else {
    Write-Host "  [WARN] LICENSE not found at $sourceLicense (PSGallery license 警告将出现)" -ForegroundColor Yellow
}

# 2. reverse verify: 16 exported functions (12 original + 3 exit code resolution + 1 pre-push)
$expected = @(
    'Get-HookContent','Get-PrePushContent','Write-HookNoBom','Write-FileWithBom',
    'Backup-ExistingHook','Test-HookUpToDate',
    'Set-SourceRepoEnv','Test-SourceRepoEnv',
    'Resolve-GitDir','Test-HookMarker',
    'Test-HookExecutable','Repair-HookPermission','Invoke-SafeHookWrite',
    'Get-HookExitCodeMap','Resolve-HookExitCode','Invoke-HookWithCapture'
)

# load module and check exports (use fresh module name to avoid conflict)
Import-Module $targetPsm1 -Force -ErrorAction SilentlyContinue
$module = Get-Module -Name 'tlm-hook-failsafe' -ErrorAction SilentlyContinue
if (-not $module) {
    # try by file path (when module name differs)
    $module = Get-Module -All | Where-Object { $_.Path -eq $targetPsm1 } | Select-Object -First 1
}

$exported = @()
if ($module) {
    $exported = @($module.ExportedCommands.Keys)
}

$missing = $expected | Where-Object { $_ -notin $exported }
$extra   = $exported | Where-Object { $_ -notin $expected }

if ($missing) {
    Write-Host "  [FAIL] missing functions: $($missing -join ', ')" -ForegroundColor Red
    throw "function list mismatch after sync"
}

Write-Host "  [OK] verified $($expected.Count) exported functions" -ForegroundColor Green
if ($extra) {
    Write-Host "  [INFO] extra functions: $($extra -join ', ')" -ForegroundColor Yellow
}

# 3. hash consistency report
# 变易：用 .NET SHA256 而非 Get-FileHash —— Git bash 触发的 post-commit 场景下
#      $env:PSModulePath 指向 pwsh 7 模块路径，Windows PowerShell 5.1 无法加载
#      Microsoft.PowerShell.Utility，Get-FileHash 不可用。.NET 类型不依赖模块加载。
function Get-Sha256Hex([string]$Path) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $stream = [System.IO.File]::OpenRead($Path)
        try {
            $bytes = $sha.ComputeHash($stream)
            return (($bytes | ForEach-Object { $_.ToString("x2") }) -join "")
        } finally {
            $stream.Dispose()
        }
    } finally {
        $sha.Dispose()
    }
}

$srcHash = Get-Sha256Hex $sourcePsm1
$pkgHash = Get-Sha256Hex $targetPsm1
if ($srcHash -eq $pkgHash) {
    Write-Host "  [OK] hash match: $srcHash" -ForegroundColor Green
} else {
    Write-Host "  [WARN] hash mismatch (encoding diff possible):" -ForegroundColor Yellow
    Write-Host "        source: $srcHash"
    Write-Host "        package: $pkgHash"
}

Write-Host ""
Write-Host "[DONE] sync complete" -ForegroundColor Green
exit 0
