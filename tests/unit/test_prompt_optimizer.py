"""提示词自优化器单元测试（任务 EVO-T4 上下文进化闭环）

覆盖验收条件（任务 4 执行步骤 5）:
    1. 优化器评估流程（真实执行器 + 样本池打分）
    2. 变体生成与择优筛选（LLM 生成 / 注入生成器 / 解析失败）
    3. 阈值判定（提升 > 3% 才产出建议版）
    4. 无样本降级（no_samples，不产出伪建议）
    5. 默认不自动应用（无 apply 路径，谱系 decision=pending_review）
    6. 谱系写入（object_type=prompt）
    7. 度量埋点（yunshu_prompt_optimization_*）
    8. 反思 Lesson 通道（LessonEvalChannel 验证管道）
"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.cognitive.prompt_optimizer import (
    COMPARISON_PAIRED,
    LessonEvalChannel,
    PromptOptimizationProposal,
    PromptOptimizer,
    SOURCE_EVALUATOR,
    STATUS_NO_IMPROVEMENT,
    STATUS_NO_SAMPLES,
    STATUS_NO_VARIANTS,
    STATUS_PROPOSED,
)
from agent.skills_mgmt.evaluator import EvalSample, EvalSamplePool, EvaluationResult, ExecOutcome
from agent.skills_mgmt.lineage import EvolutionArchive, EvolutionRecord


# ════════════════════════════════════════════════════════════
#  构造辅助
# ════════════════════════════════════════════════════════════

def make_sample(sid, category="search", task="查询测试", expected=None):
    return EvalSample(id=sid, category=category, task=task,
                      expected_output=expected or {"type": "contains", "values": ["ok"]})


def make_pool(tmp_path, category_samples):
    """临时样本池：{category: [EvalSample]} → 写文件 + 返回 pool"""
    base = Path(tmp_path) / "evals"
    for cat, samples in category_samples.items():
        d = base / cat
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{cat}.json").write_text(
            json.dumps([s.to_dict() for s in samples], ensure_ascii=False),
            encoding="utf-8")
    return EvalSamplePool(base_dir=str(base))


def make_archive(tmp_path):
    return EvolutionArchive(
        active_path=str(Path(tmp_path) / "archive.jsonl"),
        archive_path=str(Path(tmp_path) / "archive_old.jsonl"),
        active_generations=10)


class StubEvaluator:
    """确定性评估器：按提示词文本查表返回目标分数（阈值判定测试用）

    EvaluationResult.score 为派生属性：
    score = 0.5*success_rate + 0.3*latency_norm + 0.2*satisfaction
    （latency_norm = 1 - latency_ms / 5000）。
    本 stub 令 success_rate=satisfaction=v，并调整 latency 使
    latency_norm=v，从而派生 score 恰等于 score_map 中的 v（0-1 截断）。
    """

    def __init__(self, score_map):
        self.score_map = score_map

    def evaluate(self, skill, sample_ids=None, params=None, **kwargs):
        prompt = (params or {}).get("system_prompt", "")
        v = max(0.0, min(1.0, float(self.score_map.get(prompt, 0.5))))
        return EvaluationResult(
            skill_id=getattr(skill, "id", ""), status="completed",
            success_rate=v, satisfaction=v,
            latency_ms=(1.0 - v) * 5000.0,  # latency_norm=v → score=0.5v+0.3v+0.2v=v
            sample_count=1, cost_tokens=10)


def make_prompt_runner(score_fn):
    """真实执行器 stub：(prompt, task, params) -> ExecOutcome（证据可控）"""
    def _run(prompt, task, params):
        ok, result = score_fn(prompt, task, params)
        return ExecOutcome(success=ok, exit_code=0 if ok else -1,
                           result=result, stdout=str(result), duration_ms=5.0)
    return _run


def make_lesson(lesson_id="lesson_001", task_type="query", solution=None,
                failure_point="执行超时"):
    from planning.reflector import Lesson
    return Lesson(id=lesson_id, task_type=task_type,
                  task_description="测试任务", failure_point=failure_point,
                  solution=solution, timestamp="2026-08-12T00:00:00")


# ════════════════════════════════════════════════════════════
#  1. 评估流程（真实执行器 + 样本池）
# ════════════════════════════════════════════════════════════

class TestEvaluatePrompt:
    def test_evaluate_prompt_real_runner_scores_by_output(self, tmp_path):
        """真实执行：提示词决定输出是否满足 contains 校验 → 分数不同"""
        pool = make_pool(tmp_path, {"search": [
            make_sample("s1", task="查询A"),
            make_sample("s2", task="查询B"),
        ]})
        # 提示词含 GOOD → 输出含 ok；否则不含
        def score_fn(prompt, task, params):
            good = "GOOD" in prompt
            return good, "ok" if good else "bad"
        opt = PromptOptimizer(pool=pool, prompt_runner=make_prompt_runner(score_fn),
                              archive=make_archive(tmp_path))
        good = opt.evaluate_prompt("GOOD 提示词", category="search")
        bad = opt.evaluate_prompt("普通提示词", category="search")
        assert good.status == "completed"
        assert good.score > bad.score
        assert bad.sample_count == 2

    def test_evaluate_prompt_no_samples(self, tmp_path):
        """无样本类别 → status=no_samples（绝不伪造指标）"""
        pool = make_pool(tmp_path, {"search": [make_sample("s1")]})
        opt = PromptOptimizer(pool=pool, prompt_runner=make_prompt_runner(
            lambda p, t, params: (True, "ok")), archive=make_archive(tmp_path))
        result = opt.evaluate_prompt("提示词", category="chat")
        assert result.status == STATUS_NO_SAMPLES
        assert result.score == 0.0


# ════════════════════════════════════════════════════════════
#  2. 阈值判定（原版 vs 建议版）
# ════════════════════════════════════════════════════════════

class TestCompareThreshold:
    def test_compare_proposed_when_above_threshold(self, tmp_path):
        """提升 > 阈值（3%）→ 产出建议版，含原版/建议版评分对比"""
        opt = PromptOptimizer(evaluator=StubEvaluator({"orig": 0.5, "cand": 0.9}),
                              archive=make_archive(tmp_path))
        proposal = opt.compare("orig", "cand", category="search")
        assert proposal.status == STATUS_PROPOSED
        assert proposal.suggested_prompt == "cand"
        assert proposal.original_score == 0.5
        assert proposal.suggested_score == 0.9
        assert proposal.improvement == pytest.approx(0.8)
        assert proposal.comparison == "paired"

    def test_compare_not_proposed_below_threshold(self, tmp_path):
        """提升 < 阈值 → no_improvement，不产出建议版"""
        opt = PromptOptimizer(evaluator=StubEvaluator({"orig": 0.9, "cand": 0.9}),
                              archive=make_archive(tmp_path))
        proposal = opt.compare("orig", "cand", category="search")
        assert proposal.status == STATUS_NO_IMPROVEMENT
        assert proposal.suggested_prompt is None

    def test_compare_threshold_boundary(self, tmp_path):
        """恰好等于阈值 → 建议（>= 阈值判定）"""
        # 0.515 相对 0.5 的提升 = 0.03，恰在阈值边界（浮点略大于 0.03）
        opt = PromptOptimizer(evaluator=StubEvaluator({"orig": 0.5, "cand": 0.515}),
                              improvement_threshold=0.03,
                              archive=make_archive(tmp_path))
        proposal = opt.compare("orig", "cand", category="search")
        assert proposal.status == STATUS_PROPOSED

    def test_compare_zero_orig_uses_absolute_delta(self, tmp_path):
        """原版得分 0（避免除零）→ 用绝对差值判定"""
        opt = PromptOptimizer(evaluator=StubEvaluator({"orig": 0.0, "cand": 0.1}),
                              archive=make_archive(tmp_path))
        proposal = opt.compare("orig", "cand", category="search")
        assert proposal.status == STATUS_PROPOSED
        assert proposal.improvement == pytest.approx(0.1)


# ════════════════════════════════════════════════════════════
#  3. 变体生成与择优
# ════════════════════════════════════════════════════════════

class TestOptimize:
    def test_optimize_picks_best_variant(self, tmp_path):
        """2-3 变体择优：选评分最高的变体作为建议版"""
        score_map = {"orig": 0.5, "v1": 0.6, "v2": 0.95, "v3": 0.7}
        opt = PromptOptimizer(evaluator=StubEvaluator(score_map),
                              variant_generator=lambda p, n: ["v1", "v2", "v3"],
                              archive=make_archive(tmp_path))
        proposal = opt.optimize("orig", category="search")
        assert proposal.status == STATUS_PROPOSED
        assert proposal.suggested_prompt == "v2"
        assert proposal.suggested_score == 0.95

    def test_optimize_variant_generator_via_llm(self, tmp_path):
        """LLM 生成变体：返回 JSON 数组 → 解析、去重、剔除原版"""
        llm = MagicMock()
        llm.chat.return_value = '["v1", "v1", "orig", "v2"]'
        opt = PromptOptimizer(evaluator=StubEvaluator(
            {"orig": 0.5, "v1": 0.6, "v2": 0.9}), llm=llm,
            archive=make_archive(tmp_path))
        variants = opt.generate_variants("orig", n=3)
        assert variants == ["v1", "v2"]

    def test_optimize_variant_parse_failure(self, tmp_path):
        """LLM 返回垃圾 → 变体为空 → no_variants（不产出伪建议）"""
        llm = MagicMock()
        llm.chat.return_value = "not json"
        opt = PromptOptimizer(evaluator=StubEvaluator({"orig": 0.5}),
                              llm=llm, archive=make_archive(tmp_path))
        proposal = opt.optimize("orig", category="search")
        assert proposal.status == STATUS_NO_VARIANTS
        assert proposal.suggested_prompt is None

    def test_optimize_no_variants_when_no_llm(self, tmp_path):
        """无 LLM 且未注入生成器 → no_variants"""
        opt = PromptOptimizer(evaluator=StubEvaluator({"orig": 0.5}),
                              archive=make_archive(tmp_path))
        proposal = opt.optimize("orig", category="search")
        assert proposal.status == STATUS_NO_VARIANTS

    def test_max_variants_clamped_to_2_3(self, tmp_path):
        """变体数强制收敛到 2-3（任务要求）"""
        opt = PromptOptimizer(evaluator=StubEvaluator({}), max_variants=10,
                              archive=make_archive(tmp_path))
        assert opt.max_variants == 3
        opt2 = PromptOptimizer(evaluator=StubEvaluator({}), max_variants=1,
                               archive=make_archive(tmp_path))
        assert opt2.max_variants == 2


# ════════════════════════════════════════════════════════════
#  4. 绝对验证（蒸馏/反思无基线）
# ════════════════════════════════════════════════════════════

class TestValidateAbsolute:
    def test_validate_above_min_score(self, tmp_path):
        """候选得分 ≥ 最低接受分 → 建议（absolute 模式，无基线）"""
        opt = PromptOptimizer(evaluator=StubEvaluator({"cand": 0.8}),
                              abs_min_score=0.5, archive=make_archive(tmp_path))
        proposal = opt.validate("cand", category="search")
        assert proposal.status == STATUS_PROPOSED
        assert proposal.comparison == "absolute"
        assert proposal.improvement is None
        assert proposal.suggested_score == 0.8

    def test_validate_below_min_score(self, tmp_path):
        """候选得分 < 最低接受分 → 不产出建议"""
        opt = PromptOptimizer(evaluator=StubEvaluator({"cand": 0.3}),
                              abs_min_score=0.5, archive=make_archive(tmp_path))
        proposal = opt.validate("cand", category="search")
        assert proposal.status == STATUS_NO_IMPROVEMENT
        assert proposal.suggested_prompt is None

    def test_validate_with_original_delegates_to_compare(self, tmp_path):
        """提供原版 → 走 paired 对比（相对提升判定）"""
        opt = PromptOptimizer(evaluator=StubEvaluator({"orig": 0.5, "cand": 0.9}),
                              archive=make_archive(tmp_path))
        proposal = opt.validate("cand", original_prompt="orig", category="search")
        assert proposal.comparison == "paired"
        assert proposal.status == STATUS_PROPOSED


# ════════════════════════════════════════════════════════════
#  5. 默认不自动应用 / 谱系写入 / 度量
# ════════════════════════════════════════════════════════════

class TestNoAutoApplyAndLineage:
    def test_no_auto_apply_path_exists(self):
        """优化器无任何应用/写回提示词的方法（验收：默认不自动应用）"""
        opt = PromptOptimizer()
        assert not hasattr(opt, "apply")
        assert not hasattr(opt, "apply_prompt")
        assert not hasattr(opt, "commit")
        assert not hasattr(opt, "deploy")

    def test_proposal_never_committed_decision(self, tmp_path):
        """建议产出后谱系 decision=pending_review（不自动 committed）"""
        archive = make_archive(tmp_path)
        opt = PromptOptimizer(evaluator=StubEvaluator({"orig": 0.5, "cand": 0.9}),
                              archive=archive)
        proposal = opt.compare("orig", "cand", category="search")
        record = archive.get(proposal.record_id)
        assert record is not None
        assert record.decision == "pending_review"
        assert record.object_type == "prompt"
        assert "不自动应用" in record.decision_reason

    def test_lineage_written_for_each_optimization_event(self, tmp_path):
        """每次优化事件写入谱系：no_samples / no_improvement 也记录（skipped）"""
        archive = make_archive(tmp_path)
        opt = PromptOptimizer(evaluator=StubEvaluator({"orig": 0.9, "cand": 0.9}),
                              archive=archive)
        proposal = opt.compare("orig", "cand", category="search")
        record = archive.get(proposal.record_id)
        assert record.decision == "skipped"
        assert record.object_type == "prompt"
        assert record.cost["tokens"] == 20  # 两次评估各 10

    def test_no_samples_lineage_skipped(self, tmp_path):
        """无样本 → 谱系 decision=skipped，不产出伪建议"""
        pool = make_pool(tmp_path, {"search": [make_sample("s1")]})
        archive = make_archive(tmp_path)
        opt = PromptOptimizer(pool=pool, evaluator=None,
                              prompt_runner=make_prompt_runner(
                                  lambda p, t, params: (True, "ok")),
                              archive=archive)
        proposal = opt.compare("orig", "cand", category="chat")
        assert proposal.status == STATUS_NO_SAMPLES
        assert proposal.suggested_prompt is None
        record = archive.get(proposal.record_id)
        assert record.decision == "skipped"

    @patch("agent.skills_mgmt.observability.emit_metric")
    def test_metrics_emitted(self, mock_emit, tmp_path):
        """yunshu_prompt_optimization_* 埋点（对齐 yunshu_skill_* 系列）"""
        opt = PromptOptimizer(evaluator=StubEvaluator({"orig": 0.5, "cand": 0.9}),
                              archive=make_archive(tmp_path))
        opt.compare("orig", "cand", category="search")
        names = [call.kwargs.get("name") or call.args[0]
                 for call in mock_emit.call_args_list]
        assert any("yunshu_prompt_optimization_total" in n for n in names)
        assert any("yunshu_prompt_optimization_improvement" in n for n in names)


# ════════════════════════════════════════════════════════════
#  5.5 服务端失败桶（降基数）
# ════════════════════════════════════════════════════════════

def _bucket_prop(pid, status, score=0.5):
    """构造 _record_failure_bucket 所需的 proposal（直接单元级调用，确定性高）"""
    return PromptOptimizationProposal(
        proposal_id="ppo-t", object_id=pid, original_prompt="orig",
        suggested_prompt=None, original_score=score, suggested_score=None,
        improvement=0.0, status=status, comparison=COMPARISON_PAIRED,
        source=SOURCE_EVALUATOR, reason="test", category="search", sample_count=1)


class TestFailureBucket:
    """服务端聚合降基数：仅连续失败达阈值才 emit 带 prompt_id 的指标"""

    @patch("agent.skills_mgmt.observability.emit_metric")
    def test_continuous_failures_emit_once(self, mock_emit, tmp_path):
        """同一 prompt 连续失败 3 次 → 仅触发一次，labels 含 prompt_id"""
        opt = PromptOptimizer(evaluator=StubEvaluator({}),
                              archive=make_archive(tmp_path))
        for _ in range(3):
            opt._record_failure_bucket(_bucket_prop("p1", STATUS_NO_IMPROVEMENT))
        prompts = [c.kwargs.get("labels", {}).get("prompt_id")
                   for c in mock_emit.call_args_list
                   if "failed_prompt_total" in str(c)]
        assert prompts == ["p1"]
        assert opt._failure_store._d == {}  # 上报后移除，防桶膨胀

    @patch("agent.skills_mgmt.observability.emit_metric")
    def test_success_resets_bucket(self, mock_emit, tmp_path):
        """失败 2 次后成功 → 清零不触发"""
        opt = PromptOptimizer(evaluator=StubEvaluator({}),
                              archive=make_archive(tmp_path))
        for _ in range(2):
            opt._record_failure_bucket(_bucket_prop("p1", STATUS_NO_SAMPLES))
        opt._record_failure_bucket(_bucket_prop("p1", STATUS_PROPOSED))
        assert not mock_emit.called

    @patch("agent.skills_mgmt.observability.emit_metric")
    def test_different_prompt_ids_separate(self, mock_emit, tmp_path):
        """不同 prompt_id 各自计数，不互相累计（均未达阈值）"""
        opt = PromptOptimizer(evaluator=StubEvaluator({}),
                              archive=make_archive(tmp_path))
        for _ in range(2):
            opt._record_failure_bucket(_bucket_prop("p1", STATUS_NO_IMPROVEMENT))
            opt._record_failure_bucket(_bucket_prop("p2", STATUS_NO_IMPROVEMENT))
        assert not mock_emit.called

    @patch("agent.skills_mgmt.observability.emit_metric")
    def test_threshold_configurable(self, mock_emit, tmp_path):
        """构造传阈值 2 → 连续失败 2 次即触发"""
        opt = PromptOptimizer(evaluator=StubEvaluator({}),
                              archive=make_archive(tmp_path),
                              failure_emit_threshold=2)
        for _ in range(2):
            opt._record_failure_bucket(_bucket_prop("p1", STATUS_NO_IMPROVEMENT))
        assert mock_emit.called

    def test_default_threshold_three(self, tmp_path):
        """默认阈值 3（与 .env PROMPT_OPT_FAILURE_BUCKET 对齐）"""
        opt = PromptOptimizer(evaluator=StubEvaluator({}),
                              archive=make_archive(tmp_path))
        assert opt.failure_emit_threshold == 3


# ════════════════════════════════════════════════════════════
#  6. 反思 Lesson 通道
# ════════════════════════════════════════════════════════════

class TestLessonEvalChannel:
    def test_verifiable_lesson_produces_proposal(self, tmp_path):
        """可验证 Lesson（query + failure_point）→ 验证通过返回 proposal_id"""
        lesson = make_lesson(task_type="query", failure_point="执行超时")
        cand_prompt = LessonEvalChannel._lesson_to_prompt(lesson)
        opt = PromptOptimizer(evaluator=StubEvaluator({cand_prompt: 0.8}),
                              abs_min_score=0.5, archive=make_archive(tmp_path))
        channel = LessonEvalChannel(optimizer=opt,
                                    verifiable_task_types=["query"])
        proposal_id = channel.submit_lesson(lesson)
        assert proposal_id is not None
        assert proposal_id.startswith("ppo-")

    def test_unverifiable_lesson_returns_none(self, tmp_path):
        """无 failure_point/solution 或类别不可验证 → 返回 None"""
        opt = PromptOptimizer(evaluator=StubEvaluator({"cand": 0.8}),
                              abs_min_score=0.5, archive=make_archive(tmp_path))
        channel = LessonEvalChannel(optimizer=opt,
                                    verifiable_task_types=["query"])
        assert channel.submit_lesson(
            make_lesson(task_type="query", failure_point="")) is None
        assert channel.submit_lesson(
            make_lesson(task_type="code", failure_point="错误")) is None

    def test_lesson_solution_used_when_present(self, tmp_path):
        """Lesson 自带 solution → 作为改进指令（否则用失败点兜底指令）"""
        channel = LessonEvalChannel(
            optimizer=MagicMock(), verifiable_task_types=["query"])
        with_solution = channel._lesson_to_prompt(
            make_lesson(task_type="query", solution="增加校验"))
        without = channel._lesson_to_prompt(
            make_lesson(task_type="query", failure_point="超时"))
        assert "增加校验" in with_solution
        assert "超时" in without

    def test_channel_failed_validation_returns_none(self, tmp_path):
        """验证未通过（得分低于接受分）→ 返回 None"""
        lesson = make_lesson(task_type="query", failure_point="超时")
        cand_prompt = LessonEvalChannel._lesson_to_prompt(lesson)
        opt = PromptOptimizer(evaluator=StubEvaluator({cand_prompt: 0.3}),
                              abs_min_score=0.5, archive=make_archive(tmp_path))
        channel = LessonEvalChannel(optimizer=opt,
                                    verifiable_task_types=["query"])
        assert channel.submit_lesson(lesson) is None
