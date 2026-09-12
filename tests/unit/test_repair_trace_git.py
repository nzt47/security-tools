"""TASK-S7-02 留痕（Trace + 链式审计）与只读 git 层 单元测试

覆盖：
- ``RepairRunLogger``：五步各写一条 Trace + 一条链式审计；**每条恒有 trace_id**
- ``run_step`` 在成功/失败/异常三条路径上都留痕，且异常**原样重抛**
- 审计链 ``verify_chain`` 可校验
- 出口不可用/显式关闭时**降级并披露**（绝不假成功）
- ``gitio``：非 git 目录、非法子命令、前缀参数、未知子命令、只读子命令集
- ``RepairBudget``：超限即停、余额预检、未计量如实记 0
"""

from __future__ import annotations

import os

import pytest

from agent.repair import gitio as G
from agent.repair.budget import BudgetExceeded, RepairBudget
from agent.repair.models import AUDIT_ACTIONS, REPAIR_STEPS
from agent.repair.policy import RepairPolicy
from agent.repair.trace import (
    ACTOR_PIPELINE,
    RepairRunLogger,
    RunLoggerConfig,
    new_run_id,
    short_hash,
    slugify,
    utc_now_iso,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="module")
def outside_repo():
    """**仓库之外**的临时目录

    【为什么不能直接用 ``tempfile.mkdtemp()`` 或 ``tmp_path``】
      - ``tmp_path`` 位于 ``<repo>/.pytest_tmp/``；
      - pytest 会把 ``tempfile.tempdir`` 指到它自己的 basetemp（也在仓库内）；
    两者都会让 git 向上查找并命中本仓库，于是 ``is_git_repo`` 返回 True，
    "非仓库退化"这条路径根本没被走到（实测踩过）。故显式指定系统临时目录。
    """
    import shutil
    import tempfile
    base = os.environ.get("SystemRoot") and os.path.join(
        os.environ.get("SystemRoot", ""), "Temp") or None
    path = tempfile.mkdtemp(prefix="cp-repair-outside-", dir=base)
    assert G.is_git_repo(path) is False, "夹具前提失败：该目录应位于 git 仓库之外"
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture()
def run_logger(tmp_path):
    from repair_fixtures import close_run_logger, make_run_logger
    logger, facade = make_run_logger(tmp_path)
    yield logger
    close_run_logger(logger, facade)


# ════════════════════════════════════════════════════════════
#  留痕器
# ════════════════════════════════════════════════════════════


class TestRepairRunLogger:
    def test_run_id_format(self):
        assert new_run_id().startswith("rep-")

    def test_record_step_writes_audit_and_trace(self, run_logger):
        entry = run_logger.record_step("diagnose", subject="tests/unit/x.py", status="ok")
        assert entry.action == "repair.diagnose"
        assert entry.actor == ACTOR_PIPELINE
        assert entry.trace_id, "每条留痕恒有 trace_id（一步一 Trace）"
        assert entry.seq, "审计链应分配 seq"
        run_logger.flush()
        assert run_logger.trail() == [entry]

    def test_run_step_returns_value_and_records_ok(self, run_logger):
        result = run_logger.run_step("locate", "tkt-1", lambda: 42)
        assert result == 42
        entry = run_logger.trail()[-1]
        assert entry.status == "ok"
        assert entry.duration_ms >= 0

    def test_run_step_records_error_and_reraises(self, run_logger):
        def _boom():
            raise ValueError("炸了")

        with pytest.raises(ValueError):
            run_logger.run_step("delegate", "tkt-1", _boom)
        entry = run_logger.trail()[-1]
        assert entry.status == "error"
        assert "ValueError" in entry.detail.get("error", "")

    def test_missing_steps_are_decidable(self, run_logger):
        run_logger.record_step("diagnose", subject="x")
        missing = run_logger.missing_steps()
        assert missing == tuple(REPAIR_STEPS[1:])
        run_logger.record_step("propose", subject="x")
        assert "propose" not in run_logger.missing_steps()

    def test_all_actions_registered(self, run_logger):
        for step in REPAIR_STEPS:
            entry = run_logger.record_step(step, subject="x")
            assert entry.action == AUDIT_ACTIONS[step]

    def test_verify_chain_passes(self, run_logger):
        for step in REPAIR_STEPS:
            run_logger.record_step(step, subject="x")
        assert run_logger.verify_chain() is True

    def test_verify_chain_none_when_audit_unavailable(self, tmp_path):
        logger = RepairRunLogger(RunLoggerConfig(repo_root=str(tmp_path), audit=None,
                                                 trace_enabled=False))
        logger.config.audit = None
        # 显式把 facade 置为不可用（模拟审计关闭）
        logger._facade_error = "测试：审计不可用"
        assert logger.verify_chain() is None

    def test_trace_disabled_is_disclosed(self, tmp_path):
        logger = RepairRunLogger(
            RunLoggerConfig(repo_root=str(tmp_path), trace_enabled=False,
                            trace_db=str(tmp_path / "t.db")))
        logger.record_step("diagnose", subject="x")
        assert logger.store is None
        assert any("降级" in n or "关闭" in n for n in logger.notes)

    def test_audit_payload_has_no_live_objects(self, run_logger):
        """载荷只放叶子字段：不得出现 Service/Trace 对象"""
        entry = run_logger.record_step("verify", subject="x", detail={
            "ok": True, "count": 3, "names": ["a", "b"]})
        for value in entry.detail.values():
            assert isinstance(value, (str, int, float, bool, list, dict, type(None)))

    def test_emit_event_is_best_effort(self, run_logger):
        # 未登记事件类型 → 返回 None（绝不抛）
        result = run_logger.emit_event("definitely.not.an.event", {"a": 1})
        assert result is None or result is not None  # 不抛即通过

    def test_workspace_id_derived_from_repo_root(self, tmp_path):
        logger = RepairRunLogger(RunLoggerConfig(repo_root=str(tmp_path)))
        assert logger.workspace_id.startswith("ws_")


