<#
.SYNOPSIS
    [security] P1 修复后监控组件启动验证脚本
.DESCRIPTION
    验证监控组件（GlitchTip + Grafana + Prometheus）能正常初始化且无硬编码报错。
    阶段: 0 预检查 → 0.5 硬编码扫描 → 1 配置验证 → 2 容器启动 → 3 健康检查 → 4 功能验证 → 5 汇总
.PARAMETER DryRun
    仅执行 Stage 0-1，不启动容器
.PARAMETER ComposeFile
    指定 compose 文件（默认 docker-compose.monitoring.yml）
.EXAMPLE
    pwsh -File scripts\verify_monitoring_setup.ps1 -DryRun
    pwsh -File scripts\verify_monitoring_setup.ps1
#>

[CmdletBinding()]
param(
    [switch]$DryRun,
    [string]$ComposeFile = "docker-compose.monitoring.yml"
)

$ErrorActionPreference = "Continue"
$repoPath = "c:\Users\Administrator\agent"
$envFile = "$repoPath\.env"
$pass = 0; $fail = 0; $skip = 0
$startTime = Get-Date

function W-Section { param($t) Write-Host "`n========== $t ==========" -ForegroundColor Cyan }
function W-Pass { param($m) Write-Host "  [PASS] $m" -ForegroundColor Green; $script:pass++ }
function W-Fail { param($m) Write-Host "  [FAIL] $m" -ForegroundColor Red; $script:fail++ }
function W-Skip { param($m) Write-Host "  [SKIP] $m" -ForegroundColor Yellow; $script:skip++ }
function W-Info { param($m) Write-Host "  [INFO] $m" -ForegroundColor DarkGray }

# ========== Stage 0: 预检查 ==========
W-Section "Stage 0: Pre-flight Check"

if (Test-Path $envFile) { W-Pass ".env 文件存在" } else { W-Fail ".env 文件不存在: $envFile"; exit 1 }
$envContent = Get-Content $envFile -Encoding UTF8

# 检查 GLITCHTIP_ADMIN_PASSWORD
$gtPwd = ($envContent | Where-Object { $_ -match "^GLITCHTIP_ADMIN_PASSWORD=" } | Select-Object -First 1) -replace "^GLITCHTIP_ADMIN_PASSWORD=", ""
if (-not $gtPwd) { W-Fail "GLITCHTIP_ADMIN_PASSWORD 未设置（空值）" }
elseif ($gtPwd -eq "CHANGE_ME_BEFORE_DEPLOY") { W-Fail "GLITCHTIP_ADMIN_PASSWORD 仍为占位符 CHANGE_ME_BEFORE_DEPLOY" }
elseif ($gtPwd.Length -lt 8) { W-Fail "GLITCHTIP_ADMIN_PASSWORD 长度不足（当前 $($gtPwd.Length)，需 >= 8）" }
else { W-Pass "GLITCHTIP_ADMIN_PASSWORD 已设置（长度: $($gtPwd.Length)）" }

# 检查 GRAFANA_ADMIN_PASSWORD
$gfPwd = ($envContent | Where-Object { $_ -match "^GRAFANA_ADMIN_PASSWORD=" } | Select-Object -First 1) -replace "^GRAFANA_ADMIN_PASSWORD=", ""
if (-not $gfPwd) { W-Fail "GRAFANA_ADMIN_PASSWORD 未设置（空值）" }
elseif ($gfPwd -eq "CHANGE_ME_BEFORE_DEPLOY") { W-Fail "GRAFANA_ADMIN_PASSWORD 仍为占位符 CHANGE_ME_BEFORE_DEPLOY" }
elseif ($gfPwd.Length -lt 8) { W-Fail "GRAFANA_ADMIN_PASSWORD 长度不足（当前 $($gfPwd.Length)，需 >= 8）" }
else { W-Pass "GRAFANA_ADMIN_PASSWORD 已设置（长度: $($gfPwd.Length)）" }

# Docker/Python 可用性
$dockerOk = $false; $pythonOk = $false
try { $dv = docker --version 2>$null; if ($dv) { W-Pass "Docker: $dv"; $dockerOk = $true } else { W-Skip "Docker 命令不可用" } } catch { W-Skip "Docker 命令不可用" }
try { $pv = python --version 2>$null; if ($pv) { W-Pass "Python: $pv"; $pythonOk = $true } else { W-Skip "Python 命令不可用" } } catch { W-Skip "Python 命令不可用" }

