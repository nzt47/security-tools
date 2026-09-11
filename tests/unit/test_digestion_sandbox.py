"""TASK-S3-02 回放沙箱单测（`agent/digestion/sandbox.py`）

验收对应：
- **回放确定性**：同输入同环境两次结果逐字段一致；
- **副作用只记录不双写**：`commit()` 恒拒 + 出界路径拦下 + 真实文件系统零改动；
- **三层比对**：结构 schema（硬）→ 副作用集合（硬）→ judge ≥0.85（软）+ 10% 人工抽检，
  且**任一层失败都有明确报告**；
- 配额（§5.2 TEST 约束）与接口补齐/绑定/条件求值的正确性。

全部用例在 tmp_path 与内存虚拟文件系统内执行，不触碰运行时数据。
"""

from __future__ import annotations

import os

import pytest

from agent.digestion import cases as C
from agent.digestion import sandbox as S
from agent.digestion.models import (
    BranchCondition,
    CandidatePattern,
    PatternStep,
    SameTaskKey,
)

CAP = "cp.builtin.write_file"


# ════════════════════════════════════════════════════════════
#  构造工具
# ════════════════════════════════════════════════════════════


def write_case(case_id: str = "w1", *, path: str = "C:/sandbox/out/a.txt",
               content: str = "hello", fixtures: bool = False,
               expected_writes: bool = True) -> C.EquivalenceCase:
    return C.EquivalenceCase(
        case_id=case_id, capability_id=CAP,
        input={"path": path, "content": content},
        upstream=[C.ProgramStep(label="write_file",
                                params={"path": "${path}",
                                        "content": "${content}"})],
        expected_output_schema={"ok": "bool", "path": "str", "bytes": "number"},
        expected_side_effects={"files_written": ([path] if expected_writes else [])},
        fixtures=({path: "original"} if fixtures else {}))


def read_case(case_id: str = "r1", *, path: str = "C:/sandbox/src/a.py",
              present: bool = True) -> C.EquivalenceCase:
    return C.EquivalenceCase(
        case_id=case_id, capability_id="cp.builtin.read_file",
        input={"path": path, "encoding": "utf-8"},
        upstream=[C.ProgramStep(label="read_file",
                                params={"path": "${path}",
                                        "encoding": "utf-8"})],
        expected_output_schema=({"ok": "bool", "path": "str", "bytes": "number",
                                "lines": "number"} if present else
                               {"ok": "bool", "error_code": "str",
                                "error": "str", "path": "str"}),
        expected_status=("success" if present else "error"),
        fixtures=({path: "line one\nline two\n"} if present else {}))


def sample_pattern() -> CandidatePattern:
    key = SameTaskKey(capability_id=CAP, intent_key="write+file",
                      outcome="success")
    return CandidatePattern(
        pattern_id="pat-1", key=key, support=25, sample_size=28, coverage=1.0,
        lcs_length=3, confidence=0.95,
        steps=[PatternStep(seq=1, label="read_file",
                           capability_id="cp.builtin.read_file",
                           params={"path": "${path}"}),
               PatternStep(seq=2, label="shell_execute",
                           capability_id="cp.builtin.shell_execute",
                           params={"cmd": "${cmd}"},
                           condition="步骤 `write_file` 缺失"),
               PatternStep(seq=3, label="write_file",
                           capability_id=CAP,
                           params={"content_2": "${content}"})])


# ════════════════════════════════════════════════════════════
#  1. 虚拟环境：守卫 / 配额 / 只记录不双写
# ════════════════════════════════════════════════════════════


