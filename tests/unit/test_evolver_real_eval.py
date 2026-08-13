"""OfflineEvolver 真实评估集成测试（任务 EVO-T2）

覆盖验收条件:
    1. 真实执行评估路径生效：注入 evaluator 后，基线/变异体均走真实评估，
       变异参数被透传；真实评估结果与启发式预测可区分（验收条件 1）；
    2. 无样本技能 → evolve_once 跳过，绝不伪造指标（验收条件 2）；
    3. 分阶段评估：阶段1 淘汰的变异体不会进入阶段2（验收条件 3，
       用 runner 调用次数证明全量阶段未触发）；
    4. 评估结果写入谱系档案库：EvolutionRecord.eval_result 有值（验收条件 4）；
    5. 预算熔断：基线/变异体 budget_exceeded 的降级处理路径；
    6. 向后兼容：不传 evaluator 保持既有启发式路径，
       提交判定逻辑（improvement >= threshold）未改（验收条件 7）。
"""
import json
from pathlib import Path

import pytest

from agent.skills_mgmt.enhancer import SkillEnhancer
from agent.skills_mgmt.evaluator import (
    EvalSample,
    EvalSamplePool,
    EvaluationResult,
    ExecOutcome,
    SkillExecutorEvaluator,
    StagedEvaluator,
)
from agent.skills_mgmt.lineage import EvolutionArchive
from agent.skills_mgmt.models import (
    ContentType,
    Skill,
    SkillCategory,
    SkillMetrics,
    SkillStatus,
)
from agent.skills_mgmt.offline_evolver import EvolutionStrategy, OfflineEvolver
from agent.skills_mgmt.store import SkillStore


# ════════════════════════════════════════════════════════════
#  构造辅助
# ════════════════════════════════════════════════════════════

_CANDIDATE_ID = "real-search-opt"
_STRATEGIES = [EvolutionStrategy.FINE_TUNE, EvolutionStrategy.FINE_TUNE]


def make_sample(sid: str, *, task: str = "查询云枢", expected=None, **meta):
    return EvalSample(id=sid, category="search", task=task,
                      expected_output=expected, metadata=meta)


def make_pool(base: Path, category_samples):
    """构造临时样本池：{category: [EvalSample]} → 写文件 + 返回 pool"""
    base = Path(base) / "evals"
    for cat, samples in category_samples.items():
        d = base / cat
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{cat}.json").write_text(
            json.dumps([s.to_dict() for s in samples], ensure_ascii=False),
            encoding="utf-8")
    return EvalSamplePool(base_dir=str(base))


def build_stack(base: Path, *, threshold: float = 0.05):
    """构造 store + enhancer(注入档案库) + evolver（候选技能满足进化条件）

    Returns:
        (evolver, store, archive)
    """
    store = SkillStore(path=str(base / "skills.json"))
    store.upsert(Skill(
        id=_CANDIDATE_ID, name="搜索优化", description="d",
        content="# c", content_type=ContentType.MARKDOWN,
        category=SkillCategory.CUSTOM, status=SkillStatus.APPROVED,
        enabled=True, version="1.0.0", tags=["search"],
        default_params={"threshold": 0.5, "max_results": 100, "boost_factor": 1.2},
        metrics=SkillMetrics(
            usage_count=50, success_count=35, failure_count=15,
            success_rate=0.7, avg_latency_ms=3000, p95_latency_ms=4500,
        ),
    ))
    archive = EvolutionArchive(
        active_path=str(base / "archive.jsonl"),
        archive_path=str(base / "archive_old.jsonl"),
    )
    enhancer = SkillEnhancer(store, lineage_archive=archive)
    evolver = OfflineEvolver(
        store, enhancer,
        min_usage=10, target_success_rate=0.95,
        max_variants_per_skill=2,
        improvement_threshold=threshold, random_seed=42,
    )
    return evolver, store, archive


class _CountingRunner:
    """记录调用次数的 stub runner（阶段2 是否触发的证据）"""

    def __init__(self, result=None):
        self.calls = 0
        self._result = result

    def __call__(self, skill, params):
        self.calls += 1
        return ExecOutcome(success=True, result=self._result, duration_ms=10)


