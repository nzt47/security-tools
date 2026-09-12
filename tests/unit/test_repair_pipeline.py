"""TASK-S7-02 步骤 5 产出 + 五步流水线 单元测试

覆盖（任务书 §四 验收清单）：
- **未通过验证就不产出**（``propose()`` 结构性拒绝；端到端 status=discarded 且
  ``proposal is None``）
- 产出物：分支 + 补丁 + PR 描述 + 体检报告；**分支为本地分支**（未 push/未合并）
- PR 描述含：问题摘要 / 根因（引 Trace 证据）/ 补丁说明 / 三关验证证据 /
  undo_hint / **至少 3 条人工复核重点**
- **改测试断言时 PR 描述强制显式标注**
- 五步全部留 Trace + 链式审计（``verify_chain`` 通过；``audit_completeness`` 机械判定）
- 预算与轮次上限端到端生效（超限即停并留证）
- 「无失败」→ ``status=no_failure`` 且**不产出**、不编造问题
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from repair_fixtures import (
    FakeDelegationExecutor,
    FakeOutcome,
    StubProbeExecutor,
    make_patch,
    make_ticket,
    make_verification,
    simple_diff,
)

from agent.repair import pipeline as PL
from agent.repair import propose as PR
from agent.repair.models import (
    REASON_NO_FAILURE,
    REPAIR_STEPS,
    DiagnosisReport,
    FailureItem,
)
from agent.repair.policy import RepairPolicy


# ════════════════════════════════════════════════════════════
#  产出：验证不过 → 结构性拒绝
# ════════════════════════════════════════════════════════════


class TestProposalGate:
    def test_unverified_patch_cannot_be_proposed(self, tmp_path):
        """边界 ③ 的代码级落点：未验证 → ``ProposalNotVerified``"""
        with pytest.raises(PR.ProposalNotVerified):
            PR.propose(PR.ProposeRequest(
                repo_root=str(tmp_path), ticket=make_ticket(),
                patch=make_patch(simple_diff()),
                verification=make_verification(ok=False),
                run_id="rep-x", artifact_dir=str(tmp_path / "art")))

    def test_missing_verification_cannot_be_proposed(self, tmp_path):
        with pytest.raises(PR.ProposalNotVerified):
            PR.propose(PR.ProposeRequest(
                repo_root=str(tmp_path), ticket=make_ticket(),
                patch=make_patch(simple_diff()), verification=None,
                run_id="rep-x", artifact_dir=str(tmp_path / "art")))

    def test_nothing_written_when_rejected(self, tmp_path):
        art = tmp_path / "art"
        with pytest.raises(PR.ProposalNotVerified):
            PR.propose(PR.ProposeRequest(
                repo_root=str(tmp_path), ticket=make_ticket(),
                patch=make_patch(simple_diff()),
                verification=make_verification(ok=False),
                run_id="rep-x", artifact_dir=str(art)))
        assert not art.exists() or not list(art.iterdir())


# ════════════════════════════════════════════════════════════
#  产出：PR 描述结构
# ════════════════════════════════════════════════════════════


def _description(**overrides):
    kwargs = {
        "proposal_branch": "repair/20260912-tests-unit-test-demo-math-py-test-add",
        "ticket": make_ticket(),
        "patch": make_patch(simple_diff()),
        "verification": make_verification(ok=True),
        "diagnosis": DiagnosisReport(run_id="rep-1", repo_root="/repo",
                                     test_command="python -m pytest tests/unit/x.py",
                                     tests_passed=1, anchor_ok=True),
        "policy_dict": RepairPolicy().to_dict(),
        "run_id": "rep-1",
    }
    kwargs.update(overrides)
    return PR.pr_description(**kwargs)


class TestPrDescription:
    def test_contains_all_required_sections(self):
        text, focus, undo = _description()
        for section in ("问题摘要", "根因分析", "补丁说明", "验证证据", "风险与回滚",
                        "人工复核重点", "复现命令"):
            assert section in text, f"缺少章节：{section}"
        assert focus and undo

    def test_declares_local_only(self):
        text, _, _ = _description()
        assert "未 push 远端、未合并、未创建远端 PR" in text

    def test_review_focus_at_least_three(self):
        from agent.repair.policy import MIN_REVIEW_FOCUS_ITEMS
        _, focus, _ = _description()
        assert len(focus) >= MIN_REVIEW_FOCUS_ITEMS

    def test_review_focus_covers_root_cause_loosened_tests_and_scope(self):
        _, focus, _ = _description()
        joined = " ".join(focus)
        assert "根因" in joined
        assert "断言" in joined or "测试" in joined
        assert "范围" in joined or "无关" in joined

    def test_undo_hint_gives_concrete_commands(self):
        _, _, undo = _description()
        assert "git branch -D" in undo
        assert "git revert" in undo
        assert "git apply -R" in undo

    def test_test_change_is_explicitly_flagged(self):
        """改测试断言 → PR 描述**强制**显式标注（不依赖子代理自述）"""
        patch = make_patch(simple_diff("tests/unit/test_demo_math.py"))
        text, focus, _ = _description(patch=patch)
        assert "本次修改了测试文件" in text
        assert "人工必须重点审" in text
        assert "tests/unit/test_demo_math.py" in text
        assert any("测试文件改动清单" in f for f in focus)

    def test_no_flag_when_only_source_changed(self):
        text, _, _ = _description()
        assert "本次修改了测试文件" not in text

    def test_evidence_gap_is_disclosed(self):
        ticket = make_ticket(evidence_gaps=["Trace 库中无相关记录"])
        text, focus, _ = _description(ticket=ticket)
        assert "证据缺口" in text
        assert any("证据缺口" in f for f in focus)

    def test_missing_rationale_becomes_review_point(self):
        patch = make_patch(simple_diff(), rationale="")
        text, _, _ = _description(patch=patch)
        assert "未给出变更理由" in text

    def test_verification_table_lists_three_gates(self):
        text, _, _ = _description()
        assert "| target |" in text
        assert "| anchor |" in text
        assert "| adjacent |" in text


# ════════════════════════════════════════════════════════════
#  端到端流水线（小仓库 + 桩执行器/探针）
# ════════════════════════════════════════════════════════════


@pytest.fixture()
def e2e_repo(tmp_path):
    """一个**小型 git 仓库**：被测模块 + 失败测试（模拟注入的真实小 bug）"""
    root = tmp_path / "repo"
    (root / "agent").mkdir(parents=True)
    (root / "tests" / "unit").mkdir(parents=True)
    (root / "eval" / "l0_anchor").mkdir(parents=True)
    (root / "agent" / "demo_math.py").write_text("def add(a, b):\n    return a - b\n",
                                                 encoding="utf-8")
    (root / "agent" / "__init__.py").write_text("", encoding="utf-8")
    (root / "tests" / "unit" / "test_demo_math.py").write_text(
        "from agent.demo_math import add\n\n\ndef test_add():\n"
        "    assert add(1, 1) == 2\n", encoding="utf-8")
    (root / "eval" / "l0_anchor" / "cases.json").write_text('{"a": 1}', encoding="utf-8")
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    for args in (["init", "-q"], ["config", "user.email", "t@e.com"],
                 ["config", "user.name", "t"], ["add", "-A"],
                 ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", *args], cwd=str(root), capture_output=True, env=env)
    return root


JUNIT_FAIL = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="1" failures="1">
<testcase classname="tests.unit.test_demo_math" name="test_add"
 file="tests/unit/test_demo_math.py" line="5" time="0.01">
<failure message="assert -1 == 2">def test_add():
&gt;       assert add(1, 1) == 2
E       assert -1 == 2

tests/unit/test_demo_math.py:5: AssertionError</failure>
</testcase></testsuite></testsuites>
"""

