"""真实接管：策略 / 预算 / 台账 / 比对 / 失败回落（TASK-S8-03 步骤 4）

覆盖四条不肯让的契约：

1. **默认关闭**（开关 + 显式比例缺一不可，非法值回退关闭）；
2. **无隔离即拒绝**（`in_process` ⇒ 不开，理由写明）；
3. **有每日预算上限**（预算 0 ⇒ 一次都不跑）；
4. **连续失败自动回落** `sandbox_replay_only` + 事故卡 + 审计。
"""

from __future__ import annotations

import json
import os
import sys

import pytest

from agent.digestion import isolation as ISO
from agent.digestion import takeover as TK

from isolation_util import CAP, FakeExecutor, make_case

ENABLE = {TK.ENV_REAL_TAKEOVER: "true", TK.ENV_TAKEOVER_RATIO: "0.05"}


def _container_plan() -> ISO.IsolationPlan:
    return ISO.IsolationPlan(level=ISO.ISOLATION_CONTAINER)


def _subprocess_plan() -> ISO.IsolationPlan:
    return ISO.IsolationPlan(level=ISO.ISOLATION_SUBPROCESS_HARDENED)


def _no_isolation_plan() -> ISO.IsolationPlan:
    return ISO.IsolationPlan(level=ISO.ISOLATION_IN_PROCESS)


# ════════════════════════════════════════════════════════════
#  策略（默认关闭 / 显式比例 / 无隔离即拒绝）
# ════════════════════════════════════════════════════════════


class TestTakeoverPolicy:
    def test_default_off(self):
        policy = TK.resolve_takeover_policy(env={}, isolation=_container_plan())
        assert policy.enabled is False
        assert policy.source == TK.TAKEOVER_SOURCE_DEFAULT
        assert policy.transport == TK.TRANSPORT_SANDBOX_ONLY
        assert any(TK.ENV_REAL_TAKEOVER in r for r in policy.reasons)

    def test_flag_without_ratio_stays_off(self):
        policy = TK.resolve_takeover_policy(
            env={TK.ENV_REAL_TAKEOVER: "true"}, isolation=_container_plan())
        assert policy.enabled is False
        assert any("非法或缺失" in r for r in policy.reasons)

    def test_ratio_without_flag_stays_off(self):
        policy = TK.resolve_takeover_policy(
            env={TK.ENV_TAKEOVER_RATIO: "0.05"}, isolation=_container_plan())
        assert policy.enabled is False
        assert any("未开启" in r for r in policy.reasons)

    @pytest.mark.parametrize("bad", ["abc", "0", "-1", "1.5", ""])
    def test_invalid_ratio_falls_back_to_off(self, bad):
        policy = TK.resolve_takeover_policy(
            env={TK.ENV_REAL_TAKEOVER: "true", TK.ENV_TAKEOVER_RATIO: bad},
            isolation=_container_plan())
        assert policy.enabled is False
        assert policy.ratio == 0.0

    @pytest.mark.parametrize("raw,expected", [("1", True), ("true", True),
                                              ("yes", True), ("on", True),
                                              ("0", False), ("false", False),
                                              ("maybe", False)])
    def test_flag_parsing(self, raw, expected):
        policy = TK.resolve_takeover_policy(
            env={TK.ENV_REAL_TAKEOVER: raw, TK.ENV_TAKEOVER_RATIO: "0.1"},
            isolation=_container_plan())
        assert policy.enabled is expected

    def test_enabled_requires_both_and_marks_transport(self):
        policy = TK.resolve_takeover_policy(env=ENABLE, isolation=_container_plan())
        assert policy.enabled is True and policy.ratio == 0.05
        assert policy.transport == TK.TRANSPORT_ISOLATED_TAKEOVER
        assert policy.source == TK.TAKEOVER_SOURCE_ENV
        assert policy.to_dict()["transport"] == TK.TRANSPORT_ISOLATED_TAKEOVER

    def test_refused_when_no_isolation(self):
        """**核心安全底线**：无隔离环境 ⇒ 拒绝接管，绝不"看起来能接管" """
        policy = TK.resolve_takeover_policy(env=ENABLE,
                                            isolation=_no_isolation_plan())
        assert policy.enabled is False
        assert policy.source == TK.TAKEOVER_SOURCE_REFUSED
        assert any("拒绝" in r and "in_process" in r for r in policy.reasons)

    def test_refusal_beats_descriptor_configuration(self):
        policy = TK.resolve_takeover_policy(
            shadow_config={"real_takeover": True, "real_takeover_ratio": 0.5},
            env={}, isolation=_no_isolation_plan())
        assert policy.enabled is False
        assert policy.source == TK.TAKEOVER_SOURCE_REFUSED

    def test_descriptor_configuration_wins_over_env(self):
        policy = TK.resolve_takeover_policy(
            shadow_config={"real_takeover": True, "real_takeover_ratio": 0.2},
            env=ENABLE, isolation=_container_plan())
        assert policy.enabled is True and policy.ratio == 0.2
        assert policy.source == TK.TAKEOVER_SOURCE_DESCRIPTOR

    def test_descriptor_enabled_without_ratio_stays_off(self):
        policy = TK.resolve_takeover_policy(
            shadow_config={"real_takeover": True}, env={},
            isolation=_container_plan())
        assert policy.enabled is False
        assert any("real_takeover_ratio" in r for r in policy.reasons)

    def test_descriptor_disabled_is_respected(self):
        policy = TK.resolve_takeover_policy(
            shadow_config={"real_takeover": False, "real_takeover_ratio": 0.9},
            env=ENABLE, isolation=_container_plan())
        assert policy.enabled is False

    def test_policy_resolves_isolation_itself_when_not_given(self, monkeypatch):
        """未传隔离决议 ⇒ **自己去探**（而不是假定 in_process 把接管静默关掉）"""
        calls: list = []
        monkeypatch.setattr(TK, "resolve_isolation_level",
                            lambda **kw: (calls.append(kw),
                                          _subprocess_plan())[1])
        policy = TK.resolve_takeover_policy(env=ENABLE)
        assert calls and policy.enabled is True
        assert policy.isolation_level == ISO.ISOLATION_SUBPROCESS_HARDENED

    def test_fail_threshold_env(self):
        policy = TK.resolve_takeover_policy(
            env=dict(ENABLE, **{TK.ENV_FAIL_THRESHOLD: "5"}),
            isolation=_container_plan())
        assert policy.fail_threshold == 5
        bad = TK.resolve_takeover_policy(
            env=dict(ENABLE, **{TK.ENV_FAIL_THRESHOLD: "not-a-number"}),
            isolation=_container_plan())
        assert bad.fail_threshold == TK.DEFAULT_FAIL_THRESHOLD


