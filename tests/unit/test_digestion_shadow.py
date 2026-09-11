"""TASK-S3-03 shadow 灰度单测（`agent/digestion/shadow.py`）

验收对应：
- 抽样 trace_id 哈希**确定性**（同批两次一致）；日预算 ``min(日均×15%, 50)`` 不超；
- **灰度 5% 开关默认关闭**，开启需**显式阈值**（非法值回退关闭）；
- 三层比对任一层失败即**负例**；劣化（R4）信号与降级建议；
- **【M1】** judge_kind 可区分（LLM-judge vs 确定性打分器；LLM 不可用时如实回落）；
- **【M2】** 条件⑤ 用**真实墙钟** p99（与 S3-02 的模型时钟量分开标注）；
- **【M4】** 用例 ↔ 候选适用性过滤（显式字段 + 排除清单）；
- **【M5】** 10% 人工抽检**实际复核动作留痕**（未闭合不得视为已验收）；
- **【M6】** 隔离边界显式声明（进程内确定性模型，非容器隔离）；
- **凭 `PassportStore` 通行证**放行（无证 fail-closed，不自建开关绕门）。

判定集 / 通行证 / 灰度台账 / 人工抽检 / 事件 / 审计全部隔离到 tmp_path。
"""

from __future__ import annotations

import json

import pytest

from agent.audit import facade as facade_mod
from agent.audit.chain import AuditChain
from agent.digestion import cases as C
from agent.digestion import gate as G
from agent.digestion import shadow as SH
from agent.digestion.sandbox import ProgramImplementation, ReplaySandbox

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
    facade_mod.audit.enabled = True
    facade_mod.audit.reset_counters()
    yield facade_mod.audit
    facade_mod.audit.bind(previous)


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    """判定集/通行证/灰度台账/抽检的**默认落点**一律隔离（防运行时目录污染）"""
    monkeypatch.setenv("CP_EVENTS_DIR", str(tmp_path / "events"))
    monkeypatch.setenv(C.CASE_ROOT_ENV, str(tmp_path / "cases"))
    monkeypatch.setenv(SH.SHADOW_DIR_ENV, str(tmp_path / "shadow"))
    import agent.observability.events as events_mod
    events_mod.reset_event_stores()
    yield tmp_path
    events_mod.reset_event_stores()


@pytest.fixture
def runner(tmp_path):
    """默认灰度器（judge 用确定性打分器；台账/抽检落 tmp）"""
    return SH.ShadowRunner(
        judge=SH.resolve_judge("local").scorer, judge_kind=SH.JUDGE_KIND_LOCAL,
        passport_store=G.PassportStore(str(tmp_path / "cases")),
        case_store=C.open_case_store(str(tmp_path / "cases")),
        ledger=SH.ShadowLedger(str(tmp_path / "shadow" / "ledger.jsonl")),
        review_queue=SH.ManualReviewQueue(str(tmp_path / "shadow" / "reviews.jsonl")),
        env={}, emit_events=False)


# ════════════════════════════════════════════════════════════
#  构造工具
# ════════════════════════════════════════════════════════════


def make_case(index: int = 0, *, root: str = "C:/sandbox") -> C.EquivalenceCase:
    """一条可回放的三段任务链用例（上游程序 = 候选程序 ⇒ 三层比对全过）"""
    path = f"{root}/out/a{index}.txt"
    steps = [C.ProgramStep(label="read_file", params={"path": path}),
             C.ProgramStep(label="write_file", params={"path": path,
                                                       "content": f"c{index}"})]
    return C.EquivalenceCase(
        case_id=f"case-{index:03d}", capability_id=CAP, input={"path": path},
        upstream=steps, native=steps,
        fixtures={path: f"c{index}"},
        expected_side_effects={"files_written": [path]},
        expected_status="success", sandbox_root=root)


def make_case_set(size: int = 4, **kwargs) -> C.CaseSet:
    return C.build_case_set(CAP, [make_case(i, **kwargs) for i in range(size)])


def issue_passport(case_set: C.CaseSet, tmp_path, **kwargs):
    """走 S3-02 验收门**真实发证**（测试要测"凭通行证放行"，就必须先有真证）"""
    if case_set.size < G.GATE_REPLAY_MIN:
        cases = list(case_set.cases)
        for i in range(len(cases), G.GATE_REPLAY_MIN):
            cases.append(make_case(i))
        case_set = C.build_case_set(CAP, cases, version=case_set.version)
    store = G.PassportStore(str(tmp_path / "cases"))
    result = G.acceptance_gate(CAP, case_set=case_set, passport_store=store,
                               emit_events=False, **kwargs)
    return result, store


# ════════════════════════════════════════════════════════════
#  抽样与预算（§4.5）
# ════════════════════════════════════════════════════════════


class TestSampling:
    def test_hash_fraction_is_deterministic_and_bounded(self):
        values = [SH.hash_fraction(f"trace-{i}") for i in range(50)]
        assert values == [SH.hash_fraction(f"trace-{i}") for i in range(50)]
        assert all(0.0 <= v < 1.0 for v in values)

    def test_same_batch_sampled_twice_identically(self):
        ids = [f"t{i:03d}" for i in range(500)]
        assert SH.deterministic_sample(ids, 20) == SH.deterministic_sample(ids, 20)

    def test_sample_is_order_insensitive(self):
        ids = [f"t{i:03d}" for i in range(100)]
        assert (SH.deterministic_sample(ids, 10)
                == SH.deterministic_sample(list(reversed(ids)), 10))

    def test_sample_respects_size(self):
        assert len(SH.deterministic_sample([f"t{i}" for i in range(30)], 7)) == 7
        assert SH.deterministic_sample([], 5) == []
        assert SH.deterministic_sample(["a", "b"], 0) == []

    def test_sample_size_larger_than_universe(self):
        assert SH.deterministic_sample(["a", "b"], 99) == ["a", "b"]

    def test_budget_formula_matches_design(self):
        # §4.5：每日预算 = min(日均 × 15%, 50)
        assert SH.daily_budget(1000) == 50          # 150 → 上限 50
        assert SH.daily_budget(200) == 30           # 30
        assert SH.daily_budget(120) == 18
        assert SH.daily_budget(0) == 0

    def test_budget_never_exceeds_cap(self):
        for avg in (1, 7, 33, 99, 333, 9999):
            assert SH.daily_budget(avg) <= SH.SHADOW_BUDGET_CAP

    def test_budget_low_traffic_floor(self):
        # T2 同源：低流量下 15% 取整为 0 ⇒ 保底 1（否则永远无样本）
        assert SH.daily_budget(3) == 1
        assert SH.daily_budget(3, min_budget=0) == 0

    def test_budget_invalid_inputs_fall_back(self):
        assert SH.daily_budget("nonsense") == 0
        assert SH.daily_budget(100, ratio="x") == 15      # 非法比例回退 0.15
        assert SH.daily_budget(100, cap=-5) == 0

    def test_budget_from_env_invalid_values_fall_back(self):
        params = SH.budget_from_env({"CP_DIGESTION_SHADOW_BUDGET_RATIO": "abc",
                                     "CP_DIGESTION_SHADOW_BUDGET_CAP": "zzz"})
        assert params["ratio"] == SH.SHADOW_BUDGET_RATIO
        assert params["cap"] == SH.SHADOW_BUDGET_CAP

    def test_budget_from_env_accepts_valid_values(self):
        params = SH.budget_from_env({"CP_DIGESTION_SHADOW_BUDGET_RATIO": "0.25",
                                     "CP_DIGESTION_SHADOW_BUDGET_CAP": "9",
                                     "CP_DIGESTION_SHADOW_MIN_BUDGET": "2"})
        assert (params["ratio"], params["cap"], params["min_budget"]) == (0.25, 9, 2)


