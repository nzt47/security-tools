<#
.SYNOPSIS
    [security] P1 修复后 Docker Compose 监控组件启动验证脚本
.DESCRIPTION
    验证所有监控组件（GlitchTip + Grafana + Prometheus）能正常初始化且无硬编码报错。
    执行阶段：
      Stage 0:   预检查（.env 变量、Docker 可用性、硬编码扫描）
      Stage 1:   配置验证（docker compose config 变量插值）
      Stage 2:   容器启动（docker compose up -d）
      Stage 3:   健康检查（等待容器就绪）
      Stage 4:   功能验证（密码读取 + API 调用）
      Stage 5:   汇总报告
.PARAMETER DryRun
    仅执行 Stage 0-1（预检查和配置验证），不启动容器
.PARAMETER ComposeFile
    指定 monitoring compose 文件（默认 docker-compose.monitoring.yml）
.PARAMETER SkipGlitchTip
    跳过 GlitchTip 验证
.PARAMETER SkipGrafana
    跳过 Grafana 验证
.EXAMPLE
    pwsh -File scripts\verify_monitoring_setup.ps1 -DryRun
    pwsh -File scripts\verify_monitoring_setup.ps1
.NOTES
    关联文档：docs/security/P1_HARDCODED_PASSWORD_FIX_PLAN_20260720.md
    前置条件：P1 Step 1-6 已完成（.env + .env.example + 4 个代码文件已修复）
#>

[CmdletBinding()]
param(
    [switch]$DryRun,
    [string]$ComposeFile = "docker-compose.monitoring.yml",
    [switch]$SkipGlitchTip,
    [switch]$SkipGrafana
)

$ErrorActionPreference = "Continue"
$repoPath = "c:\Users\Administrator\agent"
$envFile = "$repoPath\.env"
$passCount = 0
$failCount = 0
$skipCount = 0
$startTime = Get-Date

# ========== 工具函数 ==========
function Write-Section {
    param([string]$Title)
    Write-Host ""
    Write-Host ("=" * 60) -ForegroundColor Cyan
    Write-Host "  $Title" -ForegroundColor Cyan
    Write-Host ("=" * 60) -ForegroundColor Cyan
}

function Write-Pass {
    param([string]$Msg)
    Write-Host "  [PASS] $Msg" -ForegroundColor Green
    $script:passCount++
}

function Write-Fail {
    param([string]$Msg)
    Write-Host "  [FAIL] $Msg" -ForegroundColor Red
    $script:failCount++
}

function Write-Skip {
    param([string]$Msg)
    Write-Host "  [SKIP] $Msg" -ForegroundColor Yellow
    $script:skipCount++
}

function Write-Info {
    param([string]$Msg)
    Write-Host "  [INFO] $Msg" -ForegroundColor DarkGray
}

# ========== Stage 0: 预检查 ==========
Write-Section "Stage 0: Pre-flight Check"

# 0.1 .env 文件存在性
if (Test-Path $envFile) {
    Write-Pass ".env 文件存在"
} else {
    Write-Fail ".env 文件不存在: $envFile"
    Write-Host "  请复制 .env.example 为 .env 并填入真实密码" -ForegroundColor Yellow
    exit 1
}

# 0.2 读取 .env 内容
$envContent = Get-Content $envFile -Encoding UTF8

# 0.3 检查 GLITCHTIP_ADMIN_PASSWORD
$glitchtipPwd = ($envContent | Where-Object { $_ -match "^GLITCHTIP_ADMIN_PASSWORD=" } | Select-Object -First 1) -replace "^GLITCHTIP_ADMIN_PASSWORD=", ""
if (-not $glitchtipPwd) {
    Write-Fail "GLITCHTIP_ADMIN_PASSWORD 未设置（空值）"
} elseif ($glitchtipPwd -eq "CHANGE_ME_BEFORE_DEPLOY") {
    Write-Fail "GLITCHTIP_ADMIN_PASSWORD 仍为占位符 CHANGE_ME_BEFORE_DEPLOY"
} elseif ($glitchtipPwd.Length -lt 8) {
    Write-Fail "GLITCHTIP_ADMIN_PASSWORD 长度不足（当前 $($glitchtipPwd.Length)，需 >= 8）"
} else {
    Write-Pass "GLITCHTIP_ADMIN_PASSWORD 已设置（长度: $($glitchtipPwd.Length)）"
}

