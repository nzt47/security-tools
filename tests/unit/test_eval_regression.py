"""评估集回归门禁测试（任务1 Step 4/Step 6）

覆盖四分支（PASS/FAIL/NO_SAMPLES/budget_exceeded）与基线语义:
    1. 首次评估 → 记录基线 + PASS（无退化判定基准）
    2. 未退化（delta >= -0.05）→ PASS
    3. 退化（delta < -0.05）→ FAIL
    4. 版本/类别未登记 → NO_SAMPLES（绝不伪造指标）
    5. 预算熔断 → budget_exceeded（绝不伪造分数）
    6. 基线只升不降：新分更高更新基线，更低不更新
    7. query_regression_status 只读查询
    8. CLI 预算解析（500k）
"""
import json
from pathlib import Path

import pytest

from agent.skills_mgmt.eval_regression import (
    BUDGET_EXCEEDED,
    FAIL,
    NO_SAMPLES,
    PASS,
    BaselineStore,
    RegressionGate,
    RegressionResult,
    SamplesetRegistry,
    _parse_budget,
    evaluate_regression,
    query_regression_status,
)
from agent.skills_mgmt.evaluator import (
    EvalSample,
    EvalSamplePool,
    EvaluationResult,
    ExecOutcome,
)


# ════════════════════════════════════════════════════════════
#  构造辅助
# ════════════════════════════════════════════════════════════

def make_sample(sid: str, category: str, *, task: str = "查询云枢",
                expected=None) -> EvalSample:
    meta = {"input": {"query": "云枢"}, "difficulty": "SIMPLE",
            "source": "manual", "input_hash": f"hash-{sid}"}
    return EvalSample(id=sid, category=category, task=task,
                      expected_output=expected, metadata=meta)


def make_pool(base: Path, samples_by_cat: dict) -> EvalSamplePool:
    base = Path(base) / "evals"
    for cat, samples in samples_by_cat.items():
        d = base / cat
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{cat}.json").write_text(
            json.dumps([s.to_dict() for s in samples], ensure_ascii=False),
            encoding="utf-8")
    return EvalSamplePool(base_dir=str(base))


def write_manifest(base: Path, ids_by_cat: dict) -> Path:
    path = Path(base) / "manifest.json"
    path.write_text(json.dumps({
        "current": "v1",
        "versions": {"v1": {"categories": ids_by_cat}},
    }), encoding="utf-8")
    return path


class _StubEvaluator:
    """协议实现：按注入的脚本返回 EvaluationResult；类别按技能标签解析"""

    def __init__(self, script):
        self.pool = None
        self._script = script

    def resolve_category(self, skill):
        tags = [str(t).lower() for t in (getattr(skill, "tags", None) or [])]
        for cat in ("search", "code", "chat", "tool", "planning"):
            if cat in tags:
                return cat
        return "search"

    def evaluate(self, skill, sample_ids=None, *, params=None, budget_tokens=None):
        return self._script(skill, sample_ids, params, budget_tokens)


def _completed(skill, sample_ids, params, budget, *, success_rate=0.8,
               latency_ms=1000, satisfaction=0.5, status="completed"):
    return EvaluationResult(
        skill_id=skill.id, status=status,
        success_rate=success_rate, latency_ms=latency_ms,
        satisfaction=satisfaction, sample_count=len(sample_ids or []),
        cost_tokens=min(budget or 100000, 100),
    )


class _Skill:
    def __init__(self, sid="skill-x", tags=None):
        self.id = sid
        self.tags = tags or ["search"]


def build_gate(base: Path, *, evaluator=None, script=None,
               degrade_threshold=None) -> RegressionGate:
    pool = make_pool(base, {"search": [make_sample("s1", "search"),
                                       make_sample("s2", "search")]})
    write_manifest(base, {"search": ["s1", "s2"]})
    if evaluator is None:
        evaluator = _StubEvaluator(script or _completed)
    return RegressionGate(
        samples_dir=str(pool.base_dir),
        manifest_path=base / "manifest.json",
        baseline_store=BaselineStore(base / "baselines.json"),
        evaluator_factory=lambda skill: evaluator,
        degrade_threshold=degrade_threshold,
    )


