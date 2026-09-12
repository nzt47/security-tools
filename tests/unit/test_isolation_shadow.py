"""灰度链路接线：隔离等级 + 真实接管 + **未双写** + 失败回落（TASK-S8-03 步骤 4/6）

本文件是"解锁真实接管"这条主张的**端到端断言集**，逐条对应任务书 §四：

- `ReplaySandbox.isolation_level` 接入，且**回放语义逐字不变**；
- `real_takeover` **默认关闭**（既有行为零变化）；
- 开启后在隔离边界内执行，**副作用只记录不双写**（用例断言真实环境无副作用）；
- 连续失败 ⇒ 自动回落 `sandbox_replay_only` + 事故卡；
- 隔离声明**如实分层**（实际执行模型 / 本次是否真接管 / 环境最高等级）。
"""

from __future__ import annotations

import json
import os

import pytest

from agent.audit import facade as facade_mod
from agent.audit.chain import AuditChain
from agent.digestion import cases as C
from agent.digestion import gate as G
from agent.digestion import isolation as ISO
from agent.digestion import shadow as SH
from agent.digestion import takeover as TK
from agent.digestion.sandbox import ReplaySandbox

from isolation_util import CAP, FakeExecutor, make_case

ENABLE = {TK.ENV_REAL_TAKEOVER: "true", TK.ENV_TAKEOVER_RATIO: "0.05"}


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
    import agent.observability.events as events_mod
    events_mod.reset_event_stores()
    yield tmp_path
    events_mod.reset_event_stores()


def _runner(tmp_path, *, env=None, executor=None, isolation_plan=None,
            ledger=None, incident_dir="", isolation_level="", emit=False):
    """灰度器（台账/抽检/接管台账/事故卡/Trace 库全部落 tmp；不写运行时区）"""
    return SH.ShadowRunner(
        judge=SH.resolve_judge("local").scorer, judge_kind=SH.JUDGE_KIND_LOCAL,
        passport_store=G.PassportStore(str(tmp_path / "cases")),
        case_store=C.open_case_store(str(tmp_path / "cases")),
        ledger=SH.ShadowLedger(str(tmp_path / "shadow" / "ledger.jsonl")),
        review_queue=SH.ManualReviewQueue(str(tmp_path / "shadow" / "reviews.jsonl")),
        takeover_ledger=ledger or TK.TakeoverLedger(str(tmp_path / "takeover.jsonl")),
        incident_dir=incident_dir or str(tmp_path / "incidents"),
        trace_db=str(tmp_path / "tool_trace.db"),
        isolation_executor=executor, isolation_plan=isolation_plan,
        isolation_level=isolation_level,
        env=dict(env or {}), emit_events=bool(emit))


def make_case_set(size: int = 8) -> C.CaseSet:
    return C.build_case_set(CAP, [make_case(i) for i in range(size)])


@pytest.fixture
def ready(tmp_path):
    """已发证的判定集 + 灰度器（接管用**假执行器**，故毫秒级）"""
    from tests.unit.test_digestion_shadow import issue_passport
    case_set = make_case_set(8)
    result, store = issue_passport(case_set, tmp_path)
    assert result.passed, result.reasons
    runner = _runner(tmp_path)
    return runner, case_set, store


# ════════════════════════════════════════════════════════════
#  ReplaySandbox.isolation_level（回放语义不变）
# ════════════════════════════════════════════════════════════