class TestReplayEnv:
    def test_write_inside_root_is_recorded(self):
        env = S.ReplayEnv(root="C:/sandbox")
        key = env.write_text("C:/sandbox/out/a.txt", "hi")
        assert key == "C:/sandbox/out/a.txt"
        assert env.side_effects["files_written"] == ["C:/sandbox/out/a.txt"]

    def test_write_outside_root_is_blocked(self):
        env = S.ReplayEnv(root="C:/sandbox")
        with pytest.raises(S.SandboxEscapeError):
            env.write_text("C:/real/secret.txt", "hi")

    def test_empty_path_is_blocked(self):
        env = S.ReplayEnv(root="C:/sandbox")
        with pytest.raises(S.SandboxEscapeError):
            env.resolve("")

    def test_commit_always_refuses(self):
        """副作用只记录不双写：本沙箱**不提供**真实落盘通道"""
        env = S.ReplayEnv(root="C:/sandbox")
        with pytest.raises(S.SandboxError):
            env.commit()

    def test_real_filesystem_untouched(self, tmp_path):
        """回放绝不双写真实环境：真实目录内的同名文件回放后原样未动"""
        target = tmp_path / "outside.txt"
        target.write_text("real", encoding="utf-8")
        root = str(tmp_path).replace("\\", "/")
        case = write_case(path=f"{root}/outside.txt", content="sandbox only")
        case.sandbox_root = root      # 沙箱根 = 该用例路径的前缀（仍是虚拟语义）
        sandbox = S.ReplaySandbox()
        replay = sandbox.replay_case(case, case.upstream)
        assert replay.passed is True
        assert replay.candidate.side_effects["files_written"] == \
            [f"{root}/outside.txt"]
        assert target.read_text(encoding="utf-8") == "real"

    def test_allow_real_io_is_forced_false(self):
        quota = S.SandboxQuota(allow_real_io=True)
        assert quota.allow_real_io is False

    def test_quota_normalizes_non_positive_values(self):
        quota = S.SandboxQuota(max_steps=0, max_bytes=-5, sim_budget_ms=0)
        assert quota.max_steps == 1 and quota.max_bytes == 1

    def test_quota_from_env_reads_and_falls_back(self):
        quota = S.SandboxQuota.from_env({
            f"{S.QUOTA_ENV_PREFIX}_MAX_STEPS": "4",
            f"{S.QUOTA_ENV_PREFIX}_MAX_BYTES": "not-a-number",
            f"{S.QUOTA_ENV_PREFIX}_DENY_EXTERNAL": "true",
        })
        assert quota.max_steps == 4
        assert quota.max_bytes == S.SandboxQuota().max_bytes
        assert quota.deny_external is True

    def test_external_labels_are_stubbed_not_executed(self):
        env = S.ReplayEnv(root="C:/sandbox")
        result = env.call("shell_execute", {"cmd": "rm -rf /"})
        assert result["ok"] is True and result["simulated"] == "shell_execute"
        assert env.side_effects["external_calls"] == ["shell_execute"]

    def test_deny_external_returns_denied(self):
        env = S.ReplayEnv(root="C:/sandbox",
                          quota=S.SandboxQuota(deny_external=True))
        result = env.call("shell_execute", {"cmd": "ls"})
        assert result["ok"] is False
        assert result["error_code"] == S.ERR_EXTERNAL_DENIED

    def test_latency_is_a_model_clock(self):
        env = S.ReplayEnv(root="C:/sandbox")
        env.call("read_file", {"path": "C:/sandbox/a"})
        assert env.sim_ms == S.TOOL_LATENCY_MS["read_file"]

    def test_snapshot_digests_are_shape_normalized(self):
        env = S.ReplayEnv(root="C:/sandbox")
        env.write_text("C:/sandbox/a.txt", "x")
        assert env.snapshot()["digests"] == [f"${{path}}={S._sha8('x')}"]

    def test_list_paths_scoped_to_prefix(self):
        env = S.ReplayEnv(root="C:/sandbox", fixtures={"C:/sandbox/a.txt": "1"})
        assert env.list_paths("C:/sandbox") == ["C:/sandbox/a.txt"]


