"""TASK-S3-02 验收门与漂移重探单测（`agent/digestion/gate.py`）

验收对应：
- 验收门**四条件**齐才发通行证；四条各自可单独失败（失败清单化）；
- 通行证 → `digest.stage` 事件 + 链式审计；凭通行证推进 `mirrored → shadow`；
- **无通行证时 stage 行为与 S3-01 逐字一致**（回归保护）；
- 破坏性分支覆盖（含参数级条件 —— S3-01 遗留 #4 扩展点）；
- 漂移重探：30 天 / 上游版本变化 / 探针失败 → drifted → 失效 → 重生成。

审计链 / 事件目录 / 台账 / 判定集存储全部隔离到 tmp_path。
"""

from __future__ import annotations

import json

import pytest

from agent.audit import facade as facade_mod
from agent.audit.chain import AuditChain
from agent.digestion import cases as C
from agent.digestion import gate as G
from agent.digestion import sandbox as S
from agent.digestion import stage as ST
from agent.digestion.models import (
    VERDICT_APPLIED,
    VERDICT_DEFERRED,
    VERDICT_INSUFFICIENT_EVIDENCE,
    BranchCondition,
    CandidatePattern,
    PatternStep,
    SameTaskKey,
)

CAP = "cp.builtin.read_file"


# ════════════════════════════════════════════════════════════
#  隔离 fixtures
# ════════════════════════════════════════════════════════════


@pytest.fixture
def chain(tmp_path):
    c = AuditChain(str(tmp_path / "audit.db"),
                   roots_path=str(tmp_path / "roots.jsonl"),
                   signing_key_path=str(tmp_path / "k.pem"), auto_seal=False)
    yield c
    c.close(timeout=2.0)


@pytest.fixture(autouse=True)
def isolated_audit(chain):
    previous = facade_mod.audit.bind(chain)
    old_enabled = facade_mod.audit.enabled
    facade_mod.audit.enabled = True
    facade_mod.audit.reset_counters()
    yield facade_mod.audit
    facade_mod.audit.bind(previous)
    facade_mod.audit.enabled = old_enabled


@pytest.fixture(autouse=True)
def isolated_events(tmp_path, monkeypatch):
    monkeypatch.setenv("CP_EVENTS_DIR", str(tmp_path / "events"))
    import agent.observability.events as events_mod
    events_mod.reset_event_stores()
    yield str(tmp_path / "events")
    events_mod.reset_event_stores()


@pytest.fixture(autouse=True)
def isolated_case_root(tmp_path, monkeypatch):
    """判定集/通行证的**默认落点**也隔离到 tmp_path

    为什么必须 autouse：`acceptance_gate` / `advance_to_shadow` / `PassportStore()` /
    `open_case_store()` 的默认根都是 ``CP_DIGESTION_CASE_DIR``（生产上落在
    `data/digestion/cases/`）。只要有一个用例忘了显式传 store，就会把通行证写进
    **运行时目录**并污染同一文件内的后续用例（实现期实测：一次断言失败让"上一张通行证"
    被后测读到）。故在**会话层**兜住默认落点，而不是靠每个用例自觉传参。
    """
    monkeypatch.setenv(C.CASE_ROOT_ENV, str(tmp_path / "cases"))
    yield str(tmp_path / "cases")


@pytest.fixture
def store(tmp_path):
    return C.JsonCaseStore(str(tmp_path / "cases"))


def events_of(event_type: str, directory: str):
    import agent.observability.events as events_mod
    return [e for e in events_mod.iter_events(directory=directory)
            if e.type == event_type]


# ════════════════════════════════════════════════════════════
#  构造工具
# ════════════════════════════════════════════════════════════


