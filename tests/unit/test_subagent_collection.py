"""TASK-S4-04 §3.9 回收三件套 单元测试

覆盖：
- 三件套（产物 + 轨迹 + 反思）**缺任一 → 不齐全**
- 反思两半：上游自评 + 云枢复评；缺任一 → 反思不完整 → 三件套不齐全
- 轨迹附加缺陷：``actor != sub_agent`` / ``parent_trace_id`` 为空
- **缺任一 → 不计成本核算、视为浪费**（counted / wasted 两栏分离，浪费可见）
- **缺任一 → 阻塞 stage 推进**（``StageGate`` 抛 ``StageBlocked`` 并点名缺哪一件）
"""

from __future__ import annotations

import pytest

from agent.subagent.collection import (
    E_STAGE_BLOCKED,
    E_WASTED_DELEGATION,
    REFLECTION_CLOUD,
    REFLECTION_UPSTREAM,
    TRIAD_ARTIFACTS,
    TRIAD_PARTS,
    TRIAD_REFLECTION,
    TRIAD_TRACE,
    CloudPivotReview,
    CollectedTriad,
    CostLedger,
    Reflection,
    StageBlocked,
    StageGate,
    TriadCollector,
    UpstreamSelfEval,
    blocks_stage,
    build_trace_lookup,
    build_upstream_self_eval,
    collect,
    reflection_engine_reviewer,
    rule_reviewer,
)

TRACE_ROW = {
    "trace_id": "tr-1",
    "parent_trace_id": "tr-parent",
    "actor": "sub_agent",
    "task_id": "task-1",
}
ARTIFACTS = [{"path": "out/steps.jsonl", "kind": "steps"}]
UPSTREAM = UpstreamSelfEval(verdict="pass", score=0.9, summary="已完成")
CLOUD = CloudPivotReview(verdict="pass", score=0.85, passed=True, reviewer="test")


def complete_triad(**overrides) -> CollectedTriad:
    kwargs = {
        "agent_id": "dlg-1",
        "artifacts": ARTIFACTS,
        "trace": dict(TRACE_ROW),
        "reflection": Reflection(upstream_self_eval=UPSTREAM, cloudpivot_review=CLOUD),
    }
    kwargs.update(overrides)
    return collect(**kwargs)


# ════════════════════════════════════════════════════════════
#  三件套清单与齐全判定
# ════════════════════════════════════════════════════════════


class TestTriadCatalogue:
    def test_three_parts_only(self):
        assert TRIAD_PARTS == (TRIAD_ARTIFACTS, TRIAD_TRACE, TRIAD_REFLECTION)

    def test_complete_triad(self):
        triad = complete_triad()
        assert triad.is_complete is True
        assert triad.missing_parts == ()
        assert triad.is_wasted is False

    def test_ids_recorded(self):
        assert complete_triad().delegation_id == "dlg-1"
        assert complete_triad().trace_id == "tr-1"


class TestMissingParts:
    def test_missing_artifacts(self):
        triad = complete_triad(artifacts=[])
        assert triad.missing_parts == (TRIAD_ARTIFACTS,)
        assert triad.is_complete is False

    def test_missing_artifacts_none(self):
        assert collect("d", artifacts=None).missing_parts[0] == TRIAD_ARTIFACTS

    def test_missing_trace(self):
        triad = complete_triad(trace=None)
        assert TRIAD_TRACE in triad.missing_parts
        assert triad.is_complete is False

    def test_trace_without_trace_id_is_missing(self):
        triad = complete_triad(trace={"parent_trace_id": "p", "actor": "sub_agent"})
        assert TRIAD_TRACE in triad.missing_parts

    def test_missing_reflection(self):
        triad = complete_triad(reflection=None)
        assert triad.missing_parts == (TRIAD_REFLECTION,)

    def test_all_missing_reports_three(self):
        triad = collect("d")
        assert triad.missing_parts == TRIAD_PARTS

    def test_missing_order_follows_catalogue(self):
        triad = collect("d", trace=None)
        assert triad.missing_parts == (TRIAD_ARTIFACTS, TRIAD_TRACE, TRIAD_REFLECTION)

    def test_missing_detail_lists_parts(self):
        detail = collect("d").missing_detail()
        assert detail["missing_parts"] == list(TRIAD_PARTS)


