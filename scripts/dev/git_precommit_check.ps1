<#
.SYNOPSIS
    通用 pre-commit 检查脚本：文档链接预检 + 锚点链接回归测试

.DESCRIPTION
    封装两类自动化检查，供 git pre-commit hook 调用（经 sync_precommit_hook.ps1 部署，
    通过 TLM_HOOK_SOURCE_REPO 间接寻址，可复制到任意仓库）：
    1. 文档链接预检：调用 precheck_docs.ps1 -BlockMode（失效链接超过阈值即阻断）
    2. 锚点链接回归测试：pytest tests/unit/test_precheck_docs_anchor_links.py
       仅当目标仓库存在测试文件且 python 可用时运行，避免阻断未配置测试环境的仓库

    任一项检查失败 → exit 1（阻止提交）。

.PARAMETER TargetRepo
    被检查的仓库根目录（默认当前目录）

.EXAMPLE
    .\scripts\dev\git_precommit_check.ps1 -TargetRepo D:\code\my-repo
    .\scripts\dev\git_precommit_check.ps1
#>
param(
    [string]$TargetRepo,
    # 字节级调试模式（即 -Verbose 模式）：PS 5.1 的 -File 调用会把 -Verbose 作为保留参数名
    # 处理、不绑定到显式 switch，因此用 -BomDiag 实现（hook 经 TLM_HOOK_VERBOSE=1 透传）
    [switch]$BomDiag
)

$ErrorActionPreference = 'Continue'

# -BomDiag → 提升 Verbose 流偏好，并向下游 precheck_docs.ps1 传递
if ($BomDiag) {
    $VerbosePreference = 'Continue'
}

if (-not $TargetRepo) {
    $TargetRepo = (Get-Location).Path
}
if (-not (Test-Path $TargetRepo)) {
    Write-Host "[pre-commit][ERROR] 目标仓库不存在: $TargetRepo" -ForegroundColor Red
    exit 1
}
Set-Location $TargetRepo

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pass = 0
$fail = 0

# -BomDiag 时向子进程传递调试开关，输出字节级 BOM/路径诊断
$verboseArgs = @()
if ($VerbosePreference -eq 'Continue') {
    $verboseArgs = @('-BomDiag')
    Write-Verbose "字节级调试开启：失效链接将输出 BOM 状态 / 锚点剥离 / 路径解析"
}

# ── 检查 1: 文档链接预检（阻塞模式，阈值 0 不可绕过） ──
$precheck = Join-Path $scriptDir 'precheck_docs.ps1'
if (-not (Test-Path $precheck)) {
    Write-Host "[pre-commit][ERROR] precheck_docs.ps1 不存在: $precheck" -ForegroundColor Red
    exit 1
}
Write-Host "`n[1/2] 文档链接预检..." -ForegroundColor Yellow
& powershell -ExecutionPolicy Bypass -File $precheck -SkipChart -BlockMode -AllowBroken 0 -TargetRepo $TargetRepo @verboseArgs
if ($LASTEXITCODE -eq 0) {
    $pass++
    Write-Host "  [OK] 链接预检通过" -ForegroundColor Green
} else {
    $fail++
    Write-Host "  [FAIL] 链接预检未通过（见上方输出）" -ForegroundColor Red
}

# ── 检查 2: 锚点链接回归测试（python + 测试文件可用才运行） ──
$testFile = Join-Path $TargetRepo 'tests\unit\test_precheck_docs_anchor_links.py'
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCmd -and (Test-Path $testFile)) {
    Write-Host "`n[2/2] 锚点链接回归测试..." -ForegroundColor Yellow
    & python -m pytest $testFile -q 2>&1 | Select-Object -Last 5
    if ($LASTEXITCODE -eq 0) {
        $pass++
        Write-Host "  [OK] 锚点回归测试通过" -ForegroundColor Green
    } else {
        $fail++
        Write-Host "  [FAIL] 锚点回归测试未通过（见上方输出）" -ForegroundColor Red
    }
} else {
    Write-Host "`n[2/2] 锚点回归测试跳过（python 或测试文件不可用）" -ForegroundColor Gray
}

# ── 汇总 ──
Write-Host "`n=== pre-commit 检查汇总 ===" -ForegroundColor Cyan
Write-Host "  通过: $pass | 失败: $fail"
if ($fail -gt 0) {
    Write-Host "[BLOCK] 提交被阻止" -ForegroundColor Red
    exit 1
}
Write-Host "[OK] 预检通过" -ForegroundColor Green
exit 0