def step_case(index: int, *, path: str | None = None,
              failing: bool = False) -> C.EquivalenceCase:
    """一条三步任务链用例（read → shell → write），与演示的候选骨架同构"""
    base = "C:/sandbox"
    read_path = path or f"{base}/tests/test_mod{index:03d}.py"
    report = f"{base}/out/report{index:03d}.md"
    upstream = [C.ProgramStep(label="read_file",
                              params={"path": read_path, "encoding": "utf-8"}),
                C.ProgramStep(label="shell_execute",
                              params={"cmd": f"pytest tests/test_mod{index:03d}.py -q"})]
    if not failing:
        upstream.append(C.ProgramStep(label="write_file",
                                      params={"path": report,
                                              "content": f"report {index:03d}"}))
    case = C.EquivalenceCase(
        case_id=f"case-{index:03d}", capability_id=CAP,
        input={"path": read_path, "encoding": "utf-8"},
        bindings={"cmd": f"pytest tests/test_mod{index:03d}.py -q",
                  "content": f"report {index:03d}"},
        upstream=upstream,
        fixtures={read_path: f"# fixture {index}\n"},
        expected_output_schema={"ok": "bool", "path": "str", "bytes": "number"},
        expected_side_effects={"files_written": ([report] if not failing else []),
                               "external_calls": ([] if failing
                                                  else ["shell_execute"])},
        expected_status=("success" if not failing else "error"),
        side_effects_source=C.SIDE_EFFECT_SOURCE_AUTHORED,
        kind=C.CASE_KIND_TRACE, origin_trace_id=f"tr-{index:03d}")
    return case


def step_pattern(*, with_condition: str = "") -> CandidatePattern:
    key = SameTaskKey(capability_id=CAP, intent_key="read+file",
                      outcome="success")
    branches = []
    if with_condition:
        branches.append(BranchCondition(at_step=1, condition=with_condition,
                                        support=3, total=7, outcome="failure"))
    return CandidatePattern(
        pattern_id="pat-demo", key=key, support=25, sample_size=28, coverage=1.0,
        lcs_length=3, confidence=0.95, branches=branches,
        steps=[PatternStep(seq=1, label="read_file",
                           capability_id=CAP, params={"path": "${path}"}),
               PatternStep(seq=2, label="shell_execute",
                           capability_id="cp.builtin.shell_execute",
                           params={"cmd": "${cmd}"}),
               PatternStep(seq=3, label="write_file",
                           capability_id="cp.builtin.write_file",
                           params={"content": "${content}"})])


def make_case_set(count: int = C.MIN_CASE_SET_SIZE, *, failing: int = 0):
    cases = [step_case(i) for i in range(count - failing)]
    cases.extend(step_case(100 + i, failing=True) for i in range(failing))
    return C.build_case_set(CAP, cases)


# ════════════════════════════════════════════════════════════
#  1. 验收门四条件
# ════════════════════════════════════════════════════════════


