"""TASK-S5-02 评测执行器单测（`agent/eval/runner.py` + `solvers.py`）

覆盖：
- 单用例四态（pass / fail / error / **unassessed**）与"未评测不计入通过率分母"；
- 报告视图（counts / by_scenario / p99 / markdown / to_dict）；
- 基线固化与对照（回归 / 改进 / 新增 / 消失 / 用例集换版 `anchor_changed`）；
- **变异解的区分度不变量**：任一判定条目被破坏 → 该用例必须 fail；
- L0 fail-closed（锚被改动 → 拒绝评测）、L3 框架声明。
"""

from __future__ import annotations

import json
import os

import pytest

from agent.eval import anchor as A
from agent.eval import cases as C
from agent.eval import runner as R
from agent.eval import solvers as S


def _cases() -> C.EvalCaseSet:
    case = C.EvalCase.from_dict({
        "id": "L1-01", "layer": "L1", "scenario": C.SCENARIO_S1_FIX_BUG, "title": "t",
        "input": {},
        "expect": [
            {"checker": "equals", "path": "tool", "args": {"value": "cp.fs.read_file"}},
            {"checker": "one_of", "path": "tier", "args": {"values": ["cheap"]}},
        ],
    })
    return C.EvalCaseSet(layer="L1", cases=(case,))


# ════════════════════════════════════════════════════════════
#  单用例四态
# ════════════════════════════════════════════════════════════


class TestRunCase:
    def test_pass(self):
        result = R.run_case(_cases().cases[0],
                            lambda c: {"tool": "cp.fs.read_file", "tier": "cheap"})
        assert result.status == R.STATUS_PASS and result.passed and result.mechanical

    def test_fail_lists_failed_checks(self):
        result = R.run_case(_cases().cases[0],
                            lambda c: {"tool": "cp.fs.delete_file", "tier": "expensive"})
        assert result.status == R.STATUS_FAIL
        assert len(result.failed_checks) == 2
        assert result.to_dict()["passed"] is False

    def test_error_on_solver_exception(self):
        def boom(case):
            raise RuntimeError("solver down")

        result = R.run_case(_cases().cases[0], boom)
        assert result.status == R.STATUS_ERROR and "RuntimeError" in result.note

    def test_error_on_non_mapping_answer(self):
        result = R.run_case(_cases().cases[0], lambda c: ["not", "a", "dict"])  # type: ignore[return-value]
        assert result.status == R.STATUS_ERROR

    def test_unassessed_when_solver_returns_none(self):
        result = R.run_case(_cases().cases[0], lambda c: None)
        assert result.status == R.STATUS_UNASSESSED
        assert not result.assessed and not result.passed
        assert "未产出答案" in result.note

    def test_unsupported_case_never_assessed(self):
        case = C.EvalCase.from_dict({
            "id": "L2-01", "layer": "L2", "scenario": C.SCENARIO_S2_CODEBASE_QA,
            "title": "需要 LLM", "input": {}, "expect": [],
            "verdict_kind": C.VERDICT_UNSUPPORTED, "notes": "本环境无凭证"})
        result = R.run_case(case, lambda c: {"anything": 1})
        assert result.status == R.STATUS_UNASSESSED and result.note == "本环境无凭证"

    def test_empty_answer_fails(self):
        result = R.run_case(_cases().cases[0], lambda c: {})
        assert result.status == R.STATUS_FAIL


# ════════════════════════════════════════════════════════════
#  变异解区分度
# ════════════════════════════════════════════════════════════