# ════════════════════════════════════════════════════════════
#  预算（沿用 S3-03 口径）
# ════════════════════════════════════════════════════════════


class TestTakeoverBudget:
    def test_delegates_to_s3_03_formula(self):
        from agent.digestion.shadow import daily_budget
        for avg in (0.0, 1.0, 7.0, 100.0, 1000.0):
            assert TK.takeover_budget(avg, ratio=0.15) == daily_budget(
                avg, ratio=0.15, cap=TK.DEFAULT_TAKEOVER_BUDGET_CAP,
                min_budget=TK.DEFAULT_TAKEOVER_MIN_BUDGET)

    def test_cap_is_respected(self):
        assert TK.takeover_budget(10000, ratio=0.5, cap=20) == 20

    def test_zero_avg_means_zero_budget(self):
        assert TK.takeover_budget(0.0, ratio=0.5) == 0


# ════════════════════════════════════════════════════════════
#  台账（"连续失败"的唯一事实来源）
# ════════════════════════════════════════════════════════════


class TestTakeoverLedger:
    def _entry(self, ledger, *, attempted, failed, fallback=False, incident="",
               capability=CAP):
        ledger.record(TK.TakeoverLedgerEntry(
            capability_id=capability, at=1.0, attempted=attempted,
            succeeded=attempted - failed, failed=failed,
            fallback=fallback, incident_id=incident))

    def test_consecutive_failures_counts_only_trailing_failed_runs(self, tmp_path):
        ledger = TK.TakeoverLedger(str(tmp_path / "t.jsonl"))
        self._entry(ledger, attempted=3, failed=0)
        self._entry(ledger, attempted=3, failed=1)
        self._entry(ledger, attempted=3, failed=2)
        assert ledger.consecutive_failures(CAP) == 2
        self._entry(ledger, attempted=3, failed=0)
        assert ledger.consecutive_failures(CAP) == 0

    def test_not_running_is_not_a_failure(self, tmp_path):
        """预算为 0 / 未抽样 ⇒ attempted=0 ⇒ **不算失败**（否则"没预算"会被读成"一直失败"）"""
        ledger = TK.TakeoverLedger(str(tmp_path / "t.jsonl"))
        self._entry(ledger, attempted=2, failed=2)
        self._entry(ledger, attempted=0, failed=0)
        assert ledger.consecutive_failures(CAP) == 1

    def test_engaged_fallback_survives_restart_and_clears_on_success(self, tmp_path):
        path = str(tmp_path / "t.jsonl")
        ledger = TK.TakeoverLedger(path)
        self._entry(ledger, attempted=3, failed=3, fallback=True, incident="INC-1")
        # 新进程（新实例读同一文件）⇒ 回落态依然成立（不靠内存计数）
        reopened = TK.TakeoverLedger(path)
        engaged = reopened.engaged_fallback(CAP)
        assert engaged is not None and engaged.incident_id == "INC-1"
        self._entry(reopened, attempted=1, failed=0)
        assert reopened.engaged_fallback(CAP) is None

    def test_entries_are_namespaced_by_capability(self, tmp_path):
        ledger = TK.TakeoverLedger(str(tmp_path / "t.jsonl"))
        self._entry(ledger, attempted=1, failed=1, capability="cap.a")
        self._entry(ledger, attempted=1, failed=0, capability="cap.b")
        assert ledger.consecutive_failures("cap.a") == 1
        assert ledger.consecutive_failures("cap.b") == 0

    def test_corrupt_lines_are_skipped_not_fatal(self, tmp_path):
        path = tmp_path / "t.jsonl"
        path.write_text('{"capability_id": "x", "attempted": 1}\nnot json\n',
                        encoding="utf-8")
        ledger = TK.TakeoverLedger(str(path))
        assert len(ledger.entries()) == 1


