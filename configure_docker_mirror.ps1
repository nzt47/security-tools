# Yunshu 监控栈 - Docker 镜像加速器自动配置脚本
# 适用于 Windows 10/11 + Docker Desktop

$ErrorActionPreference = "Stop"

Write-Host "`n" -NoNewline
Write-Host "╔══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║     🔧  Docker 镜像加速器自动配置工具                    ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# 1. 检查 Docker Desktop 是否运行
Write-Host "[1/6] 检查 Docker Desktop 状态..." -ForegroundColor Yellow
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

# 2. 备份当前配置
Write-Host "`n[2/6] 备份当前 Docker 配置..." -ForegroundColor Yellow

$dockerConfigPath = "$env:USERPROFILE\.docker\daemon.json"
$backupPath = "$env:USERPROFILE\.docker\daemon.json.backup.$(Get-Date -Format 'yyyyMMdd-HHmmss')"

if (Test-Path $dockerConfigPath) {
    Copy-Item $dockerConfigPath $backupPath
    Write-Host "   ✅ 配置已备份：$backupPath" -ForegroundColor Green
} else {
    Write-Host "   ℹ️  当前无配置文件，将创建新配置" -ForegroundColor Yellow
}

# 3. 创建新的 Docker 配置
Write-Host "`n[3/6] 配置 Docker 镜像加速器..." -ForegroundColor Yellow

# 镜像加速器列表（优先级从高到低）
$mirrors = @(
    "https://docker.mirrors.ustc.edu.cn",
    "https://registry.docker-cn.com",
    "https://hub-mirror.c.163.com",
    "https://mirror.baidubce.com",
    "https://c.163.com/hub"
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
    Write-Host "`n   配置的镜像加速器:" -ForegroundColor Cyan
    for ($i = 0; $i -lt $mirrors.Count; $i++) {
        Write-Host "   $($i + 1). $($mirrors[$i])" -ForegroundColor White
    }
} catch {
    Write-Host "   ❌ 保存配置失败：$_" -ForegroundColor Red
    exit 1
}

# 4. 重启 Docker Desktop
Write-Host "`n[4/6] 重启 Docker Desktop 以应用配置..." -ForegroundColor Yellow

Write-Host "   正在停止 Docker 服务..." -ForegroundColor Cyan
try {
    # 尝试通过 API 重启 Docker
    Restart-Service -Name "com.docker.service" -Force -ErrorAction SilentlyContinue
} catch {
    Write-Host "   ⚠️  无法通过服务重启，需要手动重启 Docker Desktop" -ForegroundColor Yellow
}

Write-Host "`n   ⚠️  请手动重启 Docker Desktop:" -ForegroundColor Yellow
Write-Host "   1. 右键点击系统托盘的 Docker 图标" -ForegroundColor Cyan
Write-Host "   2. 选择 'Quit Docker Desktop'" -ForegroundColor Cyan
Write-Host "   3. 等待 10 秒后重新启动 Docker Desktop" -ForegroundColor Cyan
Write-Host ""
Write-Host "   或者运行以下命令自动重启:" -ForegroundColor Yellow
Write-Host "   Stop-Process -Name 'Docker Desktop' -Force" -ForegroundColor Cyan
Write-Host ""

$response = Read-Host "   是否现在自动重启 Docker Desktop? (Y/N)"
if ($response -eq 'Y' -or $response -eq 'y') {
    try {
        Stop-Process -Name "Docker Desktop" -Force -ErrorAction SilentlyContinue
        Write-Host "   ✅ Docker Desktop 已停止" -ForegroundColor Green
        Write-Host "   等待 10 秒后自动重启..." -ForegroundColor Cyan
        Start-Sleep -Seconds 10
        
        Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
        Write-Host "   ✅ Docker Desktop 已重启" -ForegroundColor Green
        Write-Host "   等待 Docker 完全启动..." -ForegroundColor Cyan
        Start-Sleep -Seconds 30
    } catch {
        Write-Host "   ⚠️  自动重启失败，请手动重启 Docker Desktop" -ForegroundColor Yellow
        Write-Host "   重启后继续执行下一步..." -ForegroundColor Cyan
        Read-Host "   按 Enter 键继续"
    }
} else {
    Write-Host "   请在重启 Docker Desktop 后手动运行下一步" -ForegroundColor Yellow
    Read-Host "   按 Enter 键继续"
}

