"""TASK-S7-04 子项 B 单测 —— native 期探活设施（`agent/digestion/probe.py`）

验收对应（任务书 §四 子项 B 逐条）：

- `LivenessProbe.run()` 对 ``internalized``/``native`` 能力跑**判定集子集**并产出
  通过率 / **真实墙钟** p99 / 成本增量（口径逐项标注）；
- **四项退化条件各自有触发用例**；任一命中即 ``degraded``；
- 退化产出 ``native → borrowed`` **回退建议**，且 **不自动执行** stage 变更
  （用例断言台账 stage 未变、无 `set_stage` 调用）；
- 退化升级为**事故卡**（复用 `self_healing.levels.raise_incident`），六要素齐备；
  证据不足时**如实**保留缺失项（不得编造 commit 去凑齐）；
- `register_liveness_job()` **默认关闭**，需显式环境变量才注册；
- 无 ``internalized``/``native`` 能力时安全返回（**不报错、不编造**）；
- 阈值可配；周期类判定走**注入时钟**（不依赖真实墙钟）。

隔离：判定集 / 基线台账 / 事故卡目录 / 事件目录 / 审计链**全部**落 tmp_path
（autouse fixture 兜住默认落点 —— S3-02/S3-03 两次运行时目录污染的教训）。
"""

from __future__ import annotations

import pytest

from agent.audit import facade as facade_mod
from agent.audit.chain import AuditChain
from agent.digestion import cases as C
from agent.digestion import probe as P
from agent.digestion.sandbox import ProgramImplementation

CAP = "cp.builtin.read_file"
DAY = P.DAY_SECONDS


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
    facade_mod.audit.enabled = True
    facade_mod.audit.reset_counters()
    yield facade_mod.audit
    facade_mod.audit.bind(previous)


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    """判定集 / 探活基线 / 事故卡 / 事件的**默认落点**一律隔离（防运行时目录污染）"""
    monkeypatch.setenv("CP_EVENTS_DIR", str(tmp_path / "events"))
    monkeypatch.setenv(C.CASE_ROOT_ENV, str(tmp_path / "cases"))
    monkeypatch.setenv(P.LIVENESS_DIR_ENV, str(tmp_path / "liveness"))
    monkeypatch.setenv("CP_HEALING_INCIDENTS_DIR", str(tmp_path / "incidents"))
    monkeypatch.delenv(P.LIVENESS_ENABLE_ENV, raising=False)
    monkeypatch.delenv(P.PROBE_SIZE_ENV, raising=False)
    import agent.observability.events as events_mod
    events_mod.reset_event_stores()
    yield tmp_path
    events_mod.reset_event_stores()


@pytest.fixture
def store(tmp_path):
    return C.JsonCaseStore(str(tmp_path / "cases"))


@pytest.fixture
def baselines(tmp_path):
    return P.LivenessBaselineStore(str(tmp_path / "liveness" / "liveness_baseline.json"))


# ════════════════════════════════════════════════════════════
#  假台账 / 构造工具
# ════════════════════════════════════════════════════════════


class FakeDescriptor:
    """descriptor 叶子字段（只暴露探活读到的三项；不搬运整个真实对象）"""

    def __init__(self, capability_id: str, stage: str, version: str = "") -> None:
        self.capability_id = capability_id
        self.evolution = type("Evo", (), {"stage": stage})()
        self.meta = type("Meta", (), {"version": version})()


class FakeRegistry:
    """最小 descriptor 台账（记录 `set_stage` 调用 ⇒ 断言"未自动执行 stage 变更"）"""

    def __init__(self, stages=None, *, versions=None) -> None:
        self.stages = dict(stages or {})
        self.versions = dict(versions or {})
        self.set_stage_calls: list[tuple[str, str]] = []

    def get(self, capability_id):
        stage = self.stages.get(str(capability_id))
        if stage is None:
            return None
        return FakeDescriptor(str(capability_id), stage,
                              self.versions.get(str(capability_id), ""))

    def list_by_stage(self, stage):
        return [FakeDescriptor(cid, s, self.versions.get(cid, ""))
                for cid, s in sorted(self.stages.items()) if s == str(stage)]

    def set_stage(self, capability_id, stage, **_kw):
        self.set_stage_calls.append((str(capability_id), str(stage)))
        self.stages[str(capability_id)] = str(stage)