class TestReflectionHalves:
    def test_reflection_needs_both_halves(self):
        only_upstream = Reflection(upstream_self_eval=UPSTREAM)
        assert only_upstream.is_complete is False
        assert only_upstream.missing_halves == (REFLECTION_CLOUD,)

    def test_only_cloud_review_is_incomplete(self):
        only_cloud = Reflection(cloudpivot_review=CLOUD)
        assert only_cloud.missing_halves == (REFLECTION_UPSTREAM,)

    def test_incomplete_reflection_makes_triad_incomplete(self):
        triad = complete_triad(reflection=Reflection(upstream_self_eval=UPSTREAM))
        assert TRIAD_REFLECTION in triad.missing_parts
        assert triad.missing_detail()["reflection_missing_halves"] == [REFLECTION_CLOUD]

    def test_reflection_to_dict(self):
        payload = Reflection(upstream_self_eval=UPSTREAM, cloudpivot_review=CLOUD).to_dict()
        assert payload["complete"] is True
        assert payload[REFLECTION_UPSTREAM]["verdict"] == "pass"
        assert payload[REFLECTION_CLOUD]["reviewer"] == "test"


class TestTraceProblems:
    def test_wrong_actor_is_a_problem(self):
        triad = complete_triad(trace={**TRACE_ROW, "actor": "auto"})
        assert any("actor" in p for p in triad.trace_problems)
        assert triad.is_complete is False

    def test_empty_parent_trace_id_is_a_problem(self):
        triad = complete_triad(trace={**TRACE_ROW, "parent_trace_id": ""})
        assert any("parent_trace_id" in p for p in triad.trace_problems)

    def test_correct_trace_has_no_problems(self):
        assert complete_triad().trace_problems == ()

    def test_trace_problems_empty_when_trace_missing(self):
        assert complete_triad(trace=None).trace_problems == ()

    def test_problem_detail_included_in_missing_detail(self):
        triad = complete_triad(trace={**TRACE_ROW, "actor": "auto"})
        assert triad.missing_detail()["trace_problems"]


class TestTraceCoercion:
    def test_mapping_accepted(self):
        assert collect("d", trace=dict(TRACE_ROW)).trace_id == "tr-1"

    def test_object_with_to_dict_accepted(self):
        class Row:
            def to_dict(self):
                return dict(TRACE_ROW)

        assert collect("d", trace=Row()).trace_id == "tr-1"

    def test_unrecognised_object_becomes_missing(self):
        triad = collect("d", trace=object())
        assert TRIAD_TRACE in triad.missing_parts

    def test_trace_lookup_returns_none_for_unknown(self):
        class Facade:
            def chain(self, trace_id):
                return []

        assert build_trace_lookup(Facade())("nope") is None

    def test_trace_lookup_ignores_failing_facade(self):
        class Facade:
            def chain(self, trace_id):
                raise RuntimeError("db down")

        assert build_trace_lookup(Facade())("tr-1") is None

    def test_trace_lookup_finds_row(self):
        class Row:
            trace_id = "tr-1"

        class Facade:
            def chain(self, trace_id):
                return [Row()]

        assert build_trace_lookup(Facade())("tr-1") is not None

    def test_none_facade_returns_none(self):
        assert build_trace_lookup(None)("tr-1") is None


class TestArtifactCoercion:
    def test_mapping_items_preserved(self):
        assert collect("d", artifacts=ARTIFACTS).artifacts == (ARTIFACTS[0],)

    def test_string_items_wrapped(self):
        assert collect("d", artifacts=["a"]).artifacts == ({"value": "a"},)

    def test_blank_strings_dropped(self):
        assert collect("d", artifacts=["  "]).artifacts == ()


# ════════════════════════════════════════════════════════════
#  上游自评提取 / 云枢复评
# ════════════════════════════════════════════════════════════


