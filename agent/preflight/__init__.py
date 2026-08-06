"""预检工具包 — 守护 memory_optimized 导入降级逻辑（统一 CLI 入口）

单事实源（不易）：本包是 demo 脚本逻辑的唯一归属。原 scripts/demo_memory_optimized_import.py
已删除，其 12 条路径检查迁移至 runner.py；pytest（test_memory_optimized_import.py 分支级 +
test_preflight_runner.py 整体级）与 CLI（python -m agent.preflight）复用同一实现，消除重复。

用法：
    python -m agent.preflight                  # 12 条路径检查，全过 exit 0
    python -m agent.preflight --verbose        # 附带决策日志（logging INFO）
    PREFLIGHT_FAKE_FAIL=1 python -m agent.preflight  # 故障演练（CI 阻断验证），exit 1

依赖：仅标准库 + agent.memory_optimized（其依赖 agent.logging_utils 亦为标准库），
因此可在无 chromadb/torch 的轻量容器中运行（见项目根 Dockerfile）。
"""

from agent.preflight.runner import CheckResult, run_preflight

__all__ = ["CheckResult", "run_preflight"]