def step_case(index: int, *, failing: bool = False) -> C.EquivalenceCase:
    """一条三步任务链用例（read → shell → write），与 S3-02 判定集同构"""
    base = "C:/sandbox"
    read_path = f"{base}/tests/test_mod{index:03d}.py"
    report = f"{base}/out/report{index:03d}.md"
    upstream = [
        C.ProgramStep(label="read_file",
                      params={"path": read_path, "encoding": "utf-8"}),
        C.ProgramStep(label="shell_execute",
                      params={"cmd": f"pytest tests/test_mod{index:03d}.py -q"}),
        C.ProgramStep(label="write_file",
                      params={"path": report, "content": f"report {index:03d}"}),
    ]
    return C.EquivalenceCase(
        case_id=f"case-{index:03d}", capability_id=CAP,
        input={"path": read_path, "encoding": "utf-8"},
        bindings={"cmd": f"pytest tests/test_mod{index:03d}.py -q",
                  "content": f"report {index:03d}"},
        upstream=upstream,
        fixtures={read_path: f"# fixture {index}\n"},
        expected_output_schema={"ok": "bool", "path": "str", "bytes": "number"},
        expected_side_effects={"files_written": [report],
                               "external_calls": ["shell_execute"]},
        expected_status="success",
        side_effects_source=C.SIDE_EFFECT_SOURCE_AUTHORED,
        kind=C.CASE_KIND_TRACE, origin_trace_id=f"tr-{index:03d}")


def make_case_set(count: int = 12) -> C.CaseSet:
    return C.build_case_set(CAP, [step_case(i) for i in range(count)])


def equal_candidate(case: C.EquivalenceCase):
    """与上游**等价**的候选实现（全过）—— 探活的"健康"基线"""
    return ProgramImplementation(list(case.upstream), name="native_equal")


def degraded_candidate(case: C.EquivalenceCase):
    """**退化**候选实现：丢掉最后一步（结构层硬性比对必然失败）"""
    return ProgramImplementation(list(case.upstream)[:2], name="native_degraded")


def make_probe(store, *, registry=None, baselines=None, env=None, **kwargs) -> P.LivenessProbe:
    """默认 `env=None` ⇒ 读 `os.environ`（由 autouse fixture 隔离），可显式覆盖"""
    return P.LivenessProbe(case_store=store, registry=registry,
                           baseline_store=baselines, env=env, **kwargs)


def establish_baseline(baselines, *, capability_id: str = CAP, stage: str = "native",
                       pass_rate: float = 1.0, p99_wall_ms: float = 100.0,
                       cost: float | None = None, last_success_at: float = 0.0,
                       at: float = 1_000_000.0) -> P.LivenessBaseline:
    """直接写一条既有基线（模拟"上一个探活周期以来的水位"）"""
    item = P.LivenessBaseline(
        capability_id=capability_id, stage=stage, pass_rate=pass_rate,
        p99_wall_ms=p99_wall_ms, cost_cents_per_unit=cost,
        last_success_at=last_success_at or at, last_probe_at=at,
        probes=3, established_at=at, updated_at=at, last_verdict=P.VERDICT_OK)
    baselines.save(item)
    return item


# ════════════════════════════════════════════════════════════
#  1. 四项退化条件（纯函数：逐项可单独触发）
# ════════════════════════════════════════════════════════════


