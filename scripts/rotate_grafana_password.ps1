#Requires -Version 5.1
<#
.SYNOPSIS
    Grafana 密码轮换 + 二次回归测试（一键脚本）
.DESCRIPTION
    完整流程：
    1. 校验新密码强度（≥12 字符，含大小写+数字+符号）
    2. 替换 .env 中旧密码（GLITCHTIP/GRAFANA/POSTGRES 三处）
    3. 停止容器 + 删除 grafana_data volume（Prometheus 数据可选保留）
    4. 重新启动容器（Grafana 用新密码首次初始化）
    5. 等待 Grafana/Prometheus 就绪
    6. 用新密码验证 Grafana API 认证
    7. 汇总报告
.PARAMETER NewPwd
    新的生产强密码（≥12 字符，含大小写+数字+符号，必填）
.PARAMETER KeepPrometheusData
    保留 Prometheus 历史数据（默认保留，仅删 grafana_data）
    添加 -KeepPrometheusData:$false 可同时删除 prometheus_data
.EXAMPLE
    .\rotate_grafana_password.ps1 -NewPwd "Yunshu@Prod2026#Secure"
    用新密码轮换并验证
.EXAMPLE
    .\rotate_grafana_password.ps1 -NewPwd "Yunshu@Prod2026#Secure" -KeepPrometheusData:$false
    全量重建（同时删除 Prometheus 数据）
.NOTES
    【不易】Grafana admin 密码仅在首次启动时写入数据库，后续容器重启不会更新。
           因此每次换密码都必须删除 grafana_data volume 重新初始化。
    【变易】.env 已被 .gitignore 排除，密码修改不会进版本库。
    【简易】脚本可反复执行，每次换密码只需传入新密码参数。
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true, HelpMessage="新的生产强密码（≥12 字符，含大小写+数字+符号）")]
    [string]$NewPwd,
    [switch]$KeepPrometheusData = $true
)

$ErrorActionPreference = 'Stop'
$projectRoot = "c:\Users\Administrator\agent"
$envFile = "$projectRoot\.env"
$composeFile = "$projectRoot\docker-compose.monitoring.yml"
$oldPwd = "Yunshu@P1Verify2026!"

# ── 工具函数 ───────────────────────────────────────────────────
function Write-Section {
    param([string]$Title)
    Write-Host ""
    Write-Host "========== $Title ==========" -ForegroundColor Cyan
}
function W-Pass { param([string]$Msg) Write-Host "  [PASS] $Msg" -ForegroundColor Green }
function W-Fail { param([string]$Msg) Write-Host "  [FAIL] $Msg" -ForegroundColor Red }
function W-Info { param([string]$Msg) Write-Host "  [INFO] $Msg" -ForegroundColor Yellow }

$pass = 0; $fail = 0; $skip = 0
$startTime = Get-Date

# ── Stage 0: 新密码强度校验 ────────────────────────────────────
Write-Section "Stage 0: 新密码强度校验"

if ($NewPwd.Length -lt 12) {
    W-Fail "新密码长度不足（当前 $($NewPwd.Length)，需 >= 12）"
    exit 1
}
W-Pass "密码长度: $($NewPwd.Length)（>= 12）"

$hasUpper = $NewPwd -cmatch '[A-Z]'
$hasLower = $NewPwd -cmatch '[a-z]'
$hasDigit = $NewPwd -match '\d'
$hasSymbol = $NewPwd -match '[^a-zA-Z0-9]'

if (-not $hasUpper) { W-Fail "缺少大写字母"; $fail++ } else { W-Pass "包含大写字母"; $pass++ }
if (-not $hasLower) { W-Fail "缺少小写字母"; $fail++ } else { W-Pass "包含小写字母"; $pass++ }
if (-not $hasDigit) { W-Fail "缺少数字"; $fail++ } else { W-Pass "包含数字"; $pass++ }
if (-not $hasSymbol) { W-Fail "缺少符号"; $fail++ } else { W-Pass "包含符号"; $pass++ }

if ($fail -gt 0) {
    Write-Host ""
    Write-Host "[ERR ] 密码强度不足，请使用 >=12 字符且含大小写+数字+符号" -ForegroundColor Red
    exit 1
}

if ($NewPwd -eq $oldPwd) {
    W-Fail "新密码与旧密码相同，请使用不同的密码"
    exit 1
}
W-Pass "新密码与旧密码不同"