# ════════════════════════════════════════════════════════════
#  开关（**默认关闭**）
# ════════════════════════════════════════════════════════════


class TestSwitches:
    def test_shadow_disabled_by_default(self):
        assert SH.shadow_enabled(env={}) is False
        enabled, reason = SH.shadow_enabled(reason=True, env={})
        assert enabled is False and SH.SHADOW_ENABLE_ENV in reason

    def test_shadow_enabled_requires_explicit_flag(self):
        assert SH.shadow_enabled(env={SH.SHADOW_ENABLE_ENV: "true"}) is True
        assert SH.shadow_enabled(env={SH.SHADOW_ENABLE_ENV: "0"}) is False
        assert SH.shadow_enabled(env={SH.SHADOW_ENABLE_ENV: "maybe"}) is False


class TestGrayPolicy:
    def test_gray_off_by_default(self):
        policy = SH.resolve_gray_policy()
        assert policy["enabled"] is False and policy["ratio"] == 0.0
        assert policy["source"] == "default_off"
        assert policy["real_takeover"] is False
        assert policy["candidate_execution"] == SH.TRANSPORT_SANDBOX_ONLY

    def test_gray_env_requires_enable_and_ratio(self):
        only_ratio = SH.resolve_gray_policy(
            env={SH.GRAY_RATIO_ENV: "0.05"})
        assert only_ratio["enabled"] is False        # 只有阈值不构成开启
        both = SH.resolve_gray_policy(env={SH.GRAY_ENABLE_ENV: "true",
                                           SH.GRAY_RATIO_ENV: "0.05"})
        assert both["enabled"] is True and both["ratio"] == 0.05
        assert both["source"] == "env"

    def test_gray_invalid_ratio_falls_back_to_off(self):
        for bad in ("abc", "0", "-1", "3"):
            policy = SH.resolve_gray_policy(env={SH.GRAY_ENABLE_ENV: "true",
                                                 SH.GRAY_RATIO_ENV: bad})
            assert policy["enabled"] is False, bad
            assert policy["ratio"] == SH.GRAY_RATIO_DEFAULT

    def test_gray_descriptor_config_wins_over_env(self):
        policy = SH.resolve_gray_policy(
            shadow_config={"enabled": True, "gray_ratio": 0.2},
            env={SH.GRAY_ENABLE_ENV: "true", SH.GRAY_RATIO_ENV: "0.05"})
        assert policy["source"] == "descriptor.shadow_config"
        assert policy["ratio"] == 0.2

    def test_gray_descriptor_enabled_without_ratio_stays_off(self):
        policy = SH.resolve_gray_policy(shadow_config={"enabled": True})
        assert policy["enabled"] is False
        assert any("未给出合法 gray_ratio" in r for r in policy["reasons"])

    def test_gray_descriptor_disabled_is_respected(self):
        policy = SH.resolve_gray_policy(shadow_config={"enabled": False,
                                                       "gray_ratio": 0.5})
        assert policy["enabled"] is False

    def test_gray_routed_is_deterministic_subset(self):
        ids = [f"t{i:03d}" for i in range(200)]
        routed = SH.gray_routed_ids(ids, ratio=0.05)
        assert routed == SH.gray_routed_ids(ids, ratio=0.05)
        assert 0 < len(routed) < len(ids)
        assert set(routed) <= set(ids)

    def test_gray_routed_empty_for_invalid_ratio(self):
        assert SH.gray_routed_ids(["a", "b"], ratio=0.0) == []
        assert SH.gray_routed_ids(["a", "b"], ratio="x") == []


# ════════════════════════════════════════════════════════════
#  judge（M1：真实 LLM-judge 与确定性打分器**可区分**）
# ════════════════════════════════════════════════════════════


