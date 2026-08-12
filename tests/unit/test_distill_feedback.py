"""知识蒸馏反馈回路单元测试 — 任务 EVO-T4 上下文与知识进化闭环

覆盖验收标准：
- 优质案例/失败模式能汇总为结构化建议（mock 数据验证）；
- 无样本/无优化器类别降级，不产出伪建议；
- 蒸馏产物（knowledge/processed frontmatter）只读汇总；
- 建议进入 PromptOptimizationProposal 管道（compare/validate 路径），不自动应用。
"""

from unittest.mock import MagicMock

import pytest

from agent.cognitive.prompt_optimizer import (
    SOURCE_DISTILL,
    PromptOptimizationProposal,
)
from agent.knowledge.distill_feedback import (
    DistillFeedbackSummarizer,
    DistillSuggestion,
    _FAILURE_INSTRUCTION_MAP,
)


# ════════════════════════════════════════════════════════════
#  测试数据工厂
# ════════════════════════════════════════════════════════════

def make_dislike(category: str, comment: str = "不满意"):
    return {"category": category, "comment": comment}


def make_quality(skill_id: str, score: float, title: str = "优质案例"):
    return {"skill_id": skill_id, "quality_score": score, "title": title}


def make_note(slug: str, insight: str):
    return {"slug": slug, "title": slug, "one_line_insight": insight}


def make_suggestion(category: str = "accuracy",
                    suggestion_id: str = "ds-test-1") -> DistillSuggestion:
    return DistillSuggestion(
        suggestion_id=suggestion_id,
        kind="failure_pattern",
        category=category,
        description="高频失败模式测试",
        suggested_instruction="改进指令文本",
        evidence_count=3,
        priority="medium",
    )


def make_proposal() -> PromptOptimizationProposal:
    return PromptOptimizationProposal(
        proposal_id="ppo-test",
        object_id="obj-1",
        original_prompt="base提示词",
        suggested_prompt="base提示词\n改进指令文本",
        original_score=0.7,
        suggested_score=0.8,
        improvement=0.14,
        status="proposed",
        source=SOURCE_DISTILL,
    )


# ════════════════════════════════════════════════════════════
#  失败模式汇总（确定性聚合）
# ════════════════════════════════════════════════════════════

class TestSummarizeFailurePatterns:
    """失败模式按分类聚合，频次 ≥ min_frequency 才产出"""

    def test_below_min_frequency_no_suggestion(self):
        """频次低于 min_frequency 不产出"""
        data = {
            "dislikes": [make_dislike("accuracy")],  # 仅 1 条 < 2
            "quality_cases": [],
        }
        summarizer = DistillFeedbackSummarizer(min_frequency=2, top_n=3)
        suggestions = summarizer.summarize(data)
        assert suggestions == []

    def test_at_min_frequency_produces_suggestion(self):
        """频次等于 min_frequency 产出建议，指令来自失败模式映射"""
        data = {
            "dislikes": [make_dislike("accuracy") for _ in range(2)],
            "quality_cases": [],
        }
        summarizer = DistillFeedbackSummarizer(min_frequency=2, top_n=3)
        suggestions = summarizer.summarize(data)
        assert len(suggestions) == 1
        s = suggestions[0]
        assert s.kind == "failure_pattern"
        assert s.category == "accuracy"
        assert s.evidence_count == 2
        assert s.suggested_instruction == _FAILURE_INSTRUCTION_MAP["accuracy"]
        assert s.priority == "medium"  # 2 条 < min_frequency * 3 = 6

    def test_priority_high_when_frequent(self):
        """频次 ≥ min_frequency * 3 时优先级升为 high"""
        data = {
            "dislikes": [make_dislike("accuracy") for _ in range(6)],
            "quality_cases": [],
        }
        summarizer = DistillFeedbackSummarizer(min_frequency=2, top_n=3)
        suggestions = summarizer.summarize(data)
        assert len(suggestions) == 1
        assert suggestions[0].priority == "high"

    def test_unknown_category_skipped(self):
        """不在失败模式映射中的分类不产出（无对应改进指令）"""
        data = {
            "dislikes": [make_dislike("weird_category") for _ in range(5)],
            "quality_cases": [],
        }
        summarizer = DistillFeedbackSummarizer(min_frequency=2, top_n=3)
        assert summarizer.summarize(data) == []


# ════════════════════════════════════════════════════════════
#  优质案例汇总
# ════════════════════════════════════════════════════════════

