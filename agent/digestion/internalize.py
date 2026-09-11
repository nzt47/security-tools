"""内化触发六条件引擎 + stage.promote PR 产物 + 低流量手动通道
（TASK-S3-03 步骤 3/4 / v7.2 §4.5.1〔P7.2-01〕+ T2 修正）

## 六条件（§4.5.1 逐条对齐）

| # | 条件 | 阈值口径 | 角色 |
|---|---|---|---|
| ① | 样本充足 | ``digest_count ≥ 50`` | 排序 |
| ② | 高频确认 | ``samples(月) ≥ 200`` | 排序 |
| ③ | ROI 为正 | ``月省 > 自研一次性投入 ÷ 12`` | 排序 |
| ④ | 质量不下滑 | ``success_rate(自研) ≥ 上游 × 0.98`` | 排序 |
| ⑤ | 性能不倒退 | ``p99(自研) ≤ p99(上游)`` —— **真实墙钟** | **一票否决** |
| ⑥ | 隐私闸门 | ``privacy_gate == pass``（confidential/secret 出域验证） | **一票否决** |

**硬闸门语义**（不混用）：六条**全部满足**才产出"可 promote"的判决；⑤⑥ 任一不满足
**一票否决**（无论 ①-④ 多好）；①-④ 只决定**排序优先级**（`rank_score`），不否决。

## 三条安全边界（守不易）

1. **stage.promote PR 是本地可审阅产物**：`create_promote_pr()` 只写
   **补丁 + PR 描述 + ROI 报告 + 合入说明**到本地目录；**绝不 push、绝不自动合并**
   （`pushed=False / merged=False` 是产物里的固定字段）。人工合入是设计内的门。
2. **本模块不自行改 stage**：真正的 ``shadow → internalized`` 一律经既有
   `stage.stage_migrate()`，并在 `stage.evaluate_migration()` 里凭**决策证据**
   （``internalize_decision``）opt-in 放行 —— 与 S3-02 的通行证分支同一条通道。
3. **默认关闭**：每日评估调度（`register_internalize_job`）需
   ``CP_DIGESTION_INTERNALIZE_ENABLED=true``；低流量手动通道必须经
   `skills_mgmt.approval` 留痕且**人工批准**后才可能生效。

## 低流量手动 promote 通道（T2 修正）

月 samples < 200（个人负载常态）时六条件不可达 ⇒ 消化空转。本模块给出**替代通道**：
只要 ④⑤⑥（质量/性能/隐私）复核通过，即 **不阻塞**，但显著标注
``低样本人工裁定``；流程 = 提交审批（L2，人工执行）→ **人工批准** → `stage_migrate`
（证据里带 approval 记录 id 与 ``approval_effective``，stage 门会校验）。
样本/ROI 不足**不阻塞**，但**绝不**被说成"条件齐备"。

## M2 的墙钟口径（不重犯 S3-02 §4.6 的量纲错误）

条件⑤ 的两侧**必须是同一测量口径**：候选与上游都用 `shadow.py` 在同一次灰度里
用 `perf_counter` 实测的**真实墙钟**（`p99_wall_candidate_ms` vs
`p99_wall_upstream_ms`）。S2-01 台账的 `duration_ms`（单次能力调用墙钟）**只作披露**
（`evidence["ledger_wall"]`），**不**直接当阈值 —— 那正是 S3-02 §4.6 现象 B 的错误
（拿"一次读文件的 3ms"去卡"读→测→写 的 14ms"）。

**import 纪律**：重依赖（`agent.skills_mgmt.approval` / `agent.audit.facade` /
`agent.observability.{events,utc,trace_v2}` / `agent.descriptors.registry`）一律在
**函数体内**懒加载；导入期无文件/DB/网络副作用。
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import stage as stage_mod
from .cases import canonical_json, open_case_store, slug_of
from .gate import PassportStore
from .shadow import (
    CLOCK_MODEL,
    CLOCK_WALL,
    ShadowLedger,
    ShadowReport,
    ShadowRunner,
)

logger = logging.getLogger("agent.digestion.internalize")

# ════════════════════════════════════════════════════════════
#  常量（六条件与阈值单点定义）
# ════════════════════════════════════════════════════════════

#: 内化引擎版本（进决策证据；口径变更须改版本以便追溯"当时按哪版条件判的"）
INTERNALIZE_VERSION = "s3-03.1"

#: 条件名（决策证据与阶段门共用稳定键）
COND_DIGEST = "digest_count"
COND_SAMPLES = "monthly_samples"
COND_ROI = "roi_positive"
COND_SUCCESS = "success_rate"
COND_P99 = "p99"
COND_PRIVACY = "privacy_gate"
#: 角色：一票否决 / 仅排序（§4.5.1 硬闸门）
DIMENSION_VETO = "veto"
DIMENSION_RANK = "rank"
CONDITIONS: Tuple[Tuple[str, str], ...] = (
    (COND_DIGEST, DIMENSION_RANK),
    (COND_SAMPLES, DIMENSION_RANK),
    (COND_ROI, DIMENSION_RANK),
    (COND_SUCCESS, DIMENSION_RANK),
    (COND_P99, DIMENSION_VETO),
    (COND_PRIVACY, DIMENSION_VETO),
)
VETO_CONDITIONS: Tuple[str, ...] = tuple(n for n, d in CONDITIONS if d == DIMENSION_VETO)
RANK_CONDITIONS: Tuple[str, ...] = tuple(n for n, d in CONDITIONS if d == DIMENSION_RANK)

#: 阈值（与 §4.5.1 **逐值对齐**；与验收门同值者显式同值，不另立一套）
DIGEST_COUNT_MIN = 50
MONTHLY_SAMPLES_MIN = 200
SUCCESS_RATE_RATIO = 0.98          # 与 §4.5 验收门条件 2 同值
P99_RATIO = 1.0                    # 与 §4.5 验收门条件 3 同值
ROI_AMORTIZE_MONTHS = 12
SAMPLES_WINDOW_DAYS = 30

#: 判决
VERDICT_PROMOTE = "promote"
VERDICT_VETO_BLOCKED = "veto_blocked"
VERDICT_CONDITIONS_UNMET = "conditions_unmet"
VERDICT_LOW_TRAFFIC_MANUAL = "low_traffic_manual"
VERDICT_INSUFFICIENT_EVIDENCE = "insufficient_evidence"

#: 隐私闸门
PRIVACY_PASS = "pass"
PRIVACY_FAIL = "fail"
PRIVACY_UNKNOWN = "unknown"
PRIVACY_OPEN_CLASSES: Tuple[str, ...] = ("public", "internal")

#: promote PR 产物（**本地可审阅形式**：补丁 + 描述 + ROI + 合入说明）
PROMOTE_TARGET_STAGE = "internalized"
PROMOTE_DIRNAME = "promote_pr"
PROMOTE_BRANCH_PREFIX = "digest/promote-"
PROMOTE_PATCH_FILENAME = "stage_promote.patch"
PROMOTE_DESCRIPTION_FILENAME = "PR_DESCRIPTION.md"
PROMOTE_ROI_FILENAME = "ROI_REPORT.md"
PROMOTE_DECISION_FILENAME = "decision.json"
PROMOTE_APPLY_FILENAME = "APPLY.md"
DEFAULT_PROMOTE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "digestion", PROMOTE_DIRNAME)
PROMOTE_DIR_ENV = "CP_DIGESTION_PROMOTE_DIR"

#: 审批（低流量手动通道；复用 skills_mgmt 审批流，L2 = 人工执行）
APPROVAL_OBJECT_TYPE = "stage.promote"
APPROVAL_ACTION = "promote"
APPROVAL_LEVEL_MANUAL = "L2"
MANUAL_LABEL_LOW_SAMPLE = "低样本人工裁定"
MANUAL_LABEL_CONFIRMED = "条件齐备人工确认"

#: 调度（**默认关闭**）
SCHEDULE_ENABLE_ENV = "CP_DIGESTION_INTERNALIZE_ENABLED"
SCHEDULE_TASK_NAME = "digestion_internalize_daily"

#: 成本口径（S2-03 归一成本的兜底/覆盖）
INVESTMENT_ENV = "CP_DIGESTION_NATIVE_INVESTMENT_CENTS"
NATIVE_UNIT_COST_ENV = "CP_DIGESTION_NATIVE_UNIT_COST_CENTS"

#: 事件与审计（复用 `digest.stage`；不新增事件类型）
EVENT_SCOPE_INTERNALIZE = "internalize"
AUDIT_ACTION_EVALUATED = "digest.internalize.evaluated"
AUDIT_ACTION_PROMOTE_PR = "digest.internalize.promote_pr"
AUDIT_ACTION_MANUAL_SUBMITTED = "digest.internalize.manual_submitted"
AUDIT_ACTION_MANUAL_APPLIED = "digest.internalize.manual_applied"
AUDIT_ACTION_MANUAL_REJECTED = "digest.internalize.manual_rejected"

#: 证据来源标签
SRC_EXPLICIT = "explicit"
SRC_AUDIT = "audit_chain"
SRC_LEDGER = "s2-01_ledger"
SRC_SHADOW = "shadow_gray"
SRC_UTC = "s2-03_utc"
SRC_DESCRIPTOR = "descriptor"
SRC_UNAVAILABLE = "unavailable"


def _now() -> float:
    return time.time()


def _env_float(name: str, default: float) -> float:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning("%s=%r 非法数值，回退默认 %s", name, raw, default)
        return float(default)


# ════════════════════════════════════════════════════════════
#  数据模型
# ════════════════════════════════════════════════════════════


@dataclass
class ConditionScore:
    """一条条件的打分（逐项可读：actual/threshold/reasons/证据来源）"""

    name: str
    dimension: str
    passed: bool
    actual: Any = None
    threshold: Any = None
    comparator: str = ""
    score: float = 0.0
    reasons: List[str] = field(default_factory=list)
    detail: Dict[str, Any] = field(default_factory=dict)
    evidence_source: str = ""

    @property
    def veto(self) -> bool:
        return self.dimension == DIMENSION_VETO

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "dimension": self.dimension,
                "veto": self.veto, "passed": self.passed, "actual": self.actual,
                "threshold": self.threshold, "comparator": self.comparator,
                "score": round(float(self.score), 4), "reasons": list(self.reasons),
                "detail": dict(self.detail), "evidence_source": self.evidence_source}


@dataclass
class ROIReport:
    """ROI 报告（§4.5.1 条件③；数据源 S2-03 成本归一，**公式可复现**）

    ``月省 = (上游单位成本 − 自研单位成本) × 月样本数``；
    ``摊销 = 自研一次性投入 ÷ 12``；``ROI 为正 ⟺ 月省 > 摊销``。
    """

    monthly_samples: int = 0
    upstream_unit_cents: float = 0.0
    native_unit_cents: float = 0.0
    upstream_monthly_cents: float = 0.0
    native_monthly_cents: float = 0.0
    monthly_saving_cents: float = 0.0
    one_time_investment_cents: float = 0.0
    amortized_monthly_cents: float = 0.0
    net_monthly_cents: float = 0.0
    positive: bool = False
    assumptions: List[str] = field(default_factory=list)
    sources: Dict[str, Any] = field(default_factory=dict)
    caveats: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        payload = dict(self.__dict__)
        payload["formula"] = (
            "月省 = (上游单位成本 − 自研单位成本) × 月样本数；"
            f"摊销 = 一次性投入 ÷ {ROI_AMORTIZE_MONTHS}；ROI 为正 ⟺ 月省 > 摊销")
        return payload

    def markdown(self) -> str:
        lines = [
            "# ROI 报告（§4.5.1 条件③）",
            "",
            "| 项 | 值 |", "|---|---|",
            f"| 月样本数 | {self.monthly_samples} |",
            f"| 上游单位成本 | {self.upstream_unit_cents} 分/次 |",
            f"| 自研单位成本 | {self.native_unit_cents} 分/次 |",
            f"| 月成本（上游） | {self.upstream_monthly_cents} 分 |",
            f"| 月成本（自研） | {self.native_monthly_cents} 分 |",
            f"| **月省** | **{self.monthly_saving_cents} 分** |",
            f"| 自研一次性投入 | {self.one_time_investment_cents} 分 |",
            f"| 月摊销（÷{ROI_AMORTIZE_MONTHS}） | {self.amortized_monthly_cents} 分 |",
            f"| 净月收益 | {self.net_monthly_cents} 分 |",
            f"| **ROI 为正** | **{self.positive}** |",
            "",
            "公式：月省 = (上游单位成本 − 自研单位成本) × 月样本数；"
            f"摊销 = 一次性投入 ÷ {ROI_AMORTIZE_MONTHS}；ROI 为正 ⟺ 月省 > 摊销。",
        ]
        if self.sources:
            lines += ["", "## 数据源", ""]
            lines += [f"- `{k}`：{json.dumps(v, ensure_ascii=False, default=str)}"
                      for k, v in self.sources.items()]
        if self.assumptions:
            lines += ["", "## 假设（显式列出，不藏在数字里）", ""]
            lines += [f"- {a}" for a in self.assumptions]
        if self.caveats:
            lines += ["", "## 口径提醒", ""]
            lines += [f"- {c}" for c in self.caveats]
        return "\n".join(lines) + "\n"


@dataclass
class InternalizeDecision:
    """一次内化评估的完整决策（**含完整内化决策样例所需的全部字段**）"""

    capability_id: str = ""
    verdict: str = ""
    blocker: str = ""
    conditions: List[ConditionScore] = field(default_factory=list)
    rank_score: float = 0.0
    rank_components: Dict[str, float] = field(default_factory=dict)
    roi_report: ROIReport = field(default_factory=ROIReport)
    evidence: Dict[str, Any] = field(default_factory=dict)
    stage: str = ""
    passport_id: str = ""
    manual_required: bool = False
    manual_label: str = ""
    promotable: bool = False
    generated_at: float = 0.0
    notes: List[str] = field(default_factory=list)
    event_id: str = ""
    audit_seq: int = 0
    audit_hash: str = ""

    # ── 汇总 ────────────────────────────────────────────────

    def condition(self, name: str) -> Optional[ConditionScore]:
        for item in self.conditions:
            if item.name == name:
                return item
        return None

    @property
    def failed_conditions(self) -> List[str]:
        return [c.name for c in self.conditions if not c.passed]

    @property
    def veto_failed(self) -> List[str]:
        return [c.name for c in self.conditions if c.veto and not c.passed]

    @property
    def rank_failed(self) -> List[str]:
        return [c.name for c in self.conditions if not c.veto and not c.passed]

    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.conditions)

    def evidence_for_stage(self, **extra: Any) -> Dict[str, Any]:
        """阶段门证据（**已按** `stage.INTERNALIZE_DECISION_KEY` 包裹）

        `stage.evaluate_migration()` 只在调用方**显式带上决策键**时才走 opt-in
        放行分支；故本方法返回的必须是 ``{"internalize_decision": {...}}`` 结构。

        ``passed``（= 允许推进）的语义：

        - 自动路径：六条件齐备（``promotable``）；
        - 低流量手动路径：``manual_required`` 且 ``approval_effective``（人工已批准）
          —— 此时 ``all_conditions_passed`` 仍如实为 False，并把"人工通道允许未通过的
          条件名"列进 ``manual_allowed_failed``（stage 门据此**严格**校验，不接受
          列表外的任何失败项）。
        """
        manual_effective = bool(self.manual_required
                                and extra.get("approval_effective"))
        payload: Dict[str, Any] = {
            "capability_id": self.capability_id,
            "verdict": self.verdict,
            "target_stage": PROMOTE_TARGET_STAGE,
            "passed": bool(self.promotable or manual_effective),
            "all_conditions_passed": bool(self.all_passed),
            "manual": bool(self.manual_required),
            "manual_label": self.manual_label,
            "manual_allowed_failed": (list(self.rank_failed)
                                      if self.manual_required else []),
            "conditions": [{"name": c.name, "dimension": c.dimension,
                            "passed": c.passed} for c in self.conditions],
            "blocker": self.blocker,
            "rank_score": self.rank_score,
            "engine_version": INTERNALIZE_VERSION,
            "generated_at": self.generated_at,
        }
        payload.update(extra)
        return {stage_mod.INTERNALIZE_DECISION_KEY: payload}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id, "verdict": self.verdict,
            "blocker": self.blocker,
            "conditions": [c.to_dict() for c in self.conditions],
            "all_passed": self.all_passed, "veto_failed": self.veto_failed,
            "rank_failed": self.rank_failed,
            "rank_score": self.rank_score,
            "rank_components": dict(self.rank_components),
            "roi_report": self.roi_report.to_dict(),
            "evidence": dict(self.evidence),
            "stage": self.stage, "passport_id": self.passport_id,
            "manual_required": self.manual_required,
            "manual_label": self.manual_label, "promotable": self.promotable,
            "notes": list(self.notes),
            "generated_at": self.generated_at,
            "engine_version": INTERNALIZE_VERSION,
            "event_id": self.event_id,
            "audit_seq": self.audit_seq, "audit_hash": self.audit_hash,
        }

    def markdown(self) -> str:
        """完整内化决策样例（人可读；验收报告直接引用本输出）"""
        lines = [
            f"# 内化决策 — `{self.capability_id}`",
            "",
            f"- 判决：**{self.verdict}**"
            f"（可 promote={self.promotable}，人工通道={self.manual_required}）",
            f"- 阻断项：{self.blocker or '（无）'}",
            f"- 排序分：{self.rank_score}（①-④ 仅排序，不否决）",
            f"- 当前 stage：{self.stage or '-'}｜通行证：`{self.passport_id or '（无）'}`",
            f"- 引擎版本：{INTERNALIZE_VERSION}",
            "",
            "## 六条件逐项打分",
            "",
            "| # | 条件 | 角色 | 通过 | 实测 | 阈值 | 比较 | 证据来源 |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for index, item in enumerate(self.conditions, 1):
            lines.append("| {} | `{}` | {} | {} | {} | {} | {} | {} |".format(
                index, item.name, "**一票否决**" if item.veto else "排序",
                "✅" if item.passed else "❌",
                json.dumps(item.actual, ensure_ascii=False, default=str)[:48],
                json.dumps(item.threshold, ensure_ascii=False, default=str)[:32],
                item.comparator or "-", item.evidence_source or "-"))
        lines += ["", "### 逐条理由", ""]
        for index, item in enumerate(self.conditions, 1):
            lines.append(f"{index}. **{item.name}**（{item.dimension}）："
                         + ("通过" if item.passed else "未通过"))
            for why in item.reasons:
                lines.append(f"   - {why}")
        if self.notes:
            lines += ["", "## 说明", ""] + [f"- {n}" for n in self.notes]
        return "\n".join(lines) + "\n"


@dataclass
class PromotePR:
    """stage.promote PR 产物（**本地可审阅形式**；不 push、不合并）"""

    pr_id: str = ""
    capability_id: str = ""
    target_stage: str = PROMOTE_TARGET_STAGE
    directory: str = ""
    branch: str = ""
    files: Dict[str, str] = field(default_factory=dict)
    patch: str = ""
    roi_markdown: str = ""
    description: str = ""
    pushed: bool = False
    merged: bool = False
    created_at: float = 0.0
    decision_verdict: str = ""
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"pr_id": self.pr_id, "capability_id": self.capability_id,
                "target_stage": self.target_stage, "directory": self.directory,
                "branch": self.branch, "files": dict(self.files),
                "pushed": self.pushed, "merged": self.merged,
                "created_at": self.created_at,
                "decision_verdict": self.decision_verdict, "note": self.note,
                "patch_lines": len((self.patch or "").splitlines())}

    def markdown(self) -> str:
        lines = [
            f"# stage.promote PR — `{self.capability_id}`",
            "",
            f"- PR id：`{self.pr_id}`｜目标 stage：`{self.target_stage}`",
            f"- 视为分支：`{self.branch}`（**本地补丁，不含 git push**）",
            f"- 产物目录：`{self.directory}`",
            f"- 自动推送：**{self.pushed}**｜自动合并：**{self.merged}**"
            "（人工合入是设计内的门）",
            "",
            "## 产物清单", "",
        ]
        lines += [f"- `{k}`" for k in sorted(self.files)]
        lines += ["", "## 变更补丁（stage 字段）", "", "```diff",
                  (self.patch or "").strip() or "（无变更）", "```"]
        return "\n".join(lines) + "\n"


@dataclass
class ManualPromoteRequest:
    """低流量手动 promote 请求（T2 通道；走 approval 留痕）"""

    capability_id: str = ""
    permitted: bool = False
    blocked_reasons: List[str] = field(default_factory=list)
    label: str = ""
    record_id: str = ""
    state: str = ""
    level: str = ""
    manual_required: bool = False
    decision_verdict: str = ""
    low_sample: bool = False
    submitted_at: float = 0.0
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


# ════════════════════════════════════════════════════════════
#  证据采集（每个数据源都标注来源；不可用即"未知"，不猜）
# ════════════════════════════════════════════════════════════


def digest_count_from_audit(capability_id: str, *, limit: int = 1000) -> Dict[str, Any]:
    """digest_count 的证据来源：链式审计里该能力的**消化动作**条数

    计数动作：``digest.stage`` / ``skill.generated`` / ``digest.acceptance.granted``
    / ``digest.internalize.*``（S3-01/S3-02/S3-03 的消化产物都走这三类动作）。
    审计不可用 ⇒ ``unavailable``（advisory，不抛）。
    """
    actions = ("digest.", "skill.generated")
    try:
        from agent.audit.facade import audit
        entries = audit.recent(limit=int(limit)) or []
    except Exception as e:  # noqa: BLE001
        return {"value": 0, "source": SRC_UNAVAILABLE,
                "detail": {"error": f"{type(e).__name__}: {e}"}}
    subject = f"capability:{capability_id}"
    hits = [e for e in entries
            if str(getattr(e, "subject", "")) == subject
            and str(getattr(e, "action", "")).startswith(actions)]
    return {"value": len(hits), "source": SRC_AUDIT,
            "detail": {"matched_actions": sorted({str(getattr(e, "action", ""))
                                                  for e in hits}),
                       "scanned": len(entries), "subject": subject}}


def monthly_samples_from_ledger(capability_id: str, *, store: Any = None,
                                days: int = SAMPLES_WINDOW_DAYS,
                                now: float = 0.0) -> Dict[str, Any]:
    """月样本数：S2-01 统一台账窗口内该能力的行数（+ 灰度样本由调用方叠加）"""
    moment = float(now or _now())
    floor = moment - float(days) * 86400.0
    try:
        if store is None:
            from agent.observability.trace_v2 import UnifiedTraceStore
            store = UnifiedTraceStore()
        rows = store.query(capability_id=capability_id, limit=5000) or []
    except Exception as e:  # noqa: BLE001
        return {"value": 0, "source": SRC_UNAVAILABLE,
                "detail": {"error": f"{type(e).__name__}: {e}"}}
    in_window = 0
    older = 0
    for row in rows:
        started = float(getattr(getattr(row, "timing", None), "started_at", 0.0) or 0.0)
        if started and started < floor:
            older += 1
            continue
        in_window += 1
    return {"value": in_window, "source": SRC_LEDGER,
            "detail": {"rows": len(rows), "window_days": int(days),
                       "outside_window": older,
                       "clock": "wall_clock(timing.started_at)"}}


def unit_cost_from_utc(*, days: int = SAMPLES_WINDOW_DAYS,
                       directory: Optional[str] = None) -> Dict[str, Any]:
    """上游单位成本：S2-03 归一成本口径（`utc.utc_window` 的 UTC，分/任务）"""
    try:
        from agent.observability.utc import utc_window
        end = time.strftime("%Y-%m-%d", time.localtime())
        start = time.strftime("%Y-%m-%d", time.localtime(_now() - days * 86400.0))
        payload = utc_window(start=start, end=end, directory=directory)
    except Exception as e:  # noqa: BLE001
        return {"value": None, "source": SRC_UNAVAILABLE,
                "detail": {"error": f"{type(e).__name__}: {e}"}}
    utc = payload.get("utc_cents_per_task")
    return {"value": (float(utc) if utc is not None else None),
            "source": SRC_UTC,
            "detail": {"window": {"start": start, "end": end},
                       "utc_cents_per_task": utc,
                       "cost_normalized_cents": payload.get("cost_normalized_cents"),
                       "tasks": payload.get("tasks"),
                       "utc_formula": payload.get("utc_formula"),
                       "caveat": "UTC 口径为**单位任务成本**；本处以能力调用样本数"
                                 "作为任务数代理（口径已在 ROI 报告中标注）"}}


def descriptor_view(registry: Any, capability_id: str) -> Dict[str, Any]:
    """descriptor 叶子字段视图（只读所需字段，不搬运整个对象）"""
    if registry is None:
        return {}
    try:
        desc = registry.get(capability_id)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}
    if desc is None:
        return {}
    stage = getattr(getattr(desc, "evolution", None), "stage", None)
    data_class = getattr(getattr(desc, "trust", None), "data_class", None)
    quality = getattr(desc, "quality", None)
    return {
        "stage": str(getattr(stage, "value", stage) or ""),
        "data_class": str(getattr(data_class, "value", data_class) or ""),
        "external_endpoint": bool(getattr(getattr(desc, "origin", None),
                                          "external_endpoint", False)),
        "requires_approval": bool(getattr(getattr(desc, "trust", None),
                                          "requires_approval", False)),
        "shadow_config": dict(getattr(getattr(desc, "evolution", None),
                                      "shadow_config", {}) or {}),
        "success_rate": float(getattr(quality, "success_rate", 0.0) or 0.0),
        "sample_count": int(getattr(quality, "sample_count", 0) or 0),
        "p99_latency_ms": float(getattr(quality, "p99_latency_ms", 0.0) or 0.0),
    }


def evaluate_privacy_gate(descriptor: Dict[str, Any], *,
                          evidence: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """条件⑥：隐私闸门（§4.5.1 "confidential/secret 级数据出域验证通过"）

    规则（**从严**，宁可挡住不可放行）：

    - ``data_class`` 未分级 ⇒ ``unknown``（**不当作 pass**：未分级即不可判定）；
    - ``public`` / ``internal`` ⇒ pass（内部等级不出域）；
    - ``confidential`` / ``secret`` ⇒ 必须同时满足：**无出域端点**且
      **有显式出域验证证据**（``evidence["out_of_domain_verified"]`` 为真）；
      缺一即 fail（这是 S3-02 移交给本任务的 trust 复核口径）。
    """
    data_class = str(descriptor.get("data_class") or "").lower()
    external = bool(descriptor.get("external_endpoint"))
    payload = dict(evidence or {})
    reasons: List[str] = []
    if not data_class:
        return {"gate": PRIVACY_UNKNOWN, "data_class": data_class,
                "external_endpoint": external,
                "reasons": ["descriptor.trust.data_class 未分级（S1-02 遗留未复核对）"
                            "⇒ 隐私闸门不可判定，不按 pass 处理"],
                "source": SRC_DESCRIPTOR}
    if data_class in PRIVACY_OPEN_CLASSES:
        if external:
            reasons.append(f"data_class={data_class} 但登记了出域端点"
                           f"（advisory：内部等级仍建议复核）")
        return {"gate": PRIVACY_PASS, "data_class": data_class,
                "external_endpoint": external,
                "reasons": reasons or [f"data_class={data_class} 不出域 ⇒ pass"],
                "source": SRC_DESCRIPTOR}
    if external:
        return {"gate": PRIVACY_FAIL, "data_class": data_class,
                "external_endpoint": True,
                "reasons": [f"data_class={data_class} 且存在出域端点 ⇒ 一票否决"],
                "source": SRC_DESCRIPTOR}
    if not payload.get("out_of_domain_verified"):
        return {"gate": PRIVACY_FAIL, "data_class": data_class,
                "external_endpoint": False,
                "reasons": [f"data_class={data_class} 属受限等级，需显式出域验证证据"
                            "（out_of_domain_verified=True）方可 pass"],
                "source": SRC_DESCRIPTOR}
    return {"gate": PRIVACY_PASS, "data_class": data_class,
            "external_endpoint": False,
            "reasons": [f"data_class={data_class}：无出域端点 + 出域验证证据齐备"],
            "source": SRC_DESCRIPTOR,
            "verified_by": str(payload.get("verified_by") or "")}


def build_roi_report(*, monthly_samples: int, upstream_unit_cents: Optional[float],
                     native_unit_cents: Optional[float] = None,
                     one_time_investment_cents: Optional[float] = None,
                     sources: Optional[Dict[str, Any]] = None) -> ROIReport:
    """ROI 报告（条件③；公式与假设全部显式）"""
    report = ROIReport(monthly_samples=max(0, int(monthly_samples or 0)),
                       sources=dict(sources or {}))
    upstream = upstream_unit_cents
    if upstream is None:
        report.assumptions.append(
            "上游单位成本不可得（S2-03 UTC 无数据）⇒ ROI 不可判定")
        report.caveats.append("ROI 判定需要上游单位成本；缺值时本报告不给正值结论")
        return report
    report.upstream_unit_cents = round(float(upstream), 6)
    if native_unit_cents is None:
        env_native = _env_float(NATIVE_UNIT_COST_ENV, 0.0)
        report.native_unit_cents = round(env_native, 6)
        report.assumptions.append(
            f"自研单位成本未显式给出 ⇒ 取 {NATIVE_UNIT_COST_ENV}"
            f"（默认 0：纯原生实现不产生模型调用成本）")
    else:
        report.native_unit_cents = round(float(native_unit_cents), 6)
    if one_time_investment_cents is None:
        env_invest = _env_float(INVESTMENT_ENV, 0.0)
        report.one_time_investment_cents = round(env_invest, 6)
        report.assumptions.append(
            f"自研一次性投入未显式给出 ⇒ 取 {INVESTMENT_ENV}（默认 0）")
    else:
        report.one_time_investment_cents = round(float(one_time_investment_cents), 6)
    report.upstream_monthly_cents = round(
        report.upstream_unit_cents * report.monthly_samples, 6)
    report.native_monthly_cents = round(
        report.native_unit_cents * report.monthly_samples, 6)
    report.monthly_saving_cents = round(
        report.upstream_monthly_cents - report.native_monthly_cents, 6)
    report.amortized_monthly_cents = round(
        report.one_time_investment_cents / float(ROI_AMORTIZE_MONTHS), 6)
    report.net_monthly_cents = round(
        report.monthly_saving_cents - report.amortized_monthly_cents, 6)
    report.positive = report.monthly_saving_cents > report.amortized_monthly_cents
    report.caveats.append(
        "单位成本口径：上游取 S2-03 归一成本（分/任务，本处以能力调用样本数"
        "作为任务数代理）；自研为假设值 —— 两项均在 assumptions 中列出")
    return report


# ════════════════════════════════════════════════════════════
#  六条件打分
# ════════════════════════════════════════════════════════════


def score_digest_count(value: Optional[int], *, source: str = "",
                       detail: Optional[Dict[str, Any]] = None) -> ConditionScore:
    if value is None:
        return ConditionScore(
            name=COND_DIGEST, dimension=DIMENSION_RANK, passed=False,
            actual=None, threshold=DIGEST_COUNT_MIN, comparator=">=", score=0.0,
            reasons=["digest_count 不可得（审计通道不可用）⇒ 条件不通过"],
            detail=dict(detail or {}), evidence_source=source or SRC_UNAVAILABLE)
    count = int(value)
    ok = count >= DIGEST_COUNT_MIN
    return ConditionScore(
        name=COND_DIGEST, dimension=DIMENSION_RANK, passed=ok, actual=count,
        threshold=DIGEST_COUNT_MIN, comparator=">=",
        score=min(1.0, count / float(DIGEST_COUNT_MIN)) if DIGEST_COUNT_MIN else 0.0,
        reasons=([f"消化次数 {count} ≥ {DIGEST_COUNT_MIN}"] if ok else
                 [f"消化次数 {count} < {DIGEST_COUNT_MIN}（样本不足；"
                  f"低流量下走手动通道，不阻塞）"]),
        detail=dict(detail or {}), evidence_source=source or SRC_AUDIT)


def score_monthly_samples(value: Optional[int], *, source: str = "",
                          detail: Optional[Dict[str, Any]] = None) -> ConditionScore:
    if value is None:
        return ConditionScore(
            name=COND_SAMPLES, dimension=DIMENSION_RANK, passed=False,
            actual=None, threshold=MONTHLY_SAMPLES_MIN, comparator=">=", score=0.0,
            reasons=["月样本数不可得 ⇒ 条件不通过（低流量可走手动通道）"],
            detail=dict(detail or {}), evidence_source=source or SRC_UNAVAILABLE)
    samples = int(value)
    ok = samples >= MONTHLY_SAMPLES_MIN
    return ConditionScore(
        name=COND_SAMPLES, dimension=DIMENSION_RANK, passed=ok, actual=samples,
        threshold=MONTHLY_SAMPLES_MIN, comparator=">=",
        score=(min(1.0, samples / float(MONTHLY_SAMPLES_MIN))
               if MONTHLY_SAMPLES_MIN else 0.0),
        reasons=([f"月样本 {samples} ≥ {MONTHLY_SAMPLES_MIN}"] if ok else
                 [f"月样本 {samples} < {MONTHLY_SAMPLES_MIN}"
                  f"（T2 修正：走『低样本人工裁定』通道，不阻塞）"]),
        detail=dict(detail or {}), evidence_source=source or SRC_LEDGER)


def score_roi(report: ROIReport) -> ConditionScore:
    known = report.upstream_unit_cents > 0.0 or report.monthly_samples > 0
    ok = bool(report.positive) and known
    if not known:
        reasons = ["ROI 不可判定：缺上游单位成本或月样本 ⇒ 条件不通过"]
        score = 0.0
    elif ok:
        reasons = [f"月省 {report.monthly_saving_cents} 分 > 摊销 "
                   f"{report.amortized_monthly_cents} 分（净 {report.net_monthly_cents} 分）"]
        score = 1.0
    else:
        reasons = [f"月省 {report.monthly_saving_cents} 分 ≤ 摊销 "
                   f"{report.amortized_monthly_cents} 分 ⇒ ROI 非正"]
        base = report.amortized_monthly_cents or 1.0
        score = max(0.0, min(1.0, report.monthly_saving_cents / base))
    return ConditionScore(
        name=COND_ROI, dimension=DIMENSION_RANK, passed=ok,
        actual=report.monthly_saving_cents,
        threshold=report.amortized_monthly_cents,
        comparator=">", score=score, reasons=reasons,
        detail=report.to_dict(), evidence_source=SRC_UTC)


def score_success_rate(*, candidate: Optional[float], upstream: Optional[float],
                       source: str = "", detail: Optional[Dict[str, Any]] = None
                       ) -> ConditionScore:
    if candidate is None or upstream is None:
        return ConditionScore(
            name=COND_SUCCESS, dimension=DIMENSION_RANK, passed=False,
            actual={"candidate": candidate, "upstream": upstream},
            threshold=None, comparator=">=", score=0.0,
            reasons=["成功率证据缺失（需灰度比对或 descriptor.quality）⇒ 不通过"],
            detail=dict(detail or {}), evidence_source=source or SRC_UNAVAILABLE)
    floor = round(float(upstream) * SUCCESS_RATE_RATIO, 4)
    ok = float(candidate) >= floor
    return ConditionScore(
        name=COND_SUCCESS, dimension=DIMENSION_RANK, passed=ok,
        actual=round(float(candidate), 4), threshold=floor, comparator=">=",
        score=(min(1.0, float(candidate) / floor) if floor > 0 else 0.0),
        reasons=([f"成功率 {candidate:.4f} ≥ 上游 {upstream:.4f} × "
                  f"{SUCCESS_RATE_RATIO} = {floor:.4f}"] if ok else
                 [f"成功率 {candidate:.4f} < 上游 {upstream:.4f} × "
                  f"{SUCCESS_RATE_RATIO} = {floor:.4f}（质量下滑）"]),
        detail=dict(detail or {}), evidence_source=source or SRC_SHADOW)


def score_p99(*, candidate_ms: Optional[float], upstream_ms: Optional[float],
              clock: str = CLOCK_WALL, source: str = "",
              detail: Optional[Dict[str, Any]] = None) -> ConditionScore:
    """条件⑤（**一票否决**）：p99 ≤ 上游 —— 两侧必须**同一时钟口径**"""
    if candidate_ms is None or upstream_ms is None:
        return ConditionScore(
            name=COND_P99, dimension=DIMENSION_VETO, passed=False,
            actual={"candidate_ms": candidate_ms, "upstream_ms": upstream_ms},
            threshold=None, comparator="<=", score=0.0,
            reasons=["p99 证据缺失（需灰度期真实墙钟采集）⇒ 一票否决"],
            detail=dict(detail or {}, clock=clock),
            evidence_source=source or SRC_UNAVAILABLE)
    ceiling = round(float(upstream_ms) * P99_RATIO, 3)
    ok = float(candidate_ms) <= ceiling
    return ConditionScore(
        name=COND_P99, dimension=DIMENSION_VETO, passed=ok,
        actual=round(float(candidate_ms), 3), threshold=ceiling, comparator="<=",
        score=(1.0 if ok else max(0.0, ceiling / float(candidate_ms))
               if candidate_ms else 0.0),
        reasons=([f"候选 p99 {candidate_ms}ms ≤ 上游 {upstream_ms}ms"
                  f"（口径 {clock}）"] if ok else
                 [f"候选 p99 {candidate_ms}ms > 上游 {upstream_ms}ms"
                  f"（口径 {clock}）⇒ 性能倒退，一票否决"]),
        detail=dict(detail or {}, clock=clock),
        evidence_source=source or SRC_SHADOW)


def score_privacy(gate: Dict[str, Any]) -> ConditionScore:
    """条件⑥（**一票否决**）：privacy_gate == pass"""
    state = str(gate.get("gate") or PRIVACY_UNKNOWN)
    ok = state == PRIVACY_PASS
    return ConditionScore(
        name=COND_PRIVACY, dimension=DIMENSION_VETO, passed=ok, actual=state,
        threshold=PRIVACY_PASS, comparator="==", score=1.0 if ok else 0.0,
        reasons=list(gate.get("reasons") or []) + (
            [] if ok else ["隐私闸门未通过 ⇒ 一票否决"]),
        detail=dict(gate), evidence_source=str(gate.get("source") or SRC_DESCRIPTOR))


def _condition_passed(decision: InternalizeDecision, name: str) -> bool:
    """条件是否通过（缺失即视为未通过 —— 与引擎口径一致，不因缺项放宽）"""
    item = decision.condition(name)
    return bool(item is not None and item.passed)


def _first_reason(decision: InternalizeDecision, name: str) -> str:
    item = decision.condition(name)
    if item is None or not item.reasons:
        return ""
    return str(item.reasons[0])


def rank_of(conditions: Sequence[ConditionScore]) -> Tuple[float, Dict[str, float]]:
    """①-④ 的**排序分**（仅排序，不否决；公式显式：等权均值）"""
    ranks = {c.name: round(float(c.score), 4) for c in conditions if not c.veto}
    if not ranks:
        return 0.0, {}
    return round(sum(ranks.values()) / float(len(ranks)), 4), ranks


# ════════════════════════════════════════════════════════════
#  内化引擎
# ════════════════════════════════════════════════════════════


class InternalizeEngine:
    """内化触发六条件引擎（评估 → 判决 → PR 产物 / 手动通道）

    用法::

        engine = InternalizeEngine()
        decision = engine.evaluate(cap, shadow_report=report, registry=reg)
        pr = engine.create_promote_pr(decision, registry=reg)   # 本地产物，不 push
    """

    def __init__(self, *, case_store: Any = None,
                 passport_store: Optional[PassportStore] = None,
                 registry: Any = None, ledger: Optional[ShadowLedger] = None,
                 shadow_runner: Optional[ShadowRunner] = None,
                 env: Optional[Dict[str, str]] = None,
                 emit_events: bool = True,
                 actor: str = "digestion_service") -> None:
        self.env = dict(env or {})
        self.emit_events = bool(emit_events)
        self.actor = str(actor or "digestion_service")
        self._case_store = case_store
        self._passport_store = passport_store
        self._registry = registry
        self._ledger = ledger
        self._runner = shadow_runner

    # ── 依赖（懒加载；显式传入才读写运行时区） ──────────────

    @property
    def passport_store(self) -> PassportStore:
        if self._passport_store is None:
            self._passport_store = PassportStore()
        return self._passport_store

    @property
    def registry(self) -> Any:
        if self._registry is None:
            from agent.descriptors.registry import DescriptorRegistry
            self._registry = DescriptorRegistry()
        return self._registry

    @property
    def case_store(self) -> Any:
        if self._case_store is None:
            self._case_store = open_case_store()
        return self._case_store

    @property
    def ledger(self) -> ShadowLedger:
        if self._ledger is None:
            self._ledger = ShadowLedger()
        return self._ledger

    # ── 证据采集 ────────────────────────────────────────────

    def collect_evidence(self, capability_id: str, *,
                         shadow_report: Optional[ShadowReport] = None,
                         registry: Any = None, ledger_store: Any = None,
                         explicit: Optional[Dict[str, Any]] = None,
                         samples: Optional[Dict[str, Any]] = None,
                         now: float = 0.0) -> Dict[str, Any]:
        """六条件所需的全部证据（**每个来源都标注**；缺项即"未知"）"""
        given = dict(explicit or {})
        view = descriptor_view(registry if registry is not None else self._registry,
                               capability_id)

        # ① digest_count
        if "digest_count" in given:
            digest = {"value": int(given["digest_count"]), "source": SRC_EXPLICIT,
                      "detail": {}}
        else:
            digest = digest_count_from_audit(capability_id)

        # ② monthly_samples（台账窗口 + 灰度样本）
        if "monthly_samples" in given:
            monthly = {"value": int(given["monthly_samples"]), "source": SRC_EXPLICIT,
                       "detail": {}}
        else:
            base = samples or monthly_samples_from_ledger(capability_id,
                                                          store=ledger_store, now=now)
            shadow_count = (shadow_report.total if shadow_report is not None else 0)
            monthly = {"value": int(base.get("value") or 0) + shadow_count,
                       "source": base.get("source") or SRC_LEDGER,
                       "detail": dict(base.get("detail") or {},
                                      shadow_samples=shadow_count)}

        # ④ success_rate（候选 / 上游）
        if "success_rate" in given and isinstance(given["success_rate"], dict):
            given_rate = dict(given["success_rate"])
            rates = {"candidate": given_rate.get("candidate"),
                     "upstream": given_rate.get("upstream"), "source": SRC_EXPLICIT,
                     "detail": {"note": "调用方显式给出"}}
        elif shadow_report is not None and shadow_report.total:
            rates = {"candidate": shadow_report.pass_rate,
                     "upstream": 1.0, "source": SRC_SHADOW,
                     "detail": {"candidate_source": "shadow 灰度通过率",
                                "upstream_source": "灰度上游臂全过（同环境双跑）",
                                "judge_kind": shadow_report.judge_kind,
                                "total": shadow_report.total}}
        else:
            rates = {"candidate": (view.get("success_rate") or None),
                     "upstream": None, "source": SRC_DESCRIPTOR,
                     "detail": {"note": "仅有 descriptor.quality，缺上游基线 ⇒ 不通过"}}

        # ⑤ p99（**真实墙钟**；两侧同口径）
        if "p99" in given and isinstance(given["p99"], dict):
            given_p99 = dict(given["p99"])
            p99 = {"candidate_ms": given_p99.get("candidate_ms"),
                   "upstream_ms": given_p99.get("upstream_ms"),
                   "clock": str(given_p99.get("clock") or CLOCK_WALL),
                   "source": SRC_EXPLICIT, "detail": {"note": "调用方显式给出"}}
        elif shadow_report is not None and shadow_report.total:
            p99 = {"candidate_ms": shadow_report.p99_wall_candidate_ms(),
                   "upstream_ms": shadow_report.p99_wall_upstream_ms(),
                   "clock": CLOCK_WALL, "source": SRC_SHADOW,
                   "detail": {"model_clock_candidate_ms":
                              shadow_report.p99_model_candidate_ms(),
                              "model_clock_upstream_ms":
                              shadow_report.p99_model_upstream_ms(),
                              "model_clock_note": CLOCK_MODEL,
                              "note": "两侧均为同一次灰度内 perf_counter 实测墙钟"
                                      "（同环境双跑）⇒ 同口径可比；"
                                      "台账 duration_ms 只作披露不当阈值"}}
        else:
            p99 = {"candidate_ms": None, "upstream_ms": None, "clock": CLOCK_WALL,
                   "source": SRC_UNAVAILABLE,
                   "detail": {"note": "无灰度报告 ⇒ 无真实墙钟 p99 ⇒ 条件⑤ 一票否决"}}

        # ⑥ privacy_gate
        privacy = evaluate_privacy_gate(view, evidence=given.get("privacy"))

        # ③ ROI
        roi_inputs = dict(given.get("roi") or {})
        cost = ({"value": roi_inputs.get("upstream_unit_cents"),
                 "source": SRC_EXPLICIT, "detail": {}}
                if "upstream_unit_cents" in roi_inputs
                else unit_cost_from_utc(days=int(roi_inputs.get("days")
                                                 or SAMPLES_WINDOW_DAYS),
                                        directory=roi_inputs.get("cost_directory")))
        roi = build_roi_report(
            monthly_samples=int(monthly["value"] or 0),
            upstream_unit_cents=cost.get("value"),
            native_unit_cents=roi_inputs.get("native_unit_cents"),
            one_time_investment_cents=roi_inputs.get("one_time_investment_cents"),
            sources={"monthly_samples": {"value": monthly["value"],
                                         "source": monthly["source"]},
                     "upstream_unit_cost": {"value": cost.get("value"),
                                            "source": cost["source"],
                                            "detail": cost.get("detail")},
                     "native_unit_cost": SRC_EXPLICIT,
                     "one_time_investment": SRC_EXPLICIT})

        ledger_wall = self._ledger_wall(capability_id, ledger_store)
        return {"capability_id": capability_id, "collected_at": float(now or _now()),
                "descriptor": view, "digest_count": digest,
                "monthly_samples": monthly, "success_rate": rates, "p99": p99,
                "privacy": privacy, "roi": roi.to_dict(), "cost": cost,
                "ledger_wall": ledger_wall,
                "sources": {"digest_count": digest["source"],
                            "monthly_samples": monthly["source"],
                            "success_rate": rates["source"],
                            "p99": p99["source"],
                            "privacy": privacy.get("source"),
                            "roi": cost["source"]}}

    @staticmethod
    def _ledger_wall(capability_id: str, store: Any = None) -> Dict[str, Any]:
        """S2-01 台账的墙钟统计 —— **只作披露**（量纲口径见模块文档）"""
        try:
            if store is None:
                return {"available": False, "reason": "未提供台账 store（不隐式读运行时区）"}
            rows = store.query(capability_id=capability_id, limit=500) or []
        except Exception as e:  # noqa: BLE001
            return {"available": False, "reason": f"{type(e).__name__}: {e}"}
        durations = sorted(float(getattr(getattr(r, "timing", None),
                                        "duration_ms", 0.0) or 0.0)
                           for r in rows)
        durations = [d for d in durations if d > 0]
        p99 = durations[min(len(durations) - 1, int(0.99 * len(durations)))] \
            if durations else 0.0
        return {"available": bool(durations), "rows": len(rows),
                "p99_duration_ms": round(p99, 3),
                "clock": "wall_clock(单次能力调用 timing.duration_ms)",
                "note": ("**不作条件⑤的阈值**：粒度/量纲与『整条任务链』不同"
                         "（S3-02 §4.6 现象 B 的教训）；仅作披露")}

    # ── 评估 ────────────────────────────────────────────────

    def evaluate(self, capability_id: str, *,
                 shadow_report: Optional[ShadowReport] = None,
                 registry: Any = None, ledger_store: Any = None,
                 evidence: Optional[Dict[str, Any]] = None,
                 snapshot: Optional[Dict[str, Any]] = None,
                 now: float = 0.0) -> InternalizeDecision:
        """六条件逐项打分 → 判决（⑤⑥ 一票否决；①-④ 仅排序）"""
        data = (snapshot if snapshot is not None
                else self.collect_evidence(capability_id, shadow_report=shadow_report,
                                           registry=registry, ledger_store=ledger_store,
                                           explicit=evidence, now=now))
        conditions: List[ConditionScore] = [
            score_digest_count(data["digest_count"].get("value"),
                               source=data["digest_count"].get("source", ""),
                               detail=data["digest_count"].get("detail")),
            score_monthly_samples(data["monthly_samples"].get("value"),
                                  source=data["monthly_samples"].get("source", ""),
                                  detail=data["monthly_samples"].get("detail")),
            score_roi(ROIReport(**{k: v for k, v in
                                   (data.get("roi") or {}).items()
                                   if k in ROIReport.__dataclass_fields__})),
            score_success_rate(candidate=(data["success_rate"] or {}).get("candidate"),
                               upstream=(data["success_rate"] or {}).get("upstream"),
                               source=(data["success_rate"] or {}).get("source", ""),
                               detail=(data["success_rate"] or {}).get("detail")),
            score_p99(candidate_ms=(data["p99"] or {}).get("candidate_ms"),
                      upstream_ms=(data["p99"] or {}).get("upstream_ms"),
                      clock=str((data["p99"] or {}).get("clock") or CLOCK_WALL),
                      source=(data["p99"] or {}).get("source", ""),
                      detail=(data["p99"] or {}).get("detail")),
            score_privacy(data.get("privacy") or {}),
        ]
        rank_score, components = rank_of(conditions)
        decision = InternalizeDecision(
            capability_id=str(capability_id or ""), conditions=conditions,
            rank_score=rank_score, rank_components=components,
            roi_report=ROIReport(**{k: v for k, v in
                                    (data.get("roi") or {}).items()
                                    if k in ROIReport.__dataclass_fields__}),
            evidence=data, stage=str((data.get("descriptor") or {}).get("stage") or ""),
            generated_at=float(now or _now()))

        veto_failed = decision.veto_failed
        rank_failed = decision.rank_failed
        low_sample = COND_SAMPLES in rank_failed or COND_DIGEST in rank_failed
        quality_failed = COND_SUCCESS in rank_failed
        if not veto_failed and not rank_failed:
            decision.verdict = VERDICT_PROMOTE
            decision.promotable = True
            decision.manual_label = MANUAL_LABEL_CONFIRMED
            decision.notes.append("六条件齐备 ⇒ 自动创建 stage.promote PR"
                                  "（**人工合入**是设计内的门）")
        elif veto_failed:
            decision.verdict = VERDICT_VETO_BLOCKED
            decision.blocker = ("一票否决：" + "、".join(
                f"{name}（{_first_reason(decision, name)}）" for name in veto_failed))
            decision.notes.append("⑤⑥ 一票否决：无论 ①-④ 多好都不产出 promote PR")
        elif quality_failed:
            decision.verdict = VERDICT_CONDITIONS_UNMET
            decision.blocker = "质量不下滑（④）未满足：" + "、".join(rank_failed)
            decision.notes.append("④ 属质量硬指标：低样本通道**不**豁免质量复核")
        elif low_sample:
            decision.verdict = VERDICT_LOW_TRAFFIC_MANUAL
            decision.manual_required = True
            decision.manual_label = MANUAL_LABEL_LOW_SAMPLE
            decision.blocker = "样本/ROI 不足：" + "、".join(rank_failed)
            decision.notes.append(
                "T2 修正：样本不足不阻塞，但必须走『低样本人工裁定』通道"
                "（approval 留痕 + 人工批准 + ④⑤⑥ 复核）")
        else:
            decision.verdict = VERDICT_CONDITIONS_UNMET
            decision.blocker = "条件未满足：" + "、".join(rank_failed)

        decision.passport_id = str(
            (self.passport_store.latest(capability_id) or {}).get("passport_id") or "")
        decision.event_id, decision.audit_seq, decision.audit_hash = self._record(
            decision)
        return decision

    def _record(self, decision: InternalizeDecision) -> Tuple[str, int, str]:
        event_id = ""
        if self.emit_events:
            event_id = _emit_internalize_event(
                {"capability_id": decision.capability_id,
                 "from_stage": decision.stage or "shadow",
                 "to_stage": PROMOTE_TARGET_STAGE,
                 "applied": False, "verdict": decision.verdict,
                 "scope": EVENT_SCOPE_INTERNALIZE,
                 "reasons": [f"{c.name}={'pass' if c.passed else 'fail'}"
                             for c in decision.conditions],
                 "digest_run_id": decision.passport_id or decision.capability_id,
                 "passport_id": decision.passport_id,
                 "rank_score": decision.rank_score,
                 "manual_required": decision.manual_required,
                 "note": "内化评估（判决≠合入：promote PR 由人工合入）"},
                correlation_id=f"internalize:{decision.capability_id}:"
                               f"{int(decision.generated_at)}",
                idempotency_key=f"internalize:{decision.capability_id}:"
                                f"{int(decision.generated_at)}:{decision.verdict}")
        seq, digest = _audit(AUDIT_ACTION_EVALUATED,
                             capability_id=decision.capability_id,
                             payload={"verdict": decision.verdict,
                                      "blocker": decision.blocker,
                                      "rank_score": decision.rank_score,
                                      "conditions": [{"name": c.name,
                                                      "dimension": c.dimension,
                                                      "passed": c.passed,
                                                      "actual": c.actual,
                                                      "threshold": c.threshold}
                                                     for c in decision.conditions],
                                      "roi": decision.roi_report.to_dict(),
                                      "manual_required": decision.manual_required},
                             status=decision.verdict, actor=self.actor)
        return event_id, seq, digest

    # ── PR 产物（本地可审阅；不 push、不合并） ──────────────

    def create_promote_pr(self, decision: InternalizeDecision, *,
                          registry: Any = None,
                          out_dir: str = "",
                          write: bool = True) -> Optional[PromotePR]:
        """生成 stage.promote PR 的**本地**产物（补丁 + 描述 + ROI + 合入说明）

        - 仅当 ``decision.promotable`` 为真才产出（否则返回 ``None`` 并说明原因）；
        - 产物目录：``CP_DIGESTION_PROMOTE_DIR/<slug>/<pr_id>/``；
        - **不调用 git push / git merge**，`pushed=False / merged=False` 固定写入产物。
        """
        if not decision.promotable:
            logger.info("不产出 promote PR：判决 %s（%s）",
                        decision.verdict, decision.blocker)
            return None
        reg = registry if registry is not None else self._registry
        view = descriptor_view(reg, decision.capability_id)
        before = {"capability_id": decision.capability_id,
                  "evolution.stage": view.get("stage") or "",
                  "target_stage": PROMOTE_TARGET_STAGE}
        after = dict(before, **{"evolution.stage": PROMOTE_TARGET_STAGE})
        patch = "".join(difflib.unified_diff(
            json.dumps(before, ensure_ascii=False, indent=1, sort_keys=True).splitlines(True),
            json.dumps(after, ensure_ascii=False, indent=1, sort_keys=True).splitlines(True),
            fromfile="a/descriptor-evolution.json",
            tofile="b/descriptor-evolution.json"))

        roi_md = decision.roi_report.markdown()
        pr_id = _pr_id(decision)
        base = str(out_dir or os.environ.get(PROMOTE_DIR_ENV) or DEFAULT_PROMOTE_DIR)
        directory = os.path.join(base, slug_of(decision.capability_id), pr_id)
        branch = f"{PROMOTE_BRANCH_PREFIX}{slug_of(decision.capability_id)}-{pr_id[-8:]}"
        description = self._pr_description(decision, branch, patch)
        apply_md = self._apply_instructions(decision, branch, directory)

        files: Dict[str, str] = {}
        if write:
            os.makedirs(directory, exist_ok=True)
            payloads = {
                PROMOTE_PATCH_FILENAME: patch,
                PROMOTE_DESCRIPTION_FILENAME: description,
                PROMOTE_ROI_FILENAME: roi_md,
                PROMOTE_DECISION_FILENAME: json.dumps(decision.to_dict(),
                                                      ensure_ascii=False, indent=1,
                                                      default=str),
                PROMOTE_APPLY_FILENAME: apply_md,
            }
            for name, body in payloads.items():
                path = os.path.join(directory, name)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(body)
                files[name] = path
            _audit(AUDIT_ACTION_PROMOTE_PR, capability_id=decision.capability_id,
                   payload={"pr_id": pr_id, "directory": directory, "branch": branch,
                            "pushed": False, "merged": False,
                            "verdict": decision.verdict},
                   status="created", actor=self.actor)
        pr = PromotePR(pr_id=pr_id, capability_id=decision.capability_id,
                       directory=directory, branch=branch, files=files, patch=patch,
                       roi_markdown=roi_md, description=description,
                       created_at=_now(), decision_verdict=decision.verdict,
                       note=("本地可审阅产物（补丁 + PR 描述 + ROI + 合入说明）；"
                             "未推送远端、未自动合并"))
        return pr

    def _pr_description(self, decision: InternalizeDecision, branch: str,
                        patch: str) -> str:
        lines = [
            f"# stage.promote: `{decision.capability_id}` → `{PROMOTE_TARGET_STAGE}`",
            "",
            f"> 由 TASK-S3-03 内化引擎（v{INTERNALIZE_VERSION}）自动生成。"
            "**证据齐备 ≠ 已合入**：本 PR 必须**人工审阅并合入**。",
            "",
            "## 一、变更", "",
            f"- 目标能力：`{decision.capability_id}`",
            f"- stage：`{decision.stage or 'shadow'}` → `{PROMOTE_TARGET_STAGE}`",
            f"- 建议分支：`{branch}`（本地补丁文件已附，**未推送**）",
            f"- 通行证：`{decision.passport_id or '（无）'}`",
            "",
            "```diff", (patch or "").strip() or "（无变更）", "```", "",
            "## 二、六条件证据（§4.5.1 / P7.2-01）", "",
            "| 条件 | 角色 | 结果 | 实测 | 阈值 | 证据来源 |",
            "|---|---|---|---|---|---|",
        ]
        for item in decision.conditions:
            lines.append("| `{}` | {} | {} | {} | {} | {} |".format(
                item.name, "一票否决" if item.veto else "排序",
                "✅" if item.passed else "❌",
                json.dumps(item.actual, ensure_ascii=False, default=str)[:40],
                json.dumps(item.threshold, ensure_ascii=False, default=str)[:24],
                item.evidence_source))
        lines += [
            "",
            f"- 排序分（①-④，仅排序）：**{decision.rank_score}**",
            f"- 时钟口径（条件⑤）：**{CLOCK_WALL}**"
            "（两侧同一次灰度实测；模型时钟量仅作披露）",
            "",
            "## 三、ROI 报告", "",
            decision.roi_report.markdown().strip(), "",
            "## 四、审阅要点（人工合入前必看）", "",
            "1. 六条件证据是否来自**真实流量**还是**离线设施**（Seed Pack / 合成回放）"
            "—— 后者不代表真实收益；",
            "2. 条件⑤ 的墙钟是否为**同一测量口径**（本引擎固定同口径，改口径须改代码）；",
            "3. 10% 人工抽检是否已全部裁定（未闭合不得视为已验收）；",
            "4. 灰度期劣化信号（R4）与负例清单；",
            "5. ROI 的单位成本假设（见 ROI 报告 assumptions）。",
        ]
        return "\n".join(lines) + "\n"

    @staticmethod
    def _apply_instructions(decision: InternalizeDecision, branch: str,
                            directory: str) -> str:
        return "\n".join([
            "# 人工合入说明（stage.promote）", "",
            "本目录是**本地可审阅产物**：内化引擎不会推送远端、不会自动合并。", "",
            "## 路径 A：走审计通道（推荐）", "",
            "```python",
            "from agent.digestion.internalize import InternalizeEngine",
            "from agent.digestion import stage as stage_mod",
            "engine = InternalizeEngine()",
            "decision = engine.evaluate(%r)" % decision.capability_id,
            "migration = stage_mod.stage_migrate(",
            "    %r, 'internalized', decision.evidence_for_stage()," % decision.capability_id,
            "    actor='<human-approver>',",
            "    reason='人工合入 stage.promote PR %s')" % decision.capability_id,
            "```", "",
            "## 路径 B：按补丁手工改（等价，但需自行补审计）", "",
            f"1. 审阅 `{PROMOTE_PATCH_FILENAME}`；",
            "2. 将 `evolution.stage` 改为 `internalized` 并补齐审计留痕；", "",
            "## 建议分支名", "", f"`{branch}`", "",
            "## 产物目录", "", f"`{directory}`",
        ]) + "\n"

    # ── 低流量手动 promote 通道（T2） ───────────────────────

    def approval_flow(self, flow: Any = None) -> Any:
        """审批流（复用既有 `skills_mgmt.approval`；L2 = 人工执行）"""
        if flow is not None:
            return flow
        from agent.skills_mgmt.approval import ApprovalFlow
        return ApprovalFlow(level_map={(APPROVAL_OBJECT_TYPE, APPROVAL_ACTION):
                                       APPROVAL_LEVEL_MANUAL},
                            default_level=APPROVAL_LEVEL_MANUAL)

    def manual_promote(self, capability_id: str, actor: str, *,
                       decision: Optional[InternalizeDecision] = None,
                       shadow_report: Optional[ShadowReport] = None,
                       registry: Any = None, ledger_store: Any = None,
                       evidence: Optional[Dict[str, Any]] = None,
                       flow: Any = None, note: str = "") -> ManualPromoteRequest:
        """低流量手动 promote：**④⑤⑥ 复核通过**即可提交人工裁定（T2）

        样本/ROI 不足（①②③）**不阻塞**，但显著标注 ``低样本人工裁定``。
        提交只产生**待审批**记录；生效必须经 `confirm_manual_promote` +
        `apply_manual_promote`（人工批准 + 审计通道）。
        """
        resolved = decision or self.evaluate(
            capability_id, shadow_report=shadow_report, registry=registry,
            ledger_store=ledger_store, evidence=evidence)
        request = ManualPromoteRequest(
            capability_id=str(capability_id or ""),
            decision_verdict=resolved.verdict,
            label=(MANUAL_LABEL_LOW_SAMPLE
                   if COND_SAMPLES in resolved.rank_failed or COND_DIGEST in resolved.rank_failed
                   else MANUAL_LABEL_CONFIRMED),
            low_sample=bool(COND_SAMPLES in resolved.rank_failed
                            or COND_DIGEST in resolved.rank_failed),
            submitted_at=_now())
        required = [COND_SUCCESS, COND_P99, COND_PRIVACY]
        missing = [name for name in required if not _condition_passed(resolved, name)]
        if missing:
            request.permitted = False
            request.blocked_reasons = [
                "手动通道仍须满足 ④⑤⑥（质量/性能/隐私）复核：未通过对 "
                + "、".join(missing),
                "其中 ⑤⑥ 为一票否决，不可由人工绕过",
            ]
            return request
        if resolved.veto_failed:
            request.permitted = False
            request.blocked_reasons = [
                "一票否决生效：" + "、".join(resolved.veto_failed)]
            return request
        flow = self.approval_flow(flow)
        record = flow.submit(
            APPROVAL_OBJECT_TYPE, capability_id, action=APPROVAL_ACTION,
            description=(f"低流量手动 promote（{request.label}）："
                         f"{capability_id} → {PROMOTE_TARGET_STAGE}"),
            payload={"label": request.label, "verdict": resolved.verdict,
                     "rank_score": resolved.rank_score,
                     "conditions": [c.to_dict() for c in resolved.conditions],
                     "blocker": resolved.blocker,
                     "roi": resolved.roi_report.to_dict(),
                     "note": note or "T2 修正：样本不足不阻塞，人工裁定后放行"},
            eval_result={"verdict": resolved.verdict,
                         "veto_failed": resolved.veto_failed},
            actor=str(actor or "requester"), trigger="manual")
        request.permitted = True
        request.record_id = str(getattr(record, "record_id", "") or "")
        request.state = str(getattr(record, "state", "") or "")
        request.level = str(getattr(record, "level", "") or "")
        request.manual_required = bool(getattr(record, "manual_required", False))
        request.note = ("已提交审批（L2 人工执行）；批准后由 "
                        "apply_manual_promote 经 stage_migrate 生效")
        _audit(AUDIT_ACTION_MANUAL_SUBMITTED, capability_id=capability_id,
               payload={"record_id": request.record_id, "label": request.label,
                        "state": request.state, "verdict": resolved.verdict,
                        "low_sample": request.low_sample},
               status=request.state or "submitted", actor=str(actor or "requester"))
        return request

    def confirm_manual_promote(self, capability_id: str, *, record_id: str,
                               actor: str, approve: bool = True,
                               note: str = "", flow: Any = None) -> Dict[str, Any]:
        """人工裁定（approve / reject）—— **留痕在审批流自身**（JSONL + 审计）"""
        flow = self.approval_flow(flow)
        if approve:
            record = flow.approve(record_id, actor=str(actor or "reviewer"),
                                  note=str(note or "人工复核通过"))
        else:
            record = flow.reject(record_id, actor=str(actor or "reviewer"),
                                 reason=str(note or "人工复核驳回"))
            _audit(AUDIT_ACTION_MANUAL_REJECTED, capability_id=capability_id,
                   payload={"record_id": record_id, "actor": actor, "note": note},
                   status="rejected", actor=str(actor or "reviewer"))
        return {"record_id": record_id, "state": str(getattr(record, "state", "")),
                "actor": str(getattr(record, "actor", "")),
                "decision_reason": str(getattr(record, "decision_reason", "")),
                "manual_required": bool(getattr(record, "manual_required", False))}

    def apply_manual_promote(self, capability_id: str, *, record_id: str,
                             actor: str, registry: Any = None, flow: Any = None,
                             decision: Optional[InternalizeDecision] = None,
                             require_approval: bool = True) -> Dict[str, Any]:
        """人工批准后**经审计通道**生效 ``shadow → internalized``

        门禁（缺一不放行）：审批记录存在且状态为 ``approved``/``merged``；
        ``require_approval=False`` 仅供测试/离线演示显式使用。
        """
        flow = self.approval_flow(flow)
        record = flow.get(record_id) if record_id else None
        state = str(getattr(record, "state", "") or "")
        if require_approval and state not in ("approved", "merged"):
            return {"applied": False,
                    "reasons": [f"审批记录 {record_id!r} 状态 {state or '不存在'}"
                                "（须人工批准后方可生效）"]}
        if record is not None and flow.enabled and state == "approved":
            try:
                flow.merge(record_id, actor=str(actor or "reviewer"))
                state = "merged"
            except Exception as e:  # noqa: BLE001
                logger.debug("approval merge 失败（不阻断迁移，仅留痕）: %s", e)
        resolved = decision
        if resolved is None:
            resolved = self.evaluate(capability_id, registry=registry)
        evidence = resolved.evidence_for_stage(
            approval_record_id=str(record_id or ""),
            approval_effective=bool(state in ("approved", "merged")),
            approved_by=str(actor or ""))
        migration = stage_mod.stage_migrate(
            capability_id, PROMOTE_TARGET_STAGE, evidence,
            registry=registry if registry is not None else self._registry,
            emit_event=self.emit_events, actor=str(actor or "reviewer"),
            reason=(f"人工合入 stage.promote（{resolved.manual_label or '人工裁定'}；"
                    f"审批记录 {record_id}）"))
        seq, digest = _audit(AUDIT_ACTION_MANUAL_APPLIED, capability_id=capability_id,
                             payload={"record_id": record_id, "actor": actor,
                                      "migration_verdict": getattr(migration, "verdict", ""),
                                      "stage_applied": bool(getattr(migration,
                                                                    "applied", False)),
                                      "label": resolved.manual_label},
                             status="applied" if getattr(migration, "applied", False)
                             else "refused", actor=str(actor or "reviewer"))
        return {"applied": bool(getattr(migration, "applied", False)),
                "migration": migration, "state": state,
                "reasons": list(getattr(migration, "reasons", []) or []),
                "audit_seq": seq, "audit_hash": digest}


def _pr_id(decision: InternalizeDecision) -> str:
    material = canonical_json({
        "capability_id": decision.capability_id,
        "verdict": decision.verdict,
        "generated_at": int(decision.generated_at),
        "rank_score": decision.rank_score,
        "engine_version": INTERNALIZE_VERSION,
    })
    return "pr_" + hashlib.sha1(material.encode("utf-8")).hexdigest()[:16]


def _emit_internalize_event(payload: Dict[str, Any], *, correlation_id: str,
                            idempotency_key: str) -> str:
    try:
        from agent.observability.events import EV_DIGEST_STAGE, emit, trace_fields
        fields = trace_fields()
        body = dict(payload)
        body.setdefault("workspace_id", fields.get("workspace_id", ""))
        body.setdefault("subject_id", fields.get("subject_id", ""))
        body.setdefault("trace_id", fields.get("trace_id", ""))
        envelope = emit(EV_DIGEST_STAGE, body, correlation_id=correlation_id,
                        idempotency_key=idempotency_key)
        return getattr(envelope, "event_id", "") or ""
    except Exception as e:  # noqa: BLE001
        logger.debug("digest.stage（internalize）事件发送失败: %s", e)
        return ""


def _audit(action: str, *, capability_id: str, payload: Dict[str, Any],
           status: str, actor: str) -> Tuple[int, str]:
    try:
        from agent.audit.facade import audit
        entry = audit.record(action, actor=actor,
                             subject=f"capability:{capability_id}",
                             payload=payload, source="agent", status=status,
                             technical={"internalize_version": INTERNALIZE_VERSION})
        if entry is None:
            return 0, ""
        return (int(getattr(entry, "seq", 0) or 0),
                str(getattr(entry, "self_hash", "") or ""))
    except Exception as e:  # noqa: BLE001
        logger.debug("内化审计写入失败: %s", e)
        return 0, ""


# ════════════════════════════════════════════════════════════
#  每日评估调度（**默认关闭**）
# ════════════════════════════════════════════════════════════


def cost_policy_restricted() -> Tuple[bool, str]:
    """S5-03 成本刹车降本联动（默认 ``(False, "")`` = **不干预**）

    断食/日熔断期间返回 ``(True, 原因)``，调度任务体据此**跳过本周期**
    （§6.7：「错误预算耗尽 → 自动冻结非关键消化任务（停新 shadow/新内化，保执行）」）。

    任何异常一律按「不抑制」处理：成本刹车是 S5-03 新增机制，
    其自身故障**不得**让消化流水线停摆（新增机制失败不得阻断主流程）。
    """
    try:
        from agent.monitoring.cost_brake import digestion_restricted
        return digestion_restricted()
    except Exception as e:  # noqa: BLE001
        logger.debug("成本刹车状态不可用（按不抑制处理）: %s", e)
        return False, ""


def register_internalize_job(scheduler: Any = None, *,
                             engine: Optional[InternalizeEngine] = None,
                             enabled: Optional[bool] = None,
                             interval_seconds: float = 86400.0,
                             auto_pr: bool = False) -> Dict[str, Any]:
    """注册每日内化评估任务（复用既有 `task_scheduler`；**默认关闭**）

    ``CP_DIGESTION_INTERNALIZE_ENABLED=true`` 才注册（与 `register_reprobe_job`
    同一条安全底线）。任务体对每个"七日内有灰度样本"的能力评估六条件；
    ``auto_pr=False``（默认）时**只评估不产出 PR**，避免无人值守下堆积产物。
    """
    if enabled is None:
        enabled = str(os.environ.get(SCHEDULE_ENABLE_ENV, "") or "").strip().lower() \
            in ("1", "true", "yes", "on")
    if not enabled:
        return {"status": "disabled",
                "note": f"内化评估调度默认关闭（安全底线）；开启："
                        f"{SCHEDULE_ENABLE_ENV}=true"}
    try:
        if scheduler is None:
            from agent.task_scheduler import get_scheduler
            scheduler = get_scheduler()
    except Exception as e:  # noqa: BLE001
        logger.error("调度器不可用: %s", e)
        return {"status": "error", "error": str(e)}

    box = engine or InternalizeEngine()

    def _tick() -> Dict[str, Any]:
        # S5-03 成本刹车联动：断食/日熔断期冻结非关键消化任务（保执行，§6.7）
        restricted, why = cost_policy_restricted()
        if restricted:
            logger.info("内化评估被成本刹车抑制（%s），本周期跳过", why)
            return {"status": "suppressed", "reason": why,
                    "source": "agent.monitoring.cost_brake"}
        try:
            ledger = box.ledger
            caps = sorted({str(row.get("capability_id") or "")
                           for row in ledger.rows() if row.get("capability_id")})
            out = []
            for cid in caps:
                decision = box.evaluate(cid)
                pr = box.create_promote_pr(decision) if (auto_pr and decision.promotable) \
                    else None
                out.append({"capability_id": cid, "verdict": decision.verdict,
                            "promotable": decision.promotable,
                            "pr_id": (pr.pr_id if pr else "")})
            return {"status": "ok", "evaluated": len(out),
                    "promotable": sum(1 for r in out if r["promotable"]),
                    "results": out}
        except Exception as e:  # noqa: BLE001  调度线程不得因单次失败挂掉
            logger.error("内化评估任务失败: %s", e)
            return {"status": "error", "error": str(e)}

    scheduler.add_interval_task(SCHEDULE_TASK_NAME, func=_tick,
                                interval_seconds=float(interval_seconds))
    task_id = ""
    tasks = getattr(scheduler, "tasks", None)
    if tasks:
        task_id = str(tasks[-1].get("task_id") or "")
    return {"status": "scheduled", "task_id": task_id,
            "interval_seconds": float(interval_seconds), "auto_pr": bool(auto_pr),
            "note": "每日评估；promote PR 仍需人工合入（不自动推送/合并）"}


__all__ = [
    # 常量
    "INTERNALIZE_VERSION", "COND_DIGEST", "COND_SAMPLES", "COND_ROI",
    "COND_SUCCESS", "COND_P99", "COND_PRIVACY", "CONDITIONS",
    "VETO_CONDITIONS", "RANK_CONDITIONS", "DIMENSION_VETO", "DIMENSION_RANK",
    "DIGEST_COUNT_MIN", "MONTHLY_SAMPLES_MIN", "SUCCESS_RATE_RATIO", "P99_RATIO",
    "ROI_AMORTIZE_MONTHS", "SAMPLES_WINDOW_DAYS",
    "VERDICT_PROMOTE", "VERDICT_VETO_BLOCKED", "VERDICT_CONDITIONS_UNMET",
    "VERDICT_LOW_TRAFFIC_MANUAL", "VERDICT_INSUFFICIENT_EVIDENCE",
    "PRIVACY_PASS", "PRIVACY_FAIL", "PRIVACY_UNKNOWN", "PRIVACY_OPEN_CLASSES",
    "PROMOTE_TARGET_STAGE", "PROMOTE_DIRNAME", "PROMOTE_BRANCH_PREFIX",
    "PROMOTE_PATCH_FILENAME", "PROMOTE_DESCRIPTION_FILENAME",
    "PROMOTE_ROI_FILENAME", "PROMOTE_DECISION_FILENAME", "PROMOTE_APPLY_FILENAME",
    "DEFAULT_PROMOTE_DIR", "PROMOTE_DIR_ENV",
    "APPROVAL_OBJECT_TYPE", "APPROVAL_ACTION", "APPROVAL_LEVEL_MANUAL",
    "MANUAL_LABEL_LOW_SAMPLE", "MANUAL_LABEL_CONFIRMED",
    "SCHEDULE_ENABLE_ENV", "SCHEDULE_TASK_NAME",
    "INVESTMENT_ENV", "NATIVE_UNIT_COST_ENV",
    "EVENT_SCOPE_INTERNALIZE", "AUDIT_ACTION_EVALUATED", "AUDIT_ACTION_PROMOTE_PR",
    "AUDIT_ACTION_MANUAL_SUBMITTED", "AUDIT_ACTION_MANUAL_APPLIED",
    "AUDIT_ACTION_MANUAL_REJECTED",
    "SRC_EXPLICIT", "SRC_AUDIT", "SRC_LEDGER", "SRC_SHADOW", "SRC_UTC",
    "SRC_DESCRIPTOR", "SRC_UNAVAILABLE",
    # 模型
    "ConditionScore", "ROIReport", "InternalizeDecision", "PromotePR",
    "ManualPromoteRequest",
    # 证据与打分
    "digest_count_from_audit", "monthly_samples_from_ledger", "unit_cost_from_utc",
    "descriptor_view", "evaluate_privacy_gate", "build_roi_report",
    "score_digest_count", "score_monthly_samples", "score_roi",
    "score_success_rate", "score_p99", "score_privacy", "rank_of",
    # 引擎
    "InternalizeEngine", "register_internalize_job",
]
