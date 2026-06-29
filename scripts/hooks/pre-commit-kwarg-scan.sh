#!/bin/bash
# Git pre-commit hook — 关键字参数冲突扫描
#
# 安装: cp scripts/hooks/pre-commit-kwarg-scan.sh .git/hooks/pre-commit
#       chmod +x .git/hooks/pre-commit
#
# 功能: 提交前自动扫描 agent/ 目录，发现 HIGH 风险时阻断提交

set -e

echo "=== 关键字参数冲突扫描 (HIGH) ==="

# 获取项目根目录
PROJECT_ROOT=$(git rev-parse --show-toplevel)
cd "$PROJECT_ROOT"

# 运行扫描器
python scripts/scan_kwarg_conflicts.py \
  --path agent/ \
  --min-risk HIGH \
  --format text \
  --output /tmp/kwarg-scan-result.txt 2>/dev/null

EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
  echo ""
  echo "❌ 检测到 HIGH 级别关键字参数冲突风险，提交已阻断"
  echo ""
  echo "详细信息:"
  cat /tmp/kwarg-scan-result.txt
  echo ""
  echo "修复方法:"
  echo "  1. 在 **kwargs 展开前过滤保留键:"
  echo "     _RESERVED = {\"param1\", \"param2\"}"
  echo "     safe_kwargs = {k: v for k, v in kwargs.items() if k not in _RESERVED}"
  echo "     func(explicit_param=value, **safe_kwargs)"
  echo ""
  echo "  2. 使用 safe_ 前缀命名过滤变量，扫描器会自动识别"
  echo ""
  echo "跳过检查(不推荐): git commit --no-verify"
  exit 1
fi

echo "✓ HIGH 风险扫描通过（0 处发现）"
exit 0
