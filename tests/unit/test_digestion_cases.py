"""TASK-S3-02 判定集资产单测（`agent/digestion/cases.py`）

验收对应：
- 判定集资产带 `origin_trace_id`，**独立存储**（Trace 过期/删除不影响）；
- 三通道生成（Seed Pack / Trace 自动 / LLM+人工）与 §3.1 规模区间裁定；
- 复制台账原始参数（§3.1「复制数据脱离其生命周期」）与清洗后步骤的**对齐**；
- 不变量校验不静默（非法用例显式抛错）。

全部用例使用 tmp_path 隔离存储，不触碰运行时数据。
"""

from __future__ import annotations

import json
import os

import pytest

from agent.digestion import cases as C
from agent.digestion.generalize import infer_parameter_slots
from agent.digestion.models import SameTaskKey, Trajectory, TrajectoryStep

CAP = "cp.builtin.read_file"


#: 会话级兜底：判定集/通行证的默认落点（``CP_DIGESTION_CASE_DIR``）隔离到 tmp_path，
#: 防止任何用例（含未来新增）把资产写进运行时目录 `data/digestion/cases/`
@pytest.fixture(autouse=True)
def isolated_case_root(tmp_path, monkeypatch):
    monkeypatch.setenv(C.CASE_ROOT_ENV, str(tmp_path / "cases"))
    yield str(tmp_path / "cases")


# ════════════════════════════════════════════════════════════
#  构造工具（刻意不接收 **kwargs，规避仓库 kwarg 冲突扫描规则）
# ════════════════════════════════════════════════════════════


def make_case(case_id: str = "c1", *, capability_id: str = CAP,
              kind: str = C.CASE_KIND_SEED, path: str = "C:/sandbox/a.txt",
              side_effects: bool = True, origin: str = "t-1"
              ) -> C.EquivalenceCase:
    case = C.EquivalenceCase(
        case_id=case_id, capability_id=capability_id,
        input={"path": path},
        upstream=[C.ProgramStep(label="read_file", params={"path": path})],
        expected_output_schema={"ok": "bool", "path": "str"},
        expected_side_effects=({"files_written": [path]} if side_effects else {}),
        kind=kind, origin_trace_id=(origin if kind == C.CASE_KIND_TRACE else ""))
    return case


def make_trajectory(task_id: str, *, index: int = 0,
                    failing: bool = False) -> Trajectory:
    """合成一条清洗后轨迹（read → shell → write；failing 时缺末步）"""
    steps = [
        TrajectoryStep(seq=1, label="read_file", capability_id=CAP,
                       params={"path": f"${{path}}"},
                       files_written=[]),
        TrajectoryStep(seq=2, label="shell_execute",
                       capability_id="cp.builtin.shell_execute",
                       params={"cmd": f"${{cmd}}"},
                       external_calls=["shell_execute"]),
    ]
    if not failing:
        steps.append(TrajectoryStep(
            seq=3, label="write_file", capability_id="cp.builtin.write_file",
            params={"path": "${path_2}", "content": "${content}"},
            files_written=[f"C:/repo/{task_id}/out.md"]))
    key = SameTaskKey(capability_id=CAP, intent_key="read+file",
                      outcome=("failure" if failing else "success"))
    return Trajectory(trajectory_id=task_id, task_id=task_id, key=key,
                      steps=steps, source_trace_id=f"tr-{task_id}",
                      workspace_id="ws", started_at=1000.0)


# ════════════════════════════════════════════════════════════
#  1. 模型与不变量
# ════════════════════════════════════════════════════════════