class TestSummarizeQualityPatterns:
    """优质案例按 skill_id 聚合，取最高分组 top_n"""

    def test_group_by_skill_top_n(self):
        """top_n 限制产出条数，按最高分降序"""
        data = {
            "dislikes": [],
            "quality_cases": [
                make_quality("skill_a", 0.9, "案例A"),
                make_quality("skill_b", 0.99, "案例B"),
            ],
        }
        summarizer = DistillFeedbackSummarizer(top_n=1)
        suggestions = summarizer.summarize(data)
        assert len(suggestions) == 1
        assert suggestions[0].kind == "quality_pattern"
        assert suggestions[0].target_skill_id == "skill_b"

    def test_evidence_uses_title(self):
        """evidence 取案例标题"""
        data = {
            "dislikes": [],
            "quality_cases": [make_quality("skill_a", 0.9, "标题A")],
        }
        summarizer = DistillFeedbackSummarizer(top_n=3)
        suggestions = summarizer.summarize(data)
        assert suggestions[0].evidence == ["标题A"]
        assert suggestions[0].priority == "medium"


# ════════════════════════════════════════════════════════════
#  蒸馏笔记汇总
# ════════════════════════════════════════════════════════════

class TestSummarizeKnowledgeInsights:
    """蒸馏笔记（已确认知识要点）汇总为 knowledge_insight 建议"""

    def test_notes_top_n(self):
        """蒸馏笔记取前 top_n"""
        data = {
            "dislikes": [],
            "quality_cases": [],
            "distilled_notes": [
                make_note("n1", "要点1"),
                make_note("n2", "要点2"),
                make_note("n3", "要点3"),
            ],
        }
        summarizer = DistillFeedbackSummarizer(top_n=2)
        suggestions = summarizer.summarize(data)
        assert len(suggestions) == 2
        assert all(s.kind == "knowledge_insight" for s in suggestions)
        assert suggestions[0].category == "knowledge"
        assert suggestions[0].source == "knowledge"
        assert "要点1" in suggestions[0].suggested_instruction

    def test_blank_insight_skipped(self):
        """空洞见不产出伪建议"""
        data = {
            "dislikes": [],
            "quality_cases": [],
            "distilled_notes": [make_note("n1", ""), make_note("n2", "   ")],
        }
        summarizer = DistillFeedbackSummarizer(top_n=3)
        assert summarizer.summarize(data) == []


# ════════════════════════════════════════════════════════════
#  数据收集
# ════════════════════════════════════════════════════════════

class TestCollect:
    """collect 汇总反馈与蒸馏产物，只读不修改知识库状态"""

    def test_collect_with_mock_manager(self):
        """mock feedback_manager 下字段齐全"""
        mgr = MagicMock()
        mgr.get_feedback_summary.return_value = {"total": 5}
        mgr.list_feedback.return_value = [make_dislike("accuracy")]
        mgr.list_quality_cases.return_value = [make_quality("search", 0.9)]
        summarizer = DistillFeedbackSummarizer(feedback_manager=mgr, top_n=3)
        summarizer._collect_distilled_notes = MagicMock(return_value=[])
        data = summarizer.collect(days=7)
        assert data["summary"] == {"total": 5}
        assert data["dislikes"] == [make_dislike("accuracy")]
        assert data["quality_cases"] == [make_quality("search", 0.9)]
        # 无蒸馏产物时不写入该键（collect 仅在有笔记时添加）
        assert "distilled_notes" not in data

    def test_collect_distilled_notes_missing_dir(self, monkeypatch):
        """蒸馏目录不存在 → 空列表（不抛异常）"""
        monkeypatch.setattr(
            "agent.knowledge.ingest.get_knowledge_root",
            lambda *a, **k: "/nonexistent/knowledge/root")
        summarizer = DistillFeedbackSummarizer(feedback_manager=MagicMock())
        assert summarizer._collect_distilled_notes() == []

    def test_collect_distilled_notes_parses_frontmatter(self, tmp_path, monkeypatch):
        """frontmatter 解析：distilled=True 且含 one_line_insight 才收录"""
        proc = tmp_path / "processed"
        proc.mkdir()
        (proc / "a.md").write_text(
            "---\ndistilled: true\nslug: note-a\ntitle: 笔记A\n"
            "one_line_insight: 要点A\n---\n正文内容", encoding="utf-8")
        (proc / "b.md").write_text(
            "---\ndistilled: false\nslug: note-b\n"
            "one_line_insight: 不应收录\n---\n", encoding="utf-8")
        (proc / "c.md").write_text("# 无 frontmatter", encoding="utf-8")
        monkeypatch.setattr(
            "agent.knowledge.ingest.get_knowledge_root",
            lambda *a, **k: str(tmp_path))
        summarizer = DistillFeedbackSummarizer(feedback_manager=MagicMock())
        notes = summarizer._collect_distilled_notes()
        assert len(notes) == 1
        assert notes[0]["slug"] == "note-a"
        assert notes[0]["one_line_insight"] == "要点A"


# ════════════════════════════════════════════════════════════
#  run() 验证管道（复用任务 2 评估器，不自动应用）
# ════════════════════════════════════════════════════════════