class _SplitEvaluator:
    """协议实现：基线（params=None）与变异体（params!=None）返回不同真实结果

    params 被透传证明：变异体评估使用的是变异后的参数（非基线参数）。
    """

    def __init__(self, base: EvaluationResult, variant: EvaluationResult):
        self.pool = None
        self._base = base
        self._variant = variant
        self.calls: list = []  # 记录每次评估收到的 params

    def resolve_category(self, skill):
        return "search"

    def evaluate(self, skill, sample_ids=None, *, params=None, budget_tokens=None):
        self.calls.append(dict(params) if params else None)
        return self._variant if params is not None else self._base


def _result(*, success_rate: float, latency_ms: float, satisfaction: float,
            sample_count: int = 5) -> EvaluationResult:
    return EvaluationResult(
        skill_id=_CANDIDATE_ID, status="completed",
        success_rate=success_rate, latency_ms=latency_ms,
        satisfaction=satisfaction, sample_count=sample_count,
    )


# ════════════════════════════════════════════════════════════
#  验收条件 1：真实评估与启发式预测可区分
# ════════════════════════════════════════════════════════════

class TestRealEvalDistinguishable:
    def test_heuristic_path_backward_compatible(self, tmp_path):
        """不传 evaluator → 保持既有启发式路径（验收条件 7）"""
        evolver, _, _ = build_stack(tmp_path / "h", threshold=0.0)
        r = evolver.evolve_once(_CANDIDATE_ID, strategies=_STRATEGIES)
        # 启发式预测小幅参数偏离会提升成功率 → 提交
        assert r.committed is True
        assert r.improvement > 0.0
        assert r.strategy is not None

    def test_real_eval_reflects_true_success_and_latency(self, tmp_path):
        """真实评估反映真实 success/latency：成功样本 → success_rate=1.0，latency=执行耗时"""
        pool = make_pool(tmp_path, {"search": [make_sample(
            "q1", expected={"type": "contains", "values": ["云枢"]})]})
        runner = _CountingRunner(result={"answer": "云枢是什么"})
        evaluator = SkillExecutorEvaluator(pool=pool, runner=runner)
        skill = build_stack(tmp_path / "s", threshold=0.05)[1].get(_CANDIDATE_ID)
        ev = evaluator.evaluate(skill)
        assert ev.success_rate == 1.0
        assert ev.latency_ms == 10.0  # 真实执行耗时（runner 提供）
        assert runner.calls == 1

    def test_real_eval_distinguishable_from_heuristic(self, tmp_path):
        """同一技能：启发式预测提交，真实评估不提交（结果可区分，验收条件 1）"""
        # 启发式腿：参数小幅偏离 → 预测成功率提升 → 提交
        evolver_h, _, _ = build_stack(tmp_path / "h", threshold=0.0)
        r_h = evolver_h.evolve_once(_CANDIDATE_ID, strategies=_STRATEGIES)
        assert r_h.committed is True and r_h.improvement > 0.0

        # 真实腿：真实执行输出不含关键词 → 真实 success_rate=0.0 → 无提升 → 不提交
        pool = make_pool(tmp_path, {"search": [make_sample(
            "q1", task="查询云枢",
            expected={"type": "contains", "values": ["云枢"]})]})
        evaluator = SkillExecutorEvaluator(
            pool=pool, runner=_CountingRunner(result={"answer": "完全无关的回复"}))
        evolver_r, _, _ = build_stack(tmp_path / "r", threshold=0.01)
        r_r = evolver_r.evolve_once(
            _CANDIDATE_ID, strategies=_STRATEGIES, evaluator=evaluator)
        # 同一技能、同一变异策略，两者输出不一致（真实 0 提升 vs 启发式 >0）
        assert r_r.committed is False
        assert r_r.improvement == 0.0
        assert r_r.improvement < r_h.improvement
        # 直接评估可见真实指标：0 成功、10ms 真实延迟（与技能历史 success_rate=0.7 不同）
        ev = evaluator.evaluate(evolver_r._store.get(_CANDIDATE_ID))
        assert ev.success_rate == 0.0
        assert ev.latency_ms == 10.0

    def test_params_threaded_to_variant_eval(self, tmp_path):
        """变异体评估使用变异后的参数（params 透传）"""
        evolver, _, _ = build_stack(tmp_path, threshold=0.05)
        evaluator = _SplitEvaluator(
            base=_result(success_rate=0.5, latency_ms=2000, satisfaction=0.5),
            variant=_result(success_rate=0.9, latency_ms=500, satisfaction=0.9),
        )
        evolver.evolve_once(_CANDIDATE_ID, strategies=_STRATEGIES, evaluator=evaluator)
        assert len(evaluator.calls) == 3  # 1 基线 + 2 变异体
        assert evaluator.calls[0] is None          # 基线无参数覆盖
        assert evaluator.calls[1] is not None      # 变异体带变异参数
        # 变异参数来自技能默认参数集（变异体继承并微调）
        assert any(k in evaluator.calls[1]
                   for k in ("threshold", "max_results", "boost_factor"))