# ════════════════════════════════════════════════════════════
#  1. 首次评估建立基线 + PASS
# ════════════════════════════════════════════════════════════

class TestFirstEvalBaseline:
    def test_first_eval_records_baseline_and_passes(self, tmp_path):
        gate = build_gate(tmp_path)
        r = gate.evaluate(_Skill("skill-a"))
        assert r.status == PASS
        assert r.baseline_score is None
        assert r.delta_vs_baseline is None
        assert "首次评估" in r.notes[0]
        # 基线已持久化
        assert gate.baseline_score("skill-a") == pytest.approx(
            _completed(_Skill("skill-a"), None, None, None).score, abs=0.05)

    def test_second_eval_same_score_passes(self, tmp_path):
        gate = build_gate(tmp_path)
        gate.evaluate(_Skill("skill-a"))
        r = gate.evaluate(_Skill("skill-a"))
        assert r.status == PASS
        assert r.baseline_score is not None
        assert r.delta_vs_baseline == pytest.approx(0.0, abs=0.01)


# ════════════════════════════════════════════════════════════
#  2. 未退化 → PASS
# ════════════════════════════════════════════════════════════

class TestPass:
    def test_slight_degradation_within_threshold_passes(self, tmp_path):
        """退化 0.02 < 阈值 0.05 → PASS"""
        gate = build_gate(tmp_path, script=lambda s, ids, p, b: _completed(
            s, ids, p, b, success_rate=0.8))
        gate.evaluate(_Skill("skill-a"))  # 基线 success_rate=0.8

        gate2 = build_gate(tmp_path, script=lambda s, ids, p, b: _completed(
            s, ids, p, b, success_rate=0.78))
        r = gate2.evaluate(_Skill("skill-a"))
        assert r.status == PASS
        assert r.delta_vs_baseline is not None

    def test_improvement_updates_baseline(self, tmp_path):
        """新分高于基线 → PASS 且基线更新（基线只升不降）"""
        gate = build_gate(tmp_path, script=lambda s, ids, p, b: _completed(
            s, ids, p, b, success_rate=0.8))
        gate.evaluate(_Skill("skill-a"))
        base0 = gate.baseline_score("skill-a")

        gate2 = build_gate(tmp_path, script=lambda s, ids, p, b: _completed(
            s, ids, p, b, success_rate=0.95))
        r = gate2.evaluate(_Skill("skill-a"))
        assert r.status == PASS
        assert gate2.baseline_score("skill-a") > base0


# ════════════════════════════════════════════════════════════
#  3. 退化 → FAIL
# ════════════════════════════════════════════════════════════

class TestFail:
    def test_regression_beyond_threshold_fails(self, tmp_path):
        gate = build_gate(tmp_path, script=lambda s, ids, p, b: _completed(
            s, ids, p, b, success_rate=0.9))
        gate.evaluate(_Skill("skill-a"))  # 基线 0.9

        gate2 = build_gate(tmp_path, script=lambda s, ids, p, b: _completed(
            s, ids, p, b, success_rate=0.5))  # 显著退化
        r = gate2.evaluate(_Skill("skill-a"))
        assert r.status == FAIL
        assert r.delta_vs_baseline < -0.05

    def test_fail_does_not_update_baseline(self, tmp_path):
        gate = build_gate(tmp_path, script=lambda s, ids, p, b: _completed(
            s, ids, p, b, success_rate=0.9))
        gate.evaluate(_Skill("skill-a"))
        base0 = gate.baseline_score("skill-a")

        gate2 = build_gate(tmp_path, script=lambda s, ids, p, b: _completed(
            s, ids, p, b, success_rate=0.4))
        gate2.evaluate(_Skill("skill-a"))
        # 退化不更新基线（防温水煮青蛙）
        assert gate2.baseline_score("skill-a") == pytest.approx(base0, abs=1e-9)

    def test_custom_threshold(self, tmp_path):
        """自定义阈值 0.1：退化 0.08 < 0.1 → PASS"""
        gate = build_gate(tmp_path, script=lambda s, ids, p, b: _completed(
            s, ids, p, b, success_rate=0.9))
        gate.evaluate(_Skill("skill-a"))
        gate2 = build_gate(tmp_path, script=lambda s, ids, p, b: _completed(
            s, ids, p, b, success_rate=0.82), degrade_threshold=0.1)
        r = gate2.evaluate(_Skill("skill-a"))
        assert r.status == PASS