class TestRunPipeline:
    """蒸馏建议经 PromptOptimizer 验证产出 PromptOptimizationProposal"""

    def test_run_without_optimizer_returns_empty(self):
        """无 PromptOptimizer 不产伪建议"""
        summarizer = DistillFeedbackSummarizer(feedback_manager=MagicMock(),
                                               optimizer=None)
        assert summarizer.run(base_prompt="base") == []

    def test_run_compare_when_base_prompt(self):
        """有 base_prompt → 走 compare（建议版 = 原版 + 改进指令）"""
        summarizer = DistillFeedbackSummarizer(feedback_manager=MagicMock(),
                                               optimizer=MagicMock())
        summarizer.summarize = MagicMock(return_value=[make_suggestion()])
        opt = summarizer._optimizer
        opt.compare.return_value = make_proposal()
        proposals = summarizer.run(base_prompt="基础提示词", category="accuracy",
                                   prompt_id="p-1")
        assert len(proposals) == 1
        assert proposals[0].proposal_id == "ppo-test"
        opt.compare.assert_called_once()
        args, kwargs = opt.compare.call_args
        assert args[0] == "基础提示词"
        assert "改进指令文本" in args[1]
        assert kwargs["source"] == SOURCE_DISTILL

    def test_run_validate_without_base_prompt(self):
        """无 base_prompt → 走 validate（绝对验证）"""
        summarizer = DistillFeedbackSummarizer(feedback_manager=MagicMock(),
                                               optimizer=MagicMock())
        summarizer.summarize = MagicMock(return_value=[make_suggestion()])
        opt = summarizer._optimizer
        opt.validate.return_value = make_proposal()
        proposals = summarizer.run(category="accuracy", prompt_id="p-1")
        assert len(proposals) == 1
        opt.validate.assert_called_once()
        assert opt.validate.call_args[0][0] == "改进指令文本"

    def test_run_survives_single_failure(self):
        """单条验证失败不影响整体"""
        summarizer = DistillFeedbackSummarizer(feedback_manager=MagicMock(),
                                               optimizer=MagicMock())
        summarizer.summarize = MagicMock(return_value=[
            make_suggestion(category="accuracy", suggestion_id="ds-1"),
            make_suggestion(category="quality", suggestion_id="ds-2"),
        ])
        opt = summarizer._optimizer
        opt.compare.side_effect = [ValueError("评估器故障"), make_proposal()]
        proposals = summarizer.run(base_prompt="base")
        assert len(proposals) == 1
        assert proposals[0].proposal_id == "ppo-test"

    def test_run_candidate_does_not_auto_apply(self):
        """run 仅产出建议，不调用任何应用路径"""
        summarizer = DistillFeedbackSummarizer(feedback_manager=MagicMock(),
                                               optimizer=MagicMock())
        summarizer.summarize = MagicMock(return_value=[make_suggestion()])
        opt = summarizer._optimizer
        opt.compare.return_value = make_proposal()
        summarizer.run(base_prompt="base")
        # 只允许评估/对比调用，不允许出现 apply/commit 类方法调用
        applied = [name for name, _ in opt.call_args_list if "apply" in name
                   or "commit" in name]
        assert applied == []

    def test_run_passthrough_target_skill_to_evaluator(self):
        """quality_pattern 建议透传 target_skill_id 为评估类别（按技能采样验证）"""
        quality = DistillSuggestion(
            suggestion_id="ds-quality-search", kind="quality_pattern",
            category="search", description="优质案例模式",
            suggested_instruction="保持检索精确匹配", evidence_count=1,
            priority="medium", target_skill_id="search")
        summarizer = DistillFeedbackSummarizer(feedback_manager=MagicMock(),
                                               optimizer=MagicMock())
        summarizer.summarize = MagicMock(return_value=[
            quality,
            make_suggestion(),  # failure_pattern 无 target → 走默认类别
        ])
        opt = summarizer._optimizer
        opt.validate.return_value = make_proposal()
        summarizer.run(category="general", prompt_id="p-1")
        calls = opt.validate.call_args_list
        assert len(calls) == 2
        # quality_pattern → 透传 target_skill_id
        assert calls[0].kwargs["category"] == "search"
        # failure_pattern → 默认 category
        assert calls[1].kwargs["category"] == "general"

    def test_run_passthrough_respects_compare_path(self):
        """有 base_prompt 时透传同样作用于 compare 路径"""
        quality = DistillSuggestion(
            suggestion_id="ds-quality-code", kind="quality_pattern",
            category="code", description="优质案例模式",
            suggested_instruction="延续可运行代码风格", evidence_count=1,
            priority="medium", target_skill_id="code")
        summarizer = DistillFeedbackSummarizer(feedback_manager=MagicMock(),
                                               optimizer=MagicMock())
        summarizer.summarize = MagicMock(return_value=[quality])
        opt = summarizer._optimizer
        opt.compare.return_value = make_proposal()
        summarizer.run(base_prompt="基础提示词", category="general",
                       prompt_id="p-1")
        opt.compare.assert_called_once()
        assert opt.compare.call_args.kwargs["category"] == "code"
