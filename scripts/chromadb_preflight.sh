#!/usr/bin/env bash
# ChromaDB 导入降级预检（可复用工具包 · CI/Linux 版）
#
# 统一入口为 python -m agent.preflight（agent/preflight/ 包，单事实源）。
# 两道防线：
#   1) CLI：12 条导入路径（含 30s 子进程超时降级），python -m 统一入口
#   2) pytest 用例：分支级（test_memory_optimized_import.py，14 用例）
#      + 整体级（test_preflight_runner.py，复用 runner）
# 任一步失败即非零退出（CI 中 unit-tests 的 needs 依赖阻断）。
#
# 用法：
#   bash scripts/chromadb_preflight.sh           # 正常预检
#   PREFLIGHT_FAKE_FAIL=1 bash scripts/chromadb_preflight.sh
#     # 故障演练：环境变量开关（任意非空值触发），模拟预检失败（验证 CI 阻断）

set -euo pipefail

if [[ -n "${PREFLIGHT_FAKE_FAIL:-}" ]]; then
  echo "== 故障演练：PREFLIGHT_FAKE_FAIL 已设置，模拟预检失败（CI 中 unit-tests 将被 needs 阻断跳过）==" >&2
  exit 1
fi

echo "=== 1/2 python -m agent.preflight（12 条导入路径）==="
python -m agent.preflight

echo "=== 2/2 pytest 用例（test_memory_optimized_import + test_preflight_runner）==="
python -m pytest tests/unit/test_memory_optimized_import.py tests/unit/test_preflight_runner.py -q -p no:cacheprovider --no-header

echo "=== ChromaDB 导入降级预检通过 ==="