class TestAcceptanceGateConditions:
    def test_all_four_conditions_pass_and_issue_passport(self, store):
        case_set = make_case_set()
        store.save(case_set)
        result = G.acceptance_gate(CAP, case_set=case_set, store=store,
                                   candidate=step_pattern(),
                                   passport_store=G.PassportStore(store.root))
        assert result.passed is True
        assert result.failed_conditions == []
        assert [c.name for c in result.conditions] == list(G.GATE_CONDITIONS)
        assert result.passport["passed"] is True
        assert result.passport["executed"] == C.MIN_CASE_SET_SIZE
        assert result.passport["required_replays"] == G.GATE_REPLAY_MIN
        assert result.audit_seq > 0 and result.event_id

    def test_condition1_fails_below_twenty_replays(self, store):
        case_set = make_case_set(count=G.GATE_REPLAY_MIN - 1)
        result = G.acceptance_gate(CAP, case_set=case_set, candidate=step_pattern())
        c1 = result.condition(G.COND_REPLAY)
        assert result.passed is False and c1.passed is False
        assert "小于" in c1.reasons[0] or "<" in c1.reasons[0]

    def test_condition1_fails_when_a_replay_fails(self, store):
        case_set = make_case_set()
        broken = S.PatternImplementation(step_pattern(), drop_steps=(2,))
        result = G.acceptance_gate(CAP, case_set=case_set, candidate=broken)
        c1 = result.condition(G.COND_REPLAY)
        assert c1.passed is False
        assert "回放未全过" in c1.reasons[0]
        assert c1.detail["failed"] == case_set.size
        assert result.failure_list()[0]["failed_layers"]

    def test_condition2_fails_on_success_rate(self, store):
        case_set = make_case_set()
        result = G.acceptance_gate(
            CAP, case_set=case_set,
            candidate=S.PatternImplementation(step_pattern(), drop_steps=(2,)),
            baseline={"success_rate": 1.0, "p99": 100.0})
        c2 = result.condition(G.COND_SUCCESS_RATE)
        assert c2.passed is False and "成功率" in c2.reasons[0]

    def test_condition3_fails_when_p99_regresses(self, store):
        case_set = make_case_set()
        result = G.acceptance_gate(CAP, case_set=case_set,
                                   candidate=step_pattern(),
                                   baseline={"success_rate": 1.0, "p99": 1.0})
        c3 = result.condition(G.COND_P99)
        assert c3.passed is False and "p99" in c3.reasons[0]

    def test_condition4_fails_on_uncovered_branch(self, store):
        case_set = make_case_set()
        pattern = step_pattern(with_condition="cmd contains force-recreate")
        result = G.acceptance_gate(CAP, case_set=case_set, candidate=pattern,
                                   pattern=pattern)
        c4 = result.condition(G.COND_BRANCH)
        assert c4.passed is False and "未覆盖破坏性分支" in c4.reasons[0]

    def test_condition4_passes_when_covered(self, store):
        case_set = make_case_set()
        pattern = step_pattern()
        result = G.acceptance_gate(
            CAP, case_set=case_set, candidate=pattern, pattern=pattern,
            extra_conditions=[BranchCondition(at_step=1,
                                              condition="path contains test",
                                              support=3, total=7,
                                              outcome="failure")])
        assert result.condition(G.COND_BRANCH).passed is True

    def test_no_case_set_returns_explicit_result(self):
        result = G.acceptance_gate(CAP, store=None, case_set=None)
        assert result.passed is False
        assert "no_case_set" in result.note
        assert all(not c.passed for c in result.conditions)

    def test_no_active_cases_returns_explicit_result(self):
        case_set = make_case_set()
        case_set.invalidate("上游契约漂移")
        result = G.acceptance_gate(CAP, case_set=case_set)
        assert result.passed is False and "no_active_cases" in result.note

    def test_explicit_baseline_is_used_verbatim(self, store):
        case_set = make_case_set()
        result = G.acceptance_gate(CAP, case_set=case_set,
                                   candidate=step_pattern(),
                                   baseline={"success_rate": 1.0, "p99": 999.0,
                                             "source": "s3-03_shadow"})
        assert result.baseline_source == "s3-03_shadow"
        assert result.baseline["p99"] == 999.0

    def test_default_baseline_is_upstream_arm_not_ledger(self, store):
        """台账统计只作披露：阈值取同一回放系统内的上游臂（同量纲可比）"""
        case_set = make_case_set()

        class Ledger:
            def query(self, capability_id=None, limit=None):
                class Row:
                    response = type("R", (), {"status": "error"})()
                    timing = type("T", (), {"duration_ms": 1.0})()
                return [Row() for _ in range(50)]

        result = G.acceptance_gate(CAP, case_set=case_set,
                                   candidate=step_pattern(),
                                   baseline_store=Ledger())
        assert result.baseline_source == G.BASELINE_SOURCE_SANDBOX
        assert result.baseline["success_rate"] == 1.0
        assert result.baseline["ledger"]["success_rate"] == 0.0

    def test_gate_result_is_json_serializable(self, store):
        result = G.acceptance_gate(CAP, case_set=make_case_set(),
                                   candidate=step_pattern())
        payload = json.loads(json.dumps(result.to_dict(), default=str))
        assert payload["capability_id"] == CAP
        assert len(payload["conditions"]) == 4

    def test_candidate_provider_is_supported(self, store):
        case_set = make_case_set()
        provider = lambda _case: step_pattern()
        result = G.acceptance_gate(CAP, case_set=case_set, candidate=provider)
        assert result.passed is True and "provider" in result.note

    def test_seed_native_candidate_is_default(self, store):
        case_set = C.build_case_set(
            "cp.builtin.read_file", C.seed_cases_for("cp.builtin.read_file"))
        result = G.acceptance_gate("cp.builtin.read_file", case_set=case_set,
                                   write_passport=False)
        assert "seed_pack_native" in result.note
        assert result.passed is False          # 起步集只有 4 组 < 20


