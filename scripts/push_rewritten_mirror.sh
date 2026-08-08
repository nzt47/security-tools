#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# 阶段 2：强推重写后的镜像到远端（GitHub / Gitee）
# 前置：阶段 1 已完成（scripts/rewrite_history_filter_repo.sh 或手工生成镜像，
#       镜像内 _edge_profile 对象残留已归零，filter-repo 已删除 origin remote）。
#
# 【安全设计】（不易：强推不可逆）
#   1. 推前复验镜像完整性：对象残留=0、分支数>0、commit 数>0
#   2. 显示目标远端 URL + 冻结确认（团队已冻结写入窗口）→ 必须 --yes
#   3. 推送后自动校验：ls-remote 远端 hash 与镜像 refs 逐分支比对
#   4. 自动输出回滚提示（仅 git filter-repo 类历史重写可回滚，普通 push 不可）
#
# 【用法】（对两个远端各执行一次）
#   bash scripts/push_rewritten_mirror.sh \
#       --mirror /c/Windows/Temp/fr-origin \
#       --url git@github.com:nzt47/security-tools.git --yes
#   bash scripts/push_rewritten_mirror.sh \
#       --mirror /c/Windows/Temp/fr-gitee \
#       --url git@gitee.com:nzt47/security-tools.git --yes
#
# 【退出码】0=推送并校验通过  1=失败/校验不通过  2=用法/前置错误
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

MIRROR=""
REMOTE_URL=""
CONFIRMED=0

log()  { printf '\n\033[1m[%s]\033[0m %s\n' "$(date '+%H:%M:%S')" "$*"; }
die()  { printf '\033[31m[ERROR]\033[0m %s\n' "$*" >&2; exit "${2:-1}"; }

usage() {
  cat <<'EOF'
用法:
  bash scripts/push_rewritten_mirror.sh --mirror <镜像目录> --url <远端URL> [--yes]
EOF
}

# ── 参数解析 ──────────────────────────────────────────────────────────
while [ $# -gt 0 ]; do
  case "$1" in
    --mirror) MIRROR="$2"; shift 2 ;;
    --url) REMOTE_URL="$2"; shift 2 ;;
    --yes) CONFIRMED=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "未知参数: $1" 2 ;;
  esac
done
[ -n "$MIRROR" ] || die "缺少 --mirror 参数" 2
[ -n "$REMOTE_URL" ] || die "缺少 --url 参数" 2
[ "$CONFIRMED" -eq 1 ] || die "破坏性操作需 --yes 确认（确认团队已冻结远端写入窗口）" 2
[ -d "$MIRROR" ] || die "镜像目录不存在: $MIRROR" 2

cd "$MIRROR"

# ── 推前复验 ──────────────────────────────────────────────────────────
log "推前镜像完整性复验"
left=$(git rev-list --all --objects | grep -c "_edge_profile" || true)
[ "$left" -eq 0 ] || die "镜像对象残留 ${left} 个 _edge_profile（阶段 1 未完成），中止"
branches=$(git for-each-ref refs/heads | wc -l | tr -d ' ')
commits=$(git rev-list --all --count | tr -d ' ')
echo "  ✓ 对象残留=0"
echo "  ✓ 分支数=$branches  commit 数=$commits"
[ "$branches" -gt 0 ] || die "镜像无任何分支" 1
[ "$commits" -gt 0 ] || die "镜像无任何 commit" 1

# ── 配置远端 ──────────────────────────────────────────────────────────
log "配置远端 origin = $REMOTE_URL"
if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$REMOTE_URL"
else
  git remote add origin "$REMOTE_URL"
fi

log "强推准备就绪（目标: $REMOTE_URL，分支 $branches 个）"
echo "  ⚠️ 强制推送会覆盖远端全部历史，影响所有协作者与未合并 PR。Ctrl+C 可中止。"
sleep 3

# ── 强推 ──────────────────────────────────────────────────────────────
log "强推所有分支"
git push --force origin --all  || die "强推分支失败（网络/权限），远端可能未变化" 1
log "强推所有 tags"
git push --force origin --tags || die "强推 tags 失败" 1

# ── 推后校验 ──────────────────────────────────────────────────────────
log "推后校验：远端 hash 与镜像逐分支比对"
mismatch=0
while read -r sha ref; do
  branch="${ref#refs/heads/}"
  [ "$branch" = "$ref" ] && continue
  remote_sha=$(git ls-remote origin "refs/heads/$branch" 2>/dev/null | awk '{print $1}')
  if [ -z "$remote_sha" ]; then
    echo "  [FAIL] 远端缺少分支: $branch"
    mismatch=1
  elif [ "$remote_sha" != "$sha" ]; then
    echo "  [FAIL] hash 不一致: $branch 本地=$sha 远端=$remote_sha"
    mismatch=1
  else
    echo "  [PASS] $branch  hash=${sha:0:10}"
  fi
# 显式两列格式：新版 git for-each-ref 默认输出含对象类型列，须指定 format
# （否则 branch=${ref#refs/heads/} 剥除失败，校验循环被全部 continue 跳过）
done < <(git for-each-ref --format='%(objectname) %(refname)' refs/heads)

if [ "$mismatch" -ne 0 ]; then
  die "远端与镜像存在不一致，请人工核查（可能强推被远端规则拒绝）" 1
fi

# ── 汇总 ──────────────────────────────────────────────────────────────
log "强推完成并校验通过"
echo "  ✓ $REMOTE_URL 全部 ${branches} 个分支 hash 与重写后镜像一致"
echo "  ── 后续必做 ──"
echo "  1. 执行《验收报告_强推后验证》逐项确认（PR 重建/账号轮换）"
echo "  2. 通知协作者: 重新 clone 或强制重置到新历史"
echo "  3. 本地旧仓库禁止再次 push（旧对象仍含凭据）"
exit 0