class TestDegradationConditions:
    """① 通过率 ② p99 回归 ③ 成本上升 ④ 超周期 —— 各自独立可触发"""

    def test_condition_pass_rate_triggered_below_baseline_ratio(self):
        verdict = P.evaluate_liveness(
            capability_id=CAP, pass_rate=0.97, baseline_pass_rate=1.0,
            now=1000.0, last_success_at=1000.0)
        item = verdict.condition(P.LIVENESS_COND_PASS_RATE)
        assert item.applicable is True and item.triggered is True
        assert item.threshold == 0.98          # 1.0 × 0.98
        assert verdict.degraded is True
        assert verdict.triggered_conditions == [P.LIVENESS_COND_PASS_RATE]

    def test_condition_pass_rate_at_floor_is_not_degraded(self):
        """恰好等于 基线×0.98 ⇒ **不**判退化（阈值语义是 <，不是 ≤）"""
        verdict = P.evaluate_liveness(capability_id=CAP, pass_rate=0.98,
                                      baseline_pass_rate=1.0, now=1000.0,
                                      last_success_at=1000.0)
        assert verdict.degraded is False
        assert verdict.condition(P.LIVENESS_COND_PASS_RATE).triggered is False

    def test_condition_p99_regression_triggered(self):
        verdict = P.evaluate_liveness(
            capability_id=CAP, pass_rate=1.0, baseline_pass_rate=1.0,
            p99_wall_ms=15.1, baseline_p99_wall_ms=10.0,
            now=1000.0, last_success_at=1000.0)
        item = verdict.condition(P.LIVENESS_COND_P99)
        assert item.applicable is True and item.triggered is True
        assert item.threshold == 15.0          # 10.0 × 1.5
        assert verdict.triggered_conditions == [P.LIVENESS_COND_P99]

    def test_condition_cost_increase_triggered(self):
        verdict = P.evaluate_liveness(
            capability_id=CAP, pass_rate=1.0, baseline_pass_rate=1.0,
            cost_cents_per_unit=1.31, baseline_cost_cents_per_unit=1.0,
            now=1000.0, last_success_at=1000.0)
        item = verdict.condition(P.LIVENESS_COND_COST)
        assert item.applicable is True and item.triggered is True
        assert item.threshold == 1.3           # 1.0 × 1.3（与成本刹车断食阈值同值）
        assert verdict.triggered_conditions == [P.LIVENESS_COND_COST]

    def test_condition_overdue_triggered_by_injected_clock(self):
        """④ 用**注入时钟**判定：now − last_success_at > 7 天 ⇒ 命中"""
        last = 1_000_000.0
        verdict = P.evaluate_liveness(
            capability_id=CAP, pass_rate=1.0, baseline_pass_rate=1.0,
            now=last + 7 * DAY + 1.0, last_success_at=last)
        item = verdict.condition(P.LIVENESS_COND_OVERDUE)
        assert item.applicable is True and item.triggered is True
        assert item.threshold == 7 * DAY
        assert verdict.triggered_conditions == [P.LIVENESS_COND_OVERDUE]

    def test_condition_overdue_not_triggered_within_period(self):
        last = 1_000_000.0
        verdict = P.evaluate_liveness(
            capability_id=CAP, pass_rate=1.0, baseline_pass_rate=1.0,
            now=last + 6 * DAY, last_success_at=last)
        assert verdict.condition(P.LIVENESS_COND_OVERDUE).triggered is False
        assert verdict.degraded is False

    def test_all_four_conditions_can_trigger_together(self):
        last = 1_000_000.0
        verdict = P.evaluate_liveness(
            capability_id=CAP, pass_rate=0.5, baseline_pass_rate=1.0,
            p99_wall_ms=100.0, baseline_p99_wall_ms=1.0,
            cost_cents_per_unit=9.0, baseline_cost_cents_per_unit=1.0,
            now=last + 30 * DAY, last_success_at=last)
        assert set(verdict.triggered_conditions) == set(P.LIVENESS_CONDITIONS)
        assert verdict.degraded is True

    def test_first_probe_has_no_applicable_conditions(self):
        """首次探活（无基线、无历史成功）⇒ **四项全不判定**（不是"全通过"）"""
        verdict = P.evaluate_liveness(
            capability_id=CAP, pass_rate=1.0, p99_wall_ms=3.0,
            cost_cents_per_unit=None, now=1000.0, last_success_at=0.0)
        assert verdict.degraded is False
        assert set(verdict.unmeasured_conditions) == set(P.LIVENESS_CONDITIONS)
        assert P.LIVENESS_COND_COST in verdict.missing_evidence_conditions
        assert P.LIVENESS_COND_PASS_RATE not in verdict.missing_evidence_conditions

    def test_thresholds_are_configurable(self):
        verdict = P.evaluate_liveness(
            capability_id=CAP, pass_rate=0.6, baseline_pass_rate=1.0,
            now=1000.0, last_success_at=1000.0, pass_rate_ratio=0.5)
        assert verdict.condition(P.LIVENESS_COND_PASS_RATE).threshold == 0.5
        assert verdict.degraded is False

    def test_condition_objects_are_serializable_and_labelled(self):
        verdict = P.evaluate_liveness(capability_id=CAP, pass_rate=1.0,
                                      baseline_pass_rate=1.0, now=1.0,
                                      last_success_at=1.0)
        payload = verdict.to_dict()
        assert [c["name"] for c in payload["conditions"]] == list(P.LIVENESS_CONDITIONS)
        assert all(c["label"] for c in payload["conditions"])


# ════════════════════════════════════════════════════════════
#  2. 作用范围与安全返回
# ════════════════════════════════════════════════════════════


