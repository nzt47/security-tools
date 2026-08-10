<#
群通知发送脚本（钉钉）

配置规范（【不易】配置一律走 .env，其他文件通过环境变量/本脚本读取）：
  - DINGTALK_WEBHOOK  : 钉钉机器人 Webhook URL（必填）
  - DINGTALK_SECRET   : 加签密钥（可选，机器人开启加签时必填）

用法（仓库根目录执行）：
  # 1) 将 Webhook 写入 .env（幂等：已存在则覆盖值，不存在则追加）
  .\scripts\dev\notify_group.ps1 -SetWebhook "https://oapi.dingtalk.com/robot/send?access_token=xxx" [-SetSecret "SECxxx"]

  # 2) 发送通知（自动从 .env 读取 webhook）
  .\scripts\dev\notify_group.ps1 -Message "通知内容" [-Status success|failure|cancelled]

  # 3) 发送前验证 .env 配置是否就绪（不发送）
  .\scripts\dev\notify_group.ps1 -Check

说明：
  - 底层调用 scripts/observability_dingtalk_notify.py（--webhook 必填，不支持环境变量，
    故本脚本负责「.env → 命令行参数」的桥接）。
  - CI 自动通知走 GitHub Secrets（secrets.DINGTALK_WEBHOOK），与 .env 独立；
    本地/临时群通知走本脚本。
#>
param(
    [string]$Message = "",
    [ValidateSet("success", "failure", "cancelled")]
    [string]$Status = "success",
    [string]$SetWebhook = "",   # 提供则写入 .env 的 DINGTALK_WEBHOOK（幂等）
    [string]$SetSecret = "",    # 提供则写入 .env 的 DINGTALK_SECRET（幂等）
    [switch]$Check              # 仅校验配置，不发送
)

$ErrorActionPreference = "Stop"
$Root = git rev-parse --show-toplevel 2>$null
if (-not $Root) { throw "必须在 git 仓库内执行" }
$EnvFile = Join-Path $Root ".env"
$NotifyScript = Join-Path $Root "scripts/observability_dingtalk_notify.py"
if (-not (Test-Path $EnvFile)) { throw ".env 不存在: $EnvFile" }
if (-not (Test-Path $NotifyScript)) { throw "通知脚本不存在: $NotifyScript" }

# ── 读 .env 单变量（返回去空格值）──────────────────────────────
function Get-EnvValue([string]$Name) {
    $line = Get-Content $EnvFile -Encoding utf8 | Where-Object { $_ -match "^$([regex]::Escape($Name))\s*=" } | Select-Object -First 1
    if ($line) { return ($line -replace "^$([regex]::Escape($Name))\s*=\s*", "").Trim() }
    return ""
}

# ── 幂等 upsert 到 .env（保留其他行与注释，仅更新/追加目标行）──
function Set-EnvValue([string]$Name, [string]$Value) {
    $lines = Get-Content $EnvFile -Encoding utf8
    $pattern = "^$([regex]::Escape($Name))\s*="
    $found = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match $pattern) {
            $lines[$i] = "$Name=$Value"
            $found = $true
            break
        }
    }
    if (-not $found) { $lines += "$Name=$Value" }
    $lines | Set-Content $EnvFile -Encoding utf8
    Write-Host "      .env 已更新: $Name"
}

# ── 占位符/空值检测（防止把示例值当真发送）─────────────────────
function Test-Placeholder([string]$Value) {
    return [string]::IsNullOrWhiteSpace($Value) -or
           $Value -match "DEMO|xxx|YOUR_|CHANGE_ME|example\.com|REPLACE_WITH|hooks\.example"
}

# ── 1) 写入模式 ────────────────────────────────────────────────
if ($SetWebhook -ne "") {
    if (Test-Placeholder $SetWebhook) { throw "SetWebhook 是占位符/空值，拒绝写入 .env" }
    Set-EnvValue "DINGTALK_WEBHOOK" $SetWebhook
}
if ($SetSecret -ne "") {
    Set-EnvValue "DINGTALK_SECRET" $SetSecret
}

# ── 2) 读取配置 ────────────────────────────────────────────────
$Webhook = Get-EnvValue "DINGTALK_WEBHOOK"
$Secret = Get-EnvValue "DINGTALK_SECRET"

if ($Check -or $SetWebhook -ne "" -or $SetSecret -ne "") {
    Write-Host "── 钉钉通知配置 ──"
    if ($Webhook -eq "") {
        Write-Host "  DINGTALK_WEBHOOK: ❌ 未配置"
    } elseif (Test-Placeholder $Webhook) {
        Write-Host "  DINGTALK_WEBHOOK: ❌ 占位符值（示例值未替换）"
    } else {
        Write-Host "  DINGTALK_WEBHOOK: ✅ 已配置（$($Webhook.Substring(0, [Math]::Min(24, $Webhook.Length)))...）"
    }
    Write-Host "  DINGTALK_SECRET : $(if ($Secret) { "✅ 已配置" } else { "（未配置，机器人未加签时无需）" })"
    if ($Check) { return }  # 仅校验模式，退出
}

# ── 3) 发送模式校验 ────────────────────────────────────────────
if ([string]::IsNullOrWhiteSpace($Message)) {
    throw "缺少 -Message。配置/校验请加 -SetWebhook / -Check 参数。"
}
if (Test-Placeholder $Webhook) {
    throw "DINGTALK_WEBHOOK 未配置或为占位符，无法发送。请先执行: .\scripts\dev\notify_group.ps1 -SetWebhook <真实URL>"
}

# ── 4) 调用通知脚本（.env → 命令行参数桥接）────────────────────
Write-Host "── 发送通知 (status=$Status) ──"
$cmdArgs = @("$NotifyScript", "--webhook", $Webhook, "--status", $Status,
             "--message", $Message, "--msg-type", "markdown")
if ($Secret) { $cmdArgs += @("--secret", $Secret) }

& python $cmdArgs
if ($LASTEXITCODE -ne 0) { throw "通知发送失败 (exit=$LASTEXITCODE)" }
Write-Host "发送完成。"