class TestJudge:
    def test_parse_scores_from_common_shapes(self):
        assert SH.parse_judge_score('{"score": 0.87, "reason": "x"}') == 0.87
        assert SH.parse_judge_score("0.9") == 0.9
        assert SH.parse_judge_score("87%") == 0.87
        assert SH.parse_judge_score("score: 1") == 1.0

    def test_parse_returns_none_on_garbage(self):
        assert SH.parse_judge_score("无法判断") is None
        assert SH.parse_judge_score("") is None
        assert SH.parse_judge_score(None) is None

    def test_llm_judge_scores_via_injected_invoke(self):
        calls = []

        def invoke(prompt):
            calls.append(prompt)
            return '{"score": 0.91}'

        judge = SH.LLMJudge(invoke=invoke)
        assert judge.is_available() is True
        assert judge.score("a", "b")["score"] == 0.91
        assert judge.score("a", "b")["kind"] == SH.JUDGE_KIND_LLM
        assert judge.calls == 2 and judge.usage["prompt_chars"] > 0
        assert "等价" in calls[0]

    def test_llm_judge_unparseable_reply_is_unavailable(self):
        judge = SH.LLMJudge(invoke=lambda prompt: "嗯")
        with pytest.raises(SH.JudgeUnavailable):
            judge.score("a", "b")

    def test_llm_judge_channel_resolves_without_probe_call(self):
        calls = []
        judge = SH.LLMJudge(invoke=lambda prompt: calls.append(1) or '{"score": 1}')
        assert judge.is_available() is True
        assert calls == []          # 可用性探测**不**发起模型调用

    def test_llm_judge_probe_detects_broken_channel(self):
        judge = SH.LLMJudge(invoke=_raiser)
        result = judge.probe()
        assert result["ok"] is False and "RuntimeError" in result["reason"]

    def test_llm_judge_probe_ok_on_working_channel(self):
        judge = SH.LLMJudge(invoke=lambda prompt: '{"score": 1.0}')
        assert judge.probe()["ok"] is True

    def test_llm_judge_call_failure_is_unavailable(self):
        judge = SH.LLMJudge(invoke=_raiser)
        with pytest.raises(SH.JudgeUnavailable):
            judge.score("a", "b")

    def test_llm_judge_without_credentials_is_unavailable(self, monkeypatch):
        monkeypatch.delenv(SH.JUDGE_PROVIDER_ENV, raising=False)
        monkeypatch.delenv(SH.JUDGE_MODEL_ENV, raising=False)
        judge = SH.LLMJudge()
        assert judge.is_available() is False

    def test_resolve_judge_local_mode(self):
        resolved = SH.resolve_judge("local", env={})
        assert resolved.kind == SH.JUDGE_KIND_LOCAL and resolved.is_llm is False

    def test_resolve_judge_auto_falls_back_with_reason(self):
        resolved = SH.resolve_judge("auto", env={})
        assert resolved.kind == SH.JUDGE_KIND_LLM_FALLBACK
        assert resolved.is_llm is False
        assert "unavailable_reason" in resolved.detail

    def test_resolve_judge_llm_mode_uses_injected_channel(self):
        resolved = SH.resolve_judge("llm", invoke=lambda p: '{"score": 0.99}',
                                    env={})
        assert resolved.kind == SH.JUDGE_KIND_LLM and resolved.is_llm is True
        assert resolved.scorer("a", "b") == 0.99

    def test_resolve_judge_llm_mode_falls_back_when_unavailable(self):
        resolved = SH.resolve_judge("llm", env={SH.JUDGE_PROVIDER_ENV: "nope",
                                                SH.JUDGE_MODEL_ENV: "nope"})
        assert resolved.scorer is not None
        assert resolved.kind != SH.JUDGE_KIND_LLM

    def test_resolve_judge_explicit_injection_wins(self):
        resolved = SH.resolve_judge(judge=lambda a, b: 0.5, env={})
        assert resolved.kind == SH.JUDGE_KIND_INJECTED

    def test_resolve_judge_invalid_mode_falls_back_to_auto(self):
        resolved = SH.resolve_judge("nonsense", env={})
        assert resolved.mode == SH.JUDGE_MODE_AUTO

    def test_resolved_judge_payload_is_json_safe(self):
        resolved = SH.resolve_judge("llm", invoke=lambda p: '{"score": 0.9}',
                                    env={})
        json.dumps(resolved.to_dict(), ensure_ascii=False)   # 不含活对象

    def test_judge_kind_recorded_in_sandbox_layer(self):
        from agent.digestion.sandbox import Observation, three_layer_diff

        case = make_case()
        impl = ProgramImplementation(case.upstream)
        left = impl.run(case)
        right = impl.run(case)
        result = three_layer_diff(left, right, case, judge=lambda a, b: 1.0,
                                  judge_kind=SH.JUDGE_KIND_LLM)
        assert result.layer("judge").detail["judge_kind"] == SH.JUDGE_KIND_LLM
        assert isinstance(left, Observation)


def _raiser(prompt):
    raise RuntimeError("no channel")


class FakeAdapter:
    """模型适配器替身（证明**真实 LLM 通道**可跑通，无需凭证）"""

    def __init__(self, reply='{"score": 0.93}', *, available=True, raises=None):
        self.reply = reply
        self.available = available
        self.raises = raises
        self.prompts = []

    def is_available(self):
        return self.available

    def generate(self, prompt, **kwargs):
        self.prompts.append(prompt)
        if self.raises:
            raise self.raises
        return {"text": self.reply}


