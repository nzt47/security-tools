"""TASK-S8-05 步骤 2/3（D2 适用性、D3 归组键）单测

验收对应（任务书 §四）：

- 【D2】适用性字段可由 ``upstream`` 步骤集合推导；多能力链**不再**用于单能力等价判定；
- 【D2】抽检取样按适用性过滤：对 ``cp.builtin.read_file`` 重新采样，采出用例均为
  单能力形状；
- 【D3】归组键含结构维度；给出改进前后**同键冲突率**对比；S7-05 的 7 条不再与
  无关任务同键。

## 口径变更声明（S3-01 → S8-05）

``intent_key`` 由纯文本键改为 **v2 结构键**（``v2|cap=…|steps:…‖<文本归一>``），
``SameTaskKey`` 另增 ``step_count_bucket`` / ``capability_set`` 两维。理由：CJK 按字
切分使 60 条不同的任务链归一到同一键（实测），"同类轨迹"判定近乎失效。**文本维度
未删**（`cleaning.text_key_of()` 可取回），两条既有保证（顺序无关/取值无关）不变。
"""

from __future__ import annotations

import pytest

from agent.digestion import cases as C
from agent.digestion import cleaning as cl
from agent.digestion.models import (
    OUTCOME_FAILURE,
    OUTCOME_SUCCESS,
    SameTaskKey,
    same_capability_set,
    same_task_shape,
)

CAP = "cp.builtin.read_file"
SHELL = "cp.builtin.shell_execute"
WRITE = "cp.builtin.write_file"


# ════════════════════════════════════════════════════════════
#  D3：结构维度
# ════════════════════════════════════════════════════════════


class TestStepCountBucket:
    @pytest.mark.parametrize("count,expected", [
        (0, "0"), (1, "1"), (2, "2"), (3, "3-5"), (5, "3-5"),
        (6, "6-10"), (10, "6-10"), (11, "11+"), (99, "11+"),
    ])
    def test_buckets(self, count, expected):
        assert cl.step_count_bucket(count) == expected

    @pytest.mark.parametrize("value", [None, "x", -1, object()])
    def test_invalid_is_unknown_not_guessed(self, value):
        assert cl.step_count_bucket(value) == cl.STEP_BUCKET_UNKNOWN


class TestCapabilitySetKey:
    def test_order_irrelevant_and_deduped(self):
        assert cl.capability_set_key(["b", "a", "b", " a "]) == "a+b"

    def test_empty_is_none(self):
        assert cl.capability_set_key([]) == "none"
        assert cl.capability_set_key(["", "   "]) == "none"
        assert cl.capability_set_key(["  ", "\t"]) == "none"

    def test_steps_capability_set_prefers_capability_id(self):
        steps = [{"capability_id": CAP, "label": "read_file"},
                 {"label": "shell_execute"}]
        assert cl.steps_capability_set(steps) == f"{CAP}+shell_execute"

    def test_steps_capability_set_drops_explore_prefix(self):
        """**前导**探索步是噪声，不进任务形状（中后段不削）"""
        steps = [{"capability_id": "cp.builtin.list_dir"},
                 {"capability_id": CAP}, {"capability_id": WRITE}]
        assert cl.steps_capability_set(steps) == f"{CAP}+{WRITE}"
        assert cl.task_step_count(steps) == 2

    def test_steps_capability_set_keeps_midchain_explore(self):
        """中段的探索步是**真实步骤**，不得削（与 `strip_noise_prefix` 同纪律）"""
        steps = [{"capability_id": CAP},
                 {"capability_id": "cp.builtin.list_dir"},
                 {"capability_id": WRITE}]
        # 集合按字典序（`list_dir` < `read_file`）
        assert cl.steps_capability_set(steps) == \
            f"cp.builtin.list_dir+{CAP}+{WRITE}"
        assert cl.task_step_count(steps) == 3

    def test_does_not_mutate_input(self):
        steps = [{"capability_id": CAP}, {"capability_id": WRITE}]
        cl.steps_capability_set(steps)
        assert steps == [{"capability_id": CAP}, {"capability_id": WRITE}]