# ════════════════════════════════════════════════════════════
#  4. NO_SAMPLES（绝不伪造指标）
# ════════════════════════════════════════════════════════════

class TestNoSamples:
    def test_version_not_registered(self, tmp_path):
        gate = build_gate(tmp_path)
        r = gate.evaluate(_Skill("skill-a"), sampleset_version="v9")
        assert r.status == NO_SAMPLES
        assert r.score == 0.0
        assert "NO_SAMPLES" in r.notes[0]

    def test_category_not_in_manifest(self, tmp_path):
        pool = make_pool(tmp_path, {"search": [make_sample("s1", "search")]})
        write_manifest(tmp_path, {"search": ["s1"]})
        gate = RegressionGate(
            samples_dir=str(pool.base_dir),
            manifest_path=tmp_path / "manifest.json",
            baseline_store=BaselineStore(tmp_path / "baselines.json"),
            evaluator_factory=lambda s: _StubEvaluator(
                lambda s, ids, p, b: _completed(s, ids, p, b)),
        )
        skill = _Skill("code-skill", tags=["code"])
        r = gate.evaluate(skill)
        assert r.status == NO_SAMPLES

    def test_degraded_eval_maps_to_no_samples(self, tmp_path):
        gate = build_gate(tmp_path, script=lambda s, ids, p, b: _completed(
            s, ids, p, b, status="degraded"))
        r = gate.evaluate(_Skill("skill-a"))
        assert r.status == NO_SAMPLES


# ════════════════════════════════════════════════════════════
#  5. 预算熔断
# ════════════════════════════════════════════════════════════

class TestBudgetExceeded:
    def test_budget_exceeded_status(self, tmp_path):
        gate = build_gate(tmp_path, script=lambda s, ids, p, b: _completed(
            s, ids, p, b, status="budget_exceeded"))
        r = gate.evaluate(_Skill("skill-a"), budget_tokens=10)
        assert r.status == BUDGET_EXCEEDED
        assert r.used_tokens >= 0
        assert "预算熔断" in r.notes[0]


# ════════════════════════════════════════════════════════════
#  6. 查询与 CLI
# ════════════════════════════════════════════════════════════

class TestQueryAndCli:
    def test_query_regression_status_readonly(self, tmp_path):
        gate = build_gate(tmp_path)
        gate.evaluate(_Skill("skill-a"))
        status = query_regression_status("skill-a", gate=gate)
        assert status is not None
        assert status["skill_id"] == "skill-a"
        assert status["baseline_score"] is not None
        # 只读：不产生基线写（再次查询无副作用）
        status2 = query_regression_status("skill-a", gate=gate)
        assert status2["baseline_score"] == status["baseline_score"]

    def test_query_no_data_returns_none(self, tmp_path):
        gate = build_gate(tmp_path)
        assert query_regression_status("ghost", gate=gate) is None

    def test_parse_budget(self):
        assert _parse_budget("500k") == 500000
        assert _parse_budget("1m") == 1000000
        assert _parse_budget("100000") == 100000
        assert _parse_budget("") > 0

    def test_evaluate_regression_free_function(self, tmp_path):
        gate = build_gate(tmp_path)
        r = evaluate_regression(_Skill("skill-a"), gate=gate)
        assert r.status in (PASS, FAIL, NO_SAMPLES, BUDGET_EXCEEDED)

    def test_result_to_dict(self):
        r = RegressionResult(skill_id="s", sampleset_version="v1", status=PASS,
                             score=0.8, baseline_score=0.8, delta_vs_baseline=0.0,
                             used_tokens=10, sample_count=2)
        d = r.to_dict()
        assert d["status"] == PASS
        assert d["delta_vs_baseline"] == 0.0
        assert d["baseline_score"] == 0.8
