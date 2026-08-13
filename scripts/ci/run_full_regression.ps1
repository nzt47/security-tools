<#
CI 全量回归脚本（TASK-01 补充，可在本地/CI 运行）

背景：本地沙箱存在 chromadb（pydantic_settings 访问系统路径）限制，全量 pytest
无法在沙箱内完成；CI（GitHub Actions）无此限制，本脚本提供统一的回归入口。

用法（PowerShell，项目根或任意目录）：
  # 1) unit 套件全量（TASK-01 验收标准：0 失败）
  .\scripts\ci\run_full_regression.ps1 -Suite unit

  # 2) 全量 tests/ 固定 seed 回归（顺序污染收敛验证，seed 见 pytest.ini 注释）
  .\scripts\ci\run_full_regression.ps1 -Suite all -Seed 20260813

  # 3) 禁用随机（对照 T-0 基线 failures_baseline.txt，2026-08-13: 68 failed/14359 passed/10 errors）
  .\scripts\ci\run_full_regression.ps1 -Suite all -NoRandom

退出码：pytest 退出码透传（0=全绿，1=有失败，2=中断，3=内部错误，4=用法错误，5=无测试收集）。
#>
param(
    [ValidateSet("unit", "all")]
    [string]$Suite = "unit",
    [string]$Seed = "20260813",
    [switch]$NoRandom
)

$ErrorActionPreference = "Stop"
# 中文日志编码（Windows CI runner 必需，否则报错信息乱码/截断）
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

# 脚本位于 <root>/scripts/ci/，仓库根 = 上两级
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Push-Location $RepoRoot
try {
    Write-Host "==> CI 全量回归 | Suite=$Suite Seed=$Seed NoRandom=$NoRandom | cwd=$RepoRoot"
    if ($Suite -eq "all") {
        if ($NoRandom) {
            python -m pytest tests/ -q -p no:randomly
        } else {
            python -m pytest tests/ -q --randomly-seed=$Seed
        }
    } else {
        python -m pytest tests/unit -q
    }
    $code = $LASTEXITCODE
    if ($code -eq 0) {
        Write-Host "==> 回归通过（exit=$code）"
    } else {
        Write-Host "==> 回归失败（exit=$code）。若为 all 套件，请与 failures_baseline.txt 比对确认是否新增顺序污染回归。" -ForegroundColor Yellow
    }
    exit $code
}
finally {
    Pop-Location
}
