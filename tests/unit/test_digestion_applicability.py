"""TASK-S3-03 / M4：用例 ↔ 候选**显式适用性字段**单测（`agent/digestion/cases.py`）

验收对应（S3-02 移交遗留 M4「引入显式的 case↔candidate 适用性字段」）：
- 显式字段可声明、可机检（不再读人写的 ``notes`` 才知道为何排除）；
- 与 ``active`` **正交**（active 管"这条用例是否生效"，applicability 管"对哪个候选适用"）；
- 存储往返保真；**未声明约束时不落盘**（既有判定集存储字节不变）；
- 判定集重生成后可经 `apply_applicability()` **机器重新施加**；
- 排除必须给理由（``reason`` 缺失即校验失败）；未知字段一律拒绝（不静默丢弃）。
"""

from __future__ import annotations

import pytest

from agent.digestion import cases as C

CAP = "cp.builtin.read_file"


def make_case(index: int = 0, **kwargs) -> C.EquivalenceCase:
    path = f"C:/sandbox/out/a{index}.txt"
    return C.EquivalenceCase(
        case_id=f"case-{index:03d}", capability_id=CAP, input={"path": path},
        upstream=[C.ProgramStep(label="read_file", params={"path": path})],
        expected_status="success", sandbox_root="C:/sandbox", **kwargs)


# ════════════════════════════════════════════════════════════
#  字段语义
# ════════════════════════════════════════════════════════════


class TestCaseApplicability:
    def test_default_is_unrestricted(self):
        case = make_case()
        assert case.applicability.restricted is False
        assert case.applies_to("candidate_pattern") is True
        assert case.applies_to("seed_pack_native") is True

    def test_whitelist_limits_applicability(self):
        case = make_case()
        case.applicability = C.CaseApplicability(
            include_kinds=[C.CANDIDATE_KIND_SEED_NATIVE], reason="仅 Seed 候选")
        assert case.applies_to("seed_pack_native") is True
        assert case.applies_to("candidate_pattern") is False

    def test_blacklist_removes_applicability(self):
        case = make_case()
        case.applicability = C.CaseApplicability(
            exclude_kinds=[C.CANDIDATE_KIND_PATTERN], reason="形状不同")
        assert case.applies_to("candidate_pattern") is False
        assert case.applies_to("seed_pack_native") is True

    def test_detailed_kind_matches_base(self):
        case = make_case()
        case.applicability = C.CaseApplicability(
            exclude_kinds=["implementation:upstream"], reason="旧实现不参与")
        assert case.applies_to("implementation:upstream") is False
        assert case.applies_to("implementation:candidate") is True

    def test_base_kind_matches_detailed(self):
        case = make_case()
        case.applicability = C.CaseApplicability(
            exclude_kinds=[C.CANDIDATE_KIND_IMPLEMENTATION], reason="实现类候选不参与")
        assert case.applies_to("implementation:anything") is False

    def test_candidate_object_is_normalized(self):
        from agent.digestion.models import (
            CandidatePattern, PatternStep, SameTaskKey,
        )
        pattern = CandidatePattern(
            pattern_id="p1",
            key=SameTaskKey(capability_id=CAP, intent_key="fix", outcome="success"),
            steps=[PatternStep(label="read_file", seq=0, params={})])
        case = make_case()
        case.applicability = C.CaseApplicability(
            exclude_kinds=[C.CANDIDATE_KIND_PATTERN], reason="形状不同")
        assert case.applies_to(pattern) is False
        assert case.applies_to("candidate_pattern") is False

    def test_explain_is_human_readable(self):
        case = make_case()
        case.applicability = C.CaseApplicability(
            exclude_kinds=[C.CANDIDATE_KIND_PATTERN], reason="单次读取 vs 三段链")
        reasons = case.applicability_reason(C.CANDIDATE_KIND_PATTERN)
        assert "排除清单" in reasons and "单次读取" in reasons

    def test_reason_required_when_restricted(self):
        case = make_case()
        case.applicability = C.CaseApplicability(
            exclude_kinds=[C.CANDIDATE_KIND_PATTERN], reason="")
        assert any("未给出 reason" in r for r in case.validate())

    def test_unrestricted_needs_no_reason(self):
        assert make_case().validate() == []

    def test_kinds_are_normalized_and_deduped(self):
        applicability = C.CaseApplicability(
            include_kinds=[" candidate_pattern ", "candidate_pattern", ""],
            reason="x")
        assert applicability.include_kinds == [C.CANDIDATE_KIND_PATTERN]

    def test_from_storage_rejects_unknown_fields(self):
        with pytest.raises(C.CaseValidationError):
            C.CaseApplicability.from_storage_dict({"include_kinds": [],
                                                   "surprise": 1})

    def test_from_storage_handles_none(self):
        assert C.CaseApplicability.from_storage_dict(None).restricted is False