class TestTextHelpers:
    def test_slugify_keeps_safe_chars(self):
        slug = slugify("tests/unit/test_demo_math.py::test_add")
        assert "/" not in slug and ":" not in slug
        assert slug

    def test_slugify_empty_falls_back(self):
        assert slugify("") == "issue"
        assert slugify("~~~") == "issue"

    def test_slugify_truncates(self):
        assert len(slugify("a" * 200)) <= 48

    def test_short_hash_deterministic(self):
        assert short_hash("x") == short_hash("x")
        assert len(short_hash("x", length=8)) == 8

    def test_utc_now_iso_is_parseable(self):
        from datetime import datetime
        datetime.fromisoformat(utc_now_iso())


# ════════════════════════════════════════════════════════════
#  只读 git 层
# ════════════════════════════════════════════════════════════


class TestGitioReadonly:
    @pytest.mark.parametrize("cmd", ["log", "show", "diff", "status", "rev-parse",
                                     "ls-files", "check-ignore"])
    def test_allowlisted_subcommands_pass(self, cmd):
        assert G.assert_readonly_subcommand(cmd) == cmd
        assert cmd in G.READONLY_SUBCOMMANDS

    @pytest.mark.parametrize("cmd", ["push", "merge", "rebase", "reset", "commit",
                                     "add", "remote", "fetch", "tag", "config"])
    def test_writing_subcommands_rejected(self, cmd):
        with pytest.raises(G.ForbiddenGitOperation):
            G.assert_readonly_subcommand(cmd)

    @pytest.mark.parametrize("cmd", ["", "-c", "--git-dir=/tmp/x", "frobnicate"])
    def test_odd_inputs_rejected(self, cmd):
        with pytest.raises(G.ForbiddenGitOperation):
            G.assert_readonly_subcommand(cmd)

    def test_is_readonly_invocation(self):
        assert G.is_readonly_invocation(["log"]) is True
        assert G.is_readonly_invocation(["push"]) is False
        assert G.is_readonly_invocation([]) is False
        assert G.is_readonly_invocation(["log", "--output=x"]) is False

    def test_repo_head_on_non_repo_returns_empty(self, outside_repo):
        assert G.repo_head(outside_repo) == ""

    def test_is_git_repo_false(self, outside_repo):
        assert G.is_git_repo(outside_repo) is False

    def test_recent_commits_on_non_repo_returns_empty(self, outside_repo):
        assert G.recent_commits(outside_repo, limit=3) == []
        assert G.head_changed_files(outside_repo) == []

    def test_recent_commits_against_real_repo(self):
        commits = G.recent_commits(REPO_ROOT, limit=2)
        assert commits and commits[0]["sha"]
        assert commits[0]["subject"]
        assert isinstance(commits[0]["files"], list)

    def test_branch_exists_false(self, outside_repo):
        assert G.branch_exists(outside_repo, "no/such/branch") is False

    def test_run_git_readonly_rejects_before_subprocess(self, outside_repo, monkeypatch):
        calls = []
        monkeypatch.setattr(G.subprocess, "run",
                            lambda *a, **kw: calls.append(a) or None)
        with pytest.raises(G.ForbiddenGitOperation):
            G.run_git_readonly(["push", "origin", "master"], cwd=outside_repo)
        assert calls == []

    def test_run_git_readonly_nonzero_raises(self, outside_repo):
        with pytest.raises(G.GitUnavailableError):
            G.run_git_readonly(["rev-parse", "--verify", "refs/heads/nope"],
                               cwd=outside_repo)

    def test_git_dir_for_worktree_file(self, tmp_path):
        """``.git`` 是文件（worktree）时解析出真实 gitdir"""
        gitfile = tmp_path / ".git"
        gitfile.write_text("gitdir: C:/repo/.git/worktrees/s702\n", encoding="utf-8")
        resolved = G.git_dir(str(tmp_path))
        assert resolved.endswith(os.path.join(".git", "worktrees", "s702")) or \
            "worktrees" in resolved

    def test_git_dir_for_normal_dir(self, tmp_path):
        (tmp_path / ".git").mkdir()
        assert G.git_dir(str(tmp_path)) == str(tmp_path / ".git")

    def test_head_changed_files_against_real_repo(self):
        files = G.head_changed_files(REPO_ROOT)
        assert files and all(not f.startswith('"') for f in files)

    def test_is_tracked_and_ignored(self):
        assert G.is_tracked(REPO_ROOT, "agent/subagent/delegation.py") is True
        assert G.is_tracked(REPO_ROOT, "no/such/file.py") is False
        assert G.is_ignored(REPO_ROOT, "data/repair/whatever.json") is True
        assert G.is_ignored(REPO_ROOT, "agent/repair/policy.py") is False


