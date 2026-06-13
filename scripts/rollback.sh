#!/bin/bash
# ============================================================
# 云枢服务回滚脚本（Shell 版本）
# 用途：快速恢复到历史备份版本
# 适用环境：Linux / macOS / WSL / Git Bash
# ============================================================

set -e

# ════════════════════════════════════════════════════════════════
#  配置
# ════════════════════════════════════════════════════════════════

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_FILE="$PROJECT_ROOT/logs/rollback_$(date +%Y%m%d_%H%M%S).log"
SERVER_URL="http://127.0.0.1:5678"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# ════════════════════════════════════════════════════════════════
#  函数
# ════════════════════════════════════════════════════════════════

log() {
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo -e "[$timestamp] [$1] $2" | tee -a "$LOG_FILE"
}

log_info() { log "INFO" "$2"; }
log_warn() { log "WARN" "$2"; }
log_error() { log "ERROR" "$2"; }

# 显示帮助
show_help() {
    echo -e "${CYAN}╔══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║       云枢服务回滚脚本 - 快速操作指南                   ║${NC}"
    echo -e "${CYAN}╚══════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo "用法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  -h, --help              显示此帮助信息"
    echo "  -l, --list              列出所有可用备份"
    echo "  -t, --target <TYPE>     指定回滚目标: all|code|data|monitoring (默认: all)"
    echo "  -n, --no-restart        回滚后不自动重启服务"
    echo "  -f, --force             跳过确认，直接执行"
    echo ""
    echo "示例:"
    echo "  $0                      # 交互式回滚到最新版本"
    echo "  $0 -t code              # 仅回滚代码"
    echo "  $0 -t data -n           # 仅回滚数据，不重启"
    echo "  $0 -t monitoring        # 仅回滚监控配置（告警规则）"
    echo "  $0 -l                   # 列出所有备份"
    echo "  $0 -f                   # 跳过确认，直接回滚"
    echo ""
}

# 查找最新备份
find_latest_backup() {
    local pattern="$1"
    local result=$(find "$PROJECT_ROOT" -name "${pattern}*.bak_*" -type f 2>/dev/null | sort -t_ -k$(echo "$pattern" | tr -cd '_' | wc -c | xargs -I{} expr {} + 2) -r | head -1)
    echo "$result"
}

# 列出备份
list_backups() {
    echo -e "\n${CYAN}══════════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}📋 可用备份列表${NC}"
    echo -e "${CYAN}══════════════════════════════════════════════════════════${NC}"
    
    local has_backups=false
    
    # 代码备份
    local code_backups=$(find "$PROJECT_ROOT" -maxdepth 1 -name "app_server.py.bak_*" -type f 2>/dev/null | sort -r)
    if [ -n "$code_backups" ]; then
        has_backups=true
        echo -e "\n${YELLOW}📁 应用服务器代码:${NC}"
        echo "$code_backups" | while read -r f; do
            local size=$(du -h "$f" | cut -f1)
            local date=$(stat -f "%Sm" "$f" 2>/dev/null || stat -c "%y" "$f" 2>/dev/null | cut -d' ' -f1-2)
            echo -e "   ${GREEN}[$date]${NC} $(basename "$f") ($size)"
        done
    fi
    
    # 数据备份
    local data_backups=$(find "$PROJECT_ROOT/data" -name "messages.jsonl.bak_*" -type f 2>/dev/null | sort -r)
    if [ -n "$data_backups" ]; then
        has_backups=true
        echo -e "\n${YELLOW}📁 历史记忆数据:${NC}"
        echo "$data_backups" | while read -r f; do
            local size=$(du -h "$f" | cut -f1)
            local date=$(stat -f "%Sm" "$f" 2>/dev/null || stat -c "%y" "$f" 2>/dev/null | cut -d' ' -f1-2)
            echo -e "   ${GREEN}[$date]${NC} $(basename "$f") ($size)"
        done
    fi
    
    # 监控配置备份
    local monitoring_backups=$(find "$PROJECT_ROOT/monitoring" -name "*.yml.bak_*" -type f 2>/dev/null | sort -r)
    if [ -n "$monitoring_backups" ]; then
        has_backups=true
        echo -e "\n${YELLOW}📁 监控配置（告警规则）:${NC}"
        echo "$monitoring_backups" | while read -r f; do
            local size=$(du -h "$f" | cut -f1)
            local date=$(stat -f "%Sm" "$f" 2>/dev/null || stat -c "%y" "$f" 2>/dev/null | cut -d' ' -f1-2)
            echo -e "   ${GREEN}[$date]${NC} $(basename "$f") ($size)"
        done
    fi
    
    # 工具类文件备份
    local utils_backups=$(find "$PROJECT_ROOT/utils" -name "*.py.bak_*" -type f 2>/dev/null | sort -r)
    if [ -n "$utils_backups" ]; then
        has_backups=true
        echo -e "\n${YELLOW}📁 SafeFileReader 工具类:${NC}"
        echo "$utils_backups" | while read -r f; do
            local size=$(du -h "$f" | cut -f1)
            local date=$(stat -f "%Sm" "$f" 2>/dev/null || stat -c "%y" "$f" 2>/dev/null | cut -d' ' -f1-2)
            echo -e "   ${GREEN}[$date]${NC} $(basename "$f") ($size)"
        done
    fi
    
    if [ "$has_backups" = false ]; then
        echo -e "\n${RED}⚠️ 未找到任何备份文件${NC}"
        echo "   备份文件命名格式: *.bak_YYYYMMDD 或 *.bak_YYYYMMDD_HHmmss"
    fi
    
    echo ""
}

