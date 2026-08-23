#!/usr/bin/env bash
# 本地模拟 CI/CD test 步骤（对应 .github/workflows/deploy.yml 的 test job）
#
# 执行：路由挂载自检 + A/B 单测（与 CI 相同的两个文件）
#
# 用法：
#   ./run_ci_test_local.sh                 # 全量（含开放接口鉴权单测，较慢）
#   ./run_ci_test_local.sh --quick         # 仅审计告警单测（快，跳过 open_api）
#   ./run_ci_test_local.sh --python py     # 指定 python 解释器
#
# 退出码：0 = 全部通过；1 = 任一失败
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# 探测可用 Python 解释器（Git Bash/Windows 下 python 可能不在 PATH）
detect_python() {
  for c in python3 python py; do
    if command -v "$c" >/dev/null 2>&1; then
      echo "$c"
      return 0
    fi
  done
  echo "python"
}
PYTHON="${PYTHON:-$(detect_python)}"
QUICK=0
TEST_FILES=(
  "tests/unit/test_audit_alert_analyze.py"   # A 项告警逻辑 + B 项 SMTP 发送
  "tests/unit/test_open_api_endpoints.py"    # 开放接口鉴权（8 端点 401/403/200）
)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --quick) QUICK=1; shift ;;
    --python) PYTHON="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

cd "$ROOT"
FAILED=0

echo "== [test] 路由挂载自检 =="
"$PYTHON" scripts/check_mounted_routes.py || FAILED=1

echo ""
echo "== [test] 单测（pytest）=="
PYTEST_ARGS=("$PYTHON" -m pytest)
if [[ "$QUICK" -eq 1 ]]; then
  PYTEST_ARGS+=("${TEST_FILES[0]}")   # 仅审计告警（快速）
  echo "（--quick 模式：仅 ${TEST_FILES[0]}）"
else
  PYTEST_ARGS+=("${TEST_FILES[@]}")
fi
PYTEST_ARGS+=(-q)
"${PYTEST_ARGS[@]}" || FAILED=1

echo ""
if [[ "$FAILED" -eq 0 ]]; then
  echo "[OK] 本地 CI test 模拟全部通过"
  exit 0
fi
echo "[FAIL] 存在失败项（见上方输出）"
exit 1