# ════════════════════════════════════════════════════════════
#  验收条件 2：无样本 → no_samples → 跳过（绝不伪造指标）
# ════════════════════════════════════════════════════════════

class TestNoSamples:
    def test_no_samples_skips_evolution(self, tmp_path):
        evolver, _, _ = build_stack(tmp_path, threshold=0.05)
        pool = EvalSamplePool(base_dir=str(tmp_path / "empty_evals"))  # 无任何样本目录
        evaluator = SkillExecutorEvaluator(pool=pool, runner=_CountingRunner())
        r = evolver.evolve_once(_CANDIDATE_ID, evaluator=evaluator)
        assert r.skipped is True
        assert "真实评估不可用" in r.error
        assert "no_samples" in r.error


# ════════════════════════════════════════════════════════════
#  验收条件 3：分阶段评估 — 阶段1 淘汰不进阶段2
# ════════════════════════════════════════════════════════════

class TestStagedInEvolver:
    def test_stage1_eliminated_variants_never_enter_stage2(self, tmp_path):
        """初筛淘汰的变异体只用阶段1样本集，全量阶段（10 条）不触发

        证据：runner 总调用数 == 基线 1 + 变异体 2 = 3（每评估仅 1 条阶段1样本）。
        若任一评估进入阶段2 全量，总调用数将 ≥ 3 + 9。
        """
        samples = [make_sample(f"s{i}", task="查询云枢",
                               expected={"type": "contains", "values": ["云枢"]})
                   for i in range(10)]
        pool = make_pool(tmp_path, {"search": samples})
        runner = _CountingRunner(result={"answer": "无关内容"})  # 阶段1 校验失败
        staged = StagedEvaluator(
            SkillExecutorEvaluator(pool=pool, runner=runner),
            pool=pool, stage1_ratio=0.1, stage1_max_samples=1,
            stage1_min_score=0.9,
        )
        evolver, _, _ = build_stack(tmp_path, threshold=0.05)
        r = evolver.evolve_once(
            _CANDIDATE_ID, strategies=_STRATEGIES, evaluator=staged)
        assert r.committed is False            # 无真实提升 → 不提交
        assert runner.calls == 3               # 全量阶段未触发

    def test_stage2_full_eval_when_pass_stage1(self, tmp_path):
        """阶段1 通过 → 阶段2 全量评估（10 条样本全部执行）"""
        samples = [make_sample(f"s{i}", task="查询云枢",
                               expected={"type": "contains", "values": ["云枢"]})
                   for i in range(10)]
        pool = make_pool(tmp_path, {"search": samples})
        runner = _CountingRunner(result={"answer": "云枢 Digital Life"})  # 校验通过
        staged = StagedEvaluator(
            SkillExecutorEvaluator(pool=pool, runner=runner),
            pool=pool, stage1_ratio=0.1, stage1_max_samples=1,
            stage1_min_score=0.3,
        )
        evolver, _, _ = build_stack(tmp_path, threshold=0.05)
        ev = staged.evaluate(evolver._store.get(_CANDIDATE_ID))
        assert ev.stage == "stage2"
        assert ev.sample_count == 10


# ════════════════════════════════════════════════════════════
#  验收条件 4：评估结果写入谱系档案库
# ════════════════════════════════════════════════════════════

