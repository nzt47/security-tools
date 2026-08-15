"""进化循环单元测试（任务 EVO-T3，重建版）

⚠️ 本文件为重建版：原 test_evolution_loop.py 与 test_offline_evolver.py 均被
并行会话清理（从未提交，无法从 git 恢复）。重建依据 offline_evolver.py 当前
实现（evolve_once/evolve_batch/预算熔断/父代串联）与 test_evolver_real_eval.py
的构造模式。

覆盖进化循环核心（补充 T3 验收证据）:
    1. 首代提交 + 第二代父代记录串联（parent_record_id 指向上一代）；
    2. 无提升 → rejected，拒绝也写谱系；
    3. 技能不存在 / 不满足候选 → skipped，跳过也写谱系；
    4. 预算熔断：max_tokens_per_round 超限 → 不再评估新变异体；
    5. evolve_batch 多技能循环 + 批量报告；
    6. 真实评估（注入 evaluator）驱动提交判定（improvement >= threshold）。
"""
from pathlib import Path

import pytest

from agent.skills_mgmt.evaluator import EvaluationResult
from agent.skills_mgmt.lineage import EvolutionArchive, get_default_archive
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
#  构造辅助（对齐 test_evolver_real_eval.py 模式）
# ════════════════════════════════════════════════════════════

_CANDIDATE_ID = "loop-search-opt"
_STRATEGIES = [EvolutionStrategy.FINE_TUNE, EvolutionStrategy.FINE_TUNE]


def _make_skill(sid: str = _CANDIDATE_ID, *, version: str = "1.0.0",
                usage: int = 50, success_rate: float = 0.7) -> Skill:
    return Skill(
        id=sid, name="循环测试技能", description="d",
        content="# c", content_type=ContentType.MARKDOWN,
        category=SkillCategory.CUSTOM, status=SkillStatus.APPROVED,
        enabled=True, version=version, tags=["search"],
        default_params={"threshold": 0.5, "max_results": 100, "boost_factor": 1.2},
        metrics=SkillMetrics(
            usage_count=usage, success_count=int(usage * success_rate),
            failure_count=int(usage * (1 - success_rate)),
            success_rate=success_rate, avg_latency_ms=3000, p95_latency_ms=4500,
        ),
    )


@pytest.fixture
def loop_stack(tmp_path):
    """store + enhancer(注入档案库) + evolver（候选技能满足进化条件）"""
    store = SkillStore(path=str(tmp_path / "skills.json"))
    store.upsert(_make_skill())
    archive = EvolutionArchive(
        active_path=str(tmp_path / "archive.jsonl"),
        archive_path=str(tmp_path / "archive_old.jsonl"),
    )
    enhancer = _StubEnhancer(store, lineage_archive=archive)
    evolver = OfflineEvolver(
        store, enhancer,
        min_usage=10, target_success_rate=0.95,
        max_variants_per_skill=2,
        improvement_threshold=0.01, random_seed=42,
        archive=archive,  # 谱系钩子写入同一档案库（否则落默认单例）
    )
    return evolver, store, archive


class _BumpResult:
    """bump_version 返回对象（对齐 VersionBump 的 new_version 字段）"""

    def __init__(self, *, new_version, old_version):
        self.new_version = new_version
        self.old_version = old_version


