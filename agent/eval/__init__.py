"""`agent.eval` —— v7.2 §6.5 评测分层（L0–L3）与 §6.7 指标字典（TASK-S5-02）

模块地图：

============================  ==========================================================
模块                           职责
============================  ==========================================================
`agent.eval.cases`            用例数据契约（schema `eval.cases.v1`）+ 哈希 + 校验
`agent.eval.checkers`         判定器（机械可验优先；代理判定器须显式降级）
`agent.eval.anchor`           **L0 锚**：独立只读存储 + 哈希锚定 + 写入守门（系统不可写）
`agent.eval.solvers`          被测解算器（reference/static/mutant/null）
`agent.eval.runner`           `run_l0()` 等分层执行器 + 基线对照 + L3 框架契约
`agent.eval.baseline`         L2 Core-50 基线（UTC + shadow 真实墙钟 p99 + 样本充分性）
`agent.eval.metrics`          §6.7 指标字典与周报计算（**复用** acr.py / utc.py，不另建计数）
`agent.eval.calibration`      S2-03 遗留 #3：ACR 难度启发式 → 真实信号的**切换路径**（默认关闭）
============================  ==========================================================

包导入**保持轻量**：只在 `__init__` 里导出契约与执行器的核心名字；指标/基线/校准
三个模块按需导入（它们会触及可观测性与消化子系统）。
"""

from __future__ import annotations

from agent.eval.anchor import (
    ANCHOR_SCHEMA,
    AnchorError,
    AnchorIndependenceError,
    AnchorIntegrityError,
    AnchorManifest,
    AnchorReadOnlyError,
    AnchorStore,
    assert_independent_of_system_data,
    freeze_anchor,
    guard_write,
    verify_anchor,
)
from agent.eval.cases import (
    LAYER_L0,
    LAYER_L1,
    LAYER_L2,
    LAYER_L3,
    LAYERS,
    SCENARIOS,
    SCHEMA_NAME,
    SEED_SCENARIOS,
    VERDICT_MECHANICAL,
    VERDICT_PROXY,
    VERDICT_UNSUPPORTED,
    CaseError,
    CaseSchemaError,
    CaseSetError,
    EvalCase,
    EvalCaseSet,
    caseset_digest,
    load_case_set,
    validate_case_set,
)
from agent.eval.checkers import MECHANICAL_CHECKERS, PROXY_CHECKERS, run_check
from agent.eval.runner import (
    CLOCK_WALL,
    EvalReport,
    l3_framework,
    run_l0,
    run_l1,
    run_l2,
    run_l3,
    run_layer,
)
from agent.eval.solvers import (
    mutant_solver,
    null_solver,
    reference_solver,
    solver_from_spec,
)

__all__ = [
    "ANCHOR_SCHEMA", "AnchorError", "AnchorIndependenceError",
    "AnchorIntegrityError", "AnchorManifest", "AnchorReadOnlyError", "AnchorStore",
    "assert_independent_of_system_data", "freeze_anchor", "guard_write",
    "verify_anchor",
    "LAYERS", "LAYER_L0", "LAYER_L1", "LAYER_L2", "LAYER_L3", "SCENARIOS",
    "SEED_SCENARIOS", "SCHEMA_NAME", "VERDICT_MECHANICAL", "VERDICT_PROXY",
    "VERDICT_UNSUPPORTED", "CaseError", "CaseSchemaError", "CaseSetError",
    "EvalCase", "EvalCaseSet", "caseset_digest", "load_case_set",
    "validate_case_set",
    "MECHANICAL_CHECKERS", "PROXY_CHECKERS", "run_check",
    "CLOCK_WALL", "EvalReport", "l3_framework", "run_l0", "run_l1", "run_l2",
    "run_l3", "run_layer",
    "mutant_solver", "null_solver", "reference_solver", "solver_from_spec",
]