class TestUpstreamSelfEval:
    def test_absent_returns_none(self):
        assert build_upstream_self_eval({}) is None
        assert build_upstream_self_eval(None) is None

    def test_structured_dict(self):
        ev = build_upstream_self_eval({"self_eval": {
            "verdict": "partial", "score": 0.5, "summary": "完成一半",
            "issues": ["缺少溯源"]}})
        assert ev.verdict == "partial"
        assert ev.score == 0.5
        assert ev.issues == ("缺少溯源",)

    def test_string_form(self):
        ev = build_upstream_self_eval({"reflection": "我认为已完成"})
        assert ev.verdict == "unstated"
        assert "已完成" in ev.summary

    def test_alternative_keys(self):
        for key in ("self_evaluation", "self_reflection", "upstream_reflection"):
            assert build_upstream_self_eval({key: {"verdict": "pass"}}) is not None

    def test_pass_verdict_without_score_infers_one(self):
        ev = build_upstream_self_eval({"self_eval": {"verdict": "pass"}})
        assert ev.score == 1.0

    def test_fail_verdict_without_score_infers_zero(self):
        ev = build_upstream_self_eval({"self_eval": {"verdict": "fail"}})
        assert ev.score == 0.0

    def test_empty_dict_value_treated_as_absent(self):
        assert build_upstream_self_eval({"self_eval": {}}) is None

    def test_to_dict_shape(self):
        payload = UPSTREAM.to_dict()
        assert payload["verdict"] == "pass"
        assert payload["issues"] == []


class TestCloudReview:
    def test_rule_reviewer_passes_good_output(self):
        review = rule_reviewer(task_id="t", input_text="做一个足够长的任务描述",
                               output="已完成全部 3 项，产物见 out/steps.jsonl")
        assert review.passed is True
        assert review.reviewer == "rule_reviewer"

    def test_rule_reviewer_flags_empty_output(self):
        review = rule_reviewer(task_id="t", input_text="x" * 100, output="")
        assert review.passed is False
        assert "产物为空" in review.issues

    def test_reflection_engine_reviewer_returns_review(self):
        review = reflection_engine_reviewer(
            task_id="t", input_text="做一个足够长的任务描述",
            output="已完成全部 3 项，产物见 out/steps.jsonl")
        assert isinstance(review, CloudPivotReview)
        assert review.reviewer in ("reflection_engine", "rule_reviewer")

    def test_reviewer_marks_low_score_fail(self):
        review = rule_reviewer(task_id="t", input_text="x" * 100, output="")
        assert review.verdict in ("fail", "partial")


# ════════════════════════════════════════════════════════════
#  收集器
# ════════════════════════════════════════════════════════════


class Outcome:
    """执行结果的鸭子类型替身"""

    def __init__(self, **kwargs):
        self.delegation_id = "dlg-1"
        self.artifacts = tuple(ARTIFACTS)
        self.payload = {"artifacts": list(ARTIFACTS),
                        "self_eval": {"verdict": "pass", "score": 0.9}}
        self.trace = dict(TRACE_ROW)
        self.trace_id = "tr-1"
        self.output_text = "已完成全部 3 项，产物见 out/steps.jsonl"
        self.duration_ms = 120.0
        self.tool_calls = ()
        for key, value in kwargs.items():
            setattr(self, key, value)