class _StubEnhancer:
    """最小 SkillEnhancer 鸭子类型：bump_version 直接改 store 中的版本号

    签名对齐 offline_evolver 调用（bump_version(skill_id, "patch",
    changelog=..., eval_result=...)），返回带 new_version 属性的对象；
    触发谱系钩子时透传 skill_id/old_version/new_version/changelog
    （_lineage_hook 以 _round_ctx 为主数据源，ctx 提供版本信息）。
    """

    def __init__(self, store, *, lineage_archive=None):
        self._store = store
        self._lineage_archive = lineage_archive or get_default_archive()
        self._lineage_hook = None

    def bump_version(self, skill_id, version_type="patch", *,
                     changelog=None, eval_result=None):
        skill = self._store.get(skill_id)
        old = skill.version
        major, minor, patch = (int(p) for p in old.split("."))
        new_version = f"{major}.{minor}.{patch + 1}"
        skill.version = new_version
        self._store.upsert(skill)
        if self._lineage_hook is not None:
            self._lineage_hook({
                "skill_id": skill_id,
                "old_version": old,
                "new_version": new_version,
                "changelog": changelog or "",
                "eval_result": eval_result,
            })
        return _BumpResult(new_version=new_version, old_version=old)

    def set_lineage_hook(self, hook):
        self._lineage_hook = hook


class _ControlledEvaluator:
    """可控真实评估器：基线/变异体分数与 token 成本均可配置

    签名对齐 offline_evolver 调用：
        evaluate(skill)             → 基线结果
        evaluate(skill, params=...) → 变异体结果
    """

    def __init__(self, *, base_score=0.5, variant_score=0.9,
                 base_cost=10, variant_cost=20):
        self.base_score = base_score
        self.variant_score = variant_score
        self.base_cost = base_cost
        self.variant_cost = variant_cost
        self.eval_calls = 0

    @staticmethod
    def _result(score, cost_tokens):
        return EvaluationResult(
            skill_id="loop", status="completed",
            success_rate=score, latency_ms=100.0, satisfaction=0.5,
            cost_tokens=cost_tokens, sample_count=10, stage="stage2",
        )

    def evaluate(self, skill, params=None):
        self.eval_calls += 1
        if params is None:
            return self._result(self.base_score, self.base_cost)
        return self._result(self.variant_score, self.variant_cost)


# ════════════════════════════════════════════════════════════
#  谱系记录辅助
# ════════════════════════════════════════════════════════════

def _records(archive, skill_id):
    return archive.list_by_object(skill_id)


def _committed_ids(archive, skill_id):
    return [r.record_id for r in _records(archive, skill_id)
            if r.decision == "committed"]


# ════════════════════════════════════════════════════════════
#  首代提交 + 父代串联
# ════════════════════════════════════════════════════════════

class TestFirstGenerationCommit:
    def test_first_round_commits_no_parent(self, loop_stack):
        evolver, store, archive = loop_stack
        res = evolver.evolve_once(
            _CANDIDATE_ID, strategies=_STRATEGIES,
            evaluator=_ControlledEvaluator(base_score=0.5, variant_score=0.9),
        )
        assert res.decision == "committed"
        committed = [r for r in _records(archive, _CANDIDATE_ID)
                     if r.decision == "committed"]
        assert len(committed) == 1
        assert committed[0].parent_record_id is None  # 首代无父代

    def test_second_round_links_parent(self, loop_stack):
        """第二代 committed 记录的 parent_record_id 指向第一代记录（验收 2）"""
        evolver, store, archive = loop_stack
        ev = _ControlledEvaluator(base_score=0.5, variant_score=0.9)
        evolver.evolve_once(_CANDIDATE_ID, strategies=_STRATEGIES, evaluator=ev)
        evolver.evolve_once(_CANDIDATE_ID, strategies=_STRATEGIES, evaluator=ev)

        ids = _committed_ids(archive, _CANDIDATE_ID)
        assert len(ids) == 2
        recs = [r for r in _records(archive, _CANDIDATE_ID)
                if r.decision == "committed"]
        recs.sort(key=lambda r: r.created_at)
        assert recs[1].parent_record_id == recs[0].record_id


# ════════════════════════════════════════════════════════════
#  拒绝 / 跳过路径（均写谱系）
# ════════════════════════════════════════════════════════════