# ════════════════════════════════════════════════════════════
#  比对（硬性两层 + 软性层如实 not_run）
# ════════════════════════════════════════════════════════════


class TestCompareTakeover:
    class _Obs:
        def __init__(self, status="success", steps=(), effects=None):
            self.status = status
            self.steps = list(steps)
            self.side_effects = dict(effects or {})

    def test_match_on_status_steps_and_effects(self):
        obs = self._Obs(steps=["read_file", "write_file"],
                        effects={"files_written": ["C:/sandbox/out/a.txt"]})
        result = ISO.IsolationResult(
            level="container", ran=True, status=ISO.STATUS_SUCCESS,
            steps=["read_file", "write_file"],
            side_effects={"files_written": ["/work/candidate/out/a.txt"]})
        verdict = TK.compare_takeover(obs, result)
        assert verdict["matched"] is True
        assert verdict["layers_passed"] == ["structure", "side_effects"]

    def test_status_mismatch_fails(self):
        obs = self._Obs(status="success", steps=["write_file"])
        result = ISO.IsolationResult(level="container", ran=True,
                                     status=ISO.STATUS_ESCAPE_BLOCKED,
                                     steps=["write_file"])
        verdict = TK.compare_takeover(obs, result)
        assert verdict["matched"] is False
        assert any("结构层" in r for r in verdict["reasons"])

    def test_step_mismatch_fails(self):
        obs = self._Obs(steps=["read_file", "write_file"])
        result = ISO.IsolationResult(level="container", ran=True,
                                     status=ISO.STATUS_SUCCESS,
                                     steps=["write_file"])
        assert TK.compare_takeover(obs, result)["matched"] is False

    def test_effect_mismatch_fails(self):
        obs = self._Obs(steps=["write_file"], effects={"files_written": ["C:/a.txt"]})
        result = ISO.IsolationResult(level="container", ran=True,
                                     status=ISO.STATUS_SUCCESS, steps=["write_file"],
                                     side_effects={"files_deleted": ["/work/a.txt"]})
        verdict = TK.compare_takeover(obs, result)
        assert verdict["matched"] is False
        assert any("副作用层" in r for r in verdict["reasons"])

    def test_soft_layer_is_marked_not_run(self):
        """软性 judge 层**不外推**（两条通道输出形态不同，比相似度会造假等价）"""
        obs = self._Obs(steps=["write_file"])
        result = ISO.IsolationResult(level="container", ran=True,
                                     status=ISO.STATUS_SUCCESS, steps=["write_file"])
        verdict = TK.compare_takeover(obs, result)
        judge = next(l for l in verdict["layers"] if l["layer"] == "judge")
        assert judge["passed"] is None
        assert judge["detail"]["status"] == "not_run"

    def test_status_aliases_keep_vocabulary_aligned(self):
        assert TK._norm_status("timeout") == ISO.STATUS_QUOTA_EXCEEDED
        assert TK._norm_status("killed") == ISO.STATUS_QUOTA_EXCEEDED
        assert TK._norm_status("success") == ISO.STATUS_SUCCESS


