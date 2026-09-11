"""TASK-S3-02 Seed Pack 资产单测（`agent/digestion/seed_pack.json` + 加载器）

验收对应（P7.2-23）：
- **≥12 技能 × ≥3 组**可加载，且**全部可回放**（本文件逐例实跑沙箱）；
- 每组用例带 provenance（来源/引用/说明）与能力契约（期望输出/副作用/终态）；
- 用例级候选实现覆盖与条件步（参数级分支）在真实回放中被**执行/跳过**两种情形都覆盖；
- 资产是**纯数据**（JSON），不夹带代码，可被非开发者增补。

用例不触碰运行时数据（沙箱是内存虚拟环境）。
"""

from __future__ import annotations

import json
import os

import pytest

from agent.digestion import cases as C
from agent.digestion import sandbox as S

TDDSKILL = "cp.skill.pd-test-driven-development-8562c8ad-skill"


#: 会话级兜底：判定集默认落点隔离（与 cases/gate 套件同口径；防运行时目录污染）
@pytest.fixture(autouse=True)
def isolated_case_root(tmp_path, monkeypatch):
    monkeypatch.setenv(C.CASE_ROOT_ENV, str(tmp_path / "cases"))
    yield str(tmp_path / "cases")


@pytest.fixture(scope="module")
def sandbox() -> S.ReplaySandbox:
    return S.ReplaySandbox()


@pytest.fixture(scope="module")
def all_seed_cases():
    cases = []
    for case_set in C.seed_pack_case_sets():
        cases.extend(case_set.cases)
    return cases


