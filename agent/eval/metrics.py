"""§6.7 指标字典与周报计算（TASK-S5-02）

## 口径纪律（本模块的三条硬规矩）

1. **复用，不另建**：任务闭环/介入/成本一律经 `acr.acr_summary()` 与
   `utc.utc_window()`（S2-03 交付）取得；本模块**只**从 events.v1 事件流读取
   §6.7 要求、而上述聚合尚未覆盖的少量字段（`digest.stage` / `approval` /
   `task.*`），不重复实现 ACR/UTC 的权重与分母口径。
2. **不可追溯的数字不出现**：每个指标都必须带 ``source``（数据源）+ ``formula``
   （计算公式）+ ``numerator``/``denominator``（分子分母）+ ``samples``（样本量）；
   无数据源的一律 ``value=None`` + ``status="framework_only"``，**绝不填 0 冒充**。
3. **样本不足只披露不考核**：样本量 < `MIN_SAMPLES_FOR_ASSESSMENT`（20，对齐
   "每能力 ≥20 条同类轨迹"的口径纪律）时，``status="insufficient_samples"``，
   数字照给但标注"不参与考核"。

## 指标清单（§6.7）

======================  ==========================  ==============  ===============
key                      指标                        目标             状态
======================  ==========================  ==============  ===============
digest_throughput        消化吞吐                    ≥2/周（W10+）    可计算（事件）
internalization_rate     内化转化率                  ≥10%             可计算（台账）
delegation_recovery      委派回收率                  100%             框架（S4-04）
skill_success_rate       技能成功率                  ≥上游×0.98       可计算（灰度台账）
healing_latency          MTTD / MTTR                 <3s / <30s       框架（无发射方）
approval_decay_rate      审批衰减率                  ≥70%             可计算（事件）
delegation_cycle_days    委派中位周期                ≤14 天           可计算（事件）
abandoned_rate           弃单率                      披露不考核        可计算（事件）
routing_accuracy         路由准确率                  >85%（W10+）     框架（需事后标注）
exploration_satisfaction 探索满意度（S2-03 #4）      披露不考核        可计算（双列）
utc_cents_per_task       UTC（辅助，非 §6.7 条目）   由 S5-03 定阈值   可计算（复用 utc）
======================  ==========================  ==============  ===============

**探索满意度正式口径（S2-03 遗留 #4）**：原实现（`acr.exploration.satisfaction`）
是**代理指标**（关闭成功率）。本任务定义正式口径为**双列**：

* **👍率** = like / (like + dislike)，源 `agent.feedback.FeedbackManager.get_feedback_summary()`；
* **任务闭环率** = closed / (closed + failed)，源 `acr.acr_summary()["exploration"]`。

两列**并列披露、均不纳入 ACR 考核**（explore/consult 本就排除在 ACR 分母之外），
并在报告中显式声明"闭环率是代理口径、👍率是直接信号，二者替代关系见
`exploration.disclosure`"。
"""

from __future__ import annotations

import json
import logging
import os
import statistics
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from agent.observability import acr as ACR
from agent.observability import utc as UTC
from agent.observability.events import (
    EV_APPROVAL,
    EV_DIGEST_STAGE,
    EV_HEALING_TRIGGERED,
    EV_TASK_ABANDONED,
    EV_TASK_CLOSED,
    EventEnvelope,
    iter_events,
    shift_day,
)

logger = logging.getLogger("agent.eval.metrics")

#: 样本量门槛（对齐口径纪律：每能力 ≥20 条同类轨迹；低于此只披露不考核）
MIN_SAMPLES_FOR_ASSESSMENT = 20

#: 度量状态
STATUS_OK = "ok"
STATUS_INSUFFICIENT = "insufficient_samples"
STATUS_FRAMEWORK = "framework_only"
STATUS_UNAVAILABLE = "source_unavailable"
#: R4：委派行**跨判定主体**（mechanical / llm 混在一起）⇒ 不产出混合率
STATUS_MIXED = "mixed_signal_kinds"

#: 判定主体（R4 两列口径；标签与 `agent/subagent/mechanical.py` 同词表）
SIGNAL_KIND_MECHANICAL = "mechanical"
SIGNAL_KIND_LLM = "llm"
SIGNAL_KIND_UNLABELED = "unlabeled"
#: 两列（**严禁混算**；`unlabeled` 单独成列，不进任何一列）
SIGNAL_KIND_COLUMNS: Tuple[str, ...] = (SIGNAL_KIND_MECHANICAL, SIGNAL_KIND_LLM,
                                        SIGNAL_KIND_UNLABELED)

#: 灰度台账目录环境变量（S3-03 交付 `ShadowLedger` 的默认落点）
SHADOW_DIR_ENV = "CP_SHADOW_DIR"


@dataclass(frozen=True)
class MetricSpec:
    """§6.7 指标字典条目（定义 / 公式 / 数据源 / 目标 / 是否可计算）"""

    key: str
    name: str
    definition: str
    formula: str
    source: str
    target: str
    unit: str = ""
    computable: bool = True
    phase: str = ""
    owner: str = ""
    disclosure: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key, "name": self.name, "definition": self.definition,
            "formula": self.formula, "source": self.source, "target": self.target,
            "unit": self.unit, "computable": self.computable, "phase": self.phase,
            "owner": self.owner, "disclosure": self.disclosure,
        }