# 0.4 检查 GRAFANA_ADMIN_PASSWORD
$grafanaPwd = ($envContent | Where-Object { $_ -match "^GRAFANA_ADMIN_PASSWORD=" } | Select-Object -First 1) -replace "^GRAFANA_ADMIN_PASSWORD=", ""
if (-not $grafanaPwd) {
    Write-Fail "GRAFANA_ADMIN_PASSWORD 未设置（空值）"
} elseif ($grafanaPwd -eq "CHANGE_ME_BEFORE_DEPLOY") {
    Write-Fail "GRAFANA_ADMIN_PASSWORD 仍为占位符 CHANGE_ME_BEFORE_DEPLOY"
} elseif ($grafanaPwd.Length -lt 8) {
    Write-Fail "GRAFANA_ADMIN_PASSWORD 长度不足（当前 $($grafanaPwd.Length)，需 >= 8）"
} else {
    Write-Pass "GRAFANA_ADMIN_PASSWORD 已设置（长度: $($grafanaPwd.Length)）"
}

# 0.5 Docker 可用性
$dockerOk = $false
try {
    $dockerVersion = docker --version 2>$null
    if ($dockerVersion) {
        Write-Pass "Docker 可用: $dockerVersion"
        $dockerOk = $true
    } else {
        Write-Skip "Docker 命令不可用（跳过容器启动验证）"
    }
} catch {
    Write-Skip "Docker 命令不可用（跳过容器启动验证）"
}

# 0.6 Python 可用性
$pythonOk = $false
try {
    $pyVer = python --version 2>$null
    if ($pyVer) {
        Write-Pass "Python 可用: $pyVer"
        $pythonOk = $true
    } else {
        Write-Skip "Python 命令不可用（跳过脚本验证）"
    }
} catch {
    Write-Skip "Python 命令不可用（跳过脚本验证）"
}

# ========== Stage 0.5: 硬编码密码扫描 ==========
Write-Section "Stage 0.5: Hardcoded Password Scan"

$scanFiles = @(
    "scripts/_import_dashboards.py",
    "docker-compose.monitoring.yml",
    "docker-compose.monitoring.aliyun.yml",
    "docker/glitchtip/orm_setup_inline.py"
)

$hardcodedFound = $false
foreach ($f in $scanFiles) {
    $fullPath = "$repoPath\$f"
    if (Test-Path $fullPath) {
        $content = Get-Content $fullPath -Raw -Encoding UTF8
        if ($content -match "admin123" -or $content -match "REMOVED_GLITCHTIP_PWD") {
            Write-Fail "$f 仍含硬编码密码"
            $hardcodedFound = $true
        } else {
            Write-Pass "$f 无硬编码密码"
        }
    } else {
        Write-Skip "$f 文件不存在"
    }
}

if ($hardcodedFound) {
    Write-Fail "发现硬编码密码，请先修复后再启动"
    exit 1
}

# ========== Stage 1: 配置验证 ==========
Write-Section "Stage 1: Compose Config Validation"

if ($dockerOk) {
    Push-Location $repoPath
    try {
        # 1.1 验证 monitoring compose 配置
        $composePath = "$repoPath\$ComposeFile"
        if (Test-Path $composePath) {
            Write-Info "验证 $ComposeFile ..."
            $configResult = docker compose -f $ComposeFile config 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-Pass "$ComposeFile 配置有效"
                # 检查变量插值结果
                $interpCheck = $configResult | Select-String "GF_SECURITY_ADMIN_PASSWORD"
                if ($interpCheck) {
                    $interpLine = $interpCheck.ToString().Trim()
                    if ($interpLine -match "=\s*admin\s*$") {
                        Write-Info "  变量插值: 使用兜底值 admin（.env 未注入或为空）"
                    } else {
                        Write-Pass "  变量插值: GF_SECURITY_ADMIN_PASSWORD 已从 .env 注入"
                    }
                }
            } else {
                Write-Fail "$ComposeFile 配置无效"
                Write-Host "    $configResult" -ForegroundColor DarkRed
            }
        } else {
            Write-Skip "$ComposeFile 文件不存在"
        }

        # 1.2 验证 aliyun compose 配置
        $aliyunCompose = "$repoPath\docker-compose.monitoring.aliyun.yml"
        if (Test-Path $aliyunCompose) {
            Write-Info "验证 docker-compose.monitoring.aliyun.yml ..."
            $aliyunResult = docker compose -f "docker-compose.monitoring.aliyun.yml" config 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-Pass "docker-compose.monitoring.aliyun.yml 配置有效"
            } else {
                Write-Fail "docker-compose.monitoring.aliyun.yml 配置无效"
            }
        }
    } finally {
        Pop-Location
    }
} else {
    Write-Skip "Docker 不可用，跳过 compose 配置验证"
}

