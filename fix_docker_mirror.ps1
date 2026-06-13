# Yunshu 监控栈 - Docker 镜像加速器自动修复脚本
# 解决国内无法访问 Docker Hub 的问题

$ErrorActionPreference = "Continue"

Write-Host "`n" -NoNewline
Write-Host "╔══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║     🔧  Docker 镜像加速器自动修复工具                    ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# 1. 检查 Docker 状态
Write-Host "[1/7] 检查 Docker Desktop 状态..." -ForegroundColor Yellow
try {
    $dockerVersion = docker version --format "{{.Server.Version}}" 2>$null
    if ($dockerVersion) {
        Write-Host "   ✅ Docker 运行正常 (版本：$dockerVersion)" -ForegroundColor Green
    } else {
        throw "Docker 未响应"
    }
} catch {
    Write-Host "   ❌ Docker Desktop 未运行!" -ForegroundColor Red
    Write-Host ""
    Write-Host "   请先启动 Docker Desktop，然后重新运行此脚本" -ForegroundColor Yellow
    exit 1
}

# 2. 检查当前镜像配置
Write-Host "`n[2/7] 检查当前镜像配置..." -ForegroundColor Yellow
$registryConfig = docker info --format '{{json .RegistryConfig}}' 2>$null | ConvertFrom-Json
if ($registryConfig.Mirrors -and $registryConfig.Mirrors.Count -gt 0) {
    Write-Host "   ℹ️  已配置镜像加速器:" -ForegroundColor Cyan
    $registryConfig.Mirrors | ForEach-Object { Write-Host "      - $_" }
} else {
    Write-Host "   ⚠️  未配置镜像加速器" -ForegroundColor Yellow
}

# 3. 创建备份
Write-Host "`n[3/7] 备份当前配置..." -ForegroundColor Yellow
$dockerConfigPath = "$env:USERPROFILE\.docker\daemon.json"
$backupPath = "$env:USERPROFILE\.docker\daemon.json.backup.$(Get-Date -Format 'yyyyMMdd-HHmmss')"

if (Test-Path $dockerConfigPath) {
    Copy-Item $dockerConfigPath $backupPath
    Write-Host "   ✅ 配置已备份：$backupPath" -ForegroundColor Green
} else {
    Write-Host "   ℹ️  当前无配置文件" -ForegroundColor Yellow
}

# 4. 创建新的 Docker 配置
Write-Host "`n[4/7] 配置 Docker 镜像加速器..." -ForegroundColor Yellow

# 镜像加速器列表（2026 年可用）
$mirrors = @(
    "https://docker.m.daocloud.io",
    "https://docker.1panel.live",
    "https://hub.rat.dev",
    "https://dhub.kubesre.xyz",
    "https://docker.fxxk.dedyn.io",
    "https://registry.docker.cn"
)

# 创建配置对象
$dockerConfig = @{
    "registry-mirrors" = $mirrors
    "max-concurrent-downloads" = 10
    "log-level" = "info"
    "debug" = $false
}

# 确保 .docker 目录存在
$dockerConfigDir = "$env:USERPROFILE\.docker"
if (-not (Test-Path $dockerConfigDir)) {
    New-Item -ItemType Directory -Path $dockerConfigDir -Force | Out-Null
    Write-Host "   ✅ 创建配置目录：$dockerConfigDir" -ForegroundColor Green
}

# 保存配置文件
try {
    $dockerConfig | ConvertTo-Json -Depth 10 | Out-File -FilePath $dockerConfigPath -Encoding UTF8
    Write-Host "   ✅ 配置文件已保存：$dockerConfigPath" -ForegroundColor Green
    
    # 显示配置内容
    Write-Host "`n   配置的镜像加速器 (按优先级排序):" -ForegroundColor Cyan
    for ($i = 0; $i -lt $mirrors.Count; $i++) {
        Write-Host "   $($i + 1). $($mirrors[$i])" -ForegroundColor White
    }
} catch {
    Write-Host "   ❌ 保存配置失败：$_" -ForegroundColor Red
    Write-Host ""
    Write-Host "   请手动创建文件：$dockerConfigPath" -ForegroundColor Yellow
    Write-Host "   内容如下:" -ForegroundColor Cyan
    Write-Host ""
    $dockerConfig | ConvertTo-Json -Depth 10
    exit 1
}

# 5. 重启 Docker Desktop
Write-Host "`n[5/7] 重启 Docker Desktop 以应用配置..." -ForegroundColor Yellow
Write-Host "   正在停止 Docker Desktop..." -ForegroundColor Cyan

try {
    Stop-Process -Name "Docker Desktop" -Force -ErrorAction SilentlyContinue
    Write-Host "   ✅ Docker Desktop 已停止" -ForegroundColor Green
} catch {
    Write-Host "   ⚠️  停止失败，请手动关闭 Docker Desktop" -ForegroundColor Yellow
}

Write-Host "   等待 10 秒..." -ForegroundColor Cyan
Start-Sleep -Seconds 10

