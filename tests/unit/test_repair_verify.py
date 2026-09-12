"""TASK-S7-02 步骤 4 隔离验证 单元测试（核心：不过即丢弃）

覆盖（任务书 §四 验收清单）：
- **补丁未通过三关则不产出**（逐关单独失败：目标用例不过 / L0 锚不过 / 邻接回归不过）
- **「打补丁前目标用例本来就通过」→ 也不放行**（防"本来没坏"被当成"修好了"）
- 补丁应用失败 → 不放行
- 验证**只在临时副本**上进行：真实仓库根在验证后 **git status 无漂移**
- 临时副本在收尾时被清理
- 统一 diff 应用器：新增/删除文件、上下文不匹配即整体放弃（不留半成品）
"""

from __future__ import annotations

import os
import subprocess

import pytest

from repair_fixtures import (
    make_patch,
    make_ticket,
    simple_diff,
)

from agent.repair import patchapply as PA
from agent.repair import verify as V
from agent.repair.models import RepairTicket, FailureItem
from agent.repair.policy import RepairPolicy


# ════════════════════════════════════════════════════════════
#  统一 diff 应用器
# ════════════════════════════════════════════════════════════


@pytest.fixture()
def mini_repo(tmp_path):
    """一个最小仓库树（**不建 git**，应用器不需要）"""
    root = tmp_path / "repo"
    (root / "agent").mkdir(parents=True)
    (root / "agent" / "demo_math.py").write_text(
        "def add(a, b):\n    return a - b\n\n", encoding="utf-8")
    return root


class TestPatchApply:
    def test_applies_simple_change(self, mini_repo):
        result = PA.apply_unified_diff(simple_diff(), str(mini_repo))
        assert result.ok is True, result.error
        assert result.applied == ["agent/demo_math.py"]
        text = (mini_repo / "agent" / "demo_math.py").read_text(encoding="utf-8")
        assert "return a + b" in text

    def test_mismatched_context_is_rejected_atomically(self, mini_repo):
        """上下文不匹配 → 整体放弃（文件**不被修改**，不留半成品）"""
        before = (mini_repo / "agent" / "demo_math.py").read_text(encoding="utf-8")
        bad = simple_diff(old_content="def add(a, b):\n    return a * b\n\n",
                          new_content="def add(a, b):\n    return a + b\n\n")
        result = PA.apply_unified_diff(bad, str(mini_repo))
        assert result.ok is False
        assert result.failed_path == "agent/demo_math.py"
        after = (mini_repo / "agent" / "demo_math.py").read_text(encoding="utf-8")
        assert before == after

    def test_partial_failure_does_not_write_other_files(self, mini_repo):
        """多文件中有一个失败 → 另一个也**不**落盘（原子性）"""
        (mini_repo / "agent" / "other.py").write_text("y = 0\nx = 1\n", encoding="utf-8")
        good = simple_diff("agent/other.py", old_content="y = 0\nx = 1\n",
                           new_content="y = 0\nx = 2\n")
        # 把 good 的 hunk 头改成一个**不存在的位置**，使上下文匹配失败
        broken = good.replace("@@ -1,2 +1,2 @@", "@@ -900,2 +900,2 @@")
        diff = simple_diff() + broken
        before = (mini_repo / "agent" / "demo_math.py").read_text(encoding="utf-8")
        result = PA.apply_unified_diff(diff, str(mini_repo))
        assert result.ok is False
        assert (mini_repo / "agent" / "demo_math.py").read_text(encoding="utf-8") == before

    def test_new_file_created(self, mini_repo):
        from repair_fixtures import new_file_diff
        result = PA.apply_unified_diff(new_file_diff("agent/new.py", "def f():\n    return 1\n"),
                                       str(mini_repo))
        assert result.ok is True, result.error
        assert result.created == ["agent/new.py"]
        assert "def f()" in (mini_repo / "agent" / "new.py").read_text(encoding="utf-8")

    def test_deleted_file_removed(self, mini_repo):
        from repair_fixtures import DEFAULT_OLD_CONTENT, deleted_file_diff
        result = PA.apply_unified_diff(
            deleted_file_diff("agent/demo_math.py", DEFAULT_OLD_CONTENT), str(mini_repo))
        assert result.ok is True, result.error
        assert not (mini_repo / "agent" / "demo_math.py").exists()

    def test_missing_target_file_rejected(self, mini_repo):
        result = PA.apply_unified_diff(simple_diff("agent/ghost.py"), str(mini_repo))
        assert result.ok is False
        assert "不存在" in result.error

    def test_dry_run_does_not_write(self, mini_repo):
        before = (mini_repo / "agent" / "demo_math.py").read_text(encoding="utf-8")
        result = PA.apply_unified_diff(simple_diff(), str(mini_repo), dry_run=True)
        assert result.ok is True
        assert (mini_repo / "agent" / "demo_math.py").read_text(encoding="utf-8") == before

    def test_fuzz_window_finds_shifted_block(self, mini_repo):
        """hunk 期望位置漂移但在窗口内 → 仍能精确匹配成功"""
        (mini_repo / "agent" / "demo_math.py").write_text(
            "# 头部注释 1\n# 头部注释 2\ndef add(a, b):\n    return a - b\n",
            encoding="utf-8")
        result = PA.apply_unified_diff(simple_diff(), str(mini_repo))
        assert result.ok is True, result.error
        assert "return a + b" in (mini_repo / "agent" / "demo_math.py").read_text(
            encoding="utf-8")

    def test_hunk_blocks_split(self):
        diff = simple_diff()
        parsed, _ = __import__("agent.repair.guardrails", fromlist=["x"]).parse_unified_diff(diff)
        old, new = PA.hunk_blocks(parsed[0].hunks[0])
        assert "    return a - b" in old
        assert "    return a + b" in new
        assert "def add(a, b):" in old and "def add(a, b):" in new


