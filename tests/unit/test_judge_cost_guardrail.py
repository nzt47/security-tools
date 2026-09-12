"""TASK-S8-04 judge 成本护栏 / 两栏成本 / 抽检一致率 单测

覆盖任务书 §四 验收清单的后半：
- judge 调用**计入 UTC**（成本可查）；**每日预算超限自动回落** + 事件（用例）
- 业务成本与 judge 成本**两栏不混算**（用例断言）
- 抽检一致率统计产出；分歧样本入复核队列；样本 <20 只披露
- 回落原因码精确（``budget_exceeded`` / ``budget_unreadable`` / ``cost_policy_fasting``）

隔离纪律（S3-02/S3-03 教训）：事件目录 / 灰度目录 / 判定存档 / 判定集 / 通行证
全部落 ``tmp_path``；本文件不触碰运行时区、不发起真实模型调用。
"""

from __future__ import annotations

import json

import pytest

from agent.audit import facade as facade_mod
from agent.audit.chain import AuditChain
from agent.digestion import cases as C
from agent.digestion import gate as G
from agent.digestion import judge_runtime as JR
from agent.digestion import shadow as SH
from agent.observability import events as ev
from agent.observability import utc as U

CAP = "cp.builtin.read_file"
SECRET = "sk-fake-judge-secret-0123456789abcdef"


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
    monkeypatch.setenv("CP_EVENTS_DIR", str(tmp_path / "events"))
    monkeypatch.setenv(C.CASE_ROOT_ENV, str(tmp_path / "cases"))
    monkeypatch.setenv(SH.SHADOW_DIR_ENV, str(tmp_path / "shadow"))
    for name in (JR.JUDGE_ENABLE_ENV, SH.JUDGE_PROVIDER_ENV, SH.JUDGE_MODEL_ENV,
                 JR.JUDGE_BUDGET_ENV, JR.JUDGE_THRESHOLD_ENV,
                 JR.JUDGE_FOLLOW_FASTING_ENV, JR.JUDGE_SECRET_FILE_ENV,
                 JR.JUDGE_DOTENV_ENV, "OPENAI_API_KEY", "LLM_API_KEY",
                 "LLM_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    U.reset_config_cache()
    ev.reset_event_stores()
    yield tmp_path
    U.reset_config_cache()
    ev.reset_event_stores()


@pytest.fixture
def events_dir(tmp_path) -> str:
    return str(tmp_path / "events")


def stub_reply(confidence: float = 0.95, verdict: str = "equivalent") -> str:
    return json.dumps({"verdict": verdict, "confidence": confidence,
                       "reason": "stub"}, ensure_ascii=False)


def build_runtime(*, events_dir: str, tmp_path, enabled: bool = True,
                  budget: float = 100.0, invoke=None, capability_id: str = CAP,
                  verdict_store=None, model: str = "gpt-4o-mini",
                  follow_fasting: bool = True) -> JR.JudgeRuntime:
    """桩 judge 运行时（注入 invoke ⇒ 无需凭证、零网络）"""
    config = JR.JudgeConfig(enabled=enabled, provider="probe", model=model,
                            daily_budget_cents=budget, follow_fasting=follow_fasting)
    return JR.build_judge_runtime(
        config, env={}, invoke=invoke or (lambda prompt: stub_reply()),
        dotenv_path=str(tmp_path / "absent.env"), events_dir=events_dir,
        verdict_store=verdict_store, capability_id=capability_id)


# ── 端到端灰度所需的构造工具（与 test_digestion_shadow 同款；此处独立复刻）──


def make_case(index: int = 0, *, root: str = "C:/sandbox") -> C.EquivalenceCase:
    path = f"{root}/out/a{index}.txt"
    steps = [C.ProgramStep(label="read_file", params={"path": path}),
             C.ProgramStep(label="write_file", params={"path": path,
                                                       "content": f"c{index}"})]
    return C.EquivalenceCase(
        case_id=f"case-{index:03d}", capability_id=CAP, input={"path": path},
        upstream=steps, native=steps, fixtures={path: f"c{index}"},
        expected_side_effects={"files_written": [path]},
        expected_status="success", sandbox_root=root)


def make_case_set(size: int = 24) -> C.CaseSet:
    return C.build_case_set(CAP, [make_case(i) for i in range(size)])


def issue_passport(case_set: C.CaseSet, tmp_path):
    if case_set.size < G.GATE_REPLAY_MIN:
        cases = list(case_set.cases)
        for i in range(len(cases), G.GATE_REPLAY_MIN):
            cases.append(make_case(i))
        case_set = C.build_case_set(CAP, cases, version=case_set.version)
    store = G.PassportStore(str(tmp_path / "cases"))
    result = G.acceptance_gate(CAP, case_set=case_set, passport_store=store,
                              emit_events=False)
    return result, store


def build_runner(*, tmp_path, runtime, store, review_queue=None) -> SH.ShadowRunner:
    return SH.ShadowRunner(
        judge_runtime=runtime, passport_store=store,
        case_store=C.open_case_store(str(tmp_path / "cases")),
        ledger=SH.ShadowLedger(str(tmp_path / "shadow" / "l.jsonl")),
        review_queue=review_queue or SH.ManualReviewQueue(
            str(tmp_path / "shadow" / "r.jsonl")),
        env={}, emit_events=False)


# ════════════════════════════════════════════════════════════
#  1. judge 成本计入 UTC + 两栏不混算
# ════════════════════════════════════════════════════════════


class TestJudgeCostIntoUTC:
    def test_recorded_cost_appears_in_utc_judge_column(self, events_dir, tmp_path):
        runtime = build_runtime(events_dir=events_dir, tmp_path=tmp_path)
        payload = runtime.budget.record(model="gpt-4o-mini", provider="probe",
                                        tokens_in=1000, tokens_out=500,
                                        estimated=False, interaction_id="j-1")
        assert payload["recorded"] is True
        assert payload["source"] == JR.JUDGE_COST_SOURCE

        daily = U.utc_daily(directory=events_dir)
        assert daily["judge_calls"] == 1
        assert daily["judge_cost_normalized_cents"] > 0
        assert daily["business_calls"] == 0
        assert daily["business_cost_normalized_cents"] == 0.0

    def test_two_columns_do_not_mix(self, events_dir, tmp_path):
        """**两栏不混算**：judge 栏只含 judge，业务栏只含业务，且合计=总额"""
        runtime = build_runtime(events_dir=events_dir, tmp_path=tmp_path)
        U.record_cost(model="gpt-4o-mini", provider="probe", source="business",
                      tokens_in=1000, tokens_out=500, interaction_id="biz-1")
        runtime.budget.record(model="gpt-4o-mini", provider="probe",
                              tokens_in=200, tokens_out=100,
                              interaction_id="judge-1")
        daily = U.utc_daily(directory=events_dir)
        business = daily["cost_columns"]["business"]
        judge = daily["cost_columns"]["judge"]
        total = daily["cost_columns"]["total"]
        assert business["calls"] == 1 and judge["calls"] == 1
        assert business["cost_normalized_cents"] > 0
        assert judge["cost_normalized_cents"] > 0
        assert judge["cost_normalized_cents"] < business["cost_normalized_cents"]
        assert total["cost_normalized_cents"] == pytest.approx(
            business["cost_normalized_cents"] + judge["cost_normalized_cents"])
        assert total["cost_normalized_cents"] == pytest.approx(
            daily["cost_normalized_cents"])
        assert daily["cost_columns"]["unattributed_cents"] == pytest.approx(0.0)

    def test_judge_cost_is_included_in_total_so_fasting_sees_it(
            self, events_dir, tmp_path):
        """judge 成本**计入 UTC 总额**（故日熔断/周断食判定看得见它）"""
        runtime = build_runtime(events_dir=events_dir, tmp_path=tmp_path)
        runtime.budget.record(model="gpt-4o-mini", tokens_in=1000, tokens_out=500,
                              interaction_id="j-1")
        daily = U.utc_daily(directory=events_dir)
        assert daily["cost_normalized_cents"] == pytest.approx(
            daily["judge_cost_normalized_cents"])
        assert daily["cost_normalized_cents"] > 0

    def test_unspecified_source_goes_to_business_column(self, events_dir, tmp_path):
        """未标注 source 的历史事件计业务栏，但保留 `unspecified` 桶可追溯"""
        U.record_cost(model="gpt-4o-mini", tokens_in=100, tokens_out=50,
                      interaction_id="legacy-1")
        daily = U.utc_daily(directory=events_dir)
        assert daily["business_calls"] == 1 and daily["judge_calls"] == 0
        assert "unspecified" in daily["by_source"]

    def test_judge_cost_cents_reads_only_judge_column(self, events_dir):
        assert U.judge_cost_cents(directory=events_dir)["cost_normalized_cents"] == 0.0
        U.record_cost(model="gpt-4o-mini", source="business", tokens_in=1000,
                      tokens_out=1000, interaction_id="biz")
        assert U.judge_cost_cents(directory=events_dir)["cost_normalized_cents"] == 0.0
        U.record_cost(model="gpt-4o-mini", source="judge", tokens_in=1000,
                      tokens_out=1000, interaction_id="jud")
        read = U.judge_cost_cents(directory=events_dir)
        assert read["cost_normalized_cents"] > 0
        assert read["calls"] == 1 and read["error"] == ""

    def test_estimated_tokens_are_flagged_in_the_event(self, events_dir, tmp_path):
        """拿不到真实 usage 时用估算 ⇒ 事件里**显式标注** `tokens_estimated`"""
        runtime = build_runtime(events_dir=events_dir, tmp_path=tmp_path)
        runtime.budget.record(model="gpt-4o-mini", tokens_in=10, tokens_out=5,
                              estimated=True, interaction_id="j-est")
        rows = [e for e in ev.iter_events(types=(ev.EV_COST,), directory=events_dir)]
        assert len(rows) == 1
        assert rows[0].payload["tokens_estimated"] is True
        assert rows[0].payload["source"] == "judge"

    def test_extra_cannot_override_source(self, events_dir, tmp_path):
        """`extra` 只能**新增**字段，不得覆盖既有口径（防两栏被悄悄改写）"""
        U.record_cost(model="gpt-4o-mini", source="judge", tokens_in=10,
                      interaction_id="x", extra={"source": "business", "note": "n"})
        rows = ev.iter_events(types=(ev.EV_COST,), directory=events_dir)
        assert rows[0].payload["source"] == "judge"
        assert rows[0].payload["note"] == "n"

    def test_record_failure_is_not_silent(self, events_dir, tmp_path, monkeypatch):
        """记账失败必须**留痕**（`record_errors`），不得静默丢账"""
        runtime = build_runtime(events_dir=events_dir, tmp_path=tmp_path)

        class BoomUtc:
            @staticmethod
            def record_cost(**kwargs):
                raise RuntimeError("events store down")

        runtime.budget._utc = BoomUtc
        payload = runtime.budget.record(model="gpt-4o-mini", tokens_in=1,
                                        interaction_id="j-fail")
        assert payload["recorded"] is False
        assert runtime.budget.record_errors


# ════════════════════════════════════════════════════════════
#  2. 每日预算超限 → 自动回落 + 事件（不静默）
# ════════════════════════════════════════════════════════════


class TestBudgetGuardrail:
    def test_zero_budget_blocks_before_any_call(self, events_dir, tmp_path):
        calls = []

        def invoke(prompt):
            calls.append(prompt)
            return stub_reply()

        runtime = build_runtime(events_dir=events_dir, tmp_path=tmp_path,
                                budget=0.0, invoke=invoke)
        assert runtime.availability.state == JR.AVAILABILITY_AVAILABLE
        probe = runtime.guard.probe()
        assert calls == []                                  # **不发真实调用**
        assert probe["ok"] is False and probe.get("precheck") is True
        assert runtime.guard.effective_kind == "deterministic_local(budget_exceeded)"
        assert runtime.is_llm is False

    def test_budget_exceeded_emits_event(self, events_dir, tmp_path):
        runtime = build_runtime(events_dir=events_dir, tmp_path=tmp_path, budget=0.0)
        runtime.guard.probe()
        rows = ev.iter_events(types=(ev.EV_MODEL_DEGRADED,), directory=events_dir)
        assert len(rows) == 1
        payload = rows[0].payload
        assert payload["to"] == SH.JUDGE_KIND_LOCAL
        assert payload["judge_reason_code"] == SH.JUDGE_REASON_BUDGET_EXCEEDED
        assert payload["judge_kind"] == "deterministic_local(budget_exceeded)"
        assert payload["capability_id"] == CAP

    def test_exhausted_budget_falls_back_with_reason_code(self, events_dir, tmp_path):
        runtime = build_runtime(events_dir=events_dir, tmp_path=tmp_path)
        # 先记一笔超过预算的 judge 成本（模拟"今天已经花超了"）
        runtime.budget.record(model="gpt-4o-mini", tokens_in=10_000_000,
                              tokens_out=10_000_000, interaction_id="burn")
        state = runtime.budget.state()
        assert state.blocked is True
        assert state.reason_code == SH.JUDGE_REASON_BUDGET_EXCEEDED
        assert runtime.budget.precheck().startswith(SH.JUDGE_REASON_BUDGET_EXCEEDED)
        assert state.spent_cents >= state.budget_cents

    def test_budget_unreadable_is_fail_closed(self, events_dir, tmp_path, monkeypatch):
        """**读不到成本 ⇒ 停用真实 judge**（fail-closed），不赌"大概没超" """
        runtime = build_runtime(events_dir=events_dir, tmp_path=tmp_path)
        monkeypatch.setattr(U, "judge_cost_cents",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        state = runtime.budget.state()
        assert state.blocked is True
        assert state.reason_code == SH.JUDGE_REASON_BUDGET_UNREADABLE
        assert runtime.budget.precheck().startswith(
            SH.JUDGE_REASON_BUDGET_UNREADABLE)

    def test_fasting_policy_blocks_and_is_configurable(self, events_dir, tmp_path,
                                                      monkeypatch):
        """断食期**默认跟随**（停用 judge）；``follow_fasting=False`` 时不拦"""
        monkeypatch.setattr(JR, "cost_policy_factor", lambda env=None: 0.0)
        following = build_runtime(events_dir=events_dir, tmp_path=tmp_path,
                                  follow_fasting=True)
        assert following.budget.state().reason_code == SH.JUDGE_REASON_FASTING
        assert following.guard.probe()["ok"] is False

        ignoring = build_runtime(events_dir=events_dir, tmp_path=tmp_path,
                                 follow_fasting=False)
        assert ignoring.budget.state().blocked is False
        assert ignoring.guard.probe()["ok"] is True

    def test_partial_fasting_factor_shrinks_budget(self, events_dir, tmp_path,
                                                  monkeypatch):
        """断食系数 0.5 ⇒ 可用预算减半（可配，且**如实标注**系数）"""
        monkeypatch.setattr(JR, "cost_policy_factor", lambda env=None: 0.5)
        runtime = build_runtime(events_dir=events_dir, tmp_path=tmp_path, budget=100.0)
        state = runtime.budget.state()
        assert state.fasting_factor == pytest.approx(0.5)
        assert state.budget_cents == pytest.approx(100.0)
        assert state.remaining_cents == pytest.approx(50.0)

    def test_state_payload_is_json_safe(self, events_dir, tmp_path):
        runtime = build_runtime(events_dir=events_dir, tmp_path=tmp_path)
        json.dumps(runtime.budget.to_dict(), ensure_ascii=False, default=str)

    def test_disabled_runtime_never_blocks_or_emits(self, events_dir, tmp_path):
        """**未启用 ≠ 回落**：默认关闭时不得发"降级"事件（免得把默认态报成故障）"""
        runtime = build_runtime(events_dir=events_dir, tmp_path=tmp_path,
                                enabled=False)
        assert runtime.availability.state == JR.AVAILABILITY_DISABLED
        assert runtime.guard.to_dict()["precheck_blocks"] == 0   # 从不做预算前置拦截
        assert runtime.guard.probe()["ok"] is False
        assert runtime.budget.recorded == []                     # 零成本
        assert ev.iter_events(types=(ev.EV_MODEL_DEGRADED,),
                              directory=events_dir) == []


# ════════════════════════════════════════════════════════════
#  3. ShadowRunner 集成（真实通道走既有注入点；批内标签如实）
# ════════════════════════════════════════════════════════════


class TestShadowRunnerIntegration:
    def test_runner_reports_precise_llm_kind(self, tmp_path, events_dir):
        case_set = make_case_set(24)
        result, store = issue_passport(case_set, tmp_path)
        assert result.passed
        runtime = build_runtime(events_dir=events_dir, tmp_path=tmp_path)
        runner = build_runner(tmp_path=tmp_path, runtime=runtime, store=store)
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=40)
        assert report.judge_kind == "llm:probe:gpt-4o-mini"
        assert report.judge_is_llm is True
        assert report.judge["runtime"]["availability"]["state"] == "available"
        assert report.negative == 0

    def test_runner_records_judge_cost_in_utc(self, tmp_path, events_dir):
        case_set = make_case_set(24)
        _, store = issue_passport(case_set, tmp_path)
        runtime = build_runtime(events_dir=events_dir, tmp_path=tmp_path)
        runner = build_runner(tmp_path=tmp_path, runtime=runtime, store=store)
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=40)
        assert report.total >= 1
        assert runtime.budget.recorded                  # 每次真实调用都记了账
        daily = U.utc_daily(directory=events_dir)
        assert daily["judge_calls"] == len(runtime.budget.recorded)
        assert daily["judge_cost_normalized_cents"] > 0
        assert daily["business_calls"] == 0             # 灰度不产生业务成本

    def test_per_sample_judge_kind_is_truthful_when_budget_runs_out(
            self, tmp_path, events_dir, monkeypatch):
        """**同一批样本内 judge_kind 可解释**：超预算后逐样本标签跟着实际判定器走

        桩替身：探针（1 次真实调用）后仍放行，第 1 个样本又记 1 笔 ⇒ 从第 2 个样本
        起预算判定为超限 ⇒ 回落。于是"过渡样本"也必须带回落后的标签。
        """
        case_set = make_case_set(24)
        _, store = issue_passport(case_set, tmp_path)
        runtime = build_runtime(events_dir=events_dir, tmp_path=tmp_path,
                                budget=1000.0)
        runner = build_runner(tmp_path=tmp_path, runtime=runtime, store=store)

        def fake_spent():
            spent = 9999.0 if len(runtime.budget.recorded) >= 2 else 0.0
            return {"day": runtime.budget.day, "source": "judge",
                    "cost_normalized_cents": spent, "cost_raw_cents": spent,
                    "calls": len(runtime.budget.recorded), "error": ""}

        monkeypatch.setattr(runtime.budget, "spent", fake_spent)
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=40)
        assert report.total >= 3, "抽样预算过小，用例无法观察批内回落"
        kinds = [s.judge_kind for s in report.samples]
        assert kinds[0] == "llm:probe:gpt-4o-mini"
        assert kinds[-1] == "deterministic_local(budget_exceeded)"
        assert report.judge_kind == "deterministic_local(budget_exceeded)"
        assert report.judge_is_llm is False
        assert report.judge["guard"]["reason_code"] == SH.JUDGE_REASON_BUDGET_EXCEEDED
        # 逐样本真值：过渡样本之后的每一个都必须是回落标签（不得留不实标注）
        first_fallback = kinds.index("deterministic_local(budget_exceeded)")
        assert all(k == "deterministic_local(budget_exceeded)"
                   for k in kinds[first_fallback:])

    def test_disabled_runner_labels_disabled_and_stays_off(self, tmp_path, events_dir):
        case_set = make_case_set(24)
        _, store = issue_passport(case_set, tmp_path)
        runtime = build_runtime(events_dir=events_dir, tmp_path=tmp_path,
                                enabled=False,
                                invoke=lambda prompt: pytest.fail("默认关闭时不得调用模型"))
        runner = build_runner(tmp_path=tmp_path, runtime=runtime, store=store)
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=40)
        assert report.judge_kind == "deterministic_local(disabled)"
        assert report.judge_is_llm is False
        assert report.negative == 0
        assert ev.iter_events(types=(ev.EV_MODEL_DEGRADED,),
                              directory=events_dir) == []

    def test_no_credentials_runner_never_claims_llm(self, tmp_path, events_dir):
        case_set = make_case_set(24)
        _, store = issue_passport(case_set, tmp_path)
        runtime = JR.build_judge_runtime(
            JR.JudgeConfig(enabled=True, provider="openai", model="gpt-4o-mini"),
            env={}, secret_provider=lambda name: None,
            dotenv_path=str(tmp_path / "absent.env"), events_dir=events_dir,
            capability_id=CAP)
        runner = build_runner(tmp_path=tmp_path, runtime=runtime, store=store)
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=40)
        assert report.judge_kind == "deterministic_local(no_credentials)"
        assert report.judge_is_llm is False

    def test_runner_report_has_no_secret(self, tmp_path, events_dir):
        case_set = make_case_set(24)
        _, store = issue_passport(case_set, tmp_path)
        runtime = JR.build_judge_runtime(
            JR.JudgeConfig(enabled=True, provider="openai", model="gpt-4o-mini"),
            env={}, secret_provider=lambda n: SECRET if n == "OPENAI_API_KEY" else None,
            dotenv_path=str(tmp_path / "absent.env"), events_dir=events_dir,
            capability_id=CAP)
        runner = build_runner(tmp_path=tmp_path, runtime=runtime, store=store)
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=40)
        text = json.dumps(report.to_dict(), ensure_ascii=False, default=str)
        assert SECRET not in text
        assert report.judge["runtime"]["availability"]["credential"]["fingerprint"]

    def test_runtime_and_explicit_sandbox_judge_conflict_raises(self, tmp_path,
                                                               events_dir):
        """两套判定器并存会让 `judge_kind` 不再如实 ⇒ **显式报错**，不静默择一"""
        from agent.digestion.sandbox import ReplaySandbox
        runtime = build_runtime(events_dir=events_dir, tmp_path=tmp_path)
        box = ReplaySandbox(judge=lambda a, b: 1.0)
        with pytest.raises(ValueError, match="judge_runtime"):
            SH.ShadowRunner(sandbox=box, judge_runtime=runtime)

    def test_verdict_store_records_structured_verdicts(self, tmp_path, events_dir):
        case_set = make_case_set(24)
        _, store = issue_passport(case_set, tmp_path)
        verdicts = JR.JudgeVerdictStore(str(tmp_path / "shadow" / "judge.jsonl"))
        runtime = build_runtime(events_dir=events_dir, tmp_path=tmp_path,
                                verdict_store=verdicts)
        runner = build_runner(tmp_path=tmp_path, runtime=runtime, store=store)
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=40)
        summary = verdicts.summary(CAP)
        assert summary["stored"] == report.total
        assert summary["by_verdict"] == {"pass": report.total}
        row = verdicts.rows()[0]
        assert row["confidence"] == pytest.approx(0.95)
        assert row["reason"] == "stub"
        assert row["judge_kind"] == "llm:probe:gpt-4o-mini"
        assert row["format"] == "structured"

    def test_judge_verdicts_do_not_become_manual_reviews(self, tmp_path, events_dir):
        """judge 判定**不得**被当成人工裁定（M5 口径的核心不变量）"""
        case_set = make_case_set(24)
        _, store = issue_passport(case_set, tmp_path)
        review_queue = SH.ManualReviewQueue(str(tmp_path / "shadow" / "r.jsonl"))
        verdicts = JR.JudgeVerdictStore(str(tmp_path / "shadow" / "judge.jsonl"))
        runtime = build_runtime(events_dir=events_dir, tmp_path=tmp_path,
                                verdict_store=verdicts)
        runner = build_runner(tmp_path=tmp_path, runtime=runtime, store=store,
                              review_queue=review_queue)
        runner.run(CAP, case_set=case_set, force=True, daily_avg=40)
        assert verdicts.summary(CAP)["stored"] > 0
        # 抽检队列只有"待裁定"的人工项，绝无 judge 结论混入
        assert all(item.verdict == "" for item in review_queue.items(CAP))


