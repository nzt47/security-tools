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

  # 4) 模拟运行（不真实发送，打印命令与模拟结果，用于验证链路/排查）
  .\scripts\dev\notify_group.ps1 -DryRun -Message "测试通知"

日志说明：
  - 所有关键步骤输出 [HH:mm:ss] 前缀日志，便于定位失败环节；
  - Webhook/Secret 值一律脱敏（仅显示前 24 字符），防止日志泄露凭据。

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
    [switch]$Check,             # 仅校验配置，不发送
    [switch]$DryRun             # 模拟运行：不真实调用发送，打印命令与模拟结果
)

$ErrorActionPreference = "Stop"

# ── 日志辅助：统一 [HH:mm:ss] 前缀，便于按时间线排查 ──────────
function Write-Step([string]$Msg) {
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] $Msg"
}
function Write-Info([string]$Msg) { Write-Host "      $Msg" }

# ── 脱敏：仅显示前 24 字符与长度，防日志泄露凭据 ───────────────
function Mask-Secret([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) { return "(空)" }
    $head = $Value.Substring(0, [Math]::Min(24, $Value.Length))
    if ($Value.Length -le 24) { return "$head (长度 $($Value.Length))" }
    return "$head...(长度 $($Value.Length))"
}

# ── 0) 初始化 ──────────────────────────────────────────────────
Write-Step "初始化: 定位仓库根目录"
$Root = git rev-parse --show-toplevel 2>$null
if (-not $Root) { throw "必须在 git 仓库内执行" }
$EnvFile = Join-Path $Root ".env"
$NotifyScript = Join-Path $Root "scripts/observability_dingtalk_notify.py"
Write-Info "仓库根: $Root"
Write-Info ".env:   $EnvFile"
Write-Info "通知脚本: $NotifyScript"
if (-not (Test-Path $EnvFile)) { throw ".env 不存在: $EnvFile" }
if (-not (Test-Path $NotifyScript)) { throw "通知脚本不存在: $NotifyScript" }
Write-Step "初始化完成"

# ── 读 .env 单变量（返回去空格值）──────────────────────────────
function Get-EnvValue([string]$Name) {
    $line = Get-Content $EnvFile -Encoding utf8 | Where-Object { $_ -match "^$([regex]::Escape($Name))\s*=" } | Select-Object -First 1
    if ($line) { return ($line -replace "^$([regex]::Escape($Name))\s*=\s*", "").Trim() }
    return ""
}

# ── 幂等 upsert 到 .env（保留其他行与注释，仅更新/追加目标行）──
# 注意: 必须用 @(...) 强制数组，理由同上（单行文件防 string 拼接灾难）。
function Set-EnvValue([string]$Name, [string]$Value) {
    Write-Step "写入 .env: $Name"
    $lines = @(Get-Content $EnvFile -Encoding utf8)
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
    if ($found) { Write-Info "$Name 已存在 → 值已覆盖" } else { Write-Info "$Name 不存在 → 已追加" }
}

# ── 占位符/空值检测（防止把示例值当真发送）─────────────────────
function Test-Placeholder([string]$Value) {
    return [string]::IsNullOrWhiteSpace($Value) -or
           $Value -match "DEMO|xxx|YOUR_|CHANGE_ME|example\.com|REPLACE_WITH|hooks\.example"
}

# ── 1) 写入模式 ────────────────────────────────────────────────
if ($SetWebhook -ne "") {
    if (Test-Placeholder $SetWebhook) { throw "SetWebhook 是占位符/空值，拒绝写入 .env" }
    Write-Info "SetWebhook 校验通过（非占位符），即将写入: $(Mask-Secret $SetWebhook)"
    Set-EnvValue "DINGTALK_WEBHOOK" $SetWebhook
}
if ($SetSecret -ne "") {
    Set-EnvValue "DINGTALK_SECRET" $SetSecret
}

# ── 2) 读取配置 ────────────────────────────────────────────────
Write-Step "读取 .env 配置"
$Webhook = Get-EnvValue "DINGTALK_WEBHOOK"
$Secret = Get-EnvValue "DINGTALK_SECRET"
Write-Info "DINGTALK_WEBHOOK = $(Mask-Secret $Webhook)"
Write-Info "DINGTALK_SECRET  = $(if ($Secret) { Mask-Secret $Secret } else { '(未配置)' })"

if ($Check -or $SetWebhook -ne "" -or $SetSecret -ne "") {
    Write-Step "配置状态汇总"
    if ($Webhook -eq "") {
        Write-Host "  DINGTALK_WEBHOOK: ❌ 未配置"
    } elseif (Test-Placeholder $Webhook) {
        Write-Host "  DINGTALK_WEBHOOK: ❌ 占位符值（示例值未替换）"
    } else {
        Write-Host "  DINGTALK_WEBHOOK: ✅ 已配置（$(Mask-Secret $Webhook)）"
    }
    Write-Host "  DINGTALK_SECRET : $(if ($Secret) { "✅ 已配置" } else { "（未配置，机器人未加签时无需）" })"
    # 配置类模式（-Check / -SetWebhook / -SetSecret）到此结束，不进入发送流程
    Write-Step "配置模式完成，结束（发送请单独执行 -Message）"
    return
}

# ── 3) 发送模式校验 ────────────────────────────────────────────
Write-Step "发送前校验"
if ([string]::IsNullOrWhiteSpace($Message)) {
    throw "缺少 -Message。配置/校验请加 -SetWebhook / -Check 参数。"
}
if (Test-Placeholder $Webhook) {
    throw "DINGTALK_WEBHOOK 未配置或为占位符，无法发送。请先执行: .\scripts\dev\notify_group.ps1 -SetWebhook <真实URL>"
}
Write-Info "校验通过: Message 非空、Webhook 非占位符"

# ── 4) 构建调用参数（.env → 命令行参数桥接）────────────────────
Write-Step "构建通知脚本参数"
$cmdArgs = @($NotifyScript, "--webhook", $Webhook, "--status", $Status,
             "--message", $Message, "--msg-type", "markdown")
if ($Secret) { $cmdArgs += @("--secret", $Secret) }
$maskedCmd = @("python", $cmdArgs[0], "--webhook", (Mask-Secret $Webhook),
               "--status", $Status, "--message", $Message, "--msg-type", "markdown")
Write-Info "命令（脱敏）: $($maskedCmd -join ' ')"

# ── 5) DryRun：模拟发送，不真实调用 ────────────────────────────
if ($DryRun) {
    Write-Step "DryRun 模式: 模拟发送 (status=$Status)，不调用通知脚本"
    Write-Info "若真实执行: 将调用 observability_dingtalk_notify.py"
    Write-Info "模拟结果: 发送成功（dry-run，未产生真实网络请求）"
    Write-Host ""
    Write-Host "✅ [DryRun] 链路验证通过: 配置读取 → 校验 → 参数构建均正常。"
    return
}

# ── 6) 真实调用并记录结果（失败时打印完整输出便于排查）────────
Write-Step "调用通知脚本发送 (status=$Status)"
$output = & python $cmdArgs 2>&1
$exitCode = $LASTEXITCODE
if ($output) {
    Write-Info "脚本输出:"
    $output | ForEach-Object { Write-Host "    $_" }
} else {
    Write-Info "脚本无输出"
}
if ($exitCode -ne 0) {
    throw "通知发送失败 (exit=$exitCode)。请检查: ① webhook URL 有效性 ② 钉钉机器人是否开启加签(需配 DINGTALK_SECRET) ③ 网络连通性"
}
Write-Step "发送成功"