class TestSeedPackShape:
    def test_meets_p7_2_23_minimums(self):
        summary = C.seed_pack_summary()
        assert summary["meets_p7_2_23"] is True
        assert summary["skills"] >= C.SEED_PACK_MIN_SKILLS
        assert summary["cases"] >= (C.SEED_PACK_MIN_SKILLS
                                    * C.MIN_SEED_CASES_PER_SKILL)
        assert summary["skills_below_min"] == []

    def test_every_skill_has_display_name_and_provenance(self):
        for skill in C.seed_pack_skills():
            assert skill["capability_id"].startswith("cp.")
            assert skill["display_name"]
            provenance = skill.get("provenance") or {}
            assert provenance.get("source") and provenance.get("ref")
            assert provenance.get("note")

    def test_every_skill_declares_native_template(self):
        for skill in C.seed_pack_skills():
            assert skill.get("native_template"), skill["capability_id"]

    def test_every_case_has_contract_fields(self):
        for case in C.seed_pack_case_sets()[0].cases:
            assert case.title and case.intent_key
            assert case.expected_status in C.EXPECTED_STATUSES
            assert case.expected_output_schema or case.expected_output

    def test_asset_is_pure_json_data(self):
        with open(C.SEED_PACK_PATH, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        assert payload["schema_version"] == C.CASE_SCHEMA_VERSION
        assert payload["conventions"]["sandbox_root"] == C.DEFAULT_SANDBOX_ROOT
        blob = json.dumps(payload, ensure_ascii=False)
        assert "import " not in blob and "eval(" not in blob

    def test_all_paths_are_inside_sandbox_root(self):
        """Seed 用例不使用真实路径（无真实数据；沙箱根之外一律拒绝）"""
        root = C.DEFAULT_SANDBOX_ROOT
        for case in C.seed_pack_case_sets()[1].cases:
            for value in case.input.values():
                if isinstance(value, str) and "/" in value:
                    assert value.startswith(root)
            for path in case.fixtures:
                assert path.startswith(root)

    def test_destructive_cases_exist(self):
        destructive = [c for case_set in C.seed_pack_case_sets()
                       for c in case_set.cases if c.destructive]
        assert len(destructive) >= 3
        assert any("force" in tag or "delete" in tag or "覆盖" in tag
                   for c in destructive for tag in c.branch_tags)

    def test_case_level_native_overrides_exist(self):
        overrides = [c for case_set in C.seed_pack_case_sets()
                     for c in case_set.cases if c.native]
        assert overrides, "至少一组用例声明自己的候选实现（形状不同的边界用例）"
        for case in overrides:
            assert C.seed_candidate_for(case) == case.native


class TestSeedPackReplay:
    def test_every_seed_case_replays_green(self, sandbox, all_seed_cases):
        """P7.2-23 起步集必须**全部可回放**（验收硬项）"""
        failures = [case.case_id for case in all_seed_cases
                    if not sandbox.replay_case(case,
                                               C.seed_candidate_for(case)).passed]
        assert failures == []

    def test_replay_is_deterministic_per_case(self, sandbox, all_seed_cases):
        for case in all_seed_cases[:6]:
            probe = sandbox.determinism_probe(case, C.seed_candidate_for(case))
            assert probe["deterministic"] is True

    def test_seed_cases_reject_a_wrong_candidate(self, sandbox, all_seed_cases):
        """负向：把候选换成错误程序，Seed 用例必须判否（不是"恒绿"资产）"""
        case = next(c for c in all_seed_cases
                    if c.case_id == "seed-write_file-new")
        wrong = [C.ProgramStep(label="write_file",
                               params={"path": "C:/sandbox/out/other.txt",
                                       "content": "different"})]
        replay = sandbox.replay_case(case, wrong)
        assert replay.passed is False
        assert S.LAYER_SIDE_EFFECTS in replay.diff.failed_layers

    def test_error_path_cases_expect_error_status(self, sandbox, all_seed_cases):
        error_cases = [c for c in all_seed_cases
                       if c.expected_status == "error"]
        assert len(error_cases) >= 5
        for case in error_cases:
            replay = sandbox.replay_case(case, C.seed_candidate_for(case))
            assert replay.candidate.status == S.OBS_ERROR

    def test_external_call_cases_never_execute_for_real(self, sandbox,
                                                        all_seed_cases):
        shell_cases = [c for c in all_seed_cases
                       if "shell_execute" in c.expected_side_effects.get(
                           "external_calls", [])]
        assert shell_cases
        for case in shell_cases:
            replay = sandbox.replay_case(case, C.seed_candidate_for(case))
            assert "shell_execute" in replay.candidate.side_effects["external_calls"]


class TestSeedConditionalStep:
    """参数级条件（S3-01 遗留 #4 扩展点）在 Seed 资产里的两种情形"""

    def test_condition_step_executes_when_condition_holds(self, sandbox):
        case = next(c for c in C.seed_cases_for(TDDSKILL)
                    if c.case_id == "seed-tdd-red-green")
        replay = sandbox.replay_case(case, C.seed_candidate_for(case))
        assert replay.passed is True
        assert replay.candidate.skipped == []
        assert "C:/sandbox/out/test_review.md" in \
            replay.candidate.side_effects["files_written"]

    def test_condition_step_is_skipped_when_condition_fails(self, sandbox):
        case = next(c for c in C.seed_cases_for(TDDSKILL)
                    if c.case_id == "seed-tdd-no-test-path")
        replay = sandbox.replay_case(case, C.seed_candidate_for(case))
        assert replay.passed is True
        assert replay.candidate.skipped == ["write_file"]
        assert replay.candidate.side_effects["files_written"] == \
            ["C:/sandbox/specs/lexer_spec.py"]

    def test_branch_tags_declare_parameter_level_conditions(self):
        tags = {tag for case_set in C.seed_pack_case_sets()
                for c in case_set.cases for tag in c.branch_tags}
        assert "path contains test" in tags
        assert "path 不含 test" in tags

    def test_condition_is_evaluated_against_bound_params(self):
        case = next(c for c in C.seed_cases_for(TDDSKILL)
                    if c.case_id == "seed-tdd-no-test-path")
        ctx = S.ConditionContext(labels=tuple(case.labels),
                                 params=case.bindings,
                                 step_count=case.step_count)
        assert S.condition_matches("path contains test", ctx) is False
        assert S.condition_matches("path 不含 test", ctx) is False


class TestSeedPackLoader:
    def test_env_override_path(self, tmp_path, monkeypatch):
        custom = tmp_path / "custom.json"
        custom.write_text(json.dumps({"schema_version": 1, "skills": [
            {"capability_id": "cp.x.y", "display_name": "自定义",
             "native_template": [{"label": "read_file"}],
             "cases": [{"case_id": "c-1", "title": "t", "intent_key": "i",
                        "upstream": [{"label": "read_file"}],
                        "expected_output_schema": {"ok": "bool"},
                        "expected_side_effects": {}}]}]}, ensure_ascii=False),
            encoding="utf-8")
        monkeypatch.setenv(C.SEED_PACK_ENV, str(custom))
        C.reset_seed_pack_cache()
        try:
            summary = C.seed_pack_summary()
            assert summary["skills"] == 1
            assert summary["meets_p7_2_23"] is False
            assert len(C.seed_cases_for("cp.x.y")) == 1
        finally:
            C.reset_seed_pack_cache()

    def test_default_path_is_packaged_asset(self):
        assert os.path.basename(C.SEED_PACK_PATH) == "seed_pack.json"
        assert os.path.exists(C.SEED_PACK_PATH)

    def test_cache_is_reused_then_reset(self):
        """缓存复用（返回副本，调用方无法污染缓存）"""
        C.reset_seed_pack_cache()
        first = C.load_seed_pack()
        second = C.load_seed_pack()
        assert first == second and first is not second
        second["skills"].clear()
        assert C.load_seed_pack()["skills"]        # 缓存未被调用方改坏
        C.reset_seed_pack_cache()
        assert C.load_seed_pack()["skills"]
