"""父代选择策略单元测试（任务 EVO-T3，重建版）

⚠️ 本文件为重建版：原 test_parent_selection.py 被并行会话清理（从未提交，
无法从 git 恢复）。重建依据 parent_selection.py 当前实现（5 策略 + 子代惩罚）。

覆盖验收条件 1：
    5 种选择策略均可运行；score_child_prop 子代惩罚衰减有测试证明。
"""
import random
from pathlib import Path

import pytest

from agent.skills_mgmt.lineage import EvolutionArchive, EvolutionRecord
from agent.skills_mgmt.parent_selection import (
    ParentSelectionStrategy,
    ParentSelector,
    child_penalty,
    score_child_prop_weight,
    score_weight,
    sigmoid,
)


# ════════════════════════════════════════════════════════════
#  构造辅助
# ════════════════════════════════════════════════════════════

@pytest.fixture
def archive(tmp_path):
    return EvolutionArchive(
        active_path=str(tmp_path / "evolution_archive.jsonl"),
        archive_path=str(tmp_path / "evolution_archive_old.jsonl"),
        active_generations=10,
    )


def make_record(obj="skill-p", version="1.0.0", *, parent_id=None,
                decision="committed", score=None):
    rec = EvolutionRecord(
        object_type="skill",
        object_id=obj,
        parent_record_id=parent_id,
        parent_version=parent_id or "",
        new_version=version,
        strategy="fine_tune",
        change_summary="",
        decision=decision,
        trigger="manual",
        eval_result=(
            {"score": score, "dimensions": {"success_rate": 0.9}, "sample_count": 10}
            if score is not None else None
        ),
    )
    return rec


def add_committed(archive, obj="skill-p", version="1.0.0", *, parent_id=None,
                  score=None):
    rec = make_record(obj, version, parent_id=parent_id, score=score)
    archive.append(rec)
    return rec


# ════════════════════════════════════════════════════════════
#  纯函数：sigmoid / child_penalty / 联合权重
# ════════════════════════════════════════════════════════════

class TestSigmoid:
    def test_zero_score_is_half(self):
        assert sigmoid(0.0) == pytest.approx(0.5)

    def test_positive_increasing(self):
        assert sigmoid(0.1) < sigmoid(0.9) < sigmoid(10.0)

    def test_saturates_to_one(self):
        assert sigmoid(100.0) == pytest.approx(1.0)

    def test_negative_low(self):
        assert sigmoid(-10.0) < 0.01

    def test_no_overflow(self):
        assert sigmoid(1e6) == 1.0


class TestChildPenalty:
    def test_zero_children_no_penalty(self):
        assert child_penalty(0) == 1.0

    def test_n_children_approx_e_inv(self):
        assert child_penalty(8) == pytest.approx(0.3679, abs=1e-3)

    def test_many_children_approaches_zero(self):
        assert child_penalty(100) < 0.01

    def test_custom_n_and_power(self):
        assert child_penalty(16, n=16, power=2) == pytest.approx(0.3679, abs=1e-3)

    def test_monotonic_decreasing(self):
        values = [child_penalty(k) for k in range(0, 20)]
        assert all(values[i] >= values[i + 1] for i in range(len(values) - 1))


class TestScoreChildPropWeight:
    def test_combines_score_and_penalty(self):
        expected = sigmoid(0.8) * child_penalty(2)
        assert score_child_prop_weight(0.8, 2) == pytest.approx(expected)

    def test_more_children_lower_weight(self):
        w0 = score_child_prop_weight(0.8, 0)
        w8 = score_child_prop_weight(0.8, 8)
        assert w8 < w0

    def test_none_score_keeps_exploration(self):
        assert score_weight(None) == pytest.approx(0.5)


# ════════════════════════════════════════════════════════════
#  候选与统计
# ════════════════════════════════════════════════════════════

class TestCandidates:
    def test_only_active_committed(self, archive):
        add_committed(archive, version="1.0.0", score=0.6)
        rejected = make_record("skill-p", "1.0.1", decision="rejected", score=0.9)
        archive.append(rejected)
        sel = ParentSelector(archive)
        cands = sel.candidate_records("skill-p")
        assert len(cands) == 1
        assert cands[0].new_version == "1.0.0"

    def test_archived_excluded(self, archive):
        rec = add_committed(archive, version="1.0.0", score=0.5)
        rec.archived = True
        sel = ParentSelector(archive)
        assert sel.candidate_records("skill-p") == []

    def test_children_count(self, archive):
        parent = add_committed(archive, version="1.0.0", score=0.7)
        add_committed(archive, version="1.0.1", parent_id=parent.record_id, score=0.8)
        add_committed(archive, version="1.0.2", parent_id=parent.record_id, score=0.9)
        sel = ParentSelector(archive)
        assert sel.children_count("skill-p", parent.record_id) == 2


