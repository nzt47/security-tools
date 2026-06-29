<#
.SYNOPSIS
本地运行集成测试的启动脚本

.DESCRIPTION
提供便捷的方式运行集成测试，支持多种参数和日志配置

.EXAMPLE
.\run_tests_local.ps1
运行所有集成测试

.EXAMPLE
.\run_tests_local.ps1 -Module circuit_breaker_degrade_flow
运行指定测试模块

.EXAMPLE
.\run_tests_local.ps1 -All -Verbose
运行所有测试并显示详细日志

.EXAMPLE
.\run_tests_local.ps1 -Docker
使用Docker容器运行测试
#>

param(
    [string]$Module = "",
    [string]$Test = "",
    [switch]$All = $false,
    [switch]$Verbose = $false,
    [switch]$Docker = $false,
    [string]$LogFile = "",
    [int]$Timeout = 120
)

$scriptPath = $MyInvocation.MyCommand.Path
$scriptDir = Split-Path $scriptPath -Parent
$projectRoot = Split-Path $scriptDir -Parent

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "         云枢集成测试运行器" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "项目根目录: $projectRoot" -ForegroundColor Gray
Write-Host "Python版本: $(python --version)" -ForegroundColor Gray
Write-Host ""

if ($Docker) {
    Write-Host "[Docker模式] 构建测试镜像..." -ForegroundColor Yellow
    
    docker build -f Dockerfile.test -t yunshu-integration-test .
    
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Docker构建失败!" -ForegroundColor Red
        exit 1
    }
    
    Write-Host "Docker镜像构建成功" -ForegroundColor Green
    Write-Host ""
    
    $dockerCmd = "docker run --rm yunshu-integration-test"
    
    if ($Module) {
        $dockerCmd += " pytest tests/integration/test_${Module}.py -v"
    }
    elseif ($All) {
        $dockerCmd += " pytest tests/integration/ -v"
    }
    
    Write-Host "运行命令: $dockerCmd" -ForegroundColor Cyan
    Invoke-Expression $dockerCmd
    
    exit $LASTEXITCODE
}

Write-Host "[本地模式] 检查虚拟环境..." -ForegroundColor Yellow

$venvPath = Join-Path $projectRoot "venv"
$pythonPath = Join-Path $venvPath "Scripts/python.exe"

if (-not Test-Path $pythonPath) {
    Write-Host "未找到虚拟环境，尝试使用系统Python..." -ForegroundColor Warning
    $pythonPath = "python"
}

Write-Host "使用Python: $pythonPath" -ForegroundColor Green

Write-Host ""
Write-Host "[配置测试参数]" -ForegroundColor Yellow

$pytestArgs = @()
$pytestArgs += "-v"
$pytestArgs += "--tb=short"
$pytestArgs += "--timeout=$Timeout"

if ($Verbose) {
    $pytestArgs += "-s"
    $env:PYTHONDEBUG = "1"
}

if ($LogFile) {
    $pytestArgs += "--log-file=$LogFile"
}

$testPath = "tests/integration/"

if ($Module) {
    $testPath = "tests/integration/test_${Module}.py"
    Write-Host "测试模块: $Module" -ForegroundColor Cyan
}
elseif ($All) {
    Write-Host "测试范围: 所有集成测试" -ForegroundColor Cyan
}
else {
    Write-Host "测试范围: 默认集成测试" -ForegroundColor Cyan
}

if ($Test) {
    $testPath += "::${Test}"
    Write-Host "测试用例: $Test" -ForegroundColor Cyan
}

$pytestArgs += $testPath

Write-Host ""
Write-Host "[开始运行测试]" -ForegroundColor Yellow
Write-Host "命令: $pythonPath -m pytest $($pytestArgs -join ' ')" -ForegroundColor Gray
Write-Host "----------------------------------------" -ForegroundColor DarkGray

& $pythonPath -m pytest $pytestArgs

$exitCode = $LASTEXITCODE

Write-Host "----------------------------------------" -ForegroundColor DarkGray

if ($exitCode -eq 0) {
    Write-Host ""
    Write-Host "测试全部通过! ✅" -ForegroundColor Green
}
else {
    Write-Host ""
    Write-Host "测试失败，退出码: $exitCode ❌" -ForegroundColor Red
}

Write-Host "========================================" -ForegroundColor Cyan

exit $exitCode