class TestLLMJudgeAdapterPath:
    def test_real_adapter_channel_scores(self):
        adapter = FakeAdapter('{"score": 0.93, "reason": "等价"}')
        judge = SH.LLMJudge(adapter=adapter)
        assert judge.is_available() is True
        assert judge.score("a", "b")["score"] == 0.93
        assert judge.score("a", "b")["kind"] == SH.JUDGE_KIND_LLM
        assert adapter.prompts and "等价性判定器" in adapter.prompts[0]

    def test_adapter_unavailable_is_reported(self):
        judge = SH.LLMJudge(adapter=FakeAdapter(available=False))
        assert judge.is_available() is False
        assert "不可用" in judge.unavailable_reason

    def test_adapter_exception_becomes_unavailable(self):
        judge = SH.LLMJudge(adapter=FakeAdapter(raises=RuntimeError("429")))
        with pytest.raises(SH.JudgeUnavailable):
            judge.score("a", "b")

    def test_adapter_plain_string_reply(self):
        judge = SH.LLMJudge(adapter=FakeAdapter("0.5"))
        assert judge.score("a", "b")["score"] == 0.5

    def test_adapter_dict_without_text_is_serialized(self):
        class DictAdapter(FakeAdapter):
            def generate(self, prompt, **kwargs):
                return {"score": 0.8}

        judge = SH.LLMJudge(adapter=DictAdapter())
        assert judge.score("a", "b")["score"] == 0.8

    def test_adapter_factory_construction_failure_is_reported(self, monkeypatch):
        class BoomFactory:
            @staticmethod
            def create(provider, model):
                raise RuntimeError("factory down")

        monkeypatch.setattr("agent.model_router.adapters.ModelAdapterFactory",
                            BoomFactory, raising=False)
        judge = SH.LLMJudge(provider="openai", model="gpt-4o-mini")
        assert judge.is_available() is False

    def test_judge_guard_probe_falls_back_on_broken_llm(self):
        guard = SH.JudgeGuard(_raiser, kind_primary=SH.JUDGE_KIND_LLM)
        result = guard.probe()
        assert result["ok"] is False
        assert guard.effective_kind == SH.JUDGE_KIND_LLM_FALLBACK
        assert guard.fallbacks == 1 and guard.reasons

    def test_judge_guard_keeps_llm_label_when_healthy(self):
        guard = SH.JudgeGuard(lambda a, b: 0.9, kind_primary=SH.JUDGE_KIND_LLM)
        assert guard.probe()["ok"] is True
        assert guard.effective_kind == SH.JUDGE_KIND_LLM
        assert guard("a", "b") == 0.9

    def test_judge_guard_no_probe_for_local_kind(self):
        guard = SH.JudgeGuard(lambda a, b: 0.4, kind_primary=SH.JUDGE_KIND_LOCAL)
        assert guard.probe()["ok"] is True
        assert guard.effective_kind == SH.JUDGE_KIND_LOCAL

    def test_judge_guard_falls_back_mid_run(self):
        calls = {"n": 0}

        def flaky(a, b):
            calls["n"] += 1
            if calls["n"] > 2:          # 探针 + 首次调用成功，之后通道中断
                raise SH.JudgeUnavailable("quota")
            return 0.9

        guard = SH.JudgeGuard(flaky, kind_primary=SH.JUDGE_KIND_LLM)
        assert guard.probe()["ok"] is True          # 探针成功
        assert guard("a", "b") == 0.9
        assert guard("a", "b") == SH.judge_similarity("a", "b")   # 运行中回落
        assert guard.effective_kind == SH.JUDGE_KIND_LLM_FALLBACK
        assert guard.fallbacks == 1

    def test_guard_payload_is_json_safe(self):
        guard = SH.JudgeGuard(lambda a, b: 1.0, kind_primary=SH.JUDGE_KIND_LOCAL)
        guard.probe()
        json.dumps(guard.to_dict(), ensure_ascii=False)

    def test_runner_uses_fallback_label_without_false_negatives(self, tmp_path):
        """LLM 通道坏掉时：**如实回落**且**不把整批样本误判成负例**（M1 关键行为）"""
        case_set = make_case_set(24)
        result, store = issue_passport(case_set, tmp_path)
        assert result.passed

        def broken(a, b):
            raise RuntimeError("llm down")

        runner = SH.ShadowRunner(
            judge=broken, judge_kind=SH.JUDGE_KIND_LLM, passport_store=store,
            case_store=C.open_case_store(str(tmp_path / "cases")),
            ledger=SH.ShadowLedger(str(tmp_path / "shadow" / "l.jsonl")),
            review_queue=SH.ManualReviewQueue(str(tmp_path / "shadow" / "r.jsonl")),
            env={}, emit_events=False)
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=40)
        assert report.judge_kind == SH.JUDGE_KIND_LLM_FALLBACK
        assert report.judge["probe"]["ok"] is False
        assert report.negative == 0 and report.pass_rate == 1.0

    def test_runner_uses_llm_label_with_healthy_channel(self, tmp_path):
        case_set = make_case_set(24)
        result, store = issue_passport(case_set, tmp_path)
        assert result.passed
        runner = SH.ShadowRunner(
            judge=SH.LLMJudge(adapter=FakeAdapter('{"score": 1.0}')),
            judge_kind=SH.JUDGE_KIND_LLM, passport_store=store,
            case_store=C.open_case_store(str(tmp_path / "cases")),
            ledger=SH.ShadowLedger(str(tmp_path / "shadow" / "l.jsonl")),
            review_queue=SH.ManualReviewQueue(str(tmp_path / "shadow" / "r.jsonl")),
            env={}, emit_events=False)
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=20)
        assert report.judge_kind == SH.JUDGE_KIND_LLM and report.judge_is_llm is True
        assert report.negative == 0
        assert report.judge["effective_kind"] == SH.JUDGE_KIND_LLM
        assert report.overhead["judge_usage"]["prompt_chars"] > 0


# ════════════════════════════════════════════════════════════
#  三层比对流水线（compare → CompareVerdict）
# ════════════════════════════════════════════════════════════


class TestCompareVerdict:
    def test_compare_passes_for_equivalent_implementation(self):
        case = make_case()
        verdict = SH.compare(case, case.upstream)
        assert verdict.passed is True and verdict.negative is False
        assert verdict.failed_layers == [] and verdict.reasons == []
        assert [layer["layer"] for layer in verdict.layers] == [
            "structure", "side_effects", "judge"]
        assert verdict.judge_score >= SH.JUDGE_THRESHOLD

    def test_compare_fails_when_candidate_misses_step(self):
        case = make_case()
        verdict = SH.compare(case, case.upstream[:1])
        assert verdict.passed is False and verdict.negative is True
        assert verdict.failed_layers and verdict.reasons
        assert "structure" in verdict.hard_layer_failures

    def test_compare_uses_real_wall_clock(self):
        case = make_case()
        verdict = SH.compare(case, case.upstream)
        assert verdict.clock == SH.CLOCK_WALL
        assert verdict.wall_ms_candidate > 0 and verdict.wall_ms_upstream > 0
        assert verdict.model_clock_ms_candidate > 0    # 模型时钟量另列

    def test_compare_records_injected_judge_kind(self):
        case = make_case()
        verdict = SH.compare(case, case.upstream, judge=lambda a, b: 0.99,
                             judge_kind=SH.JUDGE_KIND_LLM)
        assert verdict.judge_kind == SH.JUDGE_KIND_LLM
        assert verdict.to_dict()["judge_kind"] == SH.JUDGE_KIND_LLM

    def test_compare_flags_manual_sample(self):
        case = make_case()
        verdict = SH.compare(case, case.upstream, manual_flagged=True)
        assert verdict.manual_flagged is True
        assert any("人工抽检" in r for r in verdict.reasons +
                   [l["reasons"][0] for l in verdict.layers if l["reasons"]])

    def test_verdict_payload_is_json_safe(self):
        case = make_case()
        json.dumps(SH.compare(case, case.upstream).to_dict(), ensure_ascii=False)

    def test_low_judge_score_makes_negative(self):
        case = make_case()
        verdict = SH.compare(case, case.upstream, judge=lambda a, b: 0.1)
        assert verdict.passed is False
        assert verdict.hard_layer_failures == []       # 软性层失败（非硬性）
        assert "judge" in verdict.failed_layers


