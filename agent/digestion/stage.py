"""stage 迁移驱动（TASK-S3-01 步骤 4 / v7.2 §3.3 七态状态机）

本模块负责三件事，**三处联动**且各有单一写入方（守 S2-03 §4.4 的"一动作一记录"）：

| 落点 | 写入方 | 证据形态 |
|---|---|---|
| `descriptor.evolution.stage` | `registry.set_stage()` | 台账字段 |
| 链式审计 | **registry 内部** `_audit("descriptor.stage")` | `data/audit/audit_chain.db` |
| `digest.stage` 事件 | 本模块 `events.emit()` | `data/events/events.jsonl` |

**不重复留痕**：`digest.stage` 不在 `AUDIT_MIRROR_TYPES`（S2-03 裁定只镜像
`policy.denied`/`healing.triggered`/`model.degraded`/`escape`），因此事件不会二次入链；
而 stage 写入的链式留痕由 registry 独占（本模块**不**再调一次 `audit.record`）。
唯一例外是**被拒**的迁移：台账未被触碰 ⇒ 链上本无记录，此时补一条
`digest.stage.refused`，使"拒绝推进"这一治理决策同样可审计（**不静默**）。

**迁移门（§3.3）**：

- ``None → borrowed``：首次入轨。凡台账中存在的资产均可入轨；borrowed 必填
  `trace_policy` ⇒ 由 `bridge.ledger_trace_policy()` 给**真实统一台账引用**（S3-01 L3）。
- ``borrowed → mirrored``：条件 = **同类轨迹 ≥20 条**（清洗后）+ **模式可提取**
  （骨架非空且达到 `MIN_PATTERN_STEPS`）；验收物 = **副作用画像 + 候选模式**，
  两者缺一即拒（不静默推进）。
- ``mirrored → shadow``：**S3-02 验收门通行证**（§4.5 四条件齐）为 opt-in 放行
  证据 —— 见 `acceptance_passport_ok()` 与 `ACCEPTANCE_PASSPORT_KEY` 的注释；
  **无通行证时行为与 S3-01 逐字一致**（仍 `deferred_to_downstream`）。
- 其余边（``shadow → internalized`` 等）由 S3-03 shadow 灰度门控；
  本模块返回 `deferred_to_downstream` 并如实记录，不越权推进。
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .models import (
    VERDICT_APPLIED,
    VERDICT_DEFERRED,
    VERDICT_ERROR,
    VERDICT_ILLEGAL_TRANSITION,
    VERDICT_INSUFFICIENT_EVIDENCE,
    VERDICT_NOT_FOUND,
    MIN_PATTERN_STEPS,
    MIN_SAME_KIND_TRACES,
    CandidatePattern,
    StageMigration,
)

logger = logging.getLogger("agent.digestion.stage")

#: 七态主链顺序（`models.EvolutionStage` 的取值顺序，S0-02 §3.2）
SEVEN_STATES: Tuple[str, ...] = (
    "borrowed", "mirrored", "shadow", "internalized", "native",
    "permanent_borrowed", "deprecated",
)

#: 主链（可顺序演进）与旁支（终态/例外）
MAIN_CHAIN: Tuple[str, ...] = (
    "borrowed", "mirrored", "shadow", "internalized", "native")
SIDE_STATES: Tuple[str, ...] = ("permanent_borrowed", "deprecated")

#: 本任务（S3-01）有权驱动的迁移边
DRIVEN_EDGES: Tuple[Tuple[Optional[str], str], ...] = (
    (None, "borrowed"),          # 首次入轨
    ("borrowed", "mirrored"),    # §3.3 borrowed→mirrored
)
#: 交由下游任务门控的迁移边（本任务只登记证据与建议，不推进）
DOWNSTREAM_EDGES: Tuple[Tuple[str, str], ...] = (
    ("mirrored", "shadow"), ("shadow", "internalized"),
    ("internalized", "native"), ("native", "permanent_borrowed"),
)

#: **S3-02 验收门通行证**的证据键（§3.3：``mirrored → shadow`` 的条件是
#: "等价判定集通过 + 原生实现单元测试通过"）。S3-02 交付 `gate.acceptance_gate()`
#: 后，"等价判定集通过"这一半有了**可审计的通行证资产**，故本模块为
#: ``mirrored → shadow`` 开一条 **opt-in** 放行分支：
#:
#: - **有**合法通行证 ⇒ 放行（`VERDICT_APPLIED`）；
#: - **无**通行证 ⇒ 行为与 S3-01 **逐字一致**（仍 `deferred_to_downstream`，
#:   拒绝理由不变）——本扩展不改变任何既有调用方的结果。
ACCEPTANCE_PASSPORT_KEY = "acceptance_passport"

#: **S3-03 内化决策**的证据键（``shadow → internalized``）。T2 修正后的内化有两条
#: 合法路径，二者都产出本键：
#:
#: - **自动路径**：§4.5.1 六条件齐备（`internalize.InternalizeEngine.evaluate()`
#:   判决 ``promote``）⇒ 自动创建 stage.promote PR，**人工合入**时把决策作为证据；
#: - **低流量手动路径**（T2）：①②③（样本/ROI）不足**不阻塞**，但必须 ④⑤⑥ 复核
#:   通过 + `skills_mgmt.approval` 留痕 + **人工批准** ⇒ 证据里带
#:   ``approval_record_id`` 与 ``approval_effective``。
#:
#: 与通行证一样，本键是**opt-in**：调用方不带该键时 ``shadow → internalized``
#: 的行为与 S3-01/S3-02 **逐字一致**（仍 ``deferred_to_downstream``）。
INTERNALIZE_DECISION_KEY = "internalize_decision"


def digest_run_id(*, capability_id: str, from_stage: Optional[str],
                  to_stage: Optional[str], evidence: Dict[str, Any]) -> str:
    """确定性 digest run id（同一迁移 + 同一证据 ⇒ 同一 id，重放不产生新事件）"""
    material = "|".join([
        str(capability_id or ""), str(from_stage or ""), str(to_stage or ""),
        _evidence_digest(evidence),
    ])
    return "dg_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _evidence_digest(evidence: Dict[str, Any]) -> str:
    """证据摘要（只摘要**标量/短值**，避免把大对象拖进哈希与载荷）"""
    import json
    try:
        material = json.dumps(_scalar_view(evidence), ensure_ascii=False,
                              sort_keys=True, default=str)
    except Exception:  # noqa: BLE001
        material = str(sorted(str(k) for k in (evidence or {})))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _scalar_view(evidence: Any, depth: int = 0) -> Any:
    """证据 → 标量视图（供哈希/事件载荷；截断长字符串）"""
    if depth > 4:
        return "[depth]"
    if isinstance(evidence, dict):
        return {str(k): _scalar_view(v, depth + 1) for k, v in evidence.items()}
    if isinstance(evidence, (list, tuple)):
        return [_scalar_view(v, depth + 1) for v in evidence][:20]
    if isinstance(evidence, str):
        return evidence[:200]
    if isinstance(evidence, (int, float, bool)) or evidence is None:
        return evidence
    return str(evidence)[:80]


# ════════════════════════════════════════════════════════════
#  迁移门
# ════════════════════════════════════════════════════════════


def acceptance_passport_ok(capability_id: str,
                           evidence: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """校验 S3-02 验收门通行证（**自洽性**校验，不依赖 S3-02 的门槛常量）

    校验项（缺一即不放行，且理由逐条可读）：

    1. 通行证存在且为 dict；
    2. ``passed`` 为真；
    3. ``capability_id`` 与本能力一致（防张冠李戴）；
    4. 四条件**全部** passed（条件名/数量取自通行证自身，不写死 S3-02 常量）；
    5. ``executed >= required_replays``（自洽：不许"用 3 条回放发的证"冒充 ≥20）；
    6. ``case_set_version`` 为正（证指向某版判定集）。
    """
    passport = evidence.get(ACCEPTANCE_PASSPORT_KEY)
    reasons: List[str] = []
    if not isinstance(passport, dict) or not passport:
        return False, ["缺少 acceptance_passport 证据（S3-02 验收门通行证）——"
                       "mirrored→shadow 不放行"]
    if not passport.get("passed"):
        reasons.append("通行证 passed=False（验收门未通过）")
    if str(passport.get("capability_id") or "") != str(capability_id or ""):
        reasons.append(
            f"通行证能力 {passport.get('capability_id')!r} 与目标 "
            f"{capability_id!r} 不一致")
    conditions = passport.get("conditions") or []
    if not conditions:
        reasons.append("通行证未附条件明细（conditions 为空）")
    else:
        failed = [str(c.get("name")) for c in conditions
                  if not (isinstance(c, dict) and c.get("passed"))]
        if failed:
            reasons.append(f"通行证条件未全通过：{failed}")
    executed = int(passport.get("executed") or 0)
    required = int(passport.get("required_replays") or 0)
    if required <= 0:
        reasons.append("通行证缺少 required_replays（无法校验回放条数自洽）")
    elif executed < required:
        reasons.append(
            f"通行证 executed={executed} < required_replays={required}"
            f"（未达 §4.5 的 ≥20 条回放全过）")
    if int(passport.get("case_set_version") or 0) <= 0:
        reasons.append("通行证未指向有效的判定集版本（case_set_version）")
    return (not reasons), reasons


def internalize_decision_ok(capability_id: str,
                            evidence: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """校验 S3-03 内化决策证据（**自洽性**校验，不依赖 internalize 的门槛常量）

    校验项（缺一即不放行，理由逐条可读）：

    1. 决策存在且为 dict；
    2. ``capability_id`` 与本能力一致（防张冠李戴）；
    3. ``target_stage == "internalized"``（防止拿别的目标的决策来推进）；
    4. ``passed`` 为真（决策自身判定"可 promote"）；
    5. 条件自洽：**一票否决**条件（``dimension == "veto"``）必须全过；非人工路径下
       **全部**条件必须 passed；人工路径（T2）下只允许 ``manual_allowed_failed``
       列出的条件未通过（列表外的任何失败都拒绝 —— 不接受"随手放宽"）；
    6. **人工路径自洽**：``manual`` 为真时，必须带 ``approval_record_id`` 且
       ``approval_effective`` 为真（T2 通道：人工裁定必须留痕且已生效）。
    """
    decision = evidence.get(INTERNALIZE_DECISION_KEY)
    reasons: List[str] = []
    if not isinstance(decision, dict) or not decision:
        return False, ["缺少 internalize_decision 证据（S3-03 六条件引擎决策）——"
                       "shadow→internalized 不放行"]
    if str(decision.get("capability_id") or "") != str(capability_id or ""):
        reasons.append(f"决策能力 {decision.get('capability_id')!r} 与目标 "
                       f"{capability_id!r} 不一致")
    if str(decision.get("target_stage") or "") != "internalized":
        reasons.append("决策 target_stage 不是 internalized"
                       f"（实际 {decision.get('target_stage')!r}）")
    if not decision.get("passed"):
        reasons.append(f"决策 passed=False（verdict={decision.get('verdict')!r}；"
                       f"blocker={decision.get('blocker')!r}）")
    conditions = decision.get("conditions") or []
    if not conditions:
        reasons.append("决策未附条件明细（conditions 为空）")
    else:
        failed = [str(c.get("name")) for c in conditions
                  if not (isinstance(c, dict) and c.get("passed"))]
        veto_failed = [str(c.get("name")) for c in conditions
                       if isinstance(c, dict) and c.get("dimension") == "veto"
                       and not c.get("passed")]
        if veto_failed:
            reasons.append(f"一票否决条件未通过：{veto_failed}（不可放行）")
        if decision.get("manual"):
            allowed = {str(x) for x in (decision.get("manual_allowed_failed") or [])}
            unexpected = [name for name in failed if name not in allowed]
            if unexpected:
                reasons.append(
                    f"人工通道只允许 {sorted(allowed) or '（无）'} 未通过；"
                    f"其余未通过条件：{unexpected}")
        elif failed:
            reasons.append(f"决策条件未全通过：{failed}")
    if decision.get("manual"):
        if not str(decision.get("approval_record_id") or "").strip():
            reasons.append("低流量人工通道缺少 approval_record_id（人工裁定须留痕）")
        if not decision.get("approval_effective"):
            reasons.append("低流量人工通道 approval_effective=False"
                           "（人工未批准，不得放行）")
    return (not reasons), reasons


def evaluate_migration(*, capability_id: str, from_stage: Optional[str],
                       to_stage: str,
                       evidence: Dict[str, Any]) -> Tuple[str, List[str]]:
    """迁移门评估 → ``(verdict, reasons)``（**纯函数**，不触碰台账/事件/审计）

    Returns:
        verdict ∈ {applied 表示"可推进", insufficient_evidence, illegal_transition,
        deferred_to_downstream, not_found}
        reasons：人类可读的逐条判定依据（推进与否都要写进报告）
    """
    if not capability_id:
        return VERDICT_NOT_FOUND, ["capability_id 为空"]
    edge = (from_stage, to_stage)

    # ── S3-02 通行证：mirrored → shadow 的 opt-in 放行（见常量注释）──
    # 仅当调用方**显式**带上通行证键时才走本分支；否则落到下面的
    # `DOWNSTREAM_EDGES` 分支 —— 与 S3-01 的行为逐字一致（既有调用方零影响）。
    if edge == ("mirrored", "shadow") and ACCEPTANCE_PASSPORT_KEY in evidence:
        ok, passport_reasons = acceptance_passport_ok(capability_id, evidence)
        if ok:
            return VERDICT_APPLIED, [
                f"等价判定集通过（§4.5 验收门通行证 "
                f"{evidence.get(ACCEPTANCE_PASSPORT_KEY, {}).get('passport_id', '')}）"
                f" ⇒ mirrored → shadow 放行"]
        return VERDICT_INSUFFICIENT_EVIDENCE, passport_reasons + [
            "（S3-02 验收门未通过或证据缺失 ⇒ 保持 mirrored，不静默推进）"]

    # ── S3-03 内化决策：shadow → internalized 的 opt-in 放行（见常量注释）──
    # 同样只在调用方显式带上决策键时生效；不带键 ⇒ 与 S3-01/S3-02 行为逐字一致。
    if edge == ("shadow", "internalized") and INTERNALIZE_DECISION_KEY in evidence:
        ok, decision_reasons = internalize_decision_ok(capability_id, evidence)
        if ok:
            decision = evidence.get(INTERNALIZE_DECISION_KEY) or {}
            label = str(decision.get("manual_label") or "").strip()
            suffix = f"（{label}）" if label else ""
            return VERDICT_APPLIED, [
                f"内化六条件决策通过（verdict={decision.get('verdict')!r}，"
                f"排序分 {decision.get('rank_score')}）{suffix}"
                f" ⇒ shadow → internalized 放行"]
        return VERDICT_INSUFFICIENT_EVIDENCE, decision_reasons + [
            "（S3-03 六条件/人工裁定证据不足 ⇒ 保持 shadow，不静默推进）"]

    if edge in DOWNSTREAM_EDGES:
        return VERDICT_DEFERRED, [
            f"{from_stage} → {to_stage} 的门控属 S3-02（判定集/回放沙箱）与 "
            f"S3-03（shadow/灰度），本任务（S3-01）不越权推进",
        ]

    if edge not in DRIVEN_EDGES:
        if to_stage not in SEVEN_STATES:
            return VERDICT_ILLEGAL_TRANSITION, [f"{to_stage!r} 不是七态取值"]
        return VERDICT_ILLEGAL_TRANSITION, [
            f"{from_stage} → {to_stage} 不在本任务可驱动的迁移边内"
            f"（可驱动：{DRIVEN_EDGES}）",
        ]

    if edge == (None, "borrowed"):
        # 首次入轨：证据要求 = 台账中确存在 + 可给出轨迹引用策略
        reasons: List[str] = []
        if not evidence.get("first_entry"):
            reasons.append("缺少 first_entry 证据标记")
        if not str(evidence.get("trace_policy") or "").strip():
            reasons.append("borrowed 必填 trace_policy（真实轨迹台账引用）——缺失")
        return (VERDICT_INSUFFICIENT_EVIDENCE if reasons else VERDICT_APPLIED), reasons

    # ── borrowed → mirrored（§3.3 条件 + 验收物）──
    reasons = []
    trace_count = int(evidence.get("trace_count") or 0)
    if trace_count < MIN_SAME_KIND_TRACES:
        reasons.append(
            f"同类轨迹 {trace_count} 条 < 门槛 {MIN_SAME_KIND_TRACES} 条"
            f"（清洗后计数）")
    pattern_steps = int(evidence.get("pattern_steps") or 0)
    if not evidence.get("pattern_extracted"):
        reasons.append("未提取出候选模式（pattern_extracted=False）")
    elif pattern_steps < MIN_PATTERN_STEPS:
        reasons.append(
            f"骨架步骤 {pattern_steps} < {MIN_PATTERN_STEPS}（单步骨架不成模式）")
    profile = evidence.get("side_effect_profile")
    if not isinstance(profile, dict) or not profile:
        reasons.append("缺少副作用画像（mirrored 的验收物之一）")
    elif "files_written_count" not in profile:
        reasons.append("副作用画像结构不完整（缺 files_written_count）")
    return (VERDICT_INSUFFICIENT_EVIDENCE if reasons else VERDICT_APPLIED), reasons


def recommended_stage(report_evidence: Dict[str, Any]) -> Dict[str, Any]:
    """由证据推导 **stage 建议**（供 `DigestionReport.stage_recommendation`）

    建议只是建议：未入轨 → 建议 `borrowed`（首次入轨）；已 `borrowed` 且满足
    §3.3 条件 → 建议 `mirrored`；否则维持 `current_stage` 并列出缺口 ——
    让"没推进"这件事在报告里看得见。
    """
    current = report_evidence.get("current_stage")
    capability_id = str(report_evidence.get("capability_id") or "")
    if current is None:
        return {
            "from": None, "to": "borrowed", "eligible": True,
            "reasons": ["未入轨（stage 为空）→ 首次入轨至七态主轨最低证据态 borrowed"],
        }
    verdict, reasons = evaluate_migration(
        capability_id=capability_id, from_stage=current, to_stage="mirrored",
        evidence=report_evidence)
    if verdict == VERDICT_APPLIED:
        return {"from": current, "to": "mirrored", "eligible": True,
                "reasons": ["同类轨迹与模式均达 §3.3 条件"]}
    downstream = [r for r in reasons if "S3-02" in r or "S3-03" in r]
    return {"from": current, "to": current, "eligible": False,
            "reasons": reasons,
            "deferred": bool(downstream),
            "shortfall": {
                "trace_count": int(report_evidence.get("trace_count") or 0),
                "threshold": MIN_SAME_KIND_TRACES,
                "pattern_extracted": bool(report_evidence.get("pattern_extracted")),
            }}


# ════════════════════════════════════════════════════════════
#  stage 迁移执行（三处联动）
# ════════════════════════════════════════════════════════════


def _default_registry() -> Any:
    from agent.descriptors.registry import DescriptorRegistry
    return DescriptorRegistry()


def _emit_digest_stage_event(migration: StageMigration,
                             evidence: Dict[str, Any]) -> str:
    """发 `digest.stage` 事件（events.v1 信封；幂等键 = 迁移身份）"""
    try:
        from agent.observability.events import EV_DIGEST_STAGE, emit, trace_fields
        fields = trace_fields()
        payload = {
            "capability_id": migration.capability_id,
            "from_stage": migration.from_stage,
            "to_stage": migration.to_stage,
            "applied": migration.applied,
            "verdict": migration.verdict,
            "reasons": list(migration.reasons)[:10],
            "digest_run_id": migration.digest_run_id,
            "scope": evidence.get("scope") or "digestion",
            "trace_count": int(evidence.get("trace_count") or 0),
            "pattern_id": str(evidence.get("pattern_id") or ""),
            "workspace_id": fields.get("workspace_id", ""),
            "subject_id": fields.get("subject_id", ""),
            "trace_id": fields.get("trace_id", ""),
        }
        envelope = emit(EV_DIGEST_STAGE, payload, correlation_id=migration.digest_run_id,
                        idempotency_key=f"{migration.digest_run_id}:{migration.verdict}")
        return getattr(envelope, "event_id", "") or ""
    except Exception as e:  # noqa: BLE001  事件失败不得影响迁移结果
        logger.debug("digest.stage 事件发送失败: %s", e)
        return ""


def _audit_refusal(migration: StageMigration,
                   evidence: Dict[str, Any]) -> Tuple[int, str]:
    """被拒迁移入链（`digest.stage.refused`）—— 台账未变，故此处必须补链上留痕"""
    try:
        from agent.audit.facade import audit
        entry = audit.record(
            "digest.stage.refused",
            actor=str(evidence.get("actor") or "digestion_pipeline"),
            subject=f"capability:{migration.capability_id}",
            payload={"from": migration.from_stage, "to": migration.to_stage,
                     "verdict": migration.verdict,
                     "reasons": list(migration.reasons)[:10]},
            source="agent", status="refused",
            technical={"digest_run_id": migration.digest_run_id},
        )
        if entry is None:
            return 0, ""
        return int(getattr(entry, "seq", 0) or 0), str(getattr(entry, "self_hash", "") or "")
    except Exception as e:  # noqa: BLE001
        logger.debug("拒绝迁移入链失败: %s", e)
        return 0, ""


def _latest_stage_audit(capability_id: str,
                        action: str = "descriptor.stage") -> Tuple[int, str]:
    """取该能力最近一条 stage 审计（registry 独占写入的那条）的 seq/self_hash"""
    try:
        from agent.audit.facade import audit
        rows = audit.recent(limit=20, action=action)
        wanted = f"capability:{capability_id}"
        for entry in reversed(rows):
            if str(getattr(entry, "subject", "")) == wanted:
                return (int(getattr(entry, "seq", 0) or 0),
                        str(getattr(entry, "self_hash", "") or ""))
    except Exception as e:  # noqa: BLE001
        logger.debug("读取 stage 审计失败: %s", e)
    return 0, ""


def stage_migrate(
    capability_id: str,
    to_stage: Optional[str],
    evidence: Optional[Dict[str, Any]] = None,
    *,
    registry: Any = None,
    emit_event: bool = True,
    actor: str = "digestion_pipeline",
    reason: str = "",
) -> StageMigration:
    """驱动一次 stage 迁移：**写 descriptor + 审计 + digest.stage 事件**

    证据不足 / 非法迁移 / 边属下游 ⇒ **保持原 stage**，只记录（不静默推进，
    守"宁可冗余不可误合"）。任何异常都被收敛为 `StageMigration(verdict=error)`，
    **不抛给调用方**（消化流水线不得因台账问题中断）。

    Args:
        capability_id: 目标能力（canonical capability_id）
        to_stage: 目标态（七态取值；``None`` 表示"清空"——本任务不产生此动作）
        evidence: 证据（``first_entry`` / ``trace_count`` / ``pattern_extracted`` /
            ``pattern_steps`` / ``side_effect_profile`` / ``trace_policy`` ...）
        registry: DescriptorRegistry；缺省读默认台账 `data/descriptors.json`
        emit_event: 是否发 `digest.stage` 事件（用例可关，默认开）
        actor / reason: 审计与台账留痕的操作者与理由

    Returns:
        `StageMigration`（含 applied/verdict/reasons/event_id/audit_seq/audit_hash）
    """
    ev: Dict[str, Any] = dict(evidence or {})
    ev.setdefault("actor", actor)
    migration = StageMigration(
        capability_id=str(capability_id or ""),
        from_stage=None,
        to_stage=(str(to_stage) if to_stage else None),
        evidence=_scalar_view(ev),
    )
    migration.digest_run_id = digest_run_id(
        capability_id=migration.capability_id, from_stage=None,
        to_stage=migration.to_stage, evidence=ev)

    try:
        reg = registry if registry is not None else _default_registry()
        desc = reg.get(migration.capability_id)
    except Exception as e:  # noqa: BLE001
        migration.verdict = VERDICT_ERROR
        migration.reasons = [f"台账不可用: {type(e).__name__}: {e}"]
        # 台账读失败同样**不静默**：发事件留痕（台账未变，故不补链上拒绝记录）
        _finish(migration, ev, emit_event)
        return migration

    if desc is None:
        migration.verdict = VERDICT_NOT_FOUND
        migration.reasons = [f"台账中不存在 capability：{migration.capability_id}"]
        _finish(migration, ev, emit_event)
        return migration

    current = getattr(desc.evolution, "stage", None)
    migration.from_stage = _enum_value(current) or None
    migration.digest_run_id = digest_run_id(
        capability_id=migration.capability_id, from_stage=migration.from_stage,
        to_stage=migration.to_stage, evidence=ev)

    verdict, reasons = evaluate_migration(
        capability_id=migration.capability_id, from_stage=migration.from_stage,
        to_stage=migration.to_stage or "", evidence=ev)
    migration.verdict = verdict
    migration.reasons = reasons
    if verdict != VERDICT_APPLIED:
        _finish(migration, ev, emit_event)
        return migration

    # ── 落点 1：descriptor.evolution.stage（+ registry 内建的落点 2 链式审计）──
    try:
        # 三处联动的**共享关联键**：把 digest_run_id 固定写进链上 reason。
        # 为何必须显式写：链上记录与 `digest.stage` 事件是两条独立轨道，若不共享
        # 关联键，审计只能靠 (subject, detail.to) 隐式 join；且此前仅"流水线默认
        # reason"含 run id，**首次入轨路径传入自定义 reason 时不含** ⇒ 该路径的两轨
        # 无法互查（实现期实测确认的缺口）。此处收敛到单一位置统一追加。
        # 已核验：24/32/64 位 hex 关联键在链载荷脱敏下**原样保留**（未被掩码），
        # 故写进 reason 文本可安全反查。
        base_reason = reason or (
            f"TASK-S3-01 消化流水线 stage 迁移 "
            f"{migration.from_stage} → {migration.to_stage}")
        marker = f"run={migration.digest_run_id}"
        migration.audit_reason = (base_reason if marker in base_reason
                                 else f"{base_reason}（{marker}）")
        trace_policy = str(ev.get("trace_policy") or "")
        reg.set_stage(
            migration.capability_id, migration.to_stage,
            actor=actor, reason=migration.audit_reason,
            trace_policy=(trace_policy or None),
        )
    except Exception as e:  # noqa: BLE001
        migration.verdict = VERDICT_ERROR
        migration.reasons = [f"set_stage 失败（原 stage 保持不变）: "
                             f"{type(e).__name__}: {e}"]
        _finish(migration, ev, emit_event)
        return migration

    migration.applied = True
    migration.trace_policy = str(ev.get("trace_policy") or "")
    migration.audit_action = "descriptor.stage"
    migration.audit_seq, migration.audit_hash = _latest_stage_audit(
        migration.capability_id)
    _finish(migration, ev, emit_event)
    return migration


def _finish(migration: StageMigration, evidence: Dict[str, Any],
            emit_event: bool) -> None:
    """收尾：发事件；被拒迁移补链上留痕（台账未变的唯一情形）"""
    if emit_event:
        migration.event_id = _emit_digest_stage_event(migration, evidence)
    if not migration.applied and migration.verdict in (
            VERDICT_INSUFFICIENT_EVIDENCE, VERDICT_ILLEGAL_TRANSITION):
        migration.audit_action = migration.audit_action or "digest.stage.refused"
        migration.audit_seq, migration.audit_hash = _audit_refusal(migration, evidence)


# ════════════════════════════════════════════════════════════
#  首次入轨（消费 S1-02 遗留：stage 未入轨 warning）
# ════════════════════════════════════════════════════════════


def _enum_value(value: Any) -> str:
    """枚举成员 → 字符串值（`str, Enum` 混入下 `str(member)` 得到 ``SourceType.X``，
    故必须先取 `.value`——与 `events.normalize_type` 同一处理）"""
    if value is None:
        return ""
    inner = getattr(value, "value", value)
    return str(inner)


def _trace_policy_for(desc: Any) -> str:
    """按来源为 borrowed 生成**真实统一轨迹台账引用**（S3-01 L3 同一构造器）"""
    from agent.descriptors.bridge import ledger_trace_policy
    source_type = _enum_value(getattr(getattr(desc, "origin", None),
                                      "source_type", ""))
    source_id = str(getattr(getattr(desc, "origin", None), "source_id", "") or "")
    if source_type == "skill":
        return ledger_trace_policy(source="skill-import",
                                   capability_id=desc.capability_id,
                                   route="import-ledger")
    if source_type == "mcp":
        return ledger_trace_policy(source=source_id or "mcp",
                                   capability_id=desc.capability_id,
                                   route="call-side")
    return ledger_trace_policy(source=source_id or source_type or "builtin",
                               capability_id=desc.capability_id,
                               route="call-side")


#: trace_policy 占位串标记（S1-01/S2 期遗留：台账未建成时只能声明"策略待定"）
PLACEHOLDER_POLICY_MARKERS = ("pending", "(s2-", "s2-ledger-pending")


def is_placeholder_trace_policy(policy: Any) -> bool:
    """``trace_policy`` 是否为 S2 期**占位串**（非真实台账引用）"""
    text = str(policy or "").strip().lower()
    if not text:
        return False
    return any(marker in text for marker in PLACEHOLDER_POLICY_MARKERS)


def refresh_trace_policies(
    registry: Any = None,
    *,
    execute: bool = False,
    actor: str = "digestion_pipeline",
) -> Dict[str, Any]:
    """把 borrowed 资产的**占位** `trace_policy` 切换为真实台账引用（L3 收口）

    经 `registry.update_fields({"evolution.trace_policy": ...})` 写入 —— 该入口的
    审计动作是 ``descriptor.patch``（字段级变更），**不是** ``descriptor.stage``
    （stage 未变），故不会与 stage 迁移的留痕语义混淆，也不发 `digest.stage` 事件。

    Returns:
        {"scanned", "placeholder", "refreshed", "failed", "plan"}
    """
    reg = registry if registry is not None else _default_registry()
    rows = sorted(reg.list(), key=lambda d: d.capability_id)
    plan: List[Dict[str, Any]] = []
    for desc in rows:
        policy = getattr(desc.evolution, "trace_policy", "") or ""
        if not is_placeholder_trace_policy(policy):
            continue
        new_policy = _trace_policy_for(desc)
        plan.append({"capability_id": desc.capability_id, "old": str(policy),
                     "new": new_policy})
    result: Dict[str, Any] = {
        "executed": bool(execute), "scanned": len(rows),
        "placeholder": len(plan), "plan": plan,
        "refreshed": [], "failed": [],
    }
    if not execute:
        return result

    refreshed: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    for item in plan:
        try:
            reg.update_fields(
                item["capability_id"], {"evolution.trace_policy": item["new"]},
                actor=actor,
                reason=("TASK-S3-01【L3】：S2 占位串 → 真实统一轨迹台账引用"
                        f"（原串={item['old'][:80]}）"))
            refreshed.append({"capability_id": item["capability_id"],
                              "new": item["new"]})
        except Exception as e:  # noqa: BLE001
            failed.append({"capability_id": item["capability_id"],
                           "error": f"{type(e).__name__}: {e}"})
    result["refreshed"] = refreshed
    result["failed"] = failed
    return result


def first_entry_stage(desc: Any) -> Tuple[str, str, str]:
    """未入轨资产的**首次入轨态**与理由 → ``(stage, trace_policy, rationale)``

    裁定：七态主轨的**最低证据态**是 `borrowed`，故凡 stage 为空的存量资产一律以
    ``borrowed`` 起步（TASK-S3-01 §步骤 4）。**不直接置 ``native``**：S0-02 §3.4
    明确 native 需 30 天零回退台账，缺证据不置位（宁可冗余不可误合）。
    """
    source_type = _enum_value(getattr(getattr(desc, "origin", None),
                                      "source_type", ""))
    policy = _trace_policy_for(desc)
    rationale = (
        f"TASK-S3-01 首次入轨：stage 为空 → borrowed（七态主轨最低证据态）；"
        f"source_type={source_type}；不置 native（需 30 天零回退台账，S0-02 §3.4）")
    return "borrowed", policy, rationale


def backfill_stages(
    registry: Any = None,
    *,
    execute: bool = False,
    emit_events: bool = True,
    actor: str = "digestion_pipeline",
    limit: Optional[int] = None,
    refresh_policies: bool = True,
) -> Dict[str, Any]:
    """存量 stage 未入轨资产**首次入轨** + 占位 `trace_policy` 切换（L3+L4 收口）

    Args:
        execute: ``False``（默认）为**干跑**——只产出清单与计划，不写台账；
            ``True`` 才真正写入（写台账 + 链式审计 + `digest.stage` 事件）。
        limit: 仅处理前 N 条（便于分批；``None`` 为全部）
        refresh_policies: 同一趟顺带把占位 `trace_policy` 切到真实台账引用（L3）

    Returns:
        {"total_assets", "empty_stage", "ingested", "skipped", "failed",
         "plan": [...], "executed": bool, "by_source_type": {...},
         "residual": [...], "warnings_before", "warnings_after", "policies": {...}}
    """
    reg = registry if registry is not None else _default_registry()
    rows = sorted(reg.list(), key=lambda d: d.capability_id)
    if limit is not None:
        rows = rows[:int(limit)]

    empty_before: List[Any] = []
    for desc in rows:
        stage = getattr(desc.evolution, "stage", None)
        if not stage:
            empty_before.append(desc)

    plan: List[Dict[str, Any]] = []
    for desc in empty_before:
        stage, policy, rationale = first_entry_stage(desc)
        plan.append({
            "capability_id": desc.capability_id,
            "source_type": _enum_value(getattr(getattr(desc, "origin", None),
                                               "source_type", "")),
            "provenance": _enum_value(getattr(getattr(desc, "origin", None),
                                              "provenance", "")),
            "from": None,
            "to": stage,
            "trace_policy": policy,
            "rationale": rationale,
        })

    by_source: Dict[str, int] = {}
    for item in plan:
        key = str(item["source_type"] or "unknown")
        by_source[key] = by_source.get(key, 0) + 1

    result: Dict[str, Any] = {
        "executed": bool(execute),
        "total_assets": len(rows),
        "empty_stage": len(empty_before),
        "warnings_before": len(empty_before),
        "by_source_type": by_source,
        "plan": plan,
        "ingested": [],
        "failed": [],
        "residual": [],
        "warnings_after": len(empty_before),
    }
    if not execute:
        result["policies"] = (refresh_trace_policies(reg, execute=False)
                              if refresh_policies else {})
        return result

    ingested: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    for item in plan:
        migration = stage_migrate(
            item["capability_id"], item["to"], {
                "first_entry": True,
                "trace_policy": item["trace_policy"],
                "scope": "first_entry_backfill",
            },
            registry=reg, emit_event=emit_events, actor=actor,
            reason=item["rationale"])
        record = {
            "capability_id": item["capability_id"],
            "to": item["to"],
            "applied": migration.applied,
            "verdict": migration.verdict,
            "event_id": migration.event_id,
            "audit_seq": migration.audit_seq,
            "audit_hash": migration.audit_hash,
            "reasons": list(migration.reasons),
        }
        (ingested if migration.applied else failed).append(record)

    still_empty = []
    for desc in reg.list():
        stage = getattr(desc.evolution, "stage", None)
        if not stage:
            still_empty.append(desc.capability_id)

    result["ingested"] = ingested
    result["failed"] = failed
    result["residual"] = sorted(still_empty)
    result["warnings_after"] = len(still_empty)
    if not execute:
        result["policies"] = refresh_trace_policies(reg, execute=False)
        return result
    if refresh_policies:
        result["policies"] = refresh_trace_policies(
            reg, execute=True, actor=actor)
    return result


__all__ = [
    "SEVEN_STATES", "MAIN_CHAIN", "SIDE_STATES", "DRIVEN_EDGES",
    "DOWNSTREAM_EDGES", "ACCEPTANCE_PASSPORT_KEY", "INTERNALIZE_DECISION_KEY",
    "digest_run_id", "evaluate_migration", "recommended_stage",
    "stage_migrate", "first_entry_stage", "backfill_stages",
    "acceptance_passport_ok", "internalize_decision_ok",
]
