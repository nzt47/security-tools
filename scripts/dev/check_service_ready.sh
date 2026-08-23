#!/usr/bin/env bash
# 本地服务启动检查：轮询端口 5678 健康 + 校验启动日志输出（部署手册验证清单）
#
# 用法：
#   ./check_service_ready.sh                      # 裸机：轮询 /api/health + 查 logs/
#   ./check_service_ready.sh --container yunshu   # 容器：轮询 /api/health + docker logs
#   ./check_service_ready.sh --port 5679          # 自定义端口
#   ./check_service_ready.sh --timeout 90         # 轮询超时（秒）
#
# 退出码：0 = 就绪；1 = 超时/失败
set -euo pipefail

PORT=5678
CONTAINER=""
TIMEOUT=60
HEALTH_PATH="/api/health"
LOG_DIR="${LOG_DIR:-logs}"

usage() {
  sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --container) CONTAINER="$2"; shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "未知参数: $1" >&2; usage ;;
  esac
done

BASE_URL="http://127.0.0.1:${PORT}"
echo "== 服务启动检查 =="
echo "  健康端点: ${BASE_URL}${HEALTH_PATH}"
echo "  轮询超时: ${TIMEOUT}s"
[[ -n "$CONTAINER" ]] && echo "  容器日志: ${CONTAINER}"

# 健康探测：优先 curl；失败时回退 python urllib（沙盒/Git Bash 下 curl 与宿主环回网络可能隔离）
detect_python() {
  for c in python3 python py; do
    command -v "$c" >/dev/null 2>&1 && { echo "$c"; return 0; }
  done
  echo "python"
}
PY_CMD="$(detect_python)"
check_health() {
  local url="$1"
  if curl -fsS --max-time 3 "$url" >/dev/null 2>&1; then
    return 0
  fi
  "$PY_CMD" -c "import urllib.request; urllib.request.urlopen('$url', timeout=3)" >/dev/null 2>&1
}

# 1) 端口/健康轮询
deadline=$(( $(date +%s) + TIMEOUT ))
ready=0
while (( $(date +%s) < deadline )); do
  if check_health "${BASE_URL}${HEALTH_PATH}"; then
    ready=1
    break
  fi
  sleep 2
done
if [[ "$ready" -eq 1 ]]; then
  echo "  [OK] ${HEALTH_PATH} 可达（端口 ${PORT} 正常）"
else
  echo "  [FAIL] ${TIMEOUT}s 内 ${BASE_URL}${HEALTH_PATH} 不可达（服务未启动/端口错误）"
  exit 1
fi

# 2) 启动日志校验
STARTUP_MARK="云枢"
log_ok=0
if [[ -n "$CONTAINER" ]]; then
  if docker logs --tail 200 "$CONTAINER" 2>&1 | grep -q "$STARTUP_MARK"; then
    log_ok=1
    echo "  [OK] 容器日志含启动标志（${STARTUP_MARK}）"
  else
    echo "  [WARN] 容器日志未找到启动标志（docker logs ${CONTAINER} --tail 200 | grep 云枢）"
  fi
elif [[ -d "$LOG_DIR" ]]; then
  if grep -rq "$STARTUP_MARK" "$LOG_DIR" 2>/dev/null; then
    log_ok=1
    echo "  [OK] ${LOG_DIR}/ 含启动标志"
  else
    echo "  [WARN] ${LOG_DIR}/ 未找到启动标志（裸机 stdout 可能未落盘，健康检查已通过即视为就绪）"
  fi
else
  echo "  [WARN] 未指定 --container 且 ${LOG_DIR}/ 不存在，跳过日志检查（健康检查已通过）"
fi

echo ""
if [[ "$ready" -eq 1 ]]; then
  echo "[OK] 服务就绪（健康检查通过）"
  exit 0
fi
exit 1