METRIC_SPECS: Tuple[MetricSpec, ...] = (
    MetricSpec(
        key="digest_throughput",
        name="消化吞吐",
        definition="窗口内新达成 mirrored 的能力数（日增 mirrored 能力，折算为每周）",
        formula="count(distinct capability_id | digest.stage.applied=True 且 to_stage='mirrored') × 7 / 窗口天数",
        source="agent.observability.events（EV_DIGEST_STAGE，S3-01/S3-02 写入）",
        target="≥2/周（W10 起）",
        unit="能力/周",
        phase="W10+",
        owner="S5-02",
    ),
    MetricSpec(
        key="internalization_rate",
        name="内化转化率",
        definition="(internalized + native) 能力数 / 总能力数",
        formula="(n_internalized + n_native) / n_total",
        source="agent.descriptors.registry.DescriptorRegistry（capability 台账 stage 字段）",
        target="≥10%",
        unit="比率",
        owner="S5-02",
    ),
    MetricSpec(
        key="delegation_recovery",
        name="委派回收率",
        definition="回收三件套（产物 + 轨迹 + 反思）齐全的委派占比（**机械 / LLM 两列**）",
        formula=("两列分别计算：count(三件套齐全 ∧ signal_kind=k) / count(signal_kind=k)"
                 "（k ∈ {mechanical, llm}）；**跨列不得合并**"),
        source="委派行契约（见 delegation_recovery_contract()）：产物/轨迹/反思三源齐全性"
               " + 复评半边的 signal_kind（R4）",
        target="100%",
        unit="比率",
        computable=False,
        owner="S4-04（委派链路）+ S5-02（口径）+ S7-06（R4 两列）",
        disclosure=("委派链路归 S4-04（未交付），当前无真实委派数据源；"
                    "**R4 起按判定主体分两列披露**：机械列（①产物结构/②测试 fail→pass/"
                    "③副作用核对/④可回放性）与 LLM 兜底列**严禁混算** —— 把 LLM 判定的"
                    "成功算进机械成功率等于谎报证据强度；行未标 signal_kind 时单独记"
                    "`unlabeled` 列，不并入任一列"),
    ),
    MetricSpec(
        key="delegation_success_rate",
        name="委派成功率",
        definition="复评半边判定为通过的委派占比（**机械 / LLM 两列**，R4）",
        formula=("两列分别计算：count(review_passed=True ∧ signal_kind=k) / "
                 "count(已判定 ∧ signal_kind=k)（k ∈ {mechanical, llm}）；"
                 "**跨列不得合并**"),
        source="委派行契约的 `review_passed`（或 reflection.cloudpivot_review.passed）"
               "+ `signal_kind`（R4）",
        target="披露不考核（样本 < 20 只披露）",
        unit="比率",
        owner="S5-02（口径）+ S7-06（R4 两列）",
        disclosure=("机械信号清单见 `delegation_signal_dictionary()`（单一事实源在 "
                    "`agent/subagent/mechanical.py`）；两列**严禁混算**，"
                    "且行未标 signal_kind 时记 `unlabeled` 列（不当机械、也不当 LLM）"),
    ),
    MetricSpec(
        key="skill_success_rate",
        name="技能成功率",
        definition="灰度期候选通过率（与上游通过率对比）",
        formula="Σpassed / Σsampled（ShadowLedger 行）；上游基准 = 上游臂通过率",
        source="agent.digestion.shadow.ShadowLedger.rows()（S3-03 交付）",
        target="≥上游 × 0.98",
        unit="比率",
        owner="S5-02",
    ),
    MetricSpec(
        key="healing_latency",
        name="MTTD / MTTR",
        definition="故障发现时间（MTTD）与恢复时间（MTTR）",
        formula="median(healing.triggered.mttd_ms) / median(healing.triggered.mttr_ms)",
        source="agent.observability.events（EV_HEALING_TRIGGERED，S4-03 归口的发射方）",
        target="<3s / <30s",
        unit="ms",
        computable=False,
        owner="S4-03（自愈与 Saga）",
        disclosure=("`healing.triggered` 事件类型已登记（events.v1）但**当前无发射方**"
                    "（自愈链路归 S4-03）；缺数据时返回 framework_only，不填 0"),
    ),
    MetricSpec(
        key="approval_decay_rate",
        name="审批衰减率",
        definition="审批面中由策略自动消化（auto_pass）的占比",
        formula="count(approval.kind='auto_pass') / count(approval)",
        source="agent.observability.events（EV_APPROVAL，S2-03 埋点）",
        target="≥70%",
        unit="比率",
        owner="S5-02",
    ),
    MetricSpec(
        key="delegation_cycle_days",
        name="委派中位周期",
        definition="能力从 borrowed 到 internalized 的天数中位数",
        formula="median(internalized.ts − borrowed.ts)（按 capability_id 配对）",
        source="agent.observability.events（EV_DIGEST_STAGE 的 stage 迁移时间戳）",
        target="≤14 天",
        unit="天",
        owner="S5-02",
    ),
    MetricSpec(
        key="abandoned_rate",
        name="弃单率",
        definition="abandoned 任务 / 全部任务（含 explore/consult）",
        formula="count(task.abandoned) / count(task.closed + task.abandoned)",
        source="agent.observability.events（EV_TASK_CLOSED / EV_TASK_ABANDONED）",
        target="披露不考核",
        unit="比率",
        owner="S5-02",
    ),
    MetricSpec(
        key="routing_accuracy",
        name="路由准确率",
        definition="Router 选择与事后最优一致率",
        formula="count(router_choice == hindsight_best) / count(已标注决策)",
        source="事后标注行契约（人工裁定 / 结果回测，见 routing_annotation_contract()）",
        target=">85%（W10 起）",
        unit="比率",
        computable=False,
        owner="S5-02（框架）+ 后续标注源",
        disclosure=("事后最优尚无标注源（设计文档要求人工/结果回测标注）；"
                    "本任务交付标注契约与计算函数，未标注时返回 framework_only"),
    ),
    MetricSpec(
        key="exploration_satisfaction",
        name="探索满意度（S2-03 遗留 #4 正式口径）",
        definition="explore/consult 任务的 👍率 与 任务闭环率（双列，均不纳入 ACR 考核）",
        formula="👍率 = like/(like+dislike)；闭环率 = closed/(closed+failed)",
        source="👍率：agent.feedback.FeedbackManager.get_feedback_summary()；闭环率：acr.acr_summary()['exploration']",
        target="披露不考核",
        unit="比率",
        owner="S5-02",
        disclosure=("闭环率是**代理口径**（原实现），👍率是直接信号；二者并列披露，"
                    "替代关系为「👍率为主 + 闭环率兜底」，任一口径都不得单独用于考核"),
    ),
    MetricSpec(
        key="utc_cents_per_task",
        name="UTC（单位任务成本，辅助指标）",
        definition="窗口内归一化成本 / 任务数（closed+failed）",
        formula="cost_normalized_cents / (closed + failed)",
        source="agent.observability.utc.utc_window()（S2-03 交付，与 S5-03 同源）",
        target="由 S5-03 依 §6.3 阶段阈值设定",
        unit="cents/任务",
        owner="S5-03（阈值）+ S5-02（口径透传）",
        disclosure="本指标非 §6.7 条目，作为 UTC 刹车（S5-03）的数据源透传展示",
    ),
)

#: key → spec（查表用）
METRIC_BY_KEY: Dict[str, MetricSpec] = {spec.key: spec for spec in METRIC_SPECS}


def metric_dictionary() -> List[Dict[str, Any]]:
    """§6.7 指标字典（定义 + 公式 + 数据源 + 目标 + 可计算性），供周报/面板直接引用"""
    return [spec.to_dict() for spec in METRIC_SPECS]


def computable_metrics() -> List[str]:
    """当前**可计算**的指标 key 清单（验收要求 ≥5 项）"""
    return [spec.key for spec in METRIC_SPECS if spec.computable]


# ════════════════════════════════════════════════════════════
#  行契约（尚未落地的数据源，先定义契约）
# ════════════════════════════════════════════════════════════