# ════════════════════════════════════════════════════════════
#  人工抽检队列（M5）
# ════════════════════════════════════════════════════════════


class TestManualReviewQueue:
    def test_enqueue_and_pending(self, tmp_path):
        queue = SH.ManualReviewQueue(str(tmp_path / "r.jsonl"))
        items = queue.enqueue(CAP, ["c1", "c2"], candidate_kind="candidate_pattern",
                              reasons={"c1": ["10% 抽检"]})
        assert [i.case_id for i in items] == ["c1", "c2"]
        summary = queue.summary(CAP)
        assert summary["sampled"] == 2 and summary["pending"] == 2
        assert summary["closed"] is False
        assert "不得视为已验收" in summary["note"]

    def test_enqueue_is_idempotent_for_pending(self, tmp_path):
        queue = SH.ManualReviewQueue(str(tmp_path / "r.jsonl"))
        queue.enqueue(CAP, ["c1"])
        queue.enqueue(CAP, ["c1"])
        assert len(queue.items(CAP)) == 1

    def test_record_review_closes_item(self, tmp_path):
        queue = SH.ManualReviewQueue(str(tmp_path / "r.jsonl"))
        queue.enqueue(CAP, ["c1"])
        item = queue.record_review("c1", capability_id=CAP,
                                   verdict=SH.REVIEW_VERDICT_PASS,
                                   reviewer="owner", note="逐字段核对等价")
        assert item.decided and item.approved and item.role == SH.REVIEW_ROLE_HUMAN
        summary = queue.summary(CAP)
        assert summary["closed"] is True and summary["human_reviewed"] == 1
        assert queue.is_closed(CAP) is True

    def test_record_review_rejects_invalid_verdict(self, tmp_path):
        queue = SH.ManualReviewQueue(str(tmp_path / "r.jsonl"))
        with pytest.raises(ValueError):
            queue.record_review("c1", capability_id=CAP, verdict="looks-fine",
                                reviewer="owner")

    def test_agent_assisted_review_is_distinguishable(self, tmp_path):
        queue = SH.ManualReviewQueue(str(tmp_path / "r.jsonl"))
        queue.enqueue(CAP, ["c1"])
        queue.record_review("c1", capability_id=CAP, verdict="pass",
                            reviewer="agent", role=SH.REVIEW_ROLE_AGENT)
        summary = queue.summary(CAP)
        assert summary["agent_assisted_reviewed"] == 1
        assert summary["human_reviewed"] == 0

    def test_latest_verdict_wins(self, tmp_path):
        queue = SH.ManualReviewQueue(str(tmp_path / "r.jsonl"))
        queue.enqueue(CAP, ["c1"])
        queue.record_review("c1", capability_id=CAP, verdict="uncertain",
                            reviewer="owner")
        queue.record_review("c1", capability_id=CAP, verdict="fail", reviewer="owner")
        assert queue.items(CAP)[-1].verdict == "fail"

    def test_summary_scoped_by_capability(self, tmp_path):
        queue = SH.ManualReviewQueue(str(tmp_path / "r.jsonl"))
        queue.enqueue(CAP, ["c1"])
        queue.enqueue("other.cap", ["c2"])
        assert queue.summary(CAP)["sampled"] == 1
        assert queue.summary("other.cap")["sampled"] == 1

    def test_review_sheet_marks_pending(self, tmp_path):
        queue = SH.ManualReviewQueue(str(tmp_path / "r.jsonl"))
        queue.enqueue(CAP, ["c1"])
        sheet = queue.review_sheet(CAP)
        assert "c1" in sheet and "待裁定" in sheet
        assert "不得视为已验收" in sheet

    def test_review_writes_audit(self, tmp_path):
        queue = SH.ManualReviewQueue(str(tmp_path / "r.jsonl"))
        queue.enqueue(CAP, ["c1"])
        queue.record_review("c1", capability_id=CAP, verdict="pass", reviewer="owner")
        actions = [e.action for e in facade_mod.audit.recent(limit=20)]
        assert SH.AUDIT_ACTION_REVIEWED in actions

    def test_corrupt_lines_are_skipped(self, tmp_path):
        path = tmp_path / "r.jsonl"
        queue = SH.ManualReviewQueue(str(path))
        queue.enqueue(CAP, ["c1"])
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("{ not json\n")
        assert len(queue.items(CAP)) == 1


# ════════════════════════════════════════════════════════════
#  灰度台账
# ════════════════════════════════════════════════════════════


class TestShadowLedger:
    def _report(self, samples: int = 3):
        report = SH.ShadowReport(capability_id=CAP, generated_at=1000.0)
        report.samples = [SH.ShadowSample(sample_id=f"s{i}", case_id=f"c{i}")
                          for i in range(samples)]
        report.plan.budget = 5
        return report

    def test_record_and_rows(self, tmp_path):
        ledger = SH.ShadowLedger(str(tmp_path / "l.jsonl"))
        ledger.record(self._report(4))
        rows = ledger.rows(CAP)
        assert len(rows) == 1 and rows[0]["sampled"] == 4
        assert rows[0]["capability_id"] == CAP

    def test_daily_average_from_history(self, tmp_path):
        ledger = SH.ShadowLedger(str(tmp_path / "l.jsonl"))
        ledger.record(self._report(4))
        assert ledger.daily_average(capability_id=CAP) == 4.0
        assert ledger.daily_average(capability_id="other") == 0.0

    def test_rows_scoped_by_capability(self, tmp_path):
        ledger = SH.ShadowLedger(str(tmp_path / "l.jsonl"))
        ledger.record(self._report(1))
        other = SH.ShadowReport(capability_id="x")
        ledger.record(other)
        assert len(ledger.rows()) == 2 and len(ledger.rows(CAP)) == 1

    def test_missing_file_is_empty(self, tmp_path):
        assert SH.ShadowLedger(str(tmp_path / "none.jsonl")).rows() == []


# ════════════════════════════════════════════════════════════
#  劣化（R4）
# ════════════════════════════════════════════════════════════


