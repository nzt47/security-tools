#!/bin/sh
# 一键安装 pre_commit_ci_guard hook — 部署操作手册 §2 安装步骤自动化
#
# 用法（在 git 仓库根目录执行）:
#   bash scripts/install_guard.sh            # Linux / macOS / Git Bash
#   & "C:\Program Files\Git\bin\bash.exe" scripts\install_guard.sh   # Windows PowerShell
#
# 步骤: 1) 定位仓库根  2) 确保 guard 脚本存在（缺失时从发布包恢复）
#       3) 安装 hook（容错 + 增量阻断 + 链式 pre-commit 框架）
#       4) 端到端验证
set -u

echo "==> pre_commit_ci_guard 一键安装"

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
if [ -z "$ROOT" ]; then
  echo "[error] 当前目录不是 git 仓库（git rev-parse 失败）"
  exit 1
fi
echo "==> 目标仓库: $ROOT"

GUARD="$ROOT/scripts/pre_commit_ci_guard.py"
if [ ! -f "$GUARD" ]; then
  SRC="$ROOT/release/pre_commit_ci_guard/pre_commit_ci_guard.py"
  if [ -f "$SRC" ]; then
    mkdir -p "$ROOT/scripts"
    cp "$SRC" "$GUARD"
    echo "[ok] guard 脚本缺失，已从发布包恢复: $GUARD"
  else
    echo "[error] 未找到 guard 脚本: $GUARD"
    echo "       请先获取发布包 release/pre_commit_ci_guard_*.zip 并解压到 release/pre_commit_ci_guard/，或联系维护者。"
    exit 1
  fi
fi

if ! command -v python >/dev/null 2>&1; then
  echo "[error] 未找到 python 命令（需 Python >= 3.8）"
  exit 1
fi

echo "==> 安装 pre-commit hook..."
python "$GUARD" --install-hook || { echo "[error] hook 安装失败"; exit 1; }

echo "==> 验证（--static-only --strict）..."
python "$GUARD" --static-only --strict
RC=$?
if [ "$RC" -ne 0 ]; then
  echo ""
  echo "[warn] 当前仓库存在 FAIL 项或基线外新增 WARN（见上方输出）。"
  echo "       修复前提交会被拦截——这是增量阻断的预期行为，请处理后再提交。"
else
  echo "[ok] guard 检查通过（存量 WARN 豁免、无新增阻断）"
fi

if command -v pre-commit >/dev/null 2>&1; then
  echo "==> 检测到 pre-commit 框架（$(pre-commit --version)）：commit 阶段将链式运行 .pre-commit-config.yaml 的 hooks（失败仅警告放行）"
fi

echo "[ok] 安装完成。此后每次 git commit 自动执行检查。"
echo "    卸载: python scripts/pre_commit_ci_guard.py --uninstall 或 python <发布包>/install.py --uninstall --repo $ROOT"
exit 0
