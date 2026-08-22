"""offline_evolver 回归门禁接线测试（任务1 Step 5/Step 6）

覆盖:
    1. EVOLUTION_REGRESSION_GATE=enforce：构造退化候选 → decision=rejected 且谱系有记录
    2. warn_only（默认）：退化候选 → 只读告警，仍提交（decision=committed）
    3. off：完全不调用门禁（零开销，行为与未接线一致）
    4. 无基线 warn_only → 跳过评估（零行为变化）
    5. enforce 首次评估：以当前技能建立基线，再评估变异体
"""
import json

import pytest

from agent.skills_mgmt.eval_regression import FAIL, PASS, RegressionResult
from agent.skills_mgmt.enhancer import SkillEnhancer
from agent.skills_mgmt.evaluator import EvaluationResult
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

_CANDIDATE_ID = "reg-gate-skill"


def build_stack(base, *, threshold: float = 0.05,
                regression_gate=None):
    """构造 store + enhancer + evolver（可注入回归门禁）"""
    store = SkillStore(path=str(base / "skills.json"))
    store.upsert(Skill(
        id=_CANDIDATE_ID, name="回归门禁技能", description="d",
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
        regression_gate=regression_gate,
    )
    return evolver, store, archive


class _SplitEvaluator:
    """基线成功、变异体成功（进化提升可提交）的真实评估桩"""

    def __init__(self):
        self.pool = None

    def resolve_category(self, skill):
        return "search"

    def evaluate(self, skill, sample_ids=None, *, params=None, budget_tokens=None):
        if params is None:
            return EvaluationResult(
                skill_id=skill.id, status="completed",
                success_rate=0.6, latency_ms=2000, satisfaction=0.5,
                sample_count=2, cost_tokens=10)
        return EvaluationResult(
            skill_id=skill.id, status="completed",
            success_rate=0.95, latency_ms=500, satisfaction=0.9,
            sample_count=2, cost_tokens=10)


class _FakeGate:
    """回归门禁桩：按脚本返回 RegressionResult"""

    def __init__(self, script):
        self._script = script
        self.calls = []

    def has_baseline(self, skill_id, version=None):
        self.calls.append(("has_baseline", skill_id, version))
        return self._script.get("has_baseline", True)

    def evaluate(self, skill, *, params=None, sampleset_version=None,
                 budget_tokens=None, record_baseline=True, evaluator=None):
        self.calls.append(("evaluate", skill.id, dict(params or {}),
                           sampleset_version))
        status = self._script.get("status", PASS)
        return RegressionResult(
            skill_id=skill.id, sampleset_version=sampleset_version or "v1",
            status=status, score=0.8,
            baseline_score=0.85 if status == FAIL else 0.8,
            delta_vs_baseline=-0.05 if status == FAIL else 0.0,
            used_tokens=10, sample_count=2,
            eval_result={"status": "completed", "score": 0.8,
                         "sample_count": 2},
        )


# ════════════════════════════════════════════════════════════
#  1. enforce：退化候选被拒 + 谱系有记录
# ════════════════════════════════════════════════════════════

class TestEnforceRejects:
    def test_degraded_candidate_rejected_with_lineage(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVOLUTION_REGRESSION_GATE", "enforce")
        gate = _FakeGate({"status": FAIL, "has_baseline": True})
        evolver, _, archive = build_stack(tmp_path, regression_gate=gate)
        r = evolver.evolve_once(
            _CANDIDATE_ID,
            strategies=[EvolutionStrategy.FINE_TUNE, EvolutionStrategy.FINE_TUNE],
            evaluator=_SplitEvaluator())
        assert r.committed is False
        assert r.decision == "rejected"
        assert "回归" in r.error or "评估集回归" in r.error
        # 谱系有 rejected 记录
        chain = archive.get_lineage(_CANDIDATE_ID)
        assert chain
        last = chain[-1]
        assert last.decision == "rejected"
        assert "评估集回归" in last.change_summary
        # 门禁被调用（基线判断 + 变异体评估）
        assert any(c[0] == "evaluate" for c in gate.calls)

    def test_no_samples_enforce_rejects(self, tmp_path, monkeypatch):
        from agent.skills_mgmt.eval_regression import NO_SAMPLES
        monkeypatch.setenv("EVOLUTION_REGRESSION_GATE", "enforce")
        gate = _FakeGate({"status": NO_SAMPLES, "has_baseline": True})
        evolver, _, archive = build_stack(tmp_path, regression_gate=gate)
        r = evolver.evolve_once(
            _CANDIDATE_ID,
            strategies=[EvolutionStrategy.FINE_TUNE, EvolutionStrategy.FINE_TUNE],
            evaluator=_SplitEvaluator())
        assert r.decision == "rejected"
        assert archive.get_lineage(_CANDIDATE_ID)[-1].decision == "rejected"

    def test_enforce_first_eval_establishes_baseline_before_variant(self,
                                                                    tmp_path,
                                                                    monkeypatch):
        """首次（enforce）：先以当前技能（params=None）建立基线，再评估变异体"""
        monkeypatch.setenv("EVOLUTION_REGRESSION_GATE", "enforce")
        gate = _FakeGate({"status": PASS, "has_baseline": False})
        evolver, _, _ = build_stack(tmp_path, regression_gate=gate)
        r = evolver.evolve_once(
            _CANDIDATE_ID,
            strategies=[EvolutionStrategy.FINE_TUNE, EvolutionStrategy.FINE_TUNE],
            evaluator=_SplitEvaluator())
        assert r.committed is True
        evals = [c for c in gate.calls if c[0] == "evaluate"]
        # 首次：先 params=None 建立基线，再评估变异体（带参数）
        assert evals[0][2] == {} or evals[0][2] is None
        assert any(len(c[2]) > 0 for c in evals[1:])


# ════════════════════════════════════════════════════════════
#  2. warn_only：只读告警仍提交
# ════════════════════════════════════════════════════════════

class TestWarnOnly:
    def test_warn_only_still_commits(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVOLUTION_REGRESSION_GATE", "warn_only")
        gate = _FakeGate({"status": FAIL, "has_baseline": True})
        evolver, _, archive = build_stack(tmp_path, regression_gate=gate)
        r = evolver.evolve_once(
            _CANDIDATE_ID,
            strategies=[EvolutionStrategy.FINE_TUNE, EvolutionStrategy.FINE_TUNE],
            evaluator=_SplitEvaluator())
        assert r.committed is True
        assert r.decision == "committed"
        assert archive.get_lineage(_CANDIDATE_ID)[-1].decision == "committed"

    def test_warn_only_no_baseline_skips_eval(self, tmp_path, monkeypatch):
        """无基线 warn_only → 跳过评估（零行为变化）"""
        monkeypatch.setenv("EVOLUTION_REGRESSION_GATE", "warn_only")
        gate = _FakeGate({"status": PASS, "has_baseline": False})
        evolver, _, _ = build_stack(tmp_path, regression_gate=gate)
        r = evolver.evolve_once(
            _CANDIDATE_ID,
            strategies=[EvolutionStrategy.FINE_TUNE, EvolutionStrategy.FINE_TUNE],
            evaluator=_SplitEvaluator())
        assert r.committed is True
        # 只做了 has_baseline 判断，未做 evaluate（零额外消耗）
        assert not any(c[0] == "evaluate" for c in gate.calls)


# ════════════════════════════════════════════════════════════
#  3. off：完全不调用门禁
# ════════════════════════════════════════════════════════════

class TestOff:
    def test_off_never_calls_gate(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVOLUTION_REGRESSION_GATE", "off")
        gate = _FakeGate({"status": FAIL, "has_baseline": True})
        evolver, _, _ = build_stack(tmp_path, regression_gate=gate)
        r = evolver.evolve_once(
            _CANDIDATE_ID,
            strategies=[EvolutionStrategy.FINE_TUNE, EvolutionStrategy.FINE_TUNE],
            evaluator=_SplitEvaluator())
        assert r.committed is True
        assert gate.calls == []  # 零调用

    def test_default_gate_off_for_backward_compat(self, tmp_path, monkeypatch):
        """默认 warn_only + 无注入门禁 + 无基线 → 提交不受影响（向后兼容）"""
        monkeypatch.delenv("EVOLUTION_REGRESSION_GATE", raising=False)
        evolver, _, archive = build_stack(tmp_path, regression_gate=None)
        r = evolver.evolve_once(
            _CANDIDATE_ID,
            strategies=[EvolutionStrategy.FINE_TUNE, EvolutionStrategy.FINE_TUNE],
            evaluator=_SplitEvaluator())
        assert r.committed is True
        assert archive.get_lineage(_CANDIDATE_ID)[-1].decision == "committed"
