"""TASK-S3-01 轨迹清洗 + 参数泛化单测

覆盖任务书 §步骤 2 的四条清洗规则与同类判定键定义（T3 要求）：
噪声轨迹清洗后模式可提取、失败轨迹保留为负样本、同任务不同路径的归一并集判定。
"""

from __future__ import annotations

import pytest

from agent.digestion import cleaning
from agent.digestion import generalize as gen
from agent.digestion.models import (
    NOISE_DUPLICATE_STEP,
    NOISE_EXPLORE_PREFIX,
    NOISE_NEGATIVE_SAMPLE,
    NOISE_RETRY_PREFIX,
    OUTCOME_FAILURE,
    OUTCOME_SUCCESS,
    SameTaskKey,
    TrajectoryStep,
)


def _step(label: str, *, seq: int = 1, params: dict | None = None,
          status: str = "success") -> TrajectoryStep:
    return TrajectoryStep(seq=seq, label=label, params=params or {},
                          status=status)


# ════════════════════════════════════════════════════════════
#  1. 标签语汇
# ════════════════════════════════════════════════════════════


class TestLabelVocabulary:
    @pytest.mark.parametrize("label", [
        "list_dir", "explore_repo", "probe_env", "排查启动失败", "scan", "grep",
    ])
    def test_explore_labels(self, label):
        assert cleaning.is_explore_label(label) is True

    @pytest.mark.parametrize("label", ["read_file", "write_file", "shell_execute"])
    def test_non_explore_labels(self, label):
        assert cleaning.is_explore_label(label) is False

    @pytest.mark.parametrize("label", ["retry", "retry_tool", "backoff_wait", "重试"])
    def test_retry_labels(self, label):
        assert cleaning.is_retry_label(label) is True

    def test_empty_label_is_neither(self):
        assert cleaning.is_explore_label("") is False
        assert cleaning.is_retry_label("") is False


# ════════════════════════════════════════════════════════════
#  2. 同类轨迹判定键：intent 归一
# ════════════════════════════════════════════════════════════


class TestIntentNormalization:
    def test_windows_and_posix_paths_normalize_equal(self):
        """同任务不同路径 → 同一 intent_key（任务书单测要求）"""
        a = cleaning.normalize_intent(r"请帮我修复 C:\repo\a.py 里的失败测试")
        b = cleaning.normalize_intent("修复 /repo/b.py 中失败的测试，请")
        assert a == b != ""

    def test_value_forms_are_erased(self):
        """UUID / 时间戳 / 数字被抹掉 ⇒ 换实例仍同键"""
        a = cleaning.normalize_intent(
            "review commit 3f2504e0-4f89-11d3-9a0c-0305e82c3301 at 2026-09-10")
        b = cleaning.normalize_intent(
            "review commit 11111111-2222-3333-4444-555555555555 at 2027-01-02")
        assert a == b

    def test_word_order_is_irrelevant(self):
        assert cleaning.normalize_intent("fix failing test") == \
            cleaning.normalize_intent("test failing fix")

    def test_empty_and_none(self):
        assert cleaning.normalize_intent("") == ""
        assert cleaning.normalize_intent(None) == ""

    def test_stopwords_removed(self):
        key = cleaning.normalize_intent("please help me to fix the failing test")
        assert "the" not in key.split("+")
        assert "please" not in key.split("+")
        assert "failing" in key.split("+")

    def test_deterministic(self):
        text = "修复 模块 A 的失败测试"
        assert cleaning.normalize_intent(text) == cleaning.normalize_intent(text)


