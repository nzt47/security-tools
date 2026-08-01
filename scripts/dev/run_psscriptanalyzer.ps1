<#
.SYNOPSIS
    PSScriptAnalyzer 静态分析包装脚本 (pre-commit hook + CI 共用)

.DESCRIPTION
    扫描 scripts/dev/ 下的 PowerShell 脚本，发现指定 Severity 级别问题时 exit 1。
    - 自动检测并安装 PSScriptAnalyzer 模块 (本地 + CI ubuntu-latest 兼容)
    - 默认 Severity=Error 只拦截严重问题，避免噪声阻断开发
    - pre-commit 与 CI 共用同一份逻辑，确保本地与远程检查一致

.PARAMETER Path
    扫描路径 (相对项目根，默认 scripts/dev)

.PARAMETER Severity
    严重级别: Error(默认) / Warning / Information
    Error 只阻断严重问题；Warning 适合严格模式；Information 输出全部

.EXAMPLE
    pwsh -File scripts/dev/run_psscriptanalyzer.ps1
    pwsh -File scripts/dev/run_psscriptanalyzer.ps1 -Severity Warning
    pwsh -File scripts/dev/run_psscriptanalyzer.ps1 -Path scripts/ -Severity Error
#>
[CmdletBinding()]
param(
    [string]$Path = "scripts/dev",
    [ValidateSet("Error", "Warning", "Information")]
    [string]$Severity = "Error"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $ProjectRoot

# 【不易】扫描路径不存在时静默退出(0)，不阻断提交
# 场景: 新克隆仓库尚无 scripts/dev/，或路径参数手误
$targetPath = Join-Path $ProjectRoot $Path
if (-not (Test-Path $targetPath)) {
    Write-Host "[SKIP] 扫描路径不存在: $Path" -ForegroundColor Yellow
    exit 0
}

# 【变易】自动检测并安装 PSScriptAnalyzer
# 本地首次运行或 CI ubuntu-latest 需要安装；已安装则跳过
$module = Get-Module -ListAvailable PSScriptAnalyzer -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $module) {
    Write-Host "[INSTALL] 安装 PSScriptAnalyzer (首次运行)..." -ForegroundColor Cyan
    try {
        Install-Module PSScriptAnalyzer -Force -Scope CurrentUser -AcceptLicense -ErrorAction Stop
    } catch {
        Write-Host "[ERROR] PSScriptAnalyzer 安装失败: $_" -ForegroundColor Red
        Write-Host "  手动安装: Install-Module PSScriptAnalyzer -Force -Scope CurrentUser" -ForegroundColor Yellow
        exit 1
    }
    $module = Get-Module -ListAvailable PSScriptAnalyzer | Select-Object -First 1
}
Write-Host "[INFO] PSScriptAnalyzer v$($module.Version) | 扫描: $Path | Severity: $Severity" -ForegroundColor Cyan

# 【不易】运行静态分析，-Recurse 递归扫描子目录
$results = Invoke-ScriptAnalyzer -Path $targetPath -Severity $Severity -Recurse -ErrorAction SilentlyContinue

if (-not $results -or $results.Count -eq 0) {
    Write-Host "[PASS] 无 $Severity 级别问题" -ForegroundColor Green
    exit 0
}

# 【简易】输出问题详情: 文件:行号 消息 [规则名]
Write-Host "[FAIL] 发现 $($results.Count) 个 $Severity 级别问题:" -ForegroundColor Red
$results | ForEach-Object {
    $relPath = $null
    try {
        $relPath = (Resolve-Path $_.ScriptPath -Relative) -replace '^\.\\', '' -replace '\\', '/'
    } catch {
        $relPath = $_.ScriptName
    }
    Write-Host ("  {0}:{1} {2} [{3}]" -f $relPath, $_.Line, $_.Message, $_.RuleName) -ForegroundColor Yellow
}
exit 1
