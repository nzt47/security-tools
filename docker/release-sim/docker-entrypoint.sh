#!/usr/bin/env bash
# docker-entrypoint.sh — release-sim 入口
#   --health : 环境自检（发布依赖工具齐全性），任一核心工具缺失 exit 1
#              挂载脚本检查为可选提示（不挂载 /project 时提示，不判失败）
#   其他     : exec 透传（bash / 自定义命令）
set -uo pipefail

if [ "${1:-}" = "--health" ]; then
  FAIL=0
  echo "=== release-sim 环境自检 ==="
  # 核心发布依赖（对齐 GitHub Actions ubuntu-latest runner）
  for tool in git python3 pwsh curl gh; do
    if command -v "$tool" >/dev/null 2>&1; then
      echo "  $tool: $($tool --version 2>&1 | head -n 1)"
    else
      echo "  $tool: MISSING"
      FAIL=1
    fi
  done
  # 挂载脚本（可选：CI 模拟用法 -v "$(pwd):/project" 时存在）
  for f in /project/scripts/update_changelog.py /project/scripts/create_gitee_release.ps1 /project/scripts/release_shell_lib.sh; do
    if [ -f "$f" ]; then
      echo "  $(basename "$f"): OK (挂载)"
    else
      echo "  $(basename "$f"): 未挂载（docker run 时用 -v \"$(pwd):/project\"）"
    fi
  done
  if [ "$FAIL" -eq 1 ]; then
    echo "=== 自检失败：存在缺失依赖 ==="
    exit 1
  fi
  echo "=== 自检通过：全部发布依赖就绪 ==="
  exit 0
fi

exec "$@"