# 5. 验证配置
Write-Host "`n[5/6] 验证 Docker 配置..." -ForegroundColor Yellow

try {
    $dockerInfo = docker info --format "{{.RegistryConfig.Mirrors}}" 2>$null
    if ($dockerInfo) {
        Write-Host "   ✅ Docker 配置已更新" -ForegroundColor Green
        Write-Host "   当前镜像加速器：$dockerInfo" -ForegroundColor Cyan
    } else {
        # 尝试另一种方式验证
        $dockerInfoJson = docker info --format '{{json .RegistryConfig}}' 2>$null
        if ($dockerInfoJson) {
            Write-Host "   ✅ Docker 配置已更新" -ForegroundColor Green
            Write-Host "   配置详情：$dockerInfoJson" -ForegroundColor Cyan
        } else {
            Write-Host "   ⚠️  无法验证配置，但可能已生效" -ForegroundColor Yellow
        }
    }
} catch {
    Write-Host "   ⚠️  验证失败：$_" -ForegroundColor Yellow
}

# 6. 测试镜像拉取
Write-Host "`n[6/6] 测试镜像拉取..." -ForegroundColor Yellow

$testImage = "registry.cn-hangzhou.aliyuncs.com/prometheus/prometheus:v2.45.0"
Write-Host "   尝试拉取测试镜像：$testImage" -ForegroundColor Cyan

try {
    docker pull $testImage
    Write-Host "   ✅ 镜像拉取成功!" -ForegroundColor Green
    
    # 清理测试镜像
    Write-Host "   清理测试镜像..." -ForegroundColor Cyan
    docker rmi $testImage -f 2>$null
} catch {
    Write-Host "   ❌ 镜像拉取失败：$_" -ForegroundColor Red
    Write-Host ""
    Write-Host "   可能的原因:" -ForegroundColor Yellow
    Write-Host "   1. Docker Desktop 未完全重启" -ForegroundColor Cyan
    Write-Host "   2. 镜像加速器不可用" -ForegroundColor Cyan
    Write-Host "   3. 网络连接问题" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "   建议:" -ForegroundColor Yellow
    Write-Host "   1. 完全重启 Docker Desktop" -ForegroundColor Cyan
    Write-Host "   2. 尝试其他镜像加速器" -ForegroundColor Cyan
    Write-Host "   3. 使用 docker-compose.monitoring.aliyun.yml 直接拉取" -ForegroundColor Cyan
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
Write-Host "   1. 启动监控栈:" -ForegroundColor White
Write-Host "      docker-compose -f docker-compose.monitoring.aliyun.yml up -d" -ForegroundColor Cyan
Write-Host ""
Write-Host "   2. 查看日志:" -ForegroundColor White
Write-Host "      docker-compose -f docker-compose.monitoring.aliyun.yml logs -f" -ForegroundColor Cyan
Write-Host ""
Write-Host "   3. 访问服务:" -ForegroundColor White
Write-Host "      Prometheus: http://localhost:9090" -ForegroundColor Cyan
Write-Host "      Grafana: http://localhost:3000 (admin/admin123)" -ForegroundColor Cyan

Write-Host "`n💡 提示:" -ForegroundColor Yellow
Write-Host "   - 如果仍然无法拉取镜像，请使用离线导入方式" -ForegroundColor White
Write-Host "   - 参考文档：offline_image_import_guide.md" -ForegroundColor Cyan

Write-Host ""
