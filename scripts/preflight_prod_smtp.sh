#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# 生产环境部署预检：Alertmanager → smtp.139.com:587 连通性 + 防火墙策略
# 对应文档：docs/zh/知识库重构计划/最终部署执行SOP_AlertmanagerSMTP_20260808.md（P0/P1 阶段）
#
# 与 verify 的区别：
#   - 本脚本 = 部署【前】预检，专注主机级 587 出站 + 防火墙策略（只读，不依赖容器）
#   - verify  = 部署【后】验收，额外检查容器状态/配置/邮件发送
#
# 用法（在目标生产服务器上执行）:
#   bash scripts/preflight_prod_smtp.sh
#
# 退出码：0=全部通过（或仅 WARN 提示项）  1=存在 FAIL  2=脚本用法/前置错误
# ═══════════════════════════════════════════════════════════════════════
set -u

SMTP_HOST="${SMTP_HOST:-smtp.139.com}"
SMTP_PORT="${SMTP_PORT:-587}"
CONTAINER="yunshu-prod-alertmanager"

PASS=0; FAIL=0; WARN=0
ok()   { PASS=$((PASS+1)); printf '  [PASS] %s\n' "$*"; }
bad()  { FAIL=$((FAIL+1)); printf '  [FAIL] %s\n' "$*"; }
warn() { WARN=$((WARN+1)); printf '  [WARN] %s\n' "$*"; }
section() { printf '\n── %s ──\n' "$*"; }

# ── 0. 环境信息 ────────────────────────────────────────────────────────
section "0. 环境信息"
echo "  主机: $(hostname)  时间: $(date '+%Y-%m-%d %H:%M:%S %Z')"
if [ -r /etc/os-release ]; then
  echo "  OS: $(. /etc/os-release; echo "$PRETTY_NAME")"
fi

# ── 1. DNS 解析 ────────────────────────────────────────────────────────
section "1. DNS 解析 ${SMTP_HOST}"
if getent hosts "$SMTP_HOST" >/dev/null 2>&1; then
  ips=$(getent hosts "$SMTP_HOST" | awk '{print $1}' | sort -u | tr '\n' ' ')
  ok "解析成功: $ips"
else
  bad "DNS 解析失败（getent hosts ${SMTP_HOST}）"
fi

# ── 2. 主机出站 587 连通性 ─────────────────────────────────────────────
section "2. 主机出站 ${SMTP_HOST}:${SMTP_PORT} 连通性"
conn_ok=0
if command -v nc >/dev/null 2>&1; then
  if nc -vz -w 5 "$SMTP_HOST" "$SMTP_PORT" >/dev/null 2>&1; then ok "TCP 连通 (nc)"; conn_ok=1; else bad "TCP 不通 (nc)"; fi
elif command -v curl >/dev/null 2>&1; then
  if curl -s --connect-timeout 5 -o /dev/null "smtp://${SMTP_HOST}:${SMTP_PORT}" >/dev/null 2>&1; then ok "TCP 连通 (curl)"; conn_ok=1; else bad "TCP 不通 (curl)"; fi
elif command -v timeout >/dev/null 2>&1; then
  if timeout 5 bash -c "exec 3<>/dev/tcp/${SMTP_HOST}/${SMTP_PORT}" 2>/dev/null; then ok "TCP 连通 (/dev/tcp)"; conn_ok=1; else bad "TCP 不通 (/dev/tcp)"; fi
else
  warn "无可用连通性工具（nc/curl/bash），跳过实际探测"
fi
if [ "$conn_ok" -eq 0 ]; then
  echo "  └ 处置: 出站 587 被阻断时检查云安全组出站规则 + 宿主机 firewalld/iptables/nftables（见第 3 节）"
fi

# ── 3. 防火墙策略检查 ──────────────────────────────────────────────────
section "3. 防火墙策略（出站 TCP ${SMTP_PORT}）"
fw_found=0

# 3.1 firewalld
if command -v firewall-cmd >/dev/null 2>&1; then
  fw_found=1
  echo "  检测到 firewalld:"
  if firewall-cmd --state >/dev/null 2>&1; then
    zone=$(firewall-cmd --get-default-zone 2>/dev/null || echo unknown)
    echo "    默认 zone: $zone"
    ports=$(firewall-cmd --list-ports 2>/dev/null | tr '\n' ' ')
    echo "    已放行端口: ${ports:-（无显式端口规则，出站默认允许时无害）}"
  else
    warn "firewalld 服务未运行（若改用其他防火墙请忽略）"
  fi
fi