class TestQuota:
    def test_step_quota_aborts_run(self):
        case = write_case()
        case.upstream = [C.ProgramStep(label="write_file",
                                       params={"path": "${path}"})] * 3
        obs = S.run_program(case.upstream, case,
                            quota=S.SandboxQuota(max_steps=2))
        assert obs.status == S.OBS_QUOTA_EXCEEDED
        assert obs.error_code == S.ERR_QUOTA_STEPS

    def test_file_write_quota(self):
        case = write_case()
        case.upstream = [C.ProgramStep(label="write_file",
                                       params={"path": "${path}"})] * 3
        obs = S.run_program(case.upstream, case,
                            quota=S.SandboxQuota(max_files_written=1))
        assert obs.error_code == S.ERR_QUOTA_FILES

    def test_external_call_quota(self):
        case = write_case()
        case.input["cmd"] = "ls"
        case.upstream = [C.ProgramStep(label="shell_execute",
                                       params={"cmd": "${cmd}"}),
                         C.ProgramStep(label="shell_execute",
                                       params={"cmd": "${cmd}"})]
        obs = S.run_program(case.upstream, case,
                            quota=S.SandboxQuota(max_external_calls=1))
        assert obs.error_code == S.ERR_QUOTA_EXTERNAL

    def test_time_budget_quota(self):
        case = write_case()
        case.upstream = [C.ProgramStep(label="write_file",
                                       params={"path": "${path}",
                                               "content": "${content}"}),
                         C.ProgramStep(label="write_file",
                                       params={"path": "${path}",
                                               "content": "${content}"})]
        obs = S.run_program(case.upstream, case,
                            quota=S.SandboxQuota(sim_budget_ms=2.0))
        assert obs.error_code == S.ERR_QUOTA_TIME


# ════════════════════════════════════════════════════════════
#  2. 绑定 / 接口补齐 / 键名还原
# ════════════════════════════════════════════════════════════


class TestBinding:
    def test_named_placeholder_binds_from_input(self):
        case = read_case()
        bound, unbound = S.bind_params({"path": "${path}"}, case)
        assert bound["path"] == case.input["path"] and unbound == []

    def test_unbound_named_placeholder_is_reported(self):
        case = read_case()
        _bound, unbound = S.bind_params({"path": "${mystery}"}, case)
        assert unbound == ["${mystery}"]

    def test_shape_placeholder_is_synthesized_deterministically(self):
        case = C.EquivalenceCase(case_id="c", capability_id=CAP, input={},
                                 upstream=[C.ProgramStep(label="read_file")])
        first = S.bind_value("${path}", case)
        second = S.bind_value("${path}", case)
        assert first == second and first.startswith(case.sandbox_root)

    def test_unbound_input_aborts_run(self):
        case = read_case()
        case.input = {}
        case.bindings = {}
        obs = S.run_program([C.ProgramStep(label="write_file",
                                           params={"path": "${target}"})], case)
        assert obs.status == S.OBS_UNBOUND_INPUT
        assert obs.error_code == S.ERR_UNBOUND_INPUT

    def test_missing_input_keys_is_static_check(self):
        case = read_case()
        steps = [C.ProgramStep(label="write_file", params={"path": "${target}"})]
        assert S.missing_input_keys(steps, case) == ["target"]
        assert S.missing_input_keys([C.ProgramStep(label="read_file",
                                                   params={"path": "${path}"})],
                                    case) == []

    def test_complete_params_fills_from_upstream_position(self):
        case = read_case()
        merged, fills = S.complete_params(
            "read_file", 0, {}, case.upstream, case)
        assert merged["path"] == case.input["path"]     # 按位次取到该位置的绑定值
        assert fills[0]["source"] == S.FILL_FROM_POSITION

    def test_complete_params_prefers_positional_slot_over_bare_key(self):
        """同名键两处出现：写步骤必须取到**报告路径**而不是读路径（实测缺陷回归）"""
        case = C.EquivalenceCase(
            case_id="positional", capability_id=CAP,
            input={"path": "C:/sandbox/src/a.py"},
            bindings={"path_2": "C:/sandbox/out/report.md"},
            upstream=[C.ProgramStep(label="read_file",
                                    params={"path": "${path}"}),
                      C.ProgramStep(label="shell_execute",
                                    params={"cmd": "pytest"}),
                      C.ProgramStep(label="write_file",
                                    params={"path": "${path_2}",
                                            "content": "body"})])
        merged, fills = S.complete_params("write_file", 2, {}, case.upstream, case)
        assert merged["path"] == "C:/sandbox/out/report.md"
        assert fills[0]["source"] == S.FILL_FROM_POSITION

    def test_complete_params_uses_literal_over_bare_key_fallback(self):
        """位次记的是字面量时直接用字面量，不回退到裸键（否则会写到别的位置）"""
        case = C.EquivalenceCase(
            case_id="literal", capability_id=CAP,
            input={"path": "C:/sandbox/src/a.py"},
            upstream=[C.ProgramStep(label="read_file",
                                    params={"path": "${path}"}),
                      C.ProgramStep(label="write_file",
                                    params={"path": "C:/sandbox/out/fixed.md"})])
        merged, _fills = S.complete_params("write_file", 1, {}, case.upstream, case)
        assert merged["path"] == "C:/sandbox/out/fixed.md"

    def test_complete_params_falls_back_to_case_input(self):
        case = read_case()
        merged, fills = S.complete_params("write_file", 5, {}, [], case)
        assert merged["path"] == case.input["path"]
        assert fills[0]["source"] == S.FILL_FROM_INPUT

    def test_complete_params_never_overrides_candidate_params(self):
        case = read_case()
        merged, fills = S.complete_params(
            "write_file", 0, {"path": "C:/sandbox/chosen.txt"}, [], case)
        assert merged["path"] == "C:/sandbox/chosen.txt" and fills == []

    def test_complete_params_no_requirement_is_noop(self):
        case = read_case()
        merged, fills = S.complete_params("list_dir", 0, {}, [], case)
        assert merged == {} and fills == []

    def test_restore_param_keys_maps_slot_suffix(self):
        assert S.restore_param_keys({"cmd_2": "${cmd_2}",
                                     "content_2": "${content_2}"}) == {
            "cmd": "${cmd_2}", "content": "${content_2}"}

    def test_restore_param_key_keeps_unknown_suffix(self):
        assert S.restore_param_key("my_index_2") == "my_index_2"
        assert S.restore_param_key("path") == "path"

    def test_restore_param_keys_keeps_first_on_collision(self):
        restored = S.restore_param_keys({"path": "a", "path_2": "b"})
        assert restored == {"path": "a"}

    def test_condition_step_is_skipped_when_false(self):
        case = write_case()
        steps = [C.ProgramStep(label="write_file",
                               params={"path": "${path}"},
                               condition="path contains absent")]
        obs = S.run_program(steps, case)
        assert obs.skipped == ["write_file"] and obs.steps == []

    def test_condition_step_runs_when_true(self):
        case = write_case(path="C:/sandbox/tests/test_out.txt")
        steps = [C.ProgramStep(label="write_file",
                               params={"path": "${path}"},
                               condition="path contains test")]
        obs = S.run_program(steps, case)
        assert obs.steps == ["write_file"] and obs.skipped == []