Write-Host "   正在启动 Docker Desktop..." -ForegroundColor Cyan
try {
    Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    Write-Host "   ✅ Docker Desktop 已启动" -ForegroundColor Green
} catch {
    Write-Host "   ❌ 启动失败，请手动启动 Docker Desktop" -ForegroundColor Red
    Write-Host "   路径：C:\Program Files\Docker\Docker\Docker Desktop.exe" -ForegroundColor Yellow
}

Write-Host "   等待 Docker 完全启动 (约 30 秒)..." -ForegroundColor Cyan
Start-Sleep -Seconds 30

# 6. 验证配置
Write-Host "`n[6/7] 验证 Docker 配置..." -ForegroundColor Yellow

try {
    $dockerInfo = docker info --format '{{json .RegistryConfig}}' 2>$null | ConvertFrom-Json
    if ($dockerInfo.Mirrors -and $dockerInfo.Mirrors.Count -gt 0) {
        Write-Host "   ✅ Docker 配置已更新" -ForegroundColor Green
        Write-Host "   当前镜像加速器:" -ForegroundColor Cyan
        $dockerInfo.Mirrors | ForEach-Object { Write-Host "      - $_" }
    } else {
        Write-Host "   ⚠️  配置可能未生效，请重启 Docker Desktop" -ForegroundColor Yellow
    }
} catch {
    Write-Host "   ⚠️  验证失败：$_" -ForegroundColor Yellow
}

# 7. 测试镜像拉取
Write-Host "`n[7/7] 测试镜像拉取..." -ForegroundColor Yellow

$testImage = "prom/prometheus:latest"
Write-Host "   尝试拉取测试镜像：$testImage" -ForegroundColor Cyan

try {
    $pullOutput = docker pull $testImage 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "   ✅ 镜像拉取成功!" -ForegroundColor Green
        
        # 显示拉取详情
        $pullOutput | Where-Object { $_ -match "Pulling|Downloaded|Extracting" } | 
            ForEach-Object { Write-Host "   $_" }
        
        # 清理测试镜像
        Write-Host "   清理测试镜像..." -ForegroundColor Cyan
        docker rmi $testImage -f 2>$null | Out-Null
    } else {
        Write-Host "   ❌ 镜像拉取失败" -ForegroundColor Red
        Write-Host ""
        Write-Host "   可能的原因:" -ForegroundColor Yellow
        Write-Host "   1. Docker Desktop 未完全重启" -ForegroundColor Cyan
        Write-Host "   2. 镜像加速器不可用或已失效" -ForegroundColor Cyan
        Write-Host "   3. 网络连接问题" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "   建议:" -ForegroundColor Yellow
        Write-Host "   1. 完全重启 Docker Desktop (Quit 后重新启动)" -ForegroundColor Cyan
        Write-Host "   2. 尝试其他镜像加速器" -ForegroundColor Cyan
        Write-Host "   3. 参考：docker_startup_troubleshooting.md" -ForegroundColor Cyan
    }
} catch {
    Write-Host "   ❌ 镜像拉取异常：$_" -ForegroundColor Red
}

# 完成
Write-Host "`n" -NoNewline
Write-Host "╔══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║                  🎉 配置完成!                            ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan

Write-Host "`n📋 配置摘要:" -ForegroundColor Yellow
Write-Host "   配置文件：$dockerConfigPath" -ForegroundColor White
Write-Host "   备份文件：$backupPath" -ForegroundColor White
Write-Host "   镜像加速器数量：$($mirrors.Count)" -ForegroundColor White

Write-Host "`n🚀 下一步操作:" -ForegroundColor Yellow
Write-Host "   1. 拉取 Prometheus 和 Grafana 镜像:" -ForegroundColor White
Write-Host "      docker pull prom/prometheus:latest" -ForegroundColor Cyan
Write-Host "      docker pull grafana/grafana:latest" -ForegroundColor Cyan
Write-Host ""
Write-Host "   2. 启动监控栈:" -ForegroundColor White
Write-Host "      docker-compose -f docker-compose.monitoring.yml up -d" -ForegroundColor Cyan
Write-Host ""
Write-Host "   3. 验证服务:" -ForegroundColor White
Write-Host "      docker-compose -f docker-compose.monitoring.yml ps" -ForegroundColor Cyan
Write-Host ""
Write-Host "   4. 访问服务:" -ForegroundColor White
Write-Host "      Prometheus: http://localhost:9090" -ForegroundColor Cyan
Write-Host "      Grafana: http://localhost:3000 (admin/admin123)" -ForegroundColor Cyan

Write-Host "`n💡 提示:" -ForegroundColor Yellow
Write-Host "   - 如果拉取仍然失败，请查看 docker_startup_troubleshooting.md" -ForegroundColor White
Write-Host "   - 或使用离线镜像导入方式 (参考 offline_image_import_guide.md)" -ForegroundColor White

Write-Host ""
