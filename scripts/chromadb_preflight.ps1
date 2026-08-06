<#
ChromaDB 导入降级预检（可复用工具包 · 本地 PowerShell 版）

统一入口为 python -m agent.preflight（agent/preflight/ 包，单事实源）。
两道防线：
  1) CLI：12 条导入路径（含 30s 子进程超时降级），python -m 统一入口
  2) pytest 用例：分支级（test_memory_optimized_import.py，14 用例）
     + 整体级（test_preflight_runner.py，复用 runner）
任一步失败即以非零退出码结束（CI 中 unit-tests 的 needs 依赖会阻断跳过）。

用法：
    .\scripts\chromadb_preflight.ps1                     # 正常预检
    $env:PREFLIGHT_FAKE_FAIL="1"; .\scripts\chromadb_preflight.ps1
        # 故障演练：环境变量开关（任意非空值触发），模拟预检失败（验证 CI 阻断）
#>
param()

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$env:PYTHONIOENCODING = "utf-8"

if ($env:PREFLIGHT_FAKE_FAIL) {
    Write-Error "== 故障演练：PREFLIGHT_FAKE_FAIL 已设置，模拟预检失败（CI 中 unit-tests 将被 needs 阻断跳过）=="
    exit 1
}

Write-Host "=== 1/2 python -m agent.preflight（12 条导入路径）==="
python -m agent.preflight
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "=== 2/2 pytest 用例（test_memory_optimized_import + test_preflight_runner）==="
python -m pytest tests/unit/test_memory_optimized_import.py tests/unit/test_preflight_runner.py -q -p no:cacheprovider --no-header
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "=== ChromaDB 导入降级预检通过 ==="
exit 0