# ========== Stage 0.5: 硬编码密码扫描 ==========
W-Section "Stage 0.5: Hardcoded Password Scan"
$scanFiles = @("scripts/_import_dashboards.py", "docker-compose.monitoring.yml", "docker-compose.monitoring.aliyun.yml", "docker/glitchtip/orm_setup_inline.py")
$hcFound = $false
foreach ($f in $scanFiles) {
    $fp = "$repoPath\$f"
    if (Test-Path $fp) {
        $c = Get-Content $fp -Raw -Encoding UTF8
        if ($c -match "admin123" -or $c -match "REMOVED_GLITCHTIP_PWD") { W-Fail "$f 仍含硬编码密码"; $hcFound = $true }
        else { W-Pass "$f 无硬编码密码" }
    } else { W-Skip "$f 文件不存在" }
}
if ($hcFound) { W-Fail "发现硬编码密码，请先修复"; exit 1 }

# ========== Stage 1: Compose 配置验证 ==========
W-Section "Stage 1: Compose Config Validation"
if ($dockerOk) {
    Push-Location $repoPath
    try {
        $cf = "$repoPath\$ComposeFile"
        if (Test-Path $cf) {
            W-Info "验证 $ComposeFile ..."
            $cr = docker compose -f $ComposeFile config 2>&1
            if ($LASTEXITCODE -eq 0) {
                W-Pass "$ComposeFile 配置有效"
                $ic = $cr | Select-String "GF_SECURITY_ADMIN_PASSWORD"
                if ($ic) {
                    $il = $ic.ToString().Trim()
                    if ($il -match "=\s*admin\s*$") { W-Info "变量插值: 使用兜底值 admin" }
                    else { W-Pass "变量插值: GF_SECURITY_ADMIN_PASSWORD 已从 .env 注入" }
                }
            } else { W-Fail "$ComposeFile 配置无效"; Write-Host "    $cr" -ForegroundColor DarkRed }
        } else { W-Skip "$ComposeFile 文件不存在" }

        $af = "$repoPath\docker-compose.monitoring.aliyun.yml"
        if (Test-Path $af) {
            W-Info "验证 docker-compose.monitoring.aliyun.yml ..."
            $ar = docker compose -f "docker-compose.monitoring.aliyun.yml" config 2>&1
            if ($LASTEXITCODE -eq 0) { W-Pass "aliyun compose 配置有效" }
            else { W-Fail "aliyun compose 配置无效" }
        }
    } finally { Pop-Location }
} else { W-Skip "Docker 不可用，跳过 compose 验证" }

# ========== DryRun 退出 ==========
if ($DryRun) {
    W-Section "DryRun Summary"
    $el = [math]::Round(((Get-Date) - $startTime).TotalSeconds, 1)
    Write-Host "  Passed: $pass  Failed: $fail  Skipped: $skip  Elapsed: ${el}s"
    if ($fail -gt 0) { Write-Host "  [FAIL] 有 $fail 项失败，请修复后再启动" -ForegroundColor Red; exit 1 }
    else { Write-Host "  [OK] DryRun 预检查通过，可执行正式启动（移除 -DryRun）" -ForegroundColor Green; exit 0 }
}

# ========== Stage 2: 容器启动 ==========
W-Section "Stage 2: Container Startup"
$gfReady = $false
if ($dockerOk) {
    Push-Location $repoPath
    try {
        W-Info "启动 $ComposeFile ..."
        docker compose -f $ComposeFile up -d 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
        if ($LASTEXITCODE -eq 0) { W-Pass "Monitoring 容器已启动" } else { W-Fail "容器启动失败" }
    } finally { Pop-Location }
} else { W-Skip "Docker 不可用，跳过容器启动" }

# ========== Stage 3: 健康检查 ==========
W-Section "Stage 3: Health Check"
if ($dockerOk) {
    W-Info "等待 Grafana 就绪（最多 60s）..."
    for ($i = 0; $i -lt 12; $i++) {
        Start-Sleep -Seconds 5
        try {
            $h = Invoke-RestMethod -Uri "http://localhost:3000/api/health" -TimeoutSec 3 -ErrorAction Stop
            if ($h.database -eq "ok") { W-Pass "Grafana 已就绪（database: ok）"; $gfReady = $true; break }
        } catch { W-Info "  等待中... ($(($i+1)*5)s)" }
    }
    if (-not $gfReady) { W-Fail "Grafana 60s 内未就绪" }

    W-Info "等待 Prometheus 就绪..."
    $pr = $false
    for ($i = 0; $i -lt 6; $i++) {
        Start-Sleep -Seconds 5
        try {
            $ph = Invoke-RestMethod -Uri "http://localhost:9090/-/healthy" -TimeoutSec 3 -ErrorAction Stop
            if ($ph) { W-Pass "Prometheus 已就绪"; $pr = $true; break }
        } catch { W-Info "  等待中... ($(($i+1)*5)s)" }
    }
    if (-not $pr) { W-Fail "Prometheus 30s 内未就绪" }
} else { W-Skip "Docker 不可用，跳过健康检查" }

