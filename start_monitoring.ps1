# Yunshu 监控栈启动脚本
# 适用于 Windows PowerShell

$ErrorActionPreference = "Stop"

Write-Host "`n" -NoNewline
Write-Host "╔══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║     🚀  Yunshu 监控栈启动工具                            ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# 1. 检查 Docker
Write-Host "[1/5] 检查 Docker 状态..." -ForegroundColor Yellow
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
    Write-Host "   请先启动 Docker Desktop:" -ForegroundColor Yellow
    Write-Host "   1. 在开始菜单搜索 'Docker Desktop'" -ForegroundColor Cyan
    Write-Host "   2. 点击启动并等待图标变绿" -ForegroundColor Cyan
    Write-Host "   3. 然后重新运行此脚本" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "   或者运行以下命令自动启动:" -ForegroundColor Yellow
    Write-Host "   Start-Process 'C:\Program Files\Docker\Docker\Docker Desktop.exe'" -ForegroundColor Cyan
    exit 1
}

# 2. 检查配置文件
Write-Host "`n[2/5] 检查配置文件..." -ForegroundColor Yellow
$configFiles = @(
    "docker-compose.monitoring.yml",
    "monitoring/prometheus.yml",
    "monitoring/alerts_production.yml",
    "monitoring/grafana/datasources/prometheus.yml",
    "monitoring/grafana/dashboards/yunshu-alerts-monitor.json"
)

$allFilesExist = $true
foreach ($file in $configFiles) {
    if (Test-Path $file) {
        Write-Host "   ✅ $file" -ForegroundColor Green
    } else {
        Write-Host "   ❌ $file 不存在" -ForegroundColor Red
        $allFilesExist = $false
    }
}

if (-not $allFilesExist) {
    Write-Host "`n   ❌ 配置文件不完整，无法启动!" -ForegroundColor Red
    exit 1
}

# 3. 停止旧容器
Write-Host "`n[3/5] 清理旧容器..." -ForegroundColor Yellow
try {
    docker-compose -f docker-compose.monitoring.yml down -q 2>$null
    Write-Host "   ✅ 清理完成" -ForegroundColor Green
} catch {
    Write-Host "   ⚠️  无旧容器需要清理" -ForegroundColor Yellow
}

# 4. 启动监控栈
Write-Host "`n[4/5] 启动 Prometheus 和 Grafana..." -ForegroundColor Yellow
try {
    Set-Location $PSScriptRoot
    $output = docker-compose -f docker-compose.monitoring.yml up -d 2>&1
    Write-Host "   ✅ 容器启动成功" -ForegroundColor Green
    
    # 显示输出
    $output | ForEach-Object {
        if ($_ -match "Started") {
            Write-Host "   $_" -ForegroundColor Green
        }
    }
} catch {
    Write-Host "   ❌ 启动失败：$_" -ForegroundColor Red
    exit 1
}

# 5. 等待并验证
Write-Host "`n[5/5] 验证服务..." -ForegroundColor Yellow
Write-Host "   等待服务启动..." -ForegroundColor Cyan
Start-Sleep -Seconds 15

# 检查容器状态
$containers = docker-compose -f docker-compose.monitoring.yml ps --format "{{.Name}}\t{{.State}}\t{{.Ports}}" 2>$null
Write-Host "`n   容器状态:" -ForegroundColor Yellow
foreach ($container in $containers) {
    $parts = $container -split "\t"
    $name = $parts[0]
    $state = $parts[1]
    
    if ($state -eq "running") {
        Write-Host "   ✅ $name - 运行中" -ForegroundColor Green
    } else {
        Write-Host "   ⚠️  $name - $state" -ForegroundColor Yellow
    }
}

# 测试端口
Write-Host "`n   端口检查:" -ForegroundColor Yellow
$ports = @{
    "Prometheus (9090)" = 9090
    "Grafana (3000)" = 3000
}

foreach ($name, $port in $ports.GetEnumerator()) {
    try {
        $tcpClient = New-Object System.Net.Sockets.TcpClient("localhost", $port)
        $tcpClient.Close()
        Write-Host "   ✅ $name - 可访问" -ForegroundColor Green
    } catch {
        Write-Host "   ❌ $name - 无法访问" -ForegroundColor Red
    }
}

# 显示访问信息
Write-Host "`n" -NoNewline
Write-Host "╔══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║                  🎉 启动成功!                            ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan

Write-Host "`n📊 访问地址:" -ForegroundColor Yellow
Write-Host "   Prometheus: http://localhost:9090" -ForegroundColor Cyan
Write-Host "   Grafana:    http://localhost:3000" -ForegroundColor Cyan
Write-Host ""
Write-Host "   Grafana 登录信息:" -ForegroundColor Yellow
Write-Host "   用户名：admin" -ForegroundColor White
Write-Host "   密码：admin123" -ForegroundColor White

Write-Host "`n📋 下一步操作:" -ForegroundColor Yellow
Write-Host "   1. 访问 Prometheus 验证告警规则" -ForegroundColor White
Write-Host "      导航到：Status → Rules" -ForegroundColor Cyan
Write-Host ""
Write-Host "   2. 在 Grafana 导入仪表盘" -ForegroundColor White
Write-Host "      Dashboards → Import → 上传 yunshu-alerts-monitor.json" -ForegroundColor Cyan
Write-Host ""
Write-Host "   3. 运行测试清单" -ForegroundColor White
Write-Host "      打开：alert_rules_test_checklist.md" -ForegroundColor Cyan

Write-Host "`n💡 提示:" -ForegroundColor Yellow
Write-Host "   - 查看日志：docker-compose -f docker-compose.monitoring.yml logs -f" -ForegroundColor Cyan
Write-Host "   - 停止服务：docker-compose -f docker-compose.monitoring.yml down" -ForegroundColor Cyan
Write-Host "   - 重启服务：docker-compose -f docker-compose.monitoring.yml restart" -ForegroundColor Cyan

Write-Host ""
