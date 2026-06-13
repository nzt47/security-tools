# Digital Life 生产环境部署脚本 (Windows PowerShell)
# 遵循 PRODUCTION_DEPLOYMENT_CHECKLIST.md 中的检查清单

# 颜色输出
$GREEN = "`e[32m"
$RED = "`e[31m"
$YELLOW = "`e[33m"
$BLUE = "`e[34m"
$NC = "`e[0m"

Write-Host "$BLUE========================================$NC"
Write-Host "$BLUE  Digital Life 生产环境部署脚本$NC"
Write-Host "$BLUE========================================$NC"
Write-Host ""

# ========================================
# 1. 检查环境
# ========================================
Write-Host "$YELLOW[1/8] 检查环境...$NC"

# 检查 Docker
try {
    docker version | Out-Null
    Write-Host "$GREEN✅ Docker 已安装$NC"
} catch {
    Write-Host "$RED❌ Docker 未安装$NC"
    exit 1
}

# 检查 Docker Compose
try {
    docker-compose version | Out-Null
    Write-Host "$GREEN✅ Docker Compose 已安装$NC"
} catch {
    try {
        docker compose version | Out-Null
        Write-Host "$GREEN✅ Docker Compose (v2) 已安装$NC"
    } catch {
        Write-Host "$RED❌ Docker Compose 未安装$NC"
        exit 1
    }
}

# 检查 .env 文件
if (-not (Test-Path .env)) {
    Write-Host "$YELLOW⚠️  .env 文件不存在，从 .env.example 复制$NC"
    Copy-Item .env.example .env
    Write-Host "$YELLOW⚠️  请编辑 .env 文件填入真实配置$NC"
}
Write-Host "$GREEN✅ 配置文件已就绪$NC"

# ========================================
# 2. 创建必要目录
# ========================================
Write-Host "$YELLOW[2/8] 创建必要目录...$NC"
New-Item -ItemType Directory -Force -Path logs, data, .backups | Out-Null
Write-Host "$GREEN✅ 目录已创建$NC"

# ========================================
# 3. 检查现有容器
# ========================================
Write-Host "$YELLOW[3/8] 检查现有容器...$NC"
$containerExists = docker ps -aq -f name=digital-life
if ($containerExists) {
    Write-Host "$YELLOW⚠️  发现现有容器，正在停止...$NC"
    docker-compose down
}
Write-Host "$GREEN✅ 容器状态已检查$NC"

# ========================================
# 4. 构建镜像
# ========================================
Write-Host "$YELLOW[4/8] 构建 Docker 镜像...$NC"
docker-compose build --no-cache
Write-Host "$GREEN✅ 镜像构建完成$NC"

# ========================================
# 5. 启动服务
# ========================================
Write-Host "$YELLOW[5/8] 启动服务...$NC"
docker-compose up -d
Write-Host "$GREEN✅ 服务已启动$NC"

# ========================================
# 6. 等待服务启动
# ========================================
Write-Host "$YELLOW[6/8] 等待服务启动...$NC"
Start-Sleep -Seconds 10

# 检查容器状态
$containerStatus = docker inspect -f '{{.State.Running}}' digital-life 2>$null
if ($containerStatus -ne "true") {
    Write-Host "$RED❌ 容器未正常运行$NC"
    docker-compose logs
    exit 1
}
Write-Host "$GREEN✅ 服务运行正常$NC"

# ========================================
# 7. 显示部署信息
# ========================================
Write-Host "$YELLOW[7/8] 显示部署信息...$NC"
Write-Host ""
Write-Host "$GREEN========================================$NC"
Write-Host "$GREEN  部署成功！$NC"
Write-Host "$GREEN========================================$NC"
Write-Host ""
Write-Host "$BLUE服务状态:$NC"
docker-compose ps
Write-Host ""
Write-Host "$BLUE查看日志:$NC"
Write-Host "  docker-compose logs -f"
Write-Host ""
Write-Host "$BLUE停止服务:$NC"
Write-Host "  docker-compose down"
Write-Host ""
Write-Host "$BLUE重启服务:$NC"
Write-Host "  docker-compose restart"
Write-Host ""
Write-Host "$BLUE错误日志位置:$NC"
Write-Host "  ./logs/digital_life_errors.log"
Write-Host ""
Write-Host "$YELLOW⚠️  请确认错误上报配置是否正确$NC"
Write-Host "$YELLOW   检查清单: PRODUCTION_DEPLOYMENT_CHECKLIST.md$NC"
Write-Host ""