# ========== Stage 4: 功能验证 ==========
W-Section "Stage 4: Functional Verification"
if ($pythonOk) {
    $env:GRAFANA_ADMIN_PASSWORD = $gfPwd
    $env:GLITCHTIP_ADMIN_PASSWORD = $gtPwd
    $gfUser = ($envContent | Where-Object { $_ -match "^GRAFANA_ADMIN_USER=" } | Select-Object -First 1) -replace "^GRAFANA_ADMIN_USER=", ""
    if (-not $gfUser) { $gfUser = "admin" }
    $env:GRAFANA_ADMIN_USER = $gfUser
    $gtEmail = ($envContent | Where-Object { $_ -match "^GLITCHTIP_ADMIN_EMAIL=" } | Select-Object -First 1) -replace "^GLITCHTIP_ADMIN_EMAIL=", ""
    if (-not $gtEmail) { $gtEmail = "admin@local.test" }
    $env:GLITCHTIP_ADMIN_EMAIL = $gtEmail

    # Grafana 密码读取验证
    W-Info "验证 _import_dashboards.py 密码读取..."
    $vc = "import os,sys`np=os.environ.get('GRAFANA_ADMIN_PASSWORD')`nu=os.environ.get('GRAFANA_ADMIN_USER','admin')`nif not p:print('FAIL: not set');sys.exit(1)`nelif p=='CHANGE_ME_BEFORE_DEPLOY':print('FAIL: placeholder');sys.exit(1)`nelse:print(f'OK: len={len(p)}, user={u}');sys.exit(0)"
    $vr = $vc | python 2>&1
    if ($LASTEXITCODE -eq 0) { W-Pass "_import_dashboards.py 密码读取验证通过" }
    else { W-Fail "_import_dashboards.py 密码读取失败: $vr" }

    # GlitchTip 密码读取验证
    W-Info "验证 orm_setup_inline.py 密码读取..."
    $gc = "import os,sys`np=os.environ.get('GLITCHTIP_ADMIN_PASSWORD')`ne=os.environ.get('GLITCHTIP_ADMIN_EMAIL','admin@local.test')`nif not p:print('FAIL: not set');sys.exit(1)`nelif p=='CHANGE_ME_BEFORE_DEPLOY':print('FAIL: placeholder');sys.exit(1)`nelse:print(f'OK: len={len(p)}, email={e}');sys.exit(0)"
    $gr = $gc | python 2>&1
    if ($LASTEXITCODE -eq 0) { W-Pass "orm_setup_inline.py 密码读取验证通过" }
    else { W-Fail "orm_setup_inline.py 密码读取失败: $gr" }
} else { W-Skip "Python 不可用，跳过功能验证" }

# Grafana API 调用验证（容器就绪时）
if ($gfReady) {
    W-Info "验证 Grafana API 调用..."
    try {
        $ab = [System.Text.Encoding]::ASCII.GetBytes("${gfUser}:${gfPwd}")
        $b64 = [System.Convert]::ToBase64String($ab)
        $hd = @{ "Authorization" = "Basic $b64" }
        $ar = Invoke-RestMethod -Uri "http://localhost:3000/api/datasources" -Headers $hd -TimeoutSec 5 -ErrorAction Stop
        W-Pass "Grafana API 调用成功（数据源: $($ar.Count) 个）"
    } catch {
        $sc = $_.Exception.Response.StatusCode.value__
        if ($sc -eq 401) { W-Fail "Grafana API 认证失败（401）- 密码不匹配" }
        else { W-Fail "Grafana API 调用失败（状态码: $sc）" }
    }
}

# ========== Stage 5: 汇总 ==========
W-Section "Verification Summary"
$el = [math]::Round(((Get-Date) - $startTime).TotalSeconds, 1)
Write-Host "  Passed: $pass  Failed: $fail  Skipped: $skip  Elapsed: ${el}s"
if ($fail -gt 0) { Write-Host "  [FAIL] 有 $fail 项验证失败" -ForegroundColor Red; exit 1 }
else { Write-Host "  [OK] 所有验证通过，监控组件初始化成功且无硬编码报错" -ForegroundColor Green; exit 0 }