class TestArgShapeSignature:
    def test_same_keys_different_values_same_shape(self):
        a = cleaning.arg_shape_signature({"path": "C:/x.py"})
        b = cleaning.arg_shape_signature({"path": "/y/z.py"})
        assert a == b

    def test_different_keys_differ(self):
        assert cleaning.arg_shape_signature({"path": "x"}) != \
            cleaning.arg_shape_signature({"cmd": "x"})

    def test_nested_keys_included_one_level(self):
        sig = cleaning.arg_shape_signature({"opts": {"a": 1, "b": 2}})
        assert "opts(a,b)" in sig

    @pytest.mark.parametrize("value,expected", [
        (None, "none"), ([], "list"), ("text", "str"), (7, "int"),
    ])
    def test_non_dict_shapes(self, value, expected):
        assert cleaning.arg_shape_signature(value) == expected

    def test_empty_dict(self):
        assert cleaning.arg_shape_signature({}) == "empty"


class TestOutcomeClassification:
    @pytest.mark.parametrize("status,expected", [
        ("success", OUTCOME_SUCCESS), ("error", OUTCOME_FAILURE),
        ("blocked", OUTCOME_FAILURE), ("", OUTCOME_FAILURE),
        (None, OUTCOME_FAILURE),
    ])
    def test_classify(self, status, expected):
        assert cleaning.classify_outcome(status) == expected


class TestSameTaskKey:
    def test_triple_definition_and_no_task_id(self):
        key = SameTaskKey("cp.builtin.read_file", "shape:path", OUTCOME_SUCCESS)
        assert key.as_tuple() == ("cp.builtin.read_file", "shape:path", "success")
        assert key.as_str() == "cp.builtin.read_file|shape:path|success"
        assert "task" not in key.as_str()

    def test_outcome_splits_success_and_failure(self):
        ok = SameTaskKey("c", "i", OUTCOME_SUCCESS)
        ng = SameTaskKey("c", "i", OUTCOME_FAILURE)
        assert ok.as_str() != ng.as_str()
        assert ng.is_negative is True and ok.is_negative is False

    def test_same_path_task_merges_same_key(self):
        """同任务不同执行路径（步骤顺序/多寡不同）不改变判定键 ⇒ 可归并"""

        class _T:
            def __init__(self, args, status="success"):
                self.request = type("R", (), {"args_redacted": args})()
                self.response = type("S", (), {"status": status})()
                self.side_effects = type("SE", (), {"notes": []})()
                self.capability_id = "cp.builtin.read_file"

        a = cleaning.same_task_key(_T({"path": "C:/a.py"}))
        b = cleaning.same_task_key(_T({"path": "/repo/b.py"}))
        assert a == b

    def test_explicit_intent_overrides_shape(self):
        class _T:
            request = type("R", (), {"args_redacted": {"path": "x"}})()
            response = type("S", (), {"status": "success"})()
            side_effects = type("SE", (), {"notes": []})()
            capability_id = "c"

        key = cleaning.same_task_key(_T(), intent="修复 失败 测试")
        assert key.intent_key == cleaning.normalize_intent("修复 失败 测试")

    def test_notes_intent_channel(self):
        class _T:
            request = type("R", (), {"args_redacted": {"path": "x"}})()
            response = type("S", (), {"status": "success"})()
            side_effects = type("SE", (), {"notes": ["intent:deploy service"]})()
            capability_id = "c"

        assert cleaning.intent_key_for_trace(_T()) == \
            cleaning.normalize_intent("deploy service")

    def test_unknown_intent_is_honest(self):
        class _T:
            request = type("R", (), {"args_redacted": None})()
            response = type("S", (), {"status": "success"})()
            side_effects = type("SE", (), {"notes": []})()
            capability_id = "c"

        assert cleaning.intent_key_for_trace(_T()) == "none"


# ════════════════════════════════════════════════════════════
#  3. 清洗规则
# ════════════════════════════════════════════════════════════


