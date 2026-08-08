#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# git filter-repo 历史重写 + 强制推送远端 —— SOP 执行脚本
# 用途：从 git 历史【彻底】移除 _edge_profile（含真实凭据），并强推远端。
#
# 【安全设计】（遵循"不易"：重写历史是不可逆破坏性操作）
#   1. 在 mirror 克隆副本中重写（官方推荐），绝不直接操作当前工作区仓库
#   2. 重写前自动备份镜像（tar 包）
#   3. 强推是破坏性动作：必须显式加 --push --yes 双确认
#   4. 全程打印远端 URL 供人工核对
#
# 【用法】
#   bash scripts/rewrite_history_filter_repo.sh               # 阶段1：镜像克隆 + filter-repo + 验证（安全）
#   bash scripts/rewrite_history_filter_repo.sh --push --yes  # 阶段2：强推远端（破坏性，需 --yes）
#   bash scripts/rewrite_history_filter_repo.sh --path <路径> # 自定义移除路径（默认 _edge_profile）
#   bash scripts/rewrite_history_filter_repo.sh --remote gitee # 指定远端（默认 origin；多远端逐个执行）
#
# 【退出码】0=成功  1=验证失败/操作中止  2=用法/前置错误
# 【前置】需 git-filter-repo（缺失时自动提示安装：pip install git-filter-repo）
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

REMOVE_PATH="${REMOVE_PATH:-_edge_profile}"
REMOTE_NAME="${REMOTE_NAME:-origin}"
WORK_DIR=""
BACKUP_TAR=""

log()  { printf '\n\033[1m[%s]\033[0m %s\n' "$(date '+%H:%M:%S')" "$*"; }
die()  { printf '\033[31m[ERROR]\033[0m %s\n' "$*" >&2; exit "${2:-1}"; }

usage() {
  cat <<'EOF'
用法:
  bash scripts/rewrite_history_filter_repo.sh [--path <路径>] [--remote <远端名>]
  bash scripts/rewrite_history_filter_repo.sh --push --yes [--path <路径>] [--remote <远端名>]
EOF
}

# ── 参数解析 ──────────────────────────────────────────────────────────
DO_PUSH=0
while [ $# -gt 0 ]; do
  case "$1" in
    --push) DO_PUSH=1; shift ;;
    --yes)  shift ;;
    --path) REMOVE_PATH="$2"; shift 2 ;;
    --remote) REMOTE_NAME="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "未知参数: $1" 2 ;;
  esac
done

# ── 前置检查 ──────────────────────────────────────────────────────────
log "前置检查"
command -v git >/dev/null 2>&1 || die "未找到 git 命令" 2
# git-filter-repo 常随 pip 装入 Windows Python Scripts（Git Bash 的 PATH 可能不含该目录）
for d in "${LOCALAPPDATA:-$HOME/AppData/Local}/Programs/Python/"*/Scripts \
         /c/Users/*/AppData/Local/Programs/Python/*/Scripts; do
  [ -d "$d" ] && PATH="$d:$PATH"
done
if ! command -v git-filter-repo >/dev/null 2>&1 && ! git filter-repo --version >/dev/null 2>&1; then
  die "未找到 git-filter-repo，请先安装: pip install git-filter-repo" 2
fi
REMOTE_URL="$(git remote get-url "$REMOTE_NAME" 2>/dev/null || true)"
if [ -z "$REMOTE_URL" ]; then
  die "当前仓库无远端 $REMOTE_NAME（git remote -v 确认）。未推送过则无需历史重写，直接忽略即可。" 2
fi
log "远端[$REMOTE_NAME]: $REMOTE_URL"
echo "  ⚠️ 请人工确认上方远端为【目标仓库】。重写不可逆！"

# ── 阶段1：镜像克隆 + filter-repo + 验证 ──────────────────────────────
log "创建镜像克隆副本（不触碰当前工作区）"
WORK_DIR="$(mktemp -d)"
MIRROR="$WORK_DIR/filter-repo-mirror.git"
git clone --mirror "$REMOTE_URL" "$MIRROR" >/dev/null 2>&1 || die "镜像克隆失败（网络/权限），中止"
echo "  镜像目录: $MIRROR"

BACKUP_TAR="$WORK_DIR/mirror-backup-$(date +%Y%m%d%H%M%S).tar"
tar -cf "$BACKUP_TAR" -C "$WORK_DIR" "filter-repo-mirror.git"
log "备份完成: $BACKUP_TAR"

cd "$MIRROR"
log "执行 filter-repo 移除路径: $REMOVE_PATH"
git filter-repo --path "$REMOVE_PATH" --invert-paths --force

log "验证历史已清除"
left=$(git rev-list --all --objects | grep -c "$REMOVE_PATH" || true)
if [ "$left" -ne 0 ]; then
  die "验证失败：对象库仍残留 ${left} 个 $REMOVE_PATH 对象"
fi
added=$(git log --all --diff-filter=A --oneline -- "$REMOVE_PATH" | wc -l | tr -d ' ')
if [ "$added" -ne 0 ]; then
  die "验证失败：历史中仍存在新增 $REMOVE_PATH 的提交"
fi
echo "  ✓ 对象库残留: 0"
echo "  ✓ 历史新增提交: 0"
echo "  ✓ $REMOVE_PATH 已从全部可达历史中移除"

# ── 阶段2：强推远端（仅显式 --push --yes） ─────────────────────────────
if [ "$DO_PUSH" -eq 0 ]; then
  log "阶段1 完成。下一步（人工决策）在镜像目录执行强推："
  echo "  cd \"$MIRROR\""
  echo "  git push --force $REMOTE_NAME --all && git push --force $REMOTE_NAME --tags"
  echo "  或直接重跑: bash scripts/rewrite_history_filter_repo.sh --push --yes"
  echo "  ⚠️ 强推后：通知所有协作者重新 clone；历史凭据视为已泄露 → 立即轮换账号密码"
  exit 0
fi

log "强推远端（破坏性操作）"
echo "  目标远端: $REMOTE_URL"
echo "  备份镜像: $BACKUP_TAR"
echo "  ⚠️ 强推会覆盖远端历史，影响所有协作者！Ctrl+C 可中止。"
sleep 3
git push --force "$REMOTE_NAME" --all
git push --force "$REMOTE_NAME" --tags

log "重写后清理（使旧对象真正被回收）"
git reflog expire --expire=now --all
git gc --prune=now --aggressive >/dev/null 2>&1 || true

log "全部完成"
echo "  ✓ 历史已重写并强推: $REMOTE_URL"
echo "  ✓ 备份保留: $BACKUP_TAR（确认无问题后可删除）"
echo "  ── 后续必做 ──"
echo "  1. 通知所有协作者: git fetch --all --prune 后强制重置或重新 clone"
echo "  2. 立即执行紧急响应预案（账号轮换 + 强制登出）"
echo "  3. 原本地仓库已含旧历史对象，须删除或重新 clone，避免再次 push 污染"
exit 0