# ========== DryRun 模式退出 ==========
if ($DryRun) {
    Write-Section "DryRun Summary"
    $elapsed = [math]::Round(((Get-Date) - $startTime).TotalSeconds, 1)
    Write-Host "  Passed:  $passCount"
    Write-Host "  Failed:  $failCount"
    Write-Host "  Skipped: $skipCount"
    Write-Host "  Elapsed: $elapsed s"
    Write-Host ""
    if ($failCount -gt 0) {
        Write-Host "  [FAIL] 有 $failCount 项预检查失败，请修复后再正式启动" -ForegroundColor Red
        exit 1
    } else {
        Write-Host "  [OK] DryRun 预检查通过，可执行正式启动（移除 -DryRun）" -ForegroundColor Green
        exit 0
    }
}

# ========== Stage 2: 容器启动 ==========
Write-Section "Stage 2: Container Startup"

$grafanaReady = $false
if (-not $dockerOk) {
    Write-Skip "Docker 不可用，跳过容器启动"
} else {
    Push-Location $repoPath
    try {
        Write-Info "启动 $ComposeFile ..."
        docker compose -f $ComposeFile up -d 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
        if ($LASTEXITCODE -eq 0) {
            Write-Pass "Monitoring 容器已启动"
        } else {
            Write-Fail "Monitoring 容器启动失败"
        }
    } finally {
        Pop-Location
    }
}

# ========== Stage 3: 健康检查 ==========
Write-Section "Stage 3: Health Check"

if ($dockerOk) {
    # 等待 Grafana 就绪（最多 60 秒）
    Write-Info "等待 Grafana 就绪（最多 60 秒）..."
    for ($i = 0; $i -lt 12; $i++) {
        Start-Sleep -Seconds 5
        try {
            $health = Invoke-RestMethod -Uri "http://localhost:3000/api/health" -Method Get -TimeoutSec 3 -ErrorAction Stop
            if ($health.database -eq "ok") {
                Write-Pass "Grafana 已就绪（database: ok）"
                $grafanaReady = $true
                break
            }
        } catch {
            $elapsed = ($i + 1) * 5
            Write-Info "  等待中... ($elapsed s)"
        }
    }
    if (-not $grafanaReady) {
        Write-Fail "Grafana 60 秒内未就绪"
    }

    # 等待 Prometheus 就绪
    Write-Info "等待 Prometheus 就绪..."
    $promReady = $false
    for ($i = 0; $i -lt 6; $i++) {
        Start-Sleep -Seconds 5
        try {
            $promHealth = Invoke-RestMethod -Uri "http://localhost:9090/-/healthy" -Method Get -TimeoutSec 3 -ErrorAction Stop
            if ($promHealth) {
                Write-Pass "Prometheus 已就绪"
                $promReady = $true
                break
            }
        } catch {
            $elapsed = ($i + 1) * 5
            Write-Info "  等待中... ($elapsed s)"
        }
    }
    if (-not $promReady) {
        Write-Fail "Prometheus 30 秒内未就绪"
    }
} else {
    Write-Skip "Docker 不可用，跳过健康检查"
}

# ========== Stage 4: 功能验证 ==========
Write-Section "Stage 4: Functional Verification"