class TestCaseModel:
    def test_valid_seed_case_has_no_violation(self):
        assert make_case().validate() == []

    def test_missing_capability_rejected(self):
        case = make_case(capability_id="")
        assert any("capability_id" in r for r in case.validate())

    def test_empty_upstream_rejected(self):
        case = make_case()
        case.upstream = []
        assert any("upstream" in r for r in case.validate())

    def test_trace_kind_requires_origin_trace_id(self):
        case = make_case(kind=C.CASE_KIND_TRACE, origin="")
        assert any("origin_trace_id" in r for r in case.validate())
        assert make_case(kind=C.CASE_KIND_TRACE).validate() == []

    def test_illegal_kind_rejected(self):
        case = make_case()
        case.kind = "nonsense"
        assert any("kind" in r for r in case.validate())

    def test_illegal_expected_status_rejected(self):
        case = make_case()
        case.expected_status = "weird"
        assert any("expected_status" in r for r in case.validate())

    def test_illegal_side_effect_kind_rejected(self):
        case = make_case()
        case.expected_side_effects["unknown_kind"] = ["x"]
        assert any("副作用类型" in r for r in case.validate())

    def test_illegal_side_effects_source_rejected(self):
        case = make_case()
        case.side_effects_source = "guess"
        assert any("side_effects_source" in r for r in case.validate())

    def test_require_valid_raises_with_reasons(self):
        case = make_case(capability_id="")
        with pytest.raises(C.CaseValidationError) as excinfo:
            case.require_valid()
        assert "capability_id" in str(excinfo.value)

    def test_case_id_is_deterministic_when_absent(self):
        first = C.case_id_for(CAP, kind=C.CASE_KIND_TRACE, index=3,
                              origin_trace_id="t-9")
        second = C.case_id_for(CAP, kind=C.CASE_KIND_TRACE, index=3,
                               origin_trace_id="t-9")
        assert first == second and first.startswith("case_")

    def test_side_effect_set_is_shape_normalized(self):
        case = make_case(path="C:/sandbox/deep/nested/file.txt")
        assert case.side_effect_set()["files_written"] == ["${path}"]

    def test_input_fingerprint_is_stable_and_input_sensitive(self):
        first = make_case()
        assert first.input_fingerprint() == make_case().input_fingerprint()
        other = make_case(path="C:/sandbox/b.txt")
        assert first.input_fingerprint() != other.input_fingerprint()

    def test_expected_output_kind_detection(self):
        case = make_case()
        assert case.expected_output_kind == "schema"
        case.expected_output = {"ok": True}
        assert case.expected_output_kind == "value"
        case.expected_output_schema = {}
        assert case.expected_output_kind == "value"
        case.expected_output = {}
        assert case.expected_output_kind == "none"

    def test_round_trip_storage_dict(self):
        case = make_case(kind=C.CASE_KIND_TRACE)
        restored = C.EquivalenceCase.from_storage_dict(case.to_storage_dict())
        assert restored.case_id == case.case_id
        assert restored.upstream[0].label == "read_file"
        assert restored.side_effects_source == case.side_effects_source

    def test_unknown_field_is_rejected_not_dropped(self):
        payload = make_case().to_storage_dict()
        payload["mystery"] = 1
        with pytest.raises(C.CaseValidationError):
            C.EquivalenceCase.from_storage_dict(payload)

    def test_upstream_fingerprint_tracks_program_shape(self):
        case = make_case()
        before = case.upstream_fingerprint()
        case.upstream[0].params["encoding"] = "utf-8"
        assert case.upstream_fingerprint() != before


# ════════════════════════════════════════════════════════════
#  2. CaseSet 与规模裁定
# ════════════════════════════════════════════════════════════