def delegation_recovery_contract() -> Dict[str, Any]:
    """委派回收率/成功率的数据源契约（S4-04 交付后按此填充即可计算）

    一行 = 一次委派（把一类工作交给上游/子智能体）：
    ``{"delegation_id", "capability_id", "artifact": bool|dict, "trace": bool|str,
    "reflection": bool|dict}`` —— §3.9「回收三件套：产物 + 轨迹 + 反思」。

    **R4 增补（可选，但决定该行进哪一列）**：

    - ``signal_kind``：``mechanical`` / ``llm``（复评半边的判定主体；取值来自
      `agent.subagent.mechanical`）。缺失即 ``unlabeled`` —— **不并入任一列**；
    - ``review_passed``：复评半边的判定结论（布尔）；缺失即该行不计入成功率分母。
    """
    return {
        "rows": "Sequence[Mapping]",
        "required": ["delegation_id", "capability_id"],
        "triple_fields": ["artifact", "trace", "reflection"],
        "optional": ["signal_kind", "review_passed"],
        "signal_kinds": list(SIGNAL_KIND_COLUMNS),
        "source_owner": "S4-04（subagent 真实现）",
        "note": "三件套缺任一 → 该次委派不计回收（§3.9：缺一不计成本核算、视为浪费）",
        "column_rule": ("判定主体两列**严禁混算**：机械列 = ①产物结构 / ②测试 fail→pass / "
                        "③副作用核对 / ④可回放性；LLM 列 = 兜底复评（①-④ 全不适用）；"
                        "未标注单列 `unlabeled`。跨列合并的比率**不产出**"),
    }


def delegation_signal_dictionary() -> List[Dict[str, Any]]:
    """委派成功率的**机械信号清单**（R4；单一事实源在 `agent/subagent/mechanical.py`）

    本函数只**引用**该清单（不复制一份），保证"指标字典里的定义"与"复评时执行的代码"
    逐字一致；模块不可用时如实返回错误项（不静默给空表）。
    """
    try:
        from agent.subagent.mechanical import mechanical_signal_catalog
        return mechanical_signal_catalog()
    except Exception as e:  # noqa: BLE001 清单不可用 ⇒ 如实标注
        return [{"error": f"机械信号清单不可用: {type(e).__name__}: {e}"}]


def delegation_signal_kind(row: Mapping[str, Any]) -> str:
    """一行委派的判定主体（R4）：``mechanical`` / ``llm`` / ``unlabeled``"""
    kind = str(row.get("signal_kind") or "").strip().lower()
    if kind in (SIGNAL_KIND_MECHANICAL, SIGNAL_KIND_LLM):
        return kind
    # 兼容：嵌套 reflection 里带了 signal_kind（Subagent 三件套的原样行）
    reflection = row.get("reflection")
    if isinstance(reflection, Mapping):
        nested = str(reflection.get("signal_kind") or "").strip().lower()
        if nested in (SIGNAL_KIND_MECHANICAL, SIGNAL_KIND_LLM):
            return nested
    return SIGNAL_KIND_UNLABELED


def delegation_review_passed(row: Mapping[str, Any]) -> Optional[bool]:
    """一行的复评结论（``review_passed`` 优先，其次嵌套 ``cloudpivot_review.passed``）"""
    if isinstance(row.get("review_passed"), bool):
        return bool(row["review_passed"])
    reflection = row.get("reflection")
    if isinstance(reflection, Mapping):
        review = reflection.get("cloudpivot_review")
        if isinstance(review, Mapping) and isinstance(review.get("passed"), bool):
            return bool(review["passed"])
    return None


def _signal_columns(delegations: Optional[Sequence[Mapping[str, Any]]]) -> Dict[str, Any]:
    """按判定主体分列计算（R4；**每列独立算，跨列不合并**）

    每列给出：``samples``（该列委派数）、``recovered/completed``（三件套齐全）、
    ``recovery_rate``、``judged/passed``（已判定/判定通过）、``success_rate``、
    ``status``（样本 < 20 ⇒ ``insufficient_samples``，只披露不考核）。
    """
    buckets: Dict[str, List[Mapping[str, Any]]] = {k: [] for k in SIGNAL_KIND_COLUMNS}
    for row in delegations or []:
        buckets[delegation_signal_kind(row)].append(row)
    columns: Dict[str, Dict[str, Any]] = {}
    for kind in SIGNAL_KIND_COLUMNS:
        rows = buckets[kind]
        samples = len(rows)
        complete = sum(1 for r in rows
                       if all(bool(r.get(f)) for f in ("artifact", "trace", "reflection")))
        judged = 0
        passed = 0
        for r in rows:
            verdict = delegation_review_passed(r)
            if verdict is None:
                continue
            judged += 1
            passed += 1 if verdict else 0
        status = STATUS_OK
        if samples == 0:
            status = STATUS_UNAVAILABLE
        elif samples < MIN_SAMPLES_FOR_ASSESSMENT:
            status = STATUS_INSUFFICIENT
        recovery = round(complete / samples, 6) if samples else None
        success = round(passed / judged, 6) if judged else None
        columns[kind] = {
            "signal_kind": kind, "samples": samples,
            "recovered": complete, "recovery_rate": recovery,
            "recovery_target_met": (None if recovery is None else recovery >= 1.0),
            "judged": judged, "passed": passed, "success_rate": success,
            "status": status, "min_samples": MIN_SAMPLES_FOR_ASSESSMENT,
            "assessment": ("只披露不考核（样本 < "
                           f"{MIN_SAMPLES_FOR_ASSESSMENT}）" if status == STATUS_INSUFFICIENT
                           else ("无样本" if status == STATUS_UNAVAILABLE else "可考核")),
            "source": ("三件套齐全性 + signal_kind"
                       if kind != SIGNAL_KIND_UNLABELED else
                       "未标注 signal_kind 的委派行（**不并入任一列**）"),
        }
    labeled = [k for k in (SIGNAL_KIND_MECHANICAL, SIGNAL_KIND_LLM)
               if columns[k]["samples"] > 0]
    unlabeled = columns[SIGNAL_KIND_UNLABELED]["samples"]
    mixed = bool(len(labeled) >= 2 or (labeled and unlabeled))
    return {
        "columns": columns,
        "labeled_kinds": labeled,
        "mixed": mixed,
        "assessment": {
            "basis": "columns",
            "rule": ("两列分别计算、分别考核；**跨列合并的比率不产出**"
                     "（把 LLM 判定的成功算进机械成功率等于谎报证据强度）"),
            "mechanical_signals": list(SIGNAL_KIND_COLUMNS),
        },
    }


def routing_annotation_contract() -> Dict[str, Any]:
    """路由准确率的事后标注契约（人工/结果回测）

    一行 = 一条已标注的路由决策：
    ``{"task_id", "chosen", "hindsight_best", "annotator": "human"|"replay",
    "annotated_at"}``；``chosen == hindsight_best`` 即一致。
    """
    return {
        "rows": "Sequence[Mapping]",
        "required": ["task_id", "chosen", "hindsight_best"],
        "annotators": ["human", "replay"],
        "note": ("设计文档要求「shadow 事后标注」；本任务先交付契约与计算函数，"
                 "标注源接入后即可计算；未标注时不得声称已有路由准确率"),
    }


