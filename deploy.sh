#!/bin/bash
# Digital Life 生产环境部署脚本
# 遵循 PRODUCTION_DEPLOYMENT_CHECKLIST.md 中的检查清单

set -e  # 遇到错误立即退出
set -u  # 使用未定义变量报错
set -o pipefail  # 管道命令失败时退出

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Digital Life 生产环境部署脚本${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# ========================================
# 1. 检查环境
# ========================================
echo -e "${YELLOW}[1/8] 检查环境...${NC}"

# 检查 Docker
if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ Docker 未安装${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Docker 已安装${NC}"

# 检查 Docker Compose
if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
    echo -e "${RED}❌ Docker Compose 未安装${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Docker Compose 已安装${NC}"

# 检查 .env 文件
if [ ! -f .env ]; then
    echo -e "${YELLOW}⚠️  .env 文件不存在，从 .env.example 复制${NC}"
    cp .env.example .env
    echo -e "${YELLOW}⚠️  请编辑 .env 文件填入真实配置${NC}"
fi
echo -e "${GREEN}✅ 配置文件已就绪${NC}"

# ========================================
# 2. 创建必要目录
# ========================================
echo -e "${YELLOW}[2/8] 创建必要目录...${NC}"
mkdir -p logs data .backups
echo -e "${GREEN}✅ 目录已创建${NC}"

# ========================================
# 3. 检查目录权限
# ========================================
echo -e "${YELLOW}[3/8] 检查目录权限...${NC}"
chmod 755 logs data .backups
echo -e "${GREEN}✅ 目录权限已设置${NC}"

# ========================================
# 4. 检查现有容器
# ========================================
echo -e "${YELLOW}[4/8] 检查现有容器...${NC}"
if [ "$(docker ps -aq -f name=digital-life)" ]; then
    echo -e "${YELLOW}⚠️  发现现有容器，正在停止...${NC}"
    docker-compose down
fi
echo -e "${GREEN}✅ 容器状态已检查${NC}"

# ========================================
# 5. 构建镜像
# ========================================
echo -e "${YELLOW}[5/8] 构建 Docker 镜像...${NC}"
docker-compose build --no-cache
echo -e "${GREEN}✅ 镜像构建完成${NC}"

# ========================================
# 6. 启动服务
# ========================================
echo -e "${YELLOW}[6/8] 启动服务...${NC}"
docker-compose up -d
echo -e "${GREEN}✅ 服务已启动${NC}"

# ========================================
# 7. 等待服务启动
# ========================================
echo -e "${YELLOW}[7/8] 等待服务启动...${NC}"
sleep 10

# 检查容器状态
if [ "$(docker inspect -f '{{.State.Running}}' digital-life 2>/dev/null || true)" != "true" ]; then
    echo -e "${RED}❌ 容器未正常运行${NC}"
    docker-compose logs
    exit 1
fi
echo -e "${GREEN}✅ 服务运行正常${NC}"

# ========================================
# 8. 显示部署信息
# ========================================
echo -e "${YELLOW}[8/8] 显示部署信息...${NC}"
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  部署成功！${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "${BLUE}服务状态:${NC}"
docker-compose ps
echo ""
echo -e "${BLUE}查看日志:${NC}"
echo "  docker-compose logs -f"
echo ""
echo -e "${BLUE}停止服务:${NC}"
echo "  docker-compose down"
echo ""
echo -e "${BLUE}重启服务:${NC}"
echo "  docker-compose restart"
echo ""
echo -e "${BLUE}错误日志位置:${NC}"
echo "  ./logs/digital_life_errors.log"
echo ""
echo -e "${YELLOW}⚠️  请确认错误上报配置是否正确${NC}"
echo -e "${YELLOW}   检查清单: PRODUCTION_DEPLOYMENT_CHECKLIST.md${NC}"
echo ""
