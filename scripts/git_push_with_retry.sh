#!/usr/bin/env bash
# ============================================================
# git_push_with_retry.sh — CI push 竞争防御通用工具
#
# 【不易】master 是受保护合入目标，CI 自动写回 master 的 job 在
#   push 前必须先与远端同步，否则并行 workflow 推进远端后直接
#   push 会报 non-fast-forward（见 docs/observability/
#   push_race_retry_simulation_retrospective_20260806.md）。
#   本工具是 ci.yml update-ci-dashboard job 重试逻辑的通用化封装，
#   行为与其完全一致：先 pull --rebase 再 push，最多重试 N 次，
#   耗尽后默认 ::warning:: 跳过（可丢失更新不阻塞 CI）。
#
# 【变易】重试次数/间隔/远端均可配置；--fail 可将"耗尽跳过"
#   切换为"失败退出"，适配必须成功的写回场景。
#
# 【简易】单文件零依赖（仅 bash + git），无状态，CI 与本地通用。
#
# 用法:
#   bash scripts/git_push_with_retry.sh <branch> [options]
#   options:
#     -r, --retries <N>    最大重试次数（默认 3）
#     -s, --sleep <SEC>    重试间隔秒数（默认 5）
#     --remote <NAME>      远端名（默认 origin）
#     --fail               耗尽后 exit 1（默认 exit 0 + ::warning::）
#     -h, --help           显示帮助
#
# 示例:
#   bash scripts/git_push_with_retry.sh master
#   bash scripts/git_push_with_retry.sh master --retries 5 --sleep 10
#   bash scripts/git_push_with_retry.sh master --fail
# ============================================================
set -u

BRANCH=""
REMOTE="origin"
RETRIES=3
SLEEP_SEC=5
FAIL_ON_EXHAUSTED=0

usage() {
  cat <<'EOF'
用法: bash scripts/git_push_with_retry.sh <branch> [options]
  <branch>            要推送的分支名（必需）
  -r, --retries <N>   最大重试次数（默认 3）
  -s, --sleep <SEC>   重试间隔秒数（默认 5）
  --remote <NAME>     远端名（默认 origin）
  --fail              耗尽后 exit 1（默认 exit 0 + ::warning::）
  -h, --help          显示本帮助
EOF
}

# 参数解析
while [ $# -gt 0 ]; do
  case "$1" in
    -r|--retries)
      RETRIES="$2"; shift 2 ;;
    -s|--sleep)
      SLEEP_SEC="$2"; shift 2 ;;
    --remote)
      REMOTE="$2"; shift 2 ;;
    --fail)
      FAIL_ON_EXHAUSTED=1; shift ;;
    -h|--help)
      usage; exit 0 ;;
    -)
      usage; exit 2 ;;
    -*)
      echo "错误: 未知参数 $1（用 -h 查看帮助）" >&2
      exit 2 ;;
    *)
      # 首个非选项参数为分支名；多余非选项参数视为用法错误
      if [ -n "$BRANCH" ]; then
        echo "错误: 多余的参数 $1（一次仅推送一个分支）" >&2
        exit 2
      fi
      BRANCH="$1"; shift ;;
  esac
done

# 输入校验（系统边界，防误用）
if [ -z "$BRANCH" ]; then
  echo "错误: 缺少分支名参数（用 -h 查看帮助）" >&2
  exit 2
fi
case "$RETRIES" in
  ''|*[!0-9]*) echo "错误: --retries 必须为正整数（got: $RETRIES）" >&2; exit 2 ;;
esac
case "$SLEEP_SEC" in
  ''|*[!0-9]*) echo "错误: --sleep 必须为正整数（got: $SLEEP_SEC）" >&2; exit 2 ;;
esac

echo "[git_push_with_retry] push $REMOTE/$BRANCH，最多重试 $RETRIES 次，间隔 ${SLEEP_SEC}s"

# 与 ci.yml update-ci-dashboard job 逻辑逐行一致（单一事实源）
for i in $(seq 1 "$RETRIES"); do
  # pull 成功（含 rebase）后再尝试 push；pull 失败也进入重试分支
  if git pull --rebase "$REMOTE" "$BRANCH"; then
    if git push "$REMOTE" "$BRANCH"; then
      echo "已推送 $REMOTE/$BRANCH (attempt $i)"
      exit 0
    fi
  fi
  echo "[git_push_with_retry] push 竞争失败 (attempt $i/$RETRIES)，${SLEEP_SEC}s 后重试"
  sleep "$SLEEP_SEC"
done

if [ "$FAIL_ON_EXHAUSTED" -eq 1 ]; then
  echo "::error::push $REMOTE/$BRANCH ${RETRIES} 次重试仍失败，退出"
  exit 1
fi
echo "::warning::push $REMOTE/$BRANCH ${RETRIES} 次重试仍失败，本次跳过（下次推送自动补齐）"
exit 0