class TestReplaySandboxIsolationLevel:
    def test_default_is_in_process(self):
        assert ReplaySandbox().isolation_level == ISO.ISOLATION_IN_PROCESS

    @pytest.mark.parametrize("value,expected", [
        ("container", "container"),
        ("subprocess_hardened", "subprocess_hardened"),
        ("docker", "container"),
        ("off", "in_process"),
    ])
    def test_explicit_level_is_normalized(self, value, expected):
        assert ReplaySandbox(isolation_level=value).isolation_level == expected

    @pytest.mark.parametrize("bad", ["auto", "k8s", "containerx", 7])
    def test_unknown_level_falls_back_to_in_process(self, bad):
        """**保守**：宁可不隔离，也不冒称隔离"""
        assert ReplaySandbox(isolation_level=bad).isolation_level == \
            ISO.ISOLATION_IN_PROCESS

    def test_replay_semantics_unchanged_by_level(self):
        """回放通道**逐字段不变**：换等级不得改变回放结论（含副作用集合）"""
        case = make_case(0)
        plain = ReplaySandbox()
        leveled = ReplaySandbox(isolation_level="container")
        first = plain.replay_case(case, case.native)
        second = leveled.replay_case(case, case.native)
        assert first.candidate.status == second.candidate.status
        assert first.candidate.steps == second.candidate.steps
        assert first.candidate.side_effect_set() == second.candidate.side_effect_set()
        assert first.candidate.canonical_text() == second.candidate.canonical_text()
        assert first.diff.passed == second.diff.passed

    def test_commit_still_raises_so_nothing_is_written_twice(self):
        """`ReplayEnv.commit()` 仍然**恒抛** —— 新增等级不得松动这条契约"""
        from agent.digestion.sandbox import ReplayEnv, SandboxError
        with pytest.raises(SandboxError):
            ReplayEnv().commit()

    def test_explicit_in_process_plan_needs_no_probe(self):
        box = ReplaySandbox(isolation_level="in_process")
        plan = box.isolation_plan(env={})
        assert plan.level == "in_process"
        assert plan.docker.get("probed") is False

    def test_executor_matches_effective_level(self):
        box = ReplaySandbox(isolation_level="subprocess_hardened")
        executor = box.isolation_executor(env={})
        assert executor.level == "subprocess_hardened"

    def test_execute_isolated_reports_evidence_when_refused(self):
        box = ReplaySandbox(isolation_level="in_process")
        result = box.execute_isolated({"job_id": "x", "steps": []}, env={})
        assert result.ran is False and result.status == ISO.STATUS_REFUSED


# ════════════════════════════════════════════════════════════
#  默认关闭（既有行为零变化）
# ════════════════════════════════════════════════════════════


class TestDefaultOff:
    def test_takeover_is_off_by_default(self, ready):
        runner, case_set, _store = ready
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=100,
                            write_ledger=False, enqueue_manual=False)
        assert report.takeover["enabled"] is False
        assert report.takeover["executed"] == 0
        assert report.takeover["adopted"] is False
        assert report.takeover["transport"] == TK.TRANSPORT_SANDBOX_ONLY

    def test_isolation_declaration_unlocks_nothing_by_default(self, ready):
        runner, case_set, _store = ready
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=10,
                            write_ledger=False, enqueue_manual=False)
        assert report.isolation["mode"] == SH.MODE_IN_PROCESS
        assert report.isolation["container_isolated"] is False
        assert report.isolation["real_takeover"] is False
        assert report.isolation["candidate_execution"] == SH.TRANSPORT_SANDBOX_ONLY
        assert report.isolation["real_takeover_enabled"] is False

    def test_samples_are_marked_not_requested(self, ready):
        runner, case_set, _store = ready
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=20,
                            write_ledger=False, enqueue_manual=False)
        assert report.samples
        for sample in report.samples:
            assert sample.takeover is False
            assert sample.takeover_status == TK.TAKEOVER_STATUS_NOT_REQUESTED
        assert report.to_dict()["samples"][0]["takeover"] is False

    def test_no_isolation_means_refusal_even_when_enabled(self, tmp_path):
        """**无隔离 ⇒ 拒绝接管**：开关打开也不行（安全底线，不可配置绕过）"""
        from tests.unit.test_digestion_shadow import issue_passport
        case_set = make_case_set(8)
        result, store = issue_passport(case_set, tmp_path)
        assert result.passed
        runner = _runner(tmp_path, env=ENABLE,
                         isolation_plan=ISO.IsolationPlan(
                             level=ISO.ISOLATION_IN_PROCESS),
                         executor=FakeExecutor())
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=100,
                            write_ledger=False, enqueue_manual=False)
        assert report.takeover["enabled"] is False
        assert report.takeover["policy"]["source"] == TK.TAKEOVER_SOURCE_REFUSED
        assert report.isolation["real_takeover"] is False


# ════════════════════════════════════════════════════════════
#  开启后：隔离执行 + 预算 + 未双写
# ════════════════════════════════════════════════════════════


