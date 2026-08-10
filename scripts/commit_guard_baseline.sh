#!/bin/sh
# 一键提交 .guard_baseline.json 到 git 跟踪（团队共享增量阻断豁免口径）
#
# 用法（在 git 仓库根目录执行）:
#   bash scripts/commit_guard_baseline.sh            # Linux / macOS / Git Bash
#   & "C:\Program Files\Git\bin\bash.exe" scripts\commit_guard_baseline.sh   # Windows PowerShell
#
# 说明:
#   - 基线文件应在首次运行生成后立即提交，团队 pull 后豁免口径一致。
#   - 提交走 pre-commit hook（--strict）：若存在 FAIL 或基线外新增 WARN，
#     提交被拦截并提示，请先修复再重试（不要用 --no-verify 绕过）。
set -u

echo "==> 提交 .guard_baseline.json（增量阻断豁免基线）"

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
if [ -z "$ROOT" ]; then
  echo "[error] 当前目录不是 git 仓库（git rev-parse 失败）"
  exit 1
fi
cd "$ROOT" || exit 1

BASELINE="$ROOT/.guard_baseline.json"
if [ ! -f "$BASELINE" ]; then
  echo "[error] 基线文件不存在: $BASELINE"
  echo "       首次提交时 guard 会自动生成；或运行 python scripts/pre_commit_ci_guard.py --update-baseline"
  exit 1
fi

echo "==> git add .guard_baseline.json"
git add .guard_baseline.json || { echo "[error] git add 失败"; exit 1; }

echo "==> git commit（触发 guard hook，--strict 增量阻断）"
git commit -m "chore(guard): 提交 .guard_baseline.json 增量阻断豁免基线（团队共享）"
RC=$?

if [ "$RC" -eq 0 ]; then
  echo "[ok] 基线已提交：$(git rev-parse --short HEAD)"
  echo "    推送: git push（首次需 git push -u origin <branch>）"
else
  echo ""
  echo "[warn] 提交被拦截（guard 存在 FAIL 项或基线外新增 WARN）。"
  echo "       请修复后重试；排查见 docs/troubleshooting/pre_commit_ci_guard_部署操作手册_20260810.md §4"
fi
exit $RC