# ════════════════════════════════════════════════════════════
#  5 种策略 select 行为
# ════════════════════════════════════════════════════════════

class TestSelectStrategies:
    def _seed_three(self, archive):
        a = add_committed(archive, version="1.0.0", score=0.5)
        b = add_committed(archive, version="1.0.1", parent_id=a.record_id, score=0.9)
        add_committed(archive, version="1.0.2", parent_id=b.record_id, score=0.3)
        return archive

    def test_best_picks_highest_score(self, archive):
        self._seed_three(archive)
        sel = ParentSelector(archive, strategy=ParentSelectionStrategy.BEST)
        chosen = sel.select("skill-p")
        assert chosen.new_version == "1.0.1"  # score=0.9

    def test_latest_picks_newest(self, archive):
        self._seed_three(archive)
        sel = ParentSelector(archive, strategy=ParentSelectionStrategy.LATEST)
        chosen = sel.select("skill-p")
        assert chosen.new_version == "1.0.2"

    def test_random_reproducible_with_seed(self, archive):
        self._seed_three(archive)
        rng1 = random.Random(42)
        rng2 = random.Random(42)
        s1 = ParentSelector(archive, strategy=ParentSelectionStrategy.RANDOM, rng=rng1)
        s2 = ParentSelector(archive, strategy=ParentSelectionStrategy.RANDOM, rng=rng2)
        assert s1.select("skill-p").record_id == s2.select("skill-p").record_id

    def test_score_prop_weights_match_sigmoid(self, archive):
        self._seed_three(archive)
        sel = ParentSelector(archive, strategy=ParentSelectionStrategy.SCORE_PROP)
        cands = sel.candidate_records("skill-p")
        w = sel.weights("skill-p", cands)
        assert w == pytest.approx([score_weight(r.get_score()) for r in cands])

    def test_score_child_prop_is_default(self):
        assert ParentSelector(strategy=None).strategy == ParentSelectionStrategy.SCORE_CHILD_PROP

    def test_no_candidates_returns_none(self, archive):
        sel = ParentSelector(archive)
        assert sel.select("skill-missing") is None

    def test_all_zero_weights_fallback_uniform(self, archive):
        a = add_committed(archive, version="1.0.0", score=-100.0)
        add_committed(archive, version="1.0.1", parent_id=a.record_id, score=-100.0)
        sel = ParentSelector(archive, strategy=ParentSelectionStrategy.SCORE_PROP, rng=random.Random(1))
        cands = sel.candidate_records("skill-p")
        assert sum(sel.weights("skill-p", cands)) > 0


# ════════════════════════════════════════════════════════════
#  子代惩罚衰减实证（验收 1 关键测试）
# ════════════════════════════════════════════════════════════

class TestChildPenaltyDecay:
    def test_more_children_lower_weight_same_score(self, archive):
        a = add_committed(archive, version="1.0.0", score=0.7)
        b = add_committed(archive, version="1.0.1", parent_id=a.record_id, score=0.7)
        add_committed(archive, version="1.0.2", parent_id=b.record_id, score=0.8)
        add_committed(archive, version="1.0.3", parent_id=b.record_id, score=0.8)

        sel = ParentSelector(archive, strategy=ParentSelectionStrategy.SCORE_CHILD_PROP)
        cands = sel.candidate_records("skill-p")
        w = sel.weights("skill-p", cands)

        weights = {r.new_version: wi for r, wi in zip(cands, w)}
        # b(2 子代) 权重 < a(0 子代) 权重
        assert weights["1.0.1"] < weights["1.0.0"]

    def test_children_penalty_scales_with_n(self, archive):
        rec = make_record("skill-p", "1.0.0", score=0.7)
        w_default = score_child_prop_weight(0.7, 4)          # N=8
        w_aggressive = score_child_prop_weight(0.7, 4, n=4)  # N=4
        assert w_aggressive < w_default
