#!/bin/bash
#
# Linux 自动启动配置脚本
#
# 将 Prometheus 监控集成到 systemd，确保系统重启后监控自动运行。
#
# 使用方式：
#     bash setup_autostart_linux.sh --install    # 安装 systemd 服务
#     bash setup_autostart_linux.sh --uninstall  # 卸载 systemd 服务
#     bash setup_autostart_linux.sh --status     # 查看服务状态
#

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="Yunshu-prometheus"
SERVICE_FILE="${PROJECT_ROOT}/monitoring/${SERVICE_NAME}.service"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[OK]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

install_service() {
    print_info "Installing systemd service..."
    print_info "Service name: ${SERVICE_NAME}"
    print_info "Service file: ${SERVICE_FILE}"
    
    # 检查服务文件是否存在
    if [ ! -f "${SERVICE_FILE}" ]; then
        print_error "Service file not found: ${SERVICE_FILE}"
        return 1
    fi
    
    # 复制服务文件到 systemd 目录
    print_info "Copying service file to /etc/systemd/system/"
    sudo cp "${SERVICE_FILE}" /etc/systemd/system/
    
    # 更新服务文件中的路径
    print_info "Updating paths in service file..."
    sudo sed -i "s|/opt/Yunshu|${PROJECT_ROOT}|g" /etc/systemd/system/${SERVICE_NAME}.service
    
    # 重新加载 systemd
    print_info "Reloading systemd daemon..."
    sudo systemctl daemon-reload
    
    # 启用服务
    print_info "Enabling service..."
    sudo systemctl enable ${SERVICE_NAME}
    
    # 启动服务
    print_info "Starting service..."
    sudo systemctl start ${SERVICE_NAME}
    
    print_success "Service '${SERVICE_NAME}' installed and started!"
    print_info "The monitoring will start automatically when system boots."
    print_info ""
    print_info "To check service status:"
    echo "  sudo systemctl status ${SERVICE_NAME}"
    print_info ""
    print_info "To stop service:"
    echo "  sudo systemctl stop ${SERVICE_NAME}"
    print_info ""
    print_info "To restart service:"
    echo "  sudo systemctl restart ${SERVICE_NAME}"
    
    return 0
}

uninstall_service() {
    print_info "Uninstalling systemd service..."
    print_info "Service name: ${SERVICE_NAME}"
    
    # 停止服务
    print_info "Stopping service..."
    sudo systemctl stop ${SERVICE_NAME} || true
    
    # 禁用服务
    print_info "Disabling service..."
    sudo systemctl disable ${SERVICE_NAME} || true
    
    # 删除服务文件
    print_info "Removing service file..."
    sudo rm -f /etc/systemd/system/${SERVICE_NAME}.service || true
    
    # 重新加载 systemd
    print_info "Reloading systemd daemon..."
    sudo systemctl daemon-reload
    
    print_success "Service '${SERVICE_NAME}' uninstalled!"
    
    return 0
}

query_service() {
    print_info "Querying systemd service status..."
    print_info "Service name: ${SERVICE_NAME}"
    
    sudo systemctl status ${SERVICE_NAME} || true
    
    return 0
}

run_service() {
    print_info "Starting service..."
    print_info "Service name: ${SERVICE_NAME}"
    
    sudo systemctl start ${SERVICE_NAME}
    
    print_success "Service '${SERVICE_NAME}' started!"
    print_info "Check http://localhost:8000/metrics for metrics"
    
    return 0
}

end_service() {
    print_info "Stopping service..."
    print_info "Service name: ${SERVICE_NAME}"
    
    sudo systemctl stop ${SERVICE_NAME}
    
    print_success "Service '${SERVICE_NAME}' stopped!"
    
    return 0
}

show_help() {
    echo ""
    echo "Linux 自动启动配置脚本"
    echo ""
    echo "将 Prometheus 监控集成到 systemd。"
    echo ""
    echo "使用方式:"
    echo "    bash setup_autostart_linux.sh --install    安装 systemd 服务"
    echo "    bash setup_autostart_linux.sh --uninstall  卸载 systemd 服务"
    echo "    bash setup_autostart_linux.sh --status     查看服务状态"
    echo "    bash setup_autostart_linux.sh --run        启动服务"
    echo "    bash setup_autostart_linux.sh --end        停止服务"
    echo ""
    echo "说明:"
    echo "    --install: 创建一个 systemd 服务，在系统启动时自动运行"
    echo "               Prometheus 监控。"
    echo ""
    echo "    --uninstall: 删除已创建的 systemd 服务。"
    echo ""
    echo "    --status: 查询服务的当前状态。"
    echo ""
    echo "    --run: 手动启动服务。"
    echo ""
    echo "    --end: 手动停止服务。"
    echo ""
    echo "示例:"
    echo "    # 安装自动启动"
    echo "    bash setup_autostart_linux.sh --install"
    echo ""
    echo "    # 查看状态"
    echo "    bash setup_autostart_linux.sh --status"
    echo ""
    echo "    # 卸载"
    echo "    bash setup_autostart_linux.sh --uninstall"
    echo ""
}

main() {
    case "${1:-status}" in
        --install|-i)
            install_service
            ;;
        --uninstall|-u)
            uninstall_service
            ;;
        --status|-s)
            query_service
            ;;
        --run|-r)
            run_service
            ;;
        --end|-e)
            end_service
            ;;
        --help|-h)
            show_help
            ;;
        *)
            print_info "No action specified. Showing current status..."
            query_service
            print_info ""
            print_info "Available actions:"
            echo "  --install    Install systemd service"
            echo "  --uninstall  Uninstall systemd service"
            echo "  --status     Query service status"
            echo "  --run        Start service"
            echo "  --end        Stop service"
            echo "  --help       Show detailed help"
            ;;
    esac
}

echo ""
echo "======================================================================"
echo "[INFO] Yunshu V2 Prometheus Auto-start Configuration (Linux)"
echo "======================================================================"

main "$1"

echo ""
echo "======================================================================"

exit 0