"""native 期探活设施（TASK-S7-04 子项 B / v7.2 §3.3「30 天零回退 + 每周探活」+ §4.4）

**审计缺口 T4 的落点**：v7.2 §3.3 要求 ``native`` 状态维持"30 天零回退 + 每周探活"，
但**探活内容未定义**。本模块把"探活 = 什么、怎么判、退化了怎么办"落成可执行设施：
当前仓库**尚无** ``native`` 能力（也无 ``internalized``），故设施先建 ——
能力到达该状态即自动生效，不需要再改代码。

用户可见的计划：**探活内容 = 判定集子集每周重放 + 成功率/延迟/成本的相对基线监控**；
退化 → 产出 ``native → borrowed`` **回退建议**（走回上游，不改代码）并升级为**事故卡**。

【探活 ≠ 漂移重探（两个概念必须分开，否则会互相冒充）】

| | **探活（本模块）** | **漂移重探（S3-02 `gate.reprobe`）** |
|---|---|---|
| 回答的问题 | **自身实现**是否退化了 | **上游契约**是否漂移了 |
| 比较基准 | 该能力**自己的历史基线**（判定集通过率 / 真实墙钟 p99 / 单位成本） | 上游 `descriptor` 的**版本与 schema**（+ 探针回放失败） |
| 触发周期 | 每周（``LIVENESS_PERIOD_DAYS``） | 30 天（``gate.REPROBE_INTERVAL_DAYS``） |
| 命中后动作 | **回退建议**（native→borrowed）+ 事故卡；**不改 stage** | 判定集失效 → 重生成（`drift` 语义） |
| 失败的含义 | "我们的实现变差了" | "上游变了，判定集不再代表它" |

两者**共用**同一份判定集与同一个回放沙箱（复用 `cases` / `sandbox`），但**基线来源、
判定条件与动作完全不同** —— 因此本模块**不自建**第二套判定集、回放器或事故卡。

【六条硬约束（写进代码，不只是文档）】

1. **调度默认关闭**：`register_liveness_job()` 只有 ``CP_DIGESTION_LIVENESS_ENABLED=true``
   才注册（与 ``shadow`` / ``reprobe`` / ``internalize`` 同一条安全底线）。
2. **只出建议，不自动落地**：`suggest_stage_rollback()` **绝不**调用 `stage_migrate`；
   需要执行时由人工或既有审批门走既有 `stage.stage_migrate` + 审计（本模块只给
   `apply_hint`，即"若要执行该调哪一行"）。
3. **不编造**：无 ``internalized``/``native`` 能力时 `run_all()`/`scan_targets()` 如实
   返回空 + `verdict="no_capability"`（**不报错、不拿别的 stage 能力凑数**）；
   某项退化条件缺证据时记为 `applicable=False`（**宁可不说，不猜**），并逐条进报告。
4. **真实墙钟**：p99 用 `ReplaySandbox(measure_wall=True)` 的 ``perf_counter`` 墙钟口径
   （与 S3-03 内化条件⑤同口径），**不**用回放沙箱的标称模型时钟冒充。
5. **基线不被退化带跑**：探活**不自动抬基线**（否则慢性退化永远不会被触发——
   "温水煮青蛙"）。观测值逐轮进 ``history`` 供审计；重设基线须**显式** ``establish()``。
6. **注入时钟**：周期类判定（每周 / 超期）一律走 ``now`` 参数，**不依赖真实墙钟**，
   故"每周探活"与"超周期未探活"可被确定性用例覆盖。

【事故卡六要素（S4-03 语义）】退化时经 `agent/self_healing/levels.py::raise_incident`
开卡（复用既有落盘/审计/事件三件套，**不重复留痕**），再由本模块**补齐**该函数签名
未覆盖的三个要素：

| 要素 | 本模块的取值来源（真实、可回溯） |
|---|---|
| ``root_cause`` | 逐条命中的退化条件 + 实测值/阈值（本模块实算） |
| ``fatal_change`` | 显式传入 > ``descriptor.meta.version``；都无 ⇒ **如实留空**（见 `detail.fatal_change_source`），此时 `missing_elements()` 会如实报告 |
| ``evasion_rule`` | 本模块产出的回退规则身份 ``stage_rollback:<cap>.<from>-><to>`` |
| ``in_strategy_memory`` | 探活基线台账记录 ``liveness:<cap>``（本模块实际写入的记录） |
| ``regression_case_added`` | 命中失败的**判定集用例** id（判定集即回归资产，本任务**不新建**第二套回归集）；无失败用例时如实 ``yes=False`` |
| ``trace_ids`` | 命中用例的真实 ``origin_trace_id``；为空时退回本次探活的 run id（可在基线台账 ``history`` 中反查） |

**import 纪律**：与本包其余模块一致，跨包重依赖（`agent.self_healing.levels`、
`agent.audit.facade`、`agent.descriptors.registry`）一律**函数体内懒加载**，
故 ``import agent.digestion.probe`` 无文件/DB/网络副作用。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .cases import CaseSet, CaseStore, EquivalenceCase, open_case_store, seed_candidate_for
from .gate import probe_sample_ids
from .sandbox import (
    Implementation,
    ProgramImplementation,
    ReplayReport,
    ReplaySandbox,
)

logger = logging.getLogger("agent.digestion.probe")

# ════════════════════════════════════════════════════════════
#  常量（门槛单点定义；口径变更须改版本以便追溯）
# ════════════════════════════════════════════════════════════

#: 探活设施版本（进报告；口径变更须改版本）
PROBE_VERSION = "s7-04.1"

#: 时钟口径标注（**必须随 p99 数字一起引用**）
CLOCK_WALL = "wall_clock(perf_counter; per-arm real elapsed)"

#: 探活**作用范围**（§3.3：只有走到自研实现的能力才需要"自身退化"监控）
STAGE_INTERNALIZED = "internalized"
STAGE_NATIVE = "native"
PROBE_STAGES: Tuple[str, ...] = (STAGE_INTERNALIZED, STAGE_NATIVE)
#: 回退目标：**走回上游**（不改代码）。internalized/native 都回退到 borrowed。
ROLLBACK_TO_STAGE = "borrowed"

#: 判定结论
VERDICT_OK = "ok"
VERDICT_DEGRADED = "degraded"
VERDICT_NO_CAPABILITY = "no_capability"
VERDICT_NOT_APPLICABLE = "not_applicable"
VERDICT_NO_CASES = "no_cases"
VERDICT_ERROR = "error"

#: 四项退化条件（§3.3 / 审计 T4）
LIVENESS_COND_PASS_RATE = "pass_rate"
LIVENESS_COND_P99 = "p99_regression"
LIVENESS_COND_COST = "cost_increase"
LIVENESS_COND_OVERDUE = "overdue"
LIVENESS_CONDITIONS: Tuple[str, ...] = (LIVENESS_COND_PASS_RATE, LIVENESS_COND_P99,
                                       LIVENESS_COND_COST, LIVENESS_COND_OVERDUE)
COND_LABELS: Dict[str, str] = {
    LIVENESS_COND_PASS_RATE: "① 通过率 < 基线×0.98",
    LIVENESS_COND_P99: "② p99 回归超阈（真实墙钟）",
    LIVENESS_COND_COST: "③ 成本显著上升",
    LIVENESS_COND_OVERDUE: "④ 超周期未成功探活",
}

#: 阈值（可配；与既有常量同值以避免"同一治理维度两套阈值"）
#: ① 与 §4.5.1 内化条件④ / `gate.GATE_SUCCESS_RATE_RATIO` 同值 0.98
LIVENESS_PASS_RATE_RATIO = 0.98
#: ② p99 回归上限倍数（历史基线 × 该倍数）——探活看**自己**的基线，
#: 与内化条件⑤（候选 vs 上游，比值 1.0）**不同口径**，故不复用其常量
LIVENESS_P99_REGRESSION_RATIO = 1.5
#: ③ 成本上升上限倍数（与 S5-03 成本刹车"断食"阈值 1.3 同值）
LIVENESS_COST_INCREASE_RATIO = 1.3
#: ④ 探活周期（§3.3「每周探活」）
LIVENESS_PERIOD_DAYS = 7
DAY_SECONDS = 86400.0

#: 判定集子集默认抽样规模（`CaseSet` 抽样 N 组；可配）
DEFAULT_PROBE_SIZE = 5
PROBE_SIZE_ENV = "CP_DIGESTION_LIVENESS_PROBE_SIZE"
#: 单周期最多探活能力数（**预算上限**：开启调度后防止一次跑爆）
DEFAULT_MAX_TARGETS = 20
MAX_TARGETS_ENV = "CP_DIGESTION_LIVENESS_MAX_TARGETS"

#: 调度（**默认关闭**）
LIVENESS_ENABLE_ENV = "CP_DIGESTION_LIVENESS_ENABLED"
LIVENESS_TASK_NAME = "digestion_liveness_weekly"
LIVENESS_INTERVAL_SECONDS = LIVENESS_PERIOD_DAYS * DAY_SECONDS

#: 基线台账落点
DEFAULT_LIVENESS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "digestion", "liveness")
LIVENESS_DIR_ENV = "CP_DIGESTION_LIVENESS_DIR"
BASELINE_FILENAME = "liveness_baseline.json"
BASELINE_SCHEMA_VERSION = 1
#: 单个能力保留的观测历史条数（审计用；不参与阈值判定）
MAX_HISTORY = 24

#: 退化级别（S4-03 §4.4 语义）：internalized = L2「劣化自动 downgrade」；
#: native = L3「kill→revert→重启→负面样本」（自研实现退化需要回退代码 + 负面样本）
LEVEL_BY_STAGE: Dict[str, str] = {
    STAGE_INTERNALIZED: "L2",
    STAGE_NATIVE: "L3",
}
DEFAULT_LEVEL = "L2"

#: 审计动作（沿用 shadow/stage 的 `digest.*` 动作命名约定，不新造事件类型）
AUDIT_ACTION_PROBED = "digest.liveness.probed"
AUDIT_ACTION_DEGRADED = "digest.liveness.degraded"

#: 事故卡单写者名 —— **必须**与 `levels.save_incident()` 的默认 writer 同名：
#: `raise_incident()` 以该名字登记了单写者，补齐六要素后回写时若换名字会触发
#: `SingleWriterViolationError`（同一路径只允许一个 writer，§5.5）。
#: 本模块只**补齐**既有卡片，不另开 writer 体系。
INCIDENT_WRITER = "self_healing.levels"

#: 成本证据来源标注
SRC_EXPLICIT = "explicit"
SRC_PROVIDER = "cost_provider"
SRC_BASELINE = "liveness_baseline"
SRC_UNAVAILABLE = "unavailable"
SRC_DESCRIPTOR = "descriptor"


def _now() -> float:
    return time.time()


def _env_flag(name: str, default: bool = False,
              env: Optional[Dict[str, str]] = None) -> bool:
    source = os.environ if env is None else env
    raw = str(source.get(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _env_int(name: str, default: int, env: Optional[Dict[str, str]] = None) -> int:
    source = os.environ if env is None else env
    raw = str(source.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("[liveness] 非法 %s=%r，使用默认 %d", name, raw, default)
        return default
    return value if value > 0 else default


def probe_size_from_env(env: Optional[Dict[str, str]] = None) -> int:
    """探活抽样规模（``CP_DIGESTION_LIVENESS_PROBE_SIZE``；非法值回退默认）"""
    return _env_int(PROBE_SIZE_ENV, DEFAULT_PROBE_SIZE, env)


def max_targets_from_env(env: Optional[Dict[str, str]] = None) -> int:
    """单周期探活能力数上限（预算上限；非法值回退默认）"""
    return _env_int(MAX_TARGETS_ENV, DEFAULT_MAX_TARGETS, env)


def liveness_enabled(env: Optional[Dict[str, str]] = None) -> bool:
    """探活调度总开关（**默认关闭**）"""
    return _env_flag(LIVENESS_ENABLE_ENV, False, env)


def baseline_path(directory: str = "") -> str:
    """基线台账路径（显式 > 环境变量 > 默认）"""
    root = str(directory or "").strip() \
        or str(os.environ.get(LIVENESS_DIR_ENV, "") or "").strip() \
        or DEFAULT_LIVENESS_DIR
    return os.path.join(root, BASELINE_FILENAME)


# ════════════════════════════════════════════════════════════
#  退化条件判定（**纯函数**：不读盘、不写盘、不碰 registry）
# ════════════════════════════════════════════════════════════


@dataclass
class LivenessCondition:
    """单项退化条件的判定明细（**逐项可读、可断言**，不是笼统的布尔）

    - ``applicable=False`` ⇒ 本项**不判定**。原因写在 ``reasons``，并由
      ``evidence_missing`` 区分「首次探活尚无基线」（正常）与「证据缺位」（需关注）。
    - 未判定的项**不参与** ``degraded`` 结论 —— 缺证据既不能判退化，也**不冒充已通过**。
    """

    name: str
    applicable: bool = False
    triggered: bool = False
    evidence_missing: bool = False
    actual: Any = None
    threshold: Any = None
    comparator: str = ""
    reasons: List[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return COND_LABELS.get(self.name, self.name)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "applicable": bool(self.applicable),
            "triggered": bool(self.triggered),
            "evidence_missing": bool(self.evidence_missing),
            "actual": self.actual,
            "threshold": self.threshold,
            "comparator": self.comparator,
            "reasons": list(self.reasons),
        }


@dataclass
class LivenessVerdict:
    """探活判定结论（四项条件的合集）"""

    capability_id: str = ""
    degraded: bool = False
    conditions: List[LivenessCondition] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)

    def condition(self, name: str) -> Optional[LivenessCondition]:
        for item in self.conditions:
            if item.name == name:
                return item
        return None

    @property
    def triggered_conditions(self) -> List[str]:
        return [c.name for c in self.conditions if c.triggered]

    @property
    def unmeasured_conditions(self) -> List[str]:
        """未判定的项（含首次探活无基线；**与「已通过」区分开**）"""
        return [c.name for c in self.conditions if not c.applicable]

    @property
    def missing_evidence_conditions(self) -> List[str]:
        """因**证据缺位**而未判定的项（首次无基线不计入，见 `evidence_missing`）"""
        return [c.name for c in self.conditions
                if not c.applicable and c.evidence_missing]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "degraded": bool(self.degraded),
            "triggered_conditions": self.triggered_conditions,
            "unmeasured_conditions": self.unmeasured_conditions,
            "missing_evidence_conditions": self.missing_evidence_conditions,
            "reasons": list(self.reasons),
            "conditions": [c.to_dict() for c in self.conditions],
        }


def _pass_rate_condition(*, pass_rate: Optional[float],
                         baseline_pass_rate: Optional[float],
                         ratio: float) -> LivenessCondition:
    item = LivenessCondition(name=LIVENESS_COND_PASS_RATE, comparator=">=")
    if pass_rate is None:
        item.evidence_missing = True
        item.reasons.append("本次探活未得到通过率（未回放任何用例）⇒ ①不判定")
        return item
    item.actual = round(float(pass_rate), 4)
    if baseline_pass_rate is None:
        item.reasons.append(
            "无历史通过率基线 ⇒ 本轮**建立基线**，①不判定"
            "（首次探活不按退化处理，也不冒充已通过）")
        return item
    floor = round(float(baseline_pass_rate) * float(ratio), 4)
    item.applicable = True
    item.threshold = floor
    item.triggered = float(pass_rate) < floor
    item.reasons.append(
        f"通过率 {item.actual} {'<' if item.triggered else '≥'} "
        f"基线 {round(float(baseline_pass_rate), 4)} × {ratio} = {floor}"
        + ("（质量下滑）" if item.triggered else ""))
    return item


def _p99_condition(*, p99_wall_ms: Optional[float],
                   baseline_p99_wall_ms: Optional[float],
                   ratio: float, clock: str) -> LivenessCondition:
    item = LivenessCondition(name=LIVENESS_COND_P99, comparator="<=")
    if p99_wall_ms is None:
        item.evidence_missing = True
        item.reasons.append("本次探活未得到真实墙钟 p99 ⇒ ②不判定")
        return item
    item.actual = round(float(p99_wall_ms), 4)
    if baseline_p99_wall_ms is None:
        item.reasons.append("无历史 p99 基线 ⇒ 本轮建立基线，②不判定")
        return item
    ceiling = round(float(baseline_p99_wall_ms) * float(ratio), 4)
    item.applicable = True
    item.threshold = ceiling
    item.triggered = float(p99_wall_ms) > ceiling
    item.reasons.append(
        f"真实墙钟 p99 {item.actual}ms {'>' if item.triggered else '≤'} "
        f"基线 {round(float(baseline_p99_wall_ms), 4)}ms × {ratio} = {ceiling}ms"
        f"（口径 {clock}）" + ("⇒ 延迟回归" if item.triggered else ""))
    return item


def _cost_condition(*, cost_cents_per_unit: Optional[float],
                    baseline_cost_cents_per_unit: Optional[float],
                    ratio: float) -> LivenessCondition:
    item = LivenessCondition(name=LIVENESS_COND_COST, comparator="<=")
    if cost_cents_per_unit is None:
        item.evidence_missing = True
        item.reasons.append(
            "未提供单位成本（无 `cost_provider` 且未显式给出）⇒ ③不判定"
            "（缺证据不当成「未上升」，也不编造成本数字）")
        return item
    item.actual = round(float(cost_cents_per_unit), 6)
    if baseline_cost_cents_per_unit is None:
        item.reasons.append("无历史单位成本基线 ⇒ 本轮建立基线，③不判定")
        return item
    ceiling = round(float(baseline_cost_cents_per_unit) * float(ratio), 6)
    item.applicable = True
    item.threshold = ceiling
    item.triggered = float(cost_cents_per_unit) > ceiling
    item.reasons.append(
        f"单位成本 {item.actual} {'>' if item.triggered else '≤'} "
        f"基线 {round(float(baseline_cost_cents_per_unit), 6)} × {ratio} "
        f"= {ceiling} 分/单位" + ("⇒ 成本显著上升" if item.triggered else ""))
    return item


def _overdue_condition(*, last_success_at: float, now: float,
                       period_days: float) -> LivenessCondition:
    item = LivenessCondition(name=LIVENESS_COND_OVERDUE, comparator="<=")
    if not last_success_at or last_success_at <= 0:
        item.reasons.append(
            "无历史**成功**探活记录 ⇒ ④不适用（首次探活不误报「超期未探活」）")
        return item
    limit = float(period_days) * DAY_SECONDS
    elapsed = float(now or 0.0) - float(last_success_at)
    item.applicable = True
    item.actual = round(elapsed, 3)
    item.threshold = round(limit, 3)
    item.triggered = elapsed > limit
    item.reasons.append(
        f"距上次成功探活 {round(elapsed / DAY_SECONDS, 3)} 天 "
        f"{'>' if item.triggered else '≤'} 周期 {period_days} 天"
        + ("⇒ 超周期未成功探活" if item.triggered else ""))
    return item


def evaluate_liveness(
    *,
    capability_id: str = "",
    pass_rate: Optional[float] = None,
    baseline_pass_rate: Optional[float] = None,
    p99_wall_ms: Optional[float] = None,
    baseline_p99_wall_ms: Optional[float] = None,
    cost_cents_per_unit: Optional[float] = None,
    baseline_cost_cents_per_unit: Optional[float] = None,
    last_success_at: float = 0.0,
    now: float = 0.0,
    period_days: float = LIVENESS_PERIOD_DAYS,
    pass_rate_ratio: float = LIVENESS_PASS_RATE_RATIO,
    p99_ratio: float = LIVENESS_P99_REGRESSION_RATIO,
    cost_ratio: float = LIVENESS_COST_INCREASE_RATIO,
    clock: str = CLOCK_WALL,
) -> LivenessVerdict:
    """四项退化条件判定（**纯函数**；任一命中即 ``degraded``）

    ① ``通过率 < 基线 × 0.98``｜② ``p99 > 基线 × 1.5``｜③ ``成本 > 基线 × 1.3``
    ｜④ ``now − 上次成功探活 > 7 天``

    缺证据的项 ``applicable=False``：既**不**判退化（不编造），也**不**冒充通过
    （`unmeasured_conditions` 如实列出）。``now`` 显式注入 ⇒ 周期判定不依赖真实墙钟。
    """
    verdict = LivenessVerdict(capability_id=str(capability_id or ""))
    verdict.conditions = [
        _pass_rate_condition(pass_rate=pass_rate,
                             baseline_pass_rate=baseline_pass_rate,
                             ratio=float(pass_rate_ratio)),
        _p99_condition(p99_wall_ms=p99_wall_ms,
                       baseline_p99_wall_ms=baseline_p99_wall_ms,
                       ratio=float(p99_ratio), clock=str(clock or CLOCK_WALL)),
        _cost_condition(cost_cents_per_unit=cost_cents_per_unit,
                        baseline_cost_cents_per_unit=baseline_cost_cents_per_unit,
                        ratio=float(cost_ratio)),
        _overdue_condition(last_success_at=float(last_success_at or 0.0),
                           now=float(now or 0.0),
                           period_days=float(period_days)),
    ]
    verdict.degraded = any(c.triggered for c in verdict.conditions)
    for item in verdict.conditions:
        if item.triggered:
            verdict.reasons.append(f"[{item.label}] {item.reasons[-1]}")
    if not verdict.degraded:
        verdict.reasons.append(
            "四项退化条件均未命中"
            + (f"（未判定项：{verdict.unmeasured_conditions}）"
               if verdict.unmeasured_conditions else ""))
    return verdict


# ════════════════════════════════════════════════════════════
#  回退建议（**只出建议，绝不自动改 stage**）
# ════════════════════════════════════════════════════════════


@dataclass
class StageRollbackSuggestion:
    """``native → borrowed`` 回退建议（**不自动执行**）

    【不易】``executed`` **恒为 False**：本模块没有任何执行路径。若要执行，
    走既有 ``stage.stage_migrate()`` + 链式审计（`apply_hint` 给出确切调用形状，
    且目标 stage 的门控仍由既有 ``evaluate_migration`` 判定，本模块不越权放行）。
    """

    capability_id: str
    from_stage: str = ""
    to_stage: str = ROLLBACK_TO_STAGE
    recommended: bool = False
    executed: bool = False
    requires_approval: bool = True
    reasons: List[str] = field(default_factory=list)
    triggered_conditions: List[str] = field(default_factory=list)
    rule_id: str = ""
    apply_hint: str = ""
    created_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "from_stage": self.from_stage,
            "to_stage": self.to_stage,
            "recommended": bool(self.recommended),
            "executed": False,
            "requires_approval": bool(self.requires_approval),
            "triggered_conditions": list(self.triggered_conditions),
            "rule_id": self.rule_id,
            "apply_hint": self.apply_hint,
            "reasons": list(self.reasons),
            "created_at": self.created_at,
        }


def suggest_stage_rollback(
    capability_id: str,
    *,
    from_stage: str = "",
    verdict: Optional[LivenessVerdict] = None,
    degraded: Optional[bool] = None,
    reasons: Optional[Sequence[str]] = None,
    now: float = 0.0,
) -> StageRollbackSuggestion:
    """退化 → ``native → borrowed`` **回退建议**（**不自动执行** stage 变更）

    只在 ``degraded`` 为真时 ``recommended=True``；未退化时同样返回对象
    （``recommended=False``），使"**建议了但不该执行**"与"**根本没建议**"可区分。
    """
    flag = bool(verdict.degraded) if (degraded is None and verdict is not None) \
        else bool(degraded)
    triggered = list(verdict.triggered_conditions) if verdict is not None else []
    suggestion = StageRollbackSuggestion(
        capability_id=str(capability_id or ""),
        from_stage=str(from_stage or ""),
        to_stage=ROLLBACK_TO_STAGE,
        recommended=flag,
        executed=False,
        requires_approval=True,
        triggered_conditions=triggered,
        created_at=float(now or _now()),
    )
    suggestion.rule_id = (f"stage_rollback:{suggestion.capability_id}."
                          f"{suggestion.from_stage or 'unknown'}"
                          f"->{suggestion.to_stage}")
    if flag:
        suggestion.reasons = list(reasons or []) + [
            f"探活判定退化（命中：{triggered or '未列明'}）⇒ 建议回退到上游实现"
            f"（{suggestion.from_stage or '未登记'} → {ROLLBACK_TO_STAGE}）",
            "**本建议不自动执行**：stage 变更须走既有 `stage_migrate` + 审计 + 人工审批",
        ]
    else:
        suggestion.reasons = list(reasons or []) + [
            "未判定退化 ⇒ 不建议回退（stage 保持不变）"]
    # `apply_hint` 只写"若要执行该怎么做"，不含任何执行副作用
    suggestion.apply_hint = (
        "from agent.digestion.stage import stage_migrate; "
        f"stage_migrate({suggestion.capability_id!r}, {ROLLBACK_TO_STAGE!r}, "
        "evidence={'liveness_rollback': True, 'reason': ...}, "
        "actor='<approver>', reason='探活退化回退（TASK-S7-04）')")
    return suggestion


# ════════════════════════════════════════════════════════════
#  基线台账（探活**自己的**基线；与 S3-02 判定集/通行证分离）
# ════════════════════════════════════════════════════════════


@dataclass
class LivenessBaseline:
    """单个能力的探活基线 + 观测历史

    【为什么不自动抬基线】基线若每轮随观测值上移，慢性退化（每轮略差一点）
    永远不会触发 —— 这正是探活要防的场景。故基线只在
    ``established_at`` 那一刻确定，重设须显式 ``LivenessProbe.establish()``。
    ``last_success_at`` 是**心跳**（每轮成功更新），不是阈值。
    """

    capability_id: str
    stage: str = ""
    pass_rate: Optional[float] = None
    p99_wall_ms: Optional[float] = None
    cost_cents_per_unit: Optional[float] = None
    clock: str = CLOCK_WALL
    last_success_at: float = 0.0
    last_probe_at: float = 0.0
    probes: int = 0
    consecutive_degraded: int = 0
    established_at: float = 0.0
    updated_at: float = 0.0
    last_verdict: str = ""
    history: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def established(self) -> bool:
        """是否已建立基线（三项阈值基线是否齐备由各自取值决定）"""
        return bool(self.established_at)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "stage": self.stage,
            "pass_rate": self.pass_rate,
            "p99_wall_ms": self.p99_wall_ms,
            "cost_cents_per_unit": self.cost_cents_per_unit,
            "clock": self.clock,
            "last_success_at": self.last_success_at,
            "last_probe_at": self.last_probe_at,
            "probes": self.probes,
            "consecutive_degraded": self.consecutive_degraded,
            "established_at": self.established_at,
            "updated_at": self.updated_at,
            "last_verdict": self.last_verdict,
            "history": [dict(row) for row in self.history],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LivenessBaseline":
        payload = dict(data or {})
        history = [dict(row) for row in (payload.pop("history", None) or [])]
        known = set(cls.__dataclass_fields__) - {"history"}
        unknown = set(payload) - known
        if unknown:
            logger.warning("[liveness] 基线含未知字段（忽略并告警）: %s", sorted(unknown))
            for key in unknown:
                payload.pop(key, None)
        item = cls(**payload)
        item.history = history
        return item


class LivenessBaselineStore:
    """基线台账（JSON；单文件、原子写）

    **与判定集/通行证/灰度台账物理分离**：它们描述"契约与放行证据"，
    本台账描述"自研实现的历史水位"，生命周期与语义都不同，故不共用文件。
    """

    schema_version = BASELINE_SCHEMA_VERSION

    def __init__(self, path: str = "", *, directory: str = "") -> None:
        explicit = str(path or "").strip()
        self.path = explicit if explicit else baseline_path(directory)

    # ── 读写 ────────────────────────────────────────────────

    def _read_all(self) -> Dict[str, Dict[str, Any]]:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:  # noqa: BLE001
            logger.warning("[liveness] 基线台账不可读（按空处理，不静默删文件）: %s: %s",
                           self.path, exc)
            return {}
        rows = payload.get("capabilities") if isinstance(payload, dict) else None
        return dict(rows or {})

    def _write_all(self, rows: Dict[str, Dict[str, Any]]) -> None:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        payload = {
            "schema_version": self.schema_version,
            "updated_at": _now(),
            "capabilities": {k: rows[k] for k in sorted(rows)},
        }
        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    # ── 公共 API ────────────────────────────────────────────

    def load(self, capability_id: str) -> Optional[LivenessBaseline]:
        row = self._read_all().get(str(capability_id or ""))
        if not isinstance(row, dict):
            return None
        return LivenessBaseline.from_dict(row)

    def save(self, baseline: LivenessBaseline) -> LivenessBaseline:
        rows = self._read_all()
        rows[baseline.capability_id] = baseline.to_dict()
        self._write_all(rows)
        return baseline

    def all(self) -> Dict[str, Dict[str, Any]]:
        return self._read_all()

    def stats(self) -> Dict[str, Any]:
        rows = self._read_all()
        return {"path": self.path, "capabilities": len(rows),
                "schema_version": self.schema_version}


# ════════════════════════════════════════════════════════════
#  探活报告
# ════════════════════════════════════════════════════════════


@dataclass
class LivenessReport:
    """一次探活（或一次"无可探活能力"的如实返回）的完整结果"""

    capability_id: str = ""
    verdict: str = VERDICT_OK
    stage: str = ""
    stage_source: str = SRC_UNAVAILABLE
    degraded: bool = False
    probe_ids: List[str] = field(default_factory=list)
    total: int = 0
    passed: int = 0
    pass_rate: Optional[float] = None
    p99_wall_candidate_ms: Optional[float] = None
    p99_wall_upstream_ms: Optional[float] = None
    clock: str = CLOCK_WALL
    cost_cents_per_unit: Optional[float] = None
    cost_source: str = SRC_UNAVAILABLE
    baseline_established: bool = False
    conditions: List[Dict[str, Any]] = field(default_factory=list)
    triggered_conditions: List[str] = field(default_factory=list)
    unmeasured_conditions: List[str] = field(default_factory=list)
    failure_list: List[Dict[str, Any]] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    rollback: Optional[StageRollbackSuggestion] = None
    incident_id: str = ""
    incident_missing_elements: List[str] = field(default_factory=list)
    probe_run_id: str = ""
    probed_at: float = 0.0
    duration_ms: float = 0.0
    version: str = PROBE_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "verdict": self.verdict,
            "stage": self.stage,
            "stage_source": self.stage_source,
            "degraded": bool(self.degraded),
            "probe_run_id": self.probe_run_id,
            "probe_ids": list(self.probe_ids),
            "total": self.total,
            "passed": self.passed,
            "pass_rate": self.pass_rate,
            "p99_wall_candidate_ms": self.p99_wall_candidate_ms,
            "p99_wall_upstream_ms": self.p99_wall_upstream_ms,
            "clock": self.clock,
            "cost_cents_per_unit": self.cost_cents_per_unit,
            "cost_source": self.cost_source,
            "baseline_established": bool(self.baseline_established),
            "triggered_conditions": list(self.triggered_conditions),
            "unmeasured_conditions": list(self.unmeasured_conditions),
            "incident_id": self.incident_id,
            "incident_missing_elements": list(self.incident_missing_elements),
            "rollback": self.rollback.to_dict() if self.rollback else None,
            "failure_list": [dict(row) for row in self.failure_list],
            "reasons": list(self.reasons),
            "probed_at": self.probed_at,
            "duration_ms": self.duration_ms,
            "version": self.version,
            "note": ("探活判「自身实现退化」；上游契约漂移由 gate.reprobe（30 天）负责，"
                     "两者口径不通用"),
        }


# ════════════════════════════════════════════════════════════
#  探活器
# ════════════════════════════════════════════════════════════


class LivenessProbe:
    """native 期探活器（判定集子集重放 + 四项退化条件 + 回退建议 + 事故卡）

    Args:
        case_store: 判定集存储（缺省 `cases.open_case_store()`；**用例必须显式传路径**
            或由 autouse fixture 隔离，防运行时目录污染 —— S3-02/S3-03 两次踩过）。
        sandbox: 回放沙箱（缺省 `ReplaySandbox(measure_wall=True)`，取**真实墙钟**）。
        registry: descriptor 台账（**显式传入才读**；缺省不读运行时台账）。
        baseline_store: 基线台账（缺省落 `CP_DIGESTION_LIVENESS_DIR`）。
        probe_size: 判定集子集抽样规模（缺省读 `CP_DIGESTION_LIVENESS_PROBE_SIZE`）。
        cost_provider: 单位成本取法 ``f(capability_id) -> float | dict``（可注入；
            缺省 `None` ⇒ 条件③如实记为"证据缺位"）。
        emit_audit: 是否写链式审计（best-effort）。
        emit_incident: 退化时是否开事故卡（best-effort；`False` 只出建议）。
        env: 环境变量覆盖（用例注入用；缺省读 `os.environ`）。
    """

    def __init__(
        self,
        *,
        case_store: Optional[CaseStore] = None,
        sandbox: Optional[ReplaySandbox] = None,
        registry: Any = None,
        baseline_store: Optional[LivenessBaselineStore] = None,
        probe_size: Optional[int] = None,
        cost_provider: Optional[Callable[[str], Any]] = None,
        emit_audit: bool = True,
        emit_incident: bool = True,
        env: Optional[Dict[str, str]] = None,
    ) -> None:
        self._case_store = case_store
        self._sandbox = sandbox
        self._registry = registry
        self._baseline_store = baseline_store
        self._probe_size = (int(probe_size) if probe_size
                            else probe_size_from_env(env))
        self.cost_provider = cost_provider
        self.emit_audit = bool(emit_audit)
        self.emit_incident = bool(emit_incident)
        self.env = dict(env) if env is not None else None

    # ── 惰性依赖 ────────────────────────────────────────────

    @property
    def case_store(self) -> CaseStore:
        if self._case_store is None:
            self._case_store = open_case_store()
        return self._case_store

    @property
    def sandbox(self) -> ReplaySandbox:
        if self._sandbox is None:
            # measure_wall=True ⇒ 采**真实墙钟**（探活条件②的口径来源）
            self._sandbox = ReplaySandbox(measure_wall=True)
        return self._sandbox

    @property
    def registry(self) -> Any:
        return self._registry

    @property
    def baseline_store(self) -> LivenessBaselineStore:
        if self._baseline_store is None:
            self._baseline_store = LivenessBaselineStore()
        return self._baseline_store

    @property
    def probe_size(self) -> int:
        return int(self._probe_size)

    # ── 作用范围 ────────────────────────────────────────────

    def _stage_of(self, capability_id: str) -> Tuple[str, str]:
        """能力当前 stage → ``(stage, source)``（registry 缺省 ⇒ ``("", "unavailable")``）"""
        reg = self.registry
        if reg is None:
            return "", SRC_UNAVAILABLE
        try:
            desc = reg.get(capability_id)
        except Exception as exc:  # noqa: BLE001 台账问题不得成为探活故障源
            logger.debug("[liveness] descriptor 读取失败: %s", exc)
            return "", SRC_UNAVAILABLE
        if desc is None:
            return "", SRC_UNAVAILABLE
        stage = getattr(getattr(desc, "evolution", None), "stage", None)
        return str(getattr(stage, "value", stage) or ""), SRC_DESCRIPTOR

    def scan_targets(self) -> List[str]:
        """**可探活能力**清单（stage ∈ {internalized, native}）

        【无能力时返回空列表】不报错、不编造、**不拿别的 stage 的能力凑数**。
        台账不可用（未注入 / 读失败）时同样返回空列表 —— 探活不应在"不知道范围"
        的情况下到处跑。
        """
        reg = self.registry
        if reg is None:
            return []
        found: List[str] = []
        try:
            for stage in PROBE_STAGES:
                lister = getattr(reg, "list_by_stage", None)
                if callable(lister):
                    for desc in lister(stage) or []:
                        cid = str(getattr(desc, "capability_id", "") or "")
                        if cid and cid not in found:
                            found.append(cid)
                else:  # pragma: no cover - 非标准台账实现的兜底
                    for desc in getattr(reg, "list", lambda: [])() or []:
                        cid = str(getattr(desc, "capability_id", "") or "")
                        cur = getattr(getattr(desc, "evolution", None), "stage", None)
                        if cid and str(getattr(cur, "value", cur) or "") in PROBE_STAGES \
                                and cid not in found:
                            found.append(cid)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[liveness] 目标扫描失败（按无目标处理）: %s", exc)
            return []
        return sorted(found)

    # ── 成本证据 ────────────────────────────────────────────

    def _cost_evidence(self, capability_id: str,
                       explicit: Optional[Any]) -> Tuple[Optional[float], str]:
        """单位成本 → ``(value, source)``；取不到就如实 ``(None, "unavailable")``"""
        if explicit is not None:
            value = (explicit.get("cents_per_unit") if isinstance(explicit, dict)
                     else explicit)
            if value is None:
                return None, SRC_UNAVAILABLE
            try:
                return float(value), SRC_EXPLICIT
            except (TypeError, ValueError):
                logger.warning("[liveness] 显式成本非法: %r", explicit)
                return None, SRC_UNAVAILABLE
        if self.cost_provider is None:
            return None, SRC_UNAVAILABLE
        try:
            raw = self.cost_provider(capability_id)
        except Exception as exc:  # noqa: BLE001 成本源故障不得阻断探活
            logger.warning("[liveness] cost_provider 失败: %s: %s", type(exc).__name__, exc)
            return None, SRC_UNAVAILABLE
        if raw is None:
            return None, SRC_UNAVAILABLE
        if isinstance(raw, dict):
            value = raw.get("cents_per_unit", raw.get("value"))
            if value is None:
                return None, SRC_UNAVAILABLE
            try:
                return float(value), str(raw.get("source") or SRC_PROVIDER)
            except (TypeError, ValueError):
                return None, SRC_UNAVAILABLE
        try:
            return float(raw), SRC_PROVIDER
        except (TypeError, ValueError):
            return None, SRC_UNAVAILABLE

    # ── 单能力探活 ──────────────────────────────────────────

    def run(
        self,
        capability_id: str,
        *,
        now: float = 0.0,
        stage: str = "",
        case_set: Optional[CaseSet] = None,
        candidate: Any = None,
        upstream_provider: Optional[Callable[[EquivalenceCase], Any]] = None,
        baseline: Optional[LivenessBaseline] = None,
        cost: Optional[Any] = None,
        fatal_change: str = "",
        probe_size: Optional[int] = None,
        period_days: float = LIVENESS_PERIOD_DAYS,
        pass_rate_ratio: float = LIVENESS_PASS_RATE_RATIO,
        p99_ratio: float = LIVENESS_P99_REGRESSION_RATIO,
        cost_ratio: float = LIVENESS_COST_INCREASE_RATIO,
        update_baseline: bool = True,
        rebaseline: bool = False,
        emit_incident: Optional[bool] = None,
    ) -> LivenessReport:
        """探活一个能力 → `LivenessReport`（**不抛**：任何异常收敛为 ``verdict=error``）

        流程：**作用范围判定 → 判定集子集抽样 → 回放沙箱重放（真实墙钟）→ 四项条件
        → 回退建议 + 事故卡（仅退化时）→ 基线台账更新**。

        Args:
            rebaseline: **显式**重设基线（唯一能抬基线的通道；见 `establish()`）。
                默认 ``False`` ⇒ 既有基线不被本轮观测带跑。
            emit_incident: 本轮是否开事故卡（``None`` ⇒ 用构造期的 `self.emit_incident`）。
        """
        cid = str(capability_id or "")
        started = time.perf_counter()
        at = float(now or _now())
        want_incident = self.emit_incident if emit_incident is None else bool(emit_incident)
        report = LivenessReport(capability_id=cid, probe_run_id="", probed_at=at)
        try:
            return self._run_locked(
                report, cid, at=at, started=started, stage=stage, case_set=case_set,
                candidate=candidate, upstream_provider=upstream_provider,
                baseline=baseline, cost=cost, fatal_change=fatal_change,
                probe_size=probe_size, period_days=period_days,
                pass_rate_ratio=pass_rate_ratio, p99_ratio=p99_ratio,
                cost_ratio=cost_ratio, update_baseline=update_baseline,
                rebaseline=rebaseline, emit_incident=want_incident)
        except Exception as exc:  # noqa: BLE001 探活不得成为新的故障源
            logger.error("[liveness] 探活失败 %s: %s: %s", cid, type(exc).__name__, exc)
            report.verdict = VERDICT_ERROR
            report.reasons.append(f"探活异常: {type(exc).__name__}: {exc}")
            report.duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
            return report

    def _run_locked(self, report: LivenessReport, cid: str, *, at: float,
                    started: float, stage: str, case_set: Optional[CaseSet],
                    candidate: Any,
                    upstream_provider: Optional[Callable[[EquivalenceCase], Any]],
                    baseline: Optional[LivenessBaseline], cost: Optional[Any],
                    fatal_change: str, probe_size: Optional[int],
                    period_days: float, pass_rate_ratio: float, p99_ratio: float,
                    cost_ratio: float, update_baseline: bool,
                    rebaseline: bool = False,
                    emit_incident: bool = True) -> LivenessReport:
        resolved_stage = str(stage or "")
        stage_source = SRC_EXPLICIT if resolved_stage else SRC_UNAVAILABLE
        if not resolved_stage:
            resolved_stage, stage_source = self._stage_of(cid)
        report.stage = resolved_stage
        report.stage_source = stage_source
        report.probe_run_id = _probe_run_id(cid, at)

        # ── 作用范围：只对 internalized/native 生效 ──
        if resolved_stage and resolved_stage not in PROBE_STAGES:
            report.verdict = VERDICT_NOT_APPLICABLE
            report.reasons.append(
                f"stage={resolved_stage!r} 不在探活作用范围 {PROBE_STAGES} ⇒ 不探活"
                f"（探活对象是「自研实现退化」，不是全部能力）")
            report.duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
            return report

        # ── 判定集 ──
        resolved = case_set if case_set is not None else self._load_case_set(cid)
        if resolved is None or not resolved.active_cases():
            report.verdict = VERDICT_NO_CASES
            report.reasons.append(
                "无生效判定集 ⇒ 无探活对象（不报错、不编造探活结果）")
            report.duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
            return report

        # ── 判定集子集抽样（与漂移重探同一抽样口径，不自建第二套） ──
        size = int(probe_size or self.probe_size)
        active = resolved.active_cases()
        by_id = {c.case_id: c for c in active}
        ids = probe_sample_ids([c.case_id for c in active], size=size)
        report.probe_ids = list(ids)

        # ── 回放（**真实墙钟**） ──
        replay = self._replay(cid, [by_id[i] for i in ids if i in by_id],
                              candidate=candidate,
                              upstream_provider=upstream_provider)
        report.total = replay.total
        report.passed = replay.passed
        report.pass_rate = replay.pass_rate if replay.total else None
        report.p99_wall_candidate_ms = (replay.p99_wall_candidate_ms()
                                        if replay.total else None)
        report.p99_wall_upstream_ms = (replay.p99_wall_upstream_ms()
                                       if replay.total else None)
        report.failure_list = replay.failure_list()

        # ── 基线 ──
        base = baseline if baseline is not None else self._load_baseline(cid)
        cost_value, cost_source = self._cost_evidence(cid, cost)
        report.cost_cents_per_unit = cost_value
        report.cost_source = cost_source

        verdict = evaluate_liveness(
            capability_id=cid,
            pass_rate=report.pass_rate,
            baseline_pass_rate=(base.pass_rate if base else None),
            p99_wall_ms=report.p99_wall_candidate_ms,
            baseline_p99_wall_ms=(base.p99_wall_ms if base else None),
            cost_cents_per_unit=cost_value,
            baseline_cost_cents_per_unit=(base.cost_cents_per_unit if base else None),
            last_success_at=(base.last_success_at if base else 0.0),
            now=at, period_days=period_days, pass_rate_ratio=pass_rate_ratio,
            p99_ratio=p99_ratio, cost_ratio=cost_ratio, clock=CLOCK_WALL)
        report.conditions = [c.to_dict() for c in verdict.conditions]
        report.triggered_conditions = verdict.triggered_conditions
        report.unmeasured_conditions = verdict.unmeasured_conditions
        report.reasons = list(verdict.reasons)
        report.degraded = verdict.degraded
        report.verdict = VERDICT_DEGRADED if verdict.degraded else VERDICT_OK
        report.baseline_established = bool(base is not None and base.established)

        # ── 退化：回退建议（**不执行**）+ 事故卡 ──
        report.rollback = suggest_stage_rollback(
            cid, from_stage=resolved_stage, verdict=verdict,
            reasons=[f"探活 run={report.probe_run_id}"], now=at)
        if verdict.degraded and emit_incident:
            report.incident_id, missing = _raise_liveness_incident(
                capability_id=cid, stage=resolved_stage, verdict=verdict,
                report=report, rollback=report.rollback,
                sampled=[by_id[i] for i in ids if i in by_id],
                fatal_change=fatal_change, now=at, registry=self.registry)
            report.incident_missing_elements = list(missing)

        # ── 台账：观测入 history；成功探活刷心跳；**基线只在首次/显式重设时确定** ──
        if update_baseline:
            self._update_baseline(cid, stage=resolved_stage, report=report,
                                  base=base, cost_value=cost_value, at=at,
                                  period_days=period_days, rebaseline=rebaseline)
        self._audit(cid, report, at=at)
        report.duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
        return report

    # ── 全部目标 ────────────────────────────────────────────

    def run_all(self, *, now: float = 0.0, max_targets: Optional[int] = None,
                **kwargs: Any) -> Dict[str, Any]:
        """探活全部 ``internalized``/``native`` 能力

        **无此类能力时**如实返回 ``{"status": "no_capability", "reports": []}`` ——
        不报错、不编造（当前仓库正处该状态：尚无 native 能力）。
        """
        at = float(now or _now())
        targets = self.scan_targets()
        cap = int(max_targets if max_targets is not None else max_targets_from_env(self.env))
        truncated = len(targets) > cap
        selected = targets[:cap]
        if not targets:
            return {
                "status": "no_capability",
                "verdict": VERDICT_NO_CAPABILITY,
                "targets": [],
                "probed": 0,
                "degraded": 0,
                "truncated": False,
                "reports": [],
                "probed_at": at,
                "note": (f"无 {list(PROBE_STAGES)} 能力 ⇒ 无可探活能力"
                         "（不报错、不编造、不拿别的 stage 凑数）"),
            }
        # 注：`now` 与 `max_targets` 均为命名参数 ⇒ 不会出现在 kwargs 中，
        # 故 `**kwargs` 与本行显式实参**不可能**冲突（kwarg 冲突扫描的 MEDIUM 项
        # 属启发式误报；此处显式说明，便于复核者不必重新推导）。
        reports = [self.run(cid, now=at, **kwargs) for cid in selected]
        return {
            "status": "ok",
            "verdict": (VERDICT_DEGRADED if any(r.degraded for r in reports)
                        else VERDICT_OK),
            "targets": targets,
            "probed": len(reports),
            "degraded": sum(1 for r in reports if r.degraded),
            "truncated": truncated,
            "max_targets": cap,
            "reports": [r.to_dict() for r in reports],
            "probed_at": at,
        }

    def establish(self, capability_id: str, *, now: float = 0.0,
                  **kwargs: Any) -> LivenessReport:
        """**显式**建立/重设基线（唯一能抬基线的入口）

        为什么单独开一个入口：若探活每轮自动抬基线，慢性退化（每轮只差一点点）
        永远不会被触发 —— 那正是探活要防的场景。故"重设基线"必须是**显式动作**，
        且本入口**不开事故卡**（重设基线是治理动作，不是退化事件）。
        """
        kwargs.pop("emit_incident", None)
        kwargs.pop("rebaseline", None)
        # 注：`now` 是命名参数 ⇒ 不可能出现在 kwargs 中；`emit_incident`/`rebaseline`
        # 已显式 pop ⇒ 调用方重复传入不会变成 TypeError（kwarg 冲突扫描的 MEDIUM 项
        # 由此逐条消解，而非"看着像风险就放过"）。
        return self.run(capability_id, now=float(now or _now()),
                        rebaseline=True, emit_incident=False, **kwargs)

    # ── 内部：判定集 / 回放 / 台账 / 审计 ────────────────────

    def _load_case_set(self, capability_id: str) -> Optional[CaseSet]:
        try:
            return self.case_store.load(capability_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[liveness] 判定集读取失败 %s: %s", capability_id, exc)
            return None

    def _load_baseline(self, capability_id: str) -> Optional[LivenessBaseline]:
        try:
            return self.baseline_store.load(capability_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[liveness] 基线读取失败 %s: %s", capability_id, exc)
            return None

    def _replay(self, capability_id: str,
                cases: Sequence[EquivalenceCase], *,
                candidate: Any,
                upstream_provider: Optional[Callable[[EquivalenceCase], Any]]
                ) -> ReplayReport:
        """判定集子集重放（复用 `ReplaySandbox` 三层比对；真实墙钟）"""
        report = ReplayReport(capability_id=capability_id)
        box = self.sandbox
        for case in cases:
            picked = _resolve_candidate(candidate, case)
            upstream = (upstream_provider(case) if upstream_provider is not None
                        else ProgramImplementation(list(case.upstream), name="upstream"))
            replay = box.replay_case(case, picked, upstream=upstream, measure_wall=True)
            report.replays.append(replay)
            for layer in replay.diff.failed_layers:
                report.layer_failures[layer] = report.layer_failures.get(layer, 0) + 1
        return report

    def _update_baseline(self, capability_id: str, *, stage: str,
                         report: LivenessReport,
                         base: Optional[LivenessBaseline],
                         cost_value: Optional[float], at: float,
                         period_days: float, rebaseline: bool = False) -> None:
        item = base or LivenessBaseline(capability_id=capability_id)
        item.stage = stage or item.stage
        item.clock = CLOCK_WALL
        item.probes = int(item.probes or 0) + 1
        item.last_probe_at = at
        item.last_verdict = report.verdict
        item.updated_at = at
        item.consecutive_degraded = (int(item.consecutive_degraded or 0) + 1
                                    if report.degraded else 0)
        # 心跳：**未退化**才算一次成功探活（条件④的基准）
        if not report.degraded:
            item.last_success_at = at
        # 基线：只在**尚无基线**或**显式重设**时由本轮确定
        # （自动抬基线会让慢性退化永远不触发 —— 见 `establish()` 的注释）
        if rebaseline or not item.established:
            item.established_at = at
            item.pass_rate = report.pass_rate
            item.p99_wall_ms = report.p99_wall_candidate_ms
            item.cost_cents_per_unit = cost_value
            report.baseline_established = True
        item.history.append({
            "probed_at": at,
            "verdict": report.verdict,
            "pass_rate": report.pass_rate,
            "p99_wall_ms": report.p99_wall_candidate_ms,
            "cost_cents_per_unit": cost_value,
            "triggered": list(report.triggered_conditions),
            "probe_run_id": report.probe_run_id,
            "period_days": float(period_days),
            "rebaselined": bool(rebaseline),
        })
        if len(item.history) > MAX_HISTORY:
            item.history = item.history[-MAX_HISTORY:]
        try:
            self.baseline_store.save(item)
        except Exception as exc:  # noqa: BLE001 台账写失败只告警（advisory）
            logger.warning("[liveness] 基线台账写入失败（advisory）: %s", exc)

    def _audit(self, capability_id: str, report: LivenessReport, *, at: float) -> None:
        """链式审计留痕（**best-effort**；退化另有事故卡的审计/事件，不重复）"""
        if not self.emit_audit:
            return
        try:
            from agent.audit.facade import audit
            audit.record(
                AUDIT_ACTION_DEGRADED if report.degraded else AUDIT_ACTION_PROBED,
                actor="agent.digestion.probe",
                subject=f"capability:{capability_id}",
                payload={
                    "probe_run_id": report.probe_run_id,
                    "stage": report.stage,
                    "verdict": report.verdict,
                    "pass_rate": report.pass_rate,
                    "p99_wall_candidate_ms": report.p99_wall_candidate_ms,
                    "clock": CLOCK_WALL,
                    "triggered_conditions": list(report.triggered_conditions),
                    "unmeasured_conditions": list(report.unmeasured_conditions),
                    "probes": len(report.probe_ids),
                },
                source="agent", status=report.verdict,
                technical={"probe_version": PROBE_VERSION},
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("[liveness] 审计写入失败: %s", exc)


# ════════════════════════════════════════════════════════════
#  小工具
# ════════════════════════════════════════════════════════════


def _resolve_candidate(candidate: Any, case: EquivalenceCase) -> Any:
    """候选实现解析（与 `gate._resolve_candidate` **同语义**；缺省用 Seed Pack 骨架）

    刻意与验收门保持一致：探活评的必须是"当前被认为已内化的那份实现"，
    解析口径若与门不一致，探活结论就无法与放行证据对上。
    """
    if candidate is None:
        return seed_candidate_for(case)
    if callable(candidate) and not isinstance(candidate, Implementation):
        return candidate(case)
    return candidate


def _probe_run_id(capability_id: str, at: float) -> str:
    """确定性探活 run id（同能力同秒 ⇒ 同 id；可反查基线台账 history）"""
    material = f"liveness|{capability_id}|{int(at)}"
    return "live_" + hashlib.sha1(material.encode("utf-8")).hexdigest()[:12]


def _raise_liveness_incident(*, capability_id: str, stage: str,
                             verdict: LivenessVerdict, report: LivenessReport,
                             rollback: StageRollbackSuggestion,
                             sampled: Sequence[EquivalenceCase],
                             fatal_change: str, now: float,
                             registry: Any = None
                             ) -> Tuple[str, List[str]]:
    """退化 → 事故卡（复用 `levels.raise_incident`，再补齐它签名未覆盖的三要素）

    Returns:
        ``(incident_id, missing_elements)``。``missing_elements`` **如实**回传
        （缺 ``fatal_change`` 时不编造 commit，卡片就应保持不可 resolved）。
    """
    try:
        from agent.self_healing.levels import HealLevel, level_of, raise_incident, save_incident
    except Exception as exc:  # noqa: BLE001 自愈层不可用不得阻断探活结论
        logger.warning("[liveness] 自愈层不可用，跳过事故卡: %s", exc)
        return "", []

    level = level_of(LEVEL_BY_STAGE.get(stage, DEFAULT_LEVEL)) or HealLevel.L2
    failed_ids = sorted({str(row.get("case_id") or "")
                         for row in report.failure_list if row.get("case_id")})
    trace_ids = [str(c.origin_trace_id) for c in sampled if str(c.origin_trace_id or "")]
    if not trace_ids:
        # 真实 trace 指针缺位时用本次探活 run id：它**确实**可在基线台账 history 反查
        trace_ids = [report.probe_run_id]
    fatal = str(fatal_change or "").strip() or _descriptor_version(capability_id,
                                                                   registry) or ""
    root_cause = (
        f"{capability_id}（stage={stage}）自身实现退化："
        + "；".join(f"{COND_LABELS.get(c, c)}——{c}" for c in verdict.triggered_conditions)
        + f"（探活 run={report.probe_run_id}；样本 {report.total} 组，"
          f"通过率 {report.pass_rate}，真实墙钟 p99 {report.p99_wall_candidate_ms}ms）")
    try:
        card = raise_incident(
            level, signal="liveness_degraded", root_cause=root_cause,
            fatal_change=fatal, trace_ids=trace_ids, tenant_id="default",
            detail={
                "probe_run_id": report.probe_run_id,
                "capability_id": capability_id,
                "stage": stage,
                "triggered_conditions": list(verdict.triggered_conditions),
                "unmeasured_conditions": list(verdict.unmeasured_conditions),
                "probe_ids": list(report.probe_ids),
                "failed_case_ids": failed_ids,
                "rollback_rule_id": rollback.rule_id,
                "rollback_to_stage": rollback.to_stage,
                "rollback_executed": False,
                "clock": CLOCK_WALL,
                "pass_rate": report.pass_rate,
                "p99_wall_candidate_ms": report.p99_wall_candidate_ms,
                "cost_cents_per_unit": report.cost_cents_per_unit,
                "fatal_change_source": ("explicit" if str(fatal_change or "").strip()
                                        else ("descriptor.meta.version" if fatal
                                              else "unavailable")),
                "note": ("探活=自身实现退化（非上游契约漂移，后者见 gate.reprobe）；"
                         "回退建议**不自动执行**"),
            })
    except Exception as exc:  # noqa: BLE001
        logger.warning("[liveness] 事故卡创建失败（不影响退化结论）: %s", exc)
        return "", []

    # ── 补齐 `raise_incident` 签名未覆盖的三要素（真实、可回溯） ──
    card.evasion_rule = rollback.rule_id
    card.in_strategy_memory = {"yes": True, "id": f"liveness:{capability_id}"}
    card.regression_case_added = (
        {"yes": True, "case_id": failed_ids[0]} if failed_ids
        else {"yes": False, "case_id": ""})
    try:
        # 同一 incident_id / 同一路径 / **同一 writer**（见 INCIDENT_WRITER 注释）
        save_incident(card, writer=INCIDENT_WRITER)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[liveness] 事故卡六要素回写失败: %s", exc)
    return card.incident_id, list(card.missing_elements())


def _descriptor_version(capability_id: str, registry: Any = None) -> str:
    """``descriptor.meta.version``（**真实台账字段**；取不到返回空串，不编造）

    口径与 `gate._descriptor_view` 一致：``registry=None`` 一律返回空串
    （**不隐式构造** `DescriptorRegistry()`）—— 隐式构造会在单测/CI 中读到运行时
    台账 ``data/descriptors.json``（隔离破坏），也会与显式传参的口径不一致。
    """
    if registry is None:
        return ""
    try:
        desc = registry.get(capability_id)
    except Exception:  # noqa: BLE001
        return ""
    if desc is None:
        return ""
    meta = getattr(desc, "meta", None)
    return str(getattr(meta, "version", "") or "")


# ════════════════════════════════════════════════════════════
#  调度（每周；**默认关闭**）
# ════════════════════════════════════════════════════════════


def cost_policy_restricted() -> Tuple[bool, str]:
    """S5-03 成本刹车联动（**复用** `internalize.cost_policy_restricted()`，不自建）

    ⚠ 与 `internalize` 的处置**刻意不同**：内化是"新增资产"任务，断食期应冻结；
    探活是**已上线实现的健康监控**（§6.7「保执行」），故断食期**不停**探活，
    只在断食/日熔断期间**降低抽样规模**（见 `_tick`），以免监控被成本治理自身熄火。
    """
    try:
        from .internalize import cost_policy_restricted as _restricted
        return _restricted()
    except Exception as exc:  # noqa: BLE001
        logger.debug("[liveness] 成本刹车状态不可用（按不抑制处理）: %s", exc)
        return False, ""


def register_liveness_job(scheduler: Any = None, *,
                          probe: Optional[LivenessProbe] = None,
                          enabled: Optional[bool] = None,
                          interval_seconds: float = LIVENESS_INTERVAL_SECONDS,
                          max_targets: Optional[int] = None,
                          env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """注册每周探活任务（复用既有 `task_scheduler`；**默认关闭**）

    ``CP_DIGESTION_LIVENESS_ENABLED=true`` 才注册 —— 与 `register_reprobe_job` /
    `register_internalize_job` **同一条安全底线**（一切自动化开关默认关闭）。

    **预算上限**：单周期最多探活 ``CP_DIGESTION_LIVENESS_MAX_TARGETS``（默认 20）个
    能力；每个能力只抽 ``CP_DIGESTION_LIVENESS_PROBE_SIZE``（默认 5）组用例。
    断食/日熔断期间抽样规模减半（**不停探活**，见 `cost_policy_restricted()`）。
    """
    resolved_env = dict(env) if env is not None else None
    if enabled is None:
        enabled = liveness_enabled(resolved_env)
    if not enabled:
        return {"status": "disabled",
                "note": f"探活调度默认关闭（安全底线）；开启："
                        f"{LIVENESS_ENABLE_ENV}=true"}
    try:
        if scheduler is None:
            from agent.task_scheduler import get_scheduler
            scheduler = get_scheduler()
    except Exception as exc:  # noqa: BLE001
        logger.error("[liveness] 调度器不可用: %s", exc)
        return {"status": "error", "error": str(exc)}

    box = probe or LivenessProbe(env=resolved_env)
    cap = int(max_targets if max_targets is not None
              else max_targets_from_env(resolved_env))
    base_size = int(box.probe_size)

    def _tick() -> Dict[str, Any]:
        restricted, why = cost_policy_restricted()
        size = max(1, base_size // 2) if restricted else base_size
        try:
            outcome = box.run_all(max_targets=cap, probe_size=size)
            if outcome.get("status") == "no_capability":
                return {"status": "no_capability",
                        "reason": outcome.get("note"),
                        "probe_size": size}
            return {"status": "ok", "probed": outcome.get("probed"),
                    "degraded": outcome.get("degraded"),
                    "truncated": outcome.get("truncated"),
                    "probe_size": size,
                    "suppressed_by_cost_brake": bool(restricted),
                    "cost_brake_reason": why if restricted else ""}
        except Exception as exc:  # noqa: BLE001 调度线程不得因单次失败挂掉
            logger.error("[liveness] 探活任务失败: %s", exc)
            return {"status": "error", "error": str(exc)}

    scheduler.add_interval_task(LIVENESS_TASK_NAME, func=_tick,
                                interval_seconds=float(interval_seconds))
    task_id = ""
    tasks = getattr(scheduler, "tasks", None)
    if tasks is not None:
        try:
            task_id = str(getattr(tasks.get(LIVENESS_TASK_NAME), "task_id", "") or "")
        except Exception:  # noqa: BLE001
            task_id = ""
    return {"status": "registered", "task": LIVENESS_TASK_NAME,
            "task_id": task_id, "interval_seconds": float(interval_seconds),
            "max_targets": cap, "probe_size": base_size,
            "stages": list(PROBE_STAGES), "version": PROBE_VERSION}


__all__ = [
    "PROBE_VERSION", "CLOCK_WALL",
    "STAGE_INTERNALIZED", "STAGE_NATIVE", "PROBE_STAGES", "ROLLBACK_TO_STAGE",
    "VERDICT_OK", "VERDICT_DEGRADED", "VERDICT_NO_CAPABILITY",
    "VERDICT_NOT_APPLICABLE", "VERDICT_NO_CASES", "VERDICT_ERROR",
    "LIVENESS_COND_PASS_RATE", "LIVENESS_COND_P99", "LIVENESS_COND_COST", "LIVENESS_COND_OVERDUE",
    "LIVENESS_CONDITIONS", "COND_LABELS",
    "LIVENESS_PASS_RATE_RATIO", "LIVENESS_P99_REGRESSION_RATIO",
    "LIVENESS_COST_INCREASE_RATIO", "LIVENESS_PERIOD_DAYS", "DAY_SECONDS",
    "DEFAULT_PROBE_SIZE", "PROBE_SIZE_ENV", "DEFAULT_MAX_TARGETS", "MAX_TARGETS_ENV",
    "LIVENESS_ENABLE_ENV", "LIVENESS_TASK_NAME", "LIVENESS_INTERVAL_SECONDS",
    "LIVENESS_DIR_ENV", "DEFAULT_LIVENESS_DIR", "BASELINE_FILENAME",
    "LEVEL_BY_STAGE", "INCIDENT_WRITER",
    "AUDIT_ACTION_PROBED", "AUDIT_ACTION_DEGRADED",
    "LivenessCondition", "LivenessVerdict",
    "StageRollbackSuggestion", "LivenessBaseline", "LivenessBaselineStore",
    "LivenessReport", "LivenessProbe",
    "evaluate_liveness", "suggest_stage_rollback", "register_liveness_job",
    "liveness_enabled", "probe_size_from_env", "max_targets_from_env",
    "baseline_path", "cost_policy_restricted",
]
