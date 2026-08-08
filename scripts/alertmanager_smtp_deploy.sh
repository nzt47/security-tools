#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# Alertmanager → 139 邮箱 SMTP 链路：一键部署工具（统一入口）
#
# 打包的三个脚本：
#   apply     → scripts/apply_smtp_auth_code.py      注入授权码 + 587 端口验证
#   simulate  → scripts/simulate_prod_smtp_e2e.py    端到端模拟测试（本地 mock / 生产）
#   verify    → scripts/verify_prod_alertmanager.sh  生产一键验收（防火墙 + 邮件测试）
#
# 用法：
#   bash scripts/alertmanager_smtp_deploy.sh apply [--interactive] [--auth-code <真实授权码>]
#   bash scripts/alertmanager_smtp_deploy.sh simulate [--local-mock] [--auth-code <码>] [--report-out <path>]
#   bash scripts/alertmanager_smtp_deploy.sh verify   [--send-test]
#   bash scripts/alertmanager_smtp_deploy.sh full     [--interactive] [--auth-code <真实授权码>] [--send-test]
#   bash scripts/alertmanager_smtp_deploy.sh help
#
# 说明：
#   - 安全注入授权码：推荐 --interactive（交互输入，不进 shell 历史/进程列表）；
#     或经环境变量 SMTP_AUTH_CODE 注入（⚠️ 整行仍留在 shell 历史）。
#   - verify 需在目标生产服务器执行（依赖 docker 容器与出站 587）。
#   - 详细步骤见 docs/zh/知识库重构计划/生产操作手册_AlertmanagerSMTP_20260808.md
# ═══════════════════════════════════════════════════════════════════════
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPLY="$HERE/apply_smtp_auth_code.py"
SIMULATE="$HERE/simulate_prod_smtp_e2e.py"
VERIFY="$HERE/verify_prod_alertmanager.sh"

PYTHON="${PYTHON:-python3}"
BASH_BIN="${BASH_BIN:-bash}"

need_python() {
  if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "[ERROR] 未找到 python3（可用 PYTHON=python 指定）" >&2
    exit 2
  fi
}

cmd_apply() {
  need_python
  echo "── [apply] 注入 SMTP 授权码 + 587 端口验证 ──"
  "$PYTHON" "$APPLY" "$@"
}

cmd_simulate() {
  need_python
  echo "── [simulate] SMTP 端到端模拟测试 ──"
  "$PYTHON" "$SIMULATE" "$@"
}

cmd_verify() {
  if ! command -v "$BASH_BIN" >/dev/null 2>&1; then
    echo "[ERROR] 未找到 bash（verify 依赖 verify_prod_alertmanager.sh）" >&2
    exit 2
  fi
  echo "── [verify] 生产一键验收（防火墙 + 邮件发送测试）──"
  "$BASH_BIN" "$VERIFY" "$@"
}

cmd_full() {
  # full: apply（注入授权码）→ verify（验收）。授权码来源：--interactive / --auth-code / SMTP_AUTH_CODE。
  apply_args=()
  verify_args=()
  auth_code=""
  interactive=0
  send_test=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --auth-code)
        auth_code="${2:-}"; apply_args+=("--auth-code" "$auth_code"); shift 2 ;;
      --interactive) interactive=1; apply_args+=("--interactive"); shift ;;
      --send-test) send_test=1; shift ;;
      *) apply_args+=("$1"); shift ;;
    esac
  done
  if [ "$interactive" -eq 0 ] && [ -z "$auth_code" ] && [ -n "${SMTP_AUTH_CODE:-}" ]; then
    auth_code="$SMTP_AUTH_CODE"
  fi
  if [ "$interactive" -eq 0 ] && [ -z "$auth_code" ]; then
    echo "[ERROR] full 需要 --interactive（交互输入）、--auth-code 参数或 SMTP_AUTH_CODE 环境变量" >&2
    exit 2
  fi
  cmd_apply "${apply_args[@]}" || { echo "[ERROR] apply 失败，终止 full" >&2; exit 1; }
  if [ "$send_test" -eq 1 ]; then
    verify_args=("--send-test")
  fi
  cmd_verify "${verify_args[@]}"
}

cmd_help() {
  cat <<'EOF'
Alertmanager SMTP 一键部署工具

子命令:
  apply    [--interactive] [--auth-code <真实授权码>] 注入授权码 + 验证 587 端口
           （--interactive 交互输入，不进 shell 历史/进程列表，推荐）
           （也支持 --skip-port-check / --config / --smtp-host / --smtp-port）
  simulate [--local-mock] [--auth-code <码>]       端到端模拟测试
           [--report-out <报告.md>] [--keep-code]
  verify   [--send-test]                           生产一键验收（防火墙+邮件测试）
  full     [--interactive] [--auth-code <真实授权码>] [--send-test] 注入授权码 → 自动验收
  help                                            本帮助

环境变量:
  SMTP_AUTH_CODE    授权码（⚠️ 整行仍留在 shell 历史，仅避免进程列表可见；
                    彻底规避请用 --interactive 交互输入）
  SMTP_HOST         默认 smtp.139.com
  SMTP_PORT         默认 587
  PYTHON            python 解释器（默认 python3）

示例:
  bash scripts/alertmanager_smtp_deploy.sh apply --interactive
  bash scripts/alertmanager_smtp_deploy.sh apply --auth-code 'xxxx'
  bash scripts/alertmanager_smtp_deploy.sh simulate --local-mock --auth-code mock
  bash scripts/alertmanager_smtp_deploy.sh verify --send-test
  bash scripts/alertmanager_smtp_deploy.sh full --interactive --send-test
EOF
}

main() {
  if [ $# -eq 0 ]; then
    cmd_help
    exit 1
  fi
  sub="$1"; shift
  case "$sub" in
    apply)    cmd_apply "$@" ;;
    simulate) cmd_simulate "$@" ;;
    verify)   cmd_verify "$@" ;;
    full)     cmd_full "$@" ;;
    help|-h|--help) cmd_help ;;
    *)
      echo "[ERROR] 未知子命令: $sub（可用: apply / simulate / verify / full / help）" >&2
      exit 2
      ;;
  esac
}

main "$@"