class TestDegradation:
    @staticmethod
    def _samples(passed_flags):
        return [SH.ShadowSample(sample_id=f"s{i}", case_id=f"c{i}",
                                passed=flag, negative=not flag)
                for i, flag in enumerate(passed_flags)]

    def test_insufficient_samples_does_not_conclude(self):
        verdict = SH.assess_degradation(self._samples([True] * 5))
        assert verdict["verdict"] == SH.DEGRADE_VERDICT_INSUFFICIENT
        assert verdict["action"] == SH.DEGRADE_ACTION_OBSERVE

    def test_stable_when_pass_rate_high(self):
        verdict = SH.assess_degradation(self._samples([True] * 24 + [False]))
        assert verdict["verdict"] == SH.DEGRADE_VERDICT_STABLE

    def test_consecutive_negatives_trigger_degrade(self):
        flags = [True] * 20 + [False, False, False]
        verdict = SH.assess_degradation(flags and self._samples(flags))
        assert verdict["verdict"] == SH.DEGRADE_VERDICT_DEGRADED
        assert verdict["action"] == SH.DEGRADE_ACTION_ROLLBACK
        assert verdict["consecutive_negatives"] == 3

    def test_low_pass_rate_triggers_degrade(self):
        flags = [True, False] * 12
        verdict = SH.assess_degradation(self._samples(flags))
        assert verdict["verdict"] == SH.DEGRADE_VERDICT_DEGRADED

    def test_empty_samples_is_insufficient(self):
        verdict = SH.assess_degradation([])
        assert verdict["verdict"] == SH.DEGRADE_VERDICT_INSUFFICIENT


# ════════════════════════════════════════════════════════════
#  ShadowRunner（门禁 / 计划 / 执行 / 报告）
# ════════════════════════════════════════════════════════════


class TestRunnerGate:
    def test_blocked_without_passport(self, runner, tmp_path):
        case_set = make_case_set(4)
        report = runner.run(CAP, case_set=case_set, force=True)
        assert report.allowed is False and report.total == 0
        assert any("通行证" in r for r in report.blocked_reasons)
        assert report.passport_reasons

    def test_blocked_by_default_without_force(self, runner, tmp_path):
        case_set = make_case_set(4)
        _result, store = issue_passport(case_set, tmp_path)
        report = runner.run(CAP, case_set=case_set)
        assert report.allowed is False
        assert any(SH.SHADOW_ENABLE_ENV in r for r in report.blocked_reasons)

    def test_blocked_when_case_set_missing(self, tmp_path):
        case_set = make_case_set(4)
        _result, store = issue_passport(case_set, tmp_path)
        runner = SH.ShadowRunner(passport_store=store,
                                 case_store=C.open_case_store(str(tmp_path / "cases")),
                                 ledger=SH.ShadowLedger(str(tmp_path / "l.jsonl")),
                                 review_queue=SH.ManualReviewQueue(str(tmp_path / "r.jsonl")),
                                 env={}, emit_events=False)
        # 存储里没有该能力的判定集（issue_passport 未写 case store）
        report = runner.run(CAP, force=True)
        assert report.allowed is False
        assert any("判定集缺失" in r for r in report.blocked_reasons)

    def test_passport_status_reports_reasons(self, runner, tmp_path):
        status = runner.passport_status(CAP)
        assert status["ok"] is False and status["source"] == "gate.PassportStore"
        assert status["reasons"]

    def test_blocked_run_writes_ledger_and_audit(self, tmp_path):
        ledger = SH.ShadowLedger(str(tmp_path / "l.jsonl"))
        runner = SH.ShadowRunner(
            passport_store=G.PassportStore(str(tmp_path / "cases")),
            case_store=C.open_case_store(str(tmp_path / "cases")), ledger=ledger,
            review_queue=SH.ManualReviewQueue(str(tmp_path / "r.jsonl")), env={},
            emit_events=False)
        runner.run(CAP, case_set=make_case_set(2), force=True)
        assert len(ledger.rows(CAP)) == 1
        actions = [e.action for e in facade_mod.audit.recent(limit=20)]
        assert SH.AUDIT_ACTION_BLOCKED in actions


