#!/bin/bash
#
# 云枢 V2 监控堆栈快速启动脚本
#

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 打印带颜色的消息
print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查 Docker 是否安装
check_docker() {
    print_info "检查 Docker..."
    if ! command -v docker &> /dev/null; then
        print_error "Docker 未安装。请先安装 Docker。"
        exit 1
    fi
    if ! command -v docker-compose &> /dev/null && ! command -v docker &> /dev/null; then
        print_error "Docker Compose 未安装。请先安装 Docker Compose。"
        exit 1
    fi
    print_success "Docker 已安装"
}

# 启动监控堆栈
start_stack() {
    print_info "启动监控堆栈..."
    cd "$(dirname "$0")/../monitoring"
    docker-compose up -d
    print_success "监控堆栈已启动"
}

# 检查服务状态
check_status() {
    print_info "检查服务状态..."
    echo ""
    echo "┌─────────────────────────────────────────┐"
    echo "│  服务状态                               │"
    echo "├─────────────────────────────────────────┤"
    echo "│  Prometheus:  http://localhost:9090     │"
    echo "│  Grafana:    http://localhost:3000      │"
    echo "│  (admin/admin)                         │"
    echo "└─────────────────────────────────────────┘"
    echo ""
}

# 停止监控堆栈
stop_stack() {
    print_warning "停止监控堆栈..."
    cd "$(dirname "$0")/../monitoring"
    docker-compose down
    print_success "监控堆栈已停止"
}

# 查看日志
view_logs() {
    print_info "查看日志..."
    cd "$(dirname "$0")/../monitoring"
    docker-compose logs -f
}

# 主函数
main() {
    case "${1:-start}" in
        start)
            check_docker
            start_stack
            check_status
            print_info "要启动云枢 V2 指标导出，请运行："
            echo "  python prometheus_example.py"
            echo ""
            ;;
        stop)
            stop_stack
            ;;
        restart)
            stop_stack
            start_stack
            check_status
            ;;
        logs)
            view_logs
            ;;
        status)
            check_status
            ;;
        *)
            echo "用法: $0 {start|stop|restart|logs|status}"
            exit 1
            ;;
    esac
}

# 如果没有参数，显示帮助
if [ $# -eq 0 ]; then
    main start
else
    main "$@"
fi