class TestScopeAndSafeReturn:
    def test_scan_targets_only_internalized_and_native(self):
        registry = FakeRegistry({"a": "borrowed", "b": "shadow", "c": "internalized",
                                 "d": "native", "e": "deprecated"})
        probe = make_probe(None, registry=registry)
        assert probe.scan_targets() == ["c", "d"]

    def test_run_all_without_any_target_returns_empty_safely(self, store, baselines):
        """无 internalized/native 能力 ⇒ 如实返回，**不报错、不编造**"""
        registry = FakeRegistry({"a": "borrowed", "b": "mirrored"})
        store.save(make_case_set())
        probe = make_probe(store, registry=registry, baselines=baselines)
        outcome = probe.run_all()
        assert outcome["status"] == "no_capability"
        assert outcome["targets"] == [] and outcome["probed"] == 0
        assert outcome["reports"] == []
        assert "无可探活能力" in outcome["note"]

    def test_scan_targets_without_registry_is_empty(self, store, baselines):
        """台账未注入 ⇒ 空列表（**不**隐式读运行时台账）"""
        probe = make_probe(store, registry=None, baselines=baselines)
        assert probe.scan_targets() == []

    def test_run_on_out_of_scope_stage_is_not_applicable(self, store, baselines):
        registry = FakeRegistry({CAP: "shadow"})
        store.save(make_case_set())
        probe = make_probe(store, registry=registry, baselines=baselines)
        report = probe.run(CAP)
        assert report.verdict == P.VERDICT_NOT_APPLICABLE
        assert report.probe_ids == []
        assert report.baseline_established is False
        assert baselines.load(CAP) is None       # 未探活 ⇒ 未写台账

    def test_run_without_case_set_is_safe(self, store, baselines):
        registry = FakeRegistry({CAP: "native"})
        probe = make_probe(store, registry=registry, baselines=baselines)
        report = probe.run(CAP)
        assert report.verdict == P.VERDICT_NO_CASES
        assert report.pass_rate is None
        assert "不报错、不编造" in report.reasons[0]

    def test_run_never_raises_on_internal_failure(self, store, baselines):
        """内部异常收敛为 verdict=error（探活不得成为新的故障源）"""
        registry = FakeRegistry({CAP: "native"})
        store.save(make_case_set())
        probe = make_probe(store, registry=registry, baselines=baselines)

        class Boom:
            def load(self, _capability_id):
                raise RuntimeError("判定集后端炸了")

        probe._case_store = Boom()  # type: ignore[assignment]
        report = probe.run(CAP)
        assert report.verdict == P.VERDICT_NO_CASES   # 读失败 → 无判定集（不抛）


# ════════════════════════════════════════════════════════════
#  3. 正常探活：通过率 / 真实墙钟 p99 / 成本增量 / 抽样规模
# ════════════════════════════════════════════════════════════


