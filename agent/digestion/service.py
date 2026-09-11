"""消化流水线门面 `DigestionService`（TASK-S3-01 步骤 1 / v7.2 §4.5）

**范围**（本任务）：S2-01 统一台账 → 轨迹清洗 → 模式挖掘 → SKILL.md 草稿 →
stage 迁移建议/驱动。**范围外**（如实标注、不越权）：确定性回放沙箱与验收门
（S3-02）、shadow/灰度与内化（S3-03）、发布签名。

**三轨复用关系**（任务书要求"避免第三套固化逻辑"）：

| 既有轨 | 保留的入口 | 与本管道的关系 |
|---|---|---|
| ① `skills_mgmt` 评审-消化 | `assess`/`review` 权威评审（人工/门禁） | 本管道产物停在 **draft**，发布权与评审权**仍归①**；`generation.solidify_draft()` 是 opt-in 桥接（强制 `run_review=False`） |
| ② `process_distill` 素材蒸馏 | `ProcessDistillService.distill`（素材→子代理蒸馏） | 本管道**复用其正文编译器** `solidify._compile_skill_content` 与 `DistilledProcess` 模型；素材侧入口不变 |
| ③ `workflow_learning` 轨迹序列学习 | `WorkflowLearningService`（序列学习 L2，中间过渡不入态） | 本管道复用其**质量门控词汇**（`MIN_SUCCESS_COUNT`/`MIN_CONFIDENCE`/`MIN_PRIORITY`）与 `LearnedWorkflow` 载体；L2 产物经 `generation.to_learned_workflow()` 归一到同一 L3 产物形态 |

即：三条入口各自保留，**L3 产物（SKILL.md + `evolution.stage` 叠加）统一由本管道
落地**，不再新增第四套产物定义。

**事件与审计**：`digest.stage` / `skill.generated` 经 `agent.observability.events.emit()`
（events.v1 信封）；stage 写入的链式审计由 descriptor registry 独占（单一写入方），
被拒迁移补 `digest.stage.refused` —— 见 `stage.py` 模块文档。
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import capability as capability_mod
from . import cleaning, generalize, generation, mining, stage as stage_mod
from .models import (
    MIN_PATTERN_STEPS,
    MIN_SAME_KIND_TRACES,
    CandidatePattern,
    DigestionReport,
    OUTCOME_SUCCESS,
    SameTaskKey,
    SkillDraft,
    Trajectory,
    TraceSet,
)

logger = logging.getLogger("agent.digestion.service")


def _pattern_id(key: SameTaskKey, labels: Sequence[str]) -> str:
    """确定性 pattern_id（键 + 骨架 ⇒ 同一模式恒同一 id，重复运行不产生新产物）"""
    material = "|".join([key.as_str(), ",".join(labels)])
    return "pat_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _coverage_confidence(coverage: float, support: int,
                         sample_size: int) -> float:
    """置信度（确定性合成：覆盖率 6 成 + 样本充足度 4 成，上限 1.0）"""
    sufficiency = min(1.0, support / float(MIN_SAME_KIND_TRACES)) if MIN_SAME_KIND_TRACES else 0.0
    return round(min(1.0, 0.6 * float(coverage) + 0.4 * sufficiency), 4)


class DigestionService:
    """消化流水线门面（轨迹 → 清洗 → 挖掘 → 草稿 → stage 迁移）

    用法::

        svc = DigestionService()                      # 默认读 data/descriptors.json
        report = svc.pipeline(capability_id="cp.builtin.read_file")
        report.eligible, report.candidates, report.drafts, report.stage_migration
    """

    def __init__(
        self,
        *,
        store: Any = None,
        registry: Any = None,
        threshold: int = MIN_SAME_KIND_TRACES,
        draft_dir: str = "",
        persist_drafts: bool = True,
        emit_events: bool = True,
    ) -> None:
        self._store = store
        self._registry = registry
        self.threshold = int(threshold)
        self.draft_dir = draft_dir or generation.DEFAULT_DRAFT_DIR
        self.persist_drafts = bool(persist_drafts)
        self.emit_events = bool(emit_events)

    # ── 依赖（懒加载，便于测试注入与零副作用导入） ──────────────

    @property
    def store(self) -> Any:
        if self._store is None:
            from agent.observability.trace_v2 import UnifiedTraceStore
            self._store = UnifiedTraceStore()
        return self._store

    @property
    def registry(self) -> Any:
        if self._registry is None:
            from agent.descriptors.registry import DescriptorRegistry
            self._registry = DescriptorRegistry()
        return self._registry

    # ── 步骤 1：采集 ─────────────────────────────────────────

    def collect(self, capability_id: str, *, limit: int = 200
                ) -> Tuple[List[Any], Dict[str, Any]]:
        """从统一台账取该能力的全部轨迹行（含历史工具名键，见 `capability.py`）"""
        return capability_mod.collect_rows(
            self.store, capability_id, limit=limit, registry=self._registry)

    # ── 步骤 2：清洗 → 轨迹 ──────────────────────────────────

    def build_trajectories(
        self,
        rows: Sequence[Any],
        *,
        capability_id: str,
        intent: str = "",
        limit: int = 200,
    ) -> Tuple[List[Trajectory], Dict[str, Any]]:
        """轨迹行 → **同类判定键**分组前的清洗后轨迹列表

        一条轨迹 = 一个 ``task_id`` 的全部能力级行（按 ``started_at`` 升序）；
        任务级行（``capability_id`` 为空）**不作为步骤**，只用于取任务结果状态与
        工作区（否则每条轨迹都会多出一个恒定标签，污染骨架）。
        """
        groups = capability_mod.group_rows_by_task(rows)
        trajectories: List[Trajectory] = []
        seeds: List[Tuple[str, Any]] = []
        for task_id, task_rows in groups.items():
            steps = [r for r in task_rows if str(getattr(r, "capability_id", "") or "")]
            if not steps:
                continue
            seed = self._pick_seed(steps, capability_id)
            if seed is None:
                continue
            key = cleaning.same_task_key(seed, capability_id=capability_id,
                                         intent=intent)
            key = self._key_with_task_outcome(key, task_rows)
            task_level = self._task_level_row(task_rows)
            workspace = str(getattr(getattr(task_level, "tenancy", None),
                                    "workspace_id", "") or "")
            started = min(float(getattr(r.timing, "started_at", 0.0) or 0.0)
                          for r in steps)
            traj = cleaning.trajectory_from_rows(
                steps, key=key, source_trace_id=str(getattr(seed, "trace_id", "") or ""),
                task_id=task_id, workspace_id=workspace, started_at=started)
            trajectories.append(traj)
            seeds.append((task_id, seed))

        deduped = cleaning.dedupe_by_task(trajectories)
        if limit is not None and limit > 0 and len(deduped) > int(limit):
            deduped = deduped[-int(limit):]
        meta = {
            "tasks": len(groups),
            "trajectories": len(deduped),
            "raw_trajectory_count": len(trajectories),
            "dropped_duplicates": len(trajectories) - len(deduped),
            "dropped_steps": sum(t.dropped_steps for t in deduped),
            "merged_steps": sum(t.merged_steps for t in deduped),
            "negative_trajectories": sum(1 for t in deduped if t.is_negative),
            "intent_keys": sorted({t.key.intent_key for t in deduped}),
            "noise_flags": sorted({flag for t in deduped for flag in t.noise_flags}),
        }
        return deduped, meta

    @staticmethod
    def _pick_seed(steps: Sequence[Any], capability_id: str) -> Optional[Any]:
        """同任务内的"种子行"：优先取目标能力的行（其参数形态决定 intent_key）"""
        for row in steps:
            if str(getattr(row, "capability_id", "") or "") == capability_id:
                return row
        for row in steps:
            resolved = str(capability_mod.resolve(
                str(getattr(row, "capability_id", "") or ""))["capability_id"])
            if resolved == capability_id:
                return row
        return steps[0] if steps else None

    @staticmethod
    def _task_level_row(task_rows: Sequence[Any]) -> Optional[Any]:
        for row in task_rows:
            if not str(getattr(row, "capability_id", "") or ""):
                return row
        return None

    @staticmethod
    def _key_with_task_outcome(key: SameTaskKey, task_rows: Sequence[Any]
                               ) -> SameTaskKey:
        """把 outcome 换成**任务级**结果（任务级行缺失时退回步骤聚合）"""
        task_level = None
        for row in task_rows:
            if not str(getattr(row, "capability_id", "") or ""):
                task_level = row
                break
        if task_level is not None:
            outcome = cleaning.classify_outcome(
                getattr(getattr(task_level, "response", None), "status", ""))
        else:
            steps = [r for r in task_rows
                     if str(getattr(r, "capability_id", "") or "")]
            outcome = cleaning.classify_outcome(
                "success" if steps and all(
                    str(getattr(getattr(r, "response", None), "status", "")) == "success"
                    for r in steps) else "error")
        return SameTaskKey(capability_id=key.capability_id,
                           intent_key=key.intent_key, outcome=outcome)

    # ── 步骤 3：挖掘 ─────────────────────────────────────────

    def mine(self, trace_set: TraceSet, *,
             failure_set: Optional[TraceSet] = None) -> Optional[CandidatePattern]:
        """一个同类轨迹集 → 候选模式（LCS 骨架 + 参数槽 + 决策树分支 + 副作用画像）"""
        successful = [t for t in trace_set.trajectories if not t.is_negative]
        if len(successful) < self.threshold:
            return None
        matrix = [t.labels for t in sorted(successful, key=lambda x: x.trajectory_id)]
        backbone, medoid = mining.consensus_backbone(matrix)
        if not backbone:
            return None
        labels = [label for label, _, _ in backbone]
        coverage = mining.backbone_coverage(labels, matrix)
        failure_seqs = ([t.labels for t in sorted(failure_set.trajectories,
                                                 key=lambda x: x.trajectory_id)]
                        if failure_set else [])
        branches = mining.extract_branches(matrix, failure_seqs, labels)

        # 参数槽：跨轨迹取值有差异的键 → 具名占位符
        steps_for_slots: List[List[Any]] = []
        label_matrix: List[List[str]] = []
        for traj in sorted(successful, key=lambda x: x.trajectory_id):
            steps_for_slots.append([(s.label, s.params) for s in traj.steps])
            label_matrix.append(traj.labels)
        slots = generalize.infer_parameter_slots(steps_for_slots,
                                                labels_per_trajectory=label_matrix)

        capability_of: Dict[str, str] = {}
        condition_of: Dict[str, str] = {}
        for traj in successful:
            for step in traj.steps:
                capability_of.setdefault(step.label, step.capability_id)
        for branch in branches:
            if branch.at_step > 0 and branch.at_step <= len(labels):
                condition_of.setdefault(labels[branch.at_step - 1],
                                        branch.condition)
        optional = mining.optional_labels(labels, matrix)
        pattern_steps = mining.backbone_to_steps(
            backbone, capability_of=capability_of, condition_of=condition_of,
            optional=optional)
        # 参数槽挂到所属骨架步骤上，使 CandidatePattern 自包含（生成器无需再对齐）
        for slot in slots:
            for pstep in pattern_steps:
                if pstep.label == slot.step_label:
                    pstep.params[slot.name] = slot.placeholder
                    break
        pattern = CandidatePattern(
            pattern_id=_pattern_id(trace_set.key, labels),
            key=trace_set.key,
            steps=pattern_steps,
            slots=slots,
            branches=branches,
            support=len(successful),
            sample_size=len(successful) + (len(failure_set.trajectories)
                                           if failure_set else 0),
            coverage=coverage,
            lcs_length=len(labels),
            confidence=_coverage_confidence(coverage, len(successful),
                                            len(successful)),
            side_effect_profile=mining.side_effect_profile(successful),
            negative_samples=(len(failure_set.trajectories) if failure_set else 0),
        )
        pattern.side_effect_profile["medoid_index"] = medoid
        return pattern

    # ── 事件 ─────────────────────────────────────────────────

    def _emit_skill_generated(self, draft: SkillDraft,
                              pattern: CandidatePattern,
                              digest_run: str) -> str:
        if not self.emit_events:
            return ""
        try:
            from agent.observability.events import EV_SKILL_GENERATED, emit, trace_fields
            fields = trace_fields()
            envelope = emit(EV_SKILL_GENERATED, {
                "skill_id": draft.skill_id,
                "pattern_id": pattern.pattern_id,
                "capability_id": pattern.key.capability_id,
                "status": draft.status,
                "track": draft.source_track,
                "support": pattern.support,
                "coverage": pattern.coverage,
                "confidence": pattern.confidence,
                "digest_run_id": digest_run,
                "workspace_id": fields.get("workspace_id", ""),
                "trace_id": fields.get("trace_id", ""),
            }, correlation_id=digest_run,
                idempotency_key=f"{draft.skill_id}:{digest_run}")
            return getattr(envelope, "event_id", "") or ""
        except Exception as e:  # noqa: BLE001
            logger.debug("skill.generated 事件失败: %s", e)
            return ""

    # ── 门面：端到端管道 ─────────────────────────────────────

    def pipeline(
        self,
        *,
        capability_id: str = "",
        trace_set: Optional[TraceSet] = None,
        intent: str = "",
        to_stage: Optional[str] = None,
        migrate: bool = True,
        persist_draft: Optional[bool] = None,
        limit: int = 200,
    ) -> DigestionReport:
        """端到端消化：台账 → 清洗 → 挖掘 → 草稿 → stage 建议/迁移

        Args:
            capability_id: 目标能力（canonical 或工具名；入口会做 L1 归一）
            trace_set: 直接给定同类轨迹集（与 ``capability_id`` 二选一，
                供上游已自行采集的场景复用；给定时跳过台账采集）
            intent: 显式任务意图（覆盖参数形态指纹推导）
            to_stage: 指定目标态；``None`` 表示"按建议走"（未入轨 → borrowed；
                已 borrowed 且达 §3.3 条件 → mirrored）
            migrate: 是否真正驱动 stage 迁移（``False`` 只给建议，不写台账）
            persist_draft: 是否把草稿落盘到暂存区（默认取实例配置）
            limit: 采集上限
        """
        cid = str(capability_id or "").strip()
        resolution: Dict[str, Any] = {}
        rows: List[Any] = []
        collect_meta: Dict[str, Any] = {}

        if trace_set is None:
            cid = str(capability_mod.resolve(cid, registry=self._registry
                                             )["capability_id"] or cid)
            resolution = capability_mod.resolve(cid, registry=self._registry)
            rows, collect_meta = self.collect(cid, limit=limit)
            trajectories, clean_meta = self.build_trajectories(
                rows, capability_id=cid, intent=intent, limit=limit)
        else:
            cid = cid or trace_set.key.capability_id
            trajectories = list(trace_set.trajectories)
            clean_meta = {
                "tasks": len({t.task_id for t in trajectories}),
                "trajectories": len(trajectories),
                "raw_trajectory_count": len(trajectories),
                "dropped_duplicates": 0,
                "dropped_steps": sum(t.dropped_steps for t in trajectories),
                "merged_steps": sum(t.merged_steps for t in trajectories),
                "negative_trajectories": sum(1 for t in trajectories
                                             if t.is_negative),
                "intent_keys": sorted({t.key.intent_key for t in trajectories}),
                "noise_flags": sorted({f for t in trajectories
                                       for f in t.noise_flags}),
            }

        digest_run = "dg_" + hashlib.sha256(
            f"{cid}|{clean_meta['trajectories']}|{intent}".encode("utf-8")
        ).hexdigest()[:24]

        report = DigestionReport(
            capability_id=cid,
            digest_run_id=digest_run,
            threshold=self.threshold,
            rows_total=collect_meta.get("matched", len(rows)),
            trajectories_total=clean_meta["trajectories"],
            trajectories_cleaned=len(trajectories),
            negative_total=clean_meta["negative_trajectories"],
            intent_keys=list(clean_meta["intent_keys"]),
            cleanup=dict(clean_meta),
            capability_resolution=dict(resolution),
        )
        if collect_meta:
            report.cleanup["collect"] = {
                k: collect_meta[k] for k in
                ("keys", "legacy_keys", "legacy_matched", "total_in_ledger")}

        # 同类分组
        buckets = cleaning.group_by_same_task(trajectories)
        report.trace_sets = [
            {"key": key, "size": bucket.size,
             "negative": bucket.negative_count}
            for key, bucket in sorted(buckets.items())
        ]

        # 主集合：成功的、规模最大的一组（同分取 key 字典序最小 —— 确定性）
        success_sets = [b for b in buckets.values()
                        if b.key.outcome == OUTCOME_SUCCESS]
        if not success_sets:
            report.eligible = False
            report.reason = (f"无成功同类轨迹集（共 {len(buckets)} 组键，"
                             f"失败轨迹 {clean_meta['negative_trajectories']} 条"
                             f"保留为负样本）")
            report.stage_recommendation = stage_mod.recommended_stage({
                "capability_id": cid, "current_stage": self._current_stage(cid),
                "trace_count": 0, "pattern_extracted": False})
            return report
        primary = sorted(success_sets, key=lambda b: (-b.size, b.key.as_str()))[0]
        bucket = buckets[primary.key.as_str()]      # 由 primary 直接索引，必存在
        failure_key = SameTaskKey(primary.key.capability_id, primary.key.intent_key,
                                  "failure")
        failure_set = buckets.get(failure_key.as_str())
        report.cleanup["primary_key"] = primary.key.as_str()
        report.cleanup["primary_size"] = primary.size
        if failure_set is not None:
            report.cleanup["paired_failure_size"] = failure_set.size

        # 挖掘
        pattern = self.mine(bucket, failure_set=failure_set)
        current_stage = self._current_stage(cid)
        evidence: Dict[str, Any] = {
            "capability_id": cid,
            "current_stage": current_stage,
            "trace_count": pattern.support if pattern is not None else primary.size,
            "pattern_extracted": bool(pattern is not None and pattern.steps),
            "pattern_steps": len(pattern.steps) if pattern is not None else 0,
            "side_effect_profile": (pattern.side_effect_profile
                                    if pattern is not None else {}),
            "pattern_id": pattern.pattern_id if pattern is not None else "",
            "intent_key": primary.key.intent_key,
        }
        # 建议**总是**产出（含"没推进"的情形），使缺口在报告里看得见
        report.stage_recommendation = stage_mod.recommended_stage(evidence)
        if pattern is None:
            report.eligible = False
            report.reason = (
                f"同类轨迹 {primary.size} 条 < 条数门槛 {self.threshold} 条"
                if primary.size < self.threshold else "骨架挖掘为空（无可提取模式）")
            return report
        report.candidates = [pattern]
        if pattern.is_shallow:
            report.eligible = False
            report.reason = (f"骨架步骤 {len(pattern.steps)} < {MIN_PATTERN_STEPS}"
                             f"（单步骨架不成模式，不产出可升格产物）")
        else:
            report.eligible = True
            report.reason = (f"同类轨迹 {pattern.support} 条（≥{self.threshold}）"
                             f"且骨架 {len(pattern.steps)} 步可提取")

        # 生成 draft（无论是否达升格门槛都产出草稿，由 gate 在 front matter 标注）
        should_persist = (self.persist_drafts if persist_draft is None
                          else bool(persist_draft))
        drafts: List[SkillDraft] = []
        for candidate in report.candidates:
            draft = generation.build_skill_draft(candidate)
            if should_persist:
                generation.persist_skill_draft(draft, draft_dir=self.draft_dir)
            event_id = self._emit_skill_generated(draft, candidate, digest_run)
            drafts.append(draft)
            if event_id:
                report.events.append(event_id)
        report.drafts = drafts

        # stage 建议与迁移
        if migrate:
            target = to_stage
            if target is None:
                if current_stage is None:
                    target = "borrowed"
                elif current_stage == "borrowed" and report.eligible:
                    target = "mirrored"
                elif current_stage == "borrowed" and report.candidates:
                    # 已挖出模式但证据未达 §3.3 条件 → **显式尝试并记录拒绝**
                    # （台账不变时链上本无记录，故由 stage_migrate 补
                    #   `digest.stage.refused`，使"没推进"不静默）
                    target = "mirrored"
            if target:
                policy = ""
                if target == "borrowed":
                    from .stage import _trace_policy_for  # 同一构造器
                    try:
                        policy = _trace_policy_for(self.registry.get(cid))
                    except Exception:  # noqa: BLE001
                        policy = ""
                    evidence["first_entry"] = current_stage is None
                    evidence["trace_policy"] = policy
                migration = stage_mod.stage_migrate(
                    cid, target, evidence, registry=self._registry,
                    emit_event=self.emit_events)
                report.stage_migration = migration
                if migration.event_id:
                    report.events.append(migration.event_id)
        return report

    def _current_stage(self, capability_id: str) -> Optional[str]:
        try:
            desc = self.registry.get(capability_id)
        except Exception as e:  # noqa: BLE001
            logger.debug("读取当前 stage 失败: %s", e)
            return None
        if desc is None:
            return None
        stage = getattr(desc.evolution, "stage", None)
        if stage is None:
            return None
        return str(getattr(stage, "value", stage) or "")

    # ── L4：存量首次入轨 ─────────────────────────────────────

    def ingest_unstaged(self, *, execute: bool = False,
                        limit: Optional[int] = None) -> Dict[str, Any]:
        """存量 stage 未入轨资产首次入轨（薄封装 `stage.backfill_stages`）"""
        return stage_mod.backfill_stages(
            self._registry, execute=execute, emit_events=self.emit_events,
            limit=limit)


__all__ = ["DigestionService"]