# ════════════════════════════════════════════════════════════
#  单指标计算
# ════════════════════════════════════════════════════════════


def _metric(key: str, *, value: Optional[float], numerator: Any = None,
            denominator: Any = None, samples: int = 0,
            status: str = STATUS_OK, extra: Optional[Dict[str, Any]] = None
            ) -> Dict[str, Any]:
    """组装一条指标结果（**必然**带 source/formula/target/samples）"""
    spec = METRIC_BY_KEY[key]
    row: Dict[str, Any] = {
        "key": spec.key, "name": spec.name, "value": value, "unit": spec.unit,
        "numerator": numerator, "denominator": denominator, "samples": int(samples),
        "status": status, "definition": spec.definition, "formula": spec.formula,
        "source": spec.source, "target": spec.target, "computable": spec.computable,
        "min_samples": MIN_SAMPLES_FOR_ASSESSMENT,
        "target_met": None,
    }
    if spec.disclosure:
        row["disclosure"] = spec.disclosure
    if extra:
        row.update(extra)
    if row.get("target_met") is None and value is not None:
        row["target_met"] = _target_met(spec.key, value)
    if status == STATUS_OK and 0 < samples < MIN_SAMPLES_FOR_ASSESSMENT:
        row["status"] = STATUS_INSUFFICIENT
    return row


def _target_met(key: str, value: float) -> Optional[bool]:
    """目标达成判定（**只**对本任务定义的目标；披露类指标返回 None）"""
    if key == "digest_throughput":
        return value >= 2.0
    if key == "internalization_rate":
        return value >= 0.10
    if key == "delegation_recovery":
        return value >= 1.0
    if key == "approval_decay_rate":
        return value >= 0.70
    if key == "delegation_cycle_days":
        return value <= 14.0
    if key == "routing_accuracy":
        return value > 0.85
    return None


def compute_digest_throughput(rows: Sequence[EventEnvelope], *,
                              window_days: int = 7) -> Dict[str, Any]:
    """消化吞吐：窗口内新达成 mirrored 的能力数 → 折算每周"""
    span = max(1, int(window_days))
    caps = {
        str((env.payload or {}).get("capability_id") or "")
        for env in rows
        if env.type == EV_DIGEST_STAGE
        and bool((env.payload or {}).get("applied"))
        and str((env.payload or {}).get("to_stage") or "") == "mirrored"
    }
    caps.discard("")
    migrations = sum(1 for env in rows if env.type == EV_DIGEST_STAGE)
    per_week = round(len(caps) * 7.0 / span, 6)
    # 观测基数 = 窗口内 digest.stage 事件数：一个迁移事件都没有 → 证据不足（而非"0 吞吐"）
    return _metric("digest_throughput", value=per_week if migrations else None,
                   numerator=len(caps), denominator=span, samples=migrations,
                   status=STATUS_OK if migrations else STATUS_INSUFFICIENT,
                   extra={"window_days": span, "capabilities": sorted(caps)[:50],
                          "digest_stage_events": migrations,
                          "normalization": "×7/窗口天数",
                          **({} if migrations else {
                              "reason": "窗口内无 digest.stage 事件 → 无观测基数，"
                                        "不以 0 冒充吞吐"})})


def compute_internalization_rate(registry: Any = None, *,
                                 registry_path: str = "") -> Dict[str, Any]:
    """内化转化率：(internalized + native) / 总能力（源：descriptor 台账）"""
    if registry is None:
        try:
            from agent.descriptors.registry import DescriptorRegistry
            registry = DescriptorRegistry(path=registry_path or None, autosave=False)
        except Exception as e:  # noqa: BLE001 台账不可用 → 如实标注
            return _metric("internalization_rate", value=None, status=STATUS_UNAVAILABLE,
                           extra={"reason": f"descriptor 台账不可用: {type(e).__name__}: {e}"})
    counters: Dict[str, int] = {}
    try:
        for descriptor in registry.list():
            stage = getattr(getattr(descriptor, "evolution", None), "stage", None)
            name = getattr(stage, "value", stage) or "unknown"
            counters[str(name)] = counters.get(str(name), 0) + 1
    except Exception as e:  # noqa: BLE001
        return _metric("internalization_rate", value=None, status=STATUS_UNAVAILABLE,
                       extra={"reason": f"台账读取失败: {type(e).__name__}: {e}"})
    total = sum(counters.values())
    internalized = counters.get("internalized", 0) + counters.get("native", 0)
    if not total:
        return _metric("internalization_rate", value=None, numerator=0, denominator=0,
                       samples=0, status=STATUS_UNAVAILABLE,
                       extra={"by_stage": counters,
                              "reason": "capability 台账为空（无能力登记）"})
    return _metric("internalization_rate", value=round(internalized / total, 6),
                   numerator=internalized, denominator=total, samples=total,
                   extra={"by_stage": dict(sorted(counters.items()))})


def compute_delegation_recovery(delegations: Optional[Sequence[Mapping[str, Any]]]) -> Dict[str, Any]:
    """委派回收率：三件套齐全占比（数据源缺位时 framework_only，不填 0）

    **R4 两列口径**：结果按判定主体分 ``columns.mechanical`` / ``columns.llm``
    （外加 ``columns.unlabeled``），**每列独立计算**。当行**跨判定主体**（同时存在
    mechanical 与 llm，或已标注行与未标注行并存）时，顶层 ``value`` 置 ``None`` 且
    ``status=mixed_signal_kinds`` —— 混合比率**不产出**（不得混算）。

    兼容性：未标 ``signal_kind`` 的行走既有路径（顶层数字与 S5-02/S6-01 逐字一致），
    仅**新增** ``columns`` / ``mixed`` / ``assessment`` / ``signals`` 字段。
    """
    if delegations is None:
        return _metric("delegation_recovery", value=None, status=STATUS_FRAMEWORK,
                       extra={"contract": delegation_recovery_contract(),
                              "signals": delegation_signal_dictionary(),
                              "columns": {k: {"signal_kind": k, "samples": 0,
                                              "status": STATUS_FRAMEWORK}
                                          for k in SIGNAL_KIND_COLUMNS},
                              "mixed": False,
                              "assessment": {"basis": "columns",
                                             "rule": "两列分别计算，跨列不合并"}})
    total = 0
    complete = 0
    incomplete: List[str] = []
    for row in delegations:
        total += 1
        triple = [row.get(field) for field in ("artifact", "trace", "reflection")]
        if all(bool(item) for item in triple):
            complete += 1
        else:
            incomplete.append(str(row.get("delegation_id") or row.get("capability_id") or "?"))
    value = round(complete / total, 6) if total else None
    split = _signal_columns(delegations)
    extra: Dict[str, Any] = {"incomplete": incomplete[:20],
                             "contract": delegation_recovery_contract(),
                             "signals": delegation_signal_dictionary(),
                             "columns": split["columns"],
                             "labeled_kinds": split["labeled_kinds"],
                             "mixed": split["mixed"],
                             "assessment": split["assessment"]}
    row = _metric("delegation_recovery", value=value, numerator=complete,
                  denominator=total, samples=total,
                  status=STATUS_OK if total else STATUS_FRAMEWORK,
                  extra=extra)
    if split["mixed"] and total:
        # **不混算**：跨判定主体时不产出合并率（各列数字见 columns）
        row["value"] = None
        row["numerator"] = None
        row["denominator"] = None
        row["status"] = STATUS_MIXED
        row["target_met"] = None
        row["reason"] = ("窗口内委派跨判定主体（mechanical / llm / unlabeled 并存）"
                         "⇒ **不产出合并回收率**；请按 columns 分列读取"
                         "（机械与 LLM 判定严禁混算）")
        row["disclosure"] = (str(row.get("disclosure") or "") + " ｜ 本次为**混合样本**："
                             "顶层 value 置空以强制分列读取").strip()
    return row