class TestStripNoisePrefix:
    def test_explore_prefix_stripped(self):
        steps = [_step("list_dir"), _step("read_file"), _step("write_file")]
        out, flags = cleaning.strip_noise_prefix(steps)
        assert [s.label for s in out] == ["read_file", "write_file"]
        assert NOISE_EXPLORE_PREFIX in flags

    def test_retry_prefix_stripped(self):
        steps = [_step("retry", status="error"), _step("read_file"),
                 _step("write_file")]
        out, flags = cleaning.strip_noise_prefix(steps)
        assert [s.label for s in out] == ["read_file", "write_file"]
        assert NOISE_RETRY_PREFIX in flags

    def test_failed_prefix_stripped(self):
        steps = [_step("probe"), _step("read_file", status="error"),
                 _step("read_file"), _step("write_file")]
        out, flags = cleaning.strip_noise_prefix(steps)
        assert [s.label for s in out] == ["read_file", "write_file"]
        assert flags

    def test_interior_explore_step_kept(self):
        """只削前缀：中段的探索是真实步骤，不得误删"""
        steps = [_step("read_file"), _step("grep"), _step("write_file")]
        out, flags = cleaning.strip_noise_prefix(steps)
        assert [s.label for s in out] == ["read_file", "grep", "write_file"]
        assert flags == []

    def test_never_empties_trajectory(self):
        """守【不易】：削完太短则退回原序列（宁冗余不误删）"""
        steps = [_step("list_dir"), _step("probe")]
        out, flags = cleaning.strip_noise_prefix(steps, min_keep=2)
        assert len(out) == 2
        assert flags  # 判定仍然如实上报

    def test_no_prefix_no_flags(self):
        steps = [_step("read_file"), _step("write_file")]
        out, flags = cleaning.strip_noise_prefix(steps)
        assert len(out) == 2 and flags == []


class TestMergeDuplicateSteps:
    def test_consecutive_same_label_and_params_merged(self):
        steps = [_step("read_file", params={"path": "a"}),
                 _step("read_file", params={"path": "a"}),
                 _step("write_file")]
        out, merged = cleaning.merge_duplicate_steps(steps)
        assert [s.label for s in out] == ["read_file", "write_file"]
        assert merged == 1
        assert out[0].repeat == 2

    def test_same_label_different_params_not_merged(self):
        steps = [_step("read_file", params={"path": "a"}),
                 _step("read_file", params={"path": "b"})]
        out, merged = cleaning.merge_duplicate_steps(steps)
        assert len(out) == 2 and merged == 0

    def test_non_consecutive_not_merged(self):
        steps = [_step("read_file"), _step("write_file"), _step("read_file")]
        out, merged = cleaning.merge_duplicate_steps(steps)
        assert len(out) == 3 and merged == 0

    def test_seq_renumbered(self):
        steps = [_step("a"), _step("b"), _step("c")]
        out, _ = cleaning.merge_duplicate_steps(steps)
        assert [s.seq for s in out] == [1, 2, 3]

    def test_empty_input(self):
        assert cleaning.merge_duplicate_steps([]) == ([], 0)


class TestCleanTrajectory:
    def test_four_rules_in_sequence(self):
        steps = [
            _step("list_dir", status="error"),          # 探索 + 失败前缀
            _step("read_file", params={"path": "C:/a.py"}),
            _step("read_file", params={"path": "C:/a.py"}),   # 重复
            _step("write_file"),
        ]
        out, stats = cleaning.clean_trajectory(steps)
        assert [s.label for s in out] == ["read_file", "write_file"]
        assert stats["raw"] == 4
        assert stats["dropped"] == 2
        assert stats["merged"] == 1
        assert NOISE_DUPLICATE_STEP in stats["flags"]

    def test_stats_expose_before_after(self):
        steps = [_step("read_file"), _step("read_file")]
        out, stats = cleaning.clean_trajectory(steps, min_keep=1)
        assert stats["raw"] == 2 and stats["after_merge"] == 1

    def test_noise_trajectory_still_yields_pattern_steps(self):
        """噪声轨迹清洗后**模式可提取**（验收项）"""
        steps = [
            _step("probe", status="error"), _step("retry", status="error"),
            _step("read_file", params={"path": "x"}),
            _step("read_file", params={"path": "x"}),
            _step("shell_execute"), _step("write_file"),
        ]
        out, _ = cleaning.clean_trajectory(steps)
        assert len(out) >= 2
        assert [s.label for s in out] == ["read_file", "shell_execute",
                                         "write_file"]