class TestStructuralKey:
    def test_atom_format(self):
        assert cl.structural_atom(capability_set="a+b", step_count=3) == \
            "v2|cap=a+b|steps:3-5"

    def test_structural_key_keeps_text_dimension(self):
        text = cl.normalize_intent("fix failing test")
        key = cl.structural_intent_key(text, capability_set=CAP, step_count=1)
        assert key.startswith("v2|cap=")
        assert cl.text_key_of(key) == text
        assert cl.is_structural_key(key) is True

    def test_text_is_not_re_normalized(self):
        """形态指纹（``shape:path``）是**键**不是自然语言 —— 二次归一会被拆坏"""
        key = cl.structural_intent_key("shape:encoding+path",
                                       capability_set=CAP, step_count=1)
        assert cl.text_key_of(key) == "shape:encoding+path"

    def test_text_key_of_returns_legacy_key_unchanged(self):
        assert cl.text_key_of("shape:path") == "shape:path"
        assert cl.is_structural_key("shape:path") is False

    def test_cross_shape_same_text_do_not_merge(self):
        """D3 的核心断言：同文本、不同形状 ⇒ **不同键**"""
        text = cl.normalize_intent("审查模块并生成报告")
        single = cl.structural_intent_key(text, capability_set=CAP, step_count=1)
        chain = cl.structural_intent_key(text,
                                         capability_set=f"{CAP}+{SHELL}+{WRITE}",
                                         step_count=3)
        assert single != chain
        assert cl.text_key_of(single) == cl.text_key_of(chain)

    def test_outcome_not_inside_intent_key(self):
        """outcome 是 SameTaskKey 的独立第三元，不进文本键（避免两处各记一次）"""
        text = cl.normalize_intent("deploy service")
        ok = cl.structural_intent_key(text, capability_set=CAP, step_count=1)
        ng = cl.structural_intent_key(text, capability_set=CAP, step_count=1)
        assert ok == ng
        assert SameTaskKey("c", ok, OUTCOME_SUCCESS).as_str() != \
            SameTaskKey("c", ng, OUTCOME_FAILURE).as_str()


class TestRestructureTaskKey:
    def test_adds_structural_dimensions(self):
        key = SameTaskKey(CAP, cl.normalize_intent("读并写"), OUTCOME_SUCCESS)
        out = cl.restructure_task_key(key, [{"capability_id": CAP},
                                            {"capability_id": WRITE}])
        assert out.capability_set == f"{CAP}+{WRITE}"
        assert out.step_count_bucket == "2"
        assert cl.text_key_of(out.intent_key) == cl.text_key_of(key.intent_key)
        assert out.outcome == OUTCOME_SUCCESS

    def test_failure_and_success_share_shape_when_task_identical(self):
        """失败任务的前导失败步会被清洗削掉，**形状不变**（同档位 3-5）

        这正是"失败集配对 → 分支提取"能工作的前提：形状取自**清洗前削过前缀**的
        任务步骤，而不是清洗后的残留（后者会少一步 ⇒ 成败分属不同键 ⇒ 负样本 0）。
        """
        success_steps = [{"capability_id": CAP, "status": "success"},
                         {"capability_id": SHELL, "status": "success"},
                         {"capability_id": WRITE, "status": "success"}]
        failure_steps = [{"capability_id": CAP, "status": "success"},
                         {"capability_id": SHELL, "status": "success"},
                         {"capability_id": WRITE, "status": "error"}]
        base = SameTaskKey(CAP, cl.normalize_intent("链"), OUTCOME_SUCCESS)
        a = cl.restructure_task_key(base, success_steps)
        b = cl.restructure_task_key(
            SameTaskKey(CAP, base.intent_key, OUTCOME_FAILURE), failure_steps)
        assert a.step_count_bucket == b.step_count_bucket == "3-5"
        assert a.capability_set == b.capability_set
        assert same_task_shape(a, b) is True

    def test_no_shape_claim_when_nothing_given(self):
        """既无 steps 也无显式维度 ⇒ 如实标注"未提供"，不臆造"""
        out = cl.restructure_task_key(SameTaskKey(CAP, "shape:path",
                                                  OUTCOME_SUCCESS))
        assert out.step_count_bucket == ""
        assert out.capability_set == ""
        assert out.intent_key.startswith("v2|cap=none|steps:unknown‖")

    def test_rejects_nothing_and_keeps_capability(self):
        key = SameTaskKey(CAP, "shape:path", OUTCOME_SUCCESS)
        out = cl.restructure_task_key(key, [{"capability_id": WRITE}])
        assert out.capability_id == CAP


