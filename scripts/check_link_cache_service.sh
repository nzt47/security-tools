#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# 云枢 LinkCache 监控服务健康检查（Linux 生产环境）
# ═══════════════════════════════════════════════════════════════════════
# 目的: 定期验证 systemd timer 处于 enabled + active 状态，杜绝
#       「服务器重启后监控静默失效」问题（根因排查见部署手册）。
# 检查项:
#   [1] 单元文件存在（/etc/systemd/system/monitor-link-cache.{service,timer}）
#   [2] timer 已 enable（重启后自启的关键判据）
#   [3] timer 处于 active（已加载定时器）
#   [4] timer 有下一次触发时间（NEXT 非空）
#   [5] 最近一次 service 执行结果正常（非 failed；可配置失败容忍）
# 接入方式（二选一，推荐 cron）:
#   */5 * * * * root /opt/yunshu/scripts/check_link_cache_service.sh \
#       >/var/log/yunshu/health-check.log 2>&1 || echo "health-check failed"
# 退出码: 0 健康 / 1 服务异常（需修复）/ 2 检查脚本自身错误（参数/环境）
# ═══════════════════════════════════════════════════════════════════════
set -uo pipefail

SERVICE="${SERVICE:-monitor-link-cache}"
SYSTEMD_DIR="/etc/systemd/system"
ALLOW_FAILED="${ALLOW_FAILED:-1}"   # 容忍最近 N 次 service 失败（默认 1 次）
[ "$ALLOW_FAILED" -ge 0 ] 2>/dev/null || ALLOW_FAILED=1

UNIT_SERVICE="$SYSTEMD_DIR/$SERVICE.service"
UNIT_TIMER="$SYSTEMD_DIR/$SERVICE.timer"

echo "==> 健康检查: $SERVICE ($(date '+%Y-%m-%d %H:%M:%S'))"
failures=0

# ── [1] 单元文件存在 ──────────────────────────────────────────────────────
for f in "$UNIT_SERVICE" "$UNIT_TIMER"; do
  if [ ! -f "$f" ]; then
    echo "[FAIL] 单元文件缺失: $f"
    failures=$((failures + 1))
  fi
done

# ── [2] timer 是否 enabled（重启自启判据，最常见失效点）───────────────────
ENABLED="$(systemctl is-enabled "$SERVICE.timer" 2>/dev/null || echo unknown)"
if [ "$ENABLED" = "enabled" ]; then
  echo "[OK]   timer enabled"
else
  echo "[FAIL] timer 未启用（is-enabled=$ENABLED）→ 修复: systemctl enable $SERVICE.timer"
  failures=$((failures + 1))
fi

# ── [3] timer 是否 active ─────────────────────────────────────────────────
ACTIVE="$(systemctl is-active "$SERVICE.timer" 2>/dev/null || echo unknown)"
if [ "$ACTIVE" = "active" ]; then
  echo "[OK]   timer active"
else
  echo "[FAIL] timer 非 active（is-active=$ACTIVE）→ 修复: systemctl start $SERVICE.timer"
  failures=$((failures + 1))
fi

# ── [4] 下一次触发时间存在 ────────────────────────────────────────────────
NEXT="$(systemctl list-timers --no-pager --plain "$SERVICE.timer" 2>/dev/null \
  | awk 'NR==1{next} {print $1, $2, $3}')"
if [ -n "$NEXT" ] && [ "$NEXT" != " " ] && ! echo "$NEXT" | grep -qiE "^(n/a|inactive)"; then
  echo "[OK]   下次触发: $NEXT"
else
  echo "[FAIL] 无下一次触发时间（NEXT 为空）→ 检查 timer 是否被 mask/停用"
  failures=$((failures + 1))
fi

# ── [5] 最近一次 service 执行结果（failed 容忍度）─────────────────────────
SVC_ACTIVE="$(systemctl is-active "$SERVICE.service" 2>/dev/null || echo unknown)"
SVC_FAILED="$(systemctl list-units --no-pager --plain --state=failed --type=service \
  | awk -v s="$SERVICE.service" '$1==s {print 1; exit} END {print 0}')"
if [ "$SVC_ACTIVE" = "failed" ] || [ "$SVC_FAILED" = "1" ]; then
  # failed 说明上次采样超阈值（预期行为之一），按容忍次数判定
  if [ "$ALLOW_FAILED" -ge 1 ]; then
    echo "[OK]   service 最近一次为 failed（=内存超阈值告警，已按 ALLOW_FAILED=$ALLOW_FAILED 容忍）"
  else
    echo "[FAIL] service 处于 failed 且 ALLOW_FAILED=0 → 查看: journalctl -u $SERVICE.service"
    failures=$((failures + 1))
  fi
else
  echo "[OK]   service 最近执行正常（is-active=$SVC_ACTIVE）"
fi

# ── 汇总 ──────────────────────────────────────────────────────────────────
if [ "$failures" -eq 0 ]; then
  echo "==> 健康检查通过: $SERVICE 全部正常"
  exit 0
else
  echo "==> 健康检查失败: $failures 项异常（退出码 1）"
  exit 1
fi