def compute_delegation_success_rate(
        delegations: Optional[Sequence[Mapping[str, Any]]]) -> Dict[str, Any]:
    """委派成功率：按判定主体分两列（R4；**跨列不合并**）

    - 分子/分母只用**已判定**（``review_passed`` 可读）的行；
    - 样本 < ``MIN_SAMPLES_FOR_ASSESSMENT`` ⇒ ``insufficient_samples``（只披露不考核）；
    - 顶层 ``value`` 取 ``mechanical`` 列（**机械优先**），并显式标注
      ``value_basis="mechanical"``；机械列无样本时才回落 ``llm`` 列 —— 两种情况下
      ``columns`` 与 ``mixed`` 始终并列给出，任何单一数字都可追溯到列。
    """
    if delegations is None:
        return _metric("delegation_success_rate", value=None, status=STATUS_FRAMEWORK,
                       extra={"contract": delegation_recovery_contract(),
                              "signals": delegation_signal_dictionary(),
                              "columns": {k: {"signal_kind": k, "samples": 0,
                                              "status": STATUS_FRAMEWORK}
                                          for k in SIGNAL_KIND_COLUMNS},
                              "mixed": False, "value_basis": ""})
    split = _signal_columns(delegations)
    columns = split["columns"]
    basis = SIGNAL_KIND_MECHANICAL if columns[SIGNAL_KIND_MECHANICAL]["judged"] else (
        SIGNAL_KIND_LLM if columns[SIGNAL_KIND_LLM]["judged"] else "")
    value = columns[basis]["success_rate"] if basis else None
    samples = columns[basis]["judged"] if basis else 0
    status = STATUS_OK if basis else STATUS_FRAMEWORK
    extra = {
        "contract": delegation_recovery_contract(),
        "signals": delegation_signal_dictionary(),
        "columns": columns, "labeled_kinds": split["labeled_kinds"],
        "mixed": split["mixed"], "assessment": split["assessment"],
        "value_basis": basis,
        "value_basis_note": ("顶层 value 仅取**单列**（机械优先）；跨列合并的比率不产出"
                             if basis else "两列均无已判定样本 ⇒ value 记 None"),
    }
    row = _metric("delegation_success_rate", value=value,
                  numerator=columns[basis]["passed"] if basis else None,
                  denominator=samples or None, samples=samples, status=status,
                  extra=extra)
    row["target_met"] = None      # 披露不考核（样本门槛由 status 承担）
    return row


def compute_skill_success_rate(ledger_rows: Sequence[Mapping[str, Any]], *,
                               upstream_rate: Optional[float] = None) -> Dict[str, Any]:
    """技能成功率：灰度台账 Σpassed/Σsampled；与上游 ×0.98 对比"""
    passed = 0
    sampled = 0
    for row in ledger_rows:
        try:
            passed += int(row.get("passed") or 0)
            sampled += int(row.get("sampled") or 0)
        except (TypeError, ValueError):
            continue
    if not sampled:
        return _metric("skill_success_rate", value=None, numerator=passed,
                       denominator=0, samples=0, status=STATUS_UNAVAILABLE,
                       extra={"reason": "灰度台账为空（无 shadow 运行记录）",
                              "ledger_rows": len(ledger_rows)})
    value = round(passed / sampled, 6)
    extra: Dict[str, Any] = {"ledger_rows": len(ledger_rows)}
    if upstream_rate is not None:
        extra["upstream_rate"] = upstream_rate
        extra["required"] = round(float(upstream_rate) * 0.98, 6)
        extra["delta_vs_required"] = round(value - float(upstream_rate) * 0.98, 6)
    else:
        extra["upstream_rate"] = None
        extra["disclosure"] = ("缺少上游通过率标注 → 只披露候选通过率，"
                               "「≥上游×0.98」暂不可判定")
    row = _metric("skill_success_rate", value=value, numerator=passed,
                  denominator=sampled, samples=sampled, extra=extra)
    if row.get("upstream_rate") is not None:
        row["target_met"] = value >= float(row["required"])
    return row


def compute_healing_latency(rows: Sequence[EventEnvelope]) -> Dict[str, Any]:
    """MTTD / MTTR：来自 `healing.triggered`（无发射方时 framework_only）"""
    mttd: List[float] = []
    mttr: List[float] = []
    for env in rows:
        if env.type != EV_HEALING_TRIGGERED:
            continue
        payload = env.payload or {}
        for bucket, field_name in ((mttd, "mttd_ms"), (mttr, "mttr_ms")):
            try:
                value = payload.get(field_name)
                if value is not None:
                    bucket.append(float(value))
            except (TypeError, ValueError):
                continue
    if not mttd and not mttr:
        return _metric("healing_latency", value=None, status=STATUS_FRAMEWORK,
                       extra={"mttd_ms": None, "mttr_ms": None,
                              "events": 0,
                              "reason": "无 healing.triggered 事件（发射方归 S4-03）"})
    return _metric("healing_latency", value=None, samples=len(mttd) or len(mttr),
                   status=STATUS_OK,
                   extra={"mttd_ms": round(statistics.median(mttd), 3) if mttd else None,
                          "mttr_ms": round(statistics.median(mttr), 3) if mttr else None,
                          "mttd_target_met": (statistics.median(mttd) < 3000.0) if mttd else None,
                          "mttr_target_met": (statistics.median(mttr) < 30000.0) if mttr else None})