class TestShapeHelpers:
    def test_same_capability_set_subset(self):
        assert same_capability_set("a+b", "a+b") is True
        assert same_capability_set("a+b+c", "a+b") is True
        assert same_capability_set("a+b", "a+b+c") is True
        assert same_capability_set("a+b", "c+d") is False
        assert same_capability_set("", "a") is False

    def test_same_task_shape_ignores_outcome(self):
        text = cl.normalize_intent("链")
        ok = SameTaskKey(CAP, text, OUTCOME_SUCCESS, "3-5", f"{CAP}+{SHELL}+{WRITE}")
        ng = SameTaskKey(CAP, text, OUTCOME_FAILURE, "3-5", f"{CAP}+{SHELL}")
        assert same_task_shape(ok, ng) is True
        assert same_task_shape(ok, SameTaskKey(CAP, text, OUTCOME_FAILURE,
                                               "1", CAP)) is False

    def test_same_task_shape_compares_text_not_whole_v2_key(self):
        """整键比较会退化成"形状全等"，与"变体"语义自相矛盾 —— 必须比文本维度"""
        text = cl.normalize_intent("x")
        a = SameTaskKey(CAP, cl.structural_intent_key(text, capability_set="a+b",
                                                      step_count=3),
                        OUTCOME_SUCCESS, "3-5", "a+b")
        b = SameTaskKey(CAP, cl.structural_intent_key(text, capability_set="a",
                                                      step_count=3),
                        OUTCOME_FAILURE, "3-5", "a")
        assert a.intent_key != b.intent_key      # 整键确实不同（结构原子各异）
        assert same_task_shape(a, b) is True     # 但形状仍是同一任务的变体

    def test_record_shape_reports_unknown_instead_of_guessing(self):
        assert cl.record_shape() == "cap=unknown|steps:unknown"
        assert cl.record_shape(capability_set="a", step_count=3) == \
            "cap=a|steps:3-5"


class TestConflictRate:
    """同键冲突率：改进前后的**同一函数、同一批记录**对比（D3 的量化口径）"""

    def _records(self):
        text = cl.normalize_intent("审查模块并生成报告")
        rows = [{"v1": text, "capability_set": CAP, "step_count_bucket": "1"},
                {"v1": text, "capability_set": f"{CAP}+{SHELL}+{WRITE}",
                 "step_count_bucket": "3-5"}]
        for row in rows:
            row["v2"] = cl.structural_intent_key(
                text, capability_set=row["capability_set"],
                step_count=1 if row["step_count_bucket"] == "1" else 3)
        return rows

    def test_legacy_text_key_collides(self):
        report = cl.grouping_conflict_rate(self._records(),
                                           key_fn=lambda r: r["v1"])
        assert report["keys"] == 1
        assert report["conflict_items"] == 2
        assert report["ratio"] == 1.0

    def test_structural_key_separates_shapes(self):
        report = cl.grouping_conflict_rate(self._records(),
                                           key_fn=lambda r: r["v2"])
        assert report["keys"] == 2
        assert report["conflict_items"] == 0
        assert report["ratio"] == 0.0

    def test_homogeneous_group_is_not_a_conflict(self):
        rows = [{"k": "same", "capability_set": CAP, "step_count_bucket": "1"}
                for _ in range(5)]
        report = cl.grouping_conflict_rate(rows, key_fn="k")
        assert report["conflict_items"] == 0 and report["keys"] == 1

    def test_empty_input_is_safe(self):
        report = cl.grouping_conflict_rate([], key_fn="k")
        assert report["total"] == 0 and report["ratio"] == 0.0

    def test_shape_defaults_to_unknown_when_absent(self):
        """缺结构字段 ⇒ 如实标 unknown，不臆造步数/集合"""
        rows = [{"k": "same"}, {"k": "same"}]
        report = cl.grouping_conflict_rate(rows, key_fn="k")
        assert report["conflict_items"] == 0