class TestRunnerRun:
    @pytest.fixture
    def ready(self, tmp_path):
        case_set = make_case_set(24)
        result, store = issue_passport(case_set, tmp_path)
        assert result.passed is True, result.reasons()
        runner = SH.ShadowRunner(
            judge=SH.resolve_judge("local").scorer, judge_kind=SH.JUDGE_KIND_LOCAL,
            passport_store=store,
            case_store=C.open_case_store(str(tmp_path / "cases")),
            ledger=SH.ShadowLedger(str(tmp_path / "shadow" / "l.jsonl")),
            review_queue=SH.ManualReviewQueue(str(tmp_path / "shadow" / "r.jsonl")),
            env={}, emit_events=False)
        return runner, case_set, store

    def test_full_pass_run(self, ready):
        runner, case_set, _store = ready
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=100)
        assert report.allowed is True
        assert report.total == report.plan.budget == 15
        assert report.pass_rate == 1.0 and report.negative == 0
        assert report.judge_kind == SH.JUDGE_KIND_LOCAL
        assert report.layer_failures == {}

    def test_budget_never_exceeded_on_large_universe(self, ready):
        runner, case_set, _store = ready
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=100000)
        assert report.plan.budget == SH.SHADOW_BUDGET_CAP
        assert report.total <= report.plan.budget

    def test_sampling_is_reproducible_across_runs(self, ready):
        runner, case_set, _store = ready
        first = runner.run(CAP, case_set=case_set, force=True, daily_avg=100,
                           write_ledger=False, enqueue_manual=False)
        second = runner.run(CAP, case_set=case_set, force=True, daily_avg=100,
                            write_ledger=False, enqueue_manual=False)
        assert first.plan.sampled == second.plan.sampled
        assert [s.sample_id for s in first.samples] == [s.sample_id for s in second.samples]

    def test_wall_clock_p99_is_measured(self, ready):
        runner, case_set, _store = ready
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=20)
        assert report.p99_wall_candidate_ms() > 0
        assert report.p99_wall_upstream_ms() > 0
        assert report.to_dict()["clock"] == SH.CLOCK_WALL
        assert report.p99_model_candidate_ms() > 0     # 模型时钟量另列，不被冒充

    def test_manual_sample_is_ten_percent_and_enqueued(self, ready):
        runner, case_set, _store = ready
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=100)
        assert report.manual_sample
        assert report.manual_review["sampled"] == len(report.manual_sample)
        assert report.manual_review_closed() is False   # 未裁定 ⇒ 未闭合
        assert runner.review_queue.pending(CAP)

    def test_negative_sample_produces_degradation_input(self, tmp_path):
        case_set = make_case_set(24)
        result, store = issue_passport(case_set, tmp_path)
        assert result.passed
        # 破坏候选：丢掉末步 ⇒ 三层比对必然失败
        broken = [case.upstream[:1] for case in case_set.cases]
        by_id = {case.case_id: steps for case, steps in zip(case_set.cases, broken)}
        runner = SH.ShadowRunner(
            passport_store=store,
            case_store=C.open_case_store(str(tmp_path / "cases")),
            ledger=SH.ShadowLedger(str(tmp_path / "shadow" / "l.jsonl")),
            review_queue=SH.ManualReviewQueue(str(tmp_path / "shadow" / "r.jsonl")),
            env={}, emit_events=False)
        report = runner.run(CAP, case_set=case_set,
                            candidate=lambda case: by_id[case.case_id], force=True,
                            daily_avg=200)
        assert report.negative == report.total
        assert report.passed == 0
        assert report.negative_samples()[0]["failed_layers"]
        assert report.degradation["verdict"] == SH.DEGRADE_VERDICT_DEGRADED
        assert report.degradation["action"] == SH.DEGRADE_ACTION_ROLLBACK

    def test_layer_failures_counted_per_layer(self, tmp_path):
        case_set = make_case_set(24)
        result, store = issue_passport(case_set, tmp_path)
        assert result.passed
        runner = SH.ShadowRunner(
            passport_store=store,
            case_store=C.open_case_store(str(tmp_path / "cases")),
            ledger=SH.ShadowLedger(str(tmp_path / "shadow" / "l.jsonl")),
            review_queue=SH.ManualReviewQueue(str(tmp_path / "shadow" / "r.jsonl")),
            env={}, emit_events=False)
        broken = [list(case.upstream[:1]) for case in case_set.cases]
        mapping = {case.case_id: steps for case, steps in zip(case_set.cases, broken)}
        report = runner.run(CAP, case_set=case_set,
                            candidate=lambda case: mapping[case.case_id],
                            force=True, daily_avg=30)
        assert report.layer_failures.get("structure", 0) >= 1

    def test_gray_selection_recorded_and_deterministic(self, ready):
        runner, case_set, _store = ready
        config = {"enabled": True, "gray_ratio": 0.05}
        first = runner.run(CAP, case_set=case_set, force=True, daily_avg=100,
                           shadow_config=config, write_ledger=False,
                           enqueue_manual=False)
        assert first.plan.gray["enabled"] is True
        assert first.plan.gray["real_takeover"] is False
        assert set(first.plan.gray_routed) <= set(first.plan.sampled)
        assert all(s.gray_routed == (s.sample_id in set(first.plan.gray_routed))
                   for s in first.samples)

    def test_gray_off_by_default_in_plan(self, ready):
        runner, case_set, _store = ready
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=100,
                            write_ledger=False, enqueue_manual=False)
        assert report.plan.gray["enabled"] is False and report.plan.gray_routed == []

    def test_isolation_declared(self, ready):
        runner, case_set, _store = ready
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=10)
        assert report.isolation["container_isolated"] is False
        assert report.isolation["real_takeover"] is False
        assert report.isolation["mode"] == "in_process_deterministic_model"

    def test_quality_patch_from_shadow(self, ready):
        runner, case_set, _store = ready
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=20)
        patch = report.quality_patch
        assert patch["sample_count"] == report.total
        assert patch["success_rate"] == report.pass_rate
        assert "墙钟" in patch["note"]

    def test_quality_write_is_opt_in(self, ready):
        runner, case_set, _store = ready

        class _Registry:
            def __init__(self):
                self.calls = []

            def update_fields(self, capability_id, patch, **kwargs):
                self.calls.append((capability_id, patch))

        reg = _Registry()
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=20)
        runner.apply_quality(report, registry=reg)
        assert reg.calls and reg.calls[0][1]["quality"]["sample_count"] == report.total

    def test_ledger_and_audit_written_on_success(self, ready):
        runner, case_set, _store = ready
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=20)
        assert len(runner.ledger.rows(CAP)) == 1
        actions = [e.action for e in facade_mod.audit.recent(limit=20)]
        assert SH.AUDIT_ACTION_OBSERVED in actions
        assert report.to_dict(include_samples=False)["total"] == report.total

    def test_degraded_run_writes_degraded_audit(self, tmp_path):
        case_set = make_case_set(24)
        result, store = issue_passport(case_set, tmp_path)
        assert result.passed
        mapping = {case.case_id: list(case.upstream[:1]) for case in case_set.cases}
        runner = SH.ShadowRunner(
            passport_store=store,
            case_store=C.open_case_store(str(tmp_path / "cases")),
            ledger=SH.ShadowLedger(str(tmp_path / "shadow" / "l.jsonl")),
            review_queue=SH.ManualReviewQueue(str(tmp_path / "shadow" / "r.jsonl")),
            env={}, emit_events=False)
        runner.run(CAP, case_set=case_set,
                   candidate=lambda case: mapping[case.case_id], force=True,
                   daily_avg=200)
        actions = [e.action for e in facade_mod.audit.recent(limit=20)]
        assert SH.AUDIT_ACTION_DEGRADED in actions

    def test_report_markdown_contains_clock_and_isolation(self, ready):
        runner, case_set, _store = ready
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=20)
        text = report.markdown()
        assert SH.CLOCK_WALL in text and "隔离" in text and "judge" in text

    def test_trace_ids_used_as_sampling_universe(self, ready):
        runner, case_set, _store = ready
        trace_ids = [f"tr-{i:03d}" for i in range(30)]
        report = runner.run(CAP, case_set=case_set, trace_ids=trace_ids, force=True,
                            daily_avg=100, write_ledger=False, enqueue_manual=False)
        assert len(report.plan.universe) == min(len(case_set.active_cases()),
                                                len(trace_ids))
        assert all(s.sample_id.startswith("tr-") for s in report.samples)


