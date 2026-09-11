"""消化流水线（TASK-S3-01 / S3-02 / v7.2 §4.5 SkillFactory 流水线）

把 S2 数据地基（统一 Trace 台账 / 链式审计 / events.v1 事件流）接到**唯一的**
固化通道上：``轨迹台账 → 清洗 → 模式挖掘 → SKILL.md 草稿 → 判定集与回放沙箱
→ 验收门 → stage 迁移``。

模块清单：

| 模块 | 职责 | 归属 |
|---|---|---|
| `models` | 数据模型与门槛常量（同类判定键 / 候选模式 / 草稿 / 迁移结果 / 报告） | S3-01 |
| `capability` | 入口侧能力键归一（L1：工具名 ↔ canonical capability_id，含历史行） | S3-01 |
| `cleaning` | 轨迹清洗四规则 + 同类轨迹判定键 | S3-01 |
| `generalize` | 参数泛化（形态占位符 + 具名参数槽，沿用 `${name}` 既有方言） | S3-01 |
| `mining` | LCS 步骤骨架 + 决策树分支条件 + 副作用画像 | S3-01 |
| `generation` | 候选模式 → draft 态 SKILL.md（桥接 `process_distill`/`skills_mgmt`） | S3-01 |
| `stage` | stage 迁移门与执行（descriptor + 审计 + `digest.stage` 三处联动） | S3-01（S3-02 增补通行证放行） |
| `service` | `DigestionService.pipeline()` 端到端门面 | S3-01 |
| `cases` | **EquivalenceCase 判定集资产**（模型 + 独立存储 + 三通道生成 + Seed Pack） | S3-02 |
| `sandbox` | **确定性回放沙箱**（双跑 + record-and-replay + 三层比对 + 配额） | S3-02 |
| `gate` | **验收门四条件硬闸** + 通行证 + **漂移重探** | S3-02 |
| `seed_pack.json` | P7.2-23 Seed Pack 资产（14 技能 × ≥3 组预置等价用例） | S3-02 |

**import 纪律**：本包是**叶子**——既有模块不得反向导入 `agent.digestion`
（否则与 `descriptors.bridge → skills_mgmt` 诸链构成环）。重依赖（`agent.tools`、
`agent.skills_mgmt`、`agent.workflow_learning`、`agent.process_distill.solidify`、
`agent.observability.trace_v2`、`agent.audit.facade`）一律在**函数体内**懒加载，
故 `import agent.digestion` 无文件/DB/网络副作用。

**范围边界**（不越权）：shadow 灰度与内化（S3-03）；发布签名与人工 review =
既有 `skills_mgmt` 轨。本包产物一律停在 **draft**；判定集与通行证是**证据资产**，
不是发布动作。
"""

from __future__ import annotations

from .cases import (  # noqa: F401
    CASE_KIND_LLM,
    CASE_KIND_MANUAL,
    CASE_KIND_SEED,
    CASE_KIND_TRACE,
    MAX_CASE_SET_SIZE,
    MIN_CASE_SET_SIZE,
    MIN_SEED_CASES_PER_SKILL,
    SEED_PACK_MIN_SKILLS,
    CaseSet,
    CaseStore,
    CaseValidationError,
    EquivalenceCase,
    JsonCaseStore,
    ProgramStep,
    SqliteCaseStore,
    build_case_set,
    case_set_for_capability,
    cases_from_trace_set,
    load_seed_pack,
    open_case_store,
    regenerate_case_set,
    seed_candidate_for,
    seed_cases_for,
    seed_pack_case_sets,
    seed_pack_summary,
    trace_set_for,
)
from .gate import (  # noqa: F401
    GATE_P99_RATIO,
    GATE_REPLAY_MIN,
    GATE_SUCCESS_RATE_RATIO,
    GATE_VERSION,
    DriftReport,
    GateResult,
    PassportStore,
    acceptance_gate,
    advance_to_shadow,
    baseline_from_ledger,
    branch_coverage,
    due_for_reprobe,
    register_reprobe_job,
    reprobe,
)
from .models import (  # noqa: F401
    MIN_PATTERN_STEPS,
    MIN_SAME_KIND_TRACES,
    MIN_STEP_SUPPORT,
    BranchCondition,
    CandidatePattern,
    DigestionReport,
    SameTaskKey,
    SkillDraft,
    StageMigration,
    TraceSet,
    Trajectory,
    TrajectoryStep,
)
from .sandbox import (  # noqa: F401
    JUDGE_THRESHOLD,
    MANUAL_SAMPLE_RATIO,
    CaseReplay,
    DiffResult,
    Observation,
    RecordReplayJournal,
    ReplayEnv,
    ReplayReport,
    ReplaySandbox,
    SandboxQuota,
    three_layer_diff,
)
from .service import DigestionService  # noqa: F401

__all__ = [
    # 门面
    "DigestionService",
    # S3-01 模型
    "SameTaskKey", "Trajectory", "TrajectoryStep", "TraceSet",
    "CandidatePattern", "BranchCondition", "SkillDraft", "StageMigration",
    "DigestionReport",
    "MIN_SAME_KIND_TRACES", "MIN_PATTERN_STEPS", "MIN_STEP_SUPPORT",
    # S3-02 判定集
    "EquivalenceCase", "CaseSet", "ProgramStep", "CaseStore", "JsonCaseStore",
    "SqliteCaseStore", "CaseValidationError", "open_case_store",
    "build_case_set", "cases_from_trace_set", "trace_set_for",
    "case_set_for_capability", "regenerate_case_set", "load_seed_pack",
    "seed_cases_for", "seed_candidate_for", "seed_pack_case_sets",
    "seed_pack_summary",
    "CASE_KIND_SEED", "CASE_KIND_TRACE", "CASE_KIND_LLM", "CASE_KIND_MANUAL",
    "MIN_CASE_SET_SIZE", "MAX_CASE_SET_SIZE", "SEED_PACK_MIN_SKILLS",
    "MIN_SEED_CASES_PER_SKILL",
    # S3-02 回放沙箱
    "ReplaySandbox", "ReplayEnv", "ReplayReport", "CaseReplay", "Observation",
    "DiffResult", "three_layer_diff", "SandboxQuota", "RecordReplayJournal",
    "JUDGE_THRESHOLD", "MANUAL_SAMPLE_RATIO",
    # S3-02 验收门与漂移
    "acceptance_gate", "advance_to_shadow", "branch_coverage", "GateResult",
    "PassportStore", "reprobe", "DriftReport", "due_for_reprobe",
    "register_reprobe_job", "baseline_from_ledger",
    "GATE_VERSION", "GATE_REPLAY_MIN", "GATE_SUCCESS_RATE_RATIO",
    "GATE_P99_RATIO",
]