class TestTriadCollector:
    def test_collects_complete_triad(self):
        triad = TriadCollector().collect_from_outcome(Outcome())
        assert triad.is_complete is True
        assert TRIAD_ARTIFACTS not in triad.missing_parts

    def test_reflection_built_from_payload_self_eval(self):
        triad = TriadCollector().collect_from_outcome(Outcome())
        assert triad.reflection.upstream_self_eval.verdict == "pass"
        assert triad.reflection.cloudpivot_review is not None

    def test_reflection_missing_when_no_self_eval(self):
        triad = TriadCollector().collect_from_outcome(Outcome(payload={"artifacts": []}))
        assert triad.reflection.is_complete is False
        assert REFLECTION_UPSTREAM in triad.reflection.missing_halves

    def test_explicit_reflection_overrides(self):
        triad = TriadCollector().collect_from_outcome(
            Outcome(), reflection=Reflection(upstream_self_eval=UPSTREAM, cloudpivot_review=CLOUD))
        assert triad.reflection.is_complete is True

    def test_failing_reviewer_leaves_reflection_incomplete(self):
        def boom(**kwargs):
            raise RuntimeError("judge unavailable")

        triad = TriadCollector(reviewer=boom).collect_from_outcome(Outcome())
        assert triad.reflection.cloudpivot_review is None
        assert triad.is_complete is False

    def test_trace_lookup_used_when_trace_absent(self):
        collector = TriadCollector(trace_lookup=lambda tid: dict(TRACE_ROW))
        triad = collector.collect_from_outcome(Outcome(trace=None))
        assert triad.trace_id == "tr-1"

    def test_missing_trace_when_lookup_empty(self):
        collector = TriadCollector(trace_lookup=lambda tid: None)
        triad = collector.collect_from_outcome(Outcome(trace=None))
        assert TRIAD_TRACE in triad.missing_parts

    def test_payload_artifacts_used_when_outcome_artifacts_absent(self):
        triad = TriadCollector().collect_from_outcome(Outcome(artifacts=None))
        assert triad.artifacts

    def test_single_artifact_mapping_wrapped(self):
        triad = TriadCollector().collect_from_outcome(
            Outcome(artifacts={"path": "a"}))
        assert triad.artifacts == ({"path": "a"},)


# ════════════════════════════════════════════════════════════
#  成本核算：缺三件套 → 不计核算、视为浪费
# ════════════════════════════════════════════════════════════


class TestCostLedger:
    def test_complete_triad_is_counted(self):
        ledger = CostLedger()
        record = ledger.account(complete_triad(), input_tokens=100, output_tokens=50,
                                cost_usd=0.01)
        assert record.counted is True
        assert record.wasted is False
        assert record.counted_tokens == 150
        assert record.counted_cost_usd == 0.01

    @pytest.mark.parametrize("override", [
        {"artifacts": []},
        {"trace": None},
        {"reflection": None},
        {"reflection": Reflection(upstream_self_eval=UPSTREAM)},
        {"trace": {**TRACE_ROW, "actor": "auto"}},
    ])
    def test_incomplete_triad_is_wasted_and_not_counted(self, override):
        ledger = CostLedger()
        record = ledger.account(complete_triad(**override),
                                input_tokens=100, output_tokens=50, cost_usd=0.01)
        assert record.counted is False
        assert record.wasted is True
        assert record.counted_tokens == 0
        assert record.counted_cost_usd == 0.0
        assert record.total_tokens == 150          # 浪费**可见**
        assert record.cost_usd == 0.01
        assert E_WASTED_DELEGATION in record.reason

    def test_reason_names_missing_part(self):
        record = CostLedger().account(complete_triad(trace=None))
        assert TRIAD_TRACE in record.missing_parts
        assert "trace" in record.reason or "轨迹" in record.reason

    def test_totals_separate_counted_and_wasted(self):
        ledger = CostLedger()
        ledger.account(complete_triad(), input_tokens=100, output_tokens=0, cost_usd=0.02)
        ledger.account(complete_triad(artifacts=[]), input_tokens=200, output_tokens=0,
                       cost_usd=0.05)
        totals = ledger.totals()
        assert totals["delegations"] == 2
        assert totals["counted_delegations"] == 1
        assert totals["wasted_delegations"] == 1
        assert totals["counted_tokens"] == 100
        assert totals["wasted_tokens"] == 200
        assert totals["counted_cost_usd"] == 0.02
        assert totals["wasted_cost_usd"] == 0.05
        assert totals["waste_rate"] == 0.5

    def test_empty_ledger_totals(self):
        totals = CostLedger().totals()
        assert totals["delegations"] == 0
        assert totals["waste_rate"] == 0.0

    def test_records_and_wasted_accessors(self):
        ledger = CostLedger()
        ledger.account(complete_triad())
        ledger.account(complete_triad(reflection=None))
        assert len(ledger.records()) == 2
        assert len(ledger.wasted()) == 1

    def test_record_to_dict(self):
        payload = CostLedger().account(complete_triad(), input_tokens=7).to_dict()
        assert payload["counted"] is True
        assert payload["total_tokens"] == 7

    def test_audit_sink_receives_record(self):
        seen = []

        class Audit:
            def record(self, action, **kwargs):
                seen.append((action, kwargs))
                return None

        ledger = CostLedger(audit=Audit())
        ledger.account(complete_triad(reflection=None))
        assert seen and seen[0][0] == "subagent.delegation.cost"
        assert seen[0][1]["status"] == "wasted"

    def test_audit_failure_does_not_break_accounting(self):
        class Broken:
            def record(self, action, **kwargs):
                raise RuntimeError("audit down")

        ledger = CostLedger(audit=Broken())
        record = ledger.account(complete_triad())
        assert record.counted is True