# 3.2 iptables（OUTPUT 链）
if command -v iptables >/dev/null 2>&1; then
  fw_found=1
  echo "  检测到 iptables:"
  out_rules=$(iptables -L OUTPUT -n 2>/dev/null)
  if echo "$out_rules" | grep -qE 'DROP|REJECT'; then
    if echo "$out_rules" | grep -q "dpt:${SMTP_PORT}"; then
      ok "OUTPUT 链有 DROP/REJECT 但已显式放行 :${SMTP_PORT}"
    else
      bad "OUTPUT 链存在 DROP/REJECT 且未见 :${SMTP_PORT} 放行规则 → 追加: iptables -A OUTPUT -p tcp --dport ${SMTP_PORT} -j ACCEPT"
    fi
  else
    ok "OUTPUT 链无 DROP/REJECT 策略（默认放行出站）"
  fi
fi

# 3.3 ufw
if command -v ufw >/dev/null 2>&1; then
  fw_found=1
  echo "  检测到 ufw:"
  if ufw status 2>/dev/null | grep -q 'Status: active'; then
    if ufw status verbose 2>/dev/null | grep -q "${SMTP_PORT}/tcp"; then
      ok "ufw 已放行 ${SMTP_PORT}/tcp"
    else
      warn "ufw 激活但未显式放行 ${SMTP_PORT}/tcp（出站默认允许时无害；如需显式: ufw allow out ${SMTP_PORT}/tcp）"
    fi
  else
    warn "ufw 未激活（inactive）"
  fi
fi

# 3.4 nftables
if command -v nft >/dev/null 2>&1; then
  fw_found=1
  echo "  检测到 nftables:"
  if nft list ruleset >/dev/null 2>&1; then
    if nft list ruleset 2>/dev/null | grep -qE 'policy (drop|reject)|(drop|reject)' && \
       ! nft list ruleset 2>/dev/null | grep -q "dport ${SMTP_PORT}"; then
      bad "nftables 存在 drop/reject 策略且未见 ${SMTP_PORT} 放行 → 需人工核对 nft list ruleset"
    else
      ok "nftables 无阻断性 drop 策略或已放行 ${SMTP_PORT}"
    fi
  else
    warn "nftables 存在但 ruleset 读取失败（权限/语法）"
  fi
fi

if [ "$fw_found" -eq 0 ]; then
  warn "未检测到常见防火墙工具（firewalld/iptables/ufw/nftables）→ 请人工核对云安全组出站规则"
fi

# ── 4. 容器出网能力（可选，监控栈已部署时） ─────────────────────────────
section "4. 容器内 ${SMTP_HOST}:${SMTP_PORT} 连通性（可选）"
if command -v docker >/dev/null 2>&1 && docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER"; then
  if docker exec "$CONTAINER" sh -c "timeout 5 bash -c 'exec 3<>/dev/tcp/${SMTP_HOST}/${SMTP_PORT}'" >/dev/null 2>&1; then
    ok "容器内 TCP 连通（monitoring-net 出网正常）"
  else
    bad "容器内 TCP 不通（宿主出网已通时多为 monitoring-net 或容器 DNS 问题）"
  fi
else
  warn "容器 $CONTAINER 未运行或 docker 不可用，跳过（部署后再用 verify 检查）"
fi

# ── 5. 云安全组人工确认（脚本无法自动探测） ─────────────────────────────
section "5. 云安全组出站规则（人工确认，脚本无法探测）"
echo "  [提示] 以下项需人工在云控制台确认（无法自动化探测）:"
echo "    ① 安全组出站规则已放行 TCP ${SMTP_PORT}（云厂商: 阿里云/腾讯云/AWS 等）"
echo "    ② 若安全组出站为仅白名单策略，须显式加入 ${SMTP_HOST}"
echo "    ③ 变更审批单/变更窗口已确认"
warn "请人工确认安全组出站规则后再进入部署（对照 SOP P1 A 段）"

# ── 汇总 ───────────────────────────────────────────────────────────────
section "预检结果汇总"
echo "  PASS=$PASS  FAIL=$FAIL  WARN=$WARN"
if [ "$FAIL" -gt 0 ]; then
  echo "  ✗ 存在 FAIL 项：先修复网络/防火墙，再进入 P2 授权码注入" >&2
  exit 1
fi
if [ "$WARN" -gt 0 ]; then
  echo "  ✓ 无硬性 FAIL；存在 WARN 提示项（安全组/未探测项），按 SOP P1 人工确认后放行"
  exit 0
fi
echo "  ✓ 主机级预检全部通过，可进入 P2 授权码注入"
exit 0
