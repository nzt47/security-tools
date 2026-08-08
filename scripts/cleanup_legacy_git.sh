#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# 本地旧仓库 .git 目录清理工具（凭据泄露处置收尾）
#
# 背景：历史重写已在远端完成，但本地旧 .git 对象库仍含 _edge_profile 旧对象，
#       必须停止使用并清理，否则泄露数据仍残留在本地磁盘。
#
# 用法（Git Bash 执行）:
#   bash scripts/cleanup_legacy_git.sh                 # 默认 dry-run：只列出路径与将执行的动作
#   bash scripts/cleanup_legacy_git.sh --apply         # 执行：将旧 .git 重命名备份（不删除）
#   bash scripts/cleanup_legacy_git.sh --delete --yes  # 危险：真正删除（需双确认）
#   bash scripts/cleanup_legacy_git.sh --check-mirrors # 检查 %TEMP% 镜像残留（可手动删除）
#
# 安全设计（不易）:
#   - 默认 dry-run，绝不静默改动
#   - --apply 只做重命名备份（.git.legacy-<时间戳>），可回滚
#   - --delete 需 --yes 双确认
#   - 当前活动仓库（agent/.git）不直接动，给出重新 clone 指引
# ═══════════════════════════════════════════════════════════════════════
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NOW="$(date +%Y%m%d_%H%M%S)"
ACTION="dry-run"
NEED_YES=0

# 待处置路径（自动探测；若存在即列入）
CANDIDATES=(
  "$ROOT/security-tools/.git"   # 嵌套旧仓库（gitignore 已忽略该目录）
)
# 额外探测：项目内其他一级目录的 .git（排除运行时/备份目录）
for d in "$ROOT"/*/; do
  case "$d" in
    *node_modules*|*.venv*|*backup*|*workspace*|*.fix*|*.tmp*) continue ;;
  esac
  [ -d "$d.git" ] && CANDIDATES+=("$d.git")
done
# 去重
CANDIDATES=($(printf '%s\n' "${CANDIDATES[@]}" | sort -u))

parse_args() {
  for a in "$@"; do
    case "$a" in
      --apply)   ACTION="apply" ;;
      --delete)  ACTION="delete" ;;
      --yes)     NEED_YES=1 ;;
      --check-mirrors) ACTION="mirrors" ;;
      *) echo "[ERROR] 未知参数: $a" >&2; exit 2 ;;
    esac
  done
}
parse_args "$@"

echo "═══ 本地旧仓库 .git 清理工具（模式: $ACTION）═══"
echo "  仓库根: $ROOT   时间: $NOW"

# ── 1. 当前活动仓库（agent/.git）：不直接动，给指引 ─────────────────
echo ""
echo "── 1. 当前活动仓库（agent/.git）──"
echo "  ⚠️ 该 .git 为正在使用的仓库（工作区依赖），禁止原地删除。"
echo "  ✓ 处置方式：将工作区迁移到重新 clone 的仓库后，再删除旧目录："
echo "      git clone git@github.com:nzt47/security-tools.git <新目录>"
echo "      # 迁移未提交文件后，旧目录整体删除即可（含旧 .git）"

# ── 2. 嵌套/遗留 .git 目录 ───────────────────────────────────────────
echo ""
echo "── 2. 项目内嵌套/遗留 .git 目录 ──"
if [ "${#CANDIDATES[@]}" -eq 0 ]; then
  echo "  未发现需要清理的嵌套 .git 目录 ✓"
else
  for g in "${CANDIDATES[@]}"; do
    if [ ! -d "$g" ]; then
      echo "  · $g  (不存在，跳过)"
      continue
    fi
    size="$(du -sh "$g" 2>/dev/null | cut -f1)"
    case "$ACTION" in
      apply)
        bak="${g}.legacy-${NOW}"
        mv "$g" "$bak" && echo "  ✓ $g → 已重命名备份为 $bak ($size)" || echo "  ✗ 重命名失败: $g"
        ;;
      delete)
        if [ "$NEED_YES" -eq 1 ]; then
          rm -rf "$g" && echo "  ✖ $g 已删除 ($size)  [不可恢复]" || echo "  ✗ 删除失败: $g"
        else
          echo "  ⚠ 需要 --yes 确认后才删除: $g ($size)"
        fi
        ;;
      mirrors)
        echo "  · $g ($size)  提示: 若为重写镜像/旧备份，可自行评估后清理"
        ;;
      *)
        echo "  [dry-run] 将处理: $g ($size)"
        echo "            执行: bash scripts/cleanup_legacy_git.sh --apply  （重命名备份）"
        echo "            删除: bash scripts/cleanup_legacy_git.sh --delete --yes"
        ;;
    esac
  done
fi

# ── 3. 重写镜像（%TEMP%\fr-origin / fr-gitee）提示 ──────────────────
echo ""
echo "── 3. 重写镜像（阶段 2 强推产物，含重写后历史，无凭据）──"
for m in /c/Windows/Temp/fr-origin /c/Windows/Temp/fr-gitee; do
  if [ -d "$m" ]; then
    size="$(du -sh "$m" 2>/dev/null | cut -f1)"
    echo "  · $m ($size)"
    echo "    用途已完成（强推已结束）；确认不需要后可手动删除以释放空间"
  fi
done
if [ "$ACTION" = "mirrors" ]; then
  echo "  [--check-mirrors] 以上为镜像目录清单；手动删除命令示例："
  echo "      rm -rf /c/Windows/Temp/fr-origin /c/Windows/Temp/fr-gitee"
fi

# ── 4. 汇总 ──────────────────────────────────────────────────────────
echo ""
echo "── 4. 收尾检查建议 ──"
echo "  1) 重新 clone 后核对 master hash:  ecd9a9cdad5cdd10e672ac495727732128c17d64"
echo "  2) 确认本地不再存在 _edge_profile 的 git 对象引用:"
echo "       git rev-list --all --objects | grep _edge_profile   # 应无输出（新仓库中）"
echo "  3) 账号轮换清单未完成前，旧 .git 备份建议保留（勿删除）"

case "$ACTION" in
  apply)   echo "  结果: 已执行重命名备份（可回滚）" ;;
  delete)  [ "$NEED_YES" -eq 1 ] && echo "  结果: 已执行删除" || echo "  结果: 未删除（缺 --yes）" ;;
  *)       echo "  结果: dry-run，未改动任何文件。加 --apply 或 --delete --yes 执行。" ;;
esac
