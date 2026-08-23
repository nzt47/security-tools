# 一键端到端验证：自动拉起 SMTP 捕获服务器容器 → 运行告警测试容器 → 校验捕获邮件
#
# 流程：
#   1. docker compose up -d smtp-capture（健康检查就绪）
#   2. docker compose run --rm e2e-test（依赖健康条件，构造 >5% 数据集 → 真实 SMTP 发送 → 7 项断言）
#   3. 列出 ./captured 捕获的 .eml，默认清理容器（-Keep 保留）
#
# 用法（deploy/ci-runner 目录下）：
#   ./run_e2e.ps1            # 标准：拉起 → 测试 → 清理
#   ./run_e2e.ps1 -Keep      # 测试后保留容器（查看容器日志排查）
#   ./run_e2e.ps1 -Clean     # 启动前清空 ./captured（避免旧 .eml 干扰）
#
# 前置：docker daemon 运行中；Compose v2（Docker Desktop 自带）
param(
    [switch]$Keep,   # 测试后保留容器（默认 docker compose down 清理）
    [switch]$Clean   # 启动前清空 ./captured
)

$ErrorActionPreference = 'Stop'
$composeDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $composeDir

if ($Clean) {
    Write-Host "==> 清空 ./captured"
    Remove-Item -Recurse -Force ./captured -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Force ./captured | Out-Null

Write-Host "==> 拉起 SMTP 捕获服务器容器..."
docker compose up -d smtp-capture
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "==> 运行告警端到端测试（等 smtp-capture healthy）..."
docker compose run --rm e2e-test
$testCode = $LASTEXITCODE

Write-Host "`n==> 捕获邮件清单："
Get-ChildItem ./captured -Filter *.eml -ErrorAction SilentlyContinue |
    Select-Object Name, Length, LastWriteTime | Format-Table -AutoSize

if (-not $Keep) {
    Write-Host "==> 清理容器（-Keep 可保留排查）..."
    docker compose down | Out-Null
}

if ($testCode -ne 0) {
    Write-Host "`n[FAIL] 端到端测试未通过（exit=$testCode）"
} else {
    Write-Host "`n[OK] 端到端告警链路验证通过"
}
exit $testCode
