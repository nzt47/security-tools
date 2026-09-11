"""验收门硬闸与漂移重探（TASK-S3-02 步骤 3/4 / v7.2 §4.5 · §3.3）

**验收门四条件**（§4.5「验收门硬闸」，四条件齐发才发通行证）：

| # | 条件 | 阈值口径 |
|---|---|---|
| 1 | 回放**全过** | 生效用例 ≥ ``GATE_REPLAY_MIN``(=20) 且逐例通过率 100% |
| 2 | 成功率 ≥ 基线×0.98 | ``GATE_SUCCESS_RATE_RATIO``(=0.98)（与 §4.5.1 内化条件④同值） |
| 3 | p99 ≤ 上游 | ``GATE_P99_RATIO``(=1.0)（与内化条件⑤同口径：不得倒退） |
| 4 | 覆盖**破坏性分支** | 候选模式的破坏性/失败倾向分支必须被判定集覆盖 |

**通行证（Passport）**：四条件齐 → 产出可审计的通行证资产（`PassportStore`），
写 `digest.stage` 事件（``scope=acceptance_gate``）+ 链式审计
（``digest.acceptance.granted``）。通行证是 ``mirrored → shadow`` 的**唯一证据**
（§3.3 的"等价判定集通过"）；真正推进 stage 由 `advance_to_shadow()` 经 **既有**
`stage.stage_migrate()` 完成（本模块不另立写入方，守 S2-03「一动作一记录」）。

**失败清单化**：未通过的用例逐条列出（哪一层 diff 没过 / 期望为何不等），
供 S3-03 灰度期观察与人工介入 —— 门拒绝推进时**不静默**。

**基线来源**（任务书步骤 3）：**阈值**取同一回放系统内的**上游臂**实测
（回放是双跑同环境，故候选与上游天然可比）；S2-01 台账 quality 统计按任务书要求
**照实披露**在 ``GateResult.baseline["ledger"]``（台账的 `duration_ms` 是单次能力
调用墙钟，与回放的任务链模型时钟**不同量纲**，直接当阈值会得出错误的 p99 判决 ——
实现期实测）；调用方可用显式 ``baseline=`` 注入外部实测基线（如 S3-03 灰度实测）。
来源均在 `GateResult.baseline_source` 注明，绝不把"模型时钟量"说成"真实墙钟 p99"。

**漂移重探**（§4.5）：``30 天`` 或 ``上游版本变化`` 触发 → 简化探针（确定性抽样）
→ schema/契约变化 → ``drifted`` → 判定集**失效**（保留证据）→ 重生成新版本。

**术语纪律**（S0-01）：本模块只使用 v7.2 的 digest/acceptance 语义；云枢旧"评审"
一律称 review/assess，不混用。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import stage as stage_mod
from .cases import (
    CaseSet,
    CaseStore,
    EquivalenceCase,
    canonical_json,
    default_case_root,
    open_case_store,
    regenerate_case_set,
    seed_candidate_for,
)
from .models import CandidatePattern  # noqa: F401  （类型注解与外部 import 兼容保留）
from .sandbox import (
    ConditionContext,
    Implementation,
    ProgramImplementation,
    ReplayReport,
    ReplaySandbox,
    classify_condition,
    condition_matches,
    evaluate_condition,
    is_destructive_condition,
)

logger = logging.getLogger("agent.digestion.gate")

# ════════════════════════════════════════════════════════════
#  常量（门槛单点定义；与 §4.5 / §4.5.1 逐值对齐）
# ════════════════════════════════════════════════════════════

#: 验收门版本（进通行证；口径变更须改版本以便追溯"当时按哪版门发的证"）
GATE_VERSION = "s3-02.1"

#: 条件 1：≥20 条回放全过
GATE_REPLAY_MIN = 20
#: 条件 2：成功率 ≥ 基线×0.98（与 §4.5.1 内化条件④同值）
GATE_SUCCESS_RATE_RATIO = 0.98
#: 条件 3：p99 ≤ 上游（倍数上限 1.0；与内化条件⑤同口径）
GATE_P99_RATIO = 1.0

#: 四条件名（通行证与失败清单共用稳定键）
COND_REPLAY = "replay_all_pass"
COND_SUCCESS_RATE = "success_rate"
COND_P99 = "p99"
COND_BRANCH = "destructive_branch_coverage"
GATE_CONDITIONS: Tuple[str, ...] = (COND_REPLAY, COND_SUCCESS_RATE, COND_P99,
                                    COND_BRANCH)

#: 通行证与审计
PASSPORT_SCHEMA_VERSION = 1
PASSPORT_DIRNAME = "passports"
AUDIT_ACTION_GRANTED = "digest.acceptance.granted"
AUDIT_ACTION_DRIFTED = "digest.acceptance.drifted"
EVENT_SCOPE_GATE = "acceptance_gate"
EVENT_SCOPE_DRIFT = "drift_reprobe"
VERDICT_PASSPORT_GRANTED = "passport_granted"
VERDICT_DRIFTED = "drifted"
VERDICT_PROBE_OK = "probe_ok"

#: 漂移重探（§4.5：30 天或上游版本变化）
REPROBE_INTERVAL_DAYS = 30
REPROBE_PROBE_SIZE = 5
REPROBE_ENABLE_ENV = "CP_DIGESTION_REPROBE_ENABLED"
REPROBE_TASK_NAME = "digestion_case_reprobe"
DAY_SECONDS = 86400.0

#: 漂移触发源
DRIFT_TRIGGER_AGE = "age_30d"
DRIFT_TRIGGER_UPSTREAM = "upstream_version_change"
DRIFT_TRIGGER_SCHEMA = "schema_change"
DRIFT_TRIGGER_MANUAL = "manual"
DRIFT_TRIGGERS: Tuple[str, ...] = (DRIFT_TRIGGER_AGE, DRIFT_TRIGGER_UPSTREAM,
                                   DRIFT_TRIGGER_SCHEMA, DRIFT_TRIGGER_MANUAL)

#: 破坏性分支的判定类别
BRANCH_KIND_DESTRUCTIVE = "destructive_keyword"
BRANCH_KIND_FAILURE_PRONE = "failure_prone"

#: 基线来源
BASELINE_SOURCE_ARG = "arg"
BASELINE_SOURCE_LEDGER = "s2-01_ledger"
BASELINE_SOURCE_SANDBOX = "sandbox_upstream_arm"


def _now() -> float:
    return time.time()


def _short_hash(text: str, length: int = 12) -> str:
    return hashlib.sha1(str(text).encode("utf-8")).hexdigest()[:length]


# ════════════════════════════════════════════════════════════
#  条件 4 的底座：破坏性分支覆盖（含 S3-01 遗留 #4 的参数级条件）
# ════════════════════════════════════════════════════════════


@dataclass
class BranchRequirement:
    """一条必须被覆盖（或被登记观察）的分支要求"""

    condition: str
    at_step: int = -1
    outcome: str = ""
    kind: str = BRANCH_KIND_DESTRUCTIVE
    advice: str = ""
    coverage_kind: str = "addressable"
    required: bool = True
    covered_by: List[str] = field(default_factory=list)
    unknown_atoms: List[str] = field(default_factory=list)

    @property
    def matched(self) -> bool:
        return bool(self.covered_by)

    def to_dict(self) -> Dict[str, Any]:
        return {"condition": self.condition, "at_step": self.at_step,
                "outcome": self.outcome, "kind": self.kind, "advice": self.advice,
                "coverage_kind": self.coverage_kind, "required": self.required,
                "covered_by": list(self.covered_by),
                "unknown_atoms": list(self.unknown_atoms),
                "matched": self.matched}


def _branch_condition_view(branch: Any) -> Tuple[str, int, str, str]:
    """`BranchCondition` / 字符串 / dict → (条件文本, 位次, 倾向, 建议)"""
    if isinstance(branch, str):
        return branch, -1, "", ""
    if isinstance(branch, dict):
        return (str(branch.get("condition") or ""), int(branch.get("at_step") or -1),
                str(branch.get("outcome") or ""), str(branch.get("advice") or ""))
    return (str(getattr(branch, "condition", "") or ""),
            int(getattr(branch, "at_step", -1) or -1),
            str(getattr(branch, "outcome", "") or ""),
            str(getattr(branch, "advice", "") or ""))


def required_branches(pattern: Any = None, *,
                      extra_conditions: Sequence[Any] = (),
                      include_failure_prone: bool = True) -> List[BranchRequirement]:
    """候选模式 → **必须覆盖**的分支要求（破坏性关键词 + 失败倾向）

    ``extra_conditions`` 是 **S3-01 遗留 #4 的扩展点**：决策树当前只产出
    "步骤出现/缺失 + 步数档位"，参数级条件（如 ``path contains test``）由调用方
    显式补入 —— 本函数对两者一视同仁地纳入覆盖要求。
    """
    requirements: List[BranchRequirement] = []
    branches: List[Any] = list(getattr(pattern, "branches", []) or [])
    branches.extend(extra_conditions or ())
    seen: Dict[str, BranchRequirement] = {}
    for branch in branches:
        condition, at_step, outcome, advice = _branch_condition_view(branch)
        if not condition or condition in seen:
            continue
        destructive = is_destructive_condition(condition, outcome=outcome,
                                              advice=advice)
        if not destructive:
            continue
        if outcome == "failure" and not include_failure_prone:
            continue
        kind = (BRANCH_KIND_DESTRUCTIVE
                if is_destructive_condition(condition, advice=advice)
                else BRANCH_KIND_FAILURE_PRONE)
        classification = classify_condition(condition)
        req = BranchRequirement(
            condition=condition, at_step=at_step, outcome=outcome, kind=kind,
            advice=advice, coverage_kind=str(classification.get("kind") or "unknown"),
            required=bool(classification.get("required", True)),
            unknown_atoms=list(classification.get("unknown") or []))
        seen[condition] = req
        requirements.append(req)
    return sorted(requirements, key=lambda r: (r.at_step, r.condition))


def case_condition_context(case: EquivalenceCase) -> ConditionContext:
    """用例 → 条件求值上下文（可判断该用例是否覆盖某条分支）

    参数视图 = ``case.input`` 叠加 ``case.bindings``（后者优先，与沙箱绑定同序）；
    标签视图 = **上游程序**的步骤标签（判定集描述的是上游行为的分支）。
    """
    params: Dict[str, Any] = dict(case.input or {})
    params.update(case.bindings or {})
    return ConditionContext(labels=tuple(case.labels), params=params,
                            output=dict(case.expected_output or {}),
                            step_count=case.step_count)


def branch_coverage(pattern: Any = None,
                    cases: Sequence[EquivalenceCase] = (),
                    *,
                    extra_conditions: Sequence[Any] = ()) -> Dict[str, Any]:
    """破坏性分支覆盖评估（验收门条件 4 的唯一实现）"""
    active = [c for c in cases if c.active]
    requirements = required_branches(pattern, extra_conditions=extra_conditions)
    for req in requirements:
        for case in active:
            ctx = case_condition_context(case)
            tagged = any(str(tag) == req.condition for tag in case.branch_tags)
            if tagged or condition_matches(req.condition, ctx):
                if case.case_id not in req.covered_by:
                    req.covered_by.append(case.case_id)
            for atom, value, _ in evaluate_condition(req.condition, ctx)["atoms"]:
                if value is None and atom not in req.unknown_atoms:
                    req.unknown_atoms.append(atom)
    uncovered = [r for r in requirements if r.required and not r.matched]
    observed = [r for r in requirements if not r.required]
    return {
        "required": sum(1 for r in requirements if r.required),
        "covered": sum(1 for r in requirements if r.required and r.matched),
        "passed": not uncovered,
        "uncovered": [r.to_dict() for r in uncovered],
        "observed": [r.to_dict() for r in observed],
        "observed_count": len(observed),
        "requirements": [r.to_dict() for r in requirements],
        "active_cases": len(active),
        "destructive_cases": sum(1 for c in active if c.destructive),
        "note": ("结构型分支（步骤出现/缺失、步数档位）是 S3-01 决策树的**历史形态**"
                 "描述，不是可回放输入 ⇒ 登记为观察项；参数级分支必须被用例覆盖"
                 "（S3-01 遗留 #4 的扩展点）"),
    }


# ════════════════════════════════════════════════════════════
#  验收门结果
# ════════════════════════════════════════════════════════════


@dataclass
class GateConditionResult:
    """单个门条件的结果（actual/threshold 都留下，便于"差多少"可读）"""

    name: str
    passed: bool
    actual: Any = None
    threshold: Any = None
    comparator: str = ""
    reasons: List[str] = field(default_factory=list)
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "actual": self.actual,
                "threshold": self.threshold, "comparator": self.comparator,
                "reasons": list(self.reasons), "detail": dict(self.detail)}


@dataclass
class GateResult:
    """一次验收门评估的完整结果（含**未通过**的情形，绝不静默）"""

    capability_id: str
    passed: bool = False
    conditions: List[GateConditionResult] = field(default_factory=list)
    baseline: Dict[str, Any] = field(default_factory=dict)
    baseline_source: str = ""
    case_set_version: int = 0
    executed: int = 0
    replay_report: Optional[ReplayReport] = None
    branch_coverage: Dict[str, Any] = field(default_factory=dict)
    passport: Dict[str, Any] = field(default_factory=dict)
    target_stage: str = "shadow"
    gate_version: str = GATE_VERSION
    generated_at: float = 0.0
    event_id: str = ""
    audit_seq: int = 0
    audit_hash: str = ""
    migration: Optional[stage_mod.StageMigration] = None
    note: str = ""

    def condition(self, name: str) -> Optional[GateConditionResult]:
        for item in self.conditions:
            if item.name == name:
                return item
        return None

    @property
    def failed_conditions(self) -> List[str]:
        return [c.name for c in self.conditions if not c.passed]

    def failure_list(self) -> List[Dict[str, Any]]:
        """失败清单（哪些用例失败 / 哪层 diff 未过 / 哪条门条件未达）"""
        return (self.replay_report.failure_list()
                if self.replay_report is not None else [])

    def reasons(self) -> List[str]:
        out: List[str] = []
        for item in self.conditions:
            if item.passed:
                continue
            out.extend(f"[{item.name}] {r}" for r in item.reasons)
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "passed": self.passed,
            "gate_version": self.gate_version,
            "target_stage": self.target_stage,
            "case_set_version": self.case_set_version,
            "executed": self.executed,
            "baseline": dict(self.baseline),
            "baseline_source": self.baseline_source,
            "conditions": [c.to_dict() for c in self.conditions],
            "failed_conditions": self.failed_conditions,
            "failures": self.reasons(),
            "replay": (self.replay_report.to_dict()
                       if self.replay_report is not None else {}),
            "failure_list": self.failure_list(),
            "branch_coverage": dict(self.branch_coverage),
            "passport": dict(self.passport),
            "event_id": self.event_id,
            "audit_seq": self.audit_seq,
            "generated_at": self.generated_at,
            "note": self.note,
        }


# ════════════════════════════════════════════════════════════
#  基线
# ════════════════════════════════════════════════════════════


def baseline_from_traces(rows: Sequence[Any]) -> Dict[str, Any]:
    """S2-01 台账行 → 基线（success_rate / p99 / sample_count）

    只用**台账已记录的叶子字段**（``response.status`` / ``timing.duration_ms``），
    不放大载荷 —— 与 S2-01 的载荷纪律一致。
    """
    durations: List[float] = []
    success = 0
    total = 0
    for row in rows or []:
        status = str(getattr(getattr(row, "response", None), "status", "") or "")
        duration = float(getattr(getattr(row, "timing", None), "duration_ms", 0.0) or 0.0)
        total += 1
        if status == "success":
            success += 1
        if duration > 0:
            durations.append(duration)
    ordered = sorted(durations)
    p99 = 0.0
    if ordered:
        idx = min(len(ordered) - 1, int(0.99 * len(ordered)))
        p99 = round(ordered[idx], 3)
    return {
        "success_rate": round(success / total, 4) if total else 0.0,
        "p99": p99,
        "sample_count": total,
        "source": BASELINE_SOURCE_LEDGER,
    }


def baseline_from_ledger(capability_id: str, *, store: Any = None,
                         limit: int = 500) -> Optional[Dict[str, Any]]:
    """从统一台账读该能力的历史基线（不可用返回 None —— advisory）"""
    try:
        if store is None:
            from agent.observability.trace_v2 import UnifiedTraceStore
            store = UnifiedTraceStore()
        rows = store.query(capability_id=capability_id, limit=limit)
        if not rows:
            return None
        payload = baseline_from_traces(rows)
        return payload if payload.get("sample_count") else None
    except Exception as e:  # noqa: BLE001
        logger.debug("台账基线不可用: %s", e)
        return None


def _upstream_arm_success_rate(report: ReplayReport,
                               cases: Sequence[EquivalenceCase]) -> float:
    """上游臂成功率：上游观测终态与用例期望一致的比例（沙箱内同口径可比）"""
    by_id = {c.case_id: c for c in cases}
    matched = 0
    total = 0
    for replay in report.replays:
        case = by_id.get(replay.case_id)
        if case is None:
            continue
        total += 1
        expected = case.expected_status
        if expected == "any" or replay.upstream.status == expected:
            matched += 1
    return round(matched / total, 4) if total else 0.0


# ════════════════════════════════════════════════════════════
#  通行证
# ════════════════════════════════════════════════════════════


def passport_id_for(capability_id: str, *, case_set_version: int,
                    digest: str) -> str:
    return "pp_" + hashlib.sha1(
        f"{capability_id}|{case_set_version}|{digest}".encode("utf-8")
    ).hexdigest()[:20]


class PassportStore:
    """通行证存储（JSON；``<case_root>/passports/<slug>.json``，保留有限历史）

    与判定集同根但独立目录：判定集失效重生成**不清除**历史通行证（"当时按哪版
    门、哪版判定集发的证"必须可回查）。
    """

    MAX_HISTORY = 10

    def __init__(self, root: str = "") -> None:
        base = str(root or default_case_root())
        self.root = os.path.join(base, PASSPORT_DIRNAME)
        os.makedirs(self.root, exist_ok=True)

    def _path(self, capability_id: str) -> str:
        safe = "".join(ch if (ch.isalnum() or ch in "._-") else "_"
                       for ch in str(capability_id or "")) or "unknown"
        return os.path.join(self.root, f"{safe}.json")

    def save(self, passport: Dict[str, Any]) -> str:
        """写入通行证（按 ``passport_id`` **幂等**：同 id 覆盖，避免重复历史）"""
        capability_id = str(passport.get("capability_id") or "")
        path = self._path(capability_id)
        rows: List[Dict[str, Any]] = []
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            rows = list(payload.get("passports") or [])
        except (OSError, ValueError):
            rows = []
        pid = passport.get("passport_id")
        rows = [row for row in rows if row.get("passport_id") != pid]
        rows.append(passport)
        rows = rows[-self.MAX_HISTORY:]
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"schema_version": PASSPORT_SCHEMA_VERSION,
                       "capability_id": capability_id,
                       "latest": pid,
                       "updated_at": _now(),
                       "passports": rows}, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return path

    def latest(self, capability_id: str) -> Optional[Dict[str, Any]]:
        try:
            with open(self._path(capability_id), "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError):
            return None
        rows = list(payload.get("passports") or [])
        return rows[-1] if rows else None

    def history(self, capability_id: str) -> List[Dict[str, Any]]:
        try:
            with open(self._path(capability_id), "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError):
            return []
        return [{"passport_id": p.get("passport_id"),
                 "case_set_version": p.get("case_set_version"),
                 "gate_version": p.get("gate_version"),
                 "issued_at": p.get("issued_at"),
                 "passed": p.get("passed")}
                for p in (payload.get("passports") or [])]


def build_passport(result: GateResult,
                   *, case_set: Optional[CaseSet] = None) -> Dict[str, Any]:
    """`GateResult` → 通行证（``stage.ACCEPTANCE_PASSPORT_KEY`` 认得的结构）

    携带 ``executed`` / ``required_replays`` 自洽字段，使下游（stage 门）**无需**
    知道本模块的门槛常量即可校验"这条通行证是按几个回放发的"。
    """
    conditions = [{"name": c.name, "passed": c.passed, "actual": c.actual,
                   "threshold": c.threshold} for c in result.conditions]
    digest = _short_hash(canonical_json({
        "capability_id": result.capability_id,
        "case_set_version": result.case_set_version,
        "gate_version": result.gate_version,
        "conditions": conditions,
        "executed": result.executed,
    }), 16)
    passport: Dict[str, Any] = {
        "schema_version": PASSPORT_SCHEMA_VERSION,
        "passport_id": passport_id_for(result.capability_id,
                                       case_set_version=result.case_set_version,
                                       digest=digest),
        "capability_id": result.capability_id,
        "gate_version": result.gate_version,
        "passed": bool(result.passed),
        "target_stage": result.target_stage,
        "from_stage": (None if result.migration is None
                       else result.migration.from_stage),
        "case_set_version": int(result.case_set_version),
        "case_set_active": (len(case_set.active_cases()) if case_set else 0),
        "executed": int(result.executed),
        "required_replays": int(GATE_REPLAY_MIN),
        "required_success_rate_ratio": float(GATE_SUCCESS_RATE_RATIO),
        "required_p99_ratio": float(GATE_P99_RATIO),
        "conditions": conditions,
        "baseline": dict(result.baseline),
        "baseline_source": result.baseline_source,
        "pass_rate": (result.replay_report.pass_rate
                      if result.replay_report else 0.0),
        "p99_candidate_ms": (result.replay_report.p99_candidate_ms()
                             if result.replay_report else 0.0),
        "p99_upstream_ms": (result.replay_report.p99_upstream_ms()
                            if result.replay_report else 0.0),
        "branch_coverage": {"required": result.branch_coverage.get("required", 0),
                            "covered": result.branch_coverage.get("covered", 0)},
        "failed_case_ids": (result.replay_report.failed_ids
                            if result.replay_report else []),
        "manual_sample": (result.replay_report.manual_sample
                          if result.replay_report else []),
        "digest": digest,
        "issued_at": result.generated_at or _now(),
        "scope": EVENT_SCOPE_GATE,
    }
    return passport


# ════════════════════════════════════════════════════════════
#  事件与审计
# ════════════════════════════════════════════════════════════


def _emit_gate_event(payload: Dict[str, Any], *, correlation_id: str,
                     idempotency_key: str) -> str:
    """发 `digest.stage` 事件（**不新增事件类型**，避免 §13.3 硬耦合表漂移）"""
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
    except Exception as e:  # noqa: BLE001  事件失败不得影响门结果
        logger.debug("digest.stage（验收门）事件发送失败: %s", e)
        return ""


def _audit(action: str, *, capability_id: str, payload: Dict[str, Any],
           status: str, technical: Dict[str, Any], actor: str) -> Tuple[int, str]:
    try:
        from agent.audit.facade import audit
        entry = audit.record(action, actor=actor,
                             subject=f"capability:{capability_id}",
                             payload=payload, source="agent", status=status,
                             technical=technical)
        if entry is None:
            return 0, ""
        return (int(getattr(entry, "seq", 0) or 0),
                str(getattr(entry, "self_hash", "") or ""))
    except Exception as e:  # noqa: BLE001
        logger.debug("验收门审计写入失败: %s", e)
        return 0, ""


# ════════════════════════════════════════════════════════════
#  验收门
# ════════════════════════════════════════════════════════════


def _resolve_candidate(candidate: Any, case: EquivalenceCase) -> Any:
    """候选实现解析：``None`` → 用 Seed Pack 的候选骨架；callable → 按用例取"""
    if candidate is None:
        return seed_candidate_for(case)
    if callable(candidate) and not isinstance(candidate, Implementation):
        return candidate(case)
    return candidate


def _candidate_kind(candidate: Any) -> str:
    """候选来源标签（进 `GateResult.note`，使"评的是谁"可追溯）"""
    if candidate is None:
        return "seed_pack_native"
    if callable(candidate) and not isinstance(candidate, Implementation):
        return "provider"
    if isinstance(candidate, CandidatePattern):
        return "candidate_pattern"
    if isinstance(candidate, Implementation):
        return f"implementation:{candidate.name}"
    return "explicit"


def acceptance_gate(
    capability_id: str,
    *,
    case_set: Optional[CaseSet] = None,
    store: Optional[CaseStore] = None,
    candidate: Any = None,
    sandbox: Optional[ReplaySandbox] = None,
    pattern: Any = None,
    extra_conditions: Sequence[Any] = (),
    baseline: Optional[Dict[str, Any]] = None,
    baseline_store: Any = None,
    upstream_provider: Optional[Callable[[EquivalenceCase], Any]] = None,
    registry: Any = None,
    passport_store: Optional[PassportStore] = None,
    write_passport: bool = True,
    emit_events: bool = True,
    advance: bool = False,
    actor: str = "digestion_service",
    now: float = 0.0,
) -> GateResult:
    """**验收门硬闸**（§4.5）：四条件齐才发通行证

    Args:
        capability_id: 目标能力（canonical capability_id）
        case_set / store: 判定集来源（二者取一；都没有则返回 no_case_set 结果）
        candidate: 候选原生实现（`CandidatePattern` / 步骤列表 / 实现对象 /
            按用例取实现的 callable）；``None`` 时取 Seed Pack 的候选骨架
        pattern: 候选模式（条件 4 的破坏性分支来源；缺省则条件 4 需
            ``extra_conditions`` 显式给出，否则视为"无破坏性分支 ⇒ 通过"）
        baseline: 显式基线（``success_rate`` / ``p99``）；缺省按"台账→沙箱上游臂"，
            其中**台账基线仅在给出 ``baseline_store`` 时读取**（避免隐式读运行时台账）
        baseline_store: S2-01 统一台账（给出即优先用作基线来源）
        advance: 通过后是否**立即**经既有 `stage.stage_migrate()` 推进
            ``mirrored → shadow``（默认 False：本任务只发证，不替 S3-03 灰度决策）

    Returns:
        `GateResult`（未通过时 `failure_list()` 逐条列出失败用例与未过层次）
    """
    result = GateResult(capability_id=str(capability_id or ""),
                        generated_at=float(now or _now()))
    resolved = case_set
    if resolved is None and store is not None:
        resolved = store.load(result.capability_id)
    if resolved is None:
        result.note = "no_case_set：判定集缺失（须先由 cases 通道生成）"
        for name in GATE_CONDITIONS:
            result.conditions.append(GateConditionResult(
                name=name, passed=False,
                reasons=["判定集缺失，无回放可评估"]))
        return result
    result.case_set_version = int(resolved.version)
    cases = resolved.active_cases()
    result.executed = len(cases)
    if not cases:
        result.note = "no_active_cases：判定集已失效或为空"
        for name in GATE_CONDITIONS:
            result.conditions.append(GateConditionResult(
                name=name, passed=False, reasons=["无生效用例（判定集已失效？）"]))
        return result

    box = sandbox or ReplaySandbox()
    # ── 回放（候选逐例解析；上游缺省用用例录制程序） ──
    provider_kind = _candidate_kind(candidate)
    sample = set(box.sample_for_manual([c.case_id for c in cases]))
    report = ReplayReport(capability_id=result.capability_id)
    report.manual_sample = sorted(sample)
    for case in cases:
        picked = _resolve_candidate(candidate, case)
        upstream = (upstream_provider(case) if upstream_provider is not None
                    else ProgramImplementation(list(case.upstream),
                                               name="upstream"))
        replay = box.replay_case(case, picked, upstream=upstream,
                                 manual_flagged=case.case_id in sample)
        report.replays.append(replay)
        for layer in replay.diff.failed_layers:
            report.layer_failures[layer] = report.layer_failures.get(layer, 0) + 1
    result.replay_report = report

    # ── 基线（显式 > 沙箱上游臂；台账统计**只作披露**，见下方说明） ──
    #
    # 为什么台账统计不能直接当阈值：台账的 `duration_ms` 是**单次能力调用**的墙钟，
    # 而回放测的是**整条任务链**的模型时钟量 —— 两者不同量纲、不同粒度，直接比
    # 就是拿"一次读文件的 3ms"去卡"读→测→写 的 14ms"（实现期实测的错误比较）。
    # 故：**阈值一律取同一回放系统内的上游臂**（§4.5 的"p99 ≤ 上游"本就是同口径
    # 比较）；S2-01 台账 quality 统计按任务书要求**照实披露**在 `baseline.ledger`，
    # 供人工与 S5-02 评测锚参考；调用方也可用显式 `baseline=` 注入外部实测基线。
    ledger_view: Dict[str, Any] = {}
    if baseline:
        resolved_baseline = dict(baseline)
        source = str(baseline.get("source") or BASELINE_SOURCE_ARG)
    else:
        ledger_view = (baseline_from_ledger(result.capability_id,
                                           store=baseline_store)
                       if baseline_store is not None else None) or {}
        resolved_baseline = {
            "success_rate": _upstream_arm_success_rate(report, cases),
            "p99": report.p99_upstream_ms(),
            "sample_count": report.total,
            "source": BASELINE_SOURCE_SANDBOX,
        }
        source = BASELINE_SOURCE_SANDBOX
    if ledger_view:
        resolved_baseline["ledger"] = ledger_view
        resolved_baseline["ledger_source"] = BASELINE_SOURCE_LEDGER
    base_rate = float(resolved_baseline.get("success_rate") or 0.0)
    base_p99 = float(resolved_baseline.get("p99") or 0.0)
    result.baseline = resolved_baseline
    result.baseline_source = source

    # ── 条件 1：≥20 条回放全过 ──
    enough = result.executed >= GATE_REPLAY_MIN
    all_pass = report.failed_ids == []
    reasons: List[str] = []
    if not enough:
        reasons.append(f"生效用例 {result.executed} 组 < 要求 {GATE_REPLAY_MIN} 组")
    if not all_pass:
        reasons.append(
            f"回放未全过：{len(report.failed_ids)}/{report.total} 例失败 "
            f"（{report.failed_ids[:5]}）")
    result.conditions.append(GateConditionResult(
        name=COND_REPLAY, passed=(enough and all_pass),
        actual=result.executed, threshold=GATE_REPLAY_MIN,
        comparator=">=", reasons=reasons,
        detail={"executed": result.executed, "passed": report.passed,
                "failed": len(report.failed_ids), "failed_case_ids": report.failed_ids,
                "layer_failures": dict(report.layer_failures),
                "manual_sample": list(report.manual_sample)}))

    # ── 条件 2：成功率 ≥ 基线×0.98 ──
    rate = report.pass_rate
    rate_floor = round(base_rate * GATE_SUCCESS_RATE_RATIO, 4)
    rate_ok = rate >= rate_floor
    result.conditions.append(GateConditionResult(
        name=COND_SUCCESS_RATE, passed=rate_ok, actual=rate,
        threshold=rate_floor, comparator=">=",
        reasons=([] if rate_ok else [
            f"成功率 {rate:.4f} < 基线 {base_rate:.4f} × "
            f"{GATE_SUCCESS_RATE_RATIO} = {rate_floor:.4f}"]),
        detail={"baseline_success_rate": base_rate,
                "ratio": GATE_SUCCESS_RATE_RATIO,
                "baseline_source": source}))

    # ── 条件 3：p99 ≤ 上游 ──
    cand_p99 = report.p99_candidate_ms()
    p99_ceiling = round(base_p99 * GATE_P99_RATIO, 3)
    p99_ok = cand_p99 <= p99_ceiling
    result.conditions.append(GateConditionResult(
        name=COND_P99, passed=p99_ok, actual=cand_p99, threshold=p99_ceiling,
        comparator="<=",
        reasons=([] if p99_ok else [
            f"候选 p99 {cand_p99}ms > 上游 p99 {base_p99}ms × {GATE_P99_RATIO}"]),
        detail={"candidate_p99_ms": cand_p99, "upstream_p99_ms": base_p99,
                "sandbox_upstream_p99_ms": report.p99_upstream_ms(),
                "baseline_source": source,
                "clock": "确定性模型时钟（非墙钟；真实 p99 由 S3-03 灰度采集）"}))

    # ── 条件 4：覆盖破坏性分支 ──
    coverage = branch_coverage(pattern, cases, extra_conditions=extra_conditions)
    result.branch_coverage = coverage
    result.conditions.append(GateConditionResult(
        name=COND_BRANCH, passed=bool(coverage.get("passed")), actual=coverage,
        threshold="覆盖全部破坏性/失败倾向分支", comparator="covered==required",
        reasons=([] if coverage.get("passed") else [
            f"未覆盖破坏性分支 {len(coverage.get('uncovered') or [])} 条："
            + canonical_json([u.get("condition") for u in
                              (coverage.get("uncovered") or [])])]),
        detail={"requirements": coverage.get("requirements"),
                "uncovered": coverage.get("uncovered"),
                "destructive_cases": coverage.get("destructive_cases")}))

    # ── 判决与通行证 ──
    result.passed = all(c.passed for c in result.conditions)
    result.note = (f"候选实现来源：{provider_kind}"
                   + ("（Seed Pack 候选骨架）" if provider_kind == "seed_pack_native"
                      else ""))
    if result.passed and write_passport:
        passport = build_passport(result, case_set=resolved)
        result.passport = passport
        try:
            (passport_store or PassportStore()).save(passport)
        except Exception as e:  # noqa: BLE001
            logger.warning("通行证落盘失败（advisory）: %s", e)
        if emit_events:
            result.event_id = _emit_gate_event(
                {"capability_id": result.capability_id,
                 "from_stage": None, "to_stage": result.target_stage,
                 "applied": False, "verdict": VERDICT_PASSPORT_GRANTED,
                 "scope": EVENT_SCOPE_GATE,
                 "reasons": [f"{c.name}={c.actual}" for c in result.conditions],
                 "digest_run_id": passport["passport_id"],
                 "passport_id": passport["passport_id"],
                 "case_set_version": result.case_set_version,
                 "gate_version": result.gate_version,
                 "executed": result.executed,
                 "pass_rate": result.replay_report.pass_rate if result.replay_report else 0.0,
                 "note": "通行证已发；stage 推进需显式 advance 或由 S3-03 灰度驱动"},
                correlation_id=passport["passport_id"],
                idempotency_key=f"{passport['passport_id']}:granted")
        result.audit_seq, result.audit_hash = _audit(
            AUDIT_ACTION_GRANTED, capability_id=result.capability_id,
            payload={"passport_id": passport["passport_id"],
                     "gate_version": result.gate_version,
                     "case_set_version": result.case_set_version,
                     "executed": result.executed,
                     "conditions": [{"name": c.name, "passed": c.passed,
                                     "actual": c.actual, "threshold": c.threshold}
                                    for c in result.conditions],
                     "baseline": resolved_baseline,
                     "reason": "§4.5 验收门四条件齐：回放全过 / 成功率≥基线×0.98 / "
                               "p99≤上游 / 覆盖破坏性分支"},
            status="granted",
            technical={"passport_id": passport["passport_id"]},
            actor=actor)
        if advance:
            result.migration = advance_to_shadow(
                result.capability_id, result,
                passport_store=passport_store, registry=registry,
                emit_event=emit_events, actor=actor)
            # 通行证在推进前构建（`from_stage` 需回读台账才知道），推进后补齐并
            # 按 passport_id 幂等覆盖（同一张证不产生重复历史）
            if result.migration is not None:
                result.passport["from_stage"] = result.migration.from_stage
                result.passport["to_stage"] = result.migration.to_stage
                result.passport["stage_applied"] = bool(result.migration.applied)
                try:
                    (passport_store or PassportStore()).save(result.passport)
                except Exception as e:  # noqa: BLE001
                    logger.warning("通行证补齐落盘失败（advisory）: %s", e)
    return result


def advance_to_shadow(
    capability_id: str,
    gate_result: GateResult,
    *,
    passport_store: Optional[PassportStore] = None,
    registry: Any = None,
    emit_event: bool = True,
    actor: str = "digestion_service",
) -> stage_mod.StageMigration:
    """**opt-in**：凭通行证把 ``mirrored → shadow`` 交给既有 `stage_migrate()`

    本模块不写台账、不写审计（stage 三处联动由 `stage.py` 独占），只在证据里附上
    通行证 → `stage.evaluate_migration()` 的 opt-in 分支认得它并放行；
    无通行证时行为与 S3-01 完全一致（仍返回 ``deferred_to_downstream``）。
    """
    passport = dict(gate_result.passport or {})
    if not passport:
        passport = (passport_store or PassportStore()).latest(capability_id) or {}
    evidence: Dict[str, Any] = {
        "acceptance_passport": passport,
        "passport_id": str(passport.get("passport_id") or ""),
        "gate_version": gate_result.gate_version,
        "case_set_version": gate_result.case_set_version,
        "executed": gate_result.executed,
        "scope": EVENT_SCOPE_GATE,
    }
    return stage_mod.stage_migrate(
        capability_id, "shadow", evidence, registry=registry,
        emit_event=emit_event, actor=actor,
        reason=("TASK-S3-02 验收门通行证 "
                f"{passport.get('passport_id', '')}：§4.5 四条件齐 → mirrored→shadow"))


# ════════════════════════════════════════════════════════════
#  漂移重探（§4.5）
# ════════════════════════════════════════════════════════════


@dataclass
class DriftReport:
    """一次漂移重探的结果"""

    capability_id: str
    trigger: str = ""
    verdict: str = ""
    drifted: bool = False
    schema_changed: bool = False
    due: bool = False
    probes: List[str] = field(default_factory=list)
    probe_failures: List[Dict[str, Any]] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    invalidated_version: int = 0
    regenerated_version: int = 0
    regenerated_cases: int = 0
    upstream_version_before: str = ""
    upstream_version_after: str = ""
    schema_before: Dict[str, Any] = field(default_factory=dict)
    schema_after: Dict[str, Any] = field(default_factory=dict)
    probed_at: float = 0.0
    event_id: str = ""
    audit_seq: int = 0
    audit_hash: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id, "trigger": self.trigger,
            "verdict": self.verdict, "drifted": self.drifted,
            "schema_changed": self.schema_changed, "due": self.due,
            "probes": list(self.probes),
            "probe_failures": list(self.probe_failures),
            "reasons": list(self.reasons),
            "invalidated_version": self.invalidated_version,
            "regenerated_version": self.regenerated_version,
            "regenerated_cases": self.regenerated_cases,
            "upstream_version_before": self.upstream_version_before,
            "upstream_version_after": self.upstream_version_after,
            "schema_before": dict(self.schema_before),
            "schema_after": dict(self.schema_after),
            "probed_at": self.probed_at,
            "event_id": self.event_id, "audit_seq": self.audit_seq,
        }


def probe_sample_ids(case_ids: Sequence[str], *,
                     size: int = REPROBE_PROBE_SIZE) -> List[str]:
    """确定性探针抽样（按 ``sha1(case_id)`` 排序取前 N；可复现是硬要求）"""
    ordered = sorted({str(c) for c in case_ids},
                     key=lambda c: (hashlib.sha1(c.encode("utf-8")).hexdigest(), c))
    if not ordered:
        return []
    return sorted(ordered[:max(1, min(int(size), len(ordered)))])


def due_for_reprobe(case_set: CaseSet, *, now: float = 0.0,
                    interval_days: int = REPROBE_INTERVAL_DAYS) -> bool:
    """是否到期需重探（上次探针时间或创建时间起算 ``interval_days`` 天）"""
    reference = float(case_set.last_probed_at or case_set.created_at or 0.0)
    if reference <= 0:
        return True
    return (float(now or _now()) - reference) >= interval_days * DAY_SECONDS


def _descriptor_view(registry: Any, capability_id: str) -> Dict[str, Any]:
    """从 descriptor 台账取上游契约视图（版本 + 输入/输出 schema）

    ``registry=None`` 一律返回 ``{}``（**显式传入才读**）：隐式构造
    `DescriptorRegistry()` 会在单测/CI 中读到运行时台账（隔离破坏），也与
    `acceptance_gate(baseline_store=...)` 的口径保持一致。生产路径
    （`register_reprobe_job`）显式传入默认台账。
    """
    if registry is None:
        return {}
    try:
        desc = registry.get(capability_id)
    except Exception as e:  # noqa: BLE001
        logger.debug("descriptor 读取失败: %s", e)
        return {}
    if desc is None:
        return {}
    meta = getattr(desc, "meta", None)
    cap = getattr(desc, "capability", None)
    return {
        "version": str(getattr(meta, "version", "") or ""),
        "input_schema": getattr(cap, "input_schema", None),
        "output_schema": getattr(cap, "output_schema", None),
    }


def _schema_fingerprint(payload: Any) -> str:
    if not payload:
        return ""
    return _short_hash(canonical_json(payload), 16)


def reprobe(
    capability_id: str,
    *,
    trigger: str = DRIFT_TRIGGER_AGE,
    store: Optional[CaseStore] = None,
    case_set: Optional[CaseSet] = None,
    candidate: Any = None,
    sandbox: Optional[ReplaySandbox] = None,
    registry: Any = None,
    upstream_provider: Optional[Callable[[EquivalenceCase], Any]] = None,
    regenerate: bool = True,
    trace_set: Any = None,
    concrete_args: Optional[Callable[[Any], Any]] = None,
    seed_backfill: bool = True,
    emit_events: bool = True,
    actor: str = "digestion_service",
    now: float = 0.0,
) -> DriftReport:
    """**漂移重探**：简化探针 → schema 变化 → drifted → 判定集失效重生成

    Args:
        trigger: ``age_30d`` / ``upstream_version_change`` / ``schema_change`` / ``manual``
        candidate: 候选原生实现（同 `acceptance_gate`）；缺省取 Seed Pack 骨架
        upstream_provider: 上游实现的**实时**取法（缺省用用例录制的上游程序）
        regenerate: drifted 后是否立即重生成新版本判定集（重新从 Trace 采样 + Seed 回填）

    判定为 ``drifted`` 的三类证据（任一成立）：① 探针回放出现失败；
    ② 上游**版本**变化或 descriptor 的输入/输出 schema 变化；③ trigger 显式声明为
    上游版本变化。``drifted`` 后**先失效**（保留全部用例与理由作证据）再重生成 ——
    绝不在原地改写一份"看起来还成立"的判定集。
    """
    report = DriftReport(capability_id=str(capability_id or ""),
                         trigger=str(trigger or DRIFT_TRIGGER_AGE),
                         probed_at=float(now or _now()))
    resolved = case_set
    if resolved is None and store is not None:
        resolved = store.load(report.capability_id)
    if resolved is None:
        report.verdict = "not_found"
        report.reasons.append("判定集不存在，无可重探对象（须先生成）")
        return report

    report.due = due_for_reprobe(resolved, now=report.probed_at)
    report.upstream_version_before = str(resolved.upstream_version or "")
    report.schema_before = dict(resolved.upstream_schema or {})

    # ── 简化探针：确定性抽样 + 双跑三层比对 ──
    cases = resolved.active_cases()
    probe_ids = probe_sample_ids([c.case_id for c in cases])
    report.probes = probe_ids
    box = sandbox or ReplaySandbox()
    by_id = {c.case_id: c for c in cases}
    for case_id in probe_ids:
        case = by_id.get(case_id)
        if case is None:
            continue
        picked = _resolve_candidate(candidate, case)
        upstream = (upstream_provider(case) if upstream_provider is not None
                    else ProgramImplementation(list(case.upstream), name="upstream"))
        replay = box.replay_case(case, picked, upstream=upstream)
        if not replay.passed:
            report.probe_failures.append({
                "case_id": case_id,
                "failed_layers": replay.diff.failed_layers,
                "expectation_ok": replay.expectation_ok,
                "reasons": replay.failures(),
            })
    if report.probe_failures:
        report.reasons.append(
            f"探针回放失败 {len(report.probe_failures)}/{len(probe_ids)} 例"
            f"（上游行为或候选实现已偏离判定集）")

    # ── 上游版本 / schema 变化 ──
    view = _descriptor_view(registry, report.capability_id)
    if view:
        report.upstream_version_after = str(view.get("version") or "")
        report.schema_after = {
            "input_schema": view.get("input_schema"),
            "output_schema": view.get("output_schema"),
        }
        if report.upstream_version_after and \
                report.upstream_version_before and \
                report.upstream_version_after != report.upstream_version_before:
            report.schema_changed = True
            report.reasons.append(
                f"上游版本变化：{report.upstream_version_before} → "
                f"{report.upstream_version_after}")
        before_fp = _schema_fingerprint(report.schema_before)
        after_fp = _schema_fingerprint(report.schema_after)
        if before_fp and after_fp and before_fp != after_fp:
            report.schema_changed = True
            report.reasons.append(f"descriptor schema 指纹变化：{before_fp} → {after_fp}")
    if report.trigger == DRIFT_TRIGGER_UPSTREAM:
        report.schema_changed = True
        report.reasons.append("触发源声明为上游版本变化（按 §4.5 直接判定 drifted）")
    if report.trigger == DRIFT_TRIGGER_SCHEMA and not report.schema_changed:
        report.schema_changed = True
        report.reasons.append("触发源声明为 schema 变化（人工/上游通告）")

    report.drifted = bool(report.probe_failures or report.schema_changed)
    report.verdict = VERDICT_DRIFTED if report.drifted else VERDICT_PROBE_OK

    # ── 落账：先失效（保留证据），再重生成 ──
    resolved.last_probed_at = report.probed_at
    if report.drifted:
        resolved.invalidate(
            "；".join(report.reasons) or "探针判定 drifted",
            details={"trigger": report.trigger,
                     "probe_failures": report.probe_failures,
                     "schema_changed": report.schema_changed,
                     "schema_before": report.schema_before,
                     "schema_after": report.schema_after,
                     "upstream_version_before": report.upstream_version_before,
                     "upstream_version_after": report.upstream_version_after},
            now=report.probed_at)
        report.invalidated_version = resolved.version
    if store is not None:
        try:
            store.save(resolved)
        except Exception as e:  # noqa: BLE001
            logger.warning("判定集回写失败（advisory）: %s", e)

    if report.drifted and regenerate and store is not None:
        new_set = regenerate_case_set(
            report.capability_id, store=store, trace_set=trace_set,
            seed_backfill=seed_backfill, reason="；".join(report.reasons),
            created_at=report.probed_at, concrete_args=concrete_args)
        if new_set is not None:
            report.regenerated_version = new_set.version
            report.regenerated_cases = len(new_set.cases)

    if emit_events:
        report.event_id = _emit_gate_event(
            {"capability_id": report.capability_id,
             "from_stage": None, "to_stage": None, "applied": False,
             "verdict": report.verdict, "scope": EVENT_SCOPE_DRIFT,
             "reasons": list(report.reasons)[:8],
             "digest_run_id": f"drift_{report.capability_id}_"
                              f"{int(report.probed_at)}",
             "trigger": report.trigger, "probes": list(report.probes),
             "invalidated_version": report.invalidated_version,
             "regenerated_version": report.regenerated_version,
             "note": "判定集失效重生成（§4.5 漂移重探）；重生成后须重跑验收门"},
            correlation_id=f"drift:{report.capability_id}",
            idempotency_key=f"drift:{report.capability_id}:{report.trigger}:"
                            f"{int(report.probed_at)}")
    report.audit_seq, report.audit_hash = _audit(
        AUDIT_ACTION_DRIFTED, capability_id=report.capability_id,
        payload={"trigger": report.trigger, "verdict": report.verdict,
                 "drifted": report.drifted,
                 "reasons": list(report.reasons)[:8],
                 "invalidated_version": report.invalidated_version,
                 "regenerated_version": report.regenerated_version},
        status=report.verdict, technical={"trigger": report.trigger}, actor=actor)
    return report


def register_reprobe_job(
    scheduler: Any = None,
    *,
    store: Optional[CaseStore] = None,
    interval_days: Optional[int] = None,
    emit_events: bool = True,
    enabled: Optional[bool] = None,
) -> Dict[str, Any]:
    """注册 30 天漂移重探定时任务（复用既有 `task_scheduler`）

    **默认关闭**（与 `evolution_scheduler` 同一条安全底线：定时器不默认开启），
    经 ``CP_DIGESTION_REPROBE_ENABLED=true`` 显式开启。任务体对每个到期判定集
    调一次 `reprobe(trigger=age_30d)`，异常不抛出（调度线程稳定性）。
    """
    days = int(interval_days or REPROBE_INTERVAL_DAYS)
    if enabled is None:
        enabled = str(os.environ.get(REPROBE_ENABLE_ENV, "") or "").strip().lower() \
            in ("1", "true", "yes", "on")
    if not enabled:
        return {"status": "disabled", "interval_days": days,
                "note": f"漂移重探调度默认关闭（安全底线）；开启："
                        f"{REPROBE_ENABLE_ENV}=true"}
    try:
        if scheduler is None:
            from agent.task_scheduler import get_scheduler
            scheduler = get_scheduler()
    except Exception as e:  # noqa: BLE001
        logger.error("调度器不可用: %s", e)
        return {"status": "error", "error": str(e)}

    case_store = store or open_case_store()

    def _registry() -> Any:
        """默认 descriptor 台账（懒加载一次；不可用返回 None ⇒ 只做探针不查契约）"""
        try:
            from agent.descriptors.registry import DescriptorRegistry
            return DescriptorRegistry()
        except Exception as e:  # noqa: BLE001
            logger.debug("descriptor 台账不可用（重探仅做探针）: %s", e)
            return None

    def _tick() -> Dict[str, Any]:
        try:
            reg = _registry()
            reports = [
                reprobe(cid, trigger=DRIFT_TRIGGER_AGE, store=case_store,
                        registry=reg, emit_events=emit_events).to_dict()
                for cid in case_store.list_capabilities()
            ]
            return {"status": "ok", "probed": len(reports),
                    "drifted": sum(1 for r in reports if r.get("drifted"))}
        except Exception as e:  # noqa: BLE001  调度线程不得因单次失败挂掉
            logger.error("漂移重探任务失败: %s", e)
            return {"status": "error", "error": str(e)}

    scheduler.add_interval_task(REPROBE_TASK_NAME, func=_tick,
                                interval_seconds=days * DAY_SECONDS)
    task_id = ""
    tasks = getattr(scheduler, "tasks", None)
    if tasks:
        task_id = str(tasks[-1].get("task_id") or "")
    return {"status": "scheduled", "task_id": task_id, "interval_days": days,
            "note": "每周期对到期判定集执行简化探针；drifted 即失效并重生成"}


__all__ = [
    # 常量
    "GATE_VERSION", "GATE_REPLAY_MIN", "GATE_SUCCESS_RATE_RATIO",
    "GATE_P99_RATIO", "GATE_CONDITIONS", "COND_REPLAY", "COND_SUCCESS_RATE",
    "COND_P99", "COND_BRANCH",
    "PASSPORT_SCHEMA_VERSION", "PASSPORT_DIRNAME", "AUDIT_ACTION_GRANTED",
    "AUDIT_ACTION_DRIFTED", "EVENT_SCOPE_GATE", "EVENT_SCOPE_DRIFT",
    "VERDICT_PASSPORT_GRANTED", "VERDICT_DRIFTED", "VERDICT_PROBE_OK",
    "REPROBE_INTERVAL_DAYS", "REPROBE_PROBE_SIZE", "REPROBE_ENABLE_ENV",
    "REPROBE_TASK_NAME", "DAY_SECONDS",
    "DRIFT_TRIGGER_AGE", "DRIFT_TRIGGER_UPSTREAM", "DRIFT_TRIGGER_SCHEMA",
    "DRIFT_TRIGGER_MANUAL", "DRIFT_TRIGGERS",
    "BRANCH_KIND_DESTRUCTIVE", "BRANCH_KIND_FAILURE_PRONE",
    "BASELINE_SOURCE_ARG", "BASELINE_SOURCE_LEDGER", "BASELINE_SOURCE_SANDBOX",
    # 条件 4 底座
    "BranchRequirement", "required_branches", "branch_coverage",
    "case_condition_context",
    # 门
    "GateConditionResult", "GateResult", "acceptance_gate",
    "advance_to_shadow", "baseline_from_traces", "baseline_from_ledger",
    # 通行证
    "PassportStore", "build_passport", "passport_id_for",
    # 漂移
    "DriftReport", "reprobe", "due_for_reprobe", "probe_sample_ids",
    "register_reprobe_job",
]