# ── Stage 1: 替换 .env 中密码 ──────────────────────────────────
Write-Section "Stage 1: 替换 .env 中密码"

if (-not (Test-Path $envFile)) {
    W-Fail ".env 文件不存在: $envFile"
    exit 1
}

$content = [System.IO.File]::ReadAllText($envFile)
$oldCount = ([regex]::Matches($content, [regex]::Escape($oldPwd))).Count

if ($oldCount -eq 0) {
    W-Info ".env 中未找到旧密码 '$oldPwd'，可能已替换过"
    W-Info "将跳过替换步骤，直接重建容器"
} else {
    W-Info "找到旧密码 $oldCount 处，开始替换..."
    $content = $content -replace [regex]::Escape($oldPwd), $NewPwd
    [System.IO.File]::WriteAllText($envFile, $content, (New-Object System.Text.UTF8Encoding $false))
    W-Pass ".env 替换完成（UTF-8 无 BOM）"
}

# 验证替换结果
$verifyContent = [System.IO.File]::ReadAllText($envFile)
$newCount = ([regex]::Matches($verifyContent, [regex]::Escape($NewPwd))).Count
$remainingOld = ([regex]::Matches($verifyContent, [regex]::Escape($oldPwd))).Count

if ($remainingOld -gt 0) {
    W-Fail "仍残留旧密码 $remainingOld 处"
    $fail++
} else {
    W-Pass "旧密码已全部替换（残留: $remainingOld 处）"
    $pass++
}

if ($newCount -ge 3) {
    W-Pass "新密码已写入 $newCount 处（GLITCHTIP/GRAFANA/POSTGRES）"
    $pass++
} else {
    W-Fail "新密码写入处数不足（当前 $newCount，预期 >= 3）"
    $fail++
}

# ── Stage 2: 停止容器 + 删除 volume ────────────────────────────
Write-Section "Stage 2: 停止容器 + 删除 volume"

W-Info "停止监控容器..."
docker compose -f $composeFile down 2>&1 | Out-Null
W-Pass "容器已停止"

# 删除 grafana_data volume（必须，保存了旧密码）
W-Info "删除 grafana_data volume..."
try {
    docker volume rm agent_grafana_data 2>&1 | Out-Null
    W-Pass "grafana_data volume 已删除"
    $pass++
} catch {
    W-Info "grafana_data volume 不存在或已删除（可忽略）"
}

# Prometheus 数据可选保留
if (-not $KeepPrometheusData) {
    W-Info "删除 prometheus_data volume（-KeepPrometheusData:$false）..."
    try {
        docker volume rm agent_prometheus_data 2>&1 | Out-Null
        W-Pass "prometheus_data volume 已删除"
    } catch {
        W-Info "prometheus_data volume 不存在或已删除"
    }
} else {
    W-Info "保留 prometheus_data volume（Prometheus 历史数据保留）"
    $skip++
}

# ── Stage 3: 重新启动容器 ──────────────────────────────────────
Write-Section "Stage 3: 重新启动容器"

W-Info "启动监控容器（Grafana 用新密码首次初始化）..."
$upResult = docker compose -f $composeFile up -d 2>&1
$upResult | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }

if ($LASTEXITCODE -eq 0) {
    W-Pass "容器已启动"
    $pass++
} else {
    W-Fail "容器启动失败"
    $fail++
    Write-Host $upResult -ForegroundColor Red
    exit 1
}

# ── Stage 4: 等待 Grafana/Prometheus 就绪 ──────────────────────
Write-Section "Stage 4: 等待服务就绪"

# 等待 Grafana
$gfReady = $false
$elapsed = 0
$maxWait = 90
W-Info "等待 Grafana 就绪（最多 ${maxWait}s）..."
while ($elapsed -lt $maxWait) {
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:3000/api/health" -UseBasicParsing -TimeoutSec 3
        if ($r.StatusCode -eq 200) {
            $health = $r.Content | ConvertFrom-Json
            W-Pass "Grafana 就绪（database: $($health.database)）（${elapsed}s）"
            $gfReady = $true
            $pass++
            break
        }
    } catch {}
    Start-Sleep -Seconds 3
    $elapsed += 3
    if ($elapsed % 15 -eq 0) { W-Info "  等待中... (${elapsed}s)" }
}
if (-not $gfReady) {
    W-Fail "Grafana 在 ${maxWait}s 内未就绪"
    $fail++
}