# ════════════════════════════════════════════════════════════
#  与 active 正交 + 存储往返
# ════════════════════════════════════════════════════════════


class TestOrthogonalityAndStorage:
    def test_unrestricted_case_not_persisted(self):
        case = make_case()
        assert "applicability" not in case.to_storage_dict()

    def test_restricted_case_is_persisted(self):
        case = make_case()
        case.applicability = C.CaseApplicability(
            exclude_kinds=[C.CANDIDATE_KIND_PATTERN], reason="形状不同",
            declared_by="tester")
        payload = case.to_storage_dict()
        assert payload["applicability"]["exclude_kinds"] == [C.CANDIDATE_KIND_PATTERN]

    def test_round_trip_preserves_applicability(self):
        case = make_case()
        case.applicability = C.CaseApplicability(
            include_kinds=[C.CANDIDATE_KIND_SEED_NATIVE],
            exclude_kinds=[C.CANDIDATE_KIND_PATTERN], reason="r",
            declared_by="tester", declared_at=123.0)
        restored = C.EquivalenceCase.from_storage_dict(case.to_storage_dict())
        assert restored.applicability.include_kinds == [C.CANDIDATE_KIND_SEED_NATIVE]
        assert restored.applicability.reason == "r"
        assert restored.applicability.declared_at == 123.0

    def test_round_trip_without_field_still_valid(self):
        payload = make_case().to_storage_dict()
        restored = C.EquivalenceCase.from_storage_dict(payload)
        assert restored.applicability.restricted is False

    def test_active_and_applicability_are_independent(self):
        case = make_case()
        case.active = False
        case.applicability = C.CaseApplicability(
            exclude_kinds=[C.CANDIDATE_KIND_PATTERN], reason="x")
        assert case.applies_to("candidate_pattern") is False
        case.applicability = C.CaseApplicability()
        assert case.applies_to("candidate_pattern") is True

    def test_case_set_storage_round_trip(self):
        case = make_case()
        case.applicability = C.CaseApplicability(
            exclude_kinds=[C.CANDIDATE_KIND_PATTERN], reason="形状不同")
        case_set = C.build_case_set(CAP, [case])
        restored = C.CaseSet.from_storage_dict(case_set.to_storage_dict())
        assert restored.cases[0].applies_to("candidate_pattern") is False


# ════════════════════════════════════════════════════════════
#  过滤与（重生成后的）重新施加
# ════════════════════════════════════════════════════════════