class TestCaseSet:
    def test_build_dedupes_by_case_id(self):
        case = make_case("dup")
        case_set = C.build_case_set(CAP, [case, make_case("dup"),
                                          make_case("other")])
        assert case_set.size == 2

    def test_size_verdict_marks_starter_below_min(self):
        case_set = C.build_case_set(CAP, [make_case("a")])
        verdict = case_set.size_verdict()
        assert verdict["starter"] is True and verdict["complete"] is False

    def test_size_verdict_complete_within_range(self):
        cases = [make_case(f"c{i:03d}") for i in range(C.MIN_CASE_SET_SIZE)]
        case_set = C.build_case_set(CAP, cases)
        verdict = case_set.size_verdict()
        assert verdict["complete"] is True and verdict["starter"] is False

    def test_size_verdict_flags_over_max(self):
        cases = [make_case(f"c{i:03d}") for i in range(C.MAX_CASE_SET_SIZE + 1)]
        case_set = C.build_case_set(CAP, cases)
        assert case_set.size_verdict()["complete"] is False

    def test_active_cases_excludes_inactive(self):
        case = make_case("a")
        case.active = False
        case_set = C.build_case_set(CAP, [case, make_case("b")])
        assert [c.case_id for c in case_set.active_cases()] == ["b"]

    def test_capability_mismatch_rejected(self):
        case = make_case("a", capability_id="cp.other.tool")
        with pytest.raises(C.CaseValidationError):
            C.build_case_set(CAP, [case])

    def test_duplicate_case_id_in_raw_set_rejected(self):
        case_set = C.CaseSet(capability_id=CAP, cases=[make_case("x"),
                                                       make_case("x")])
        assert any("重复" in r for r in case_set.validate())

    def test_invalidate_marks_all_cases_inactive(self):
        case_set = C.build_case_set(CAP, [make_case("a"), make_case("b")])
        case_set.invalidate("上游契约变更", details={"trigger": "manual"})
        assert case_set.drifted is True and case_set.active is False
        assert case_set.active_cases() == []
        assert case_set.drift_details["trigger"] == "manual"

    def test_kind_counts(self):
        case_set = C.build_case_set(CAP, [
            make_case("a", kind=C.CASE_KIND_SEED),
            make_case("b", kind=C.CASE_KIND_TRACE)])
        assert case_set.kind_counts() == {"seed": 1, "trace": 1}

    def test_round_trip_storage_dict(self):
        case_set = C.build_case_set(CAP, [make_case("a")], version=3)
        restored = C.CaseSet.from_storage_dict(case_set.to_storage_dict())
        assert restored.version == 3 and restored.size == 1

    def test_derived_fields_ignored_on_load(self):
        payload = C.build_case_set(CAP, [make_case("a")]).to_storage_dict()
        payload["upstream_fingerprint"] = "stale"
        payload["size_verdict"] = {"stale": True}
        restored = C.CaseSet.from_storage_dict(payload)
        assert restored.size == 1

    def test_unknown_field_is_rejected(self):
        payload = C.build_case_set(CAP, [make_case("a")]).to_storage_dict()
        payload["mystery"] = True
        with pytest.raises(C.CaseValidationError):
            C.CaseSet.from_storage_dict(payload)


# ════════════════════════════════════════════════════════════
#  3. 存储（JSON / SQLite；独立于 Trace 存储）
# ════════════════════════════════════════════════════════════


class TestCaseStore:
    def test_json_store_round_trip(self, tmp_path):
        store = C.JsonCaseStore(str(tmp_path / "cases"))
        store.save(C.build_case_set(CAP, [make_case("a")]))
        loaded = store.load(CAP)
        assert loaded is not None and loaded.size == 1
        assert store.list_capabilities() == [CAP]
        assert store.exists(CAP) is True
        assert store.load("cp.absent") is None

    def test_sqlite_store_round_trip(self, tmp_path):
        store = C.SqliteCaseStore(str(tmp_path / "sqlite"))
        store.save(C.build_case_set(CAP, [make_case("a")]))
        loaded = store.load(CAP)
        assert loaded is not None and loaded.size == 1
        assert store.stats()["backend"] == C.BACKEND_SQLITE

    def test_open_case_store_env_backend(self, tmp_path, monkeypatch):
        monkeypatch.setenv(C.CASE_BACKEND_ENV, "sqlite")
        store = C.open_case_store(str(tmp_path))
        assert isinstance(store, C.SqliteCaseStore)

    def test_open_case_store_illegal_backend_falls_back(self, tmp_path,
                                                        monkeypatch):
        monkeypatch.setenv(C.CASE_BACKEND_ENV, "mongo")
        store = C.open_case_store(str(tmp_path))
        assert isinstance(store, C.JsonCaseStore)

    def test_version_history_and_latest(self, tmp_path):
        store = C.JsonCaseStore(str(tmp_path / "cases"))
        store.save(C.build_case_set(CAP, [make_case("a")], version=1))
        store.save(C.build_case_set(CAP, [make_case("a"), make_case("b")],
                                    version=2))
        assert store.load(CAP).version == 2
        assert store.load(CAP, version=1).size == 1
        assert store.load(CAP, version=99) is None
        assert [h["version"] for h in store.history(CAP)] == [1, 2]
        assert store.next_version(CAP) == 3

    def test_history_is_bounded(self, tmp_path):
        store = C.JsonCaseStore(str(tmp_path / "cases"))
        for version in range(1, C.MAX_STORE_HISTORY + 4):
            store.save(C.build_case_set(CAP, [make_case("a")], version=version))
        assert len(store.history(CAP)) == C.MAX_STORE_HISTORY
        assert store.load(CAP).version == C.MAX_STORE_HISTORY + 3

    def test_save_is_idempotent_per_version(self, tmp_path):
        store = C.JsonCaseStore(str(tmp_path / "cases"))
        case_set = C.build_case_set(CAP, [make_case("a")], version=1)
        store.save(case_set)
        store.save(case_set)
        assert len(store.history(CAP)) == 1

    def test_delete_and_stats(self, tmp_path):
        store = C.JsonCaseStore(str(tmp_path / "cases"))
        store.save(C.build_case_set(CAP, [make_case("a")]))
        assert store.stats()["capabilities"] == 1
        assert store.delete(CAP) is True
        assert store.delete(CAP) is False
        assert store.stats()["capabilities"] == 0

    def test_corrupt_file_is_skipped_not_deleted(self, tmp_path):
        store = C.JsonCaseStore(str(tmp_path / "cases"))
        store.save(C.build_case_set(CAP, [make_case("a")]))
        with open(os.path.join(store.root, "broken.json"), "w",
                  encoding="utf-8") as fh:
            fh.write("{not json")
        assert store.list_capabilities() == [CAP]

    def test_store_root_is_independent_of_trace_dir(self, tmp_path):
        store = C.JsonCaseStore(str(tmp_path / "cases"))
        assert "trace" not in store.root.replace("\\", "/").split("/")[-1]
        assert store.stats()["layout"].startswith("json:")

    def test_invalid_case_set_not_written(self, tmp_path):
        store = C.JsonCaseStore(str(tmp_path / "cases"))
        broken = C.CaseSet(capability_id="", cases=[make_case("a")])
        with pytest.raises(C.CaseValidationError):
            store.save(broken)


