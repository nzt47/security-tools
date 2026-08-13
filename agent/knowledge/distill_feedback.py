"""知识蒸馏反馈回路 — 任务 EVO-T4 上下文与知识进化闭环

【任务定位】
    周期性地将「高频失败模式」与「优质案例/知识蒸馏产物模式」汇总为
    提示词/技能优化建议，复用任务 2 评估器验证建议有效性，输出进入
    PromptOptimizationProposal 管道（与提示词自优化同一管道），不自动应用。

【数据源】
    1. agent/feedback.py 已归档的优质案例（QualityCase）；
    2. agent/feedback.py 负面反馈（失败模式，dislike 按分类聚合）；
    3. agent/knowledge 蒸馏产物（knowledge/processed/*.md 已确认笔记）。

【不易边界（人机边界铁律）】
    1. 本模块所有产物止步于「建议」（PromptOptimizationProposal / DistillSuggestion），
       绝不自动应用；应用必须经人工审批（任务 6 统一收口）。
    2. 无验证能力（无 PromptOptimizer / 无样本）时不产出伪建议。
    3. 只读汇总 knowledge 产物，不修改任何卡片/笔记状态，不触碰 conflict /
       workflow 的人机裁决边界。

【配置（.env，全部带默认值）】
    DISTILL_FEEDBACK_MIN_FREQUENCY   失败模式最低出现频次，默认 2
    DISTILL_FEEDBACK_TOP_N           每类聚合条数上限，默认 3
    DISTILL_FEEDBACK_INTERVAL_DAYS   汇总周期窗口（天），默认 7
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.cognitive.prompt_optimizer import (
    PromptOptimizationProposal,
    PromptOptimizer,
    SOURCE_DISTILL,
)

logger = logging.getLogger(__name__)

# 失败模式 → 可直接追加到提示词的改进指令（与 feedback.py 建议对齐）
_FAILURE_INSTRUCTION_MAP = {
    "accuracy": "在回答前先进行事实核查，仅陈述有依据的事实；不确定时明确说明，不编造。",
    "quality": "提高回答质量：结构清晰、避免含糊表述，先给结论再给论证。",
    "relevance": "严格围绕用户问题作答，先确认问题意图再回答，避免偏离主题。",
    "completeness": "回答须完整覆盖用户问题的各维度；信息不足时先说明再补充追问。",
    "speed": "优先给出直接结论，减少冗长铺垫，提升响应速度。",
    "safety": "拒绝不安全内容，遵守安全边界；发现越界立即终止并说明。",
    "usability": "输出格式清晰易读，善用列表与分段，贴合使用场景。",
}

# 优质案例模式 → 强化指令（按技能/类别，缺省走 general）
_QUALITY_INSTRUCTION_MAP = {
    "general": "保持稳定输出：对同类问题给出结构一致、可复现的高质量回答。",
    "accuracy": "延续准确的事实陈述风格，并为关键结论补充依据。",
    "search": "保持检索结果的精确匹配与来源标注习惯。",
    "code": "延续可运行、带测试的代码输出风格。",
    "chat": "保持自然连贯、有温度的对话风格。",
}


def _env_int(key: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(key, str(default))))
    except (TypeError, ValueError):
        return default


# ════════════════════════════════════════════════════════════
#  数据模型
# ════════════════════════════════════════════════════════════


@dataclass
class DistillSuggestion:
    """一条结构化蒸馏优化建议（验证前的聚合产物）"""
    suggestion_id: str
    kind: str                        # failure_pattern | quality_pattern | knowledge_insight
    category: str                    # 反馈分类 / 技能类别 / knowledge
    description: str
    suggested_instruction: str       # 可直接追加到提示词的指令文本
    evidence_count: int
    priority: str                    # high / medium / low
    evidence: List[str] = field(default_factory=list)   # 样例评论/案例标题/要点
    target_skill_id: str = ""
    source: str = "feedback"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ════════════════════════════════════════════════════════════
#  蒸馏反馈汇总器
# ════════════════════════════════════════════════════════════


class DistillFeedbackSummarizer:
    """蒸馏反馈汇总器 — 收集 → 汇总 → 验证 → 优化建议管道

    Args:
        feedback_manager: feedback.FeedbackManager（duck-typing；默认 get_feedback_manager()）
        optimizer: PromptOptimizer（建议验证用；None 时 run() 不产出建议）
        min_frequency: 失败模式最低出现频次（默认 .env DISTILL_FEEDBACK_MIN_FREQUENCY=2）
        top_n: 每类聚合条数上限（默认 .env DISTILL_FEEDBACK_TOP_N=3）
        interval_days: 汇总周期窗口（默认 .env DISTILL_FEEDBACK_INTERVAL_DAYS=7）
    """

    def __init__(self, feedback_manager: Any = None,
                 optimizer: Optional[PromptOptimizer] = None,
                 min_frequency: Optional[int] = None,
                 top_n: Optional[int] = None,
                 interval_days: Optional[int] = None):
        self._feedback = feedback_manager
        self._optimizer = optimizer
        self.min_frequency = (min_frequency if min_frequency is not None
                              else _env_int("DISTILL_FEEDBACK_MIN_FREQUENCY", 2))
        self.top_n = (top_n if top_n is not None
                      else _env_int("DISTILL_FEEDBACK_TOP_N", 3))
        self.interval_days = (interval_days if interval_days is not None
                              else _env_int("DISTILL_FEEDBACK_INTERVAL_DAYS", 7))

    # ─── 收集 ───

    def collect(self, days: Optional[int] = None) -> Dict[str, Any]:
        """读取反馈（失败模式/优质案例）与知识蒸馏产物

        Returns:
            {"summary", "dislikes", "quality_cases", "distilled_notes"}
            各列表元素为 dict（真实对象经 to_dict 转换，mock 对象容错）。
        """
        mgr = self._manager()
        days = days or self.interval_days
        data: Dict[str, Any] = {
            "summary": self._as_dict(mgr.get_feedback_summary(days=days)),
            "dislikes": [self._as_dict(x)
                         for x in mgr.list_feedback(feedback_type="dislike", limit=100)],
            "quality_cases": [self._as_dict(x)
                              for x in mgr.list_quality_cases(limit=50)],
        }
        notes = self._collect_distilled_notes()
        if notes:
            data["distilled_notes"] = notes
        logger.info(
            "[DistillFeedback] 收集完成 dislikes=%d quality_cases=%d distilled_notes=%d",
            len(data["dislikes"]), len(data["quality_cases"]), len(notes))
        return data

    def _manager(self) -> Any:
        if self._feedback is not None:
            return self._feedback
        from agent.feedback import get_feedback_manager
        return get_feedback_manager()

    @staticmethod
    def _as_dict(obj: Any) -> Dict[str, Any]:
        if isinstance(obj, dict):
            return obj
        if hasattr(obj, "to_dict"):
            return obj.to_dict()
        return dict(obj)

    def _collect_distilled_notes(self,
                                 knowledge_root: Optional[str] = None) -> List[Dict[str, Any]]:
        """只读汇总 knowledge/processed/*.md 蒸馏产物（已确认笔记的一句话洞见）

        解析失败/目录缺失 → 空列表（不抛异常，不触碰知识库任何状态）。
        """
        try:
            import yaml
            from agent.knowledge.ingest import get_knowledge_root
        except Exception:  # noqa: BLE001 依赖缺失 → 无蒸馏产物
            return []
        try:
            processed = Path(get_knowledge_root(knowledge_root)) / "processed"
        except Exception:  # noqa: BLE001
            return []
        if not processed.is_dir():
            return []
        notes: List[Dict[str, Any]] = []
        for f in sorted(processed.glob("*.md"))[-50:]:
            try:
                text = f.read_text(encoding="utf-8")
                if not text.startswith("---"):
                    continue
                end = text.find("\n---", 3)
                if end < 0:
                    continue
                meta = yaml.safe_load(text[3:end]) or {}
                if meta.get("distilled") and meta.get("one_line_insight"):
                    notes.append({
                        "slug": meta.get("slug", f.stem),
                        "title": meta.get("title", f.stem),
                        "one_line_insight": str(meta["one_line_insight"]),
                    })
            except Exception:  # noqa: BLE001 单条损坏不影响整体
                continue
        return notes

    # ─── 汇总（确定性聚合，测试友好）───

    def summarize(self, data: Optional[Dict[str, Any]] = None,
                  days: Optional[int] = None) -> List[DistillSuggestion]:
        """将失败模式与优质案例/蒸馏产物汇总为结构化建议

        规则（确定性）:
            - 失败模式：dislike 按 category 聚合，频次 ≥ min_frequency 才产出；
            - 优质案例：按 skill_id/类别聚合，取 quality_score 最高的 top_n；
            - 蒸馏笔记：distilled 且含一句话洞见的笔记，取前 top_n。
        """
        data = data or self.collect(days=days)
        suggestions: List[DistillSuggestion] = []

        # 1. 高频失败模式
        dislikes = data.get("dislikes", []) or []
        by_cat: Dict[str, List[str]] = {}
        for d in dislikes:
            cat = str(d.get("category") or "other")
            comment = str(d.get("comment") or "")
            by_cat.setdefault(cat, []).append(comment)
            logger.debug("[DistillFeedback] 负面反馈归类 category=%s comment=%s…",
                         cat, comment[:24])
        total = len(dislikes) or 1
        logger.info("[DistillFeedback] 失败模式分组统计（共 %d 条负面反馈）: %s",
                    len(dislikes),
                    {c: len(v) for c, v in sorted(by_cat.items(),
                                                  key=lambda kv: len(kv[1]),
                                                  reverse=True)})
        for cat, comments in sorted(by_cat.items(),
                                    key=lambda kv: len(kv[1]), reverse=True):
            if len(comments) < self.min_frequency:
                logger.info("[DistillFeedback] 失败模式跳过 category=%s 频次=%d < 最低频次=%d",
                            cat, len(comments), self.min_frequency)
                continue
            instruction = _FAILURE_INSTRUCTION_MAP.get(cat)
            if not instruction:
                logger.info("[DistillFeedback] 失败模式跳过 category=%s 无对应改进指令映射",
                            cat)
                continue
            priority = "high" if len(comments) >= self.min_frequency * 3 else "medium"
            logger.info("[DistillFeedback] 失败模式产出 category=%s 频次=%d 优先级=%s 指令=%s…",
                        cat, len(comments), priority, instruction[:24])
            suggestions.append(DistillSuggestion(
                suggestion_id=self._gen_id("fail", cat),
                kind="failure_pattern", category=cat,
                description=(f"高频失败模式：{cat}（{len(comments)} 条负面反馈，"
                             f"占比 {len(comments) / total:.0%}）"),
                suggested_instruction=instruction,
                evidence_count=len(comments),
                priority=priority,
                evidence=[c for c in comments if c][: self.top_n],
                source="feedback"))

        # 2. 优质案例模式
        cases = data.get("quality_cases", []) or []
        by_skill: Dict[str, List[Dict[str, Any]]] = {}
        for c in cases:
            key = str(c.get("skill_id") or c.get("category") or "general")
            by_skill.setdefault(key, []).append(c)
        for key, clist in sorted(
                by_skill.items(),
                key=lambda kv: max((float(x.get("quality_score") or 0)
                                    for x in kv[1]), default=0.0),
                reverse=True)[: self.top_n]:
            top = clist[: self.top_n]
            instruction = _QUALITY_INSTRUCTION_MAP.get(key, _QUALITY_INSTRUCTION_MAP["general"])
            logger.info("[DistillFeedback] 优质案例产出 skill=%s 案例数=%d 最高分=%.2f",
                        key, len(clist),
                        max((float(x.get("quality_score") or 0) for x in clist),
                            default=0.0))
            suggestions.append(DistillSuggestion(
                suggestion_id=self._gen_id("quality", key),
                kind="quality_pattern", category=key,
                description=f"优质案例模式：{key}（{len(clist)} 个案例）",
                suggested_instruction=instruction,
                evidence_count=len(clist),
                priority="medium",
                evidence=[str(c.get("title") or c.get("content_summary") or "")
                          for c in top if c.get("title") or c.get("content_summary")],
                target_skill_id=key,
                source="feedback"))

        # 3. 知识蒸馏产物 → 知识要点模式
        notes = data.get("distilled_notes", []) or []
        for note in notes[: self.top_n]:
            insight = str(note.get("one_line_insight") or "").strip()
            if not insight:
                logger.info("[DistillFeedback] 蒸馏笔记跳过 slug=%s 洞见为空",
                            note.get("slug") or note.get("title") or "")
                continue
            slug = str(note.get("slug") or "")
            logger.info("[DistillFeedback] 蒸馏笔记产出 slug=%s 洞见=%s…",
                        slug or note.get("title") or "", insight[:24])
            suggestions.append(DistillSuggestion(
                suggestion_id=self._gen_id("note", slug or "insight"),
                kind="knowledge_insight", category="knowledge",
                description=f"蒸馏知识要点：{note.get('title') or slug}",
                suggested_instruction=f"回答相关问题时参考已验证的知识要点：{insight}",
                evidence_count=1, priority="medium",
                evidence=[insight],
                source="knowledge"))
        return suggestions

    # ─── 验证并产出优化建议（复用任务2评估器，不自动应用）───

    def run(self, *, base_prompt: Optional[str] = None,
            category: str = "general",
            sample_ids: Optional[List[str]] = None,
            prompt_id: Optional[str] = None,
            days: Optional[int] = None) -> List[PromptOptimizationProposal]:
        """汇总 → 评估器验证 → PromptOptimizationProposal 管道

        - base_prompt 提供 → 建议版 = base_prompt + 改进指令，与原版对比
          （相对提升超阈值才产出建议）；
        - 未提供 → 以改进指令为候选提示词做绝对验证（得分 ≥ 最低接受分）；
        - quality_pattern 建议若携带 target_skill_id，透传为评估类别
          （按技能对应的样本池分别采样验证，更贴合业务）；
        - 无 PromptOptimizer / 验证异常 → 该条跳过，不产出伪建议。
        """
        if self._optimizer is None:
            logger.warning("[DistillFeedback] 无 PromptOptimizer，蒸馏建议无法验证，"
                           "本次不产出建议（不产生伪建议）")
            return []
        suggestions = self.summarize(days=days)
        proposals: List[PromptOptimizationProposal] = []
        for s in suggestions:
            try:
                reason = f"蒸馏建议 {s.suggestion_id}: {s.description}"
                # 【变易】quality_pattern 绑定具体技能：透传 target_skill_id 作为评估类别
                eval_category = (s.target_skill_id
                                 if s.kind == "quality_pattern" and s.target_skill_id
                                 else category)
                if base_prompt is not None:
                    candidate = base_prompt.rstrip() + "\n" + s.suggested_instruction
                    logger.debug("[DistillFeedback] 验证建议 %s via compare "
                                 "category=%s prompt_id=%s", s.suggestion_id,
                                 eval_category, prompt_id)
                    proposal = self._optimizer.compare(
                        base_prompt, candidate, category=eval_category,
                        sample_ids=sample_ids, prompt_id=prompt_id,
                        source=SOURCE_DISTILL, reason=reason)
                else:
                    logger.debug("[DistillFeedback] 验证建议 %s via validate(绝对) "
                                 "category=%s prompt_id=%s", s.suggestion_id,
                                 eval_category, prompt_id)
                    proposal = self._optimizer.validate(
                        s.suggested_instruction, category=eval_category,
                        sample_ids=sample_ids, prompt_id=prompt_id,
                        source=SOURCE_DISTILL, reason=reason)
                logger.info("[DistillFeedback] 建议验证结果 %s status=%s "
                            "orig=%s cand=%s", s.suggestion_id, proposal.status,
                            proposal.original_score, proposal.suggested_score)
                proposals.append(proposal)
            except Exception as e:  # noqa: BLE001 单条失败不影响整体
                logger.warning("[DistillFeedback] 建议验证失败 %s: %s",
                               s.suggestion_id, e)
        logger.info("[DistillFeedback] 汇总完成 suggestions=%d proposals=%d",
                    len(suggestions), len(proposals))
        return proposals

    @staticmethod
    def _gen_id(kind: str, key: str) -> str:
        return f"ds-{uuid.uuid4().hex[:8]}-{kind}-{key}"


__all__ = [
    "DistillSuggestion",
    "DistillFeedbackSummarizer",
]
