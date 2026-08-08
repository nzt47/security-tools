#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# 生产服务器 Alertmanager → 139 邮箱 SMTP 链路：一键部署验收
# 对应文档：docs/zh/知识库重构计划/生产部署检查清单_AlertmanagerSMTP_20260808.md
#
# 用法（在目标生产服务器上执行）:
#   bash scripts/verify_prod_alertmanager.sh              # 只读检查（连通性/防火墙/配置/日志）
#   bash scripts/verify_prod_alertmanager.sh --send-test  # 额外触发一条测试告警并验证邮件发送
#
# 退出码：0=全部通过  1=存在 FAIL 项  2=脚本用法/前置错误
# ⚠️ 授权码请先填入 alertmanager.yml（scripts/apply_smtp_auth_code.py 可自动替换）
# ═══════════════════════════════════════════════════════════════════════
set -u

SMTP_HOST="${SMTP_HOST:-smtp.139.com}"
SMTP_PORT="${SMTP_PORT:-587}"
CONTAINER="yunshu-prod-alertmanager"
AM_URL="${AM_URL:-http://127.0.0.1:9093}"
CONFIG_FILE="deploy/monitoring/prometheus/alertmanager.yml"
PLACEHOLDER="REPLACE_WITH_SMTP_AUTH_CODE"

PASS=0; FAIL=0; WARN=0

say()   { printf '%s\n' "$*"; }
ok()    { PASS=$((PASS+1)); printf '  [PASS] %s\n' "$*"; }
bad()   { FAIL=$((FAIL+1)); printf '  [FAIL] %s\n' "$*"; }
warn()  { WARN=$((WARN+1)); printf '  [WARN] %s\n' "$*"; }
section() { printf '\n── %s ──\n' "$*"; }

# ── 前置检查 ──────────────────────────────────────────────────────────
if [ ! -f "$CONFIG_FILE" ]; then
  echo "[ERROR] 未找到配置文件 $CONFIG_FILE（请从仓库根目录执行）" >&2
  exit 2
fi

section "0. 环境信息"
echo "  主机: $(hostname)  时间: $(date '+%Y-%m-%d %H:%M:%S %Z')"
command -v docker >/dev/null 2>&1 && echo "  docker: $(docker --version 2>/dev/null)" || warn "未检测到 docker 命令"

# ── 1. 主机出站 587 连通性 ─────────────────────────────────────────────
section "1. 主机出站 ${SMTP_HOST}:${SMTP_PORT} 连通性"
if command -v nc >/dev/null 2>&1; then
  if nc -vz -w 5 "$SMTP_HOST" "$SMTP_PORT" >/dev/null 2>&1; then ok "TCP 连通 (nc)"; else bad "TCP 不通 (nc)"; fi
elif command -v curl >/dev/null 2>&1; then
  if curl -s --connect-timeout 5 -o /dev/null "smtp://${SMTP_HOST}:${SMTP_PORT}"; then ok "TCP 连通 (curl)"; else bad "TCP 不通 (curl)"; fi
elif command -v bash >/dev/null 2>&1; then
  if timeout 5 bash -c "exec 3<>/dev/tcp/${SMTP_HOST}/${SMTP_PORT}" 2>/dev/null; then ok "TCP 连通 (/dev/tcp)"; else bad "TCP 不通 (/dev/tcp)"; fi
else
  warn "无可用连通性工具（nc/curl/bash）"
fi

# ── 2. DNS 解析 ────────────────────────────────────────────────────────
section "2. DNS 解析 ${SMTP_HOST}"
if getent hosts "$SMTP_HOST" >/dev/null 2>&1; then
  ips=$(getent hosts "$SMTP_HOST" | awk '{print $1}' | tr '\n' ' ')
  ok "解析成功: $ips"
else
  bad "DNS 解析失败"
fi

# ── 3. 防火墙 / 安全组检查（出站 587） ─────────────────────────────────
section "3. 防火墙检查（出站 TCP ${SMTP_PORT}）"
fw_found=0
if command -v firewall-cmd >/dev/null 2>&1; then
  fw_found=1
  echo "  检测到 firewalld："
  firewall-cmd --list-all 2>/dev/null | grep -iE 'port|out' || echo "    (未发现端口规则，通常默认允许出站)"
fi
if command -v iptables >/dev/null 2>&1; then
  fw_found=1
  echo "  检测到 iptables OUTPUT 链："
  if iptables -L OUTPUT -n 2>/dev/null | grep -qE 'DROP|REJECT'; then
    if iptables -L OUTPUT -n 2>/dev/null | grep -q ":${SMTP_PORT}"; then ok "OUTPUT 链存在 DROP/REJECT 但已放行 :${SMTP_PORT}"
    else bad "OUTPUT 链存在 DROP/REJECT 且未放行 :${SMTP_PORT}"
    fi
  else
    ok "OUTPUT 链无 DROP/REJECT 策略"
  fi
fi
if command -v ufw >/dev/null 2>&1; then
  fw_found=1
  echo "  检测到 ufw："
  ufw status verbose 2>/dev/null | grep -q "${SMTP_PORT}/tcp" && ok "ufw 已放行 ${SMTP_PORT}/tcp" || warn "ufw 未显式列出 ${SMTP_PORT}/tcp（出站默认允许时无害）"
fi
if [ "$fw_found" -eq 0 ]; then
  warn "未检测到常见防火墙工具（firewalld/iptables/ufw）；请自行核对云安全组出站规则"
fi

