<#
一键运行全量单元测试（本地 Windows 日常验证）

- 自动设置 DISABLE_NATIVE_EXT=1：规避 chromadb import 卡死导致的测试挂起
  （chromadb 1.5.9 + pydantic_settings 本机 import 偶发卡死；卡死线程持有全局
  import 锁，后续 `import chromadb.errors` 死锁。屏蔽后走 Mock/JSON fallback，
  与 CI Linux 同路径）。
- 排除 tests/unit/test_reranker.py：其 onnxruntime 在本机触发 ACCESS_VIOLATION
  （0xC0000005）原生崩溃，即使用例通过也会中断整个 pytest 进程；CI Linux 无此
  问题。需要时可用 -ExtraArgs "-k reranker" 单独跑。
- 用法：
    .\scripts\run_unit_tests.ps1                             # 全量单元测试（串行）
    .\scripts\run_unit_tests.ps1 -Parallel                  # 全量单元测试（pytest-xdist 并行，推荐）
    .\scripts\run_unit_tests.ps1 -LogPath test_run.log      # 同时输出完整日志到文件
    .\scripts\run_unit_tests.ps1 -ExtraArgs "-k knowledge"  # 附加 pytest 参数
#>
param([switch]$Parallel, [string]$ExtraArgs = "", [string]$LogPath = "")

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$env:PYTHONIOENCODING = "utf-8"
$env:DISABLE_NATIVE_EXT = "1"
Write-Host "==> DISABLE_NATIVE_EXT=$env:DISABLE_NATIVE_EXT (规避 chromadb import 卡死)"

$pytestArgs = @("tests/unit", "--ignore=tests/unit/test_reranker.py")
if ($Parallel) {
    # 9264 项全量串行需 30min+，本机环境长任务易被回收；CI 亦用 xdist 并行
    $pytestArgs += "-n"
    $pytestArgs += "auto"
    Write-Host "==> pytest-xdist 并行模式 (-n auto)"
}
if ($ExtraArgs) {
    # 按空白拆分为独立参数（避免 "-k knowledge -q" 被整体误传给 -k 表达式）
    $pytestArgs += $ExtraArgs -split '\s+'
}
if ($LogPath) {
    # Tee-Object 同时输出到终端与日志文件；$LASTEXITCODE 仍取 python 退出码
    python -m pytest @pytestArgs 2>&1 | Tee-Object -FilePath $LogPath
} else {
    python -m pytest @pytestArgs
}
exit $LASTEXITCODE
