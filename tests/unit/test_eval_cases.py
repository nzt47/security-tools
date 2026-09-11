"""TASK-S5-02 用例数据契约单测（`agent/eval/cases.py`）

覆盖：规范化哈希口径、对象不可变、schema 校验（未知字段/缺字段/结构）、
层规模与场景覆盖契约、读写往返、以及**哈希锚定的稳定性**（顺序无关、内容敏感）。
"""

from __future__ import annotations

import json

import pytest

from agent.eval import cases as C


def _case(case_id: str = "L0-T-01", *, layer: str = "L0",
          scenario: str = C.SCENARIO_S1_FIX_BUG, expect=None, **kwargs) -> C.EvalCase:
    payload = {
        "id": case_id, "layer": layer, "scenario": scenario,
        "title": kwargs.pop("title", "t"),
        "expect": expect if expect is not None else [
            {"checker": "nonempty", "path": "code", "args": {}, "why": "w"}],
    }
    payload.update(kwargs)
    return C.EvalCase.from_dict(payload)


# ════════════════════════════════════════════════════════════
#  哈希口径
# ════════════════════════════════════════════════════════════


class TestHashing:
    def test_canonical_json_is_key_order_independent(self):
        assert C.canonical_json({"b": 1, "a": 2}) == C.canonical_json({"a": 2, "b": 1})

    def test_case_digest_is_content_sensitive(self):
        a = _case(title="x")
        b = _case(title="y")
        assert C.case_digest(a) != C.case_digest(b)

    def test_case_digest_stable_across_instances(self):
        assert C.case_digest(_case()) == C.case_digest(_case())

    def test_caseset_digest_order_independent(self):
        a, b = _case("L0-T-01"), _case("L0-T-02", title="second")
        assert C.caseset_digest([a, b]) == C.caseset_digest([b, a])

    def test_caseset_digest_sensitive_to_any_case(self):
        a, b = _case("L0-T-01"), _case("L0-T-02")
        c = _case("L0-T-02", title="changed")
        assert C.caseset_digest([a, b]) != C.caseset_digest([a, c])

    def test_manifest_entries_sorted_and_complete(self):
        cases = [_case("L0-T-02"), _case("L0-T-01")]
        entries = C.caseset_manifest_entries(cases)
        assert list(entries) == ["L0-T-01", "L0-T-02"]
        assert set(entries.values()) == {C.case_digest(c) for c in cases}

    def test_sha256_text_matches_hashlib(self):
        import hashlib
        assert C.sha256_text("abc") == hashlib.sha256(b"abc").hexdigest()


# ════════════════════════════════════════════════════════════
#  对象契约
# ════════════════════════════════════════════════════════════


class TestEvalCaseObject:
    def test_immutable(self):
        case = _case()
        with pytest.raises(Exception):
            case.id = "L0-X-99"  # type: ignore[misc]

    def test_roundtrip_digest_equal(self):
        case = _case(tags=("a", "b"), notes="note")
        assert C.case_digest(C.EvalCase.from_dict(case.to_dict())) == C.case_digest(case)

    def test_views(self):
        case = _case(expect=[
            {"checker": "equals", "path": "a"},
            {"checker": "nonempty", "path": "b"}])
        assert case.checkers == ("equals", "nonempty")
        assert case.is_mechanical
        assert case.summary()["checks"] == 2

    def test_to_dict_omits_empty_tags(self):
        assert "tags" not in _case().to_dict()

    @pytest.mark.parametrize("payload, fragment", [
        ({"id": "", "layer": "L0", "scenario": C.SCENARIO_S1_FIX_BUG}, "id"),
        ({"layer": "L0", "scenario": C.SCENARIO_S1_FIX_BUG}, "id"),
        ({"id": "L0-T-01", "layer": "L0"}, "scenario"),
        ({"id": "L0-T-01", "layer": "L0", "scenario": C.SCENARIO_S1_FIX_BUG,
          "extra": 1}, "未知字段"),
        ({"id": "L0-T-01", "layer": "L0", "scenario": C.SCENARIO_S1_FIX_BUG,
          "expect": "x"}, "expect 必须是数组"),
        ({"id": "L0-T-01", "layer": "L0", "scenario": C.SCENARIO_S1_FIX_BUG,
          "expect": [{"checker": "nonempty", "path": "a", "oops": 1}]}, "未知字段"),
        ({"id": "L0-T-01", "layer": "L0", "scenario": C.SCENARIO_S1_FIX_BUG,
          "expect": [{"path": "a"}]}, "缺 checker"),
        ({"id": "L0-T-01", "layer": "L0", "scenario": C.SCENARIO_S1_FIX_BUG,
          "input": []}, "input 必须是对象"),
    ])
    def test_from_dict_rejects_bad_payload(self, payload, fragment):
        with pytest.raises(C.CaseSchemaError) as excinfo:
            C.EvalCase.from_dict(payload)
        assert fragment in str(excinfo.value)