# ════════════════════════════════════════════════════════════
#  stage 闸门：缺三件套 → 阻塞推进
# ════════════════════════════════════════════════════════════


class TestStageGate:
    def test_blocks_stage_predicate(self):
        assert blocks_stage(complete_triad()) is False
        assert blocks_stage(complete_triad(trace=None)) is True

    def test_all_complete_allows_advance(self):
        decision = StageGate("internalized").evaluate([complete_triad(), complete_triad()])
        assert decision.can_advance is True
        assert decision.blockers == ()

    def test_require_passes_when_complete(self):
        gate = StageGate("internalized")
        assert gate.require([complete_triad()]).can_advance is True

    def test_incomplete_blocks_stage(self):
        gate = StageGate("internalized")
        with pytest.raises(StageBlocked) as excinfo:
            gate.require([complete_triad(), complete_triad(artifacts=[])])
        err = excinfo.value
        assert err.code == E_STAGE_BLOCKED
        assert err.stage == "internalized"
        assert len(err.blockers) == 1
        assert TRIAD_ARTIFACTS in err.blockers[0]["missing_parts"]

    def test_block_message_names_missing_parts(self):
        with pytest.raises(StageBlocked) as excinfo:
            StageGate("s2").require([complete_triad(trace=None)])
        assert "trace" in str(excinfo.value)

    def test_blocker_carries_trace_problems(self):
        with pytest.raises(StageBlocked) as excinfo:
            StageGate("s2").require([complete_triad(trace={**TRACE_ROW, "actor": "auto"})])
        assert excinfo.value.blockers[0]["trace_problems"]

    def test_blocker_carries_reflection_halves(self):
        with pytest.raises(StageBlocked) as excinfo:
            StageGate("s2").require(
                [complete_triad(reflection=Reflection(upstream_self_eval=UPSTREAM))])
        assert excinfo.value.blockers[0]["reflection_missing_halves"] == [REFLECTION_CLOUD]

    def test_decision_to_dict(self):
        payload = StageGate("s2").evaluate([complete_triad(trace=None)]).to_dict()
        assert payload["stage"] == "s2"
        assert payload["can_advance"] is False
        assert payload["blocker_count"] == 1

    def test_error_to_dict(self):
        try:
            StageGate("s2").require([complete_triad(trace=None)])
        except StageBlocked as e:
            assert e.to_dict()["code"] == E_STAGE_BLOCKED


class TestTriadSerialisation:
    def test_complete_triad_to_dict(self):
        payload = complete_triad().to_dict()
        assert payload["complete"] is True
        assert payload["part_count"] == 3
        assert payload["artifact_count"] == 1
        assert payload["trace_id"] == "tr-1"

    def test_incomplete_triad_to_dict(self):
        payload = complete_triad(trace=None).to_dict()
        assert payload["complete"] is False
        assert payload["wasted"] is True
        assert TRIAD_TRACE in payload["missing"]["missing_parts"]