class TestLineageEvalResult:
    def test_committed_record_has_eval_result(self, tmp_path):
        """提交时 EvolutionRecord.eval_result 有值，且与变异体评估结果对齐"""
        evolver, _, archive = build_stack(tmp_path, threshold=0.05)
        evaluator = _SplitEvaluator(
            base=_result(success_rate=0.5, latency_ms=2000, satisfaction=0.5),
            variant=_result(success_rate=0.9, latency_ms=500, satisfaction=0.9),
        )
        r = evolver.evolve_once(_CANDIDATE_ID, strategies=_STRATEGIES, evaluator=evaluator)
        assert r.committed is True
        assert r.improvement == pytest.approx(0.9 - 0.53, abs=0.01)

        chain = archive.get_lineage(_CANDIDATE_ID)
        assert chain, "提交应写入谱系档案库"
        rec = chain[-1]
        assert isinstance(rec.eval_result, dict)
        assert rec.eval_result["score"] == pytest.approx(0.9)
        assert rec.eval_result["status"] == "completed"
        assert rec.eval_result["sample_count"] == 5
        assert set(rec.eval_result["dimensions"]) == {
            "success_rate", "latency_norm", "satisfaction"}

    def test_no_commit_no_eval_result_record(self, tmp_path):
        """未提交（真实评估无提升）→ 写 rejected 谱系记录

        Why: EVO-T3 验收『提交/拒绝/跳过均写谱系』——拒绝决策仍须落库
        （decision=rejected），保证审计链完整；修复前因谱系写入静默失败
        该路径恰好缺失，现已对齐新语义。
        """
        evolver, _, archive = build_stack(tmp_path, threshold=0.05)
        evaluator = _SplitEvaluator(
            base=_result(success_rate=0.5, latency_ms=2000, satisfaction=0.5),
            variant=_result(success_rate=0.5, latency_ms=2100, satisfaction=0.5),
        )
        r = evolver.evolve_once(_CANDIDATE_ID, strategies=_STRATEGIES, evaluator=evaluator)
        assert r.committed is False
        assert r.decision == "rejected"
        chain = archive.get_lineage(_CANDIDATE_ID)
        assert chain, "拒绝决策应写入谱系（EVO-T3『拒绝也写谱系』）"
        assert chain[-1].decision == "rejected"


# ════════════════════════════════════════════════════════════
#  预算熔断降级路径
# ════════════════════════════════════════════════════════════

class TestBudgetFuseInEvolver:
    def test_baseline_budget_exceeded_skips(self, tmp_path):
        evolver, _, _ = build_stack(tmp_path, threshold=0.05)
        evaluator = _SplitEvaluator(
            base=EvaluationResult(skill_id=_CANDIDATE_ID, status="budget_exceeded",
                                  notes=["token 预算熔断"]),
            variant=_result(success_rate=0.9, latency_ms=500, satisfaction=0.9),
        )
        r = evolver.evolve_once(_CANDIDATE_ID, strategies=_STRATEGIES, evaluator=evaluator)
        assert r.skipped is True
        assert "真实评估不可用" in r.error
        assert "budget_exceeded" in r.error

    def test_variant_budget_exceeded_excluded_from_competition(self, tmp_path):
        """变异体预算熔断 → 不参与比较（无有效变异体），绝不伪造分数"""
        evolver, _, _ = build_stack(tmp_path, threshold=0.05)
        evaluator = _SplitEvaluator(
            base=_result(success_rate=0.5, latency_ms=2000, satisfaction=0.5),
            variant=EvaluationResult(skill_id=_CANDIDATE_ID, status="budget_exceeded",
                                     notes=["token 预算熔断"]),
        )
        r = evolver.evolve_once(_CANDIDATE_ID, strategies=_STRATEGIES, evaluator=evaluator)
        assert r.skipped is True
        assert "无有效变异体通过评估" in r.error


# ════════════════════════════════════════════════════════════
#  evolve_batch 透传
# ════════════════════════════════════════════════════════════

class TestEvolveBatch:
    def test_batch_passes_evaluator_to_evolve_once(self, tmp_path):
        evolver, _, _ = build_stack(tmp_path, threshold=0.05)
        evaluator = _SplitEvaluator(
            base=_result(success_rate=0.5, latency_ms=2000, satisfaction=0.5),
            variant=_result(success_rate=0.9, latency_ms=500, satisfaction=0.9),
        )
        report = evolver.evolve_batch([_CANDIDATE_ID], max_rounds=1, evaluator=evaluator)
        assert len(report.results) == 1
        assert report.results[0].committed is True
        assert len(evaluator.calls) == 3  # 基线 + 2 变异体均走真实评估