# ════════════════════════════════════════════════════════════
#  2. 通行证与 stage 联动
# ════════════════════════════════════════════════════════════


class TestPassportAndStage:
    def test_passport_store_round_trip(self, store):
        passport_store = G.PassportStore(store.root)
        result = G.acceptance_gate(CAP, case_set=make_case_set(),
                                   candidate=step_pattern(),
                                   passport_store=passport_store)
        latest = passport_store.latest(CAP)
        assert latest["passport_id"] == result.passport["passport_id"]
        assert passport_store.history(CAP)[0]["passed"] is True
        assert passport_store.latest("cp.absent") is None

    def test_passport_emits_digest_stage_event(self, store, isolated_events):
        result = G.acceptance_gate(CAP, case_set=make_case_set(),
                                   candidate=step_pattern())
        events = events_of("digest.stage", isolated_events)
        assert events and events[-1].payload["scope"] == G.EVENT_SCOPE_GATE
        assert events[-1].payload["verdict"] == G.VERDICT_PASSPORT_GRANTED
        assert events[-1].payload["passport_id"] == result.passport["passport_id"]

    def test_passport_is_audited(self, store):
        result = G.acceptance_gate(CAP, case_set=make_case_set(),
                                   candidate=step_pattern())
        assert result.audit_seq > 0 and result.audit_hash
        rows = facade_mod.audit.recent(limit=10, action=G.AUDIT_ACTION_GRANTED)
        assert rows and rows[-1].subject == f"capability:{CAP}"

    def test_no_passport_when_gate_fails(self, store, isolated_events):
        result = G.acceptance_gate(CAP, case_set=make_case_set(count=3),
                                   candidate=step_pattern())
        assert result.passed is False and result.passport == {}
        assert events_of("digest.stage", isolated_events) == []

    def test_failed_passport_is_not_stored(self, store):
        passport_store = G.PassportStore(store.root)
        G.acceptance_gate(CAP, case_set=make_case_set(count=3),
                          candidate=step_pattern(), passport_store=passport_store)
        assert passport_store.latest(CAP) is None

    def test_stage_gate_allows_shadow_with_passport(self):
        passport = {"passed": True, "capability_id": CAP, "executed": 30,
                    "required_replays": 20, "case_set_version": 1,
                    "conditions": [{"name": n, "passed": True}
                                   for n in G.GATE_CONDITIONS]}
        verdict, reasons = ST.evaluate_migration(
            capability_id=CAP, from_stage="mirrored", to_stage="shadow",
            evidence={ST.ACCEPTANCE_PASSPORT_KEY: passport})
        assert verdict == VERDICT_APPLIED
        assert "验收门通行证" in reasons[0]

    def test_stage_gate_without_passport_is_unchanged(self):
        """S3-01 行为保持：不带通行证键时仍返回 deferred（既有调用方零影响）"""
        verdict, reasons = ST.evaluate_migration(
            capability_id=CAP, from_stage="mirrored", to_stage="shadow",
            evidence={"trace_count": 30, "pattern_extracted": True,
                      "side_effect_profile": {"files_written_count": 3}})
        assert verdict == VERDICT_DEFERRED
        assert "S3-02" in reasons[0] or "S3-03" in reasons[0]

    def test_stage_gate_rejects_incomplete_passport(self):
        verdict, reasons = ST.evaluate_migration(
            capability_id=CAP, from_stage="mirrored", to_stage="shadow",
            evidence={ST.ACCEPTANCE_PASSPORT_KEY: {"passed": True}})
        assert verdict == VERDICT_INSUFFICIENT_EVIDENCE
        assert any("capability" in r or "conditions" in r for r in reasons)

    def test_passport_self_consistency_checks(self):
        base = {"passed": True, "capability_id": CAP, "executed": 20,
                "required_replays": 20, "case_set_version": 1,
                "conditions": [{"name": n, "passed": True}
                               for n in G.GATE_CONDITIONS]}
        assert ST.acceptance_passport_ok(CAP, {"acceptance_passport": base})[0]
        wrong_cap = dict(base, capability_id="cp.other")
        assert not ST.acceptance_passport_ok(CAP, {"acceptance_passport": wrong_cap})[0]
        too_few = dict(base, executed=3)
        assert not ST.acceptance_passport_ok(CAP, {"acceptance_passport": too_few})[0]
        no_required = dict(base, required_replays=0)
        assert not ST.acceptance_passport_ok(CAP, {"acceptance_passport": no_required})[0]
        failed_cond = dict(base, conditions=[{"name": G.COND_P99, "passed": False}])
        assert not ST.acceptance_passport_ok(CAP, {"acceptance_passport": failed_cond})[0]
        no_version = dict(base, case_set_version=0)
        assert not ST.acceptance_passport_ok(CAP, {"acceptance_passport": no_version})[0]
        assert not ST.acceptance_passport_ok(CAP, {})[0]

    def test_advance_to_shadow_writes_registry_and_audit(self, store):
        from agent.descriptors.registry import DescriptorRegistry
        from agent.descriptors.bridge import register_bridge_view

        reg = DescriptorRegistry(path=str(store.root) + "/d.json", autosave=False)
        register_bridge_view(reg, "builtin",
                            [{"name": "read_file", "description": "r"}])
        reg.set_stage(CAP, "mirrored")
        result = G.acceptance_gate(CAP, case_set=make_case_set(),
                                   candidate=step_pattern(),
                                   passport_store=G.PassportStore(store.root),
                                   emit_events=False)
        migration = G.advance_to_shadow(CAP, result, registry=reg,
                                        emit_event=False)
        assert migration.applied is True
        assert migration.from_stage == "mirrored"
        assert migration.audit_action == "descriptor.stage"
        assert reg.get(CAP).evolution.stage.value == "shadow"

    def test_advance_without_passport_does_not_advance(self, store):
        from agent.descriptors.registry import DescriptorRegistry
        from agent.descriptors.bridge import register_bridge_view

        reg = DescriptorRegistry(path=str(store.root) + "/d2.json", autosave=False)
        register_bridge_view(reg, "builtin",
                            [{"name": "read_file", "description": "r"}])
        reg.set_stage(CAP, "mirrored")
        empty = G.GateResult(capability_id=CAP)
        migration = G.advance_to_shadow(CAP, empty, registry=reg,
                                        passport_store=G.PassportStore(store.root),
                                        emit_event=False)
        assert migration.applied is False
        assert reg.get(CAP).evolution.stage.value == "mirrored"

    def test_acceptance_gate_can_advance_inline(self, store):
        from agent.descriptors.registry import DescriptorRegistry
        from agent.descriptors.bridge import register_bridge_view

        reg = DescriptorRegistry(path=str(store.root) + "/d3.json", autosave=False)
        register_bridge_view(reg, "builtin",
                            [{"name": "read_file", "description": "r"}])
        reg.set_stage(CAP, "mirrored")
        result = G.acceptance_gate(CAP, case_set=make_case_set(),
                                   candidate=step_pattern(), registry=reg,
                                   passport_store=G.PassportStore(store.root),
                                   advance=True, emit_events=False)
        assert result.migration is not None and result.migration.applied is True
        assert result.passport["from_stage"] == "mirrored"
        assert result.passport["to_stage"] == "shadow"
        assert result.passport["stage_applied"] is True


