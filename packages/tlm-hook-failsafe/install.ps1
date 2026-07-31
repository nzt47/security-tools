﻿﻿﻿﻿﻿﻿﻿﻿<#
.SYNOPSIS
    tlm-hook-failsafe 模块安装脚本（3 种模式 + 跨平台）

.DESCRIPTION
    三义校验：
    - 不易：导出函数必须为 12 个，否则安装失败
    - 变易：支持自包含 / -SourcePath / -DownloadFromGit 三种安装源
    - 简易：5 步流程，自解释输出

    安装模式：
    1. 自包含（默认）：用包内已有的 tlm-hook-failsafe.psm1（离线可用）
    2. -SourcePath <repo_root>：从本地仓库源取 scripts/dev/hook_fail_safe.psm1
    3. -DownloadFromGit <url>：git clone --depth 1 后取源

    跨平台默认 TargetPath：
    - Windows: $HOME\Documents\PowerShell\Modules\tlm-hook-failsafe
    - Linux:   $HOME/.local/share/powershell/Modules/tlm-hook-failsafe

.PARAMETER SourcePath
    本地仓库根路径（取 $SourcePath/scripts/dev/hook_fail_safe.psm1）

.PARAMETER DownloadFromGit
    git URL（clone 后取 scripts/dev/hook_fail_safe.psm1）

.PARAMETER TargetPath
    安装目标目录（默认用户级 PSModulePath）

.PARAMETER Force
    覆盖已存在的目标目录

.EXAMPLE
    .\install.ps1
    .\install.ps1 -SourcePath C:\Users\Administrator\agent
    .\install.ps1 -DownloadFromGit https://github.com/user/repo.git
    .\install.ps1 -TargetPath D:\custom\path -Force
#>
[CmdletBinding()]
param(
    [string]$SourcePath,
    [string]$DownloadFromGit,
    [string]$TargetPath,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$packageDir = $PSScriptRoot

Write-Host "=== tlm-hook-failsafe 安装 ===" -ForegroundColor Cyan

# ── 跨平台检测（PS 5.1 兼容：用 $PSVersionTable.Platform 而非 $IsLinux） ──
$isUnix = $PSVersionTable.Platform -eq 'Unix'

# ── 步骤 1: 解析源 .psm1 ──
Write-Host "[1/5] 解析模块源..." -ForegroundColor Yellow
$sourcePsm1 = $null
$tempClone = $null

if ($DownloadFromGit) {
    $tempBase = if ($env:TEMP) { $env:TEMP } elseif ($isUnix) { '/tmp' } else { $HOME }
    $tempClone = Join-Path $tempBase "tlm-hfs-$(Get-Random)"
    Write-Host "  git clone --depth 1 $DownloadFromGit -> $tempClone"
    & git clone --depth 1 $DownloadFromGit $tempClone 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] git clone 失败" -ForegroundColor Red
        exit 1
    }
    $sourcePsm1 = Join-Path $tempClone (Join-Path 'scripts' (Join-Path 'dev' 'hook_fail_safe.psm1'))
} elseif ($SourcePath) {
    $sourcePsm1 = Join-Path $SourcePath (Join-Path 'scripts' (Join-Path 'dev' 'hook_fail_safe.psm1'))
} else {
    # 自包含模式：用包内已有的快照
    $sourcePsm1 = Join-Path $packageDir "tlm-hook-failsafe.psm1"
}

if (-not (Test-Path $sourcePsm1)) {
    Write-Host "[ERROR] 源模块不存在: $sourcePsm1" -ForegroundColor Red
    exit 1
}
Write-Host "  [OK] 源: $sourcePsm1" -ForegroundColor Green

# ── 步骤 2: 解析目标路径（跨平台默认） ──
Write-Host "[2/5] 解析目标路径..." -ForegroundColor Yellow
if (-not $TargetPath) {
    if ($isUnix) {
        $TargetPath = Join-Path $HOME ".local/share/powershell/Modules/tlm-hook-failsafe"
    } else {
        $TargetPath = Join-Path $HOME "Documents\PowerShell\Modules\tlm-hook-failsafe"
    }
}
Write-Host "  目标: $TargetPath"

# ── 步骤 3: 创建目标目录（Force 处理覆盖） ──
Write-Host "[3/5] 创建目标目录..." -ForegroundColor Yellow
if (Test-Path $TargetPath) {
    if (-not $Force) {
        Write-Host "[ERROR] 目标已存在: $TargetPath（用 -Force 覆盖）" -ForegroundColor Red
        exit 1
    }
    Remove-Item -Recurse -Force $TargetPath
}
New-Item -ItemType Directory -Path $TargetPath -Force | Out-Null
Write-Host "  [OK] 已创建" -ForegroundColor Green

# ── 步骤 4: 复制 .psm1 + .psd1 ──
Write-Host "[4/5] 复制模块文件..." -ForegroundColor Yellow
Copy-Item -Path $sourcePsm1 -Destination (Join-Path $TargetPath "tlm-hook-failsafe.psm1") -Force
Copy-Item -Path (Join-Path $packageDir "tlm-hook-failsafe.psd1") -Destination $TargetPath -Force
Write-Host "  [OK] 已复制 tlm-hook-failsafe.psm1 + tlm-hook-failsafe.psd1" -ForegroundColor Green

# ── 步骤 5: Import-Module 验证导出 12 函数 ──
Write-Host "[5/5] 验证模块加载..." -ForegroundColor Yellow
try {
    Import-Module (Join-Path $TargetPath "tlm-hook-failsafe.psd1") -Force -ErrorAction Stop
    $exported = (Get-Command -Module tlm-hook-failsafe).Count
    if ($exported -ne 12) {
        throw "导出函数数=$exported, 预期 12"
    }
    Write-Host "  [OK] 模块加载成功，导出 $exported 个函数" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] 验证失败: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

# ── 清理临时 clone ──
if ($tempClone -and (Test-Path $tempClone)) {
    Remove-Item -Recurse -Force $tempClone -ErrorAction SilentlyContinue
}

# ── 完成 ──
Write-Host ""
Write-Host "[DONE] 安装成功！" -ForegroundColor Green
Write-Host "  路径: $TargetPath"
Write-Host "  使用: Import-Module tlm-hook-failsafe" -ForegroundColor Cyan
Write-Host "  提示: 若 Import-Module 失败，重启 PowerShell 会话或显式指定路径：" -ForegroundColor Gray
Write-Host "        Import-Module $TargetPath\tlm-hook-failsafe.psd1" -ForegroundColor Gray
exit 0