# ════════════════════════════════════════════════════════════
#  4. 抽检一致率统计与分歧入队
# ════════════════════════════════════════════════════════════


def seed_consistency(tmp_path, pairs, *, uncertain=(),
                     verdict_store=None, review_queue=None):
    """铺 (judge_verdict, human_verdict) 对数，返回 (store, queue)"""
    store = verdict_store or JR.JudgeVerdictStore(str(tmp_path / "j.jsonl"))
    queue = review_queue or SH.ManualReviewQueue(str(tmp_path / "r.jsonl"))
    for index, (judge_verdict, human_verdict) in enumerate(pairs):
        case_id = f"case-{index:03d}"
        store.record(capability_id=CAP, case_id=case_id, verdict=judge_verdict,
                     confidence=0.9, reason="stub", judge_kind="llm:probe:fake",
                     judge_score=0.9, manual_flagged=True)
        queue.enqueue(CAP, [case_id], reasons={case_id: ["10% 抽检"]})
        verdict = human_verdict or ("uncertain" if index in uncertain else "")
        if verdict:
            queue.record_review(case_id, capability_id=CAP, verdict=verdict,
                                reviewer="owner", note="")
    return store, queue


class TestConsistency:
    def test_small_sample_only_discloses(self, tmp_path):
        """样本 <20 ⇒ 只披露不结论（S5-02 口径）"""
        store, queue = seed_consistency(
            tmp_path, [("pass", "pass")] * 4 + [("fail", "pass")])
        report = JR.judge_consistency(verdict_store=store, review_queue=queue,
                                      capability_id=CAP)
        assert report["samples"] == 5
        assert report["agree"] == 4
        assert report["disagree"] == 1
        assert report["agreement_rate"] == pytest.approx(0.8)
        assert report["conclusion"] == ""
        assert "只披露不结论" in report["disclosure"]

    def test_enough_samples_reaches_a_conclusion(self, tmp_path):
        store, queue = seed_consistency(
            tmp_path, [("pass", "pass")] * 18 + [("fail", "fail")] * 2)
        report = JR.judge_consistency(verdict_store=store, review_queue=queue,
                                      capability_id=CAP)
        assert report["samples"] == 20
        assert report["agreement_rate"] == pytest.approx(1.0)
        assert report["conclusion"]
        assert report["disclosure"] == ""

    def test_uncertain_is_excluded_from_the_denominator(self, tmp_path):
        """人工也给不出结论（uncertain）⇒ **不进分母**（否则算成 judge 错）"""
        store, queue = seed_consistency(
            tmp_path, [("pass", "pass"), ("pass", ""), ("fail", "")],
            uncertain={1, 2})
        report = JR.judge_consistency(verdict_store=store, review_queue=queue,
                                      capability_id=CAP)
        assert report["samples"] == 1 and report["agree"] == 1
        assert report["uncertain"] == 2
        assert report["agreement_rate"] == pytest.approx(1.0)

    def test_undecided_is_counted_separately(self, tmp_path):
        store, queue = seed_consistency(tmp_path, [("pass", "pass"), ("pass", "")])
        report = JR.judge_consistency(verdict_store=store, review_queue=queue,
                                      capability_id=CAP)
        assert report["undecided"] == 1
        assert report["samples"] == 1

    def test_disagreements_list_carries_both_sides(self, tmp_path):
        store, queue = seed_consistency(tmp_path, [("pass", "fail")])
        report = JR.judge_consistency(verdict_store=store, review_queue=queue,
                                      capability_id=CAP)
        entry = report["disagreements"][0]
        assert entry["judge_verdict"] == "pass" and entry["human_verdict"] == "fail"
        assert entry["case_id"] == "case-000"
        assert entry["judge_kind"] == "llm:probe:fake"
        assert entry["reviewer"] == "owner"

    def test_disagreements_can_be_enqueued(self, tmp_path):
        """分歧样本入 `ManualReviewQueue`（作为调提示词/阈值的依据）"""
        store, queue = seed_consistency(
            tmp_path, [("pass", "fail"), ("fail", "pass"), ("pass", "pass")])
        report = JR.judge_consistency(verdict_store=store, review_queue=queue,
                                      capability_id=CAP,
                                      enqueue_disagreements=True)
        assert sorted(report["enqueued"]) == ["case-000", "case-001"]
        pending = {i.case_id: i for i in queue.pending(CAP)}
        assert "case-000" in pending and "case-001" in pending
        assert any("分歧" in r for r in pending["case-000"].reasons)

    def test_agreement_rate_is_none_without_comparable_samples(self, tmp_path):
        store, queue = seed_consistency(tmp_path, [("pass", "")])
        report = JR.judge_consistency(verdict_store=store, review_queue=queue,
                                      capability_id=CAP)
        assert report["samples"] == 0
        assert report["agreement_rate"] is None
        assert report["disclosure"]

    def test_payload_is_json_safe(self, tmp_path):
        store, queue = seed_consistency(tmp_path, [("pass", "fail")])
        report = JR.judge_consistency(verdict_store=store, review_queue=queue,
                                      capability_id=CAP)
        json.dumps(report, ensure_ascii=False, default=str)

    def test_window_uses_calendar_day_not_epoch_prefix(self, tmp_path):
        """窗口用**日历日**（与 `utc.utc_daily` 同日桶），不是 epoch 字符串前缀"""
        store, queue = seed_consistency(tmp_path, [("pass", "pass")])
        report = JR.judge_consistency(verdict_store=store, review_queue=queue,
                                      capability_id=CAP)
        days = report["window"]["judge_days"]
        assert days and all(len(d) == 10 and d[4] == "-" for d in days)
        assert days[0] == store.rows()[0]["day"]

    def test_verdict_store_isolated_from_manual_ledger_files(self, tmp_path):
        """两本账**不同文件**（judge 独立存档；人工台账语义不被污染）"""
        store, queue = seed_consistency(tmp_path, [("pass", "pass")])
        assert store.path != queue.path
        assert store.rows()[0]["kind"] == "judge_verdict"
        assert "kind" not in {k for k in queue.rows()[0]} or \
            queue.rows()[0]["kind"] in ("queued", "reviewed")
