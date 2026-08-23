#!/usr/bin/env bash
# 将 .env 中的告警密钥（SMTP_* + AUDIT_ALERT_THRESHOLD）写入 GitHub Secrets
# 用途：CI 每日告警（audit-alert.yml Job1）运行时经 $GITHUB_ENV 读取这些密钥。
#
# 前置：已安装 gh CLI 并认证（gh auth login）；或使用 Web 界面手动添加（见
#       docs/zh/GitHubSecrets配置清单_20260816.md）。
#
# 用法（bash / Git Bash / WSL / Linux/macOS）：
#   ./setup_github_secrets.sh                        # 推断 repo，写入 7 项
#   ./setup_github_secrets.sh --repo owner/repo      # 指定仓库
#   ./setup_github_secrets.sh --env-file .env.local  # 指定 .env 路径
#   ./setup_github_secrets.sh --dry-run              # 只预览，不调用 gh
#
# 安全说明：SMTP_PASS 等敏感值不打印明文；dry-run 仅显示键名。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPO=""
ENV_FILE="$ROOT/.env"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) echo "未知参数: $1"; exit 2 ;;
  esac
done

[[ -f "$ENV_FILE" ]] || { echo "未找到 .env 文件: $ENV_FILE" >&2; exit 1; }

if [[ "$DRY_RUN" -ne 1 ]]; then
  command -v gh >/dev/null 2>&1 || {
    echo "未安装 gh CLI（https://cli.github.com），或请改用 Web 界面手动添加。" >&2
    exit 1
  }
fi

if [[ -z "$REPO" ]]; then
  REMOTE="$(git -C "$ROOT" remote get-url origin 2>/dev/null || true)"
  if [[ "$REMOTE" =~ [:/]([^/:]+/[^/.]+)(\.git)?$ ]]; then
    REPO="${BASH_REMATCH[1]}"
  fi
fi
[[ -n "$REPO" ]] || { echo "无法推断仓库，请用 --repo owner/repo 显式指定。" >&2; exit 1; }

TARGET_KEYS=(SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASS SMTP_TO SMTP_SSL AUDIT_ALERT_THRESHOLD)

# 解析 .env：仅提取目标键（支持引号包裹的值）
declare -A VALUES
while IFS= read -r LINE; do
  LINE="${LINE%%$'\r'}"
  [[ "$LINE" =~ ^[[:space:]]*(SMTP_[A-Z]+|AUDIT_ALERT_THRESHOLD)=(.*)$ ]] || continue
  NAME="${BASH_REMATCH[1]}"
  VAL="${BASH_REMATCH[2]}"
  VAL="${VAL#"${VAL%%[![:space:]]*}"}"          # trim 前导空白
  VAL="${VAL%"${VAL##*[![:space:]]}"}"         # trim 尾随空白
  VAL="${VAL%\"}"; VAL="${VAL#\"}"             # 去双引号
  VAL="${VAL%\'}"; VAL="${VAL#\'}"             # 去单引号
  VALUES["$NAME"]="$VAL"
done < "$ENV_FILE"

echo "目标仓库: $REPO"
echo "源文件:   $ENV_FILE"
SET_COUNT=0
for K in "${TARGET_KEYS[@]}"; do
  if [[ -z "${VALUES[$K]:-}" ]]; then
    echo "  [skip] $K （.env 缺失或为空）"
    continue
  fi
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "  [dry-run] gh secret set $K --repo $REPO"
  else
    gh secret set "$K" --body "${VALUES[$K]}" --repo "$REPO"
    echo "  [set] $K"
    SET_COUNT=$((SET_COUNT + 1))
  fi
done

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo ""
  echo "[dry-run] 预览完成（未调用 gh）。确认后去掉 --dry-run 执行。"
else
  echo ""
  echo "完成：已写入 $SET_COUNT/${#TARGET_KEYS[@]} 项 Secrets（其余跳过）。"
  echo "验证：gh secret list --repo $REPO"
fi
