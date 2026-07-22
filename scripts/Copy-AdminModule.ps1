<#
.SYNOPSIS
    一键复制 AdminDependencyChecker 模块到目标项目

.DESCRIPTION
    把 AdminDependencyChecker.psd1 + .psm1 + README.md 复制到目标项目的
    Modules\AdminDependencyChecker\ 目录, 命名遵循 PowerShell 模块解析规则
    (目录名 = 文件名 = 模块名).

    复制后用户可:
        Import-Module .\Modules\AdminDependencyChecker\AdminDependencyChecker.psd1
    或 (若 Modules 目录在 $PSModulePath 中):
        Import-Module AdminDependencyChecker

.PARAMETER Destination
    目标根目录. 模块会被复制到 <Destination>\Modules\AdminDependencyChecker\.
    默认: 当前目录

.PARAMETER Force
    覆盖已存在的目标目录.

.EXAMPLE
    .\scripts\Copy-AdminModule.ps1 -Destination D:\OtherProject
    # 复制到 D:\OtherProject\Modules\AdminDependencyChecker\

.EXAMPLE
    .\scripts\Copy-AdminModule.ps1 -Destination . -Force
    # 复制到 .\Modules\AdminDependencyChecker\ (覆盖已存在)
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [string]$Destination = '.',

    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$scriptRoot = $PSScriptRoot
$moduleName = 'AdminDependencyChecker'

# 必须复制的 3 个文件 (模块清单 + 实现 + README)
$filesToCopy = @(
    "$moduleName.psd1",
    "$moduleName.psm1",
    "$moduleName.README.md"
)

# 源文件校验: 任一缺失直接报错, 不允许半包复制
foreach ($f in $filesToCopy) {
    $src = Join-Path $scriptRoot $f
    if (-not (Test-Path $src)) {
        Write-Host "❌ 缺少源文件: $src" -ForegroundColor Red
        exit 1
    }
}

# 目标路径: <Destination>\Modules\AdminDependencyChecker\
$destRoot = Join-Path $Destination 'Modules'
$destModuleDir = Join-Path $destRoot $moduleName

# ShouldProcess 确认 (危险操作: 覆盖现有模块目录)
$target = "复制 $moduleName 模块到 $destModuleDir"
if (-not $PSCmdlet.ShouldProcess($target, "Copy module files")) {
    Write-Host "已取消 (用户拒绝或 -WhatIf)" -ForegroundColor Gray
    return
}

# 创建目标目录
if ((Test-Path $destModuleDir) -and -not $Force) {
    Write-Host "❌ 目标目录已存在: $destModuleDir" -ForegroundColor Red
    Write-Host "   使用 -Force 覆盖, 或先手动删除." -ForegroundColor Yellow
    exit 1
}

if (-not (Test-Path $destRoot)) {
    New-Item -ItemType Directory -Path $destRoot -Force | Out-Null
    Write-Host "✅ 创建 Modules 目录: $destRoot" -ForegroundColor Green
}

if (Test-Path $destModuleDir) {
    Remove-Item $destModuleDir -Recurse -Force
    Write-Host "⚠️  覆盖旧模块目录: $destModuleDir" -ForegroundColor Yellow
}

New-Item -ItemType Directory -Path $destModuleDir -Force | Out-Null
Write-Host "✅ 创建模块目录: $destModuleDir" -ForegroundColor Green

# 复制文件, 重命名 README (避免与其他模块 README 冲突)
$copied = @()
foreach ($f in $filesToCopy) {
    $src = Join-Path $scriptRoot $f
    # README 改名为 README.md (符合模块惯例)
    $destName = if ($f -match '\.README\.md$') { 'README.md' } else { $f }
    $dest = Join-Path $destModuleDir $destName
    Copy-Item -Path $src -Destination $dest -Force
    $copied += $destName
    Write-Host "  📄 $destName" -ForegroundColor Gray
}

# ─── 验证复制后模块能加载 ─────────────────────────────────────
Write-Host ""
Write-Host "=== 验证模块加载 ===" -ForegroundColor Cyan
$psd1 = Join-Path $destModuleDir "$moduleName.psd1"
try {
    Import-Module $psd1 -Force -ErrorAction Stop
    $cmd = Get-Command Test-AdminDependency -ErrorAction SilentlyContinue
    if ($cmd) {
        Write-Host "✅ 模块加载成功: Test-AdminDependency 可用" -ForegroundColor Green
    } else {
        Write-Host "❌ 模块加载后未发现 Test-AdminDependency" -ForegroundColor Red
        exit 1
    }
    # 从当前会话卸载, 避免污染调用方
    Remove-Module $moduleName -ErrorAction SilentlyContinue
} catch {
    Write-Host "❌ 模块加载失败: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "  ✅ 模块复制完成" -ForegroundColor Green
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "  目标目录: $destModuleDir" -ForegroundColor White
Write-Host "  复制文件: $($copied.Count) 个" -ForegroundColor White
Write-Host ""
Write-Host "  使用方式 (在目标项目内):" -ForegroundColor Cyan
Write-Host "    Import-Module .\Modules\AdminDependencyChecker\AdminDependencyChecker.psd1" -ForegroundColor White
Write-Host "    \$r = Test-AdminDependency -Path .\your-script.ps1" -ForegroundColor White
Write-Host "    Publish-AdminDependencyResult -Result \$r" -ForegroundColor White
Write-Host ""
Write-Host "  或加入 \$PSModulePath 全局可用:" -ForegroundColor Cyan
Write-Host "    Import-Module AdminDependencyChecker" -ForegroundColor White

exit 0