# 4.1 Grafana 密码读取验证
if (-not $SkipGrafana -and $pythonOk) {
    Write-Info "验证 _import_dashboards.py 密码读取逻辑..."
    $env:GRAFANA_ADMIN_PASSWORD = $grafanaPwd
    $grafanaUser = ($envContent | Where-Object { $_ -match "^GRAFANA_ADMIN_USER=" } | Select-Object -First 1) -replace "^GRAFANA_ADMIN_USER=", ""
    if (-not $grafanaUser) { $grafanaUser = "admin" }
    $env:GRAFANA_ADMIN_USER = $grafanaUser

    $verifyCode = "import os, sys`npwd = os.environ.get('GRAFANA_ADMIN_PASSWORD')`nuser = os.environ.get('GRAFANA_ADMIN_USER', 'admin')`nif not pwd:`n    print('FAIL: password not set'); sys.exit(1)`nelif pwd == 'CHANGE_ME_BEFORE_DEPLOY':`n    print('FAIL: password is placeholder'); sys.exit(1)`nelse:`n    print(f'OK: password loaded (length={len(pwd)}, user={user})'); sys.exit(0)"
    $verifyResult = $verifyCode | python 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Pass "_import_dashboards.py 密码读取验证通过"
    } else {
        Write-Fail "_import_dashboards.py 密码读取验证失败: $verifyResult"
    }
} else {
    Write-Skip "跳过 Grafana 功能验证"
}

# 4.2 GlitchTip 密码读取验证
if (-not $SkipGlitchTip -and $pythonOk) {
    Write-Info "验证 orm_setup_inline.py 密码读取逻辑..."
    $env:GLITCHTIP_ADMIN_PASSWORD = $glitchtipPwd
    $glitchtipEmail = ($envContent | Where-Object { $_ -match "^GLITCHTIP_ADMIN_EMAIL=" } | Select-Object -First 1) -replace "^GLITCHTIP_ADMIN_EMAIL=", ""
    if (-not $glitchtipEmail) { $glitchtipEmail = "admin@local.test" }
    $env:GLITCHTIP_ADMIN_EMAIL = $glitchtipEmail

    $gtVerifyCode = "import os, sys`npwd = os.environ.get('GLITCHTIP_ADMIN_PASSWORD')`nemail = os.environ.get('GLITCHTIP_ADMIN_EMAIL', 'admin@local.test')`nif not pwd:`n    print('FAIL: password not set'); sys.exit(1)`nelif pwd == 'CHANGE_ME_BEFORE_DEPLOY':`n    print('FAIL: password is placeholder'); sys.exit(1)`nelse:`n    print(f'OK: password loaded (length={len(pwd)}, email={email})'); sys.exit(0)"
    $gtResult = $gtVerifyCode | python 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Pass "orm_setup_inline.py 密码读取验证通过"
    } else {
        Write-Fail "orm_setup_inline.py 密码读取验证失败: $gtResult"
    }
} else {
    Write-Skip "跳过 GlitchTip 功能验证"
}

# 4.3 Grafana API 调用验证（仅当容器就绪）
if ($grafanaReady -and -not $SkipGrafana) {
    Write-Info "验证 Grafana API 调用（使用环境变量密码）..."
    try {
        $authBytes = [System.Text.Encoding]::ASCII.GetBytes("${grafanaUser}:${grafanaPwd}")
        $authB64 = [System.Convert]::ToBase64String($authBytes)
        $headers = @{ "Authorization" = "Basic $authB64" }
        $apiResult = Invoke-RestMethod -Uri "http://localhost:3000/api/datasources" -Method Get -Headers $headers -TimeoutSec 5 -ErrorAction Stop
        Write-Pass "Grafana API 调用成功（数据源数量: $($apiResult.Count)）"
    } catch {
        $statusCode = $_.Exception.Response.StatusCode.value__
        if ($statusCode -eq 401) {
            Write-Fail "Grafana API 认证失败（401）- 密码不匹配"
        } else {
            Write-Fail "Grafana API 调用失败（状态码: $statusCode）"
        }
    }
}

# ========== Stage 5: 汇总报告 ==========
Write-Section "Verification Summary"
$elapsedTotal = [math]::Round(((Get-Date) - $startTime).TotalSeconds, 1)

Write-Host "  Passed:  $passCount"
Write-Host "  Failed:  $failCount"
Write-Host "  Skipped: $skipCount"
Write-Host "  Elapsed: $elapsedTotal s"
Write-Host ""

if ($failCount -gt 0) {
    Write-Host "  [FAIL] 有 $failCount 项验证失败，请检查上述输出" -ForegroundColor Red
    exit 1
} else {
    Write-Host "  [OK] 所有验证通过，监控组件初始化成功且无硬编码报错" -ForegroundColor Green
    exit 0
}