# ════════════════════════════════════════════════════════════
#  3. 条件求值与破坏性判定
# ════════════════════════════════════════════════════════════


class TestConditions:
    def _ctx(self) -> S.ConditionContext:
        return S.ConditionContext(labels=("read_file", "write_file"),
                                  params={"path": "C:/x/test_a.py", "mode": "force"},
                                  step_count=2)

    def test_step_presence_and_absence(self):
        ctx = self._ctx()
        assert S.condition_matches("步骤 `write_file` 出现", ctx) is True
        assert S.condition_matches("步骤 `shell` 缺失", ctx) is True
        assert S.condition_matches("步骤 `write_file` 缺失", ctx) is False

    def test_param_atoms(self):
        ctx = self._ctx()
        assert S.condition_matches("path contains test", ctx) is True
        assert S.condition_matches("path contains prod", ctx) is False
        assert S.condition_matches("mode == force", ctx) is True
        assert S.condition_matches("mode != force", ctx) is False
        assert S.condition_matches("path matches .*\\.py", ctx) is True

    def test_step_count_atoms(self):
        ctx = self._ctx()
        assert S.condition_matches("steps >= 2", ctx) is True
        assert S.condition_matches("步数 < 1", ctx) is False

    def test_or_and_precedence(self):
        ctx = self._ctx()
        assert S.condition_matches("path contains prod 或 mode == force", ctx) is True
        assert S.condition_matches("path contains test 且 mode == force", ctx) is True
        assert S.condition_matches("path contains test 且 mode == safe", ctx) is False

    def test_empty_condition_matches(self):
        assert S.condition_matches("", self._ctx()) is True

    def test_unknown_atom_is_reported_not_silently_false(self):
        ctx = self._ctx()
        assert S.condition_unknown_atoms("莫名其妙的条件", ctx) == ["莫名其妙的条件"]
        assert S.condition_matches("莫名其妙的条件", ctx) is False

    def test_illegal_regex_is_unknown(self):
        assert S.condition_unknown_atoms("path matches [", self._ctx()) == ["path matches ["]

    def test_evaluate_condition_returns_group_count(self):
        result = S.evaluate_condition("path contains prod 或 mode == force",
                                      self._ctx())
        assert result["groups"] == 2 and result["matched"] is True

    def test_classify_structural_vs_addressable(self):
        assert S.classify_condition("步骤 `x` 缺失")["required"] is False
        assert S.classify_condition("步数 >= 3")["required"] is False
        assert S.classify_condition("path contains test")["required"] is True
        assert S.classify_condition("path contains t 且 步骤 `x` 出现")["required"] is True
        assert S.classify_condition("???")["kind"] == "unknown"
        assert S.classify_condition("")["required"] is False

    def test_destructive_detection(self):
        assert S.is_destructive_condition("cmd contains --force") is True
        assert S.is_destructive_condition("步骤 `delete_file` 出现") is True
        assert S.is_destructive_condition("path contains test") is False
        assert S.is_destructive_condition("path contains test",
                                         outcome="failure") is True