class TestDeclarationGap:
    def test_multi_capability_declared_as_single_is_a_gap(self):
        """声明 `read_file`，实际是三步链 ⇒ 缺口（虽然声明值确在集合内）"""
        rows = [{"declared": CAP, "capability_set": f"{CAP}+{SHELL}+{WRITE}"}]
        report = cl.capability_declaration_gap(rows)
        assert report["gap_items"] == 1
        assert report["multi_capability_items"] == 1
        assert report["undeclared_items"] == 0      # 声明值本身在集合内
        assert report["ratio"] == 1.0
        assert report["samples"][0]["declared"] == CAP

    def test_declared_outside_own_set_counts_as_undeclared(self):
        rows = [{"declared": WRITE, "capability_set": CAP}]
        report = cl.capability_declaration_gap(rows)
        assert report["undeclared_items"] == 1 and report["gap_items"] == 1

    def test_declared_field_or_capability_id_accepted(self):
        """两种记录形态都要能读：``declared``（报告用）与 ``capability_id``（模型用）"""
        via_declared = cl.capability_declaration_gap(
            [{"declared": CAP, "capability_set": f"{CAP}+{SHELL}"}])
        via_model = cl.capability_declaration_gap(
            [{"capability_id": CAP, "capability_set": f"{CAP}+{SHELL}"}])
        assert via_declared["gap_items"] == via_model["gap_items"] == 1

    def test_single_capability_is_not_a_gap(self):
        rows = [{"declared": CAP, "capability_set": CAP}]
        assert cl.capability_declaration_gap(rows)["gap_items"] == 0

    def test_normalizer_suppresses_alias_only_differences(self):
        """工具名 vs canonical 是**别名问题**，不是结构问题（避免混报）"""
        rows = [{"declared": CAP, "capability_set": "read_file"}]
        assert cl.capability_declaration_gap(rows)["undeclared_items"] == 1
        assert cl.capability_declaration_gap(
            rows, normalize=C.normalize_capability_id)["undeclared_items"] == 0


# ════════════════════════════════════════════════════════════
#  D2：形状适用性
# ════════════════════════════════════════════════════════════


def _case(case_id: str, steps, **kwargs) -> C.EquivalenceCase:
    return C.EquivalenceCase(case_id=case_id, capability_id=CAP, upstream=steps,
                             expected_status="success", **kwargs)


def _single(case_id: str = "s-1", *, capability_id: str = CAP,
            schema: bool = True) -> C.EquivalenceCase:
    return _case(case_id,
                 [C.ProgramStep(label="read_file", capability_id=capability_id,
                                params={"path": "C:/sandbox/a.txt"})],
                 expected_output_schema={"text": "str"} if schema else {})


def _chain(case_id: str = "c-1") -> C.EquivalenceCase:
    return _case(case_id, [
        C.ProgramStep(label="read_file", capability_id=CAP),
        C.ProgramStep(label="shell_execute", capability_id=SHELL),
        C.ProgramStep(label="write_file", capability_id=WRITE),
    ])


class TestCaseShape:
    def test_upstream_capabilities_derived_from_steps(self):
        assert _chain().upstream_capabilities == sorted([CAP, SHELL, WRITE])

    def test_falls_back_to_label_when_capability_id_missing(self):
        case = _case("x", [C.ProgramStep(label="read_file")])
        assert case.upstream_capabilities == ["read_file"]

    def test_shape_key_and_bucket(self):
        assert _chain().shape_key == f"cap={CAP}+{SHELL}+{WRITE}|steps:3-5"

    def test_multi_capability_flag(self):
        assert _chain().multi_capability is True
        assert _single().multi_capability is False

    @pytest.mark.parametrize("schema,output,weak", [
        ({}, {}, True),
        ({"a": "str"}, {}, False),
        ({}, {"a": 1}, False),
    ])
    def test_weak_contract(self, schema, output, weak):
        case = _case("w", [C.ProgramStep(label="read_file", capability_id=CAP)],
                     expected_output_schema=schema, expected_output=output)
        assert case.weak_contract is weak