class TestHealthyProbe:
    def test_run_reports_pass_rate_wall_p99_and_cost(self, store, baselines):
        registry = FakeRegistry({CAP: "native"})
        store.save(make_case_set())
        probe = make_probe(store, registry=registry, baselines=baselines,
                           cost_provider=lambda _cid: {"cents_per_unit": 0.4,
                                                       "source": "utc"})
        report = probe.run(CAP, candidate=equal_candidate, now=1_000_000.0)
        assert report.verdict == P.VERDICT_OK and report.degraded is False
        assert report.total == probe.probe_size == P.DEFAULT_PROBE_SIZE
        assert report.pass_rate == 1.0
        assert report.p99_wall_candidate_ms is not None
        assert report.p99_wall_candidate_ms > 0          # 真实墙钟（不是模型时钟量）
        assert report.p99_wall_upstream_ms is not None
        assert report.clock == P.CLOCK_WALL
        assert report.cost_cents_per_unit == 0.4 and report.cost_source == "utc"
        assert report.baseline_established is True

    def test_probe_size_is_configurable_by_env_and_argument(self, store, baselines,
                                                            monkeypatch):
        registry = FakeRegistry({CAP: "native"})
        store.save(make_case_set())
        monkeypatch.setenv(P.PROBE_SIZE_ENV, "3")
        env_probe = make_probe(store, registry=registry, baselines=baselines)
        assert env_probe.probe_size == 3
        assert len(env_probe.run(CAP, candidate=equal_candidate).probe_ids) == 3

        arg_probe = make_probe(store, registry=registry, baselines=baselines,
                              probe_size=2)
        report = arg_probe.run(CAP, candidate=equal_candidate)
        assert len(report.probe_ids) == 2
        assert report.probe_ids == sorted(report.probe_ids)   # 确定性抽样

    def test_illegal_probe_size_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv(P.PROBE_SIZE_ENV, "not-a-number")
        assert P.probe_size_from_env() == P.DEFAULT_PROBE_SIZE
        monkeypatch.setenv(P.PROBE_SIZE_ENV, "-4")
        assert P.probe_size_from_env() == P.DEFAULT_PROBE_SIZE

    def test_sampling_is_deterministic_across_runs(self, store, baselines):
        registry = FakeRegistry({CAP: "native"})
        store.save(make_case_set())
        probe = make_probe(store, registry=registry, baselines=baselines)
        first = probe.run(CAP, candidate=equal_candidate, now=1_000_000.0)
        second = probe.run(CAP, candidate=equal_candidate, now=1_000_001.0)
        assert first.probe_ids == second.probe_ids

    def test_healthy_probe_refreshes_heartbeat_only(self, store, baselines):
        """健康探活只刷**心跳**（last_success_at），不抬基线（防温水煮青蛙）"""
        registry = FakeRegistry({CAP: "native"})
        store.save(make_case_set())
        base = establish_baseline(baselines, pass_rate=0.9, p99_wall_ms=1e9,
                                  at=1_000_000.0)
        probe = make_probe(store, registry=registry, baselines=baselines)
        probe.run(CAP, candidate=equal_candidate, now=1_000_000.0 + 60.0)
        after = baselines.load(CAP)
        assert after.pass_rate == base.pass_rate == 0.9        # 基线未被抬高
        assert after.p99_wall_ms == base.p99_wall_ms
        assert after.last_success_at == 1_000_000.0 + 60.0    # 心跳已刷新

    def test_establish_resets_baseline_explicitly(self, store, baselines):
        registry = FakeRegistry({CAP: "native"})
        store.save(make_case_set())
        establish_baseline(baselines, pass_rate=0.5, p99_wall_ms=0.0001,
                           at=1_000_000.0)
        probe = make_probe(store, registry=registry, baselines=baselines)
        report = probe.establish(CAP, candidate=equal_candidate, now=2_000_000.0)
        after = baselines.load(CAP)
        assert report.baseline_established is True
        assert after.pass_rate == 1.0                          # 显式重设才抬基线
        assert after.established_at == 2_000_000.0

    def test_baseline_store_is_isolated_to_tmp(self, baselines, tmp_path):
        assert str(tmp_path) in baselines.path
        assert baselines.stats()["capabilities"] == 0


# ════════════════════════════════════════════════════════════
#  4. 退化：回退建议（**不自动执行**）+ 事故卡
# ════════════════════════════════════════════════════════════