# ════════════════════════════════════════════════════════════
#  用例集校验
# ════════════════════════════════════════════════════════════


def _l0_set(counts: dict = None) -> C.EvalCaseSet:
    """默认产出**合规的 20 条** L0 集（6 类场景各 ≥2）"""
    counts = counts or {"S1_fix_bug": 4, "S2_codebase_qa": 3, "S3_commit": 3,
                        "router_decision": 4, "approval_intercept": 3, "rollback": 3}
    cases = []
    for scenario, number in counts.items():
        for index in range(number):
            cases.append(_case(f"L0-{scenario}-{index + 1:02d}", scenario=scenario))
    return C.EvalCaseSet(layer="L0", cases=tuple(cases))


class TestValidateCaseSet:
    def test_unknown_layer(self):
        errors = C.validate_case_set(C.EvalCaseSet(layer="L9", cases=()))
        assert any("未知层" in e for e in errors)

    def test_duplicate_id(self):
        case = _case("L0-T-01")
        errors = C.validate_case_set(
            C.EvalCaseSet(layer="L0", cases=(case, case)), require_layer_size=False)
        assert any("重复用例 id" in e for e in errors)

    def test_layer_mismatch_and_prefix(self):
        errors = C.validate_case_set(C.EvalCaseSet(
            layer="L1", cases=(_case("L1-T-01", layer="L0"),)), require_layer_size=False)
        assert any("layer=" in e for e in errors)

    def test_unknown_scenario(self):
        errors = C.validate_case_set(C.EvalCaseSet(
            layer="L0", cases=(_case("L0-T-01", scenario="nope"),)),
            require_layer_size=False)
        assert any("未知场景" in e for e in errors)

    def test_unknown_verdict_kind(self):
        errors = C.validate_case_set(C.EvalCaseSet(
            layer="L0", cases=(_case("L0-T-01", verdict_kind="maybe"),)),
            require_layer_size=False)
        assert any("未知 verdict_kind" in e for e in errors)

    def test_unknown_checker(self):
        errors = C.validate_case_set(C.EvalCaseSet(layer="L0", cases=(
            _case("L0-T-01", expect=[{"checker": "no_such", "path": "a"}]),)),
            require_layer_size=False)
        assert any("未登记的判定器" in e for e in errors)

    def test_proxy_checker_forbidden_in_mechanical_case(self):
        errors = C.validate_case_set(C.EvalCaseSet(layer="L0", cases=(
            _case("L0-T-01", expect=[{"checker": "rubric_keywords", "path": "a"}]),
        )), require_layer_size=False)
        assert any("代理判定器" in e for e in errors)

    def test_empty_expect_requires_unsupported(self):
        errors = C.validate_case_set(C.EvalCaseSet(
            layer="L0", cases=(_case("L0-T-01", expect=[]),)), require_layer_size=False)
        assert any("expect 为空" in e for e in errors)
        ok = C.validate_case_set(C.EvalCaseSet(layer="L0", cases=(
            _case("L0-T-01", expect=[], verdict_kind=C.VERDICT_UNSUPPORTED,
                  notes="本环境无凭证"),)), require_layer_size=False)
        assert not any("expect 为空" in e for e in ok)

    def test_proxy_case_requires_notes(self):
        errors = C.validate_case_set(C.EvalCaseSet(layer="L0", cases=(
            _case("L0-T-01", expect=[{"checker": "rubric_keywords", "path": "a"}],
                  verdict_kind=C.VERDICT_PROXY),)), require_layer_size=False)
        assert any("notes" in e for e in errors)

    def test_layer_size_contract(self):
        errors = C.validate_case_set(_l0_set({"S1_fix_bug": 1}), require_layer_size=True)
        assert any("规模契约" in e for e in errors)

    def test_l0_scenario_coverage(self):
        errors = C.validate_case_set(_l0_set({"S1_fix_bug": 20}))
        assert sum(1 for e in errors if "场景覆盖不足" in e) == len(C.SCENARIOS) - 1

    def test_l2_seed_scenario_coverage(self):
        cases = [_case(f"L2-S1-{i:02d}", layer="L2", scenario=C.SCENARIO_S1_FIX_BUG)
                 for i in range(50)]
        errors = C.validate_case_set(C.EvalCaseSet(layer="L2", cases=tuple(cases)))
        assert sum(1 for e in errors if "种子场景覆盖不足" in e) == 2

    def test_valid_l0_set_passes(self):
        assert C.validate_case_set(_l0_set()) == []


