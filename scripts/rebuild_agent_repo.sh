#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# agent 工作区仓库：重新 clone + 迁移脚本（凭据泄露处置最后一步）
#
# 目标：将 agent/.git（含 _edge_profile 旧对象的本地历史）替换为
#      重新 clone 的重写后新历史，同时完整保留工作区未提交文件。
#
# 流程：快照未提交状态 → 临时 clone（校验新历史）→ 备份旧 .git →
#      迁移新 .git → 恢复 remote（origin+gitee）→ 验证对照
#
# 用法（Git Bash 执行）:
#   bash scripts/rebuild_agent_repo.sh                      # dry-run：只打印计划，不动任何文件
#   bash scripts/rebuild_agent_repo.sh --from-mirror        # 从本地镜像 fr-origin 克隆（无网络，推荐）
#   bash scripts/rebuild_agent_repo.sh --skip-clone <目录>  # 复用已有临时 clone（跳过 clone，供 IDE 关闭后收尾）
#   bash scripts/rebuild_agent_repo.sh --execute --yes      # 正式执行（需双确认）
#
# 安全设计（不易）:
#   - 默认 dry-run；--execute 才真正执行；--yes 二次确认
#   - 旧 .git 只重命名备份（.git.legacy-<时间戳>），可回滚
#   - clone 后先校验 HEAD=ecd9a9cd 且历史无 _edge_profile，再替换
#   - 工作区文件全程不动；未提交内容以快照文件留证
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

REMOTE_ORIGIN="git@github.com:nzt47/security-tools.git"
REMOTE_GITEE="git@gitee.com:nzt47/security-tools.git"
EXPECT_HEAD="ecd9a9cdad5cdd10e672ac495727732128c17d64"
MIRROR_DIR="/c/Windows/Temp/fr-origin"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NOW="$(date +%Y%m%d_%H%M%S)"
CLONE_DIR="${TEMP:-/tmp}/agent-clone-${NOW}"
SNAPSHOT="$ROOT/scripts/.agent_pre_migration_snapshot_${NOW}.txt"
MODE="dry-run"
YES=0
FROM_MIRROR=0
SKIP_CLONE=""

for a in "$@"; do
  case "$a" in
    --execute)      MODE="execute" ;;
    --yes)          YES=1 ;;
    --from-mirror)  FROM_MIRROR=1 ;;
    --skip-clone)   SKIP_CLONE="${2:-}" ; shift ;;
    *) echo "[ERROR] 未知参数: $a" >&2; exit 2 ;;
  esac
done

# ── 前置检查 ──────────────────────────────────────────────────────────
[ -d "$ROOT/.git" ] || { echo "[ERROR] 未找到 $ROOT/.git" >&2; exit 2; }
command -v git >/dev/null 2>&1 || { echo "[ERROR] 需要 git" >&2; exit 2; }
if [ "$MODE" = "execute" ] && [ "$YES" -ne 1 ]; then
  echo "[ERROR] 正式执行需 --execute --yes 双确认" >&2
  exit 2
fi

echo "═══ agent 仓库重新 clone 迁移（模式: $MODE）═══"
echo "  工作区: $ROOT   时间: $NOW"
echo "  当前分支: $(git -C "$ROOT" branch --show-current)   未提交: $(git -C "$ROOT" status --porcelain | wc -l) 行"

# ── 1. 快照当前未提交状态 ─────────────────────────────────────────────
echo ""
echo "── 1. 快照未提交状态 ──"
echo "  git status --porcelain → $SNAPSHOT"
if [ "$MODE" = "execute" ]; then
  git -C "$ROOT" status --porcelain > "$SNAPSHOT"
  echo "  ✓ 已保存 $(wc -l < "$SNAPSHOT") 行未提交记录"
fi

# ── 2. 临时 clone 并校验新历史 ────────────────────────────────────────
echo ""
echo "── 2. 临时 clone（重写后历史）──"
CLONE_SRC="$REMOTE_ORIGIN"
if [ -n "$SKIP_CLONE" ]; then
  CLONE_SRC="$SKIP_CLONE"
  [ -d "$SKIP_CLONE/.git" ] || { echo "[ERROR] --skip-clone 目录无 .git: $SKIP_CLONE" >&2; exit 2; }
  CLONE_DIR="$SKIP_CLONE"
elif [ "$FROM_MIRROR" -eq 1 ]; then
  CLONE_SRC="$MIRROR_DIR"
  [ -d "$MIRROR_DIR" ] || { echo "[ERROR] 镜像不存在: $MIRROR_DIR（可去掉 --from-mirror 走远端）" >&2; exit 2; }