def compute_approval_decay_rate(rows: Sequence[EventEnvelope]) -> Dict[str, Any]:
    """审批衰减率：approval 事件中 auto_pass 占比"""
    total = 0
    auto = 0
    by_kind: Dict[str, int] = {}
    for env in rows:
        if env.type != EV_APPROVAL:
            continue
        total += 1
        kind = str((env.payload or {}).get("kind") or "")
        by_kind[kind] = by_kind.get(kind, 0) + 1
        if kind == ACR.KIND_AUTO_PASS:
            auto += 1
    if not total:
        return _metric("approval_decay_rate", value=None, numerator=0, denominator=0,
                       samples=0, status=STATUS_UNAVAILABLE,
                       extra={"reason": "窗口内无 approval 事件"})
    return _metric("approval_decay_rate", value=round(auto / total, 6), numerator=auto,
                   denominator=total, samples=total,
                   extra={"by_kind": dict(sorted(by_kind.items()))})


def compute_delegation_cycle_days(rows: Sequence[EventEnvelope]) -> Dict[str, Any]:
    """委派中位周期：同一能力 borrowed → internalized 的天数中位数"""
    borrowed: Dict[str, str] = {}
    internalized: Dict[str, str] = {}
    for env in rows:
        if env.type != EV_DIGEST_STAGE:
            continue
        payload = env.payload or {}
        if not bool(payload.get("applied")):
            continue
        cap = str(payload.get("capability_id") or "")
        if not cap:
            continue
        to_stage = str(payload.get("to_stage") or "")
        day = str(env.ts or "")[:10]
        if to_stage == "borrowed" and cap not in borrowed:
            borrowed[cap] = day
        elif to_stage == "internalized" and cap not in internalized:
            internalized[cap] = day
    spans: List[float] = []
    pairs: List[Dict[str, Any]] = []
    for cap, end_day in sorted(internalized.items()):
        start_day = borrowed.get(cap)
        if not start_day:
            continue
        try:
            start = date.fromisoformat(start_day)
            end = date.fromisoformat(end_day)
        except ValueError:
            continue
        days = float((end - start).days)
        spans.append(days)
        pairs.append({"capability_id": cap, "borrowed": start_day,
                      "internalized": end_day, "days": days})
    if not spans:
        return _metric("delegation_cycle_days", value=None, samples=0,
                       status=STATUS_UNAVAILABLE,
                       extra={"reason": "窗口内无成对的 borrowed→internalized 迁移",
                              "pairs": []})
    return _metric("delegation_cycle_days", value=round(statistics.median(spans), 3),
                   numerator=round(sum(spans), 3), denominator=len(spans),
                   samples=len(spans), extra={"pairs": pairs[:20]})


def compute_abandoned_rate(rows: Sequence[EventEnvelope]) -> Dict[str, Any]:
    """弃单率：abandoned / (closed + failed + abandoned)"""
    closed = failed = abandoned = 0
    seen: Dict[str, str] = {}
    for env in rows:
        if env.type not in (EV_TASK_CLOSED, EV_TASK_ABANDONED):
            continue
        payload = env.payload or {}
        task_id = str(payload.get("task_id") or env.correlation_id or "")
        status = str(payload.get("status") or ("abandoned" if env.type == EV_TASK_ABANDONED
                                              else "closed"))
        if task_id and task_id in seen:
            continue
        if task_id:
            seen[task_id] = status
        if status == "abandoned":
            abandoned += 1
        elif status == "failed":
            failed += 1
        else:
            closed += 1
    total = closed + failed + abandoned
    if not total:
        return _metric("abandoned_rate", value=None, samples=0, status=STATUS_UNAVAILABLE,
                       extra={"reason": "窗口内无任务收尾事件（task.closed/task.abandoned）"})
    return _metric("abandoned_rate", value=round(abandoned / total, 6), numerator=abandoned,
                   denominator=total, samples=total,
                   extra={"closed": closed, "failed": failed, "abandoned": abandoned})


def compute_routing_accuracy(annotations: Optional[Sequence[Mapping[str, Any]]]) -> Dict[str, Any]:
    """路由准确率：已标注决策中 chosen == hindsight_best 的占比"""
    if annotations is None:
        return _metric("routing_accuracy", value=None, status=STATUS_FRAMEWORK,
                       extra={"contract": routing_annotation_contract()})
    total = 0
    hit = 0
    by_annotator: Dict[str, int] = {}
    for row in annotations:
        chosen = row.get("chosen")
        best = row.get("hindsight_best")
        if chosen is None or best is None:
            continue
        total += 1
        if str(chosen) == str(best):
            hit += 1
        annotator = str(row.get("annotator") or "unknown")
        by_annotator[annotator] = by_annotator.get(annotator, 0) + 1
    value = round(hit / total, 6) if total else None
    return _metric("routing_accuracy", value=value, numerator=hit, denominator=total,
                   samples=total, status=STATUS_OK if total else STATUS_FRAMEWORK,
                   extra={"by_annotator": dict(sorted(by_annotator.items())),
                          "contract": routing_annotation_contract()})


def compute_exploration_satisfaction(*, acr_summary: Optional[Mapping[str, Any]] = None,
                                     feedback_summary: Optional[Mapping[str, Any]] = None,
                                     ) -> Dict[str, Any]:
    """探索满意度（S2-03 遗留 #4 正式口径）：👍率 + 任务闭环率**双列**

    * 闭环率 = ``acr_summary["exploration"]["satisfaction"]``（closed/(closed+failed)）；
    * 👍率 = ``feedback_summary`` 的 like/(like+dislike)（源 `agent.feedback`）。

    两列并列披露，且显式声明替代关系（闭环率是代理、👍率是直接信号）。
    """
    closure_rate: Optional[float] = None
    closure_basis = ""
    explore_tasks = 0
    if acr_summary:
        exploration = dict(acr_summary.get("exploration") or {})
        closure_rate = exploration.get("satisfaction")
        closure_basis = str(exploration.get("satisfaction_basis") or "")
        explore_tasks = int(exploration.get("tasks") or 0)
    likes = dislikes = 0
    thumbs_rate: Optional[float] = None
    feedback_available = feedback_summary is not None
    if feedback_summary:
        try:
            likes = int(feedback_summary.get("like_count") or 0)
            dislikes = int(feedback_summary.get("dislike_count") or 0)
        except (TypeError, ValueError):
            likes = dislikes = 0
        votes = likes + dislikes
        thumbs_rate = round(likes / votes, 6) if votes else None
    samples = explore_tasks or (likes + dislikes)
    status = STATUS_OK
    extra: Dict[str, Any] = {
        "thumbs_up_rate": thumbs_rate,
        "thumbs_up_numerator": likes,
        "thumbs_up_denominator": likes + dislikes,
        "closure_rate": closure_rate,
        "closure_basis": closure_basis or "closure_success_ratio（关闭成功/(关闭成功+失败)）",
        "explore_tasks": explore_tasks,
        "feedback_available": feedback_available,
        "substitution": ("正式口径 = 👍率（直接信号，源 agent.feedback）与任务闭环率（代理指标，"
                         "源 acr.exploration）**并列双列**；闭环率单独使用会把"
                         "「没被投诉的沉默放弃」算成满意，故不得单独用于考核"),
        "scope": "explore/consult 任务排除在 ACR 分母之外；本指标披露不考核（P7.1-16）",
    }
    if thumbs_rate is None and closure_rate is None:
        status = STATUS_UNAVAILABLE
        extra["reason"] = ("两列均无数据：👍率需反馈库（agent.feedback），"
                           "闭环率需窗口内 explore/consult 任务收尾事件")
        samples = 0
    elif not feedback_available:
        extra["reason"] = ("未提供反馈汇总 → 👍率列为空（**不填 0 冒充**）；"
                          "闭环率列照常披露")
    return _metric("exploration_satisfaction", value=thumbs_rate,
                   numerator=likes, denominator=likes + dislikes, samples=samples,
                   status=status, extra=extra)