class TestMutants:
    def test_mutant_breaks_each_check_for_every_case(self):
        """不变量：**逐条**判定条目被破坏时，该用例必须判 fail"""
        case_set = _cases()
        answers = {"L1-01": {"tool": "cp.fs.read_file", "tier": "cheap"}}
        for case in case_set.cases:
            for index in range(len(case.expect)):
                broken = S.broken_answer_for_check(answers[case.id], case, index)
                result = R.run_case(case, lambda c, b=broken: b)
                assert not result.passed, f"{case.id}[{index}] 未被判负"

    def test_mutant_all_checks_mode(self):
        solve, name = S.mutant_solver({"L1-01": {"tool": "cp.fs.read_file",
                                                 "tier": "cheap"}})
        assert name == "mutant"
        result = R.run_case(_cases().cases[0], solve)
        assert not result.passed

    def test_mutant_first_check_mode(self):
        solve, _ = S.mutant_solver({"L1-01": {"tool": "cp.fs.read_file", "tier": "cheap"}},
                                   mode=S.MODE_FIRST_CHECK)
        result = R.run_case(_cases().cases[0], solve)
        assert not result.passed

    def test_mutant_unknown_mode_rejected(self):
        with pytest.raises(ValueError):
            S.mutant_solver({}, mode="nope")

    def test_mutant_missing_answer_is_unassessed(self):
        solve, _ = S.mutant_solver({})
        assert R.run_case(_cases().cases[0], solve).status == R.STATUS_UNASSESSED

    @pytest.mark.parametrize("checker,args,value,broken_expect", [
        ("equals", {"value": True}, True, False),
        ("one_of", {"values": ["a"]}, "a", S.MUTANT_MARK),
        ("numeric_between", {"min": 2, "max": 2}, 2, 1.0),
        ("length_between", {"min": 2}, [1, 2], ["x"]),
        ("path_exists", {}, "agent/eval/cases.py", S.MISSING_PATH),
        ("symbols_exist", {}, ["class EvalCase"], [S.MISSING_SYMBOL]),
        ("keys_present", {"keys": ["a"]}, {"a": 1}, {}),
        ("list_ordered", {"values": ["a", "b"]}, ["a", "b"], [S.MUTANT_MARK]),
    ])
    def test_break_value_is_checker_aware(self, checker, args, value, broken_expect):
        from agent.eval import checkers as K
        check = {"checker": checker, "path": "x", "args": args}
        broken = S.break_value(value, check)
        assert not K.run_check({"x": broken}, check)["passed"]

    def test_break_answer_falls_back_to_empty(self):
        case = C.EvalCase.from_dict({
            "id": "L1-02", "layer": "L1", "scenario": C.SCENARIO_S1_FIX_BUG,
            "title": "t", "input": {},
            "expect": [{"checker": "nonempty", "path": "", "args": {}}]})
        broken, applied = S.break_answer({"a": 1}, case)
        assert applied and broken == {}

    def test_solver_from_spec_variants(self, tmp_path):
        answers = {"L1-01": {"tool": "cp.fs.read_file", "tier": "cheap"}}
        assert S.solver_from_spec("null")[1] == S.SOLVER_NULL
        assert S.solver_from_spec("reference", answers=answers)[1] == "reference"
        assert S.solver_from_spec("static", answers=answers)[1] == "static"
        assert S.solver_from_spec("mutant", answers=answers)[1] == "mutant"
        assert S.solver_from_spec("mutant:first_check", answers=answers)[1].startswith("mutant")
        path = tmp_path / "answers.json"
        path.write_text(json.dumps({"answers": answers}, ensure_ascii=False), encoding="utf-8")
        solve, name = S.solver_from_spec(f"file:{path}")
        assert name.startswith("file:") and solve(_cases().cases[0]) is not None
        custom = lambda case: {"x": 1}  # noqa: E731
        assert S.solver_from_spec(custom)[0] is custom
        with pytest.raises(ValueError):
            S.solver_from_spec("mystery")


# ════════════════════════════════════════════════════════════
#  报告视图
# ════════════════════════════════════════════════════════════