class TestNegativeSamples:
    def test_mark_negative_sets_flag(self):
        from agent.digestion.models import Trajectory

        traj = Trajectory(trajectory_id="t1", task_id="t1",
                          key=SameTaskKey("c", "i", OUTCOME_FAILURE),
                          is_negative=True)
        cleaning.mark_negative(traj)
        cleaning.mark_negative(traj)          # 幂等
        assert traj.noise_flags.count(NOISE_NEGATIVE_SAMPLE) == 1

    def test_negative_trajectory_is_retained_not_dropped(self):
        """失败轨迹**保留**并标注（负样本，供 S5 评测与 negative_intent）"""
        from agent.digestion.models import Trajectory, TraceSet

        traj = Trajectory(trajectory_id="t1", task_id="t1",
                          key=SameTaskKey("c", "i", OUTCOME_FAILURE),
                          steps=[_step("read_file", status="error")],
                          is_negative=True)
        cleaning.mark_negative(traj)
        bucket = TraceSet(key=traj.key, trajectories=[traj])
        assert bucket.size == 1
        assert bucket.negative_count == 1


# ════════════════════════════════════════════════════════════
#  4. 分组与去重
# ════════════════════════════════════════════════════════════


class TestGroupingAndDedup:
    def _traj(self, tid, key, steps=1):
        from agent.digestion.models import Trajectory
        return Trajectory(trajectory_id=tid, task_id=tid, key=key,
                          steps=[_step(f"s{i}") for i in range(steps)])

    def test_group_by_key(self):
        k1 = SameTaskKey("c", "i", OUTCOME_SUCCESS)
        k2 = SameTaskKey("c", "i", OUTCOME_FAILURE)
        buckets = cleaning.group_by_same_task([
            self._traj("a", k1), self._traj("b", k1), self._traj("c", k2)])
        assert len(buckets) == 2
        assert buckets[k1.as_str()].size == 2
        assert buckets[k2.as_str()].negative_count == 1

    def test_group_order_is_deterministic(self):
        k = SameTaskKey("c", "i", OUTCOME_SUCCESS)
        a = cleaning.group_by_same_task([self._traj("b", k), self._traj("a", k)])
        b = cleaning.group_by_same_task([self._traj("a", k), self._traj("b", k)])
        assert [t.trajectory_id for t in a[k.as_str()].trajectories] == \
            [t.trajectory_id for t in b[k.as_str()].trajectories]

    def test_dedupe_by_task_keeps_richest(self):
        k = SameTaskKey("c", "i", OUTCOME_SUCCESS)
        out = cleaning.dedupe_by_task([self._traj("t1", k, 1),
                                       self._traj("t1", k, 3)])
        assert len(out) == 1 and out[0].step_count == 3


# ════════════════════════════════════════════════════════════
#  5. 参数泛化
# ════════════════════════════════════════════════════════════