# ════════════════════════════════════════════════════════════
#  适用性过滤（M4）与 shadow 的联动
# ════════════════════════════════════════════════════════════


class TestApplicabilityIntegration:
    def test_excluded_cases_not_executed(self, tmp_path):
        cases = [make_case(i) for i in range(24)]
        for case in cases[:4]:
            case.applicability = C.CaseApplicability(
                exclude_kinds=[C.CANDIDATE_KIND_PATTERN],
                reason="形状不匹配（单次读取 vs 三段任务链）",
                declared_by="test")
        case_set = C.build_case_set(CAP, cases)
        result, store = issue_passport(case_set, tmp_path)
        assert result.passed
        runner = SH.ShadowRunner(
            passport_store=store,
            case_store=C.open_case_store(str(tmp_path / "cases")),
            ledger=SH.ShadowLedger(str(tmp_path / "shadow" / "l.jsonl")),
            review_queue=SH.ManualReviewQueue(str(tmp_path / "shadow" / "r.jsonl")),
            env={}, emit_events=False)
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=100,
                            candidate_kind=C.CANDIDATE_KIND_PATTERN,
                            write_ledger=False, enqueue_manual=False)
        assert report.applicability["excluded"] == 4
        assert report.applicability["applicable"] == 20
        executed = {s.case_id for s in report.samples}
        assert not (executed & {case.case_id for case in cases[:4]})
        assert report.applicability["excluded_cases"][0]["reason"]

    def test_unrestricted_cases_all_included(self, tmp_path):
        case_set = make_case_set(24)
        result, store = issue_passport(case_set, tmp_path)
        assert result.passed
        runner = SH.ShadowRunner(
            passport_store=store,
            case_store=C.open_case_store(str(tmp_path / "cases")),
            ledger=SH.ShadowLedger(str(tmp_path / "shadow" / "l.jsonl")),
            review_queue=SH.ManualReviewQueue(str(tmp_path / "shadow" / "r.jsonl")),
            env={}, emit_events=False)
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=100,
                            candidate_kind=C.CANDIDATE_KIND_PATTERN,
                            write_ledger=False, enqueue_manual=False)
        assert report.applicability["excluded"] == 0
        assert report.applicability["applicable"] == len(case_set.active_cases())

    def test_candidate_kind_affects_filtering(self, tmp_path):
        cases = [make_case(i) for i in range(24)]
        for case in cases[:4]:
            case.applicability = C.CaseApplicability(
                exclude_kinds=[C.CANDIDATE_KIND_PATTERN], reason="仅对模式候选不适用")
        case_set = C.build_case_set(CAP, cases)
        result, store = issue_passport(case_set, tmp_path)
        assert result.passed
        runner = SH.ShadowRunner(
            passport_store=store,
            case_store=C.open_case_store(str(tmp_path / "cases")),
            ledger=SH.ShadowLedger(str(tmp_path / "shadow" / "l.jsonl")),
            review_queue=SH.ManualReviewQueue(str(tmp_path / "shadow" / "r.jsonl")),
            env={}, emit_events=False)
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=100,
                            candidate_kind="seed_pack_native", write_ledger=False,
                            enqueue_manual=False)
        assert report.applicability["excluded"] == 0


# ════════════════════════════════════════════════════════════
#  gate 的适性过滤是 opt-in（既有行为不变）
# ════════════════════════════════════════════════════════════


class TestGateApplicabilityOptIn:
    def test_gate_without_candidate_kind_keeps_all_cases(self, tmp_path):
        case_set = make_case_set(24)
        for case in case_set.cases[:4]:
            case.applicability = C.CaseApplicability(
                exclude_kinds=[C.CANDIDATE_KIND_PATTERN], reason="不适用")
        result = G.acceptance_gate(
            CAP, case_set=case_set, emit_events=False,
            candidate=lambda case: case.upstream,
            passport_store=G.PassportStore(str(tmp_path / "cases")))
        assert result.executed == len(case_set.active_cases())
        assert result.applicability == {}

    def test_gate_with_candidate_kind_filters(self, tmp_path):
        case_set = make_case_set(24)
        for case in case_set.cases[:4]:
            case.applicability = C.CaseApplicability(
                exclude_kinds=[C.CANDIDATE_KIND_PATTERN], reason="不适用")
        result = G.acceptance_gate(
            CAP, case_set=case_set, emit_events=False,
            candidate=lambda case: case.upstream,
            candidate_kind=C.CANDIDATE_KIND_PATTERN,
            passport_store=G.PassportStore(str(tmp_path / "cases")))
        assert result.executed == len(case_set.active_cases()) - 4
        assert result.applicability["excluded"] == 4
        assert result.passed is True


# ════════════════════════════════════════════════════════════
#  沙箱墙钟扩展（M2）不改变既有行为
# ════════════════════════════════════════════════════════════


class TestSandboxWallClock:
    def test_wall_clock_off_by_default(self):
        case = make_case()
        box = ReplaySandbox()
        replay = box.replay_case(case, case.upstream)
        assert replay.candidate.wall_ms == 0.0
        assert replay.upstream.wall_ms == 0.0
        assert box.measure_wall is False

    def test_wall_clock_opt_in_sets_field_only(self):
        case = make_case()
        box = ReplaySandbox(measure_wall=True)
        replay = box.replay_case(case, case.upstream)
        assert replay.candidate.wall_ms >= 0.0
        assert replay.upstream.wall_ms >= 0.0
        # 模型时钟量不受影响（既有语义不变）
        assert replay.candidate.duration_ms > 0
        assert replay.passed

    def test_wall_clock_does_not_change_diff(self):
        case = make_case()
        plain = ReplaySandbox().replay_case(case, case.upstream)
        wall = ReplaySandbox(measure_wall=True).replay_case(case, case.upstream)
        assert plain.diff.to_dict()["passed"] == wall.diff.to_dict()["passed"]
        assert plain.candidate.fingerprint() == wall.candidate.fingerprint()

    def test_report_wall_p99_methods(self):
        case = make_case()
        box = ReplaySandbox(measure_wall=True)
        report = box.replay_many([case, make_case(1)], case.upstream)
        assert report.wall_measured is True
        assert report.p99_wall_candidate_ms() >= 0
        payload = report.to_dict()
        assert payload["wall_measured"] is True and "clock" in payload