class TestDegradationRollbackAndIncident:
    def test_degraded_probe_recommends_rollback_without_executing(self, store,
                                                                  baselines):
        """① 命中 + 建议 native → borrowed，但 **stage 台账未被触碰**"""
        registry = FakeRegistry({CAP: "native"})
        store.save(make_case_set())
        establish_baseline(baselines, pass_rate=1.0, p99_wall_ms=1e9, at=1_000_000.0)
        probe = make_probe(store, registry=registry, baselines=baselines)
        report = probe.run(CAP, candidate=degraded_candidate,
                           fatal_change="commit-deadbeef", now=1_000_000.1)

        assert report.verdict == P.VERDICT_DEGRADED and report.degraded is True
        assert P.LIVENESS_COND_PASS_RATE in report.triggered_conditions
        assert report.rollback is not None
        assert report.rollback.recommended is True
        assert report.rollback.executed is False            # **绝不自动执行**
        assert report.rollback.from_stage == "native"
        assert report.rollback.to_stage == P.ROLLBACK_TO_STAGE == "borrowed"
        assert report.rollback.requires_approval is True
        assert "stage_migrate" in report.rollback.apply_hint
        # 硬断言：探活全程**没有**任何 stage 变更动作
        assert registry.set_stage_calls == []
        assert registry.stages[CAP] == "native"

    def test_healthy_probe_does_not_recommend_rollback(self, store, baselines):
        registry = FakeRegistry({CAP: "native"})
        store.save(make_case_set())
        establish_baseline(baselines, pass_rate=1.0, p99_wall_ms=1e9, at=1_000_000.0)
        probe = make_probe(store, registry=registry, baselines=baselines)
        report = probe.run(CAP, candidate=equal_candidate, now=1_000_000.1)
        assert report.rollback.recommended is False
        assert report.rollback.executed is False
        assert registry.set_stage_calls == []

    def test_suggest_stage_rollback_is_pure_and_never_executes(self):
        verdict = P.evaluate_liveness(capability_id=CAP, pass_rate=0.1,
                                      baseline_pass_rate=1.0, now=1.0,
                                      last_success_at=1.0)
        suggestion = P.suggest_stage_rollback(CAP, from_stage="internalized",
                                             verdict=verdict)
        assert suggestion.to_dict()["executed"] is False
        assert suggestion.to_stage == "borrowed"
        assert suggestion.rule_id == "stage_rollback:%s.internalized->borrowed" % CAP
        # 未退化时同样返回对象，但明确"不建议回退"
        ok = P.evaluate_liveness(capability_id=CAP, pass_rate=1.0,
                                 baseline_pass_rate=1.0, now=1.0, last_success_at=1.0)
        calm = P.suggest_stage_rollback(CAP, from_stage="native", verdict=ok)
        assert calm.recommended is False and calm.executed is False

    def test_incident_card_has_all_six_elements(self, store, baselines):
        """退化 → 事故卡（复用 raise_incident）+ 六要素齐备 ⇒ 可 resolved"""
        registry = FakeRegistry({CAP: "native"})
        store.save(make_case_set())
        establish_baseline(baselines, pass_rate=1.0, p99_wall_ms=1e9, at=1_000_000.0)
        probe = make_probe(store, registry=registry, baselines=baselines)
        report = probe.run(CAP, candidate=degraded_candidate,
                           fatal_change="commit-deadbeef", now=1_000_000.1)
        assert report.incident_id.startswith("inc-")
        assert report.incident_missing_elements == []

        from agent.self_healing.levels import load_incident
        card = load_incident(report.incident_id)
        assert card is not None
        assert card.missing_elements() == []
        assert card.is_resolvable() is True
        assert card.evasion_rule == report.rollback.rule_id
        assert card.in_strategy_memory["yes"] is True
        assert card.regression_case_added["yes"] is True
        assert card.trace_ids
        assert card.severity.value == "L3"          # native → L3（§4.4 语义）
        assert card.detail["rollback_executed"] is False
        assert card.detail["stage"] == "native"

    def test_internalized_degradation_maps_to_L2(self, store, baselines):
        registry = FakeRegistry({CAP: "internalized"})
        store.save(make_case_set())
        establish_baseline(baselines, stage="internalized", pass_rate=1.0,
                           p99_wall_ms=1e9, at=1_000_000.0)
        probe = make_probe(store, registry=registry, baselines=baselines)
        report = probe.run(CAP, candidate=degraded_candidate,
                           fatal_change="c1", now=1_000_000.1)
        from agent.self_healing.levels import load_incident
        card = load_incident(report.incident_id)
        assert card.severity.value == "L2"

    def test_missing_fatal_change_is_reported_honestly(self, store, baselines):
        """拿不到"致命变更 commit" ⇒ **不编造**：卡片如实保留缺失项、不可 resolved"""
        registry = FakeRegistry({CAP: "native"})       # 无 meta.version
        store.save(make_case_set())
        # 只用 ④ 超周期触发（无失败用例 ⇒ regression_case_added 也如实为否）
        establish_baseline(baselines, pass_rate=1.0, p99_wall_ms=1e9,
                           cost=None, at=1_000_000.0)
        probe = make_probe(store, registry=registry, baselines=baselines)
        report = probe.run(CAP, candidate=equal_candidate, now=1_000_000.0 + 30 * DAY)
        assert report.degraded is True
        assert report.triggered_conditions == [P.LIVENESS_COND_OVERDUE]
        assert set(report.incident_missing_elements) == {"fatal_change",
                                                         "regression_case_added"}
        from agent.self_healing.levels import load_incident
        card = load_incident(report.incident_id)
        assert card.is_resolvable() is False
        assert card.detail["fatal_change_source"] == "unavailable"

    def test_fatal_change_falls_back_to_descriptor_version(self, store, baselines):
        registry = FakeRegistry({CAP: "native"}, versions={CAP: "v9.9.9"})
        store.save(make_case_set())
        establish_baseline(baselines, pass_rate=1.0, p99_wall_ms=1e9, at=1_000_000.0)
        probe = make_probe(store, registry=registry, baselines=baselines)
        report = probe.run(CAP, candidate=degraded_candidate, now=1_000_000.1)
        from agent.self_healing.levels import load_incident
        card = load_incident(report.incident_id)
        assert card.fatal_change == "v9.9.9"
        assert card.detail["fatal_change_source"] == "descriptor.meta.version"

    def test_emit_incident_false_only_suggests(self, store, baselines):
        registry = FakeRegistry({CAP: "native"})
        store.save(make_case_set())
        establish_baseline(baselines, pass_rate=1.0, p99_wall_ms=1e9, at=1_000_000.0)
        probe = make_probe(store, registry=registry, baselines=baselines,
                           emit_incident=False)
        report = probe.run(CAP, candidate=degraded_candidate, now=1_000_000.1)
        assert report.degraded is True and report.incident_id == ""
        assert report.rollback.recommended is True

    def test_degradation_updates_consecutive_counter_and_heartbeat(self, store,
                                                                   baselines):
        registry = FakeRegistry({CAP: "native"})
        store.save(make_case_set())
        establish_baseline(baselines, pass_rate=1.0, p99_wall_ms=1e9, at=1_000_000.0)
        probe = make_probe(store, registry=registry, baselines=baselines)
        probe.run(CAP, candidate=degraded_candidate, now=1_000_000.1)
        after = baselines.load(CAP)
        assert after.consecutive_degraded == 1
        assert after.last_success_at == 1_000_000.0        # 未刷新（未成功）
        assert after.history[-1]["verdict"] == P.VERDICT_DEGRADED