def compute_utc(utc_summary: Optional[Mapping[str, Any]], *, window_days: int = 7
                ) -> Dict[str, Any]:
    """UTC（单位任务成本）：直接透传 `utc.utc_window()` 的结果（不重算）"""
    if not utc_summary:
        return _metric("utc_cents_per_task", value=None, status=STATUS_UNAVAILABLE,
                       extra={"reason": "未提供 utc_window 汇总"})
    tasks = dict(utc_summary.get("tasks") or {})
    samples = int(tasks.get("closed_and_failed") or 0)
    value = utc_summary.get("utc_cents_per_task")
    return _metric("utc_cents_per_task", value=value,
                   numerator=utc_summary.get("cost_normalized_cents"),
                   denominator=samples or None, samples=samples,
                   status=STATUS_OK if value is not None else STATUS_UNAVAILABLE,
                   extra={"window_days": window_days,
                          "utc_formula": utc_summary.get("utc_formula"),
                          "anchor_model": utc_summary.get("anchor_model"),
                          "cache_hit_rate": utc_summary.get("cache_hit_rate")})


# ════════════════════════════════════════════════════════════
#  窗口解析 / 汇总 / 周报
# ════════════════════════════════════════════════════════════


def resolve_window(*, days: int = 7, start: str = "", end: str = "") -> Tuple[str, str]:
    """解析统计窗口（缺省：截至今日的 ``days`` 天，含端点）"""
    span = max(1, int(days))
    finish = str(end or date.today().isoformat())[:10]
    begin = str(start or shift_day(finish, -(span - 1)))[:10]
    return begin, finish


def compute_metrics(*, days: int = 7, start: str = "", end: str = "",
                    events_dir: Optional[str] = None,
                    window_days: Optional[int] = None,
                    registry: Any = None, registry_path: str = "",
                    shadow_dir: str = "",
                    feedback_summary: Optional[Mapping[str, Any]] = None,
                    feedback_manager: Any = None,
                    annotations: Optional[Sequence[Mapping[str, Any]]] = None,
                    delegations: Optional[Sequence[Mapping[str, Any]]] = None,
                    upstream_rate: Optional[float] = None) -> Dict[str, Any]:
    """计算全部 §6.7 指标 → 周报负载（含口径/数据源/样本量/披露）

    **落盘纪律**：本函数**不创建任何目录**；`feedback_manager` 与 `shadow_dir`
    仅在显式传入时才被访问（避免测试/自动化流程污染运行时目录）。
    """
    begin, finish = resolve_window(days=days, start=start, end=end)
    span = window_days or (
        (date.fromisoformat(finish) - date.fromisoformat(begin)).days + 1)
    rows = iter_events(since=begin, until=f"{finish}\uffff", directory=events_dir)
    acr_window = ACR.acr_summary(since=begin, until=finish, directory=events_dir)
    utc_window = UTC.utc_window(start=begin, end=finish, directory=events_dir)

    ledger_rows: List[Dict[str, Any]] = []
    shadow_source = "未提供（--shadow-dir 缺省时跳过，避免触碰运行时目录）"
    if shadow_dir:
        try:
            from agent.digestion.shadow import ShadowLedger
            ledger_rows = ShadowLedger(directory=shadow_dir).rows()
            shadow_source = f"agent.digestion.shadow.ShadowLedger(directory={shadow_dir!r}).rows()"
        except Exception as e:  # noqa: BLE001
            shadow_source = f"灰度台账不可用: {type(e).__name__}: {e}"

    if feedback_summary is None and feedback_manager is not None:
        try:
            feedback_summary = feedback_manager.get_feedback_summary(days=span)
        except Exception as e:  # noqa: BLE001
            logger.warning("反馈汇总读取失败（👍率列置空）: %s", e)
            feedback_summary = None

    metrics = {
        "digest_throughput": compute_digest_throughput(rows, window_days=span),
        "internalization_rate": compute_internalization_rate(
            registry, registry_path=registry_path),
        "delegation_recovery": compute_delegation_recovery(delegations),
        "delegation_success_rate": compute_delegation_success_rate(delegations),
        "skill_success_rate": compute_skill_success_rate(
            ledger_rows, upstream_rate=upstream_rate),
        "healing_latency": compute_healing_latency(rows),
        "approval_decay_rate": compute_approval_decay_rate(rows),
        "delegation_cycle_days": compute_delegation_cycle_days(rows),
        "abandoned_rate": compute_abandoned_rate(rows),
        "routing_accuracy": compute_routing_accuracy(annotations),
        "exploration_satisfaction": compute_exploration_satisfaction(
            acr_summary=acr_window, feedback_summary=feedback_summary),
        "utc_cents_per_task": compute_utc(utc_window, window_days=span),
    }
    return {
        "schema": "eval.slo_weekly.v1",
        "window": {"start": begin, "end": finish, "days": span},
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "events": {"directory": events_dir or "(默认 data/events)",
                   "rows": len(rows)},
        "sources": {
            "acr": "agent.observability.acr.acr_summary(since, until)",
            "utc": "agent.observability.utc.utc_window(start, end)",
            "shadow_ledger": shadow_source,
            "events": "agent.observability.events.iter_events(since, until)",
            "feedback": ("agent.feedback.FeedbackManager.get_feedback_summary()"
                         if feedback_summary is not None else "未接入（👍率列置空）"),
        },
        "metrics": metrics,
        "dictionary": metric_dictionary(),
        "delegation_signals": delegation_signal_dictionary(),
        "acr_window": {"acr": acr_window.get("acr"),
                       "denominator": acr_window.get("denominator"),
                       "exploration": acr_window.get("exploration")},
        "utc_window": {"utc_cents_per_task": utc_window.get("utc_cents_per_task"),
                       "cost_normalized_cents": utc_window.get("cost_normalized_cents"),
                       "tasks": utc_window.get("tasks"),
                       "anchor_model": utc_window.get("anchor_model")},
        "traceability": ("每个 metric 均带 source（数据源）+ formula（公式）+ "
                         "numerator/denominator（分子分母）+ samples（样本量）；"
                         "value=None 表示数据源缺位，不以 0 冒充"),
    }