# ════════════════════════════════════════════════════════════
#  3. 破坏性分支覆盖（条件 4 底座）
# ════════════════════════════════════════════════════════════


class TestBranchCoverage:
    def test_no_requirements_means_pass(self):
        coverage = G.branch_coverage(None, [step_case(1)])
        assert coverage["required"] == 0 and coverage["passed"] is True

    def test_failure_branch_is_a_requirement(self):
        required = G.required_branches(step_pattern(with_condition="x == 1"))
        assert required and required[0].kind == G.BRANCH_KIND_FAILURE_PRONE

    def test_destructive_keyword_kind(self):
        branch = BranchCondition(at_step=1, condition="cmd contains --force",
                                 support=1, total=3, outcome="success")
        required = G.required_branches(None, extra_conditions=[branch])
        assert required[0].kind == G.BRANCH_KIND_DESTRUCTIVE

    def test_structural_branches_are_observed_not_required(self):
        branch = BranchCondition(at_step=3, condition="步骤 `write_file` 缺失",
                                 support=4, total=8, outcome="failure")
        coverage = G.branch_coverage(None, [step_case(1)],
                                     extra_conditions=[branch])
        assert coverage["required"] == 0 and coverage["passed"] is True
        assert coverage["observed_count"] == 1
        assert coverage["observed"][0]["coverage_kind"] == "structural"

    def test_param_branch_covered_by_case_bindings(self):
        coverage = G.branch_coverage(
            None, [step_case(1)], extra_conditions=[
                BranchCondition(at_step=1, condition="path contains test",
                                support=2, total=6, outcome="failure")])
        assert coverage["passed"] is True
        assert coverage["requirements"][0]["covered_by"] == ["case-001"]

    def test_param_branch_uncovered_is_reported(self):
        coverage = G.branch_coverage(
            None, [step_case(1)], extra_conditions=[
                BranchCondition(at_step=1, condition="cmd contains force",
                                support=2, total=6, outcome="failure")])
        assert coverage["passed"] is False
        assert coverage["uncovered"][0]["condition"] == "cmd contains force"

    def test_case_branch_tags_cover_requirement(self):
        case = step_case(1)
        case.branch_tags = ["cmd contains force"]
        coverage = G.branch_coverage(None, [case], extra_conditions=[
            BranchCondition(at_step=1, condition="cmd contains force",
                            support=1, total=5, outcome="failure")])
        assert coverage["passed"] is True

    def test_duplicate_conditions_are_deduped(self):
        branch = BranchCondition(at_step=1, condition="path contains test",
                                 support=1, total=2, outcome="failure")
        required = G.required_branches(None, extra_conditions=[branch, branch])
        assert len(required) == 1

    def test_string_and_dict_conditions_accepted(self):
        required = G.required_branches(
            None, extra_conditions=["cmd contains --force",
                                    {"condition": "mode == force",
                                     "outcome": "failure"}])
        assert {r.condition for r in required} == {"cmd contains --force",
                                                   "mode == force"}

    def test_unknown_atoms_surface_in_requirement(self):
        coverage = G.branch_coverage(None, [step_case(1)], extra_conditions=[
            BranchCondition(at_step=1, condition="莫名其妙的断言", support=1,
                            total=2, outcome="failure")])
        assert coverage["passed"] is False
        assert coverage["uncovered"][0]["unknown_atoms"] == ["莫名其妙的断言"]

    def test_include_failure_prone_can_be_disabled(self):
        branch = BranchCondition(at_step=1, condition="x == 1", support=1,
                                 total=2, outcome="failure")
        assert G.required_branches(None, extra_conditions=[branch],
                                  include_failure_prone=False) == []

    def test_case_condition_context_merges_input_and_bindings(self):
        ctx = G.case_condition_context(step_case(1))
        assert ctx.params["path"].endswith("test_mod001.py")
        assert ctx.params["cmd"].startswith("pytest")
        assert ctx.step_count == 3


