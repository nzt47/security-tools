<#
一键启动知识库重构任务流水线（依赖检查 → 交付物验证 → 全量任务测试）

编排顺序（拓扑序 T0→T1→...→T7）：
  1. python scripts/verify_knowledge_plan_deps.py --no-warn   # 依赖闭环检查（失败即停）
  2. python scripts/run_knowledge_tasks.py [--verbose]        # 交付物门禁 + 各任务 pytest
退出码：0=全部通过；非 0=依赖检查或任务执行失败（透传下层退出码）。

用法：
  .\scripts\run_knowledge_tasks.ps1              # 标准一键启动
  .\scripts\run_knowledge_tasks.ps1 -Verbose     # 输出 pytest 完整日志
  .\scripts\run_knowledge_tasks.ps1 -Tasks "T2,T4"  # 只跑指定任务（仍校验依赖）
#>
param(
    [switch]$Verbose,
    [string]$Tasks = ""
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$env:PYTHONIOENCODING = "utf-8"

Write-Host ""
Write-Host "==> [1/2] 依赖闭环检查 (verify_knowledge_plan_deps.py --no-warn)"
python scripts/verify_knowledge_plan_deps.py --no-warn
if ($LASTEXITCODE -ne 0) {
    Write-Host "[失败] 依赖检查未通过，终止流水线（EXITCODE=$LASTEXITCODE）" -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "==> [2/2] 交付物验证 + 全量任务测试 (run_knowledge_tasks.py)"
$runArgs = @()
if ($Verbose) { $runArgs += "--verbose" }
if ($Tasks) {
    $runArgs += "--tasks"
    $runArgs += ($Tasks -split ',')
}
python scripts/run_knowledge_tasks.py @runArgs

Write-Host ""
if ($LASTEXITCODE -eq 0) {
    Write-Host "一键流水线全部通过（T0-T7 全 PASS）。" -ForegroundColor Green
} else {
    Write-Host "一键流水线未通过（EXITCODE=$LASTEXITCODE），详见上方日志。" -ForegroundColor Red
}
exit $LASTEXITCODE