class TestEnabledTakeover:
    def _enabled_runner(self, tmp_path, executor, *, env=None, plan=None):
        from tests.unit.test_digestion_shadow import issue_passport
        case_set = make_case_set(8)
        result, store = issue_passport(case_set, tmp_path)
        assert result.passed
        runner = _runner(tmp_path, env=dict(ENABLE, **(env or {})),
                         executor=executor,
                         isolation_plan=plan or ISO.IsolationPlan(level="container"))
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=100,
                            shadow_config={"enabled": True, "gray_ratio": 0.99},
                            write_ledger=False, enqueue_manual=False)
        return report, executor

    def test_takeover_executes_inside_the_boundary(self, tmp_path):
        report, executor = self._enabled_runner(tmp_path, FakeExecutor())
        assert report.takeover["enabled"] is True
        assert report.takeover["executed"] >= 1
        assert executor.jobs, "接管必须真的经隔离执行器提交作业"
        submitted = executor.jobs[0]
        assert "steps" in submitted
        assert submitted.get("probe_mode") is not True

    def test_declaration_reports_isolated_execution(self, tmp_path):
        report, _ = self._enabled_runner(tmp_path, FakeExecutor())
        assert report.isolation["mode"] == SH.MODE_ISOLATED
        assert report.isolation["real_takeover"] is True
        assert report.isolation["container_isolated"] is True
        assert report.isolation["level"] == "container"
        assert report.isolation["candidate_execution"] == \
            TK.TRANSPORT_ISOLATED_TAKEOVER

    def test_budget_caps_the_number_of_attempts(self, tmp_path):
        report, executor = self._enabled_runner(
            tmp_path, FakeExecutor(), env={TK.ENV_TAKEOVER_BUDGET_CAP: "1"})
        assert report.takeover["budget"] == 1
        assert len(executor.jobs) <= 1
        assert report.takeover["executed"] <= 1

    def test_subsampled_takeover_is_marked_not_sampled(self, tmp_path):
        """开启了但没抽到 ⇒ 状态是"未抽样"，**不是**"成功" """
        report, _ = self._enabled_runner(tmp_path, FakeExecutor(), env={
            TK.ENV_TAKEOVER_BUDGET_CAP: "1"})
        statuses = {s.takeover_status for s in report.samples}
        assert statuses <= {TK.TAKEOVER_STATUS_RAN, TK.TAKEOVER_STATUS_FAILED,
                            TK.TAKEOVER_STATUS_NOT_SAMPLED}
        if report.total > 1:
            assert TK.TAKEOVER_STATUS_NOT_SAMPLED in statuses

    def test_real_environment_is_not_written_twice(self, tmp_path):
        """**核心验收**：接管跑完后，真实目录指纹**逐字未变**

        见证目录模拟"真实环境"（宿主工作区 + 一个哨兵文件）。接管执行器的候选
        步骤会尝试写文件，但唯一可写根是它自己的一次性临时目录 ⇒ 见证目录必须
        原封不动。这条断言就是"副作用只记录不双写"的机器可读证据。
        """
        witness = tmp_path / "witness"
        witness.mkdir()
        sentinel = witness / "sentinel.txt"
        sentinel.write_text("REAL-ENV-PRISTINE", encoding="utf-8")
        nested = witness / "nested"
        nested.mkdir()
        (nested / "keep.txt").write_text("keep", encoding="utf-8")
        before = ISO.snapshot_paths([str(witness)])

        report, executor = self._enabled_runner(tmp_path, FakeExecutor(
            side_effects={"files_written": ["/work/candidate/out/a0.txt"]}))
        assert report.takeover["executed"] >= 1
        assert executor.jobs

        after = ISO.snapshot_paths([str(witness)])
        diff = ISO.diff_snapshot(before, after)
        assert diff == {"unchanged": True, "changed": [], "created": [],
                        "removed": []}, f"真实环境被改动了：{diff}"
        assert sentinel.read_text(encoding="utf-8") == "REAL-ENV-PRISTINE"

    def test_adopted_is_always_false(self, tmp_path):
        report, _ = self._enabled_runner(tmp_path, FakeExecutor())
        assert report.takeover["adopted"] is False
        assert all(a["adopted"] is False for a in report.takeover["attempts"])
        assert "不自动合入" in report.takeover["note"]

    def test_takeover_audit_and_event_carry_isolation_facts(self, tmp_path,
                                                            monkeypatch):
        events: list = []

        def _fake_emit(event, body, **kwargs):
            events.append(dict(body))
            return type("V", (), {"event_id": "ev"})()
        monkeypatch.setattr("agent.observability.events.emit", _fake_emit)
        monkeypatch.setattr("agent.observability.events.trace_fields", lambda: {})

        runner = _runner(tmp_path, env=dict(ENABLE, **{"CP_X": "1"}),
                         executor=FakeExecutor(),
                         isolation_plan=ISO.IsolationPlan(level="subprocess_hardened"),
                         emit=True)
        from tests.unit.test_digestion_shadow import issue_passport
        case_set = make_case_set(8)
        result, _store = issue_passport(case_set, tmp_path)
        assert result.passed
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=100,
                            shadow_config={"enabled": True, "gray_ratio": 0.99},
                            write_ledger=False, enqueue_manual=False)
        takeover_events = [e for e in events
                           if e.get("scope") == TK.EVENT_SCOPE_TAKEOVER]
        assert takeover_events, f"接管运行必须发事件；实际事件 scope={[e.get('scope') for e in events]}"
        body = takeover_events[-1]
        assert any("isolation_level" in r for r in body["reasons"])
        assert any("adopted=False" in r for r in body["reasons"])
        assert body["note"].startswith("接管在隔离边界内执行")
        assert report.isolation["level"] == "subprocess_hardened"
        assert report.isolation["container_isolated"] is False


