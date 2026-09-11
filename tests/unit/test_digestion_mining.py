"""TASK-S3-01 模式挖掘单测（LCS 骨架 + 决策树分支 + 副作用画像）

验收对应：真实/模拟轨迹 ≥20 条同类可产出候选模式；骨架/分支结果**确定性可复现**。
"""

from __future__ import annotations

import pytest

from agent.digestion import mining
from agent.digestion.models import (
    MIN_STEP_SUPPORT,
    TREE_MIN_SAMPLES,
    Trajectory,
    SameTaskKey,
    TrajectoryStep,
)


def _traj(tid: str, labels, *, files=None, deleted=None, external=None,
          outcome="success"):
    """轨迹构造辅助：副作用**只挂在第一步**（与"能力级 Trace 各自带副作用"一致）"""
    steps = []
    for i, label in enumerate(labels):
        steps.append(TrajectoryStep(
            seq=i + 1, label=label,
            files_written=list(files or []) if i == 0 else [],
            files_deleted=list(deleted or []) if i == 0 else [],
            external_calls=list(external or []) if i == 0 else []))
    return Trajectory(trajectory_id=tid, task_id=tid,
                      key=SameTaskKey("cp.x.y", "shape:p", outcome), steps=steps)


# ════════════════════════════════════════════════════════════
#  1. LCS
# ════════════════════════════════════════════════════════════


class TestPairwiseLCS:
    def test_basic(self):
        assert mining.pairwise_lcs(["a", "b", "c"], ["a", "c"]) == ["a", "c"]

    def test_order_preserved(self):
        assert mining.pairwise_lcs(["c", "a", "b"], ["a", "b", "c"]) in (
            ["a", "b"], ["c"], ["a", "c"], ["b", "c"])

    def test_identical(self):
        assert mining.pairwise_lcs(["a", "b"], ["a", "b"]) == ["a", "b"]

    def test_disjoint(self):
        assert mining.pairwise_lcs(["a"], ["b"]) == []

    def test_empty(self):
        assert mining.pairwise_lcs([], ["a"]) == []
        assert mining.pairwise_lcs(["a"], []) == []

    def test_classic_example(self):
        assert mining.pairwise_lcs(
            list("AGGTAB"), list("GXTXAYB")) == list("GTAB")

    def test_deterministic(self):
        a, b = ["x", "y", "z", "w"], ["y", "x", "w", "z"]
        assert mining.pairwise_lcs(a, b) == mining.pairwise_lcs(a, b)


class TestLCSSimilarity:
    def test_identical_is_one(self):
        assert mining.lcs_similarity(["a", "b"], ["a", "b"]) == 1.0

    def test_both_empty_is_one(self):
        assert mining.lcs_similarity([], []) == 1.0

    def test_one_empty_is_zero(self):
        assert mining.lcs_similarity(["a"], []) == 0.0

    def test_partial(self):
        assert 0 < mining.lcs_similarity(["a", "b", "c"], ["a", "c"]) < 1


class TestSubsequence:
    def test_true(self):
        assert mining.is_subsequence(["a", "c"], ["a", "b", "c"]) is True

    def test_false_on_order(self):
        assert mining.is_subsequence(["c", "a"], ["a", "b", "c"]) is False

    def test_empty_pattern(self):
        assert mining.is_subsequence([], ["a"]) is True


# ════════════════════════════════════════════════════════════
#  2. 骨架
# ════════════════════════════════════════════════════════════


class TestLabelSupport:
    def test_ratio(self):
        support = mining.label_support([["a", "b"], ["a"], ["a", "c"]])
        assert support["a"] == 1.0
        assert support["b"] == pytest.approx(0.3333, abs=1e-3)
        assert support["c"] == pytest.approx(0.3333, abs=1e-3)

    def test_empty(self):
        assert mining.label_support([]) == {}


class TestMedoid:
    def test_picks_most_central(self):
        seqs = [["a", "b", "c"], ["a", "b", "c"], ["a", "b", "c"], ["z"]]
        assert mining.medoid_index(seqs) in (0, 1, 2)

    def test_deterministic_tie_break(self):
        seqs = [["b"], ["a"]]
        assert mining.medoid_index(seqs) == mining.medoid_index(list(seqs))

    def test_single(self):
        assert mining.medoid_index([["a"]]) == 0

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            mining.medoid_index([])


