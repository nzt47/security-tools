#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# 云枢 LinkCache 监控一键部署脚本（Linux 生产服务器）
# ═══════════════════════════════════════════════════════════════════════
# 用法:
#   sudo bash scripts/install_monitor_link_cache.sh /opt/yunshu
#   （第一个参数为部署根目录，缺省 /opt/yunshu）
#
# 打包内容（四件套一键完成）:
#   1. systemd 配置    monitor-link-cache.service + .timer
#   2. 监控脚本         scripts/monitor_link_cache_memory.py
#   3. 依赖独立包       packages/yunshu_cache_tools（监控脚本运行依赖）
#   4. 日志清理         journald drop-in（SystemMaxUse 上限）+ 可选 logrotate
#
# 执行步骤:
#   [1] 前置检查（python3 / 部署目录 / 仓库目录）
#   [2] 部署监控脚本与独立包到部署目录
#   [3] 安装 systemd 单元并按部署根目录修正路径
#   [4] 配置日志清理（journald 容量上限 + logrotate 轮转）
#   [5] 启用定时器 + 验证 + 手动触发一次采样冒烟
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

DEPLOY_ROOT="${1:-/opt/yunshu}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVICE="monitor-link-cache"
SYSTEMD_DIR="/etc/systemd/system"
JOURNAL_DROPIN="/etc/systemd/journald.conf.d/yunshu.conf"

echo "==> 部署根目录: $DEPLOY_ROOT   |   仓库: $REPO_ROOT"
[ "$(id -u)" -eq 0 ] || { echo "[ERROR] 请以 root 运行: sudo bash $0 $DEPLOY_ROOT"; exit 1; }

# ── [1] 前置检查 ──────────────────────────────────────────────────────────
command -v python3 >/dev/null 2>&1 || { echo "[ERROR] 缺少 python3"; exit 1; }
[ -f "$SCRIPT_DIR/monitor_link_cache_memory.py" ] || { echo "[ERROR] 仓库缺少监控脚本"; exit 1; }
[ -d "$REPO_ROOT/packages/yunshu_cache_tools/src/yunshu_cache_tools" ] || { echo "[ERROR] 仓库缺少独立包源码"; exit 1; }
python3 -c "import psutil" >/dev/null 2>&1 \
  || echo "[WARN] psutil 未安装（外部 --pid 模式不可用；进程内估算模式不受影响）"

# ── [2] 部署监控脚本与独立包 ──────────────────────────────────────────────
install -d -m 0755 "$DEPLOY_ROOT/scripts" "$DEPLOY_ROOT/packages/yunshu_cache_tools/src"
install -m 0644 "$SCRIPT_DIR/monitor_link_cache_memory.py" "$DEPLOY_ROOT/scripts/"
cp -r "$REPO_ROOT/packages/yunshu_cache_tools/src/yunshu_cache_tools" "$DEPLOY_ROOT/packages/yunshu_cache_tools/src/"
echo "==> 监控脚本与独立包已部署"

# ── [3] 安装 systemd 单元并修正路径 ───────────────────────────────────────
install -m 0644 "$SCRIPT_DIR/systemd/$SERVICE.service" "$SYSTEMD_DIR/"
install -m 0644 "$SCRIPT_DIR/systemd/$SERVICE.timer"   "$SYSTEMD_DIR/"
sed -i "s|/opt/yunshu|$DEPLOY_ROOT|g" "$SYSTEMD_DIR/$SERVICE.service"
echo "==> systemd 单元已安装，路径修正为: $DEPLOY_ROOT"

# ── [3.5] 安装 exporter 单元（Type=simple + Restart=always 自动拉起）────────
EXPORTER="exporter-link-cache"
if [ -f "$SCRIPT_DIR/systemd/$EXPORTER.service" ]; then
  install -m 0644 "$SCRIPT_DIR/systemd/$EXPORTER.service" "$SYSTEMD_DIR/"
  sed -i "s|/opt/yunshu|$DEPLOY_ROOT|g" "$SYSTEMD_DIR/$EXPORTER.service"
  echo "==> exporter 单元已安装（崩溃 5s 自动拉起，重启自启）"
else
  echo "[WARN] 未找到 $EXPORTER.service，跳过 exporter 部署"
fi

# ── [4] 日志清理：journald 容量上限（默认 100MB，超限自动淘汰旧日志）──────
install -d -m 0755 /etc/systemd/journald.conf.d
cat > "$JOURNAL_DROPIN" <<EOF
[Journal]
SystemMaxUse=100M
MaxRetentionSec=7d
EOF
systemctl restart systemd-journald 2>/dev/null || true
echo "==> journald 日志上限已配置（SystemMaxUse=100M / 保留7天）"

# 可选：logrotate（journal 之外若改输出到文件时启用；此处仅占位说明）
# cat > /etc/logrotate.d/yunshu-monitor <<'EOF'
# /var/log/yunshu/monitor-link-cache.log {
#     daily
#     rotate 7
#     compress
#     missingok
#     notifempty
# }
# EOF

# ── [5] 启用定时器 + 验证 + 冒烟 ──────────────────────────────────────────
systemctl daemon-reload
systemctl enable --now "$SERVICE.timer"
systemctl list-timers --no-pager | grep "$SERVICE" \
  || { echo "[ERROR] 定时器未注册"; exit 1; }

systemctl start "$SERVICE.service" || echo "[WARN] 手动触发失败，请查看 journalctl -u $SERVICE.service"
systemctl status "$SERVICE.service" --no-pager | head -n 12 || true

# ── [6] 启用 exporter（重启自启 + 崩溃自拉起）+ 指标冒烟 ───────────────────
if [ -f "$SYSTEMD_DIR/$EXPORTER.service" ]; then
  systemctl enable --now "$EXPORTER.service"
  sleep 2
  if curl -sf "http://127.0.0.1:9108/metrics" >/dev/null 2>&1; then
    echo "==> exporter 指标冒烟通过: http://127.0.0.1:9108/metrics"
  else
    echo "[WARN] exporter 启动但 /metrics 未就绪，查看 journalctl -u $EXPORTER.service"
  fi
fi

echo ""
echo "✅ 一键部署完成。验证与运维命令:"
echo "   systemctl list-timers | grep $SERVICE          # 定时器状态（NEXT 有值=生效）"
echo "   systemctl is-enabled $SERVICE.timer            # enabled=重启后自启"
echo "   journalctl -u $SERVICE.service -n 20           # 最近一次采样日志"
echo "   journalctl --disk-usage                        # 日志占用（应 <= 100M）"
echo "   sudo systemctl edit $SERVICE.timer             # 调整调度周期"
echo "   systemctl status $EXPORTER                     # exporter 状态（Restart=always 自动拉起）"
echo "   curl -s localhost:9108/metrics | head          # 指标可抓取（Prometheus 数据源）"