# 停止服务
stop_service() {
    log_info "正在停止云枢服务..."
    
    # 尝试多种方式停止
    if command -v pkill &> /dev/null; then
        pkill -f "python.*app_server.py" 2>/dev/null || true
    elif command -v killall &> /dev/null; then
        killall -f python 2>/dev/null || true
    else
        # 使用进程查找
        local pid=$(ps aux | grep "[p]ython.*app_server" | awk '{print $2}' | head -1)
        if [ -n "$pid" ]; then
            kill "$pid" 2>/dev/null || true
            log_info "已发送停止信号到 PID: $pid"
        fi
    fi
    
    sleep 2
    log_info "✅ 服务已停止"
}

# 启动服务
start_service() {
    log_info "正在启动云枢服务..."
    
    cd "$PROJECT_ROOT"
    export YUNSHU_FEATURE_SANDBOX='false'
    nohup python app_server.py > logs/app_server.log 2>&1 &
    
    local pid=$!
    log_info "✅ 服务已启动 (PID: $pid)"
    
    # 等待服务就绪
    log_info "等待服务就绪..."
    for i in {1..10}; do
        if curl -s "$SERVER_URL/api/health" > /dev/null 2>&1; then
            log_info "✅ 服务验证通过"
            return 0
        fi
        sleep 1
    done
    
    log_warn "⚠️ 服务验证超时，请手动检查日志: logs/app_server.log"
}

# 执行回滚
do_rollback() {
    local backup_path="$1"
    local target_path="$2"
    local description="$3"
    
    if [ ! -f "$backup_path" ]; then
        log_error "❌ 备份文件不存在: $backup_path"
        return 1
    fi
    
    # 回滚前备份当前版本
    if [ -f "$target_path" ]; then
        local pre_rollback="${target_path}.pre_rollback_$(date +%Y%m%d_%H%M%S)"
        cp "$target_path" "$pre_rollback"
        log_info "📦 回滚前已备份当前版本: $pre_rollback"
    fi
    
    cp "$backup_path" "$target_path"
    log_info "✅ $description 已回滚: $(basename "$backup_path")"
    return 0
}

# ════════════════════════════════════════════════════════════════
#  主流程
# ════════════════════════════════════════════════════════════════

# 解析参数
TARGET="all"
RESTART=true
FORCE=false
LIST=false

while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            show_help
            exit 0
            ;;
        -l|--list)
            LIST=true
            shift
            ;;
        -t|--target)
            TARGET="$2"
            shift 2
            ;;
        -n|--no-restart)
            RESTART=false
            shift
            ;;
        -f|--force)
            FORCE=true
            shift
            ;;
        *)
            echo "未知参数: $1"
            show_help
            exit 1
            ;;
    esac
done

# 确保日志目录存在
mkdir -p "$PROJECT_ROOT/logs"

echo ""
echo -e "${CYAN}══════════════════════════════════════════════════════════${NC}"
echo -e "${CYAN}🔄 云枢服务回滚工具 (Shell)${NC}"
echo -e "${CYAN}══════════════════════════════════════════════════════════${NC}"
log_info "回滚脚本启动，目标: $TARGET"

if [ "$LIST" = true ]; then
    list_backups
    exit 0
fi

# 确定回滚目标
declare -A TARGETS
if [ "$TARGET" = "all" ] || [ "$TARGET" = "code" ]; then
    TARGETS["code"]=1
fi
if [ "$TARGET" = "all" ] || [ "$TARGET" = "data" ]; then
    TARGETS["data"]=1
fi
if [ "$TARGET" = "all" ] || [ "$TARGET" = "monitoring" ]; then
    TARGETS["monitoring"]=1
fi

# 查找备份
log_info "🔍 查找最新备份..."

CODE_BACKUP=$(find_latest_backup "app_server.py")
DATA_BACKUP=$(find "$PROJECT_ROOT/data" -name "messages.jsonl.bak_*" -type f 2>/dev/null | sort -r | head -1)

if [ -n "$CODE_BACKUP" ] && [ "${TARGETS["code"]+isset}" ]; then
    log_info "✅ 找到代码备份: $(basename "$CODE_BACKUP")"
fi

