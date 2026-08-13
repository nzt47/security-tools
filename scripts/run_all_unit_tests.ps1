<#
本地一键全量单元测试（快速验证 watcher/ingest 改动是否影响其他模块）

两级流程：
  1. 快速回归（秒级）：tests/unit/test_knowledge*.py + test_routes_knowledge.py
     —— watcher/ingest 改动最可能影响的模块，先给反馈
  2. 全量回归：复用 run_unit_tests.ps1（pytest-xdist -n auto 并行，
     排除本地会原生崩溃的 test_reranker，设置 DISABLE_NATIVE_EXT=1）
任一阶段失败 → 立即退出并返回失败码（快速失败，先定位受影响模块）

用法：
  .\scripts\run_all_unit_tests.ps1              # 两级全跑（默认）
  .\scripts\run_all_unit_tests.ps1 -OnlyFull    # 跳过快速回归，直接全量
  .\scripts\run_all_unit_tests.ps1 -OnlyQuick   # 只跑 knowledge 快速回归
#>
param(
    [switch]$OnlyFull,
    [switch]$OnlyQuick
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot
$env:PYTHONIOENCODING = "utf-8"

$knowledgeTests = @(
    Get-ChildItem -Path tests/unit -Filter "test_knowledge*.py" | ForEach-Object { $_.FullName }
)
$knowledgeTests += (Join-Path $repoRoot "tests/unit/test_routes_knowledge.py")

if (-not $OnlyFull) {
    Write-Host ""
    Write-Host "==> [1/2] 快速回归（knowledge 相关，共 $($knowledgeTests.Count) 个测试文件）"
    python -m pytest $knowledgeTests -p no:cacheprovider --no-header -q
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[失败] knowledge 快速回归未通过（EXITCODE=$LASTEXITCODE）" -ForegroundColor Red
        exit $LASTEXITCODE
    }
    Write-Host "knowledge 快速回归通过。" -ForegroundColor Green
}

if (-not $OnlyQuick) {
    Write-Host ""
    Write-Host "==> [2/2] 全量单元测试（xdist 并行，复用 run_unit_tests.ps1）"
    & (Join-Path $PSScriptRoot "run_unit_tests.ps1") -Parallel
    exit $LASTEXITCODE
}
