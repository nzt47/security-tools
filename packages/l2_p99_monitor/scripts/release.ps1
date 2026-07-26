#!/usr/bin/env powershell
# l2_p99_monitor PyPI 发布脚本（Windows）
#
# 用法：
#   .\scripts\release.ps1 -Version 1.0.1
#   .\scripts\release.ps1 -Version 1.0.1 -TestPyPI

param(
    [Parameter(Mandatory=$true)]
    [string]$Version,

    [switch]$TestPyPI
)

$ErrorActionPreference = "Stop"
$PackageDir = Split-Path -Parent $PSScriptRoot

Write-Host "🚀 l2_p99_monitor v$Version 发布脚本" -ForegroundColor Cyan
Write-Host "包目录: $PackageDir"
Write-Host ""

# 1. 更新版本号
Write-Host "[1/6] 更新版本号到 $Version..." -ForegroundColor Yellow
$PyprojectPath = Join-Path $PackageDir "pyproject.toml"
$Content = Get-Content $PyprojectPath -Raw
$Content = $Content -replace 'version = "[\d.]+"', "version = `"$Version`""
Set-Content -Path $PyprojectPath -Value $Content -NoNewline

$InitPath = Join-Path $PackageDir "l2_p99_monitor\__init__.py"
$Content = Get-Content $InitPath -Raw
$Content = $Content -replace '__version__ = "[\d.]+"', "__version__ = `"$Version`""
Set-Content -Path $InitPath -Value $Content -NoNewline
Write-Host "  ✓ 版本号已更新" -ForegroundColor Green

# 2. 运行单元测试
Write-Host "[2/6] 运行单元测试..." -ForegroundColor Yellow
$env:PYTHONIOENCODING = "utf-8"
python (Join-Path $PackageDir "tests\test_monitor.py")
if ($LASTEXITCODE -ne 0) {
    Write-Host "  ✗ 单元测试失败" -ForegroundColor Red
    exit 1
}
Write-Host "  ✓ 单元测试通过" -ForegroundColor Green

# 3. 清理旧构建产物
Write-Host "[3/6] 清理旧构建产物..." -ForegroundColor Yellow
Remove-Item -Recurse -Force (Join-Path $PackageDir "dist") -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force (Join-Path $PackageDir "build") -ErrorAction SilentlyContinue
Get-ChildItem -Path $PackageDir -Filter "*.egg-info" -Directory | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "  ✓ 清理完成" -ForegroundColor Green

# 4. 构建分发包
Write-Host "[4/6] 构建分发包..." -ForegroundColor Yellow
Push-Location $PackageDir
python -m build
if ($LASTEXITCODE -ne 0) {
    Write-Host "  ✗ 构建失败" -ForegroundColor Red
    Pop-Location
    exit 1
}
Pop-Location
Write-Host "  ✓ 构建完成" -ForegroundColor Green

# 5. 检查包
Write-Host "[5/6] 检查包..." -ForegroundColor Yellow
twine check (Join-Path $PackageDir "dist\*")
if ($LASTEXITCODE -ne 0) {
    Write-Host "  ✗ 包检查失败" -ForegroundColor Red
    exit 1
}
Write-Host "  ✓ 包检查通过" -ForegroundColor Green

# 6. 上传
Write-Host "[6/6] 上传到 $(if ($TestPyPI) {'TestPyPI'} else {'PyPI'})..." -ForegroundColor Yellow
if ($TestPyPI) {
    twine upload --repository testpypi (Join-Path $PackageDir "dist\*")
} else {
    twine upload (Join-Path $PackageDir "dist\*")
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "  ✗ 上传失败" -ForegroundColor Red
    exit 1
}
Write-Host "  ✓ 上传成功" -ForegroundColor Green

Write-Host ""
Write-Host "✅ 发布完成！" -ForegroundColor Green
Write-Host ""
Write-Host "安装命令：" -ForegroundColor Cyan
if ($TestPyPI) {
    Write-Host "  pip install --index-url https://test.pypi.org/simple/ l2-p99-monitor==$Version"
} else {
    Write-Host "  pip install l2-p99-monitor==$Version"
}