# ════════════════════════════════════════════════════════════
#  连续失败 ⇒ 回落 + 事故卡
# ════════════════════════════════════════════════════════════


class TestFallbackThroughShadow:
    def _run_with(self, tmp_path, executor, *, runs=3, threshold=3, avg=100.0):
        from tests.unit.test_digestion_shadow import issue_passport
        case_set = make_case_set(8)
        result, _store = issue_passport(case_set, tmp_path)
        assert result.passed
        runner = _runner(
            tmp_path,
            env=dict(ENABLE, **{TK.ENV_FAIL_THRESHOLD: str(threshold)}),
            executor=executor,
            isolation_plan=ISO.IsolationPlan(level="subprocess_hardened"),
            incident_dir=str(tmp_path / "incidents"))
        reports = []
        for _ in range(runs):
            reports.append(runner.run(
                CAP, case_set=case_set, force=True, daily_avg=avg,
                shadow_config={"enabled": True, "gray_ratio": 0.99},
                write_ledger=False, enqueue_manual=False))
        return reports, runner, executor, case_set

    def test_consecutive_failures_fall_back_and_raise_incident(self, tmp_path):
        executor = FakeExecutor(status=ISO.STATUS_ERROR, error="候选崩了")
        reports, _runner_obj, _ex, _cs = self._run_with(tmp_path, executor, runs=3)
        assert [r.takeover["fallback"] for r in reports] == [False, False, True]
        last = reports[-1]
        assert last.takeover["fallback_transport"] == TK.FALLBACK_TRANSPORT
        incident_id = last.takeover["incident_id"]
        assert incident_id
        assert (tmp_path / "incidents" / f"{incident_id}.json").exists()
        # 事故卡必须带上"回落到哪一档"这条关键事实
        card = json.loads((tmp_path / "incidents" / f"{incident_id}.json")
                          .read_text(encoding="utf-8"))
        assert card["detail"]["fallback_transport"] == TK.FALLBACK_TRANSPORT
        assert card["detail"]["capability_id"] == CAP

    def test_fallback_stops_further_takeover(self, tmp_path):
        """回落之后：审计/事件照写，但**不再向隔离执行器提交任何作业**"""
        executor = FakeExecutor(status=ISO.STATUS_ERROR)
        reports, runner, executor, case_set = self._run_with(tmp_path, executor,
                                                             runs=3)
        assert reports[-1].takeover["fallback"] is True
        jobs_after_fallback = len(executor.jobs)
        fourth = runner.run(CAP, case_set=case_set, force=True, daily_avg=100,
                            shadow_config={"enabled": True, "gray_ratio": 0.99},
                            write_ledger=False, enqueue_manual=False)
        assert fourth.takeover["fallback"] is True
        assert fourth.takeover["executed"] == 0
        assert len(executor.jobs) == jobs_after_fallback, \
            "已回落 ⇒ 不得再执行接管"
        assert any("已处于回落态" in r for r in fourth.takeover["reasons"])

    def test_healthy_takeover_does_not_fall_back(self, tmp_path):
        """一致（matched）的接管**不**触发回落 —— 回落只由"失败"驱动

        FakeExecutor 必须造出与回放观测**同形**的副作用（`normalize_param_value`
        会把任何绝对路径归一到 `${path}`），否则算出的"失败"是夹具的锅，
        不是被测算法的结论。
        """
        executor = FakeExecutor(side_effects={
            "files_written": ["/work/candidate/out/a0.txt"]})
        reports, _runner_obj, _ex, _cs = self._run_with(tmp_path, executor, runs=3)
        assert all(r.takeover["matched"] >= 1 for r in reports)
        assert all(r.takeover["failed"] == 0 for r in reports)
        assert all(r.takeover["fallback"] is False for r in reports)

    def test_blocked_run_reports_takeover_reasons(self, tmp_path):
        """被阻断的灰度不做接管，但要**说清为什么**（不静默）"""
        runner = _runner(tmp_path, env=ENABLE, executor=FakeExecutor(),
                         isolation_plan=ISO.IsolationPlan(level="container"))
        case_set = make_case_set(8)   # 未发证 ⇒ 灰度被门挡住
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=100,
                            write_ledger=False, enqueue_manual=False)
        assert report.allowed is False
        assert report.takeover["executed"] == 0
        assert any("不执行接管" in r for r in report.takeover["reasons"])