# ════════════════════════════════════════════════════════════
#  读写
# ════════════════════════════════════════════════════════════


class TestIo:
    def test_parse_rejects_unknown_schema(self):
        with pytest.raises(C.CaseSchemaError):
            C.parse_case_set({"schema": "nope", "layer": "L0", "cases": []})

    def test_parse_rejects_non_object(self):
        with pytest.raises(C.CaseSchemaError):
            C.parse_case_set([])  # type: ignore[arg-type]

    def test_load_missing_file(self, tmp_path):
        with pytest.raises(C.CaseSetError):
            C.load_case_set(str(tmp_path / "nope.json"))

    def test_load_invalid_json(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(C.CaseSchemaError):
            C.load_case_set(str(path))

    def test_dump_and_load_roundtrip(self, tmp_path):
        case_set = _l0_set()
        path = str(tmp_path / "cases.json")
        C.dump_case_set(case_set, path)
        loaded = C.load_case_set(path)
        assert len(loaded) == len(case_set)
        assert loaded.caseset_sha256 == case_set.caseset_sha256

    def test_dump_rejects_invalid_set(self, tmp_path):
        bad = C.EvalCaseSet(layer="L0", cases=(_case("L0-T-01"),))
        with pytest.raises(C.CaseSetError):
            C.dump_case_set(bad, str(tmp_path / "x.json"))

    def test_load_reports_validation_errors(self, tmp_path):
        bad = C.EvalCaseSet(layer="L0", cases=(_case("L0-T-01"),))
        path = str(tmp_path / "cases.json")
        path_obj = tmp_path / "cases.json"
        path_obj.write_text(json.dumps(bad.to_dict(), ensure_ascii=False), encoding="utf-8")
        with pytest.raises(C.CaseSetError) as excinfo:
            C.load_case_set(path)
        assert "规模契约" in str(excinfo.value)


# ════════════════════════════════════════════════════════════
#  用例集视图
# ════════════════════════════════════════════════════════════


class TestEvalCaseSet:
    def test_views(self):
        case_set = _l0_set()
        assert len(case_set) == 20
        assert case_set.scenario_counts()[C.SCENARIO_S1_FIX_BUG] == 4
        assert case_set.verdict_counts() == {"mechanical": 20}
        assert case_set.get("L0-S1_fix_bug-01") is not None
        assert case_set.get("nope") is None
        assert len(case_set.inventory()) == 20
        assert list(iter(case_set))

    def test_to_dict_shape(self):
        payload = _l0_set().to_dict()
        assert payload["schema"] == C.SCHEMA_NAME
        assert payload["count"] == 20
        assert payload["caseset_sha256"]
        assert payload["scenario_counts"]