# 等待 Prometheus
$promReady = $false
$elapsed = 0
$maxWait = 30
W-Info "等待 Prometheus 就绪..."
while ($elapsed -lt $maxWait) {
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:9090/-/healthy" -UseBasicParsing -TimeoutSec 3
        if ($r.StatusCode -eq 200) {
            W-Pass "Prometheus 就绪"
            $promReady = $true
            $pass++
            break
        }
    } catch {}
    Start-Sleep -Seconds 2
    $elapsed += 2
}
if (-not $promReady) {
    W-Fail "Prometheus 未就绪"
    $fail++
}

# ── Stage 5: 用新密码验证 Grafana API ──────────────────────────
Write-Section "Stage 5: 新密码 API 认证验证"

if ($gfReady) {
    # 读取 .env 中的用户名
    $envContent = Get-Content $envFile
    $gfUser = ($envContent | Where-Object { $_ -match "^GRAFANA_ADMIN_USER=" } | Select-Object -First 1) -replace "^GRAFANA_ADMIN_USER=", ""
    if (-not $gfUser) { $gfUser = "admin" }

    W-Info "用新密码调用 Grafana API（用户: $gfUser）..."

    # 使用手动 Base64 编码（避免 PSCredential 处理特殊字符的问题）
    $auth = "${gfUser}:${NewPwd}"
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($auth)
    $base64 = [System.Convert]::ToBase64String($bytes)
    $headers = @{ "Authorization" = "Basic $base64" }

    try {
        $apiResult = Invoke-RestMethod -Uri "http://localhost:3000/api/datasources" -Headers $headers -TimeoutSec 5 -ErrorAction Stop
        W-Pass "Grafana API 认证成功（数据源: $($apiResult.Count) 个）"
        $pass++
    } catch {
        $sc = $_.Exception.Response.StatusCode.value__
        if ($sc -eq 401) {
            W-Fail "Grafana API 认证失败（401）- 新密码未生效"
        } else {
            W-Fail "Grafana API 调用失败（状态码: $sc）"
        }
        $fail++
    }

    # 验证旧密码已失效（反向校验）
    W-Info "反向校验：旧密码应已失效..."
    $oldAuth = "${gfUser}:${oldPwd}"
    $oldBytes = [System.Text.Encoding]::UTF8.GetBytes($oldAuth)
    $oldBase64 = [System.Convert]::ToBase64String($oldBytes)
    $oldHeaders = @{ "Authorization" = "Basic $oldBase64" }
    try {
        $oldResult = Invoke-RestMethod -Uri "http://localhost:3000/api/datasources" -Headers $oldHeaders -TimeoutSec 5 -ErrorAction Stop
        W-Fail "旧密码仍可登录（不应发生）"
        $fail++
    } catch {
        $sc = $_.Exception.Response.StatusCode.value__
        if ($sc -eq 401) {
            W-Pass "旧密码已失效（401）- 密码轮换成功"
            $pass++
        } else {
            W-Fail "旧密码校验异常（状态码: $sc）"
            $fail++
        }
    }
} else {
    W-Fail "Grafana 未就绪，跳过 API 验证"
    $skip += 2
}

# ── Stage 6: 汇总报告 ──────────────────────────────────────────
Write-Section "Verification Summary"
$el = [math]::Round(((Get-Date) - $startTime).TotalSeconds, 1)
Write-Host "  Passed: $pass  Failed: $fail  Skipped: $skip  Elapsed: ${el}s"

if ($fail -eq 0) {
    Write-Host ""
    Write-Host "  [OK] 密码轮换成功，新密码已生效" -ForegroundColor Green
    Write-Host ""
    Write-Host "  ── 后续操作 ──" -ForegroundColor Cyan
    Write-Host "  1. Grafana: http://localhost:3000 （用户: admin / 新密码）" -ForegroundColor White
    Write-Host "  2. Prometheus: http://localhost:9090" -ForegroundColor White
    Write-Host "  3. 如需导入仪表盘，运行: python scripts/_import_dashboards.py" -ForegroundColor White
    exit 0
} else {
    Write-Host ""
    Write-Host "  [FAIL] 有 $fail 项验证失败，请查看上方输出" -ForegroundColor Red
    exit 1
}