# ════════════════════════════════════════════════════════════
#  隔离声明（三层事实分开）
# ════════════════════════════════════════════════════════════


class TestIsolationDeclaration:
    def test_backward_compatible_default_shape(self):
        payload = SH.isolation_declaration()
        assert payload["mode"] == SH.MODE_IN_PROCESS
        assert payload["container_isolated"] is False
        assert payload["real_takeover"] is False
        assert payload["candidate_execution"] == SH.TRANSPORT_SANDBOX_ONLY

    def test_enabled_but_not_executed_is_distinguishable(self):
        """开关开着但一次没跑 ⇒ `real_takeover=False` 且 `real_takeover_enabled=True`"""
        payload = SH.isolation_declaration(
            plan=ISO.IsolationPlan(level="container"), real_takeover=True,
            real_takeover_enabled=True, executed=0)
        assert payload["real_takeover"] is False
        assert payload["real_takeover_enabled"] is True
        assert payload["real_takeover_executed"] == 0
        assert payload["available_level"] == "container"
        assert "预算/抽样/回落" in payload["note"]

    def test_executed_container_is_declared_as_container_isolated(self):
        payload = SH.isolation_declaration(
            plan=ISO.IsolationPlan(level="container"), real_takeover=True,
            real_takeover_enabled=True, executed=3)
        assert payload["mode"] == SH.MODE_ISOLATED
        assert payload["real_takeover"] is True
        assert payload["container_isolated"] is True

    def test_subprocess_takeover_never_claims_container(self):
        payload = SH.isolation_declaration(
            plan=ISO.IsolationPlan(level="subprocess_hardened"),
            real_takeover=True, real_takeover_enabled=True, executed=2)
        assert payload["mode"] == SH.MODE_ISOLATED
        assert payload["container_isolated"] is False
        assert payload["kernel_isolation"] is False
        assert payload["not_guaranteed"], "非内核级等级必须附不保证清单"

    def test_declaration_always_carries_not_guaranteed_list(self):
        for level in ISO.ISOLATION_LEVELS:
            payload = SH.isolation_declaration(plan=ISO.IsolationPlan(level=level))
            assert payload["not_guaranteed"], level
            assert payload["guarantees"], level


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([os.path.abspath(__file__), "-q"]))
