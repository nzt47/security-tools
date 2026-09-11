"""TASK-S5-02 评测数据集与脚本单测

覆盖：
- **提交进仓库的四层数据集**是否符合契约（L0 20 / L1 10 / L2 50 / L3 框架）；
- L0 锚清单（哈希锚定 + 参考解覆盖 + 无 proxy/unsupported）；
- 判定器**区分度**（逐条判定条目负样本对照）；
- `scripts/check_eval_datasets.py` 自检门、`scripts/run_eval.py`、
  `scripts/report_slo_weekly.py`、`scripts/freeze_eval_anchor.py` 的 CLI 行为。

slow 标记：L2 全量 50 条 × 逐条判定对照较重，标 `@pytest.mark.slow` 以便 CI 分片。
"""

from __future__ import annotations

import importlib.util
import json
import os

import pytest

from agent.eval import anchor as A
from agent.eval import cases as C
from agent.eval import runner as R
from agent.eval import solvers as S


def _load_script(name: str):
    """按路径加载 `scripts/<name>.py`（避免污染 sys.path / 触发包名冲突）"""
    path = os.path.join(A._REPO_ROOT, "scripts", name)
    spec = importlib.util.spec_from_file_location(f"eval_script_{name[:-3]}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ════════════════════════════════════════════════════════════
#  L0 锚数据集
# ════════════════════════════════════════════════════════════


class TestL0AnchorDataset:
    def test_contract(self):
        case_set = A.AnchorStore().load()
        assert len(case_set) == 20
        assert case_set.frozen is True
        counts = case_set.scenario_counts()
        assert set(counts) == set(C.SCENARIOS)
        assert all(counts[scenario] >= C.L0_MIN_PER_SCENARIO for scenario in counts)
        assert case_set.verdict_counts() == {"mechanical": 20}

    def test_ids_and_checkers_registered(self):
        case_set = A.AnchorStore().load()
        ids = [c.id for c in case_set.cases]
        assert len(ids) == len(set(ids))
        for case in case_set.cases:
            assert case.id.startswith("L0-")
            assert case.expect
            for check in case.expect:
                assert check["checker"] in C._known_checkers(None)

    def test_manifest_entries_match_cases(self):
        store = A.AnchorStore()
        case_set = store.load()
        manifest = store.manifest()
        assert manifest.count == 20
        assert manifest.entries == C.caseset_manifest_entries(case_set.cases)
        assert manifest.frozen_by and manifest.frozen_at
        assert manifest.tool_version == A.FREEZE_TOOL_VERSION

    def test_reference_covers_every_case(self):
        store = A.AnchorStore()
        answers = store.load_reference()
        assert set(answers) == {c.id for c in store.load().cases}

    def test_reference_run_all_pass_with_disclosure(self):
        report = R.run_l0(reference=True, compare_baseline=False)
        assert report.counts()[R.STATUS_PASS] == 20
        assert report.pass_rate == 1.0
        assert report.integrity["ok"]

    def test_mutant_run_no_survivors(self):
        store = A.AnchorStore()
        solve, _ = S.mutant_solver(store.load_reference())
        report = R.run_l0(store=store, solver=solve, solver_name="mutant",
                          compare_baseline=False)
        assert [r.case_id for r in report.results if r.passed] == []

    def test_every_check_discriminates(self):
        store = A.AnchorStore()
        answers = store.load_reference()
        survivors = []
        for case in store.load().cases:
            for index in range(len(case.expect)):
                broken = S.broken_answer_for_check(answers[case.id], case, index)
                if R.run_case(case, lambda c, b=broken: b).passed:
                    survivors.append(f"{case.id}[{index}]")
        assert survivors == []


# ════════════════════════════════════════════════════════════
#  L1 / L2 / L3 数据集
# ════════════════════════════════════════════════════════════


class TestL1Dataset:
    def test_contract_and_reference(self):
        case_set = C.load_case_set(R.LAYER_CASESET_PATHS[C.LAYER_L1])
        assert len(case_set) == 10
        assert case_set.verdict_counts() == {"mechanical": 10}
        assert len(case_set.by_scenario()) >= 4
        answers = R.load_layer_reference("L1")
        assert set(answers) == {c.id for c in case_set.cases}

    def test_reference_all_pass(self):
        report = R.reference_self_check("L1", compare_baseline=False)
        assert report.counts()[R.STATUS_PASS] == 10

    def test_mutant_has_no_survivors(self):
        solve, _ = S.mutant_solver(R.load_layer_reference("L1"))
        report = R.run_layer("L1", solver=solve, solver_name="mutant")
        assert [r.case_id for r in report.results if r.passed] == []


class TestL2Dataset:
    def test_contract(self):
        case_set = C.load_case_set(R.LAYER_CASESET_PATHS[C.LAYER_L2])
        assert len(case_set) == 50
        counts = case_set.scenario_counts()
        for scenario in C.SEED_SCENARIOS:
            assert counts.get(scenario, 0) >= C.L2_MIN_PER_SEED
        verdicts = case_set.verdict_counts()
        assert verdicts.get(C.VERDICT_UNSUPPORTED, 0) == 0
        assert verdicts.get(C.VERDICT_MECHANICAL, 0) >= 45

    def test_proxy_cases_are_disclosed_and_still_grounded(self):
        case_set = C.load_case_set(R.LAYER_CASESET_PATHS[C.LAYER_L2])
        proxy_cases = [c for c in case_set.cases if c.verdict_kind == C.VERDICT_PROXY]
        assert proxy_cases, "L2 应至少含 1 条代理口径用例用于演示降级与披露"
        for case in proxy_cases:
            assert case.notes, f"{case.id} 缺少披露说明"
            assert any(check["checker"] in ("path_exists", "contains_all",
                                            "symbols_exist", "equals", "one_of",
                                            "list_ordered", "commit_message",
                                            "python_probes", "not_contains")
                       for check in case.expect), f"{case.id} 代理用例缺少机械佐证"

    def test_reference_covers_all_and_passes(self):
        case_set = C.load_case_set(R.LAYER_CASESET_PATHS[C.LAYER_L2])
        answers = R.load_layer_reference("L2")
        assert set(answers) == {c.id for c in case_set.cases}
        report = R.reference_self_check("L2", compare_baseline=False)
        assert report.counts()[R.STATUS_PASS] == 50

    def test_mechanical_cases_use_only_mechanical_checkers(self):
        case_set = C.load_case_set(R.LAYER_CASESET_PATHS[C.LAYER_L2])
        from agent.eval import checkers as K
        for case in case_set.cases:
            if case.verdict_kind != C.VERDICT_MECHANICAL:
                continue
            for check in case.expect:
                assert K.is_mechanical(check["checker"]), f"{case.id}: {check['checker']}"

    @pytest.mark.slow
    @pytest.mark.timeout(300)   # 50 用例 × 212 判定条目 × 逐条负样本对照：CI 分片高负载下防误杀
    def test_mutant_and_per_check_discrimination(self):
        case_set = C.load_case_set(R.LAYER_CASESET_PATHS[C.LAYER_L2])
        answers = R.load_layer_reference("L2")
        solve, _ = S.mutant_solver(answers)
        report = R.run_layer("L2", case_set=case_set, solver=solve, solver_name="mutant")
        assert [r.case_id for r in report.results if r.passed] == []
        survivors = []
        for case in case_set.cases:
            for index in range(len(case.expect)):
                broken = S.broken_answer_for_check(answers[case.id], case, index)
                if R.run_case(case, lambda c, b=broken: b).passed:
                    survivors.append(f"{case.id}[{index}]")
        assert survivors == []


class TestPackageHygiene:
    """包内导入纪律（CI 架构规则的实际教训固化）

    `agent.observability.arch_rules` 的 AST 依赖图把 `from agent.eval import x` 记为
    「依赖**包根** agent.eval」，而包根 `__init__` 又导入各子模块 → 被判为循环依赖，
    架构规则校验（阻断合并）报 4 处违规。故包内一律用 `import agent.eval.<mod> as X`。
    """

    def test_no_intra_package_import_of_package_root(self):
        import ast

        package_dir = os.path.join(A._REPO_ROOT, "agent", "eval")
        offenders = []
        for name in sorted(os.listdir(package_dir)):
            if not name.endswith(".py") or name == "__init__.py":
                continue
            path = os.path.join(package_dir, name)
            with open(path, "r", encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "agent.eval":
                    offenders.append(f"{name}:{node.lineno}")
        assert offenders == [], (
            "包内不得 `from agent.eval import …`（会构成包根循环依赖）：" + ", ".join(offenders))

    def test_package_imports_cleanly(self):
        import importlib
        module = importlib.import_module("agent.eval")
        for attr in ("AnchorStore", "run_l0", "load_case_set", "MECHANICAL_CHECKERS"):
            assert hasattr(module, attr)


class TestL3Dataset:
    def test_framework_placeholder(self):
        case_set = C.load_case_set(R.LAYER_CASESET_PATHS[C.LAYER_L3])
        assert len(case_set) == 0
        assert case_set.meta.get("status") == "framework_only"
        report = R.run_l3()
        assert report.framework["status"] == "framework_only"
        assert report.framework["directory"].endswith("l3_golden80/")
        assert "framework_only" in json.dumps(report.framework, ensure_ascii=False)


# ════════════════════════════════════════════════════════════
#  脚本
# ════════════════════════════════════════════════════════════


class TestCheckDatasetsScript:
    def test_full_self_check_green(self, capsys):
        script = _load_script("check_eval_datasets.py")
        assert script.main(["--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert {row["layer"] for row in payload["layers"]} == set(C.LAYERS)

    def test_single_layer(self, capsys):
        script = _load_script("check_eval_datasets.py")
        assert script.main(["--layer", "L1"]) == 0
        assert "L1" in capsys.readouterr().out

    def test_reports_tampered_anchor(self, tmp_path, capsys):
        import shutil
        root = tmp_path / "l0_anchor"
        shutil.copytree(A.DEFAULT_ANCHOR_DIR, root)
        data = json.loads((root / A.CASES_FILENAME).read_text(encoding="utf-8"))
        data["cases"][0]["title"] = "tampered"
        (root / A.CASES_FILENAME).write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        script = _load_script("check_eval_datasets.py")
        assert script.main(["--layer", "L0", "--anchor-dir", str(root)]) == 1
        assert "FAIL" in capsys.readouterr().out


class TestRunEvalScript:
    def test_reference_layer_runs(self, capsys):
        script = _load_script("run_eval.py")
        assert script.main(["--layer", "L1", "--print-md"]) == 0
        out = capsys.readouterr().out
        assert "L1 评测结果" in out and "wall_clock" in out

    def test_l0_mutant_exits_nonzero(self, capsys):
        script = _load_script("run_eval.py")
        assert script.main(["--layer", "L0", "--solver", "mutant"]) == 1

    def test_l0_null_marks_unassessed(self, capsys):
        script = _load_script("run_eval.py")
        assert script.main(["--layer", "L0", "--solver", "null"]) == 0
        assert "unassessed 20" in capsys.readouterr().out

    def test_record_baseline_and_json_out(self, tmp_path, capsys):
        script = _load_script("run_eval.py")
        baseline = str(tmp_path / "l1_baseline.json")
        out = str(tmp_path / "report.json")
        assert script.main(["--layer", "L1", "--baseline", baseline,
                            "--record-baseline", "--json-out", out,
                            "--no-baseline-compare"]) == 0
        assert R.load_baseline(baseline)["pass_rate"] == 1.0
        assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))["layer"] == "L1"

    def test_l3_layer(self, capsys):
        script = _load_script("run_eval.py")
        assert script.main(["--layer", "L3"]) == 0
        assert "framework_only" in capsys.readouterr().out

    def test_write_l2_baseline(self, tmp_path, capsys):
        script = _load_script("run_eval.py")
        target = str(tmp_path / "l2_baseline.json")
        assert script.main(["--layer", "L2", "--solver", "null",
                            "--write-l2-baseline", target]) == 0
        payload = json.loads((tmp_path / "l2_baseline.json").read_text(encoding="utf-8"))
        assert payload["caseset"]["l2_dataset_cases"] == 50
        assert payload["calibration_trigger"]["l2_dataset_ready"] is True


class TestWeeklyReportScript:
    def test_show_dictionary(self, capsys):
        script = _load_script("report_slo_weekly.py")
        assert script.main(["--show-dictionary"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert len(payload["computable"]) >= 5

    def test_markdown_report_with_isolated_events(self, tmp_path, monkeypatch, capsys):
        from agent.observability import events as ev
        monkeypatch.setenv(ev.ENV_DIR, str(tmp_path / "events"))
        ev.reset_event_stores()
        try:
            script = _load_script("report_slo_weekly.py")
            assert script.main(["--days", "1", "--start", "2026-09-10",
                                "--end", "2026-09-10"]) == 0
            out = capsys.readouterr().out
            assert "指标周报" in out and "数据源" in out
        finally:
            ev.reset_event_stores()

    def test_json_and_written_outputs(self, tmp_path, monkeypatch, capsys):
        from agent.observability import events as ev
        monkeypatch.setenv(ev.ENV_DIR, str(tmp_path / "events"))
        ev.reset_event_stores()
        try:
            script = _load_script("report_slo_weekly.py")
            out_json = str(tmp_path / "w.json")
            out_md = str(tmp_path / "w.md")
            assert script.main(["--json", "--out", out_json, "--md", out_md,
                                "--days", "1", "--start", "2026-09-10",
                                "--end", "2026-09-10"]) == 0
            payload = json.loads((tmp_path / "w.json").read_text(encoding="utf-8"))
            assert "traceability" in payload and "dictionary" in payload
            assert (tmp_path / "w.md").exists()
        finally:
            ev.reset_event_stores()

    def test_fit_difficulty_writes_artifact(self, tmp_path, monkeypatch, capsys):
        from agent.observability import acr as ACR
        from agent.observability import events as ev
        monkeypatch.setenv(ev.ENV_DIR, str(tmp_path / "events"))
        ev.reset_event_stores()
        try:
            ACR.record_task_closed(task_id="t1", status="closed", intent="fix",
                                   difficulty="hard", ts="2026-09-10T10:00:00+08:00")
            script = _load_script("report_slo_weekly.py")
            target = str(tmp_path / "fit.json")
            assert script.main(["--fit-difficulty", "--fit-out", target,
                                "--days", "1", "--start", "2026-09-10",
                                "--end", "2026-09-10"]) == 0
            payload = json.loads((tmp_path / "fit.json").read_text(encoding="utf-8"))
            assert payload["status"] in ("no_signal", "insufficient_samples")
            assert payload["usable"] is False
            assert "只披露不考核" in payload["notes"]
        finally:
            ev.reset_event_stores()

    def test_feedback_dir_missing_warns_without_creating(self, tmp_path, capsys):
        script = _load_script("report_slo_weekly.py")
        target = tmp_path / "no_feedback"
        assert script.main(["--feedback-dir", str(target), "--days", "1",
                            "--start", "2026-09-10", "--end", "2026-09-10"]) == 0
        assert not target.exists()


class TestFreezeScript:
    def test_verify_only_on_repo_anchor(self, capsys):
        script = _load_script("freeze_eval_anchor.py")
        assert script.main(["--verify-only"]) == 0
        assert "系统不可写校验通过" in capsys.readouterr().out

    def test_verify_json(self, capsys):
        script = _load_script("freeze_eval_anchor.py")
        assert script.main(["--verify-only", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] and payload["count"] == 20

    def test_freeze_without_reviewer_is_refused(self, tmp_path):
        """冻结必须有**人工署名**：缺 reviewer 时底层直接拒绝（自动化无权冻结）"""
        import shutil
        root = tmp_path / "a"
        shutil.copytree(A.DEFAULT_ANCHOR_DIR, root)
        script = _load_script("freeze_eval_anchor.py")
        with pytest.raises(A.AnchorReadOnlyError):
            script.main(["--confirm-freeze", "--anchor-dir", str(root)])

    def test_freeze_without_materials_reports_failure(self, tmp_path, capsys):
        script = _load_script("freeze_eval_anchor.py")
        assert script.main(["--confirm-freeze", "--anchor-dir", str(tmp_path / "empty"),
                            "--reviewer", "unit"]) == 2
        assert "待冻结材料缺失" in capsys.readouterr().out

    def test_release_manifest_written_into_tmp(self, tmp_path, capsys):
        script = _load_script("freeze_eval_anchor.py")
        payload = script.write_release_manifest(
            A.DEFAULT_ANCHOR_DIR, str(tmp_path / "release.json"))
        assert payload["anchor_count"] == 20
        assert payload["layers"]["L2"]["count"] == 50
        assert payload["layers"]["L0"]["caseset_sha256"] == \
            payload["anchor_caseset_sha256"]

    def test_collect_layer_hashes_reports_missing_layer(self, tmp_path):
        script = _load_script("freeze_eval_anchor.py")
        hashes = script.collect_layer_hashes(str(tmp_path))
        assert hashes["L0"]["exists"] is False
        assert hashes["L2"]["exists"] is True