# ════════════════════════════════════════════════════════════
#  4. 三层比对
# ════════════════════════════════════════════════════════════


class TestThreeLayers:
    def _replay(self, case: C.EquivalenceCase, candidate):
        return S.ReplaySandbox().replay_case(case, candidate)

    def test_identical_programs_pass_all_layers(self):
        case = write_case()
        replay = self._replay(case, case.upstream)
        assert replay.passed is True
        assert replay.diff.failed_layers == []
        assert replay.diff.layer(S.LAYER_JUDGE).score == 1.0

    def test_structure_layer_catches_missing_step(self):
        case = write_case()
        candidate = [C.ProgramStep(label="read_file", params={"path": "${path}"}),
                     C.ProgramStep(label="write_file",
                                   params={"path": "${path}",
                                           "content": "${content}"})]
        case.upstream = [C.ProgramStep(label="write_file",
                                       params={"path": "${path}",
                                               "content": "${content}"})]
        replay = self._replay(case, candidate)
        assert S.LAYER_STRUCTURE in replay.diff.failed_layers
        assert "输出结构不一致" in replay.diff.layer(S.LAYER_STRUCTURE).reasons[0]

    def test_structure_layer_checks_declared_schema(self):
        case = write_case()
        case.expected_output_schema = {"ok": "bool", "absent": "str"}
        replay = self._replay(case, case.upstream)
        assert S.LAYER_STRUCTURE in replay.diff.failed_layers
        assert "缺声明字段" in replay.diff.layer(S.LAYER_STRUCTURE).reasons[0]

    def test_structure_layer_checks_declared_value(self):
        case = write_case()
        case.expected_output = {"ok": False}
        replay = self._replay(case, case.upstream)
        assert "值" in replay.diff.layer(S.LAYER_STRUCTURE).reasons[0]

    def test_side_effect_layer_catches_content_drift(self):
        case = write_case()
        candidate = [C.ProgramStep(label="write_file",
                                   params={"path": "${path}",
                                           "content": "different"})]
        case.upstream = [C.ProgramStep(label="write_file",
                                       params={"path": "${path}",
                                               "content": "${content}"})]
        replay = self._replay(case, candidate)
        assert S.LAYER_SIDE_EFFECTS in replay.diff.failed_layers
        assert "写入内容指纹不一致" in replay.diff.layer(S.LAYER_SIDE_EFFECTS).reasons[0]

    def test_side_effect_layer_catches_target_drift(self):
        case = write_case()
        case.expected_side_effects = {"files_written": ["C:/sandbox/elsewhere.txt"]}
        replay = self._replay(case, case.upstream)
        assert S.LAYER_SIDE_EFFECTS in replay.diff.failed_layers

    def test_side_effect_contract_skipped_when_source_none(self):
        case = write_case()
        case.side_effects_source = C.SIDE_EFFECT_SOURCE_NONE
        case.expected_side_effects = {"files_written": ["C:/sandbox/elsewhere.txt"]}
        replay = self._replay(case, case.upstream)
        assert S.LAYER_SIDE_EFFECTS not in replay.diff.failed_layers
        assert replay.diff.layer(S.LAYER_SIDE_EFFECTS).detail["contract_enforced"] is False

    def test_judge_layer_fails_below_threshold(self):
        case = write_case()
        sandbox = S.ReplaySandbox(judge=lambda a, b: 0.5)
        replay = sandbox.replay_case(case, case.upstream)
        layer = replay.diff.layer(S.LAYER_JUDGE)
        assert layer.passed is False and "0.5" in layer.reasons[0]
        assert layer.detail["judge_kind"] == "injected"

    def test_judge_exception_is_reported_not_raised(self):
        def boom(_a, _b):
            raise RuntimeError("judge down")

        case = write_case()
        replay = S.ReplaySandbox(judge=boom).replay_case(case, case.upstream)
        layer = replay.diff.layer(S.LAYER_JUDGE)
        assert layer.passed is False and "judge 执行失败" in layer.reasons[0]

    def test_manual_flagged_is_reported(self):
        case = write_case()
        replay = S.ReplaySandbox().replay_case(case, case.upstream,
                                              manual_flagged=True)
        layer = replay.diff.layer(S.LAYER_JUDGE)
        assert layer.detail["manual_review_flagged"] is True
        assert any("人工抽检" in r for r in layer.reasons)

    def test_similarity_scorer_bounds(self):
        assert S.judge_similarity("abc", "abc") == 1.0
        assert S.judge_similarity("abc", "xyz") == 0.0
        assert 0.0 < S.judge_similarity("a b c", "a b d") < 1.0

    def test_manual_sample_ratio_is_deterministic(self):
        ids = [f"case{i:03d}" for i in range(50)]
        first = S.manual_sample_ids(ids, ratio=0.1)
        second = S.manual_sample_ids(ids, ratio=0.1)
        assert first == second and len(first) == 5
        assert S.manual_sample_ids([]) == []

    def test_manual_sample_ratio_bounds(self):
        assert len(S.manual_sample_ids(["a"], ratio=0.0)) == 1
        assert len(S.manual_sample_ids(["a", "b"], ratio=5.0)) == 2

    def test_schema_of_includes_list_cardinality(self):
        assert S.schema_of({"a": [1, 2]}) == {"a": "list[2]<number>"}
        assert S.schema_of({"a": [1, 2, 3]}) != S.schema_of({"a": [1, 2]})

    def test_diff_result_reasons_are_layer_scoped(self):
        case = write_case()
        candidate = [C.ProgramStep(label="nonsense_tool",
                                   params={"path": "${path}"})]
        replay = self._replay(case, candidate)
        assert all(r.startswith("[") for r in replay.diff.reasons())