# ════════════════════════════════════════════════════════════
#  5. 调度（**默认关闭**）
# ════════════════════════════════════════════════════════════


class FakeScheduler:
    def __init__(self) -> None:
        self.tasks: dict[str, object] = {}
        self.intervals: dict[str, tuple[object, float]] = {}

    def add_interval_task(self, name, *, func, interval_seconds):
        self.tasks[name] = type("Task", (), {"task_id": f"task-{name}"})()
        self.intervals[name] = (func, float(interval_seconds))


class TestScheduling:
    def test_job_is_disabled_by_default(self):
        outcome = P.register_liveness_job(scheduler=FakeScheduler())
        assert outcome["status"] == "disabled"
        assert P.LIVENESS_ENABLE_ENV in outcome["note"]

    def test_job_registers_only_with_explicit_env(self, monkeypatch, store,
                                                  baselines):
        scheduler = FakeScheduler()
        probe = make_probe(store, registry=FakeRegistry({CAP: "native"}),
                           baselines=baselines)
        monkeypatch.setenv(P.LIVENESS_ENABLE_ENV, "true")
        outcome = P.register_liveness_job(scheduler=scheduler, probe=probe)
        assert outcome["status"] == "registered"
        assert outcome["task"] == P.LIVENESS_TASK_NAME
        assert outcome["interval_seconds"] == P.LIVENESS_INTERVAL_SECONDS == 7 * DAY
        assert P.LIVENESS_TASK_NAME in scheduler.tasks

    def test_illegal_enabled_value_keeps_it_off(self, monkeypatch):
        monkeypatch.setenv(P.LIVENESS_ENABLE_ENV, "maybe")
        assert P.liveness_enabled() is False
        assert P.register_liveness_job(scheduler=FakeScheduler())["status"] == "disabled"

    def test_tick_reports_no_capability_safely(self, store, baselines):
        """开启后 tick 对"无能力"同样如实返回，不报错"""
        scheduler = FakeScheduler()
        probe = make_probe(store, registry=FakeRegistry({"a": "borrowed"}),
                           baselines=baselines)
        P.register_liveness_job(scheduler=scheduler, probe=probe, enabled=True,
                                env={})
        func, _interval = scheduler.intervals[P.LIVENESS_TASK_NAME]
        result = func()
        assert result["status"] == "no_capability"

    def test_tick_probes_targets_and_respects_max_targets(self, store, baselines):
        scheduler = FakeScheduler()
        registry = FakeRegistry({CAP: "native", "cp.builtin.list_dir": "internalized"})
        store.save(make_case_set())
        for cid in ("cp.builtin.list_dir", CAP):
            establish_baseline(baselines, capability_id=cid, pass_rate=1.0,
                               p99_wall_ms=1e9, at=1_000_000.0)
        probe = make_probe(store, registry=registry, baselines=baselines)
        outcome = P.register_liveness_job(scheduler=scheduler, probe=probe,
                                          enabled=True, max_targets=1, env={})
        assert outcome["max_targets"] == 1
        func, _interval = scheduler.intervals[P.LIVENESS_TASK_NAME]
        result = func()
        assert result["status"] == "ok"
        assert result["probed"] == 1 and result["truncated"] is True

    def test_illegal_max_targets_env_falls_back(self, monkeypatch):
        monkeypatch.setenv(P.MAX_TARGETS_ENV, "0")
        assert P.max_targets_from_env() == P.DEFAULT_MAX_TARGETS
        monkeypatch.setenv(P.MAX_TARGETS_ENV, "abc")
        assert P.max_targets_from_env() == P.DEFAULT_MAX_TARGETS

    def test_interval_is_weekly(self):
        assert P.LIVENESS_PERIOD_DAYS == 7
        assert P.LIVENESS_INTERVAL_SECONDS == 7 * 86400.0