def render_weekly_markdown(report: Mapping[str, Any]) -> str:
    """周报 Markdown（**逐行带数据源**；不可追溯的数字不出现）"""
    window = dict(report.get("window") or {})
    lines = [
        "# §6.7 指标周报（SLO）",
        "",
        f"- 窗口：{window.get('start')} ~ {window.get('end')}（{window.get('days')} 天）",
        f"- 生成时间：{report.get('generated_at')}",
        f"- 事件流：{dict(report.get('events') or {}).get('directory')}"
        f"（{dict(report.get('events') or {}).get('rows')} 条）",
        "- 口径纪律：每个数字都带数据源与公式；数据源缺位记 `None`（不以 0 冒充）；"
        f"样本 < {MIN_SAMPLES_FOR_ASSESSMENT} 只披露不考核",
        "",
        "| 指标 | 值 | 单位 | 目标 | 达标 | 样本 | 状态 | 数据源 | 公式 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for key, row in (report.get("metrics") or {}).items():
        value = row.get("value")
        shown = "—" if value is None else (
            f"{value:.4f}" if isinstance(value, float) else str(value))
        target_met = row.get("target_met")
        met = "—" if target_met is None else ("达标" if target_met else "未达标")
        lines.append(
            f"| {row.get('name')} | {shown} | {row.get('unit') or '—'} | "
            f"{row.get('target') or '—'} | {met} | {row.get('samples')} | "
            f"{row.get('status')} | {row.get('source')} | {row.get('formula')} |")
    highlights = [(key, row) for key, row in (report.get("metrics") or {}).items()
                  if row.get("value") is None or row.get("disclosure")
                  or row.get("status") != STATUS_OK]
    if highlights:
        lines += ["", "## 披露与未达标数据源", ""]
        for key, row in highlights:
            note = row.get("disclosure") or row.get("reason") or ""
            lines.append(f"- **{row.get('name')}**（`{key}`，status={row.get('status')}）：{note}")
    satisfaction = (report.get("metrics") or {}).get("exploration_satisfaction") or {}
    if satisfaction:
        lines += [
            "",
            "## 探索满意度（双列口径）",
            "",
            f"- 👍率 = {satisfaction.get('thumbs_up_rate')}"
            f"（{satisfaction.get('thumbs_up_numerator')}/"
            f"{satisfaction.get('thumbs_up_denominator')}，源 agent.feedback）",
            f"- 任务闭环率 = {satisfaction.get('closure_rate')}"
            f"（源 {satisfaction.get('closure_basis')}）",
            f"- 替代关系：{satisfaction.get('substitution')}",
            f"- 考核范围：{satisfaction.get('scope')}",
        ]
    metrics = report.get("metrics") or {}
    delegation = metrics.get("delegation_recovery") or {}
    success = metrics.get("delegation_success_rate") or {}
    if delegation.get("columns") or success.get("columns"):
        lines += ["", "## 委派指标（机械 / LLM **两列，严禁混算**，R4）", ""]
        for key, label, field in (("delegation_recovery", "委派回收率", "recovery_rate"),
                                  ("delegation_success_rate", "委派成功率", "success_rate")):
            row = metrics.get(key) or {}
            columns = dict(row.get("columns") or {})
            if not columns:
                continue
            lines.append(f"**{label}**（顶层 value={row.get('value')}，"
                         f"basis={row.get('value_basis') or '—'}，"
                         f"mixed={row.get('mixed')}）：")
            lines.append("")
            lines.append("| 判定主体 | 样本 | " + field + " | 状态 | 说明 |")
            lines.append("|---|---|---|---|---|")
            for kind in SIGNAL_KIND_COLUMNS:
                column = dict(columns.get(kind) or {})
                if not column:
                    continue
                lines.append(f"| {kind} | {column.get('samples')} | "
                             f"{column.get(field)} | {column.get('status')} | "
                             f"{column.get('assessment') or column.get('source') or '—'} |")
            lines.append("")
        rule = ((delegation.get("assessment") or {}).get("rule")
                or (success.get("assessment") or {}).get("rule") or "")
        lines.append(f"- 口径规则：{rule}")
        lines.append("- 机械信号清单（单一事实源 `agent/subagent/mechanical.py`）：")
        for spec in (delegation.get("signals") or report.get("delegation_signals") or []):
            if spec.get("error"):
                lines.append(f"  - ⚠ {spec['error']}")
                continue
            lines.append(f"  - {spec.get('order')}. `{spec.get('signal')}`"
                         f"（{spec.get('kind')}）：{spec.get('definition')}")
    return "\n".join(lines)


def write_weekly_report(report: Mapping[str, Any], path: str,
                        *, markdown_path: str = "") -> Dict[str, str]:
    """写出周报（JSON + 可选 Markdown）；**默认落点由调用方显式给出**"""
    written: Dict[str, str] = {}
    if path:
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n")
        written["json"] = path
    if markdown_path:
        parent = os.path.dirname(os.path.abspath(markdown_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(markdown_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(render_weekly_markdown(report) + "\n")
        written["markdown"] = markdown_path
    return written


def feedback_summary_from_path(storage_path: str, *, days: int = 7) -> Optional[Dict[str, Any]]:
    """从**显式给定**的反馈库路径读取汇总（👍率列的数据源；不触碰默认运行时目录）

    找不到库文件时返回 ``None``（👍率列如实置空），而不是创建目录后返回 0。
    """
    if not storage_path or not os.path.exists(os.path.join(storage_path, "feedback.db")):
        return None
    from agent.feedback import FeedbackManager
    manager = FeedbackManager(storage_path=storage_path)
    return manager.get_feedback_summary(days=days)


__all__ = [
    "MIN_SAMPLES_FOR_ASSESSMENT", "STATUS_OK", "STATUS_INSUFFICIENT",
    "STATUS_FRAMEWORK", "STATUS_UNAVAILABLE", "STATUS_MIXED",
    "SIGNAL_KIND_MECHANICAL", "SIGNAL_KIND_LLM", "SIGNAL_KIND_UNLABELED",
    "SIGNAL_KIND_COLUMNS", "MetricSpec", "METRIC_SPECS",
    "METRIC_BY_KEY", "metric_dictionary", "computable_metrics",
    "delegation_recovery_contract", "routing_annotation_contract",
    "delegation_signal_dictionary", "delegation_signal_kind",
    "delegation_review_passed",
    "compute_digest_throughput", "compute_internalization_rate",
    "compute_delegation_recovery", "compute_delegation_success_rate",
    "compute_skill_success_rate",
    "compute_healing_latency", "compute_approval_decay_rate",
    "compute_delegation_cycle_days", "compute_abandoned_rate",
    "compute_routing_accuracy", "compute_exploration_satisfaction", "compute_utc",
    "resolve_window", "compute_metrics", "render_weekly_markdown",
    "write_weekly_report", "feedback_summary_from_path",
]
