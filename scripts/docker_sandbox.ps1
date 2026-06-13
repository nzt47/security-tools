﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿# Docker 沙盒环境切换脚本
# 使用方法：
#   .\scripts\docker_sandbox.ps1 enable    — 启用沙盒
#   .\scripts\docker_sandbox.ps1 disable   — 关闭沙盒
#   .\scripts\docker_sandbox.ps1 status    — 检查当前状态
#   .\scripts\docker_sandbox.ps1 build     — 构建镜像

param(
    [Parameter(Position=0)]
    [ValidateSet("enable", "disable", "status", "build")]
    [string]$Action = "status"
)

$ImageName = "yunshu-agent:latest"
$ContainerName = "digital-life-test"
$Port = 5678

function Write-Header($title) {
    Write-Host ""
    Write-Host ("=" * 56) -ForegroundColor Cyan
    Write-Host "  $title" -ForegroundColor Cyan
    Write-Host ("=" * 56) -ForegroundColor Cyan
    Write-Host ""
}

function Test-DockerRunning {
    try {
        docker info *> $null
        return $true
    } catch {
        Write-Host "[错误] Docker 未运行，请先启动 Docker Desktop" -ForegroundColor Red
        return $false
    }
}

# ── 启用沙盒 ──
if ($Action -eq "enable") {
    Write-Header "启用沙盒功能 (YUNSHU_FEATURE_SANDBOX=true)"

    if (-not (Test-DockerRunning)) { exit 1 }

    # 停止已有容器
    Write-Host "[1/3] 停止已有容器..." -ForegroundColor Yellow
    docker compose down 2>$null

    # 设置环境变量并启动
    Write-Host "[2/3] 启动容器（沙盒已启用）..." -ForegroundColor Yellow
    $env:YUNSHU_FEATURE_SANDBOX = "true"
    docker compose up -d

    if ($LASTEXITCODE -eq 0) {
        Write-Host "[3/3] 验证沙盒状态..." -ForegroundColor Yellow
        Start-Sleep -Seconds 3
        try {
            $response = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/sandbox/run" -Method POST -ContentType "application/json" -Body '{"code":"1+1"}' -ErrorAction Stop
            Write-Host "[成功] 沙盒已启用，接口返回: $($response | ConvertTo-Json -Compress)" -ForegroundColor Green
        } catch {
            Write-Host "[验证] 正在等待服务启动，请稍后手动验证..." -ForegroundColor Yellow
            Write-Host "  curl -s -X POST http://127.0.0.1:$Port/api/sandbox/run -H 'Content-Type: application/json' -d '{`"code`":`"1+1`"}'" -ForegroundColor White
        }
    } else {
        Write-Host "[错误] 容器启动失败" -ForegroundColor Red
        exit 1
    }
}

# ── 关闭沙盒 ──
elseif ($Action -eq "disable") {
    Write-Header "关闭沙盒功能 (YUNSHU_FEATURE_SANDBOX=false)"

    if (-not (Test-DockerRunning)) { exit 1 }

    Write-Host "[1/3] 停止已有容器..." -ForegroundColor Yellow
    docker compose down 2>$null

    Write-Host "[2/3] 启动容器（沙盒已关闭）..." -ForegroundColor Yellow
    $env:YUNSHU_FEATURE_SANDBOX = "false"
    docker compose up -d

    if ($LASTEXITCODE -eq 0) {
        Write-Host "[3/3] 验证沙盒状态..." -ForegroundColor Yellow
        Start-Sleep -Seconds 3
        try {
            $response = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/sandbox/run" -Method POST -ContentType "application/json" -Body '{"code":"1+1"}' -ErrorAction Stop
            Write-Host "[成功] 沙盒已关闭，接口返回 503: $($response | ConvertTo-Json -Compress)" -ForegroundColor Green
        } catch {
            # 503 会触发异常，这是预期行为
            if ($_.Exception.Response.StatusCode -eq 503) {
                Write-Host "[成功] 沙盒已关闭，接口返回 503（符合预期）" -ForegroundColor Green
            } else {
                Write-Host "[验证] 正在等待服务启动，请稍后手动验证..." -ForegroundColor Yellow
            }
        }
    } else {
        Write-Host "[错误] 容器启动失败" -ForegroundColor Red
        exit 1
    }
}

# ── 检查状态 ──
elseif ($Action -eq "status") {
    Write-Header "沙盒功能状态检查"

    # 检查容器环境变量
    Write-Host "[Docker] 检查容器环境变量..." -ForegroundColor Yellow
    $envCheck = docker compose exec digital-life env 2>$null | Select-String "YUNSHU"
    if ($envCheck) {
        Write-Host "  $envCheck" -ForegroundColor White
    } else {
        Write-Host "  容器未运行或环境变量未设置" -ForegroundColor Yellow
    }

    # 检查容器日志
    Write-Host ""
    Write-Host "[Docker] 检查容器日志..." -ForegroundColor Yellow
    $logCheck = docker compose logs digital-life 2>$null | Select-String "沙盒" | Select-Object -Last 3
    if ($logCheck) {
        $logCheck | ForEach-Object { Write-Host "  $_" -ForegroundColor White }
    } else {
        Write-Host "  未找到沙盒相关日志" -ForegroundColor Yellow
    }

    # 请求沙盒接口
    Write-Host ""
    Write-Host "[API] 请求沙盒接口..." -ForegroundColor Yellow
    try {
        $response = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/sandbox/run" -Method POST -ContentType "application/json" -Body '{"code":"1+1"}' -ErrorAction Stop
        Write-Host "  状态: 沙盒已启用 (HTTP 200)" -ForegroundColor Green
    } catch {
        $statusCode = $_.Exception.Response.StatusCode
        if ($statusCode -eq 503) {
            Write-Host "  状态: 沙盒已关闭 (HTTP 503)" -ForegroundColor Yellow
        } else {
            Write-Host "  状态: 无法连接 (服务可能未启动)" -ForegroundColor Red
        }
    }
}

# ── 构建镜像 ──
elseif ($Action -eq "build") {
    Write-Header "构建 Docker 镜像"

    if (-not (Test-DockerRunning)) { exit 1 }

    Write-Host "开始构建镜像 $ImageName ..." -ForegroundColor Yellow
    docker build -t $ImageName .

    if ($LASTEXITCODE -eq 0) {
        Write-Host "[成功] 镜像构建完成: $ImageName" -ForegroundColor Green
    } else {
        Write-Host "[错误] 镜像构建失败" -ForegroundColor Red
        exit 1
    }
}
