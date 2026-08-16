# Jira #TASK-1234 上传附件 + 状态更新 - 一键执行脚本
# 用法: 双击运行 或 在 PowerShell 中执行 .\scripts\run_jira_task1234_update.ps1
# 前提: 已设置环境变量 JIRA_BASE_URL / JIRA_EMAIL / JIRA_TOKEN，或首次运行时按提示输入
# 可选: 设置 JIRA_ATTACH 指向要上传的审计 zip（默认指向 Temp 归档，不存在则跳过）

$ErrorActionPreference = "Stop"

function Read-Secret {
    param([string]$Prompt)
    Write-Host $Prompt -NoNewline
    $secret = $null
    while ($true) {
        $key = [Console]::ReadKey($true)
        if ($key.Key -eq [ConsoleKey]::Enter) { break }
        if ($key.Key -eq [ConsoleKey]::Backspace) {
            if ($secret.Length -gt 0) { $secret = $secret.Substring(0, $secret.Length - 1) }
        } else {
            $secret += $key.KeyChar
        }
    }
    Write-Host ""
    return $secret
}

# 1) 读取/确认环境变量
if (-not $env:JIRA_BASE_URL) {
    $env:JIRA_BASE_URL = Read-Host "Jira 实例 URL (如 https://xxx.atlassian.net)"
}
if (-not $env:JIRA_EMAIL) {
    $env:JIRA_EMAIL = Read-Host "Jira 登录邮箱"
}
if (-not $env:JIRA_TOKEN) {
    $env:JIRA_TOKEN = Read-Secret -Prompt "API Token (输入不回显): "
}

if (-not $env:JIRA_BASE_URL -or -not $env:JIRA_EMAIL -or -not $env:JIRA_TOKEN) {
    Write-Host "[ERROR] JIRA_BASE_URL / JIRA_EMAIL / JIRA_TOKEN 任一为空，终止执行" -ForegroundColor Red
    exit 1
}

# 2) 确认附件（可选）
if (-not $env:JIRA_ATTACH) {
    $defaultAttach = "C:\Windows\Temp\task8_close_audit_20260815.zip"
    if (Test-Path $defaultAttach) {
        $env:JIRA_ATTACH = $defaultAttach
        Write-Host "[INFO] 使用默认审计附件: $env:JIRA_ATTACH" -ForegroundColor Cyan
    } else {
        Write-Host "[WARN] 未找到默认审计附件，将跳过上传（可设置 JIRA_ATTACH 指定路径）" -ForegroundColor Yellow
    }
}

# 3) 运行更新脚本
Write-Host "[INFO] 开始更新 #TASK-1234 ..." -ForegroundColor Cyan
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
python -B (Join-Path $scriptDir "jira_update_task_status.py")
if ($LASTEXITCODE -ne 0) {
    Write-Host "[WARN] 脚本返回非零退出码，请检查上方输出" -ForegroundColor Yellow
} else {
    Write-Host "[OK] 执行完成。请到 Jira 确认 #TASK-1234 附件、状态与备注。" -ForegroundColor Green
}
