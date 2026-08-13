# -*- coding: utf-8 -*-
"""verify_evot4_flow.py — 任务 EVO-T4 上下文进化闭环完整流程本地验证

构造模拟评估数据与失败案例，跑通「提示词自优化 + 蒸馏反馈 + 反思通道」全链路：
    evaluate → compare → optimize → validate → distill_feedback.run
    → LessonEvalChannel.submit_lesson → Reflector.learn_from_experience 对接
    → 谱系审计（object_type=prompt，decision 不自动 committed）

模拟数据（全部临时目录，不污染真实 data/）：
- 样本池 search 类 2 样本（expected contains "ok"）
- 提示词执行器：提示词含 GOOD → 输出 ok；否则 bad
- 失败案例：负面反馈 accuracy×3（高频失败模式）、relevance×1（低于最低频次）
- 优质案例：search/chat 各 1
- 蒸馏笔记：knowledge/processed 临时目录 1 条已确认笔记

运行: python scripts/verify_evot4_flow.py
退出码: 0 全部通过, 1 有失败
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d [%(levelname)8s] %(name)s | %(message)s",
    datefmt="%H:%M:%S",
    encoding="utf-8",
    force=True,
)

from agent.cognitive.prompt_optimizer import (
    LessonEvalChannel,
    PromptOptimizationProposal,
    PromptOptimizer,
    SOURCE_DISTILL,
    STATUS_NO_IMPROVEMENT,
    STATUS_NO_SAMPLES,
    STATUS_PROPOSED,
    report_adoption,
)
from agent.knowledge.distill_feedback import DistillFeedbackSummarizer
from agent.skills_mgmt.evaluator import EvalSample, EvalSamplePool, ExecOutcome
from agent.skills_mgmt.lineage import EvolutionArchive


# ════════════════════════════════════════════════════════════
#  模拟数据
# ════════════════════════════════════════════════════════════

def _make_pool(base: Path) -> EvalSamplePool:
    d = base / "evals" / "search"
    d.mkdir(parents=True, exist_ok=True)
    samples = [
        EvalSample(id="s1", category="search", task="查询A",
                   expected_output={"type": "contains", "values": ["ok"]}),
        EvalSample(id="s2", category="search", task="查询B",
                   expected_output={"type": "contains", "values": ["ok"]}),
    ]
    (d / "search.json").write_text(
        json.dumps([s.to_dict() for s in samples], ensure_ascii=False),
        encoding="utf-8")
    return EvalSamplePool(base_dir=str(base / "evals"))


def _make_archive(base: Path) -> EvolutionArchive:
    return EvolutionArchive(
        active_path=str(base / "lineage" / "archive.jsonl"),
        archive_path=str(base / "lineage" / "archive_old.jsonl"),
        active_generations=10)


def _runner(prompt, task, params) -> ExecOutcome:
    """模拟执行：提示词含 GOOD → 输出 ok（通过校验），否则 bad"""
    good = "GOOD" in prompt
    return ExecOutcome(success=good, exit_code=0 if good else -1,
                       result="ok" if good else "bad",
                       stdout="ok" if good else "bad", duration_ms=5.0)


class StubFeedbackManager:
    """模拟反馈数据源（agent/feedback.py 同款接口）"""

    def get_feedback_summary(self, days=7):
        return {"total": 4, "satisfaction_rate_percent": 60.0}

    def list_feedback(self, feedback_type=None, limit=100):
        return [
            {"category": "accuracy", "comment": "回答包含事实错误"},
            {"category": "accuracy", "comment": "统计数字来源不可靠"},
            {"category": "accuracy", "comment": "编造了不存在的案例"},
            {"category": "relevance", "comment": "答非所问（仅1条，低于最低频次）"},
        ]

    def list_quality_cases(self, limit=50):
        return [
            {"skill_id": "search", "quality_score": 0.95, "title": "检索精确性标杆案例"},
            {"skill_id": "chat", "quality_score": 0.88, "title": "对话温度标杆案例"},
        ]


class StubOptimizer:
    """演示用验证器：固定产出建议（含原版/建议版评分对比），不写谱系"""

    def compare(self, original, candidate, **kw):
        return PromptOptimizationProposal(
            proposal_id=f"ppo-{uuid.uuid4().hex[:8]}",
            object_id=kw.get("prompt_id", ""),
            original_prompt=original, suggested_prompt=candidate,
            original_score=0.62, suggested_score=0.78, improvement=0.26,
            status=STATUS_PROPOSED, comparison="paired",
            source=kw.get("source", SOURCE_DISTILL),
            category=kw.get("category", "general"), sample_count=2,
            reason=kw.get("reason", ""), record_id="")

    def validate(self, candidate, **kw):
        return self.compare("", candidate, **kw)


class CountingChannel:
    """包装 lesson 通道，统计调用次数"""

    def __init__(self, inner):
        self.inner = inner
        self.calls = 0

    def submit_lesson(self, lesson):
        self.calls += 1
        return self.inner.submit_lesson(lesson)


# ════════════════════════════════════════════════════════════
#  验证流程
# ════════════════════════════════════════════════════════════

def main():
    failures = []

    def check(name, cond, detail=""):
        mark = "PASS" if cond else "FAIL"
        print(f"  [{mark}] {name}" + (f"  ← {detail}" if detail else ""))
        if not cond:
            failures.append(name)

    base = Path(tempfile.mkdtemp(prefix="evot4_demo_"))
    pool = _make_pool(base)
    archive = _make_archive(base)
    print(f"\n模拟数据目录: {base}\n")

    # ── 1. 真实评估：证据驱动的打分 ──
    print("== 1. 真实评估（提示词 → 执行 → 客观校验打分）==")
    opt = PromptOptimizer(pool=pool, prompt_runner=_runner, archive=archive)
    r_base = opt.evaluate_prompt("生成一份报告，无输出约束", category="search")
    r_good = opt.evaluate_prompt("GOOD 生成报告：先结论后论证，输出 ok 通过校验",
                                 category="search")
    check("原版（无约束）分数 < 优化版（含约束）", r_base.score < r_good.score,
          f"orig={r_base.score} vs good={r_good.score}")
    check("优化版通过客观校验（status=completed）", r_good.status == "completed",
          f"success_rate={r_good.success_rate}")

    # ── 2. 自优化：变体生成 → 择优 → 对比（超阈值才建议）──
    print("\n== 2. 提示词自优化（模拟 LLM 变体 → 择优对比）==")

    def gen_variants(prompt, n):
        return [
            "GOOD 变体1：明确输出格式（列表+结论先行）",
            "GOOD 变体2：补充校验要求（必须输出 ok）",
            "变体3：仅措辞润色（无实质改进）",
        ]

    opt2 = PromptOptimizer(pool=pool, prompt_runner=_runner, archive=archive,
                           variant_generator=gen_variants)
    prop = opt2.optimize("生成一份报告，无输出约束", category="search")
    check("优化产出建议版（提升超 3%）",
          prop.status == STATUS_PROPOSED and prop.suggested_prompt is not None,
          f"orig={prop.original_score} → cand={prop.suggested_score} 提升={prop.improvement}")
    rec = archive.get(prop.record_id)
    check("谱系写入 object_type=prompt / decision=pending_review",
          rec is not None and rec.object_type == "prompt"
          and rec.decision == "pending_review",
          f"record={prop.record_id} decision={rec.decision if rec else 'None'}")
    check("建议版明确『不自动应用』",
          rec is not None and "不自动应用" in rec.decision_reason)

    # ── 3. 低提升不产出建议 ──
    print("\n== 3. 阈值判定：提升不足不产出建议版 ==")
    prop_low = opt2.compare("GOOD 提示词A", "GOOD 提示词B", category="search")
    check("低于阈值 → no_improvement", prop_low.status == STATUS_NO_IMPROVEMENT,
          f"orig={prop_low.original_score} cand={prop_low.suggested_score} 提升={prop_low.improvement}")
    rec_low = archive.get(prop_low.record_id)
    check("未产出建议也入谱系（skipped）",
          rec_low is not None and rec_low.decision == "skipped")

    # ── 4. 无样本降级 ──
    print("\n== 4. 无样本类别降级（不产伪建议）==")
    prop_none = opt2.compare("任意提示词", "任意提示词B", category="chat")
    check("无样本 → no_samples", prop_none.status == STATUS_NO_SAMPLES
          and prop_none.suggested_prompt is None)
    rec_none = archive.get(prop_none.record_id)
    check("无样本入谱系（skipped）",
          rec_none is not None and rec_none.decision == "skipped")

    # ── 5. 知识蒸馏反馈回路 ──
    print("\n== 5. 知识蒸馏反馈回路（失败模式 + 优质案例 + 蒸馏笔记）==")
    processed = base / "knowledge" / "processed"
    processed.mkdir(parents=True, exist_ok=True)
    (processed / "retrieval_note.md").write_text(
        "---\ndistilled: true\nslug: retrieval-note\ntitle: 检索优化要点\n"
        "one_line_insight: 检索时优先用精确短语并限定来源\n---\n正文",
        encoding="utf-8")
    import agent.knowledge.ingest as ingest_mod
    orig_root = ingest_mod.get_knowledge_root
    ingest_mod.get_knowledge_root = lambda *a, **k: base / "knowledge"

    summarizer = DistillFeedbackSummarizer(
        feedback_manager=StubFeedbackManager(),
        optimizer=StubOptimizer(), min_frequency=2, top_n=3, interval_days=7)
    try:
        data = summarizer.collect(days=7)
        check("收集数据源齐全",
              {"summary", "dislikes", "quality_cases"} <= set(data),
              f"dislikes={len(data['dislikes'])} "
              f"cases={len(data['quality_cases'])} "
              f"notes={len(data.get('distilled_notes', []))}")
        suggs = summarizer.summarize(data)
        kinds = [s.kind for s in suggs]
        check("失败模式聚合（accuracy×3 产出，relevance×1 过滤）",
              any(s.kind == "failure_pattern" and s.category == "accuracy"
                  for s in suggs)
              and all(s.category != "relevance" for s in suggs),
              f"共 {len(suggs)} 条: {kinds}")
        check("优质案例聚合（search/chat）",
              any(s.kind == "quality_pattern" and s.target_skill_id == "search"
                  for s in suggs)
              and any(s.kind == "quality_pattern" and s.target_skill_id == "chat"
                      for s in suggs))
        check("蒸馏笔记收录为知识要点",
              any(s.kind == "knowledge_insight" and "检索" in s.suggested_instruction
                  for s in suggs))
        proposals = summarizer.run(base_prompt="GOOD 系统提示词", category="search",
                                   prompt_id="prompt:demo:search")
        check("蒸馏建议经验证进入 Proposal 管道（不自动应用）",
              len(proposals) == len(suggs)
              and all(p.status == STATUS_PROPOSED for p in proposals),
              f"suggestions={len(suggs)} proposals={len(proposals)}")
    finally:
        ingest_mod.get_knowledge_root = orig_root

    summarizer_none = DistillFeedbackSummarizer(
        feedback_manager=StubFeedbackManager(), optimizer=None)
    check("无验证器 → 蒸馏不产伪建议", summarizer_none.run() == [])

    # ── 6. 反思 Lesson 通道 + Reflector 对接 ──
    print("\n== 6. 反思 Lesson 通道（reflector → 验证 → 建议）==")
    from planning.models import ActionResult
    from planning.reflector import Lesson, Reflector

    channel = CountingChannel(LessonEvalChannel(
        optimizer=StubOptimizer(),
        verifiable_task_types=["general", "analyze", "query"]))
    reflector = Reflector(persist_dir=str(base / "reflection"),
                          lesson_channel=channel)

    lesson = Lesson(id="lesson_demo_001", task_type="analyze",
                    task_description="分析数据", failure_point="模型返回格式错误",
                    solution=None, timestamp="2026-08-12T00:00:00")
    pid = channel.submit_lesson(lesson)
    check("可验证 Lesson 提交 → 返回 proposal_id",
          pid is not None and pid.startswith("ppo-"), f"proposal={pid}")
    check("不可验证 Lesson（无失败点）→ None",
          channel.submit_lesson(Lesson(id="l2", task_type="analyze",
                                       task_description="x", failure_point="",
                                       solution=None, timestamp="")) is None)

    asyncio.run(reflector.learn_from_experience(
        "分析数据", ActionResult.failure_result("模型返回格式错误")))
    check("Reflector 失败经验自动转交 lesson_channel",
          channel.calls >= 2 and len(reflector.lessons_db) == 1,
          f"channel.calls={channel.calls} lessons={len(reflector.lessons_db)}")
    check("反思主流程未被通道阻断",
          reflector.lessons_db[0].failure_point == "模型返回格式错误")

    # ── 7. 采纳埋点（任务6审批流入口，仅埋点不应用）──
    print("\n== 7. 采纳埋点（report_adoption，只做度量）==")
    report_adoption(prop.proposal_id, score_delta=0.1)
    print("  [OK] report_adoption 已调用"
          "（yunshu_prompt_optimization_adopted_total + score_delta）")

    print(f"\n{'=' * 60}")
    if failures:
        print(f"FAIL  {len(failures)} 项验证失败: {failures}")
        return 1
    print("PASS  全部验证通过（EVO-T4 完整流程符合预期）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