# ════════════════════════════════════════════════════════════
#  4. 漂移重探
# ════════════════════════════════════════════════════════════


class TestDriftReprobe:
    def test_probe_sample_is_deterministic_and_bounded(self):
        ids = [f"c{i:03d}" for i in range(40)]
        first = G.probe_sample_ids(ids)
        assert first == G.probe_sample_ids(ids)
        assert len(first) == G.REPROBE_PROBE_SIZE
        assert G.probe_sample_ids([], size=3) == []
        assert len(G.probe_sample_ids(["a"], size=5)) == 1

    def test_due_for_reprobe_age(self):
        case_set = make_case_set()
        now = case_set.created_at
        assert G.due_for_reprobe(case_set, now=now) is False
        later = now + (G.REPROBE_INTERVAL_DAYS + 1) * G.DAY_SECONDS
        assert G.due_for_reprobe(case_set, now=later) is True

    def test_due_when_never_probed_and_no_created_at(self):
        case_set = C.CaseSet(capability_id=CAP, cases=[step_case(1)])
        case_set.created_at = 0.0
        assert G.due_for_reprobe(case_set, now=1.0) is True

    def test_reprobe_clean_probe_is_not_drifted(self, store):
        case_set = make_case_set()
        store.save(case_set)
        report = G.reprobe(CAP, trigger=G.DRIFT_TRIGGER_AGE, store=store,
                           candidate=step_pattern(), emit_events=False)
        assert report.verdict == G.VERDICT_PROBE_OK
        assert report.drifted is False
        assert report.probes and not report.probe_failures
        assert store.load(CAP).active is True

    def test_reprobe_upstream_version_change_drifts(self, store):
        case_set = make_case_set()
        store.save(case_set)
        report = G.reprobe(CAP, trigger=G.DRIFT_TRIGGER_UPSTREAM, store=store,
                           candidate=step_pattern(), emit_events=False)
        assert report.drifted is True and report.schema_changed is True
        assert "上游版本变化" in "；".join(report.reasons)
        assert report.invalidated_version == 1

    def test_reprobe_probe_failure_drifts(self, store):
        case_set = make_case_set()
        store.save(case_set)
        report = G.reprobe(CAP, trigger=G.DRIFT_TRIGGER_AGE, store=store,
                           candidate=S.PatternImplementation(step_pattern(),
                                                             drop_steps=(2,)),
                           emit_events=False)
        assert report.drifted is True
        assert report.probe_failures
        assert "探针回放失败" in report.reasons[0]

    def test_reprobe_invalidates_and_regenerates(self, store):
        store.save(make_case_set())
        report = G.reprobe(CAP, trigger=G.DRIFT_TRIGGER_SCHEMA, store=store,
                           candidate=step_pattern(), emit_events=False)
        assert report.invalidated_version == 1
        assert report.regenerated_version == 2
        assert report.regenerated_cases >= 1
        invalidated = store.load(CAP, version=1)
        assert invalidated.active is False and invalidated.drifted is True
        latest = store.load(CAP, version=2)
        assert latest.active is True and latest.regeneration_count == 1
        assert latest.drift_details["regenerated_from_version"] == 1

    def test_reprobe_regenerate_false_keeps_it_invalid(self, store):
        store.save(make_case_set())
        report = G.reprobe(CAP, trigger=G.DRIFT_TRIGGER_SCHEMA, store=store,
                           candidate=step_pattern(), regenerate=False,
                           emit_events=False)
        assert report.regenerated_version == 0
        assert store.load(CAP).active is False

    def test_reprobe_missing_case_set(self, store):
        report = G.reprobe("cp.absent.tool", store=store, emit_events=False)
        assert report.verdict == "not_found"
        assert report.reasons

    def test_reprobe_detects_descriptor_schema_change(self, store):
        case_set = make_case_set()
        case_set.upstream_version = "1.0.0"
        case_set.upstream_schema = {"output_schema": {"type": "object"}}
        store.save(case_set)

        class Reg:
            def get(self, _cid):
                meta = type("M", (), {"version": "2.0.0"})()
                cap = type("C", (), {"input_schema": None,
                                     "output_schema": {"type": "array"}})()
                return type("D", (), {"meta": meta, "capability": cap})()

        report = G.reprobe(CAP, trigger=G.DRIFT_TRIGGER_AGE, store=store,
                           candidate=step_pattern(), registry=Reg(),
                           emit_events=False)
        assert report.schema_changed is True
        assert report.upstream_version_after == "2.0.0"
        assert any("版本变化" in r for r in report.reasons)

    def test_reprobe_emits_event_and_audit(self, store, isolated_events):
        store.save(make_case_set())
        report = G.reprobe(CAP, trigger=G.DRIFT_TRIGGER_UPSTREAM, store=store,
                           candidate=step_pattern())
        events = events_of("digest.stage", isolated_events)
        assert events and events[-1].payload["scope"] == G.EVENT_SCOPE_DRIFT
        assert events[-1].payload["verdict"] == G.VERDICT_DRIFTED
        rows = facade_mod.audit.recent(limit=10, action=G.AUDIT_ACTION_DRIFTED)
        assert rows and rows[-1].payload.get("status") == G.VERDICT_DRIFTED
        assert report.audit_seq > 0

    def test_reprobe_report_is_json_serializable(self, store):
        store.save(make_case_set())
        report = G.reprobe(CAP, trigger=G.DRIFT_TRIGGER_SCHEMA, store=store,
                           candidate=step_pattern(), emit_events=False)
        assert json.loads(json.dumps(report.to_dict(), default=str))["capability_id"] == CAP

    def test_register_reprobe_job_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv(G.REPROBE_ENABLE_ENV, raising=False)
        assert G.register_reprobe_job()["status"] == "disabled"

    def test_register_reprobe_job_schedules_when_enabled(self, store):
        class Sched:
            def __init__(self):
                self.tasks = []

            def add_interval_task(self, name, func, interval_seconds):
                self.tasks.append({"task_id": "t1", "name": name,
                                   "func": func, "interval": interval_seconds})

        sched = Sched()
        payload = G.register_reprobe_job(sched, store=store, enabled=True)
        assert payload["status"] == "scheduled"
        assert payload["interval_days"] == G.REPROBE_INTERVAL_DAYS
        assert sched.tasks[0]["interval"] == G.REPROBE_INTERVAL_DAYS * G.DAY_SECONDS
        assert sched.tasks[0]["func"]()["status"] == "ok"

    def test_register_reprobe_job_tolerates_scheduler_failure(self, monkeypatch):
        class Boom:
            def add_interval_task(self, name, func, interval_seconds):
                raise RuntimeError("no scheduler")

        with pytest.raises(RuntimeError):
            G.register_reprobe_job(Boom(), enabled=True)

    def test_scheduled_tick_swallows_store_errors(self, store):
        class BoomStore(C.JsonCaseStore):
            def list_capabilities(self):
                raise RuntimeError("store down")

        sched = type("S", (), {"tasks": [],
                               "add_interval_task": lambda self, n, func, interval_seconds:
                               self.tasks.append({"task_id": "t", "func": func})})()
        G.register_reprobe_job(sched, store=BoomStore(store.root), enabled=True)
        assert sched.tasks[0]["func"]()["status"] == "error"