# ════════════════════════════════════════════════════════════
#  5. 确定性与 record-and-replay
# ════════════════════════════════════════════════════════════


class TestDeterminismAndJournal:
    def test_same_input_twice_is_identical(self):
        case = write_case()
        probe = S.ReplaySandbox().determinism_probe(case, case.upstream)
        assert probe["deterministic"] is True
        assert probe["fingerprint_first"] == probe["fingerprint_second"]

    def test_arms_use_independent_environments(self):
        """共享环境会让第二臂看到第一臂的写入 ⇒ 等价性变成顺序依赖的假象"""
        case = write_case(path="C:/sandbox/out/a.txt", content="x")
        candidate = [C.ProgramStep(label="append_file",
                                   params={"path": "${path}",
                                           "content": "${content}"})]
        case.upstream = [C.ProgramStep(label="append_file",
                                       params={"path": "${path}",
                                               "content": "${content}"})]
        replay = S.ReplaySandbox().replay_case(case, candidate)
        assert replay.upstream.env_snapshot["files"] == \
            replay.candidate.env_snapshot["files"]

    def test_journal_records_and_verifies(self, tmp_path):
        case = write_case()
        sandbox = S.ReplaySandbox()
        replay = sandbox.replay_case(case, case.upstream)
        journal = S.RecordReplayJournal(str(tmp_path))
        journal.record(replay)
        assert journal.verify(replay)["replay_ok"] is True

    def test_journal_detects_drift(self, tmp_path):
        case = write_case()
        sandbox = S.ReplaySandbox()
        journal = S.RecordReplayJournal(str(tmp_path))
        journal.record(sandbox.replay_case(case, case.upstream))
        drifted = sandbox.replay_case(case, [C.ProgramStep(
            label="write_file", params={"path": "${path}", "content": "changed"})])
        verdict = journal.verify(drifted)
        assert verdict["replay_ok"] is False
        assert any("output_fingerprint" in d or "side_effects" in d
                   for d in verdict["diffs"])

    def test_journal_missing_record_is_reported(self, tmp_path):
        case = write_case()
        journal = S.RecordReplayJournal(str(tmp_path))
        verdict = journal.verify(S.ReplaySandbox().replay_case(case, case.upstream))
        assert verdict["found"] is False and verdict["replay_ok"] is False

    def test_journal_clear_and_tolerate_bad_lines(self, tmp_path):
        journal = S.RecordReplayJournal(str(tmp_path))
        with open(journal.path, "w", encoding="utf-8") as fh:
            fh.write("{bad json}\n")
        assert journal.entries() == []
        journal.clear()
        assert journal.entries() == []

    def test_journal_entry_round_trip(self):
        case = write_case()
        replay = S.ReplaySandbox().replay_case(case, case.upstream)
        entry = S.JournalEntry.of(replay)
        assert S.JournalEntry.from_dict(entry.to_dict()) == entry