class TestAppliesToCapability:
    def test_single_capability_case_applies(self):
        applies, reason = _single().applies_to_capability(CAP)
        assert applies is True and "形状匹配" in reason

    def test_multi_capability_chain_does_not_apply(self):
        """D2 的核心规则：多能力链**不得**用于单能力等价判定"""
        applies, reason = _chain().applies_to_capability(CAP)
        assert applies is False
        assert "多能力链" in reason and "链路级" in reason

    def test_different_capability_does_not_apply(self):
        applies, reason = _single(capability_id=WRITE).applies_to_capability(CAP)
        assert applies is False and "不一致" in reason

    def test_alias_names_are_aligned(self):
        """工具名 `read_file` 与 canonical `cp.builtin.read_file` 是同一形状"""
        case = _case("alias", [C.ProgramStep(label="read_file",
                                             capability_id="read_file")])
        applies, _ = case.applies_to_capability(CAP)
        assert applies is True

    def test_same_capability_repeated_steps_still_applies(self):
        """判据是"不同能力的个数"而非步数：同能力重复调用形状未变"""
        case = _case("retry", [C.ProgramStep(label="read_file", capability_id=CAP),
                               C.ProgramStep(label="read_file",
                                             capability_id=CAP)])
        applies, _ = case.applies_to_capability(CAP)
        assert applies is True

    def test_empty_capability_is_not_applicable(self):
        applies, reason = _single().applies_to_capability("")
        assert applies is False and "未给出被评能力" in reason

    def test_caseless_capability_is_not_applicable(self):
        case = _case("nocap", [C.ProgramStep(label="")])
        applies, reason = case.applies_to_capability(CAP)
        assert applies is False and "无能力标识" in reason

    def test_weak_contract_does_not_change_applicability(self):
        """契约缺失与"问对了问题没有"正交 —— 两者都必须可见"""
        weak = _single("weak", schema=False)
        assert weak.applies_to_capability(CAP)[0] is True
        notes = weak.shape_notes(CAP)
        assert any("weak_contract" in n for n in notes)

    def test_shape_notes_report_mismatch(self):
        notes = _chain().shape_notes(CAP)
        assert notes and notes[0].startswith("不适用")


class TestShapeFiltering:
    def test_sampling_only_takes_matching_shapes(self):
        """验收口径：对 cp.builtin.read_file 重新采样，采出用例**均为单能力形状**"""
        cases = [_single("s-1"), _single("s-2"), _chain("c-1"), _chain("c-2"),
                 _single("s-3", capability_id=WRITE)]
        kept, excluded = C.shape_applicable_cases(cases, CAP)
        assert [c.case_id for c in kept] == ["s-1", "s-2"]
        assert all(not c.multi_capability for c in kept)
        assert {row["case_id"] for row in excluded} == {"c-1", "c-2", "s-3"}

    def test_exclusions_carry_reason_and_shape(self):
        kept, excluded = C.shape_applicable_cases([_chain("c-1")], CAP)
        assert kept == []
        row = excluded[0]
        assert row["reason"] and row["shape"] and row["weak_contract"] is True
        assert row["evaluated_capability"] == CAP

    def test_shape_report_counts(self):
        cases = [_single("s-1"), _chain("c-1"), _chain("c-2")]
        report = C.shape_report(cases, CAP)
        assert report["total"] == 3
        assert report["multi_capability"] == 2
        assert report["applicable"] == 1 and report["excluded"] == 2
        assert report["weak_contract"] == 2      # 两条链无 expected_output*

    def test_shape_report_without_capability_skips_filtering(self):
        report = C.shape_report([_single(), _chain()])
        assert report["applicable"] == 2 and report["excluded"] == 0

    def test_empty_input(self):
        assert C.shape_applicable_cases([], CAP) == ([], [])
        assert C.shape_report([], CAP)["total"] == 0


class TestCapabilityNormalization:
    def test_alias_resolves_to_canonical(self):
        assert C.normalize_capability_id("read_file") == CAP

    def test_canonical_is_stable(self):
        assert C.normalize_capability_id(CAP) == CAP

    def test_empty_and_unknown(self):
        assert C.normalize_capability_id("") == ""
        # 台账无此名 ⇒ 返回**派生候选**（`bridge` 的口径：不冒充已登记，但可 join）
        derived = C.normalize_capability_id("totally.unknown.thing")
        assert derived == "cp.builtin.totally.unknown.thing"
        assert C.normalize_capability_id(derived) == derived     # 幂等

    def test_cache_reset_is_safe(self):
        C.normalize_capability_id("read_file")
        C.reset_capability_registry_cache()
        assert C.normalize_capability_id("read_file") == CAP