# ════════════════════════════════════════════════════════════
#  4. 通道 ②：从同类轨迹生成（含复制原始参数与对齐）
# ════════════════════════════════════════════════════════════


class TestTraceChannel:
    def _trace_set(self, count: int = 3) -> object:
        from agent.digestion.models import TraceSet
        key = SameTaskKey(capability_id=CAP, intent_key="read+file",
                          outcome="success")
        return TraceSet(key=key, trajectories=[
            make_trajectory(f"task{i:03d}") for i in range(count)])

    def test_cases_carry_origin_trace_id(self):
        cases = C.cases_from_trace_set(self._trace_set())
        assert len(cases) == 3
        assert all(c.origin_trace_id for c in cases)
        assert all(c.kind == C.CASE_KIND_TRACE for c in cases)

    def test_trace_case_contract_valid(self):
        for case in C.cases_from_trace_set(self._trace_set()):
            assert case.validate() == []

    def test_side_effect_source_is_trace_when_recorded(self):
        cases = C.cases_from_trace_set(self._trace_set())
        assert cases[0].side_effects_source == C.SIDE_EFFECT_SOURCE_TRACE

    def test_side_effect_source_none_when_not_recorded(self):
        traj = make_trajectory("task000")
        for step in traj.steps:
            step.files_written = []
            step.external_calls = []
        from agent.digestion.models import TraceSet
        trace_set = TraceSet(key=traj.key, trajectories=[traj])
        cases = C.cases_from_trace_set(trace_set)
        assert cases[0].side_effects_source == C.SIDE_EFFECT_SOURCE_NONE

    def test_negative_trajectories_excluded_by_default(self):
        from agent.digestion.models import TraceSet
        traj = make_trajectory("task-ng", failing=True)
        trace_set = TraceSet(key=traj.key, trajectories=[traj])
        assert C.cases_from_trace_set(trace_set) == []
        included = C.cases_from_trace_set(trace_set, include_negative=True)
        assert len(included) == 1
        assert included[0].expected_status == C.EXPECTED_STATUS_ANY

    def test_concrete_args_are_copied(self):
        """§3.1：判定集**复制数据**脱离 Trace 生命周期（保留参数语义内容）"""
        pairs = [("list_dir", {"path": "C:/repo/proj000"}),
                 ("read_file", {"path": "C:/repo/proj000/tests/test_a.py",
                                "encoding": "utf-8"}),
                 ("shell_execute", {"cmd": "pytest tests -q"}),
                 ("write_file", {"path": "C:/repo/proj000/out/report.md",
                                 "content": "report 000"})]
        cases = C.cases_from_trace_set(self._trace_set(1),
                                       concrete_args=lambda traj: pairs)
        case = cases[0]
        assert "tests" in case.input["path"]
        assert case.bindings["content"] == "report 000"
        assert case.provenance["concrete_args"] is True
        # 沙箱根覆盖该用例全部具体路径（逐例推导；沙箱只把路径当键，无真实 I/O）
        assert case.input["path"].startswith(case.sandbox_root + "/")
        assert all(p.startswith(case.sandbox_root + "/") for p in case.fixtures)

    def test_alignment_skips_noise_prefix_rows(self):
        """清洗会削掉探索前缀 ⇒ 按下标硬对会错位（实现期实测缺陷的回归）"""
        pairs = [("list_dir", {"path": "C:/noise"}),
                 ("read_file", {"path": "C:/repo/tests/test_a.py"}),
                 ("shell_execute", {"cmd": "pytest"}),
                 ("write_file", {"path": "C:/repo/out.md"})]
        cases = C.cases_from_trace_set(self._trace_set(1),
                                       concrete_args=lambda traj: pairs)
        assert cases[0].input["path"] == "C:/repo/tests/test_a.py"

    def test_align_concrete_args_reports_unmatched_as_empty(self):
        steps = [TrajectoryStep(seq=1, label="grep"), TrajectoryStep(seq=2, label="x")]
        pairs = [("grep", {"pattern": "p"})]
        assert C.align_concrete_args(steps, pairs) == [{"pattern": "p"}, {}]

    def test_slot_names_mirror_generalize_rule(self):
        """槽名镜像必须与 S3-01 的 `infer_parameter_slots` 逐值一致（防漂移）"""
        steps = [[("read_file", {"path": "${path}", "encoding": "utf-8"}),
                  ("shell_execute", {"cmd": "${cmd}"}),
                  ("write_file", {"cmd": "${cmd}", "content": "${content}"})]]
        slots = infer_parameter_slots(steps)
        mirrored = C.slot_names_for_program([C.ProgramStep(label=label, params=params)
                                             for label, params in steps[0]])
        for slot in slots:
            assert slot.name in mirrored[slot.step_label] or \
                slot.name in [n for names in mirrored.values() for n in names]

    def test_literal_values_are_preserved_not_synthesized(self):
        """字面量被"合成"会让候选臂与上游臂传不同的值 ⇒ 硬性层误红"""
        from agent.digestion.models import TraceSet
        traj = make_trajectory("t-literal")
        traj.steps[0].params = {"encoding": "utf-8"}
        trace_set = TraceSet(key=traj.key, trajectories=[traj])
        case = C.cases_from_trace_set(trace_set)[0]
        assert case.input["encoding"] == "utf-8"

    def test_limit_applies(self):
        assert len(C.cases_from_trace_set(self._trace_set(5), limit=2)) == 2

    def test_derive_sandbox_root(self):
        assert C.derive_sandbox_root(["C:/repo/a/x.py", "C:/repo/b/y.py"]) == "C:/repo"
        assert C.derive_sandbox_root([]) == C.DEFAULT_SANDBOX_ROOT
        assert C.derive_sandbox_root(["relative/x.py"]) == C.DEFAULT_SANDBOX_ROOT

    def test_trace_set_for_returns_none_on_broken_service(self):
        class Boom:
            def collect(self, _cid, limit=200):
                raise RuntimeError("ledger down")

        assert C.trace_set_for(CAP, service=Boom()) is None