# ════════════════════════════════════════════════════════════
#  6. 实现适配与批量回放
# ════════════════════════════════════════════════════════════


class TestImplementations:
    def test_as_implementation_rejects_unknown_type(self):
        with pytest.raises(S.SandboxError):
            S.as_implementation(object())

    def test_as_implementation_wraps_list_and_pattern(self):
        assert isinstance(S.as_implementation([]), S.TemplateImplementation)
        assert isinstance(S.as_implementation(sample_pattern()),
                          S.PatternImplementation)

    def test_as_implementation_keeps_existing_name(self):
        impl = S.ProgramImplementation([], name="mine")
        assert S.as_implementation(impl, name="other").name == "mine"

    def test_pattern_description_conditions_are_not_execution_guards(self):
        """S3-01 的 `condition` 是描述性分支标注，默认**不得**当执行守卫"""
        impl = S.PatternImplementation(sample_pattern())
        case = C.EquivalenceCase(case_id="c", capability_id=CAP, input={},
                                 upstream=[])
        steps = impl.steps_for(case)
        assert all(step.condition == "" for step in steps)

    def test_pattern_use_conditions_is_opt_in(self):
        impl = S.PatternImplementation(sample_pattern(), use_conditions=True)
        case = C.EquivalenceCase(case_id="c", capability_id=CAP, input={},
                                 upstream=[])
        steps = impl.steps_for(case)
        assert steps[1].condition == "步骤 `write_file` 缺失"

    def test_pattern_drop_steps(self):
        impl = S.PatternImplementation(sample_pattern(), drop_steps=(2,))
        case = C.EquivalenceCase(case_id="c", capability_id=CAP, input={},
                                 upstream=[])
        assert [s.label for s in impl.steps_for(case)] == ["read_file",
                                                          "shell_execute"]

    def test_pattern_slot_keys_are_restored(self):
        impl = S.PatternImplementation(sample_pattern())
        case = read_case()
        steps = impl.steps_for(case)
        assert "content" in steps[2].params

    def test_callable_implementation(self):
        case = write_case()

        def fn(_case, env):
            env.call("write_file", {"path": "C:/sandbox/out/a.txt",
                                    "content": "hello"})
            return {"ok": True}

        obs = S.CallableImplementation(fn, name="fn").run(case)
        assert obs.status == S.OBS_SUCCESS
        assert obs.side_effects["files_written"] == ["C:/sandbox/out/a.txt"]

    def test_callable_implementation_can_return_observation(self):
        case = write_case()
        obs = S.CallableImplementation(
            lambda _c, _e: S.Observation(status=S.OBS_DENIED),
            name="fn").run(case)
        assert obs.status == S.OBS_DENIED

    def test_replay_many_aggregates(self):
        cases = [write_case(f"w{i}") for i in range(4)]
        report = S.ReplaySandbox().replay_many(cases, cases[0].upstream)
        assert report.total == 4 and report.passed == 4
        assert report.pass_rate == 1.0
        assert len(report.manual_sample) == 1

    def test_replay_many_reports_failures(self):
        cases = [write_case("w1")]
        report = S.ReplaySandbox().replay_many(
            cases, [C.ProgramStep(label="write_file",
                                  params={"path": "${path}",
                                          "content": "changed"})])
        assert report.failed_ids == ["w1"]
        assert report.layer_failures[S.LAYER_SIDE_EFFECTS] == 1
        assert report.failure_list()[0]["case_id"] == "w1"

    def test_replay_many_skips_inactive_cases(self):
        cases = [write_case("w1"), write_case("w2")]
        cases[1].active = False
        report = S.ReplaySandbox().replay_many(cases, cases[0].upstream)
        assert report.total == 1

    def test_upstream_provider_override(self):
        case = write_case()
        cases = [case]
        report = S.ReplaySandbox().replay_many(
            cases, case.upstream,
            upstream_provider=lambda _c: S.ProgramImplementation(
                [C.ProgramStep(label="write_file",
                               params={"path": "${path}",
                                       "content": "different"})],
                name="upstream"))
        assert report.failed_ids == ["case" if False else report.failed_ids[0]]

    def test_p99_small_sample_takes_max(self):
        assert S._p99([1.0, 9.0, 5.0]) == 9.0
        assert S._p99([]) == 0.0

    def test_observation_canonical_text_masks_paths(self):
        case = write_case()
        obs = S.ReplaySandbox().replay_case(case, case.upstream).candidate
        assert "C:/sandbox" not in obs.canonical_text()
        assert "${path}" in obs.canonical_text()

    def test_observation_to_dict_includes_filled_params(self):
        case = write_case()
        case.upstream = []
        obs = S.run_program([C.ProgramStep(label="write_file")], case)
        assert obs.to_dict()["filled_params"][0]["source"] == S.FILL_FROM_INPUT


