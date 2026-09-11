"""消化流水线数据模型（TASK-S3-01 / v7.2 §4.5 SkillFactory 流水线）

术语与作用域（消费 S0-02 三层盘点裁定）：

- **轨迹（Trajectory）** = 一次任务执行的**能力序列**（一条任务级 Trace + 其全部
  能力级子 Trace，经 `parent_trace_id` 还原；S2-01 `UnifiedTraceStore.chain()`）。
- **同类轨迹（TraceSet）** = 判定键 `SameTaskKey` 相同的轨迹集合；判定键三元组为
  ``capability_id + intent_key + outcome``（§4.5 / T3 修正要求"同类判定键定义明确"）。
- **候选模式（CandidatePattern）** = LCS 步骤骨架 + 参数槽 + 决策树分支条件。
- **SKILL.md 草稿（SkillDraft）** = 候选模式编译的 **draft 态**产物（不自动发布）。

七态 stage 迁移的证据模型见 `StageMigration`（`borrowed → mirrored` 由本任务驱动，
`shadow`/`internalized` 等的门控归 S3-02/S3-03，此处只登记证据与建议）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ════════════════════════════════════════════════════════════
#  常量（门槛/口径单点定义，供文档与用例共同引用）
# ════════════════════════════════════════════════════════════

#: §4.5 模式挖掘样本门槛：≥20 条**同类**（清洗后计数）
MIN_SAME_KIND_TRACES = 20
#: 步骤骨架最短长度（单步骨架不成 Skill，标记为过浅而非静默产出）
MIN_PATTERN_STEPS = 2
#: 骨架步骤的最小支撑率（出现在多少比例的同类轨迹中才纳入骨架）
MIN_STEP_SUPPORT = 0.6
#: 决策树最大深度 / 叶子最小样本数
TREE_MAX_DEPTH = 3
TREE_MIN_SAMPLES = 2

#: outcome 取值（一类轨迹只可能属于其一）
OUTCOME_SUCCESS = "success"
OUTCOME_FAILURE = "failure"

#: 清洗噪声标记
NOISE_RETRY_PREFIX = "retry_prefix"
NOISE_EXPLORE_PREFIX = "explore_prefix"
NOISE_DUPLICATE_STEP = "duplicate_step_merged"
NOISE_NEGATIVE_SAMPLE = "negative_sample"
NOISE_UNPARAMETERIZED = "unparameterized_value"

#: stage 迁移裁决
VERDICT_APPLIED = "applied"
VERDICT_INSUFFICIENT_EVIDENCE = "insufficient_evidence"
VERDICT_NOT_FOUND = "not_found"
VERDICT_ILLEGAL_TRANSITION = "illegal_transition"
VERDICT_DEFERRED = "deferred_to_downstream"
VERDICT_ERROR = "error"


# ════════════════════════════════════════════════════════════
#  同类轨迹判定键
# ════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SameTaskKey:
    """同类轨迹判定键（T3 修正落地）

    **三元组定义**（无自相矛盾，逐项可判定）：

    1. ``capability_id``：轨迹所归属的 **canonical capability_id**（S3-01 L1 改写后
       的键；历史以工具名落账的行在读取时经 `capability.py` 归一到同一键）。
    2. ``intent_key``：**任务意图类别**归一（非任务身份）——默认取该轨迹触发能力的
       请求**参数形态指纹**（顶层键集合 + 一层嵌套键），故"同一类操作"必同键，
       而"同任务的不同执行路径"（步骤顺序/多寡不同）不改变本键 ⇒ 可归并。
       调用方亦可显式传入意图文本，经 `cleaning.normalize_intent()` 归一。
    3. ``outcome``：结果状态归一（``success`` / ``failure``）——成功与失败**分属
       不同键**，失败轨迹不混入成功骨架（失败轨迹仍保留为负样本，供 S5 评测与
       negative_intent 使用）。

    **不含** ``task_id``：任务身份不是"同类"的依据（否则 20 条门槛永远达不到）；
    任务身份保留在 `Trajectory.task_id` 供溯源。
    """

    capability_id: str
    intent_key: str
    outcome: str

    def as_tuple(self) -> Tuple[str, str, str]:
        return (self.capability_id, self.intent_key, self.outcome)

    def as_str(self) -> str:
        """稳定字符串（供 dict 键/事件载荷/审计 subject）"""
        return f"{self.capability_id}|{self.intent_key}|{self.outcome}"

    @property
    def is_negative(self) -> bool:
        return self.outcome != OUTCOME_SUCCESS


# ════════════════════════════════════════════════════════════
#  轨迹与轨迹集
# ════════════════════════════════════════════════════════════


@dataclass
class TrajectoryStep:
    """轨迹中的一步（= 一次能力调用，来源为一条能力级统一 Trace）"""

    seq: int
    label: str
    capability_id: str = ""
    trace_id: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    status: str = "success"
    error_code: str = ""
    duration_ms: float = 0.0
    condition: str = ""
    repeat: int = 1
    files_written: List[str] = field(default_factory=list)
    files_deleted: List[str] = field(default_factory=list)
    external_calls: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "success"


@dataclass
class Trajectory:
    """一条清洗后的轨迹（同类判定的**观测单元**）

    ``raw_step_count`` / ``dropped_steps`` / ``merged_steps`` 保留清洗前后的差异，
    使"清洗确实发生了"可被用例断言、被验收报告引用，而不是黑箱。
    """

    trajectory_id: str
    task_id: str
    key: SameTaskKey
    steps: List[TrajectoryStep] = field(default_factory=list)
    source_trace_id: str = ""
    raw_step_count: int = 0
    dropped_steps: int = 0
    merged_steps: int = 0
    is_negative: bool = False
    noise_flags: List[str] = field(default_factory=list)
    workspace_id: str = ""
    started_at: float = 0.0

    def __post_init__(self) -> None:
        """不变量：``is_negative`` 与判定键的 outcome 元**必须一致**

        轨迹的成败由 `SameTaskKey.outcome` 定义；此处把该不变量钉在构造点上，
        避免手工构造出的 Trajectory 与键自相矛盾（那会让"失败轨迹保留为负样本"
        这类断言在别处以难查的方式失效）。
        """
        self.is_negative = bool(self.is_negative or self.key.is_negative)

    @property
    def labels(self) -> List[str]:
        """步骤标签序列（LCS/骨架挖掘的输入）"""
        return [s.label for s in self.steps]

    @property
    def step_count(self) -> int:
        return len(self.steps)

    def flag(self, name: str) -> None:
        if name not in self.noise_flags:
            self.noise_flags.append(name)


@dataclass
class TraceSet:
    """同类轨迹集合（判定键相同的轨迹）"""

    key: SameTaskKey
    trajectories: List[Trajectory] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.trajectories)

    @property
    def task_ids(self) -> List[str]:
        return [t.task_id for t in self.trajectories]

    @property
    def negative_count(self) -> int:
        return sum(1 for t in self.trajectories if t.is_negative)

    def labels_matrix(self) -> List[List[str]]:
        """全部轨迹的标签序列（确定性顺序：按 trajectory_id 排序）"""
        return [t.labels for t in sorted(self.trajectories,
                                         key=lambda x: x.trajectory_id)]


# ════════════════════════════════════════════════════════════
#  模式
# ════════════════════════════════════════════════════════════


@dataclass
class ParameterSlot:
    """参数槽：跨同类轨迹**取值有差异**的键 → 具名占位符 ``${name}``

    占位符语法沿用云枢既有唯一实现（`workflow_learning.learner._templatize_params`
    / `executor._REF_RE`：``${name}`` / ``$input`` / ``$prev_output``），不新造方言。
    """

    name: str
    placeholder: str
    step_label: str = ""
    sample_count: int = 0
    distinct_values: int = 0
    examples: List[str] = field(default_factory=list)


@dataclass
class PatternStep:
    """步骤骨架中的一步（label 为归一动作标签，params 为参数槽/字面量）"""

    seq: int
    label: str
    support: float = 0.0
    optional: bool = False
    params: Dict[str, Any] = field(default_factory=dict)
    capability_id: str = ""
    condition: str = ""
    samples: int = 0


@dataclass
class BranchCondition:
    """决策树提取的分支条件（``at_step`` 为骨架位置；-1 表示"整条轨迹级"特征）"""

    at_step: int
    condition: str
    support: int
    total: int
    outcome: str
    advice: str = ""

    @property
    def support_ratio(self) -> float:
        return round(self.support / self.total, 4) if self.total else 0.0


@dataclass
class CandidatePattern:
    """候选模式：步骤骨架 + 参数槽 + 分支条件（§4.5 双路挖掘产物）"""

    pattern_id: str
    key: SameTaskKey
    steps: List[PatternStep] = field(default_factory=list)
    slots: List[ParameterSlot] = field(default_factory=list)
    branches: List[BranchCondition] = field(default_factory=list)
    support: int = 0
    sample_size: int = 0
    coverage: float = 0.0
    lcs_length: int = 0
    confidence: float = 0.0
    side_effect_profile: Dict[str, Any] = field(default_factory=dict)
    negative_samples: int = 0
    method: str = "lcs+decision_tree"

    @property
    def is_shallow(self) -> bool:
        return len(self.steps) < MIN_PATTERN_STEPS


# ════════════════════════════════════════════════════════════
#  产物与迁移
# ════════════════════════════════════════════════════════════


@dataclass
class SkillDraft:
    """SKILL.md **草稿**（draft 态；落盘只到草稿暂存区，不发布、不越过审批）"""

    skill_id: str
    name: str
    description: str
    markdown: str
    pattern_id: str
    status: str = "draft"
    tags: List[str] = field(default_factory=list)
    front_matter: Dict[str, Any] = field(default_factory=dict)
    path: str = ""
    source_track: str = "digestion"


@dataclass
class StageMigration:
    """一次 stage 迁移尝试的完整结果（含**未推进**的情形，绝不静默）"""

    capability_id: str
    from_stage: Optional[str]
    to_stage: Optional[str]
    applied: bool = False
    verdict: str = VERDICT_INSUFFICIENT_EVIDENCE
    reasons: List[str] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)
    digest_run_id: str = ""
    event_id: str = ""
    audit_seq: int = 0
    audit_hash: str = ""
    audit_action: str = ""
    audit_reason: str = ""
    trace_policy: str = ""

    @property
    def ok(self) -> bool:
        return self.applied


@dataclass
class DigestionReport:
    """`DigestionService.pipeline()` 的统一产出"""

    capability_id: str
    digest_run_id: str
    threshold: int = MIN_SAME_KIND_TRACES
    rows_total: int = 0
    trajectories_total: int = 0
    trajectories_cleaned: int = 0
    negative_total: int = 0
    intent_keys: List[str] = field(default_factory=list)
    trace_sets: List[Dict[str, Any]] = field(default_factory=list)
    eligible: bool = False
    reason: str = ""
    candidates: List[CandidatePattern] = field(default_factory=list)
    drafts: List[SkillDraft] = field(default_factory=list)
    stage_recommendation: Dict[str, Any] = field(default_factory=dict)
    stage_migration: Optional[StageMigration] = None
    events: List[str] = field(default_factory=list)
    cleanup: Dict[str, Any] = field(default_factory=dict)
    capability_resolution: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        """可 JSON 序列化的摘要（不含轨迹原文，只含标识/标签/计数）"""
        return {
            "capability_id": self.capability_id,
            "digest_run_id": self.digest_run_id,
            "threshold": self.threshold,
            "rows_total": self.rows_total,
            "trajectories_total": self.trajectories_total,
            "trajectories_cleaned": self.trajectories_cleaned,
            "negative_total": self.negative_total,
            "intent_keys": list(self.intent_keys),
            "trace_sets": list(self.trace_sets),
            "eligible": self.eligible,
            "reason": self.reason,
            "candidates": [
                {
                    "pattern_id": c.pattern_id,
                    "key": c.key.as_str(),
                    "steps": [{"seq": s.seq, "label": s.label,
                               "support": s.support, "optional": s.optional}
                              for s in c.steps],
                    "slots": [s.placeholder for s in c.slots],
                    "branches": [b.condition for b in c.branches],
                    "support": c.support,
                    "sample_size": c.sample_size,
                    "coverage": c.coverage,
                    "lcs_length": c.lcs_length,
                    "confidence": c.confidence,
                    "method": c.method,
                }
                for c in self.candidates
            ],
            "drafts": [
                {"skill_id": d.skill_id, "status": d.status, "path": d.path,
                 "pattern_id": d.pattern_id, "tags": list(d.tags)}
                for d in self.drafts
            ],
            "stage_recommendation": dict(self.stage_recommendation),
            "stage_migration": (
                None if self.stage_migration is None else {
                    "capability_id": self.stage_migration.capability_id,
                    "from": self.stage_migration.from_stage,
                    "to": self.stage_migration.to_stage,
                    "applied": self.stage_migration.applied,
                    "verdict": self.stage_migration.verdict,
                    "reasons": list(self.stage_migration.reasons),
                    "digest_run_id": self.stage_migration.digest_run_id,
                    "event_id": self.stage_migration.event_id,
                    "audit_seq": self.stage_migration.audit_seq,
                    "audit_action": self.stage_migration.audit_action,
                    "audit_reason": self.stage_migration.audit_reason,
                    "trace_policy": self.stage_migration.trace_policy,
                }),
            "events": list(self.events),
            "cleanup": dict(self.cleanup),
            "capability_resolution": dict(self.capability_resolution),
        }


__all__ = [
    "MIN_SAME_KIND_TRACES", "MIN_PATTERN_STEPS", "MIN_STEP_SUPPORT",
    "TREE_MAX_DEPTH", "TREE_MIN_SAMPLES",
    "OUTCOME_SUCCESS", "OUTCOME_FAILURE",
    "NOISE_RETRY_PREFIX", "NOISE_EXPLORE_PREFIX", "NOISE_DUPLICATE_STEP",
    "NOISE_NEGATIVE_SAMPLE", "NOISE_UNPARAMETERIZED",
    "VERDICT_APPLIED", "VERDICT_INSUFFICIENT_EVIDENCE", "VERDICT_NOT_FOUND",
    "VERDICT_ILLEGAL_TRANSITION", "VERDICT_DEFERRED", "VERDICT_ERROR",
    "SameTaskKey", "TrajectoryStep", "Trajectory", "TraceSet",
    "ParameterSlot", "PatternStep", "BranchCondition", "CandidatePattern",
    "SkillDraft", "StageMigration", "DigestionReport",
]