class TestFilteringAndReapply:
    def _cases(self):
        cases = [make_case(i) for i in range(6)]
        for case in cases[:2]:
            case.applicability = C.CaseApplicability(
                exclude_kinds=[C.CANDIDATE_KIND_PATTERN], reason="形状不同")
        return cases

    def test_applicable_cases_splits_with_reasons(self):
        kept, excluded = C.applicable_cases(self._cases(), C.CANDIDATE_KIND_PATTERN)
        assert len(kept) == 4 and len(excluded) == 2
        assert excluded[0]["reason"] and excluded[0]["case_id"]
        assert excluded[0]["applicability"]["exclude_kinds"] == [
            C.CANDIDATE_KIND_PATTERN]

    def test_case_applies_to_helper(self):
        case = self._cases()[0]
        applies, reason = C.case_applies_to(case, C.CANDIDATE_KIND_PATTERN)
        assert applies is False and "形状不同" in reason

    def test_apply_by_case_ids(self):
        case_set = C.build_case_set(CAP, self._cases())
        report = C.apply_applicability(case_set, rules=[{
            "match": {"case_ids": ["case-003", "case-004"]},
            "exclude_kinds": [C.CANDIDATE_KIND_SEED_NATIVE],
            "reason": "仅对 Seed 候选不适用"}], declared_by="tester")
        assert sorted(report["applied"][0]["matched"]) == ["case-003", "case-004"]
        assert case_set.cases[3].applies_to("seed_pack_native") is False

    def test_apply_by_step_count_and_labels(self):
        case_set = C.build_case_set(CAP, self._cases())
        report = C.apply_applicability(case_set, rules=[{
            "match": {"step_count": 1, "labels_contain": ["read_file"]},
            "include_kinds": [C.CANDIDATE_KIND_SEED_NATIVE],
            "reason": "单步读取契约"}], declared_by="tester")
        assert len(report["applied"][0]["matched"]) == len(case_set.cases)

    def test_reset_first_clears_previous_marks(self):
        case_set = C.build_case_set(CAP, self._cases())
        assert case_set.cases[0].applicability.restricted is True
        report = C.apply_applicability(case_set, rules=[], reset_first=True)
        assert report["restricted"] == []
        assert case_set.cases[0].applicability.restricted is False

    def test_reapply_after_regeneration_is_machine_runnable(self):
        """M4 的另一半：判定集重生成后**重新施加**（不再依赖人读 notes）"""
        original = C.build_case_set(CAP, self._cases())
        regenerated = C.build_case_set(CAP, [make_case(i) for i in range(6)],
                                       version=2)
        assert regenerated.cases[0].applicability.restricted is False
        C.apply_applicability(regenerated, rules=[{
            "match": {"case_ids": ["case-000", "case-001"]},
            "exclude_kinds": [C.CANDIDATE_KIND_PATTERN],
            "reason": "重生成后重新施加：形状不匹配"}],
            declared_by="regeneration_hook", reset_first=True)
        kept, excluded = C.applicable_cases(regenerated.cases,
                                            C.CANDIDATE_KIND_PATTERN)
        assert len(kept) == 4 and len(excluded) == 2
        assert original.cases[0].applies_to("candidate_pattern") is False

    def test_rule_matches_each_case_once(self):
        case_set = C.build_case_set(CAP, self._cases())
        report = C.apply_applicability(case_set, rules=[
            {"match": {"case_ids": ["case-000"]}, "exclude_kinds": ["candidate_pattern"],
             "reason": "first"},
            {"match": {"case_ids": ["case-000"]}, "include_kinds": ["candidate_pattern"],
             "reason": "second"},
        ], declared_by="tester")
        assert report["applied"][0]["matched"] == ["case-000"]
        assert report["applied"][1]["matched"] == []

    def test_invalid_rule_raises(self):
        case_set = C.build_case_set(CAP, self._cases())
        with pytest.raises(C.CaseValidationError):
            C.apply_applicability(case_set, rules=["not-a-dict"])

    def test_report_counts_unrestricted(self):
        case_set = C.build_case_set(CAP, self._cases())
        report = C.apply_applicability(case_set, rules=[], reset_first=True)
        assert report["unrestricted_count"] == 6

    def test_normalize_candidate_kind_variants(self):
        assert C.normalize_candidate_kind(None) == C.CANDIDATE_KIND_SEED_NATIVE
        assert C.normalize_candidate_kind("candidate_pattern") == "candidate_pattern"
        assert C.normalize_candidate_kind(" implementation:x ") == "implementation:x"
        assert C.normalize_candidate_kind(lambda case: case) == C.CANDIDATE_KIND_PROVIDER

    def test_candidate_kind_matches_rules(self):
        assert C.candidate_kind_matches("implementation", "implementation:x") is True
        assert C.candidate_kind_matches("implementation:x", "implementation") is True
        assert C.candidate_kind_matches("candidate_pattern", "seed_pack_native") is False
        assert C.candidate_kind_matches("", "x") is False
