"""TASK-S7-06 R4：委派成功率机械信号清单 + 两列口径单测

覆盖：
- 机械信号清单 **5 条显式定义**（优先级、适用条件、通过条件、数据源）与**词表一致性**；
- 四条机械信号的判定语义（含"证据缺失 ⇒ **不适用**而非通过"）；
- 复评半边「机械优先 + LLM 兜底」：结果带 ``signal_kind``（mechanical / llm）；
- `agent.eval` 指标 **两列不混算**（列值独立、跨列合并的比率不产出）；
- 样本 < 20 只披露不考核（S5-02 口径）；
- 副作用信号**复用 S3-02 比对能力**（形态容忍），且不把"不适用"当"通过"。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.digestion import shadow as SH
from agent.eval import metrics as M
from agent.subagent import mechanical as ME
from agent.subagent.collection import (
    CloudPivotReview,
    Reflection,
    TriadCollector,
    rule_reviewer,
)

PLACEHOLDER = "${path}"


# ════════════════════════════════════════════════════════════
#  清单（显式定义）
# ════════════════════════════════════════════════════════════


class TestSignalCatalog:
    def test_five_signals_in_priority_order(self):
        assert ME.SIGNAL_PRIORITY == (
            ME.SIGNAL_ARTIFACT_STRUCTURE, ME.SIGNAL_TEST_TRANSITION,
            ME.SIGNAL_SIDE_EFFECTS, ME.SIGNAL_REPLAYABILITY, ME.SIGNAL_LLM_REVIEW)
        assert len(ME.SIGNAL_SPECS) == 5
        assert [s.order for s in ME.SIGNAL_SPECS] == [1, 2, 3, 4, 5]

    def test_mechanical_first_llm_last(self):
        assert ME.MECHANICAL_SIGNALS == ME.SIGNAL_PRIORITY[:4]
        assert ME.LLM_SIGNALS == (ME.SIGNAL_LLM_REVIEW,)

    def test_every_spec_is_traceable(self):
        for spec in ME.SIGNAL_SPECS:
            assert spec.name and spec.definition and spec.source
            assert spec.applies_when and spec.passed_when
            assert spec.kind in (ME.KIND_MECHANICAL, ME.KIND_LLM)

    def test_catalog_is_shared_with_metric_dictionary(self):
        """指标字典里的清单与复评执行的清单**同源**（不复制第二份）"""
        from_dict = M.delegation_signal_dictionary()
        assert [s["signal"] for s in from_dict] == list(ME.SIGNAL_PRIORITY)

    def test_judge_kind_vocabulary_matches_s3_02(self):
        """``judge_kind`` 与 S3-02 `shadow.JUDGE_KIND_*` 同词表（M1 口径）"""
        assert ME.JUDGE_KIND_LLM == SH.JUDGE_KIND_LLM
        assert ME.JUDGE_KIND_LOCAL == SH.JUDGE_KIND_LOCAL
        assert ME.JUDGE_KIND_INJECTED == SH.JUDGE_KIND_INJECTED
        assert ME.JUDGE_KIND_LLM_FALLBACK == SH.JUDGE_KIND_LLM_FALLBACK

    def test_kind_and_priority_helpers(self):
        assert ME.kind_of(ME.SIGNAL_ARTIFACT_STRUCTURE) == ME.KIND_MECHANICAL
        assert ME.kind_of(ME.SIGNAL_LLM_REVIEW) == ME.KIND_LLM
        assert ME.kind_of("unknown_signal") == ME.KIND_LLM      # 未知 ⇒ 兜底列
        assert ME.priority_of(ME.SIGNAL_ARTIFACT_STRUCTURE) == 0
        assert ME.priority_of("unknown_signal") == len(ME.SIGNAL_PRIORITY)


# ════════════════════════════════════════════════════════════
#  ① 产物结构
# ════════════════════════════════════════════════════════════


class TestArtifactStructureSignal:
    def test_required_fields_pass(self):
        result = ME.artifact_structure_signal(
            artifacts=[{"summary": "s", "files": ["a"]}],
            artifact_format={"required_fields": ["summary", "files"]})
        assert result.applicable is True and result.passed is True
        assert result.kind == ME.KIND_MECHANICAL
        assert result.verdict == "pass"

    def test_missing_field_fails(self):
        result = ME.artifact_structure_signal(
            artifacts=[{"summary": "s"}],
            artifact_format={"required_fields": ["summary", "files"]})
        assert result.verdict == "fail"
        assert "缺" in result.reasons[1]

    def test_empty_artifacts_fail_when_format_declared(self):
        result = ME.artifact_structure_signal(
            artifacts=[], artifact_format={"required_fields": ["summary"]})
        assert result.applicable is True and result.passed is False
        assert "产物条目为空" in result.reasons[0]

    def test_type_schema_is_enforced(self):
        ok = ME.artifact_structure_signal(
            artifacts=[{"n": 3}], artifact_format={"schema": {"n": "int"}})
        assert ok.passed is True
        bad = ME.artifact_structure_signal(
            artifacts=[{"n": "3"}], artifact_format={"schema": {"n": "int"}})
        assert bad.passed is False

    def test_bool_is_not_int(self):
        result = ME.artifact_structure_signal(
            artifacts=[{"n": True}], artifact_format={"schema": {"n": "int"}})
        assert result.passed is False
        assert "bool" in result.reasons[1]

    def test_natural_language_format_is_not_applicable(self):
        """自然语言声明不可机验 ⇒ **不适用**（不猜关键词，也不当通过）"""
        result = ME.artifact_structure_signal(
            artifacts=[{"summary": "s"}], artifact_format="写一份 markdown 报告")
        assert result.applicable is False
        assert result.verdict == "not_applicable"

    def test_json_string_format_is_parsed(self):
        result = ME.artifact_structure_signal(
            artifacts=[{"a": 1}], artifact_format='{"required_fields": ["a"]}')
        assert result.applicable is True and result.passed is True


# ════════════════════════════════════════════════════════════
#  ② 测试 fail→pass
# ════════════════════════════════════════════════════════════


class TestTestTransitionSignal:
    def test_fail_to_pass(self):
        result = ME.test_transition_signal(
            test_evidence={"before": "fail", "after": "pass"})
        assert result.verdict == "pass"
        assert result.evidence["strict_fail_to_pass"] is True

    def test_still_failing_is_fail(self):
        result = ME.test_transition_signal(
            test_evidence={"before": "fail", "after": "fail"})
        assert result.verdict == "fail"

    def test_pass_to_pass_is_degraded_but_mechanical(self):
        result = ME.test_transition_signal(
            test_evidence={"before": "pass", "after": "pass"})
        assert result.verdict == "pass"
        assert result.evidence["strict_fail_to_pass"] is False
        assert "强度降级" in result.reasons[0]

    def test_id_sets(self):
        ok = ME.test_transition_signal(
            test_evidence={"failing_ids": ["t1", "t2"],
                           "passed_ids": ["t1", "t2", "t3"]})
        assert ok.verdict == "pass"
        bad = ME.test_transition_signal(
            test_evidence={"failing_ids": ["t1", "t2"], "passed_ids": ["t1"]})
        assert bad.verdict == "fail"

    def test_no_failing_ids_is_not_applicable(self):
        result = ME.test_transition_signal(
            test_evidence={"failing_ids": [], "passed_ids": ["t1"]})
        assert result.verdict == "not_applicable"

    def test_missing_evidence_is_not_applicable(self):
        assert ME.test_transition_signal().verdict == "not_applicable"
        assert ME.test_transition_signal(
            test_evidence={"foo": 1}).verdict == "not_applicable"


# ════════════════════════════════════════════════════════════
#  ③ 副作用核对（复用 S3-02）
# ════════════════════════════════════════════════════════════


class TestSideEffectsSignal:
    def test_placeholder_tolerance(self):
        """复用 S3-02 `_sets_match`：`${path}` 形态可匹配任意同形态具体路径"""
        result = ME.side_effects_signal(
            declared={"files_written": [PLACEHOLDER]},
            actual={"files_written": ["C:/sandbox/out.txt"]})
        assert result.verdict == "pass"
        assert result.evidence["contract_check"] == "applicable"

    def test_concrete_mismatch_fails(self):
        result = ME.side_effects_signal(
            declared={"files_written": ["C:/a.txt"]},
            actual={"files_written": ["C:/b.txt"]})
        assert result.verdict == "fail"

    def test_extra_actual_side_effect_fails(self):
        result = ME.side_effects_signal(
            declared={"files_written": ["C:/a.txt"]},
            actual={"files_written": ["C:/a.txt"], "external_calls": ["http://x"]})
        assert result.verdict == "fail"

    def test_arm_checks_are_marked_not_applicable(self):
        """无第二臂 ⇒ 臂比对/内容指纹**不适用**（不当作通过），如实标注"""
        result = ME.side_effects_signal(
            declared={"files_written": ["C:/a.txt"]},
            actual={"files_written": ["C:/a.txt"]})
        assert result.evidence["arms_check"] == "not_applicable"
        assert result.evidence["content_check"] == "not_applicable"
        assert "不当作独立证据" in result.evidence["arms_note"]

    def test_missing_sets_is_not_applicable(self):
        assert ME.side_effects_signal(
            declared={"files_written": []}).verdict == "not_applicable"
        assert ME.side_effects_signal(
            declared={"files_written": []},
            actual=None).verdict == "not_applicable"

    def test_non_mapping_sets_are_not_applicable(self):
        """裸列表**不猜类别** ⇒ 不适用"""
        assert ME.side_effects_signal(
            declared=["a.txt"], actual=["a.txt"]).verdict == "not_applicable"

    def test_adopts_s3_02_layer_result(self):
        """S3-02 双跑比对结果可直接采信（不重算、不改判）"""
        good = ME.side_effects_signal(
            side_effect_diff={"layer": "side_effects", "passed": True,
                              "reasons": ["一致"]})
        assert good.verdict == "pass" and good.evidence["arms_check"] == "applicable"
        bad = ME.side_effects_signal(
            side_effect_diff={"layer": "side_effects", "passed": False,
                              "reasons": ["不一致"]})
        assert bad.verdict == "fail"
        assert ME.side_effects_signal(
            side_effect_diff={"no_passed": 1}).verdict == "not_applicable"


# ════════════════════════════════════════════════════════════
#  ④ 可回放性
# ════════════════════════════════════════════════════════════


class TestReplayabilitySignal:
    def test_identical_fingerprints_pass(self):
        result = ME.replayability_signal(
            replay_evidence={"fingerprints": ["ab12", "ab12"]})
        assert result.verdict == "pass" and result.evidence["runs"] == 2

    def test_divergent_fingerprints_fail(self):
        assert ME.replayability_signal(
            replay_evidence={"fingerprints": ["a", "b"]}).verdict == "fail"

    def test_single_run_is_not_applicable(self):
        """单次重放不构成证据 ⇒ **不适用**（非通过、也非失败）"""
        assert ME.replayability_signal(
            replay_evidence={"fingerprints": ["a"]}).verdict == "not_applicable"
        assert ME.replayability_signal(
            replay_evidence={"runs": 1, "identical": True}).verdict == "not_applicable"

    def test_runs_identical_shape(self):
        assert ME.replayability_signal(
            replay_evidence={"runs": 3, "identical": True}).verdict == "pass"
        assert ME.replayability_signal(
            replay_evidence={"runs": 3, "identical": False}).verdict == "fail"

    def test_bad_shape_is_not_applicable(self):
        assert ME.replayability_signal(
            replay_evidence={"whatever": 1}).verdict == "not_applicable"


# ════════════════════════════════════════════════════════════
#  机械优先（取首个适用）
# ════════════════════════════════════════════════════════════


class TestMechanicalFirst:
    def test_all_absent_returns_none(self):
        assert ME.evaluate_mechanical() is None

    def test_lower_priority_not_used_when_higher_applies(self):
        chosen = ME.evaluate_mechanical(
            artifacts=[{"a": 1}], artifact_format={"required_fields": ["a"]},
            test_evidence={"before": "fail", "after": "pass"})
        assert chosen is not None
        assert chosen.signal == ME.SIGNAL_ARTIFACT_STRUCTURE

    def test_falls_through_when_higher_not_applicable(self):
        chosen = ME.evaluate_mechanical(
            artifact_format="自然语言",
            test_evidence={"before": "fail", "after": "pass"})
        assert chosen is not None and chosen.signal == ME.SIGNAL_TEST_TRANSITION

    def test_inapplicable_is_not_a_pass(self):
        """关键纪律：证据缺失时**不得**返回 pass（那是把"没验"说成"验过"）"""
        chosen = ME.evaluate_mechanical(artifact_format={"required_fields": ["a"]},
                                        artifacts=[])
        assert chosen is not None and chosen.passed is False
        assert chosen.signal == ME.SIGNAL_ARTIFACT_STRUCTURE

    def test_evidence_helper_lists_all_four(self):
        evidence = ME.mechanical_evidence(
            artifact_format={"required_fields": ["a"]}, artifacts=[{"a": 1}])
        assert evidence["mechanical_available"] is True
        assert evidence["chosen"] == ME.SIGNAL_ARTIFACT_STRUCTURE
        assert [s["verdict"] for s in evidence["signals"]] == [
            "pass", "not_applicable", "not_applicable", "not_applicable"]
        assert len(evidence["catalog"]) == 5


# ════════════════════════════════════════════════════════════
#  复评接入（机械优先 + LLM 兜底）
# ════════════════════════════════════════════════════════════


def _outcome(**overrides):
    base = dict(delegation_id="d1", artifacts=(), payload={}, output_text="产出文本",
                duration_ms=5.0, trace=None, trace_id="", tool_calls=())
    base.update(overrides)
    return SimpleNamespace(**base)


class TestReflectionWiring:
    def test_mechanical_wins_over_reviewer(self):
        """机械信号可用时**不**调用复评器（LLM 判定不得覆盖机械结论）"""
        calls = []

        def _reviewer(**kwargs):
            calls.append(kwargs)
            return CloudPivotReview(verdict="fail", score=0.0, passed=False,
                                    reviewer="spy")

        collector = TriadCollector(reviewer=_reviewer)
        triad = collector.collect_from_outcome(
            _outcome(artifacts=({"summary": "s"},)),
            context=SimpleNamespace(artifact_format={"required_fields": ["summary"]}))
        assert calls == []
        review = triad.reflection.cloudpivot_review
        assert review.signal_kind == ME.KIND_MECHANICAL
        assert review.signal == ME.SIGNAL_ARTIFACT_STRUCTURE
        assert review.judge_kind == ME.JUDGE_KIND_MECHANICAL
        assert review.passed is True
        assert review.reviewer == "mechanical_signal:artifact_structure"

    def test_mechanical_failure_is_reported_as_failure(self):
        collector = TriadCollector()
        triad = collector.collect_from_outcome(
            _outcome(artifacts=({"other": 1},)),
            context=SimpleNamespace(artifact_format={"required_fields": ["summary"]}))
        review = triad.reflection.cloudpivot_review
        assert review.signal_kind == ME.KIND_MECHANICAL
        assert review.passed is False and review.verdict == "fail"
        assert review.suggestions                    # 给出可执行的下一步

    def test_llm_fallback_when_mechanical_not_applicable(self):
        collector = TriadCollector()
        triad = collector.collect_from_outcome(
            _outcome(), context=SimpleNamespace(artifact_format="写一份 markdown"))
        review = triad.reflection.cloudpivot_review
        assert review.signal_kind == ME.KIND_LLM
        assert review.signal == ME.SIGNAL_LLM_REVIEW
        assert review.judge_kind == ME.JUDGE_KIND_LOCAL
        # 试算过程可审计："为什么落到兜底"逐条可见
        trial = review.evidence["mechanical"]
        assert trial["chosen"] == ""
        assert all(not s["applicable"] for s in trial["signals"][:4])

    def test_payload_declared_evidence_is_used(self):
        """载荷显式键即证据（子代理上报的测试/副作用/重放）"""
        test_triad = TriadCollector().collect_from_outcome(_outcome(payload={
            "test_evidence": {"before": "fail", "after": "pass"}}))
        assert test_triad.reflection.signal == ME.SIGNAL_TEST_TRANSITION
        side_triad = TriadCollector().collect_from_outcome(_outcome(payload={
            "declared_side_effects": {"files_written": [PLACEHOLDER]},
            "side_effects": {"files_written": ["C:/out.txt"]}}))
        assert side_triad.reflection.signal == ME.SIGNAL_SIDE_EFFECTS
        assert side_triad.reflection.cloudpivot_review.passed is True
        replay_triad = TriadCollector().collect_from_outcome(_outcome(payload={
            "replay_evidence": {"fingerprints": ["a", "a"]}}))
        assert replay_triad.reflection.signal == ME.SIGNAL_REPLAYABILITY

    def test_explicit_signal_evidence_overrides_payload(self):
        collector = TriadCollector()
        triad = collector.collect_from_outcome(
            _outcome(payload={"test_evidence": {"before": "fail", "after": "pass"}}),
            signal_evidence={"replay_evidence": {"fingerprints": ["x", "x"]},
                             "test_evidence": None})
        assert triad.reflection.signal == ME.SIGNAL_REPLAYABILITY

    def test_unlabeled_reviewer_defaults_to_llm_column(self):
        collector = TriadCollector(reviewer=lambda **kw: CloudPivotReview(
            verdict="pass", score=1.0, passed=True, reviewer="custom"))
        triad = collector.collect_from_outcome(_outcome())
        assert triad.reflection.signal_kind == ME.KIND_LLM

    def test_reflection_to_dict_carries_signal_kind(self):
        triad = TriadCollector().collect_from_outcome(
            _outcome(payload={"test_evidence": {"before": "fail", "after": "pass"}}))
        payload = triad.reflection.to_dict()
        assert payload["signal_kind"] == ME.KIND_MECHANICAL
        assert payload["signal"] == ME.SIGNAL_TEST_TRANSITION
        assert payload["cloudpivot_review"]["signal_kind"] == ME.KIND_MECHANICAL

    def test_missing_reflection_has_no_signal_kind(self):
        """缺复评 ≠ 机械判定（空串），指标侧据此进 unlabeled 列"""
        assert Reflection().signal_kind == ""

    def test_rule_reviewer_is_labeled_local(self):
        review = rule_reviewer(task_id="t", input_text="做一件事", output="ok")
        assert review.signal_kind == ME.KIND_LLM
        assert review.judge_kind == ME.JUDGE_KIND_LOCAL

    def test_cloud_review_to_dict_defaults_to_llm_column(self):
        bare = CloudPivotReview(verdict="pass", passed=True)
        payload = bare.to_dict()
        assert payload["signal_kind"] == ME.KIND_LLM
        assert payload["signal"] == ME.SIGNAL_LLM_REVIEW
        assert bare.is_mechanical is False


# ════════════════════════════════════════════════════════════
#  指标两列（严禁混算）
# ════════════════════════════════════════════════════════════


def _rows():
    rows = []
    for index in range(4):                      # mechanical：4/4 齐全、4/4 判定通过
        rows.append({"delegation_id": f"m{index}", "capability_id": f"cm{index}",
                     "artifact": True, "trace": True, "reflection": True,
                     "signal_kind": "mechanical", "review_passed": True})
    for index in range(2):                      # llm：0/2 齐全、0/2 判定通过
        rows.append({"delegation_id": f"l{index}", "capability_id": f"cl{index}",
                     "artifact": True, "trace": False, "reflection": True,
                     "signal_kind": "llm", "review_passed": False})
    return rows


class TestTwoColumnDiscipline:
    def test_columns_are_computed_independently(self):
        row = M.compute_delegation_recovery(_rows())
        mech = row["columns"]["mechanical"]
        llm = row["columns"]["llm"]
        assert mech["samples"] == 4 and mech["recovery_rate"] == 1.0
        assert llm["samples"] == 2 and llm["recovery_rate"] == 0.0

    def test_top_level_blended_value_is_not_produced(self):
        """**不混算**：跨判定主体时顶层 rate 必须为 None（合并率不产出）"""
        row = M.compute_delegation_recovery(_rows())
        assert row["mixed"] is True
        assert row["value"] is None
        assert row["numerator"] is None and row["denominator"] is None
        assert row["status"] == M.STATUS_MIXED
        assert row["target_met"] is None
        assert row["value"] != pytest.approx(4 / 6)
        assert "不产出合并回收率" in row["reason"]

    def test_success_rate_top_level_uses_single_column(self):
        row = M.compute_delegation_success_rate(_rows())
        assert row["value_basis"] == M.SIGNAL_KIND_MECHANICAL
        assert row["value"] == 1.0
        assert row["columns"]["llm"]["success_rate"] == 0.0
        assert row["mixed"] is True
        assert row["target_met"] is None            # 披露不考核

    def test_unlabeled_column_is_separate(self):
        rows = _rows() + [{"delegation_id": "u1", "capability_id": "cu",
                           "artifact": True, "trace": True, "reflection": True}]
        row = M.compute_delegation_recovery(rows)
        assert row["columns"]["unlabeled"]["samples"] == 1
        # 未标注行不进任一列
        assert row["columns"]["mechanical"]["samples"] == 4
        assert row["columns"]["llm"]["samples"] == 2
        assert row["mixed"] is True

    def test_legacy_rows_keep_previous_semantics(self):
        """未标 signal_kind 的行走既有路径（S5-02/S6-01 断言不回归）"""
        legacy = [{"delegation_id": "d1", "capability_id": "c1", "artifact": True,
                   "trace": "t", "reflection": {"score": 1}},
                  {"delegation_id": "d2", "capability_id": "c2", "artifact": True,
                   "trace": "t", "reflection": None}]
        row = M.compute_delegation_recovery(legacy)
        assert row["value"] == 0.5
        assert row["numerator"] == 1 and row["denominator"] == 2
        assert row["target_met"] is False
        assert row["mixed"] is False
        assert row["columns"]["unlabeled"]["samples"] == 2

    def test_single_kind_is_not_mixed(self):
        rows = [r for r in _rows() if r["signal_kind"] == "mechanical"]
        row = M.compute_delegation_recovery(rows)
        assert row["mixed"] is False
        assert row["value"] == 1.0

    def test_small_sample_only_disclosed(self):
        """样本 < 20 ⇒ 只披露不考核（两列各自判定）"""
        row = M.compute_delegation_recovery(_rows())
        for kind in ("mechanical", "llm", "unlabeled"):
            column = row["columns"][kind]
            if column["samples"]:
                assert column["status"] == M.STATUS_INSUFFICIENT
                assert "只披露不考核" in column["assessment"]
        success = M.compute_delegation_success_rate(_rows())
        assert success["status"] == M.STATUS_INSUFFICIENT
        assert success["min_samples"] == M.MIN_SAMPLES_FOR_ASSESSMENT == 20

    def test_no_source_keeps_framework_only(self):
        row = M.compute_delegation_recovery(None)
        assert row["value"] is None and row["status"] == M.STATUS_FRAMEWORK
        assert row["contract"]["optional"] == ["signal_kind", "review_passed"]
        assert row["contract"]["signal_kinds"] == list(M.SIGNAL_KIND_COLUMNS)
        assert [s["signal"] for s in row["signals"]] == list(ME.SIGNAL_PRIORITY)
        success = M.compute_delegation_success_rate(None)
        assert success["status"] == M.STATUS_FRAMEWORK

    def test_nested_reflection_signal_kind_is_read(self):
        """三件套原样行（signal_kind 在 reflection 里）也要正确分列"""
        rows = [{"delegation_id": "n1", "capability_id": "cn", "artifact": True,
                 "trace": True,
                 "reflection": {"signal_kind": "mechanical",
                                "cloudpivot_review": {"passed": True}}}]
        assert M.delegation_signal_kind(rows[0]) == M.SIGNAL_KIND_MECHANICAL
        assert M.delegation_review_passed(rows[0]) is True
        row = M.compute_delegation_success_rate(rows)
        assert row["value_basis"] == M.SIGNAL_KIND_MECHANICAL and row["value"] == 1.0

    def test_weekly_markdown_renders_both_columns(self):
        rows = _rows()
        report = {"window": {"start": "2026-09-06", "end": "2026-09-12", "days": 7},
                  "generated_at": "2026-09-12T10:00:00+0800",
                  "events": {"directory": "x", "rows": 0},
                  "metrics": {"delegation_recovery": M.compute_delegation_recovery(rows),
                              "delegation_success_rate":
                                  M.compute_delegation_success_rate(rows)}}
        markdown = M.render_weekly_markdown(report)
        assert "两列，严禁混算" in markdown
        assert "| mechanical | 4 | 1.0 |" in markdown
        assert "| llm | 2 | 0.0 |" in markdown
        assert "`artifact_structure`" in markdown

    def test_compute_metrics_includes_success_rate(self, tmp_path, monkeypatch):
        from agent.observability import events as ev
        monkeypatch.setenv(ev.ENV_DIR, str(tmp_path / "events"))
        ev.reset_event_stores()
        report = M.compute_metrics(days=1, events_dir=str(tmp_path / "events"),
                                   delegations=_rows())
        assert "delegation_success_rate" in report["metrics"]
        assert report["delegation_signals"][0]["signal"] == ME.SIGNAL_ARTIFACT_STRUCTURE
        ev.reset_event_stores()
