"""提示词自优化器（PromptOptimizer）— 任务 EVO-T4 上下文与知识进化闭环

【任务定位】
    落地设计文档的**上下文进化（Context Evolution）**维度：建立「提示词自优化」
    与「知识/记忆蒸馏自动反馈回路」。本模块是自优化器核心：
    输入一段提示词 + 一组样本任务（data/evals/）→ 用任务 2 真实评估器打分 →
    LLM 生成 2-3 个优化变体 → 变体评估 → 仅当提升超过阈值（默认 3%）产出
    PromptOptimizationProposal（原版/建议版/评分对比/理由）。

【不易边界（人机边界铁律）】
    1. 本模块**只产出建议，绝不自动应用**：没有任何应用/写回提示词的路径；
       应用必须走人工或任务 6 审批流（report_adoption 由审批流显式调用）。
    2. 评分全部来自任务 2 真实评估器（自一致性 / 反馈信号 / 客观校验），
       无样本 → status=no_samples，绝不伪造分数与建议。
    3. 每次优化事件写入谱系库（object_type=prompt），decision 为
       pending_review（建议）/ skipped（跳过），绝不自动 committed。

【避免循环依赖】
    本模块只依赖评估器（skills_mgmt.evaluator）与谱系（skills_mgmt.lineage），
    不反向依赖 knowledge 业务逻辑；蒸馏/反思通道通过本模块的 validate 接入。

【配置（.env，全部带默认值）】
    PROMPT_OPT_THRESHOLD            相对提升阈值，默认 0.03（3%）
    PROMPT_OPT_MAX_VARIANTS         变体数上限，默认 3（任务要求 2-3）
    PROMPT_OPT_ABS_MIN_SCORE        无基线绝对验证最低接受分，默认 0.5
    REFLECTOR_LESSON_VERIFIABLE_TYPES  反思 Lesson 可验证类别，默认 general,analyze,query
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from agent.skills_mgmt.evaluator import (
    EvalSamplePool,
    EvaluationResult,
    ExecOutcome,
    SkillExecutorEvaluator,
)
from agent.skills_mgmt.lineage import EvolutionRecord, get_default_archive

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════
#  常量与状态
# ════════════════════════════════════════════════════════════

# Proposal 状态（status）
STATUS_PROPOSED = "proposed"              # 建议已产出（suggested_prompt 非空，待审批）
STATUS_NO_IMPROVEMENT = "no_improvement"  # 建议版未超阈值，不产出建议
STATUS_NO_SAMPLES = "no_samples"          # 类别无评估样本，不产出伪建议
STATUS_NO_VARIANTS = "no_variants"        # 无法生成变体（LLM 不可用/解析失败）
STATUS_ERROR = "error"                    # 执行异常

# 建议来源（source）
SOURCE_EVALUATOR = "evaluator"        # 提示词自优化流程
SOURCE_DISTILL = "distill_feedback"   # 知识蒸馏反馈回路
SOURCE_REFLECTOR = "reflector"        # 反思 Lesson 通道

# 对比方式（comparison）
COMPARISON_PAIRED = "paired"          # 原版 vs 建议版（相对提升判定）
COMPARISON_ABSOLUTE = "absolute"      # 仅候选验证（蒸馏/反思无基线场景）

# ════════════════════════════════════════════════════════════
#  .env 配置读取（与 skills_mgmt 同模式：env 带默认值，非法值回退默认）
# ════════════════════════════════════════════════════════════


def _env_str(key: str, default: str) -> str:
    return os.getenv(key, default)


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(key, str(default))))
    except (TypeError, ValueError):
        return default


# ════════════════════════════════════════════════════════════
#  数据模型
# ════════════════════════════════════════════════════════════


@dataclass
class PromptOptimizationProposal:
    """一次提示词优化建议（只建议、不自动应用）

    status 语义:
        proposed        建议已产出（suggested_prompt 非空，待人工/审批流应用）
        no_improvement  建议版未达提升阈值（suggested_prompt=None）
        no_samples      类别无评估样本（不产出伪建议）
        no_variants     无法生成变体（LLM 不可用）
        error           执行异常

    comparison 语义:
        paired    原版 vs 建议版评分对比（improvement 为相对提升，可为负）
        absolute  仅候选提示词验证（无基线，improvement=None，用 abs_min_score 判定）
    """
    proposal_id: str
    object_type: str = "prompt"
    object_id: str = ""
    original_prompt: str = ""
    suggested_prompt: Optional[str] = None   # 仅 status=proposed 时有值
    original_score: Optional[float] = None   # 原版评分（absolute 时为 None）
    suggested_score: Optional[float] = None  # 建议版/候选评分
    improvement: Optional[float] = None      # 相对提升（absolute 时为 None）
    status: str = STATUS_PROPOSED
    comparison: str = COMPARISON_PAIRED
    source: str = SOURCE_EVALUATOR
    reason: str = ""
    category: str = "general"
    sample_count: int = 0
    record_id: str = ""                      # 谱系记录 ID（每次优化事件写入谱系）
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ════════════════════════════════════════════════════════════
#  提示词执行包装（复用任务2评估器的执行/校验/预算链路）
# ════════════════════════════════════════════════════════════


class _PromptSkill:
    """把提示词包装成 Skill 兼容对象（仅暴露评估器只读字段）

    Why 包装而非新写评分器: 任务 2 的 SkillExecutorEvaluator 已实现真实执行、
    客观校验、自一致性、反馈信号、预算熔断与 no_samples 降级——提示词评估
    直接复用该链路（简易），提示词通过 params.system_prompt 注入执行。
    """

    def __init__(self, prompt_id: str, category: str, prompt: str):
        self.id = prompt_id
        self.tags = [category]
        self.category = category
        self.default_params = {"system_prompt": prompt}


def _default_prompt_runner(llm: Any) -> Callable[[str, str, Dict[str, Any]], ExecOutcome]:
    """默认提示词执行器：以提示词为 system_prompt、样本任务为 user 消息调 LLM

    真实执行证据（不易）: 成功输出即 LLM 返回；LLM 不可用/异常 → 执行失败
    （样本跳过），绝不伪造输出。
    """
    def _run(prompt: str, task: str, params: Dict[str, Any]) -> ExecOutcome:
        if llm is None:
            return ExecOutcome(success=False, exit_code=-1,
                               stderr="LLM 不可用，无法执行提示词")
        t0 = time.time()
        try:
            raw = llm.chat([{"role": "user", "content": task}],
                           system_prompt=prompt)
            return ExecOutcome(success=True, exit_code=0, result=raw,
                               stdout=str(raw),
                               duration_ms=(time.time() - t0) * 1000)
        except Exception as e:  # noqa: BLE001 执行异常 → 样本跳过（不伪造）
            return ExecOutcome(success=False, exit_code=-1,
                               stderr=str(e)[:300],
                               duration_ms=(time.time() - t0) * 1000)
    return _run


def _make_skill_runner(prompt_runner: Callable) -> Callable[[Any, Dict[str, Any]], ExecOutcome]:
    """将 (prompt, task, params) 执行器适配为评估器要求的 (skill, params) 签名"""
    def _run(skill: Any, params: Dict[str, Any]) -> ExecOutcome:
        return prompt_runner(params.get("system_prompt", ""),
                             params.get("task", ""), params)
    return _run


# ════════════════════════════════════════════════════════════
#  变体生成（LLM）
# ════════════════════════════════════════════════════════════

_VARIANT_SYSTEM_PROMPT = (
    "你是提示词优化专家。基于给定的提示词，生成 {n} 个优化变体。\n"
    "要求：\n"
    "1. 保持原提示词的核心意图与语言风格；\n"
    "2. 每个变体只做有针对性的改进（补充约束、明确输出格式、降低歧义等），不要整体重写；\n"
    "3. 每个变体都是完整、可独立使用的提示词文本。\n"
    "只输出 JSON 数组，不要其他内容：[\"变体1\", \"变体2\", ...]"
)


def _parse_variants(raw: Any) -> List[str]:
    """解析 LLM 变体输出（JSON 数组，容忍 markdown 围栏）；失败返回空列表"""
    text = (raw or "").strip()
    if not text:
        return []
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [str(v) for v in data if isinstance(v, str) and v.strip()]


# ════════════════════════════════════════════════════════════
#  提示词自优化器
# ════════════════════════════════════════════════════════════


class PromptOptimizer:
    """提示词自优化器 — 打分 / 变体生成 / 择优对比 / 绝对验证，全部止步于建议

    Args:
        pool: 样本池（EvalSamplePool，默认 data/evals/）
        evaluator: 任务2评估器（默认 SkillExecutorEvaluator + 提示词执行器）
        llm: LLM 客户端（变体生成 + 默认执行器；duck-typing: chat(messages, system_prompt=)）
        prompt_runner: 提示词执行器 (prompt, task, params) -> ExecOutcome（测试注入点）
        variant_generator: 变体生成器 (prompt, n) -> List[str]（测试注入点）
        archive: 谱系档案库（默认 get_default_archive()）
        improvement_threshold: 相对提升阈值（默认 .env PROMPT_OPT_THRESHOLD=0.03）
        max_variants: 变体数上限（默认 .env PROMPT_OPT_MAX_VARIANTS=3，强制 2-3）
        abs_min_score: 绝对验证最低接受分（默认 .env PROMPT_OPT_ABS_MIN_SCORE=0.5）
    """

    def __init__(self, *, pool: Optional[EvalSamplePool] = None,
                 evaluator: Any = None, llm: Any = None,
                 prompt_runner: Optional[Callable] = None,
                 variant_generator: Optional[Callable] = None,
                 archive: Any = None,
                 improvement_threshold: Optional[float] = None,
                 max_variants: Optional[int] = None,
                 abs_min_score: Optional[float] = None,
                 consistency_runs: Optional[int] = None,
                 timeout_sec: Optional[int] = None,
                 budget_tokens: Optional[int] = None):
        self._pool = pool or EvalSamplePool()
        self._llm = llm
        self._prompt_runner = prompt_runner or _default_prompt_runner(llm)
        self._variant_generator = variant_generator or self._llm_generate_variants
        self._archive = archive if archive is not None else get_default_archive()
        self.improvement_threshold = (
            improvement_threshold if improvement_threshold is not None
            else _env_float("PROMPT_OPT_THRESHOLD", 0.03))
        # 任务要求变体限 2-3 个：无论配置多大都收敛到 [2, 3]
        self.max_variants = max(
            2, min(3, max_variants if max_variants is not None
                   else _env_int("PROMPT_OPT_MAX_VARIANTS", 3)))
        self.abs_min_score = (abs_min_score if abs_min_score is not None
                              else _env_float("PROMPT_OPT_ABS_MIN_SCORE", 0.5))
        self._evaluator = evaluator or SkillExecutorEvaluator(
            pool=self._pool,
            runner=_make_skill_runner(self._prompt_runner),
            timeout_sec=timeout_sec,
            consistency_runs=consistency_runs,
            budget_tokens=budget_tokens,
        )

    # ─── 评估 ───

    def evaluate_prompt(self, prompt: str, *, category: str = "general",
                        sample_ids: Optional[List[str]] = None,
                        prompt_id: Optional[str] = None,
                        params: Optional[Dict[str, Any]] = None) -> EvaluationResult:
        """用任务 2 评估器对提示词打分（真实执行证据）"""
        skill = _PromptSkill(prompt_id or self._prompt_id(category), category, prompt)
        run_params = dict(params or {})
        run_params["system_prompt"] = prompt
        return self._evaluator.evaluate(skill, sample_ids=sample_ids,
                                        params=run_params)

    # ─── 对比（原版 vs 建议版）───

    def compare(self, original_prompt: str, candidate_prompt: str, *,
                category: str = "general",
                sample_ids: Optional[List[str]] = None,
                prompt_id: Optional[str] = None,
                source: str = SOURCE_EVALUATOR,
                reason: str = "") -> PromptOptimizationProposal:
        """评估并对比原版与候选版，仅当提升超过阈值产出建议（不自动应用）"""
        t0 = time.time()
        prompt_id = prompt_id or self._prompt_id(category)
        orig = self.evaluate_prompt(original_prompt, category=category,
                                    sample_ids=sample_ids, prompt_id=prompt_id)
        cand = self.evaluate_prompt(candidate_prompt, category=category,
                                    sample_ids=sample_ids, prompt_id=prompt_id)
        # Why 日志为单行固定顺序 kv：多行日志会被日志分析平台按行拆散，
        # 字段顺序统一（orig_* → cand_* → improvement → verdict）便于 grok/dissect 提取
        no_samples = orig.status == STATUS_NO_SAMPLES or cand.status == STATUS_NO_SAMPLES
        improvement = None if no_samples else self._relative_improvement(orig.score, cand.score)
        if no_samples:
            verdict = "no_samples"
        elif improvement >= self.improvement_threshold:
            verdict = "proposed"
        else:
            verdict = "no_improvement"
        logger.info(
            "[PromptOpt] 对比评估 prompt_id=%s category=%s "
            "orig_score=%s orig_status=%s cand_score=%s cand_status=%s "
            "improvement=%s threshold=%.4f verdict=%s",
            prompt_id, category,
            f"{orig.score:.4f}", orig.status,
            f"{cand.score:.4f}", cand.status,
            f"{improvement:.4f}" if improvement is not None else "N/A",
            self.improvement_threshold, verdict)
        proposal = self._build_proposal(
            prompt_id, original_prompt, candidate_prompt, orig, cand,
            category=category, source=source, reason=reason,
            comparison=COMPARISON_PAIRED,
            cost_tokens=orig.cost_tokens + cand.cost_tokens,
            duration_ms=(time.time() - t0) * 1000)
        logger.info("[PromptOpt] 对比判定结果 prompt_id=%s category=%s "
                    "status=%s original=%s suggested=%s",
                    prompt_id, category, proposal.status,
                    proposal.original_score, proposal.suggested_score)
        return proposal

    # ─── 优化（变体生成 + 择优）───

    def optimize(self, prompt: str, *, category: str = "general",
                 sample_ids: Optional[List[str]] = None,
                 prompt_id: Optional[str] = None,
                 reason: str = "") -> PromptOptimizationProposal:
        """完整优化流程：当前提示词打分 → LLM 生成 2-3 变体 → 变体评估 → 择优对比

        建议版仅在最优变体相对提升超过阈值时产出；无变体（LLM 不可用）返回
        no_variants；无样本返回 no_samples。
        """
        t0 = time.time()
        prompt_id = prompt_id or self._prompt_id(category)
        variants = self.generate_variants(prompt)
        if not variants:
            return self._fail_proposal(
                prompt_id, prompt, category=category, status=STATUS_NO_VARIANTS,
                source=SOURCE_EVALUATOR,
                reason="无法生成优化变体（LLM 不可用或变体解析失败），不产出伪建议")
        orig = self.evaluate_prompt(prompt, category=category,
                                    sample_ids=sample_ids, prompt_id=prompt_id)
        best_variant, best_result = None, None
        for variant in variants[: self.max_variants]:
            vr = self.evaluate_prompt(variant, category=category,
                                      sample_ids=sample_ids, prompt_id=prompt_id)
            if best_result is None or vr.score > best_result.score:
                best_variant, best_result = variant, vr
        return self._build_proposal(
            prompt_id, prompt, best_variant, orig, best_result,
            category=category, source=SOURCE_EVALUATOR,
            reason=reason or "提示词自优化流程（LLM 生成 2-3 变体择优）",
            comparison=COMPARISON_PAIRED,
            cost_tokens=orig.cost_tokens + (best_result.cost_tokens if best_result else 0),
            duration_ms=(time.time() - t0) * 1000)

    # ─── 绝对验证（蒸馏/反思通道：无基线场景）───

    def validate(self, candidate_prompt: str, *,
                 original_prompt: Optional[str] = None,
                 category: str = "general",
                 sample_ids: Optional[List[str]] = None,
                 prompt_id: Optional[str] = None,
                 source: str = SOURCE_DISTILL,
                 reason: str = "") -> PromptOptimizationProposal:
        """验证候选提示词有效性（不自动应用）

        - original_prompt 提供 → 与原版对比（compare，相对提升超阈值才建议）；
        - 未提供 → 绝对验证：候选得分 ≥ abs_min_score 即建议。
        """
        if original_prompt is not None:
            return self.compare(original_prompt, candidate_prompt,
                                category=category, sample_ids=sample_ids,
                                prompt_id=prompt_id, source=source, reason=reason)
        t0 = time.time()
        prompt_id = prompt_id or self._prompt_id(category)
        cand = self.evaluate_prompt(candidate_prompt, category=category,
                                    sample_ids=sample_ids, prompt_id=prompt_id)
        return self._build_proposal(
            prompt_id, candidate_prompt, candidate_prompt, None, cand,
            category=category, source=source, reason=reason,
            comparison=COMPARISON_ABSOLUTE,
            cost_tokens=cand.cost_tokens,
            duration_ms=(time.time() - t0) * 1000)

    # ─── 变体生成 ───

    def generate_variants(self, prompt: str, n: Optional[int] = None) -> List[str]:
        """生成提示词优化变体（限 2-3 个，去重、剔除与原版相同者）"""
        n = max(2, min(self.max_variants, n if n is not None else self.max_variants))
        try:
            variants = self._variant_generator(prompt, n) or []
        except Exception as e:  # noqa: BLE001 变体生成失败 → 不产出伪建议
            logger.warning("[PromptOpt] 变体生成异常: %s", e)
            return []
        cleaned: List[str] = []
        for v in variants:
            text = str(v).strip()
            if text and text != prompt.strip() and text not in cleaned:
                cleaned.append(text)
        return cleaned[: n]

    def _llm_generate_variants(self, prompt: str, n: int) -> List[str]:
        if self._llm is None:
            return []
        raw = self._llm.chat([{"role": "user", "content": prompt}],
                             system_prompt=_VARIANT_SYSTEM_PROMPT.format(n=n))
        return _parse_variants(raw)

    # ─── 内部：提案构建 / 谱系 / 度量 ───

    def _build_proposal(self, prompt_id: str, original_prompt: str,
                        candidate_prompt: str,
                        orig_result: Optional[EvaluationResult],
                        cand_result: Optional[EvaluationResult], *,
                        category: str, source: str, reason: str,
                        comparison: str, cost_tokens: int,
                        duration_ms: float) -> PromptOptimizationProposal:
        no_samples = (
            (orig_result is not None and orig_result.status == STATUS_NO_SAMPLES)
            or (cand_result is not None and cand_result.status == STATUS_NO_SAMPLES)
        )
        if no_samples:
            proposal = PromptOptimizationProposal(
                proposal_id=self._gen_proposal_id(), object_id=prompt_id,
                original_prompt=original_prompt, status=STATUS_NO_SAMPLES,
                comparison=comparison, source=source,
                reason=reason or "类别无评估样本，不产出伪建议",
                category=category, sample_count=0,
                metadata=self._meta(cost_tokens, duration_ms))
        elif comparison == COMPARISON_PAIRED:
            orig_score = orig_result.score if orig_result else 0.0
            cand_score = cand_result.score if cand_result else 0.0
            improvement = self._relative_improvement(orig_score, cand_score)
            proposed = improvement >= self.improvement_threshold
            proposal = PromptOptimizationProposal(
                proposal_id=self._gen_proposal_id(), object_id=prompt_id,
                original_prompt=original_prompt,
                suggested_prompt=candidate_prompt if proposed else None,
                original_score=round(orig_score, 4),
                suggested_score=round(cand_score, 4),
                improvement=round(improvement, 4),
                status=STATUS_PROPOSED if proposed else STATUS_NO_IMPROVEMENT,
                comparison=comparison, source=source,
                reason=reason or self._improvement_reason(improvement, proposed),
                category=category,
                sample_count=max(orig_result.sample_count if orig_result else 0,
                                 cand_result.sample_count if cand_result else 0),
                metadata=self._meta(cost_tokens, duration_ms))
        else:  # COMPARISON_ABSOLUTE
            cand_score = cand_result.score if cand_result else 0.0
            proposed = cand_score >= self.abs_min_score
            proposal = PromptOptimizationProposal(
                proposal_id=self._gen_proposal_id(), object_id=prompt_id,
                original_prompt="",
                suggested_prompt=candidate_prompt if proposed else None,
                original_score=None, suggested_score=round(cand_score, 4),
                improvement=None,
                status=STATUS_PROPOSED if proposed else STATUS_NO_IMPROVEMENT,
                comparison=comparison, source=source,
                reason=reason or self._abs_reason(cand_score, proposed),
                category=category,
                sample_count=cand_result.sample_count if cand_result else 0,
                metadata=self._meta(cost_tokens, duration_ms))
        # 每次优化事件写入谱系（全状态都记，审计可追溯）
        proposal.record_id = self._record_lineage(proposal, orig_result, cand_result,
                                                  cost_tokens, duration_ms)
        self._emit_metrics(proposal)
        return proposal

    def _fail_proposal(self, prompt_id: str, prompt: str, *, category: str,
                       status: str, source: str, reason: str) -> PromptOptimizationProposal:
        """构造失败/跳过类提案（no_variants 等），同样写谱系与度量"""
        proposal = PromptOptimizationProposal(
            proposal_id=self._gen_proposal_id(), object_id=prompt_id,
            original_prompt=prompt, status=status, comparison=COMPARISON_PAIRED,
            source=source, reason=reason, category=category, sample_count=0)
        proposal.record_id = self._record_lineage(proposal, None, None, 0, 0)
        self._emit_metrics(proposal)
        return proposal

    def _record_lineage(self, proposal: PromptOptimizationProposal,
                        orig_result: Optional[EvaluationResult],
                        cand_result: Optional[EvaluationResult],
                        cost_tokens: int, duration_ms: float) -> str:
        """优化事件写入谱系（object_type=prompt；decision 不自动 committed）"""
        decision = ("pending_review" if proposal.status == STATUS_PROPOSED
                    else "skipped")
        eval_result = None
        if cand_result is not None and cand_result.status == "completed":
            eval_result = cand_result.to_eval_result_dict()
        elif orig_result is not None:
            eval_result = orig_result.to_eval_result_dict()
        trigger = ("scheduler" if proposal.source == SOURCE_DISTILL
                   else "feedback" if proposal.source == SOURCE_REFLECTOR
                   else "manual")
        record = EvolutionRecord(
            object_type="prompt",
            object_id=proposal.object_id,
            strategy="prompt_optimize",
            change_summary=self._change_summary(proposal),
            eval_result=eval_result,
            decision=decision,
            decision_reason=self._decision_reason(proposal),
            trigger=trigger,
            actor="system",
            cost={"tokens": int(cost_tokens), "duration_ms": round(duration_ms, 1)},
        )
        try:
            return self._archive.append(record)
        except Exception as e:  # noqa: BLE001 谱系不可用不阻断优化流程
            logger.warning("[PromptOpt] 谱系写入失败（不阻断）: %s", e)
            return ""

    def _emit_metrics(self, proposal: PromptOptimizationProposal) -> None:
        """yunshu_prompt_optimization_* 埋点（对齐 yunshu_skill_* 系列）"""
        try:
            from agent.skills_mgmt.observability import emit_metric
            emit_metric("yunshu_prompt_optimization_total",
                        labels={"outcome": proposal.status,
                                "source": proposal.source, "success": "true"})
            if proposal.improvement is not None:
                emit_metric("yunshu_prompt_optimization_improvement",
                            value=proposal.improvement, kind="histogram",
                            labels={"source": proposal.source, "success": "true"})
            logger.info(
                "[PromptOpt] event proposal=%s status=%s source=%s object=%s "
                "orig_score=%s cand_score=%s improvement=%s",
                proposal.proposal_id, proposal.status, proposal.source,
                proposal.object_id, proposal.original_score,
                proposal.suggested_score, proposal.improvement)
        except Exception:  # noqa: BLE001 埋点失败不影响主流程
            logger.debug("[PromptOpt] 度量埋点失败", exc_info=True)

    # ─── 内部：小工具 ───

    @staticmethod
    def _prompt_id(category: str) -> str:
        return f"prompt:{category}:{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _gen_proposal_id() -> str:
        ts = datetime.now().strftime("%Y%m%d%H%M%S%f")
        return f"ppo-{ts}-{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _meta(cost_tokens: int, duration_ms: float) -> Dict[str, Any]:
        return {"cost_tokens": int(cost_tokens),
                "duration_ms": round(duration_ms, 1)}

    @staticmethod
    def _relative_improvement(orig_score: float, cand_score: float) -> float:
        """相对提升：原版>0 用相对值，否则用绝对差值（避免除零）"""
        if orig_score > 0:
            return (cand_score - orig_score) / orig_score
        return cand_score - orig_score

    @staticmethod
    def _improvement_reason(improvement: float, proposed: bool) -> str:
        if proposed:
            return f"建议版评分提升 {improvement * 100:.1f}%（≥ 阈值），建议采纳"
        return f"建议版评分提升 {improvement * 100:.1f}%（< 阈值），不产出建议版"

    @staticmethod
    def _abs_reason(score: float, proposed: bool) -> str:
        if proposed:
            return f"候选提示词验证得分 {score:.3f} ≥ 最低接受分，建议采纳"
        return f"候选提示词验证得分 {score:.3f} < 最低接受分，不建议采纳"

    @staticmethod
    def _change_summary(proposal: PromptOptimizationProposal) -> str:
        if proposal.comparison == COMPARISON_PAIRED:
            return (f"提示词自优化评估：原版 {proposal.original_score} → "
                    f"建议版 {proposal.suggested_score}，提升 {proposal.improvement}"
                    f"（source={proposal.source}）")
        return (f"提示词绝对验证得分 {proposal.suggested_score}"
                f"（source={proposal.source}）")

    @staticmethod
    def _decision_reason(proposal: PromptOptimizationProposal) -> str:
        if proposal.status == STATUS_PROPOSED:
            return "优化建议已产出，默认不自动应用，待人工审批（任务6统一收口）"
        if proposal.status == STATUS_NO_SAMPLES:
            return "类别无评估样本，不产出伪建议"
        if proposal.status == STATUS_NO_VARIANTS:
            return "无法生成优化变体，本次跳过"
        return "建议版未达阈值/验证未通过，本次跳过"


# ════════════════════════════════════════════════════════════
#  采纳埋点（任务6审批流调用；本模块自身绝不自动应用）
# ════════════════════════════════════════════════════════════


def report_adoption(proposal_id: str, *, score_delta: Optional[float] = None) -> None:
    """采纳计数与采纳后评分变化埋点

    【不易】本函数只做度量埋点，不应用提示词；由任务 6 审批流在人工批准
    并应用后显式调用。
    """
    try:
        from agent.skills_mgmt.observability import emit_metric
        emit_metric("yunshu_prompt_optimization_adopted_total",
                    labels={"proposal_id": proposal_id, "success": "true"})
        if score_delta is not None:
            emit_metric("yunshu_prompt_optimization_score_delta",
                        value=float(score_delta), kind="histogram",
                        labels={"success": "true"})
    except Exception:  # noqa: BLE001 埋点失败不影响主流程
        logger.debug("[PromptOpt] 采纳埋点失败", exc_info=True)


# ════════════════════════════════════════════════════════════
#  反思 Lesson → 评估验证 → 优化建议通道（EVO-T4 步骤3）
# ════════════════════════════════════════════════════════════


class LessonEvalChannel:
    """反射 Lesson → 评估验证 → 优化建议管道

    作为 planning/reflector.Reflector 的可选 lesson_channel：
    - Lesson 命中可验证类别（task_type ∈ verifiable_task_types）且含解决方案时，
      将解决方案封装为候选提示词，转交 PromptOptimizer.validate 验证有效性；
    - 验证通过 → 产出 PromptOptimizationProposal（不自动应用），返回 proposal_id；
    - 未命中 / 不可验证 / 无优化器 → 返回 None（静默跳过，不改变既有反射行为）。

    Args:
        optimizer: PromptOptimizer（默认新建；测试注入 mock）
        verifiable_task_types: 可验证类别集合（默认 .env REFLECTOR_LESSON_VERIFIABLE_TYPES）
        category: 验证所用样本类别（默认 general）
    """

    def __init__(self, optimizer: Optional[PromptOptimizer] = None,
                 verifiable_task_types: Optional[List[str]] = None,
                 category: str = "general"):
        self._optimizer = optimizer if optimizer is not None else PromptOptimizer()
        if verifiable_task_types is None:
            verifiable_task_types = _env_str(
                "REFLECTOR_LESSON_VERIFIABLE_TYPES", "general,analyze,query"
            ).split(",")
        self.verifiable_task_types = {t.strip() for t in verifiable_task_types if t.strip()}
        self.category = category

    def is_verifiable(self, lesson: Any) -> bool:
        """命中可验证类别：task_type 在可验证集合内，且含解决方案或失败点

        Why 允许 failure_point 兜底: 反思引擎（learn_from_experience）产出的
        Lesson 默认 solution=None，只携带失败点——此时将失败点作为候选指令
        转交评估器验证同样有效（验证「识别失败 + 通用改进指令」是否提升质量）。
        """
        task_type = getattr(lesson, "task_type", "")
        if not task_type:
            logger.debug("[PromptOpt] Lesson 不可验证：无 task_type lesson=%s",
                         getattr(lesson, "id", ""))
            return False
        if task_type not in self.verifiable_task_types:
            logger.debug("[PromptOpt] Lesson 不可验证：task_type=%s 不在可验证集合 %s",
                         task_type, sorted(self.verifiable_task_types))
            return False
        if not (bool(getattr(lesson, "solution", None))
                or bool(getattr(lesson, "failure_point", None))):
            logger.debug("[PromptOpt] Lesson 不可验证：无解决方案且无失败点 "
                         "task_type=%s lesson=%s", task_type,
                         getattr(lesson, "id", ""))
            return False
        return True

    def submit_lesson(self, lesson: Any) -> Optional[str]:
        """转交评估验证；返回 proposal_id（仅 status=proposed），否则 None"""
        if self._optimizer is None:
            logger.debug("[PromptOpt] Lesson 未转交：无评估器 lesson=%s",
                         getattr(lesson, "id", ""))
            return None
        if not self.is_verifiable(lesson):
            return None
        prompt = self._lesson_to_prompt(lesson)
        logger.debug("[PromptOpt] Lesson 转交评估验证 lesson=%s category=%s 候选提示词=%s…",
                     getattr(lesson, "id", ""), self.category, prompt[:40].replace("\n", " "))
        proposal = self._optimizer.validate(
            prompt,
            category=self.category,
            prompt_id=f"prompt:lesson:{lesson.id}",
            source=SOURCE_REFLECTOR,
            reason=f"反思 Lesson({lesson.id}) 失败点: {getattr(lesson, 'failure_point', '')}")
        if proposal.status == STATUS_PROPOSED:
            logger.info("[PromptOpt] Lesson 验证通过 → 建议已产出 lesson=%s proposal=%s",
                        lesson.id, proposal.proposal_id)
            return proposal.proposal_id
        logger.info("[PromptOpt] Lesson 验证未通过（%s）lesson=%s",
                    proposal.status, lesson.id)
        return None

    @staticmethod
    def _lesson_to_prompt(lesson: Any) -> str:
        solution = getattr(lesson, "solution", None)
        instruction = solution if solution else (
            f"针对失败点「{getattr(lesson, 'failure_point', '')}」优化执行策略，"
            "先分析原因再重试，必要时调整执行方式。"
        )
        return (f"任务类型：{lesson.task_type}\n"
                f"已识别失败点：{getattr(lesson, 'failure_point', '')}\n"
                f"改进指令：{instruction}")


__all__ = [
    "PromptOptimizationProposal",
    "PromptOptimizer",
    "LessonEvalChannel",
    "report_adoption",
    "STATUS_PROPOSED",
    "STATUS_NO_IMPROVEMENT",
    "STATUS_NO_SAMPLES",
    "STATUS_NO_VARIANTS",
    "SOURCE_EVALUATOR",
    "SOURCE_DISTILL",
    "SOURCE_REFLECTOR",
]