# ════════════════════════════════════════════════════════════
#  6. 与"漂移重探"的区分 + 模块边界
# ════════════════════════════════════════════════════════════


class TestBoundaries:
    def test_report_distinguishes_liveness_from_drift_reprobe(self, store, baselines):
        """报告里必须显式写明"探活 = 自身退化，漂移重探 = 上游契约"两个概念"""
        registry = FakeRegistry({CAP: "native"})
        store.save(make_case_set())
        probe = make_probe(store, registry=registry, baselines=baselines)
        payload = probe.run(CAP, candidate=equal_candidate).to_dict()
        assert "自身实现退化" in payload["note"]
        assert "gate.reprobe" in payload["note"]
        assert payload["version"] == P.PROBE_VERSION

    def test_liveness_uses_its_own_baseline_not_upstream_arm(self, store, baselines):
        """② 的基线是**自己的历史** p99，不是同轮上游 p99（那是漂移口径）"""
        registry = FakeRegistry({CAP: "native"})
        store.save(make_case_set())
        # 基线设成极小值 ⇒ 必然回归；若误用"同轮上游比值"则不会触发
        establish_baseline(baselines, pass_rate=1.0, p99_wall_ms=1e-9,
                           at=1_000_000.0)
        probe = make_probe(store, registry=registry, baselines=baselines)
        report = probe.run(CAP, candidate=equal_candidate, now=1_000_000.1)
        assert P.LIVENESS_COND_P99 in report.triggered_conditions

    def test_probe_does_not_write_runtime_case_or_ledger_paths(self, store,
                                                               baselines, tmp_path):
        """所有产物都落在 tmp_path（无运行时目录污染）"""
        registry = FakeRegistry({CAP: "native"})
        store.save(make_case_set())
        probe = make_probe(store, registry=registry, baselines=baselines)
        probe.run(CAP, candidate=equal_candidate, now=1_000_000.0)
        assert str(tmp_path) in baselines.path
        assert str(tmp_path) in store.root

    def test_module_does_not_create_second_case_or_sandbox_implementation(self):
        """**不自建第二套**：判定集与沙箱一律复用 S3-02 资产"""
        import agent.digestion.probe as module
        assert module.CaseSet is C.CaseSet
        assert module.ReplaySandbox.__module__ == "agent.digestion.sandbox"
        assert module.probe_sample_ids.__module__ == "agent.digestion.gate"


# ════════════════════════════════════════════════════════════
#  7. 基线台账（独立落点 + 原子写 + 未知字段不静默）
# ════════════════════════════════════════════════════════════


class TestBaselineStore:
    def test_round_trip_and_history_cap(self, baselines):
        item = P.LivenessBaseline(capability_id=CAP, stage="native", pass_rate=1.0)
        item.history = [{"probed_at": float(i)} for i in range(P.MAX_HISTORY + 5)]
        baselines.save(item)
        loaded = baselines.load(CAP)
        assert loaded.pass_rate == 1.0 and loaded.stage == "native"
        assert len(loaded.history) == P.MAX_HISTORY + 5    # 存储层不裁剪，运行层裁剪

    def test_unknown_field_is_dropped_with_warning(self, baselines, caplog):
        payload = {"capability_id": CAP, "pass_rate": 1.0, "mystery": 1}
        baselines._write_all({CAP: payload})
        with caplog.at_level("WARNING"):
            loaded = baselines.load(CAP)
        assert loaded is not None and loaded.pass_rate == 1.0
        assert any("未知字段" in r.message for r in caplog.records)

    def test_missing_file_reads_as_empty(self, tmp_path):
        store = P.LivenessBaselineStore(str(tmp_path / "nope" / "b.json"))
        assert store.load(CAP) is None
        assert store.all() == {}