if [ -n "$DATA_BACKUP" ] && [ "${TARGETS["data"]+isset}" ]; then
    log_info "✅ 找到数据备份: $(basename "$DATA_BACKUP")"
fi

# 显示回滚计划
echo ""
echo -e "${YELLOW}══════════════════════════════════════════════════════════${NC}"
echo -e "${YELLOW}⚠️ 回滚计划:${NC}"
echo -e "${YELLOW}══════════════════════════════════════════════════════════${NC}"

if [ -n "$CODE_BACKUP" ] && [ "${TARGETS["code"]+isset}" ]; then
    echo -e "  ${GREEN}应用服务器代码${NC} ← $(basename "$CODE_BACKUP")"
fi

if [ -n "$DATA_BACKUP" ] && [ "${TARGETS["data"]+isset}" ]; then
    echo -e "  ${GREEN}历史记忆数据${NC} ← $(basename "$DATA_BACKUP")"
fi

# SafeFileReader 相关回滚项
if [ "${TARGETS["monitoring"]+isset}" ]; then
    ALERTS_BACKUP=$(find "$PROJECT_ROOT/monitoring" -name "alerts.yml.bak_*" -type f 2>/dev/null | sort -r | head -1)
    if [ -n "$ALERTS_BACKUP" ]; then
        echo -e "  ${GREEN}监控配置（告警规则）${NC} ← $(basename "$ALERTS_BACKUP")"
    fi
    
    FILE_READER_BACKUP=$(find "$PROJECT_ROOT/utils" -name "file_reader.py.bak_*" -type f 2>/dev/null | sort -r | head -1)
    if [ -n "$FILE_READER_BACKUP" ]; then
        echo -e "  ${GREEN}SafeFileReader 工具类${NC} ← $(basename "$FILE_READER_BACKUP")"
    fi
    
    PROMETHEUS_BACKUP=$(find "$PROJECT_ROOT/utils" -name "prometheus_exporter.py.bak_*" -type f 2>/dev/null | sort -r | head -1)
    if [ -n "$PROMETHEUS_BACKUP" ]; then
        echo -e "  ${GREEN}Prometheus 指标配置${NC} ← $(basename "$PROMETHEUS_BACKUP")"
    fi
fi

echo ""

# 确认执行
if [ "$FORCE" = false ]; then
    read -p "确认执行回滚？(y/N): " confirm
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        log_info "回滚已取消"
        exit 0
    fi
fi

# 停止服务
echo ""
stop_service

# 执行回滚
echo ""
log_info "══════════════════════════════════════════════"
log_info "执行回滚..."
log_info "══════════════════════════════════════════════"

if [ -n "$CODE_BACKUP" ] && [ "${TARGETS["code"]+isset}" ]; then
    do_rollback "$CODE_BACKUP" "$PROJECT_ROOT/app_server.py" "应用服务器代码"
fi

if [ -n "$DATA_BACKUP" ] && [ "${TARGETS["data"]+isset}" ]; then
    do_rollback "$DATA_BACKUP" "$PROJECT_ROOT/data/messages.jsonl" "历史记忆数据"
fi

# SafeFileReader 相关回滚
if [ "${TARGETS["monitoring"]+isset}" ]; then
    ALERTS_BACKUP=$(find "$PROJECT_ROOT/monitoring" -name "alerts.yml.bak_*" -type f 2>/dev/null | sort -r | head -1)
    if [ -n "$ALERTS_BACKUP" ]; then
        do_rollback "$ALERTS_BACKUP" "$PROJECT_ROOT/monitoring/alerts.yml" "监控配置（告警规则）"
    fi
    
    FILE_READER_BACKUP=$(find "$PROJECT_ROOT/utils" -name "file_reader.py.bak_*" -type f 2>/dev/null | sort -r | head -1)
    if [ -n "$FILE_READER_BACKUP" ]; then
        do_rollback "$FILE_READER_BACKUP" "$PROJECT_ROOT/utils/file_reader.py" "SafeFileReader 工具类"
    fi
    
    PROMETHEUS_BACKUP=$(find "$PROJECT_ROOT/utils" -name "prometheus_exporter.py.bak_*" -type f 2>/dev/null | sort -r | head -1)
    if [ -n "$PROMETHEUS_BACKUP" ]; then
        do_rollback "$PROMETHEUS_BACKUP" "$PROJECT_ROOT/utils/prometheus_exporter.py" "Prometheus 指标配置"
    fi
fi

# 重启服务
echo ""
if [ "$RESTART" = true ]; then
    start_service
else
    log_info "⏭️ 跳过服务重启（-n / --no-restart）"
    log_info "手动启动命令: cd $PROJECT_ROOT && YUNSHU_FEATURE_SANDBOX=false python app_server.py"
fi

echo ""
echo -e "${CYAN}══════════════════════════════════════════════════════════${NC}"
log_info "🎉 回滚完成！"
log_info "回滚日志: $LOG_FILE"
echo -e "${CYAN}══════════════════════════════════════════════════════════${NC}"