# ════════════════════════════════════════════════════════════
#  隔离副本
# ════════════════════════════════════════════════════════════


@pytest.fixture()
def real_repo(tmp_path):
    """带 git 的小仓库（用于"副本隔离 + 真仓库无漂移"断言）"""
    root = tmp_path / "realrepo"
    (root / "agent").mkdir(parents=True)
    (root / "tests" / "unit").mkdir(parents=True)
    (root / "eval" / "l0_anchor").mkdir(parents=True)
    (root / "agent" / "demo_math.py").write_text(
        "def add(a, b):\n    return a - b\n", encoding="utf-8")
    (root / "tests" / "unit" / "test_demo_math.py").write_text(
        "from agent.demo_math import add\n\n\ndef test_add():\n"
        "    assert add(1, 1) == 2\n", encoding="utf-8")
    (root / "eval" / "l0_anchor" / "cases.json").write_text('{"anchor": true}',
                                                            encoding="utf-8")
    (root / "junk").mkdir()
    (root / "junk" / "big.log").write_text("x" * 100, encoding="utf-8")
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    for args in (["init", "-q"], ["config", "user.email", "t@example.com"],
                 ["config", "user.name", "t"], ["add", "-A"],
                 ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", *args], cwd=str(root), capture_output=True, env=env)
    return root


class TestIsolatedCopy:
    def test_copy_excludes_git_and_logs(self, real_repo):
        copy_root = V.prepare_isolated_copy(str(real_repo))
        try:
            assert os.path.isdir(copy_root)
            assert not os.path.exists(os.path.join(copy_root, ".git"))
            assert not os.path.exists(os.path.join(copy_root, "junk", "big.log"))
            assert os.path.exists(os.path.join(copy_root, "agent", "demo_math.py"))
        finally:
            V.cleanup_isolated_copy(copy_root)

    def test_cleanup_removes_copy(self, real_repo):
        copy_root = V.prepare_isolated_copy(str(real_repo))
        assert V.cleanup_isolated_copy(copy_root) is True
        assert not os.path.exists(copy_root)

    def test_cleanup_is_idempotent(self, tmp_path):
        assert V.cleanup_isolated_copy(str(tmp_path / "nope")) is True

    def test_missing_root_raises(self, tmp_path):
        with pytest.raises(NotADirectoryError):
            V.prepare_isolated_copy(str(tmp_path / "nope"))


# ════════════════════════════════════════════════════════════
#  三关（不过即丢弃）
# ════════════════════════════════════════════════════════════


def _ticket_for(real_repo) -> RepairTicket:
    return RepairTicket(
        ticket_id="tkt-test",
        failure=FailureItem(node_id="tests/unit/test_demo_math.py::test_add",
                            file="tests/unit/test_demo_math.py", line=5,
                            message="assert -1 == 2", stack_fingerprint="fp0001"),
        slices=[], impl_files=["agent/demo_math.py"], repo_head="", 
        adjacent_tests=["tests/unit/test_demo_math.py"])


class TestVerifyGates:
    def test_three_gates_pass(self, real_repo, monkeypatch):
        """before 失败 → 打补丁 → 三关全过 → ok=True"""
        from repair_fixtures import StubProbeExecutor
        probe = StubProbeExecutor(script=[
            {"exit_code": 1, "stdout": "1 failed"},   # before
            {"exit_code": 0, "stdout": "1 passed"},   # after target
            {"exit_code": 0, "stdout": "1 passed"},   # adjacent
        ])
        monkeypatch.setattr(V, "run_anchor", lambda **kw: (True, {"total": 20,
                                                                  "failures": 0}))
        report = V.verify(V.VerifyRequest(patch=make_patch(simple_diff()),
                                          ticket=_ticket_for(real_repo),
                                          repo_root=str(real_repo),
                                          executor=probe, keep_copy=True))
        assert report.ok is True, report.to_dict()
        assert report.target_failed_before is True
        assert report.anchor.effective_pass is True
        assert report.adjacent.effective_pass is True
        V.cleanup_isolated_copy(report.temp_root)

    def test_target_pass_before_is_rejected(self, real_repo, monkeypatch):
        """打补丁前目标用例就通过 → 基线不成立 → 不放行（防"本来没坏"）"""
        from repair_fixtures import StubProbeExecutor
        probe = StubProbeExecutor(default={"exit_code": 0, "stdout": "1 passed"})
        monkeypatch.setattr(V, "run_anchor", lambda **kw: (True, {"total": 20,
                                                                  "failures": 0}))
        report = V.verify(V.VerifyRequest(patch=make_patch(simple_diff()),
                                          ticket=_ticket_for(real_repo),
                                          repo_root=str(real_repo), executor=probe))
        assert report.ok is False
        assert report.target_failed_before is False
        assert "无法证明补丁修好了任何东西" in report.target.output_summary

    def test_anchor_failure_blocks(self, real_repo, monkeypatch):
        from repair_fixtures import StubProbeExecutor
        probe = StubProbeExecutor(script=[
            {"exit_code": 1, "stdout": "1 failed"},
            {"exit_code": 0, "stdout": "1 passed"},
            {"exit_code": 0, "stdout": "1 passed"},
        ])
        monkeypatch.setattr(V, "run_anchor",
                            lambda **kw: (False, {"total": 20, "failures": 1}))
        report = V.verify(V.VerifyRequest(patch=make_patch(simple_diff()),
                                          ticket=_ticket_for(real_repo),
                                          repo_root=str(real_repo), executor=probe))
        assert report.ok is False
        assert report.target.effective_pass is True
        assert report.anchor.effective_pass is False

    def test_anchor_not_run_blocks_fail_closed(self, real_repo, monkeypatch):
        from repair_fixtures import StubProbeExecutor
        probe = StubProbeExecutor(script=[
            {"exit_code": 1, "stdout": "1 failed"},
            {"exit_code": 0, "stdout": "1 passed"},
            {"exit_code": 0, "stdout": "1 passed"},
        ])
        monkeypatch.setattr(V, "run_anchor",
                            lambda **kw: (None, {"error": "锚完整性失败"}))
        report = V.verify(V.VerifyRequest(patch=make_patch(simple_diff()),
                                          ticket=_ticket_for(real_repo),
                                          repo_root=str(real_repo), executor=probe))
        assert report.ok is False
        assert report.anchor.skipped is True
        assert report.anchor.effective_pass is False

    def test_adjacent_failure_blocks(self, real_repo, monkeypatch):
        from repair_fixtures import StubProbeExecutor
        probe = StubProbeExecutor(script=[
            {"exit_code": 1, "stdout": "1 failed"},
            {"exit_code": 0, "stdout": "1 passed"},
            {"exit_code": 1, "stdout": "1 failed"},   # 邻接回归炸了
        ])
        monkeypatch.setattr(V, "run_anchor", lambda **kw: (True, {"total": 20,
                                                                  "failures": 0}))
        report = V.verify(V.VerifyRequest(patch=make_patch(simple_diff()),
                                          ticket=_ticket_for(real_repo),
                                          repo_root=str(real_repo), executor=probe))
        assert report.ok is False
        assert report.adjacent.effective_pass is False

    def test_apply_failure_blocks(self, real_repo, monkeypatch):
        from repair_fixtures import StubProbeExecutor
        probe = StubProbeExecutor(script=[{"exit_code": 1, "stdout": "1 failed"}])
        monkeypatch.setattr(V, "run_anchor", lambda **kw: (True, {"total": 20,
                                                                  "failures": 0}))
        bad = simple_diff(old_content="def add(a, b):\n    return a * b\n",
                          new_content="def add(a, b):\n    return a + b\n")
        report = V.verify(V.VerifyRequest(patch=make_patch(bad),
                                          ticket=_ticket_for(real_repo),
                                          repo_root=str(real_repo), executor=probe))
        assert report.ok is False
        assert report.apply_error

    def test_empty_adjacent_set_does_not_block(self, real_repo, monkeypatch):
        """邻接回归为空集 → **不算失败**，但如实标注原因"""
        from repair_fixtures import StubProbeExecutor
        probe = StubProbeExecutor(script=[
            {"exit_code": 1, "stdout": "1 failed"},
            {"exit_code": 0, "stdout": "1 passed"},
        ])
        monkeypatch.setattr(V, "run_anchor", lambda **kw: (True, {"total": 20,
                                                                  "failures": 0}))
        ticket = _ticket_for(real_repo)
        ticket.adjacent_tests = []
        report = V.verify(V.VerifyRequest(patch=make_patch(simple_diff()), ticket=ticket,
                                          repo_root=str(real_repo), executor=probe))
        assert report.ok is True
        assert report.adjacent.skip_reason

    def test_real_repo_not_modified(self, real_repo, monkeypatch):
        """**核心隔离断言**：验证后真实仓库根 git status 无漂移"""
        from repair_fixtures import StubProbeExecutor
        before = subprocess.run(["git", "status", "--porcelain"], cwd=str(real_repo),
                                capture_output=True, text=True).stdout
        before_hash = __import__("hashlib").sha256(
            (real_repo / "agent" / "demo_math.py").read_bytes()).hexdigest()
        probe = StubProbeExecutor(script=[
            {"exit_code": 1, "stdout": "1 failed"},
            {"exit_code": 0, "stdout": "1 passed"},
            {"exit_code": 0, "stdout": "1 passed"},
        ])
        monkeypatch.setattr(V, "run_anchor", lambda **kw: (True, {"total": 20,
                                                                  "failures": 0}))
        report = V.verify(V.VerifyRequest(patch=make_patch(simple_diff()),
                                          ticket=_ticket_for(real_repo),
                                          repo_root=str(real_repo), executor=probe))
        after = subprocess.run(["git", "status", "--porcelain"], cwd=str(real_repo),
                               capture_output=True, text=True).stdout
        after_hash = __import__("hashlib").sha256(
            (real_repo / "agent" / "demo_math.py").read_bytes()).hexdigest()
        assert before == after, "真实仓库出现漂移（隔离被破坏）"
        assert before_hash == after_hash
        assert report.temp_root and not os.path.exists(report.temp_root), \
            "临时副本应已清理"

    def test_probe_runs_inside_copy_not_real_repo(self, real_repo, monkeypatch):
        """三关的 cwd 必须是**临时副本**，不是真实仓库根"""
        from repair_fixtures import StubProbeExecutor
        probe = StubProbeExecutor(script=[
            {"exit_code": 1, "stdout": "1 failed"},
            {"exit_code": 0, "stdout": "1 passed"},
            {"exit_code": 0, "stdout": "1 passed"},
        ])
        monkeypatch.setattr(V, "run_anchor", lambda **kw: (True, {"total": 20,
                                                                  "failures": 0}))
        V.verify(V.VerifyRequest(patch=make_patch(simple_diff()),
                                 ticket=_ticket_for(real_repo),
                                 repo_root=str(real_repo), executor=probe))
        assert probe.calls, "应至少有一次探针调用"
        for call in probe.calls:
            assert os.path.normcase(call["cwd"]) != os.path.normcase(str(real_repo))
            assert "cp-repair-" in call["cwd"]

    def test_verify_records_audit_step(self, real_repo, monkeypatch, run_logger):
        from repair_fixtures import StubProbeExecutor
        probe = StubProbeExecutor(script=[
            {"exit_code": 1, "stdout": "1 failed"},
            {"exit_code": 0, "stdout": "1 passed"},
            {"exit_code": 0, "stdout": "1 passed"},
        ])
        monkeypatch.setattr(V, "run_anchor", lambda **kw: (True, {"total": 20,
                                                                  "failures": 0}))
        report = V.verify(V.VerifyRequest(patch=make_patch(simple_diff()),
                                          ticket=_ticket_for(real_repo),
                                          repo_root=str(real_repo), executor=probe,
                                          run_logger=run_logger))
        assert report.ok is True
        entries = [e for e in run_logger.trail() if e.step == "verify"]
        assert entries and entries[0].status == "ok"
        assert entries[0].detail.get("copy_cleaned") is True

    def test_verify_failure_records_discard_reason(self, real_repo, monkeypatch,
                                                   run_logger):
        from repair_fixtures import StubProbeExecutor
        probe = StubProbeExecutor(script=[
            {"exit_code": 1, "stdout": "1 failed"},
            {"exit_code": 1, "stdout": "1 failed"},
            {"exit_code": 0, "stdout": "1 passed"},
        ])
        monkeypatch.setattr(V, "run_anchor", lambda **kw: (True, {"total": 20,
                                                                  "failures": 0}))
        V.verify(V.VerifyRequest(patch=make_patch(simple_diff()),
                                 ticket=_ticket_for(real_repo), repo_root=str(real_repo),
                                 executor=probe, run_logger=run_logger))
        entries = [e for e in run_logger.trail() if e.step == "verify"]
        assert entries[0].status == "error"
        assert "丢弃" in entries[0].detail.get("error", "") or \
            "丢弃" in str(entries[0].detail)