class TestReport:
    def _report(self):
        case_set = _cases()
        solve, name = S.reference_solver({"L1-01": {"tool": "cp.fs.read_file",
                                                    "tier": "cheap"}})
        return R.run_layer("L1", case_set=case_set, solver=solve, solver_name=name)

    def test_counts_and_pass_rate(self):
        report = self._report()
        assert report.total == 1 and report.passed == 1
        assert report.pass_rate == 1.0 and report.assessed == 1
        assert report.unassessed == 0
        assert report.counts()[R.STATUS_PASS] == 1

    def test_pass_rate_none_without_assessed(self):
        report = R.run_layer("L1", case_set=_cases(), solver=lambda c: None)
        assert report.pass_rate is None and report.assessed == 0
        assert report.unassessed == 1

    def test_by_scenario_and_p99(self):
        report = self._report()
        row = report.by_scenario()[C.SCENARIO_S1_FIX_BUG]
        assert row["total"] == 1 and row["pass_rate"] == 1.0
        assert report.p99_wall_ms() >= 0.0

    def test_to_dict_and_markdown(self):
        payload = self._report().to_dict()
        assert payload["clock"] == R.CLOCK_WALL
        assert payload["caseset_sha256"]
        text = self._report().markdown()
        assert "通过率" in text and "clock 口径" in text

    def test_markdown_lists_failures(self):
        report = R.run_layer("L1", case_set=_cases(),
                             solver=lambda c: {"tool": "x", "tier": "y"})
        text = report.markdown()
        assert "失败明细" in text and "L1-01" in text
        assert len(report.failures()) == 1

    def test_run_layer_rejects_unknown_layer(self):
        with pytest.raises(R.RunnerError):
            R.run_layer("L9", case_set=_cases())

    def test_run_layer_missing_caseset(self):
        with pytest.raises(R.RunnerError):
            R.run_layer("L1", caseset_path="/definitely/missing.json")


# ════════════════════════════════════════════════════════════
#  基线
# ════════════════════════════════════════════════════════════


class TestBaseline:
    def _report(self, answer=None):
        solve, name = S.static_solver({"L1-01": answer if answer is not None else {
            "tool": "cp.fs.read_file", "tier": "cheap"}})
        return R.run_layer("L1", case_set=_cases(), solver=solve, solver_name=name)

    def test_default_path(self):
        assert R.default_baseline_path("L2").endswith(
            os.path.join("eval", "baselines", "l2_baseline.json"))

    def test_write_and_load_roundtrip(self, tmp_path):
        path = str(tmp_path / "l1_baseline.json")
        R.write_baseline(self._report(), path, note="unit")
        payload = R.load_baseline(path)
        assert payload["layer"] == "L1" and payload["pass_rate"] == 1.0
        assert payload["results"] == {"L1-01": R.STATUS_PASS}
        assert payload["caseset_sha256"]

    def test_write_baseline_refuses_anchor_dir(self):
        with pytest.raises(A.AnchorReadOnlyError):
            R.write_baseline(self._report(), os.path.join(A.DEFAULT_ANCHOR_DIR, "b.json"))

    def test_load_missing_or_corrupt(self, tmp_path):
        assert R.load_baseline(str(tmp_path / "nope.json")) == {}
        bad = tmp_path / "bad.json"
        bad.write_text("{bad", encoding="utf-8")
        assert R.load_baseline(str(bad)) == {}

    def test_compare_missing_baseline(self):
        comparison = R.compare_to_baseline(self._report(), {})
        assert comparison["status"] == "baseline_missing" and comparison["regressions"] == []

    def test_compare_regression_detected(self, tmp_path):
        path = str(tmp_path / "b.json")
        R.write_baseline(self._report(), path)
        broken = self._report(answer={"tool": "cp.fs.delete_file", "tier": "cheap"})
        comparison = R.compare_to_baseline(broken, R.load_baseline(path))
        assert comparison["status"] == "regressed"
        assert comparison["regressions"][0]["case_id"] == "L1-01"

    def test_compare_improvement_and_new_cases(self):
        baseline = {"results": {"L1-01": R.STATUS_FAIL, "L1-99": R.STATUS_PASS},
                    "caseset_sha256": "x", "pass_rate": 0.0}
        comparison = R.compare_to_baseline(self._report(), baseline)
        assert comparison["improvements"][0]["case_id"] == "L1-01"
        assert comparison["removed_cases"] == ["L1-99"]
        assert comparison["anchor_changed"] is True
        assert comparison["status"] == "anchor_changed"
        assert "不可信" in comparison["note"]

    def test_compare_new_cases_detected(self):
        baseline = {"results": {}, "caseset_sha256": "x"}
        comparison = R.compare_to_baseline(self._report(), baseline)
        assert comparison["new_cases"] == ["L1-01"]