# ════════════════════════════════════════════════════════════
#  回归：轨迹生成路径的结构维度（D3 在管线上的落地）
# ════════════════════════════════════════════════════════════


class _FakeResponse:
    def __init__(self, status: str = "success"):
        self.status = status


class _FakeRequest:
    def __init__(self, args):
        self.args_redacted = args


class _FakeSideEffects:
    notes: list = []


class _Row:
    """最小 Trace 行替身（避免测试依赖完整台账）"""

    def __init__(self, capability_id: str, *, status: str = "success"):
        self.capability_id = capability_id
        self.trace_id = f"trace-{capability_id}"
        self.request = _FakeRequest({"path": "C:/x"})
        self.response = _FakeResponse(status)
        self.side_effects = _FakeSideEffects()
        self.timing = type("T", (), {"duration_ms": 1.0, "started_at": 0.0})()


class TestTrajectoryStructuralIntegration:
    def test_trajectory_carries_structural_dimensions(self):
        rows = [_Row(CAP), _Row(SHELL), _Row(WRITE)]
        key = cl.same_task_key(rows[0], capability_id=CAP)
        traj = cl.trajectory_from_rows(rows, key=key, source_trace_id="t1",
                                       task_id="task-1")
        assert traj.key.step_count_bucket == "3-5"
        assert traj.key.capability_set == f"{CAP}+{SHELL}+{WRITE}"
        assert cl.is_structural_key(traj.key.intent_key) is True

    def test_noise_prefix_does_not_change_shape(self):
        """失败任务的前导失败步被削掉，**形状不变** ⇒ 成败仍可配对"""
        ok_rows = [_Row(CAP), _Row(SHELL), _Row(WRITE)]
        bad_rows = [_Row(CAP), _Row(SHELL), _Row(WRITE, status="error")]
        ok = cl.trajectory_from_rows(
            ok_rows, key=cl.same_task_key(ok_rows[0], capability_id=CAP),
            source_trace_id="t1", task_id="task-1")
        bad = cl.trajectory_from_rows(
            bad_rows,
            key=cl.same_task_key(bad_rows[0], capability_id=CAP),
            source_trace_id="t2", task_id="task-2")
        assert ok.key.capability_set == bad.key.capability_set
        assert ok.key.step_count_bucket == bad.key.step_count_bucket

    def test_grouping_separates_shapes(self):
        single_rows = [_Row(CAP)]
        chain_rows = [_Row(CAP), _Row(SHELL), _Row(WRITE)]
        trajs = [
            cl.trajectory_from_rows(single_rows,
                                    key=cl.same_task_key(single_rows[0],
                                                         capability_id=CAP),
                                    source_trace_id="t1", task_id="task-1"),
            cl.trajectory_from_rows(chain_rows,
                                    key=cl.same_task_key(chain_rows[0],
                                                         capability_id=CAP),
                                    source_trace_id="t2", task_id="task-2"),
        ]
        buckets = cl.group_by_same_task(trajs)
        assert len(buckets) == 2

    def test_restructure_can_be_disabled(self):
        """``restructure_key=False`` ⇒ 结构维度保持调用方给定值（**不臆造**）

        注意：轨道键（`same_task_key`）本身仍会给出结构维度 —— 被关闭的是
        `trajectory_from_rows` 的**重建**步骤，故此处显式给一个未带结构维度的键。
        """
        rows = [_Row(CAP)]
        bare = SameTaskKey(CAP, "shape:path", OUTCOME_SUCCESS)
        traj = cl.trajectory_from_rows(rows, key=bare, source_trace_id="t1",
                                       task_id="task-1", restructure_key=False)
        assert traj.key.step_count_bucket == ""
        assert traj.key.capability_set == ""
        assert traj.key.as_str() == f"{CAP}|shape:path|success|steps:|caps:"