# ════════════════════════════════════════════════════════════
#  5. 通道 ① / ③：Seed Pack、LLM/人工合并、失效重生成
# ════════════════════════════════════════════════════════════


class TestSeedAndMerge:
    def test_seed_pack_meets_p7_2_23(self):
        summary = C.seed_pack_summary()
        assert summary["meets_p7_2_23"] is True
        assert summary["skills"] >= C.SEED_PACK_MIN_SKILLS
        assert summary["skills_below_min"] == []

    def test_seed_cases_carry_provenance(self):
        for case in C.seed_cases_for("cp.builtin.write_file"):
            assert case.provenance["kind"] == C.PROV_SEED_PACK
            assert case.provenance["ref"]
            assert case.validate() == []

    def test_seed_case_can_override_native_program(self):
        cases = C.seed_cases_for("cp.skill.engineering-test-delivery")
        overridden = [c for c in cases if c.native]
        assert overridden, "至少一组用例带用例级候选实现覆盖"
        assert C.seed_candidate_for(overridden[0]) == overridden[0].native

    def test_seed_native_template_present_for_every_skill(self):
        for capability_id in C.seed_pack_capability_ids():
            assert C.seed_native_program(capability_id), capability_id

    def test_seed_case_sets_built_per_skill(self):
        sets = C.seed_pack_case_sets()
        assert len(sets) == len(C.seed_pack_capability_ids())
        assert all(s.size >= C.MIN_SEED_CASES_PER_SKILL for s in sets)

    def test_seed_pack_missing_file_is_advisory(self, tmp_path):
        C.reset_seed_pack_cache()
        payload = C.load_seed_pack(str(tmp_path / "absent.json"))
        assert payload["skills"] == []
        assert C.seed_pack_summary(str(tmp_path / "absent.json"))["meets_p7_2_23"] is False
        C.reset_seed_pack_cache()

    def test_merge_priority_prefers_first_group(self):
        seed = make_case("same", kind=C.CASE_KIND_SEED)
        llm = make_case("same", kind=C.CASE_KIND_LLM)
        other = make_case("other", kind=C.CASE_KIND_LLM)
        merged = C.merge_cases([seed], [llm, other])
        assert [c.case_id for c in merged] == ["same", "other"]
        assert merged[0].kind == C.CASE_KIND_SEED

    def test_llm_channel_case_is_valid(self):
        case = make_case("llm-1", kind=C.CASE_KIND_LLM)
        assert case.validate() == []
        assert case.provenance["kind"] == C.CASE_KIND_LLM

    def test_regenerate_bumps_version_and_backfills_seed(self, tmp_path):
        store = C.JsonCaseStore(str(tmp_path / "cases"))
        store.save(C.build_case_set(CAP, [make_case("old")], version=1))
        new_set = C.regenerate_case_set(CAP, store=store,
                                        trace_set=None, seed_backfill=True)
        assert new_set.version == 2
        assert new_set.regeneration_count == 1
        assert any(c.kind == C.CASE_KIND_SEED for c in new_set.cases)

    def test_regenerate_refuses_empty_set(self, tmp_path):
        store = C.JsonCaseStore(str(tmp_path / "cases"))
        assert C.regenerate_case_set("cp.absent.tool", store=store,
                                     seed_backfill=False) is None

    def test_case_set_for_capability_is_idempotent(self, tmp_path):
        store = C.JsonCaseStore(str(tmp_path / "cases"))
        first = C.case_set_for_capability(CAP, store=store)
        second = C.case_set_for_capability(CAP, store=store)
        assert first.version == second.version == 1

    def test_concrete_args_provider_returns_none_on_bad_store(self):
        provider = C.concrete_args_provider(object(), CAP)
        assert provider(make_trajectory("t")) is None

    def test_pattern_capability_id(self):
        from agent.digestion.models import CandidatePattern
        pattern = CandidatePattern(pattern_id="p", key=SameTaskKey(
            capability_id=CAP, intent_key="k", outcome="success"))
        assert C.pattern_capability_id(pattern) == CAP

    def test_slug_and_case_json_are_filesystem_safe(self):
        assert C.slug_of("cp.builtin.read_file") == "cp.builtin.read_file"
        assert C.slug_of("cp/weird:name") == "cp_weird_name"


class TestSeedPackAsset:
    def test_seed_pack_json_is_parsable_and_versioned(self):
        payload = C.load_seed_pack()
        assert payload["schema_version"] == C.CASE_SCHEMA_VERSION
        assert payload["source"]

    def test_every_seed_case_has_title_and_intent(self):
        for skill in C.seed_pack_skills():
            for case in skill["cases"]:
                assert case["title"] and case["intent_key"]

    def test_seed_pack_cases_are_json_only_data(self):
        with open(C.SEED_PACK_PATH, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        assert isinstance(payload["skills"], list) and payload["skills"]