class TestShapePlaceholder:
    @pytest.mark.parametrize("value,expected", [
        ("C:/repo/a.py", gen.PH_PATH),
        (r"C:\repo\a.py", gen.PH_PATH),
        ("/repo/a.py", gen.PH_PATH),
        ("src/x.py", gen.PH_PATH),
        ("2026-09-10", gen.PH_TIMESTAMP),
        ("2026-09-10T12:30:00", gen.PH_TIMESTAMP),
        ("3f2504e0-4f89-11d3-9a0c-0305e82c3301", gen.PH_UUID),
        ("https://example.com/x", gen.PH_URL),
        ("a@b.com", gen.PH_EMAIL),
        ("a3f1c9d2e4b5a6f7a8b9c0d1e2f3a4b5", gen.PH_HEX_ID),
        (42, gen.PH_NUMBER),
        ("hello", None),
        (True, None),
        (None, None),
    ])
    def test_shapes(self, value, expected):
        assert gen.shape_placeholder(value) == expected

    def test_existing_placeholder_is_stable(self):
        assert gen.shape_placeholder("${path}") == gen.PH_PATH
        assert gen.is_placeholder("${path}") is True

    def test_is_placeholder_rejects_plain(self):
        assert gen.is_placeholder("path") is False
        assert gen.is_placeholder(3) is False

    def test_normalize_param_value_recurses(self):
        out = gen.normalize_param_value(
            {"path": "C:/a.py", "items": ["/b/c.py"], "n": 3})
        assert out["path"] == gen.PH_PATH
        assert out["items"] == [gen.PH_PATH]
        assert out["n"] == gen.PH_NUMBER

    def test_generalize_step_params_non_dict(self):
        assert gen.generalize_step_params(None) == {}
        assert gen.generalize_step_params("x")["value"] == "x"

    def test_slot_placeholder_sanitizes(self):
        assert gen.slot_placeholder("cmd-line") == "${cmd_line}"
        assert gen.slot_placeholder("") == "${arg}"


class TestParameterSlots:
    def test_varying_values_become_slot(self):
        slots = gen.infer_parameter_slots([
            [("read", {"path": "a"})], [("read", {"path": "b"})]])
        assert len(slots) == 1
        assert slots[0].placeholder == "${path}"
        assert slots[0].distinct_values == 2

    def test_constant_value_is_not_a_slot(self):
        """跨轨迹恒定值 ⇒ 字面量，不制造伪参数"""
        slots = gen.infer_parameter_slots([
            [("read", {"mode": "utf-8"})], [("read", {"mode": "utf-8"})]])
        assert slots == []

    def test_no_position_suffix_when_key_unique(self):
        slots = gen.infer_parameter_slots([
            [("a", {"x": "1"}), ("b", {"y": "1"})],
            [("a", {"x": "2"}), ("b", {"y": "2"})]])
        assert sorted(s.placeholder for s in slots) == ["${x}", "${y}"]

    def test_position_suffix_when_key_repeats(self):
        """同名键出现在多个位次 ⇒ 首位 `${键名}`、其后 `${键名_2}`（按出现次序）"""
        slots = gen.infer_parameter_slots([
            [("a", {"x": "1"}), ("b", {"x": "1"})],
            [("a", {"x": "2"}), ("b", {"x": "3"})]])
        assert {s.placeholder for s in slots} == {"${x}", "${x_2}"}
        assert {s.name for s in slots} == {"x", "x_2"}
        # 排序键为 (步骤标签, 槽名) ⇒ 首位槽归属首个出现位次
        assert [(s.step_label, s.placeholder) for s in slots] == [
            ("a", "${x}"), ("b", "${x_2}")]

    def test_examples_capped(self):
        slots = gen.infer_parameter_slots(
            [[("a", {"x": str(i)})] for i in range(10)], max_examples=2)
        assert len(slots[0].examples) == 2

    def test_plain_dict_steps_supported(self):
        slots = gen.infer_parameter_slots([[{"path": "a"}], [{"path": "b"}]])
        assert slots[0].name == "path"
        assert slots[0].placeholder == "${path}"

    def test_apply_slots_replaces(self):
        slots = gen.infer_parameter_slots([
            [("read", {"path": "a"})], [("read", {"path": "b"})]])
        out, changed = gen.apply_slots({"path": "a"}, slots)
        assert out["path"] == "${path}" and changed is True

    def test_apply_slots_keeps_unrelated_keys(self):
        slots = gen.infer_parameter_slots([
            [("read", {"path": "a"})], [("read", {"path": "b"})]])
        out, _ = gen.apply_slots({"mode": "strict"}, slots)
        assert out["mode"] == "strict"

    def test_slot_index_lookup_both_ways(self):
        slots = gen.infer_parameter_slots([
            [("read", {"path": "a"})], [("read", {"path": "b"})]])
        index = gen.slot_index(slots)
        assert index["path"] is index["${path}"]