# ════════════════════════════════════════════════════════════
#  5. 基线工具
# ════════════════════════════════════════════════════════════


class TestBaseline:
    def test_baseline_from_traces(self):
        class Row:
            def __init__(self, status, duration):
                self.response = type("R", (), {"status": status})()
                self.timing = type("T", (), {"duration_ms": duration})()

        payload = G.baseline_from_traces([Row("success", 3.0),
                                          Row("success", 5.0),
                                          Row("error", 2.0)])
        assert payload["success_rate"] == 0.6667
        assert payload["p99"] == 5.0
        assert payload["sample_count"] == 3
        assert payload["source"] == G.BASELINE_SOURCE_LEDGER

    def test_baseline_from_traces_empty(self):
        payload = G.baseline_from_traces([])
        assert payload["success_rate"] == 0.0 and payload["sample_count"] == 0

    def test_baseline_from_ledger_returns_none_without_rows(self):
        class Empty:
            def query(self, capability_id=None, limit=None):
                return []

        assert G.baseline_from_ledger(CAP, store=Empty()) is None

    def test_baseline_from_ledger_tolerates_failure(self):
        class Boom:
            def query(self, capability_id=None, limit=None):
                raise RuntimeError("db down")

        assert G.baseline_from_ledger(CAP, store=Boom()) is None

    def test_descriptor_view_is_explicit_only(self):
        """registry=None 不隐式读运行时台账（隔离纪律，与 baseline_store 同口径）"""
        assert G._descriptor_view(None, CAP) == {}

    def test_descriptor_view_reads_given_registry(self, store):
        class Reg:
            def get(self, _cid):
                meta = type("M", (), {"version": "9.9.9"})()
                cap = type("C", (), {"input_schema": {"a": 1},
                                     "output_schema": {"b": 2}})()
                return type("D", (), {"meta": meta, "capability": cap})()

        view = G._descriptor_view(Reg(), CAP)
        assert view["version"] == "9.9.9" and view["output_schema"] == {"b": 2}

    def test_descriptor_view_tolerates_get_failure(self):
        class Boom:
            def get(self, _cid):
                raise RuntimeError("ledger down")

        assert G._descriptor_view(Boom(), CAP) == {}

    def test_passport_id_is_deterministic(self):
        first = G.passport_id_for(CAP, case_set_version=1, digest="abc")
        assert first == G.passport_id_for(CAP, case_set_version=1, digest="abc")
        assert first != G.passport_id_for(CAP, case_set_version=2, digest="abc")