fi
echo "  git clone $CLONE_SRC $CLONE_DIR"
if [ "$MODE" = "execute" ]; then
  if [ -z "$SKIP_CLONE" ]; then
    git clone "$CLONE_SRC" "$CLONE_DIR"
  else
    echo "  （--skip-clone：复用已有 clone，跳过）"
  fi
  head_new="$(git -C "$CLONE_DIR" rev-parse HEAD)"
  if [ "$head_new" != "$EXPECT_HEAD" ]; then
    echo "[ERROR] 新 clone HEAD=$head_new，期望 $EXPECT_HEAD → 中止并清理" >&2
    rm -rf "$CLONE_DIR"; exit 1
  fi
  if git -C "$CLONE_DIR" rev-list --all --objects 2>/dev/null | grep -q "_edge_profile"; then
    echo "[ERROR] 新 clone 历史仍含 _edge_profile → 中止并清理" >&2
    rm -rf "$CLONE_DIR"; exit 1
  fi
  echo "  ✓ 新历史校验通过（HEAD=$EXPECT_HEAD，无 _edge_profile 残留）"
fi

# ── 3. 备份旧 .git ────────────────────────────────────────────────────
echo ""
echo "── 3. 备份旧 .git（可回滚）──"
echo "  mv $ROOT/.git $ROOT/.git.legacy-$NOW"
if [ "$MODE" = "execute" ]; then
  mv "$ROOT/.git" "$ROOT/.git.legacy-$NOW"
  echo "  ✓ 已备份"
fi

# ── 4. 迁移新 .git 到工作区 ───────────────────────────────────────────
echo ""
echo "── 4. 迁移新 .git ──"
echo "  mv $CLONE_DIR/.git $ROOT/.git   （工作区文件不动）"
echo "  rm -rf $CLONE_DIR               （仅删临时 clone 的检出文件）"
if [ "$MODE" = "execute" ]; then
  mv "$CLONE_DIR/.git" "$ROOT/.git"
  rm -rf "$CLONE_DIR"
  echo "  ✓ 迁移完成，临时目录已清理"
fi

# ── 5. 恢复 remote ────────────────────────────────────────────────────
echo ""
echo "── 5. 恢复 remote（origin + gitee）──"
echo "  git remote add origin $REMOTE_ORIGIN"
echo "  git remote add gitee  $REMOTE_GITEE"
if [ "$MODE" = "execute" ]; then
  git -C "$ROOT" remote add origin "$REMOTE_ORIGIN"
  git -C "$ROOT" remote add gitee "$REMOTE_GITEE"
  echo "  ✓ remote 已恢复"
fi

# ── 6. 迁移后验证 ─────────────────────────────────────────────────────
echo ""
echo "── 6. 迁移后验证 ──"
echo "  HEAD: git rev-parse HEAD（期望 $EXPECT_HEAD）"
echo "  remote: git remote -v"
echo "  未提交对照: git status --porcelain vs 快照（工作区内容应仍在）"
if [ "$MODE" = "execute" ]; then
  head_after="$(git -C "$ROOT" rev-parse HEAD)"
  echo ""
  if [ "$head_after" = "$EXPECT_HEAD" ]; then
    echo "  [PASS] HEAD=$head_after"
  else
    echo "  [FAIL] HEAD=$head_after（期望 $EXPECT_HEAD）" >&2
  fi
  echo "  [INFO] remote:"
  git -C "$ROOT" remote -v | sed 's/^/         /'
  n_after="$(git -C "$ROOT" status --porcelain | wc -l)"
  n_before="$(wc -l < "$SNAPSHOT")"
  echo "  [INFO] 未提交记录: 迁移前=$n_before  迁移后=$n_after"
  if [ "$n_after" -ge "$n_before" ]; then
    echo "  [PASS] 未提交内容已保留（迁移后多于或等于快照属正常：含 clone 后新增的差异）"
  else
    echo "  [WARN] 迁移后未提交记录少于快照，请人工对照 $SNAPSHOT 核对" >&2
  fi
  echo ""
  echo "  后续动作："
  echo "  1) git status 逐项确认未提交内容，重新提交并推送"
  echo "  2) 确认无异常后清理备份: rm -rf $ROOT/.git.legacy-$NOW"
  echo "  3) git rev-list --all --objects | grep _edge_profile  → 应无输出"
fi

case "$MODE" in
  dry-run) echo ""
           echo "  结果: dry-run，未改动任何文件。正式执行: bash scripts/rebuild_agent_repo.sh --execute --yes" ;;
  *)       echo ""
           echo "  结果: 迁移执行完成，请按上方验证结果处理" ;;
esac