# ════════════════════════════════════════════════════════════
#  L0 / L3 集成（真实锚）
# ════════════════════════════════════════════════════════════


class TestL0Integration:
    def test_reference_run_all_pass(self):
        report = R.run_l0(reference=True, compare_baseline=False)
        assert report.total == 20
        assert report.counts()[R.STATUS_FAIL] == 0
        assert report.pass_rate == 1.0
        assert report.integrity["ok"]
        assert any("不代表任何模型能力" in d for d in report.disclosures)

    def test_default_solver_is_null_and_discloses(self):
        report = R.run_l0(compare_baseline=False)
        assert report.unassessed == 20 and report.pass_rate is None
        assert any("未评测" in d for d in report.disclosures)
        assert any("系统数据目录" in d for d in report.disclosures)

    def test_fail_closed_on_tampered_anchor(self, tmp_path):
        import shutil
        root = str(tmp_path / "l0_anchor")
        shutil.copytree(A.DEFAULT_ANCHOR_DIR, root)
        cases_path = os.path.join(root, A.CASES_FILENAME)
        data = json.loads(open(cases_path, encoding="utf-8").read())
        data["cases"][0]["expect"][0]["checker"] = "nonempty"  # 改动一条判定
        open(cases_path, "w", encoding="utf-8", newline="\n").write(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        with pytest.raises(A.AnchorIntegrityError):
            R.run_l0(root=root, reference=True, compare_baseline=False)

    def test_mutant_discriminates_all_l0_cases(self):
        store = A.AnchorStore()
        solve, _ = S.mutant_solver(store.load_reference())
        report = R.run_l0(store=store, solver=solve, solver_name="mutant",
                          compare_baseline=False)
        assert [r.case_id for r in report.results if r.passed] == []


class TestLayers:
    def test_l1_reference_all_pass(self):
        report = R.reference_self_check("L1", compare_baseline=False)
        assert report.total == 10 and report.pass_rate == 1.0
        assert report.counts()[R.STATUS_FAIL] == 0
        assert any("不代表任何模型能力" in d for d in report.disclosures)

    def test_l2_reference_all_pass(self):
        report = R.reference_self_check("L2", compare_baseline=False)
        assert report.total == 50
        assert report.counts()[R.STATUS_FAIL] == 0
        assert report.counts()[R.STATUS_ERROR] == 0

    def test_layer_reference_loader(self, tmp_path):
        assert R.load_layer_reference("L1")
        assert R.load_layer_reference("L1", str(tmp_path / "missing.json")) == {}
        bad = tmp_path / "bad.json"
        bad.write_text("[1, 2]", encoding="utf-8")
        assert R.load_layer_reference("L1", str(bad)) == {}

    def test_l3_framework_contract(self):
        report = R.run_l3()
        assert report.layer == "L3" and report.total == 0
        assert report.framework["status"] == "framework_only"
        assert report.framework["target_size"] == 80
        assert report.framework["cadence"]["phase"]
        assert any("框架占位" in d for d in report.disclosures)
        assert R.l3_framework() == R.L3_FRAMEWORK

    def test_l3_without_caseset_file(self, tmp_path):
        report = R.run_l3(caseset_path=str(tmp_path / "missing.json"))
        assert report.framework["status"] == "framework_only" and report.total == 0
        assert any("框架占位" in d for d in report.disclosures)