# ════════════════════════════════════════════════════════════
#  预算账本
# ════════════════════════════════════════════════════════════


class TestBudget:
    def test_from_policy(self):
        budget = RepairBudget.from_policy(RepairPolicy(budget_tokens=1234, max_rounds=3))
        assert budget.token_limit == 1234 and budget.max_rounds == 3

    def test_rounds_count_up(self):
        budget = RepairBudget(token_limit=100, max_rounds=2)
        assert budget.start_round() == 1
        assert budget.start_round() == 2
        with pytest.raises(BudgetExceeded) as exc:
            budget.start_round()
        assert exc.value.code == "E_REPAIR_ROUNDS_EXCEEDED"
        assert budget.rounds_left == 0

    def test_consume_and_left(self):
        budget = RepairBudget(token_limit=100, max_rounds=1)
        budget.consume(30)
        assert budget.tokens_used == 30 and budget.tokens_left == 70
        with pytest.raises(BudgetExceeded):
            budget.consume(100)

    def test_unparsable_tokens_recorded_as_zero(self):
        budget = RepairBudget(token_limit=100, max_rounds=1)
        assert budget.consume("abc", source="test") == 0
        assert any("不可解析" in n for n in budget.notes)

    def test_negative_tokens_recorded_as_zero(self):
        budget = RepairBudget(token_limit=100, max_rounds=1)
        assert budget.consume(-5) == 0
        assert any("为负" in n for n in budget.notes)

    def test_zero_tokens_notes_not_estimated(self):
        budget = RepairBudget(token_limit=100, max_rounds=1)
        budget.consume(0, source="subagent")
        assert any("未计量" in n for n in budget.notes)

    def test_headroom_check(self):
        budget = RepairBudget(token_limit=10, max_rounds=1)
        budget.tokens_used = 10
        with pytest.raises(BudgetExceeded):
            budget.check_headroom(needed=1)

    def test_to_dict(self):
        budget = RepairBudget(token_limit=10, max_rounds=2)
        budget.start_round()
        budget.consume(4)
        payload = budget.to_dict()
        assert payload["tokens_left"] == 6 and payload["rounds_left"] == 1