class TestConsensusBackbone:
    def test_common_steps_kept_rare_dropped(self):
        seqs = [["a", "b", "c"]] * 9 + [["a", "rare"]]
        backbone, _ = mining.consensus_backbone(seqs, min_support=0.6)
        labels = [b[0] for b in backbone]
        assert labels == ["a", "b", "c"]
        assert "rare" not in labels

    def test_support_ratio_reported(self):
        seqs = [["a", "b"]] * 8 + [["a"]]
        backbone, _ = mining.consensus_backbone(seqs, min_support=0.5)
        support = dict((label, ratio) for label, ratio, _ in backbone)
        assert support["a"] == 1.0
        assert support["b"] == pytest.approx(0.8889, abs=1e-3)

    def test_order_follows_medoid(self):
        seqs = [["a", "b", "c"]] * 6 + [["c", "b", "a"]] * 4
        backbone, _ = mining.consensus_backbone(seqs)
        assert [b[0] for b in backbone] == ["a", "b", "c"]

    def test_threshold_respected(self):
        seqs = [["a", "b"]] * 5 + [["a", "x"]] * 5
        backbone, _ = mining.consensus_backbone(seqs, min_support=0.6)
        assert [b[0] for b in backbone] == ["a"]

    def test_threshold_boundary_is_inclusive(self):
        """支撑率恰好等于阈值 ⇒ 纳入骨架（`>=`，口径与常量文档一致）"""
        seqs = [["a", "b"]] * 6 + [["a", "x"]] * 4
        backbone, _ = mining.consensus_backbone(seqs, min_support=0.6)
        assert [b[0] for b in backbone] == ["a", "b"]

    def test_empty(self):
        assert mining.consensus_backbone([]) == ([], -1)

    def test_deterministic(self):
        seqs = [["a", "b", "c"]] * 5 + [["a", "c", "b"]] * 4
        assert mining.consensus_backbone(seqs) == mining.consensus_backbone(list(seqs))


class TestBackboneCoverage:
    def test_full_coverage(self):
        seqs = [["a", "b"]] * 10
        assert mining.backbone_coverage(["a", "b"], seqs) == 1.0

    def test_partial(self):
        seqs = [["a", "b"], ["a"]]
        assert mining.backbone_coverage(["a", "b"], seqs) == 0.5

    def test_subsequence_ordering_matters(self):
        seqs = [["b", "a"]]
        assert mining.backbone_coverage(["a", "b"], seqs) == 0.0

    def test_empty(self):
        assert mining.backbone_coverage(["a"], []) == 0.0


class TestOptionalLabels:
    def test_returns_non_backbone_labels(self):
        seqs = [["a", "b", "x"]] * 5 + [["a", "b"]] * 5
        assert mining.optional_labels(["a", "b"], seqs) == ["x"]

    def test_excludes_below_min_support(self):
        seqs = [["a", "b"]] * 99 + [["a", "zzz"]]
        assert mining.optional_labels(["a", "b"], seqs, min_support=0.1) == []


# ════════════════════════════════════════════════════════════
#  3. 决策树与分支条件
# ════════════════════════════════════════════════════════════


class TestDecisionTree:
    def test_pure_leaf_when_single_class(self):
        rows = [{"__label": "success"} for _ in range(5)]
        node = mining.build_decision_tree(rows, features=["has:x"])
        assert node.decision == "leaf" and node.label == "success"

    def test_splits_on_informative_feature(self):
        rows = ([{"__label": "success", "has:x": 1.0}] * 10
                + [{"__label": "failure", "has:x": 0.0}] * 10)
        node = mining.build_decision_tree(rows, features=["has:x"])
        assert node.decision in ("bool", "number")
        assert node.feature == "has:x"

    def test_no_split_when_uninformative(self):
        rows = ([{"__label": "success", "has:x": 1.0}] * 10
                + [{"__label": "failure", "has:x": 1.0}] * 10)
        node = mining.build_decision_tree(rows, features=["has:x"])
        assert node.decision == "leaf"

    def test_depth_limit(self):
        rows = ([{"__label": "success", "has:a": 1.0, "has:b": 1.0}] * 4
                + [{"__label": "failure", "has:a": 0.0, "has:b": 1.0}] * 2
                + [{"__label": "failure", "has:a": 1.0, "has:b": 0.0}] * 2)
        node = mining.build_decision_tree(rows, features=["has:a", "has:b"],
                                          max_depth=1)
        assert node.decision != "leaf"
        assert node.left.decision == "leaf"
        assert node.right.decision == "leaf"

    def test_min_samples_respected(self):
        rows = [{"__label": "success", "has:x": 1.0},
                {"__label": "failure", "has:x": 0.0}]
        node = mining.build_decision_tree(rows, features=["has:x"],
                                          min_samples=100)
        assert node.decision == "leaf"

    def test_majority_tie_break_is_deterministic(self):
        rows = [{"__label": "success"}, {"__label": "failure"}]
        node = mining.build_decision_tree(rows, features=[])
        assert node.label in ("success", "failure")
        assert node.label == mining.build_decision_tree(
            list(reversed(rows)), features=[]).label

    def test_as_dict_shape(self):
        node = mining.build_decision_tree(
            [{"__label": "success", "has:x": 1.0}], features=["has:x"])
        data = node.as_dict()
        assert data["decision"] == "leaf" and "label" in data