# ── 4. Alertmanager 容器状态 ───────────────────────────────────────────
section "4. Alertmanager 容器状态"
if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER"; then
  state=$(docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null)
  health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$CONTAINER" 2>/dev/null)
  ok "容器运行中 (status=$state, health=$health)"
  if [ "$health" != "healthy" ] && [ "$health" != "none" ]; then
    bad "容器健康检查非 healthy"
  fi
else
  bad "容器 $CONTAINER 未运行"
fi

# ── 5. 容器内 SMTP 连通性（经 monitoring-net 出网） ────────────────────
section "5. 容器内 ${SMTP_HOST}:${SMTP_PORT} 连通性"
if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER"; then
  if docker exec "$CONTAINER" sh -c "timeout 5 bash -c 'exec 3<>/dev/tcp/${SMTP_HOST}/${SMTP_PORT}'" >/dev/null 2>&1; then
    ok "容器内 TCP 连通"
  else
    bad "容器内 TCP 不通（宿主出网或 monitoring-net 问题）"
  fi
else
  warn "容器未运行，跳过"
fi

# ── 6. 配置校验（amtool + 占位符 + 端口） ─────────────────────────────
section "6. 配置校验"
if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER"; then
  if docker exec "$CONTAINER" amtool check-config /etc/alertmanager/alertmanager.yml >/dev/null 2>&1; then
    ok "amtool check-config 通过"
  else
    bad "amtool check-config 失败（配置语法错误）"
  fi
else
  warn "容器未运行，跳过 amtool 校验"
fi
if grep -q "smtp_smarthost:.*${SMTP_PORT}" "$CONFIG_FILE" 2>/dev/null; then
  ok "smarthost 端口为 ${SMTP_PORT}"
else
  bad "smarthost 端口不是 ${SMTP_PORT}（期望 smtp.139.com:${SMTP_PORT}）"
fi
if grep -q "smtp_require_tls: true" "$CONFIG_FILE" 2>/dev/null; then
  ok "smtp_require_tls: true"
else
  bad "缺少 smtp_require_tls: true"
fi
if grep -q "$PLACEHOLDER" "$CONFIG_FILE" 2>/dev/null; then
  bad "smtp_auth_password 仍为占位符 ${PLACEHOLDER}（请先填入真实授权码）"
elif grep -q "smtp_auth_password:" "$CONFIG_FILE" 2>/dev/null; then
  ok "smtp_auth_password 已配置（非占位符）"
else
  warn "未找到 smtp_auth_password 行"
fi

# ── 7. 最近邮件通知日志 ────────────────────────────────────────────────
section "7. 最近邮件通知日志（1h）"
if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER"; then
  completed=$(docker logs "$CONTAINER" --since 1h 2>&1 | grep -c 'Notify for alerts completed' || true)
  failed=$(docker logs "$CONTAINER" --since 1h 2>&1 | grep -c 'Notify attempt failed' || true)
  echo "  1h 内 notify completed=$completed, failed=$failed"
  if [ "$failed" -gt 0 ]; then
    docker logs "$CONTAINER" --since 1h 2>&1 | grep 'Notify attempt failed' | tail -3 | sed 's/^/    | /'
    bad "存在发送失败记录（请按错误关键字定位：535=授权码错 / 454=TLS 错 / dial tcp=网络不通）"
  elif [ "$completed" -gt 0 ]; then
    ok "最近 1h 内有成功发送记录"
  else
    warn "最近 1h 无发送记录（可能尚无告警触发，属正常）"
  fi
else
  warn "容器未运行，跳过日志检查"
fi

# ── 8. 邮件发送测试（可选） ─────────────────────────────────────────────
if [ "${1:-}" = "--send-test" ]; then
  section "8. 邮件发送测试（注入测试告警）"
  if grep -q "$PLACEHOLDER" "$CONFIG_FILE" 2>/dev/null; then
    bad "授权码仍为占位符，无法发送测试邮件（请先填入真实授权码）"
  else
    uid=$(date +%s)
    payload=$(cat <<EOF
[{"labels":{"alertname":"ProdSmtpVerify","instance":"verify-${uid}","team":"knowledge"},
  "annotations":{"summary":"生产 SMTP 链路一键验收（非真实故障）"},
  "startsAt":"$(date -u '+%Y-%m-%dT%H:%M:%S.000Z')"}]
EOF
)
    code=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' \
        -d "$payload" "${AM_URL}/api/v2/alerts" --connect-timeout 5)
    if [ "$code" = "200" ]; then
      echo "  测试告警已注入 (HTTP 200)，等待 group_wait(30s)+发送..."
      sleep 40
      if docker logs "$CONTAINER" --since 2m 2>&1 | grep -q 'Notify for alerts completed'; then
        ok "邮件发送成功（Notify for alerts completed）→ 请查收 13539371839@139.com（含垃圾箱）"
      elif docker logs "$CONTAINER" --since 2m 2>&1 | grep -q 'Notify attempt failed'; then
        bad "邮件发送失败（Notify attempt failed）→ 详见上方错误关键字定位"
      else
        warn "2m 内未见发送日志（可能仍在 group_wait 或告警已去重，可稍后复查）"
      fi
    else
      bad "测试告警注入失败 (HTTP $code)"
    fi
  fi
else
  section "8. 邮件发送测试"
  echo "  已跳过（加 --send-test 可触发真实测试邮件）"
fi

# ── 汇总 ───────────────────────────────────────────────────────────────
section "验收结果汇总"
echo "  PASS=$PASS  FAIL=$FAIL  WARN=$WARN"
if [ "$FAIL" -gt 0 ]; then
  echo "  ✗ 存在未通过项，请按上方 [FAIL] 提示处理后再验" >&2
  exit 1
fi
echo "  ✓ 关键链路全部通过"
exit 0