def test_sandbox_module_has_no_real_io_or_process_calls():
    """只读契约的**静态**证据：本模块不调用真实文件/子进程/网络 API"""
    import ast

    with open(S.__file__, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    banned = {"subprocess", "shutil", "socket", "requests", "urllib", "ctypes"}
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & banned)
    assert "os" in imported      # 仅用于路径拼接与目录列表（journal 目录）


def test_sandbox_open_calls_are_journal_only():
    """`open()` 只允许出现在 journal（沙箱自身的台账），不进用例数据路径"""
    import ast

    with open(S.__file__, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    owners = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for child in ast.walk(node):
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Name) \
                        and child.func.id == "open":
                    owners.append(node.name)
    assert set(owners) == {"record", "entries"}


def test_seed_pack_cases_replay_with_their_native_template():
    """Seed Pack 全部用例可回放（P7.2-23 起步集的可执行性）"""
    sandbox = S.ReplaySandbox()
    failures = []
    for case_set in C.seed_pack_case_sets():
        for case in case_set.cases:
            if not sandbox.replay_case(case, C.seed_candidate_for(case)).passed:
                failures.append(case.case_id)
    assert failures == []


def test_pattern_against_seed_only_fails_on_shape_mismatch():
    """用例/候选**形状不匹配**必须被拒绝（不做静默过滤）"""
    case = next(c for c in C.seed_cases_for("cp.builtin.read_file")
                if c.case_id == "seed-read_file-utf8")
    replay = S.ReplaySandbox().replay_case(case, sample_pattern())
    assert replay.passed is False
    assert any("候选实现所需输入缺失" in r or "终态" in r
               for r in replay.failures())


def test_commit_probe_used_by_demo_is_real(tmp_path):
    env = S.ReplayEnv(root=str(tmp_path).replace("\\", "/"))
    with pytest.raises(S.SandboxError):
        env.commit()
    assert os.path.isdir(tmp_path)