class TestExtractBranches:
    def test_discriminating_step_detected(self):
        """成功常带 `review`、失败几乎不带 → 单特征分化"""
        succ = [["read", "review"]] * 12 + [["read"]] * 8
        fail = [["read"]] * 10
        branches = mining.extract_branches(succ, fail, ["read"])
        texts = [b.condition for b in branches]
        assert any("`review`" in t and "出现" in t for t in texts)

    def test_missing_step_direction(self):
        succ = [["read"]] * 10
        fail = [["read", "skip_verify"]] * 10
        branches = mining.extract_branches(succ, fail, ["read"])
        assert any("缺失" in b.condition for b in branches)

    def test_no_branches_without_failures(self):
        branches = mining.extract_branches([["a", "b"]] * 10, [], ["a", "b"])
        assert branches == []

    def test_no_branches_when_undifferentiated(self):
        succ = [["a", "b"]] * 10
        fail = [["a", "b"]] * 10
        assert mining.extract_branches(succ, fail, ["a", "b"]) == []

    def test_support_and_total_reported(self):
        succ = [["a", "x"]] * 10
        fail = [["a"]] * 10
        branches = mining.extract_branches(succ, fail, ["a"])
        assert branches and branches[0].total == 20
        assert 0 < branches[0].support_ratio <= 1

    def test_deterministic_ordering(self):
        succ = [["a", "x", "y"]] * 10
        fail = [["a"]] * 10
        first = mining.extract_branches(succ, fail, ["a"])
        second = mining.extract_branches(list(succ), list(fail), ["a"])
        assert [b.condition for b in first] == [b.condition for b in second]

    def test_step_position_located(self):
        succ = [["a", "b", "x"]] * 10
        fail = [["a", "b"]] * 10
        branches = mining.extract_branches(succ, fail, ["a", "b"])
        located = [b for b in branches if "`b`" in b.condition or "`x`" in b.condition]
        assert located
        assert all(b.at_step == -1 or b.at_step >= 1 for b in located)

    def test_condition_text_is_consistent_with_split_direction(self):
        """`步数 >= N` 与 `步数 < N` 必须与真实分裂方向一致（不得互相矛盾）"""
        succ = [["a", "b", "c"]] * 10
        fail = [["a"]] * 10
        branches = mining.extract_branches(succ, fail, ["a", "b", "c"])
        texts = [b.condition for b in branches]
        if any("步数 <" in t for t in texts) and any("步数 >=" in t for t in texts):
            less = int([t for t in texts if "步数 <" in t][0].split("<")[1].split()[0])
            ge = int([t for t in texts if "步数 >=" in t][0].split(">=")[1].split()[0])
            assert less <= ge


# ════════════════════════════════════════════════════════════
#  4. 骨架 → PatternStep / 副作用画像
# ════════════════════════════════════════════════════════════


class TestBackboneToSteps:
    def test_maps_labels_capabilities_and_conditions(self):
        backbone = [("read", 1.0, 10), ("write", 0.8, 8)]
        steps = mining.backbone_to_steps(
            backbone, capability_of={"read": "cp.builtin.read_file"},
            condition_of={"write": "步数 >= 2"}, optional=["write"])
        assert steps[0].capability_id == "cp.builtin.read_file"
        assert steps[1].condition == "步数 >= 2"
        assert steps[1].optional is True
        assert [s.seq for s in steps] == [1, 2]

    def test_empty_backbone(self):
        assert mining.backbone_to_steps([]) == []


class TestSideEffectProfile:
    def test_shapes_values_not_raw_paths(self):
        profile = mining.side_effect_profile([
            _traj("t1", ["a"], files=["C:/repo/a.py"]),
            _traj("t2", ["a"], files=["/other/b.py"])])
        assert profile["files_written_shape"] == ["${path}"]
        assert profile["files_written_count"] == 2
        assert "C:/repo/a.py" not in str(profile)

    def test_destructive_and_external_flags(self):
        profile = mining.side_effect_profile([
            _traj("t1", ["a"], deleted=["/x/y"], external=["https://api.example.com"])])
        assert profile["destructive"] is True
        assert profile["external_endpoint"] is True
        assert "删除" in profile["undo_hint"]

    def test_non_destructive_undo_hint(self):
        profile = mining.side_effect_profile([_traj("t1", ["a"], files=["/x/y"])])
        assert profile["destructive"] is False
        assert "写类副作用" in profile["undo_hint"]

    def test_empty(self):
        profile = mining.side_effect_profile([])
        assert profile["trajectory_count"] == 0
        assert profile["writes_per_trajectory_avg"] == 0.0

    def test_writes_per_trajectory_average(self):
        profile = mining.side_effect_profile([
            _traj("t1", ["a", "b"], files=["/x/1", "/x/2"]),
            _traj("t2", ["a"], files=["/x/3"])])
        assert profile["writes_per_trajectory_avg"] == 1.5


# ════════════════════════════════════════════════════════════
#  5. 门槛常量
# ════════════════════════════════════════════════════════════


class TestThresholds:
    def test_min_same_kind_is_20(self):
        from agent.digestion.models import MIN_SAME_KIND_TRACES
        assert MIN_SAME_KIND_TRACES == 20

    def test_backbone_min_support(self):
        assert MIN_STEP_SUPPORT == 0.6

    def test_tree_min_samples(self):
        assert TREE_MIN_SAMPLES >= 2