class TestRejectAndSkip:
    def test_no_improvement_rejected(self, loop_stack):
        """变异体分数低于基线 → rejected，拒绝也写谱系（验收 2）"""
        evolver, store, archive = loop_stack
        res = evolver.evolve_once(
            _CANDIDATE_ID, strategies=_STRATEGIES,
            evaluator=_ControlledEvaluator(base_score=0.9, variant_score=0.5),
        )
        assert res.decision == "rejected"
        decisions = {r.decision for r in _records(archive, _CANDIDATE_ID)}
        assert "rejected" in decisions

    def test_missing_skill_skipped_with_lineage(self, loop_stack):
        evolver, store, archive = loop_stack
        res = evolver.evolve_once("no-such-skill", strategies=_STRATEGIES)
        assert res.decision == "skipped"
        skipped = [r for r in _records(archive, "no-such-skill")
                   if r.decision == "skipped"]
        assert len(skipped) == 1

    def test_not_candidate_skipped_with_lineage(self, loop_stack):
        """usage 过低 → 不满足候选 → skipped + 谱系"""
        store = loop_stack[1]
        store.upsert(_make_skill("low-usage", usage=2, success_rate=0.3))
        evolver, _, archive = loop_stack
        res = evolver.evolve_once("low-usage", strategies=_STRATEGIES)
        assert res.decision == "skipped"
        assert any(r.decision == "skipped"
                   for r in _records(archive, "low-usage"))


# ════════════════════════════════════════════════════════════
#  预算熔断
# ════════════════════════════════════════════════════════════

class TestBudgetBreach:
    def test_budget_breach_stops_evaluation(self, tmp_path):
        """max_tokens_per_round 注入超小值 → 熔断，不再评估更多变异体"""
        store = SkillStore(path=str(tmp_path / "skills.json"))
        store.upsert(_make_skill())
        archive = EvolutionArchive(
            active_path=str(tmp_path / "archive.jsonl"),
            archive_path=str(tmp_path / "archive_old.jsonl"),
        )
        evolver = OfflineEvolver(
            store, _StubEnhancer(store, lineage_archive=archive),
            min_usage=10, target_success_rate=0.95,
            max_variants_per_skill=2,
            improvement_threshold=0.01, random_seed=42,
            max_tokens_per_round=1,  # 1 token → 立即熔断
        )
        ev = _ControlledEvaluator(base_cost=10, variant_cost=20)
        res = evolver.evolve_once(_CANDIDATE_ID, strategies=_STRATEGIES, evaluator=ev)
        assert res.budget_breached or res.decision in ("skipped", "rejected")
        # 基线成本(10)已超预算(1)：后续变异体不应再评估
        assert ev.eval_calls <= 2  # 基线 + 至多一次变异体尝试


# ════════════════════════════════════════════════════════════
#  批量进化
# ════════════════════════════════════════════════════════════

class TestBatchEvolution:
    def test_batch_evolves_multiple_skills(self, loop_stack):
        evolver, store, archive = loop_stack
        store.upsert(_make_skill("loop-second", usage=60, success_rate=0.8))
        report = evolver.evolve_batch(
            skill_ids=[_CANDIDATE_ID, "loop-second"],
            evaluator=_ControlledEvaluator(base_score=0.5, variant_score=0.9),
        )
        assert report.evolved_count == 2
        assert any(r.decision == "committed" for r in report.results)
        assert len(_committed_ids(archive, _CANDIDATE_ID)) >= 1
        assert len(_committed_ids(archive, "loop-second")) >= 1

    def test_batch_with_missing_skill_does_not_abort(self, loop_stack):
        """批量中一个技能不存在 → 跳过该技能，不中断整批（边界显性化）"""
        evolver, store, archive = loop_stack
        report = evolver.evolve_batch(
            skill_ids=[_CANDIDATE_ID, "missing-skill"],
            evaluator=_ControlledEvaluator(base_score=0.5, variant_score=0.9),
        )
        assert report.evolved_count == 1  # missing 被跳过
        assert report.skipped_count == 1
        assert any(r.decision == "skipped"
                   for r in _records(archive, "missing-skill"))