JUNIT_PASS = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="1">
<testcase classname="tests.unit.test_demo_math" name="test_add"
 file="tests/unit/test_demo_math.py" line="5" time="0.01" />
</testsuite></testsuites>
"""


def _e2e_request(e2e_repo, tmp_path, *, diff: str = "", tokens: int = 1000,
                 policy=None, target_passes: bool = True, **overrides):
    """构造一次端到端请求：桩探针**按闸门自描述**地写 junitxml + 退出码

    探针从 ``--junitxml=<...>\\<name>.xml`` 的文件名判断当前是哪一关，从而对
    「多轮重试」也稳定成立（不依赖调用序号）：
      体检（``junit_*.xml``）→ 1 failed（junitxml 含结构化失败项，供定位器用）
      ``before.xml``  → 1 failed（证明"补丁前确实坏着"）
      ``target.xml``  → ``target_passes`` 决定（默认 1 passed）
      ``adjacent.xml``→ 1 passed
    """
    effective_diff = diff or simple_diff()

    class WritingProbe(StubProbeExecutor):
        def run(self, argv, *, cwd, timeout, env=None):
            out = super().run(argv, cwd=cwd, timeout=timeout, env=env)
            junit = ""
            for token in argv:
                if str(token).startswith("--junitxml="):
                    junit = str(token).split("=", 1)[1]
            name = os.path.basename(junit)
            if name == "target.xml":
                passing = bool(target_passes)
            elif name == "adjacent.xml":
                passing = True
            else:                       # 体检 或 before
                passing = False
            if junit:
                os.makedirs(os.path.dirname(junit) or ".", exist_ok=True)
                with open(junit, "w", encoding="utf-8") as fh:
                    fh.write(JUNIT_PASS if passing else JUNIT_FAIL)
            return ProbeOutputLike(out, 0 if passing else 1)

    probe = WritingProbe()
    executor = FakeDelegationExecutor([FakeOutcome(
        payload={"diff": effective_diff, "rationale": "off-by-one 修正",
                 "files": ["agent/demo_math.py"],
                 "self_eval": {"verdict": "pass", "score": 0.9}},
        tokens=tokens)] * 4)
    request = PL.PipelineRequest(
        repo_root=str(e2e_repo), executor=executor, targets=("tests/unit/test_demo_math.py",),
        # 轮次固定为 1：端到端样例要把「一轮跑全程」讲清楚；重试路径另有专门用例
        policy=policy or RepairPolicy(max_rounds=1), probe=probe,
        artifact_dir=str(tmp_path / "artifacts"), create_branch=False,
        with_anchor=False, emit_events=False, run_id="rep-e2e0001",
        trace_db=str(tmp_path / "trace.db"), audit=None, events_dir=str(tmp_path / "events"))
    for key, value in overrides.items():
        setattr(request, key, value)
    return request, probe, executor


def ProbeOutputLike(out, exit_code: int):
    """把探针输出改成指定退出码（保留命令与输出尾部）"""
    from agent.repair.diagnose import ProbeOutput
    return ProbeOutput(argv=out.argv, exit_code=int(exit_code), stdout=out.stdout,
                       stderr=out.stderr, duration_ms=out.duration_ms,
                       timed_out=out.timed_out, available=out.available)


class TestPipelineEndToEnd:
    def test_full_flow_produces_local_proposal(self, e2e_repo, tmp_path, monkeypatch):
        from agent.repair import verify as V
        monkeypatch.setattr(V, "run_anchor", lambda **kw: (True, {"total": 20,
                                                                  "failures": 0}))
        request, _, _ = _e2e_request(e2e_repo, tmp_path)
        report = PL.run_repair(request)

        assert report.status == PL.STATUS_PROPOSED, report.to_dict()
        assert report.produced is True
        assert report.proposal is not None
        assert report.proposal.pushed is False and report.proposal.merged is False
        assert os.path.isfile(report.proposal.patch_path)
        assert os.path.isfile(report.proposal.description_path)
        assert os.path.isfile(report.proposal.diagnose_path)
        assert len(report.proposal.review_focus) >= 3
        # 五步留痕齐全
        assert report.steps_recorded() == tuple(REPAIR_STEPS)
        ok, missing = PL.audit_completeness(report)
        assert ok is True, missing

    def test_audit_trail_has_five_steps_with_actions(self, e2e_repo, tmp_path,
                                                     monkeypatch):
        from agent.repair import verify as V
        monkeypatch.setattr(V, "run_anchor", lambda **kw: (True, {"total": 20,
                                                                  "failures": 0}))
        request, _, _ = _e2e_request(e2e_repo, tmp_path)
        report = PL.run_repair(request)
        steps = [e.step for e in report.audit_trail]
        assert steps.count("diagnose") == 1
        for step in REPAIR_STEPS:
            assert step in steps, f"缺少留痕：{step}"
        for entry in report.audit_trail:
            assert entry.action.startswith("repair.")
            assert entry.actor == "auto"
            assert entry.trace_id, f"{entry.step} 缺少 trace_id（一步一 Trace）"
    def test_verification_failure_discards_everything(self, e2e_repo, tmp_path,
                                                      monkeypatch):
        """**核心验收项**：验证不过 → 不产出（proposal is None）且如实报告"""
        from agent.repair import verify as V
        monkeypatch.setattr(V, "run_anchor", lambda **kw: (True, {"total": 20,
                                                                  "failures": 0}))
        request, probe, _ = _e2e_request(e2e_repo, tmp_path, target_passes=False)
        report = PL.run_repair(request)
        assert report.status == PL.STATUS_DISCARDED
        assert report.produced is False
        assert report.proposal is None
        assert "E_REPAIR_NOT_VERIFIED" in report.reasons
        assert report.patch is not None and report.patch.non_empty, \
            "被丢弃的补丁仍应保留在报告里（人要看子代理到底想改什么）"
        assert any("丢弃" in n for n in report.budget_notes)

    def test_readonly_patch_is_rejected_before_verification(self, e2e_repo, tmp_path,
                                                            monkeypatch):
        from agent.repair import verify as V
        calls = []
        monkeypatch.setattr(V, "run_anchor",
                            lambda **kw: calls.append(1) or (True, {"total": 20,
                                                                    "failures": 0}))
        request, probe, _ = _e2e_request(
            e2e_repo, tmp_path, diff=simple_diff("agent/audit/chain.py"))
        report = PL.run_repair(request)
        assert report.status == PL.STATUS_REJECTED
        assert "E_REPAIR_READONLY_ZONE" in report.reasons
        assert report.produced is False
        assert calls == [], "护栏拒绝的补丁不得进入隔离验证"

    def test_no_failure_is_legal_and_stops_early(self, e2e_repo, tmp_path,
                                                 monkeypatch):
        request, probe, executor = _e2e_request(e2e_repo, tmp_path)
        # 体检无失败 → 不应派工
        class PassingProbe(StubProbeExecutor):
            def run(self, argv, *, cwd, timeout, env=None):
                out = super().run(argv, cwd=cwd, timeout=timeout, env=env)
                for token in argv:
                    if str(token).startswith("--junitxml="):
                        path = str(token).split("=", 1)[1]
                        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                        with open(path, "w", encoding="utf-8") as fh:
                            fh.write(JUNIT_PASS)
                return out

        request.probe = PassingProbe(default={"exit_code": 0, "stdout": "1 passed"})
        report = PL.run_repair(request)
        assert report.status == PL.STATUS_NO_FAILURE
        assert report.reason == REASON_NO_FAILURE
        assert report.produced is False
        assert report.steps_recorded() == ("diagnose",)
        assert executor.calls == [], "无失败时不得派工"

    def test_report_json_written(self, e2e_repo, tmp_path, monkeypatch):
        from agent.repair import verify as V
        monkeypatch.setattr(V, "run_anchor", lambda **kw: (True, {"total": 20,
                                                                  "failures": 0}))
        request, _, _ = _e2e_request(e2e_repo, tmp_path)
        report = PL.run_repair(request)
        path = os.path.join(str(tmp_path / "artifacts"), f"run_{report.run_id}.json")
        assert os.path.isfile(path)
        payload = json.loads(open(path, encoding="utf-8").read())
        assert payload["run_id"] == "rep-e2e0001"
        assert payload["produced"] is True

    def test_report_markdown_renders(self, e2e_repo, tmp_path, monkeypatch):
        from agent.repair import verify as V
        monkeypatch.setattr(V, "run_anchor", lambda **kw: (True, {"total": 20,
                                                                  "failures": 0}))
        request, _, _ = _e2e_request(e2e_repo, tmp_path)
        report = PL.run_repair(request)
        md = PL.report_markdown(report)
        assert "自修复 L1 运行报告" in md
        assert "隔离验证三关" in md
        assert "未 push" in md

    def test_discarded_report_states_no_product(self, e2e_repo, tmp_path, monkeypatch):
        from agent.repair import verify as V
        monkeypatch.setattr(V, "run_anchor", lambda **kw: (True, {"total": 20,
                                                                  "failures": 0}))
        request, probe, _ = _e2e_request(e2e_repo, tmp_path, target_passes=False)
        report = PL.run_repair(request)
        md = PL.report_markdown(report)
        assert "未产出" in md
        assert "补丁未通过三关" in md


class TestPipelineBudget:
    def test_budget_exceeded_stops_with_evidence(self, e2e_repo, tmp_path, monkeypatch):
        from agent.repair import verify as V
        monkeypatch.setattr(V, "run_anchor", lambda **kw: (True, {"total": 20,
                                                                  "failures": 0}))
        policy = RepairPolicy(budget_tokens=100, max_rounds=1)
        request, _, _ = _e2e_request(e2e_repo, tmp_path, tokens=5000, policy=policy)
        report = PL.run_repair(request)
        assert report.produced is False
        assert "E_REPAIR_BUDGET_EXCEEDED" in report.reasons
        assert report.tokens_used == 5000
        assert any("预算" in n or "token" in n.lower() for n in report.budget_notes)

    def test_round_limit_stops_after_n_rounds(self, e2e_repo, tmp_path, monkeypatch):
        from agent.repair import verify as V
        monkeypatch.setattr(V, "run_anchor", lambda **kw: (True, {"total": 20,
                                                                  "failures": 0}))
        policy = RepairPolicy(max_rounds=1)
        request, probe, executor = _e2e_request(e2e_repo, tmp_path, policy=policy,
                                               target_passes=False)
        report = PL.run_repair(request)
        assert report.rounds_used == 1
        assert len(executor.calls) == 1, "轮次上限为 1 时不应派工第二次"
        assert report.produced is False

    def test_expected_steps_by_status(self):
        assert PL.expected_steps(PL.STATUS_NO_FAILURE) == ("diagnose",)
        assert PL.expected_steps(PL.STATUS_PROPOSED) == tuple(REPAIR_STEPS)
        assert PL.expected_steps(PL.STATUS_DISCARDED) == tuple(REPAIR_STEPS[:3])


class TestBranchNaming:
    def test_branch_name_format(self):
        from datetime import datetime, timezone
        name = PR.branch_name(slug="tests/unit/test_demo_math.py::test_add",
                              when=datetime(2026, 9, 12, tzinfo=timezone.utc))
        assert name.startswith("repair/20260912-")
        assert "/" not in name[len("repair/"):]
        assert name.count("/") == 1

    def test_artifact_dir_default_is_gitignored(self, tmp_path):
        target = PR.default_artifact_dir(str(tmp_path))
        assert target.endswith(os.path.join("data", "repair"))

    def test_review_focus_has_no_fewer_than_minimum(self):
        focus = PR.review_focus(patch=make_patch(simple_diff()),
                               verification=make_verification(ok=True),
                               ticket=make_ticket())
        assert len(focus) >= 3