# ════════════════════════════════════════════════════════════
#  候选 → 隔离作业
# ════════════════════════════════════════════════════════════


class TestStepsToJob:
    def test_paths_are_redirected_preserving_relative_structure(self):
        case = make_case(3)
        job = TK.steps_to_job(case.native, case, job_id="t")
        assert job["status"] == "ok"
        labels = [s["op"] for s in job["steps"]]
        assert labels == ["read_file", "write_file"], "标签序列不得改变"
        assert job["steps"][0]["params"]["path"] == "candidate/out/a3.txt"
        assert job["steps"][1]["params"]["path"] == "candidate/out/a3.txt"

    def test_fixtures_are_carried_into_the_isolation_job(self):
        """夹具必须随作业进去：否则隔离执行的读操作会因"文件不存在"而系统性失败，
        比对失败的就成了夹具而不是候选"""
        case = make_case(3)
        job = TK.steps_to_job(case.native, case, job_id="t")
        assert job["fixtures"] == {"candidate/out/a3.txt": "c3"}

    def test_distinct_directories_do_not_collapse(self):
        from agent.digestion import cases as C
        case = make_case(0)
        steps = [C.ProgramStep(label="write_file",
                               params={"path": "C:/sandbox/x/one.txt",
                                       "content": "1"}),
                 C.ProgramStep(label="write_file",
                               params={"path": "C:/sandbox/y/one.txt",
                                       "content": "2"})]
        job = TK.steps_to_job(steps, case)
        paths = [s["params"]["path"] for s in job["steps"]]
        assert paths[0] != paths[1], "不同目录下的同名文件不得塌成同一路径"

    def test_unbound_input_is_refused_before_execution(self):
        from agent.digestion import cases as C
        case = make_case(0)
        steps = [C.ProgramStep(label="write_file",
                               params={"path": "${missing_slot}",
                                       "content": "1"})]
        job = TK.steps_to_job(steps, case)
        assert job["status"] == "unbound"
        assert job["steps"] == []
        assert job["unbound"]

    def test_unknown_ops_are_not_redirected(self):
        from agent.digestion import cases as C
        case = make_case(0)
        steps = [C.ProgramStep(label="shell_execute", params={"cmd": "ls"})]
        job = TK.steps_to_job(steps, case)
        assert job["steps"][0]["params"] == {"cmd": "ls"}


# ════════════════════════════════════════════════════════════
#  引擎（执行 / 预算 / 失败回落 / 事故卡）
# ════════════════════════════════════════════════════════════


def _engine(tmp_path, executor, *, emit=False, threshold=3, incident_dir=""):
    return TK.TakeoverEngine(
        executor=executor,
        ledger=TK.TakeoverLedger(str(tmp_path / "takeover.jsonl")),
        env={}, emit=emit, incident_dir=incident_dir or str(tmp_path / "incidents"))


def _replay_obs(status="success", steps=("read_file", "write_file"), effects=None):
    class _Obs:
        pass
    obs = _Obs()
    obs.status = status
    obs.steps = list(steps)
    obs.side_effects = dict(effects or {})
    return obs


