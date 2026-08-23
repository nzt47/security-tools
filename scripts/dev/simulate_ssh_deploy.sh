#!/usr/bin/env bash
# 模拟 CI/CD deploy 步骤（对应 .github/workflows/deploy.yml 的 deploy job）
#
# 两种模式：
#   A) 本地模拟（默认）：用 deploy/t8-gateway compose 演练 构建→拉取→启动→就绪检查
#   B) 真实 SSH 验证：连接测试 + 远程 docker compose pull / up -d / ps
#
# 用法：
#   ./simulate_ssh_deploy.sh                                # A：本地模拟部署流程
#   ./simulate_ssh_deploy.sh --dry-run                      # A：仅打印将执行的命令
#   ./simulate_ssh_deploy.sh --compose-dir deploy/t8-gateway
#   ./simulate_ssh_deploy.sh --host <ip> --user <user> --key <id_rsa> --path /opt/yunshu   # B
#   ./simulate_ssh_deploy.sh --host ... --user ... --key ... --path ... --dry-run          # B
#
# 退出码：0 = 成功；1 = 任一环节失败
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_DIR="${COMPOSE_DIR:-$ROOT/deploy/t8-gateway}"
DRY_RUN=0
# 模式 B 参数
SSH_HOST=""
SSH_USER=""
SSH_KEY=""
SSH_PATH=""
SSH_PORT=22
CONNECT_TIMEOUT=10
SSH_VERBOSE=0

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] $*"; }

# ssh -v 调试输出过滤：仅保留连接超时 / 认证失败 / Host key / 成功等诊断关键行
SSH_FILTER="Connect timed out|Connection timed out|Network is unreachable|Host is unreachable|Permission denied|Authentications that can continue|No more authentication methods|denied for|Connection closed by|Connection reset|closed by remote host|Host key verification failed|Authenticated to|Offering public key|identity file|debug1: Connecting to"
ssh_run_filtered() {
  # 执行 ssh 并过滤 -v 输出；返回 ssh 退出码（不受管道影响）
  local out
  out=$("$@" 2>&1)
  local code=$?
  echo "$out" | grep -E "$SSH_FILTER" | sed 's/^/    [ssh] /' || true
  return "$code"
}

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "[dry-run] $*"
  else
    log "[run] $*"
    "$@"
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --compose-dir) COMPOSE_DIR="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --host) SSH_HOST="$2"; shift 2 ;;
    --user) SSH_USER="$2"; shift 2 ;;
    --key) SSH_KEY="$2"; shift 2 ;;
    --path) SSH_PATH="$2"; shift 2 ;;
    --ssh-port) SSH_PORT="$2"; shift 2 ;;
    --connect-timeout) CONNECT_TIMEOUT="$2"; shift 2 ;;
    --verbose) SSH_VERBOSE=1; shift ;;
    -h|--help)
      sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

echo "== [deploy] 模拟 CI/CD deploy 步骤 =="
if [[ -n "$SSH_HOST" ]]; then
  # ── 模式 B：真实 SSH 验证（连接 + 拉取 + 启动） ──
  [[ -n "$SSH_USER" ]] || { log "[FAIL] 模式 B 需 --user"; exit 2; }
  [[ -n "$SSH_KEY" ]] || { log "[FAIL] 模式 B 需 --key（SSH 私钥路径）"; exit 2; }
  [[ -n "$SSH_PATH" ]] || SSH_PATH="/opt/yunshu"
  SSH_ARGS=(-p "$SSH_PORT" -o "ConnectTimeout=$CONNECT_TIMEOUT" -o BatchMode=yes \
            -o StrictHostKeyChecking=accept-new -i "$SSH_KEY")
  [[ "$SSH_VERBOSE" -eq 1 ]] && SSH_ARGS+=(-v)
  DEST="${SSH_USER}@${SSH_HOST}"

  log "-- 1/3 连接测试：${DEST}:${SSH_PORT}（超时 ${CONNECT_TIMEOUT}s，ssh -v 详细模式）"
  log "    目标: ${SSH_HOST}:${SSH_PORT}  用户: ${SSH_USER}  密钥: ${SSH_KEY}  部署路径: ${SSH_PATH}"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "[dry-run] ssh ${SSH_ARGS[*]} -v ${DEST} 'echo connected'"
  else
    # 连接测试始终加 -v：认证/连接细节经过滤后输出（仅超时/认证失败/成功关键行）
    if ssh_run_filtered ssh "${SSH_ARGS[@]}" -v "$DEST" "echo connected"; then
      log "[OK] SSH 连接正常"
    else
      code=$?
      log "[FAIL] SSH 连接失败（exit=${code}，耗时 ${CONNECT_TIMEOUT}s 内未建立）"
      log "排查步骤："
      log "  1) 网络可达性: ping -n 3 ${SSH_HOST}  /  telnet ${SSH_HOST} ${SSH_PORT}"
      log "  2) 端口确认: 自定义端口用 --ssh-port ${SSH_PORT}（当前）"
      log "  3) 密钥认证: ssh -v -p ${SSH_PORT} -i ${SSH_KEY} ${DEST} 观察认证日志；私钥权限需 600（chmod 600 ${SSH_KEY}）"
      log "  4) 用户名/主机拼写: ${DEST}；服务端是否允许密钥登录（BatchMode 禁交互）"
      exit 1
    fi
  fi
  log "-- 2/3 远程拉取并启动（${SSH_PATH}）"
  if [[ "$SSH_VERBOSE" -eq 1 ]]; then
    ssh_run_filtered ssh "${SSH_ARGS[@]}" "$DEST" \
      "cd ${SSH_PATH} && docker compose pull && docker compose up -d"
  else
    run ssh "${SSH_ARGS[@]}" "$DEST" \
      "cd ${SSH_PATH} && docker compose pull && docker compose up -d"
  fi
  log "-- 3/3 容器状态"
  run ssh "${SSH_ARGS[@]}" "$DEST" \
    "cd ${SSH_PATH} && docker compose ps"
else
  # ── 模式 A：本地模拟（compose 演练部署流程） ──
  [[ -d "$COMPOSE_DIR" ]] || { log "[FAIL] compose 目录不存在: $COMPOSE_DIR"; exit 2; }
  log "-- 1/3 构建/拉取镜像（${COMPOSE_DIR}）"
  ( cd "$COMPOSE_DIR" && run docker compose build )
  log "-- 2/3 启动服务"
  ( cd "$COMPOSE_DIR" && run docker compose up -d )
  log "-- 3/3 容器状态"
  ( cd "$COMPOSE_DIR" && run docker compose ps )
fi

echo ""
log "[OK] deploy 模拟完成"

