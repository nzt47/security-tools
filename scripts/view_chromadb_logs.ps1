<#
运行 python -m agent.preflight 并只显示 ChromaDB 决策路径日志与用例结果。

Why: 预检 CLI 全量输出包含每条路径的 logger.info/warning 决策日志 + 用例结果，
本脚本按 action 模式过滤，一眼看清每轮 _create_client 走了哪条分支
（probe_start → probe_ok → ready|chromadb|client_failed|timeout）。

用法：
    .\scripts\view_chromadb_logs.ps1                  # 查看全部决策日志与结果
    .\scripts\view_chromadb_logs.ps1 -Filter "probe"  # 仅看探测相关日志
#>
param([string]$Filter = "chromadb\.")

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$env:PYTHONIOENCODING = "utf-8"
python -m agent.preflight 2>&1 |
    Select-String -Pattern "$Filter|^\[[0-9]+\]|^>>>"
exit $LASTEXITCODE