def _run(engine, *, policy, gray=("t1",), cases=None, daily_avg=100.0,
         obs_for=None):
    return engine.run(CAP, policy=policy, gray_routed=list(gray),
                      cases=cases if cases is not None else {"t1": make_case(0)},
                      candidate_for=lambda case: case.native,
                      replay_obs_for=obs_for or (lambda sid: _replay_obs()),
                      daily_avg=daily_avg, isolation=_container_plan())


class TestTakeoverEngine:
    def test_disabled_policy_records_why_and_runs_nothing(self, tmp_path):
        executor = FakeExecutor()
        engine = _engine(tmp_path, executor)
        policy = TK.resolve_takeover_policy(env={}, isolation=_container_plan())
        report = _run(engine, policy=policy)
        assert report.enabled is False
        assert report.executed == 0
        assert executor.jobs == [], "关闭状态下不得向执行器提交任何作业"
        assert report.transport == TK.TRANSPORT_SANDBOX_ONLY
        # 关闭也要留台账：否则"为什么今天没接管"无从查起
        rows = engine.ledger.rows(CAP)
        assert rows and rows[-1]["attempted"] == 0
        assert rows[-1]["fallback"] is False

    def test_zero_budget_runs_nothing(self, tmp_path):
        executor = FakeExecutor()
        engine = _engine(tmp_path, executor)
        policy = TK.resolve_takeover_policy(env=ENABLE, isolation=_container_plan())
        report = _run(engine, policy=policy, daily_avg=0.0)
        assert report.budget == 0 and report.executed == 0
        assert executor.jobs == []
        assert any("每日预算为 0" in r for r in report.reasons)

    def test_executes_takeover_and_never_auto_adopts(self, tmp_path):
        executor = FakeExecutor()
        engine = _engine(tmp_path, executor)
        policy = TK.resolve_takeover_policy(env=ENABLE, isolation=_container_plan())
        report = _run(engine, policy=policy)
        assert report.enabled is True
        assert report.executed == 1
        assert report.adopted is False, "不自动合入候选产物（属 L2，另议）"
        assert report.attempts[0].adopted is False
        assert report.to_dict()["adopted"] is False
        assert executor.jobs and executor.jobs[0].get("probe_mode") is not True, \
            "候选作业不得带 probe_mode（那是探针专用门，开了就是逃逸后门）"

    def test_executor_refusal_counts_as_failure(self, tmp_path):
        executor = FakeExecutor(status=ISO.STATUS_REFUSED, error="no isolation")
        engine = _engine(tmp_path, executor)
        policy = TK.resolve_takeover_policy(env=ENABLE, isolation=_container_plan())
        report = _run(engine, policy=policy)
        assert report.executed == 1 and report.failed == 1
        assert report.attempts[0].status == TK.TAKEOVER_STATUS_FAILED

    def test_quota_exceeded_is_recorded_not_silent(self, tmp_path):
        executor = FakeExecutor(status=ISO.STATUS_TIMEOUT, quota_exceeded=True,
                                error="超时")
        engine = _engine(tmp_path, executor)
        policy = TK.resolve_takeover_policy(env=ENABLE, isolation=_container_plan())
        report = _run(engine, policy=policy)
        attempt = report.attempts[0]
        assert attempt.quota_exceeded is True and attempt.failed is True
        assert any("quota_exceeded" in r for r in attempt.reasons)

    def test_missing_case_is_counted_as_failure(self, tmp_path):
        executor = FakeExecutor()
        engine = _engine(tmp_path, executor)
        policy = TK.resolve_takeover_policy(env=ENABLE, isolation=_container_plan())
        report = _run(engine, policy=policy, cases={})
        assert report.executed == 0
        assert report.attempts[0].status == "missing_case"
        assert executor.jobs == []

    def test_missing_replay_observation_fails_loudly(self, tmp_path):
        executor = FakeExecutor()
        engine = _engine(tmp_path, executor)
        policy = TK.resolve_takeover_policy(env=ENABLE, isolation=_container_plan())
        report = _run(engine, policy=policy, obs_for=lambda sid: None)
        assert report.failed == 1
        assert any("无法比对" in r for r in report.attempts[0].reasons)

    def test_consecutive_failures_trigger_fallback_with_incident_card(self, tmp_path):
        """**核心验收**：连续 3 次失败 ⇒ 自动回落 + 事故卡（用例）"""
        executor = FakeExecutor(status=ISO.STATUS_ERROR, error="候选崩了")
        engine = _engine(tmp_path, executor, incident_dir=str(tmp_path / "incidents"))
        policy = TK.resolve_takeover_policy(env=ENABLE, isolation=_container_plan())
        reports = [_run(engine, policy=policy) for _ in range(3)]
        assert [r.fallback for r in reports] == [False, False, True]
        last = reports[-1]
        assert last.fallback_transport == TK.FALLBACK_TRANSPORT
        assert last.consecutive_failures == 3
        assert last.incident_id, "回落必须留下事故卡"
        assert os.path.exists(os.path.join(str(tmp_path / "incidents"),
                                           f"{last.incident_id}.json"))
        assert any("连续 3 次" in r for r in last.reasons)

    def test_fallback_state_blocks_further_takeover(self, tmp_path):
        executor = FakeExecutor(status=ISO.STATUS_ERROR, error="崩")
        engine = _engine(tmp_path, executor)
        policy = TK.resolve_takeover_policy(env=ENABLE, isolation=_container_plan())
        for _ in range(3):
            _run(engine, policy=policy)
        jobs_before = len(executor.jobs)
        report = _run(engine, policy=policy)
        assert report.fallback is True
        assert report.executed == 0, "已回落 ⇒ 不再执行接管"
        assert len(executor.jobs) == jobs_before
        assert any("已处于回落态" in r for r in report.reasons)

    def test_healthy_run_resets_consecutive_failures(self, tmp_path):
        failing = FakeExecutor(status=ISO.STATUS_ERROR)
        engine = _engine(tmp_path, failing)
        policy = TK.resolve_takeover_policy(env=ENABLE, isolation=_container_plan())
        _run(engine, policy=policy)
        _run(engine, policy=policy)
        engine.executor = FakeExecutor()
        ok = _run(engine, policy=policy)
        assert ok.fallback is False
        assert engine.ledger.consecutive_failures(CAP) == 0

    def test_emitting_records_audit_and_event(self, tmp_path, monkeypatch):
        executor = FakeExecutor()
        engine = _engine(tmp_path, executor, emit=True)
        seen: dict = {}

        def _fake_audit(action, **kwargs):
            seen["action"] = action
            seen["payload"] = kwargs.get("payload")
            seen["actor"] = kwargs.get("actor")
            return type("E", (), {"seq": 7, "self_hash": "h"})()

        def _fake_emit(*a, **k):
            seen["emitted"] = True
            return type("V", (), {"event_id": "ev-1"})()

        import agent.audit.facade as facade_mod
        monkeypatch.setattr(facade_mod.audit, "record", _fake_audit)
        monkeypatch.setattr("agent.observability.events.emit", _fake_emit)
        monkeypatch.setattr("agent.observability.events.trace_fields", lambda: {})

        policy = TK.resolve_takeover_policy(env=ENABLE, isolation=_container_plan())
        report = _run(engine, policy=policy)
        assert seen.get("action") == TK.AUDIT_ACTION_TAKEOVER
        assert seen.get("actor", "").startswith("digestion")
        assert seen["payload"]["adopted"] is False
        assert report.audit_seq == 7 and report.event_id == "ev-1"

    def test_fallback_audit_uses_its_own_action(self, tmp_path):
        executor = FakeExecutor(status=ISO.STATUS_ERROR)
        engine = _engine(tmp_path, executor)
        policy = TK.resolve_takeover_policy(env=ENABLE, isolation=_container_plan())
        for _ in range(3):
            report = _run(engine, policy=policy)
        assert report.fallback is True
        rows = engine.ledger.rows(CAP)
        assert rows[-1]["fallback"] is True
        assert rows[-1]["incident_id"] == report.incident_id

    def test_report_markdown_states_not_adopted(self, tmp_path):
        executor = FakeExecutor()
        engine = _engine(tmp_path, executor)
        policy = TK.resolve_takeover_policy(env=ENABLE, isolation=_container_plan())
        report = _run(engine, policy=policy)
        markdown = report.markdown()
        assert "未自动合入" in markdown
        assert "隔离等级" in markdown


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([os.path.abspath(__file__), "-q"]))
