"""P0 workflow 重建脚本的单元测试

覆盖核心逻辑：
- backup_old_workflow: 备份旧 workflow 文件
- create_new_workflow: 创建新 workflow 文件
- remove_old_workflow: 删除旧 workflow 文件
- commit_and_push: 提交并推送
- check_prerequisites: 前置条件检查
- analyze_result: 运行结果分析

测试策略：
- 文件系统操作：使用 tmp_path 隔离，monkey-patch REPO_ROOT
- Git 命令：mock run_git 避免实际执行
- GitHub API：mock make_request 避免网络调用
"""
import sys
import os
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import rebuild_p0_workflow as rpw


OLD_WORKFLOW_CONTENT = """name: P0 安全验证
on:
  push:
    branches:
      - main
jobs:
  p0-security-tests:
    name: P0 Security Regression Test
    runs-on: ubuntu-22.04
    steps:
      - run: echo "test"
"""


@pytest.fixture
def isolated_repo(tmp_path, monkeypatch):
    """创建隔离的临时仓库环境"""
    monkeypatch.setattr(rpw, "REPO_ROOT", tmp_path)

    old_path = tmp_path / rpw.OLD_WORKFLOW_PATH
    old_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.write_text(OLD_WORKFLOW_CONTENT, encoding="utf-8")

    return tmp_path


# ============================================================================
# backup_old_workflow 测试
# ============================================================================


class TestBackupOldWorkflow:
    """备份旧 workflow 文件"""

    def test_backup_creates_file_with_correct_content(self, isolated_repo):
        """实际模式：创建备份文件，内容与原文件一致"""
        backup_path = rpw.backup_old_workflow(dry_run=False)

        assert backup_path.exists()
        assert backup_path.read_text(encoding="utf-8") == OLD_WORKFLOW_CONTENT
        assert backup_path.parent == isolated_repo / rpw.ARCHIVE_DIR
        assert backup_path.name.startswith("p0-security.yml.backup_")

    def test_backup_filename_contains_timestamp(self, isolated_repo):
        """备份文件名包含时间戳，格式 YYYYMMDD_HHMMSS"""
        from datetime import datetime

        before = datetime.now().strftime("%Y%m%d_%H")
        backup_path = rpw.backup_old_workflow(dry_run=False)
        after = datetime.now().strftime("%Y%m%d_%H")

        # 文件名应包含当前小时（YYYYMMDD_HH 格式）
        assert before in backup_path.name or after in backup_path.name

    def test_backup_creates_archive_dir_if_not_exists(self, isolated_repo):
        """归档目录不存在时自动创建"""
        archive_dir = isolated_repo / rpw.ARCHIVE_DIR
        assert not archive_dir.exists()

        rpw.backup_old_workflow(dry_run=False)

        assert archive_dir.exists()
        assert archive_dir.is_dir()

    def test_backup_dry_run_creates_no_file(self, isolated_repo):
        """dry-run 模式：不创建备份文件"""
        backup_path = rpw.backup_old_workflow(dry_run=True)

        assert not backup_path.exists()
        assert not (isolated_repo / rpw.ARCHIVE_DIR).exists()

    def test_backup_dry_run_returns_path_without_creating(self, isolated_repo):
        """dry-run 模式：返回路径但不创建文件"""
        backup_path = rpw.backup_old_workflow(dry_run=True)

        assert "p0-security.yml.backup_" in backup_path.name
        assert not backup_path.exists()

    def test_multiple_backups_do_not_overwrite(self, isolated_repo):
        """多次备份不会覆盖（时间戳不同）"""
        import time

        first = rpw.backup_old_workflow(dry_run=False)
        time.sleep(1.1)
        second = rpw.backup_old_workflow(dry_run=False)

        assert first != second
        assert first.exists()
        assert second.exists()


# ============================================================================
# create_new_workflow 测试
# ============================================================================


class TestCreateNewWorkflow:
    """创建新 workflow 文件"""

    def test_create_new_file_with_same_content(self, isolated_repo):
        """实际模式：创建新文件，内容与旧文件完全相同"""
        rpw.create_new_workflow(dry_run=False)

        new_path = isolated_repo / rpw.NEW_WORKFLOW_PATH
        assert new_path.exists()
        assert new_path.read_text(encoding="utf-8") == OLD_WORKFLOW_CONTENT

    def test_new_file_path_is_different_from_old(self, isolated_repo):
        """新文件路径与旧文件不同"""
        rpw.create_new_workflow(dry_run=False)

        old_path = isolated_repo / rpw.OLD_WORKFLOW_PATH
        new_path = isolated_repo / rpw.NEW_WORKFLOW_PATH

        assert old_path != new_path
        assert old_path.exists()  # 旧文件仍存在（删除在单独步骤）
        assert new_path.exists()

    def test_create_dry_run_creates_no_file(self, isolated_repo):
        """dry-run 模式：不创建新文件"""
        rpw.create_new_workflow(dry_run=True)

        new_path = isolated_repo / rpw.NEW_WORKFLOW_PATH
        assert not new_path.exists()

    def test_new_file_uses_unix_line_endings(self, isolated_repo):
        """新文件使用 Unix 换行符（newline='\\n'）"""
        rpw.create_new_workflow(dry_run=False)

        new_path = isolated_repo / rpw.NEW_WORKFLOW_PATH
        content = new_path.read_bytes()
        assert b"\r\n" not in content


# ============================================================================
# remove_old_workflow 测试
# ============================================================================


class TestRemoveOldWorkflow:
    """删除旧 workflow 文件"""

    def test_remove_dry_run_does_not_call_git(self, isolated_repo):
        """dry-run 模式：不执行 git rm"""
        with patch.object(rpw, "run_git") as mock_git:
            rpw.remove_old_workflow(dry_run=True)

            mock_git.assert_not_called()

    def test_remove_actual_calls_git_rm(self, isolated_repo):
        """实际模式：执行 git rm 删除旧文件"""
        with patch.object(rpw, "run_git") as mock_git:
            mock_git.return_value = MagicMock(returncode=0)

            rpw.remove_old_workflow(dry_run=False)

            mock_git.assert_called_once_with(["rm", rpw.OLD_WORKFLOW_PATH])


# ============================================================================
# commit_and_push 测试
# ============================================================================


class TestCommitAndPush:
    """提交并推送"""

    def test_commit_dry_run_does_not_call_git(self, isolated_repo):
        """dry-run 模式：不执行 git 命令"""
        with patch.object(rpw, "run_git") as mock_git:
            result = rpw.commit_and_push(dry_run=True)

            mock_git.assert_not_called()
            assert result is None

    def test_commit_actual_stages_new_file(self, isolated_repo):
        """实际模式：暂存新文件"""
        with patch.object(rpw, "run_git") as mock_git:
            mock_git.return_value = MagicMock(returncode=0, stdout="abc1234")

            rpw.commit_and_push(dry_run=False)

            # 第一次调用是 git add NEW_WORKFLOW_PATH
            first_call = mock_git.call_args_list[0]
            assert first_call[0][0] == ["add", rpw.NEW_WORKFLOW_PATH]

    def test_commit_actual_calls_commit_and_push(self, isolated_repo):
        """实际模式：执行 commit 和 push"""
        with patch.object(rpw, "run_git") as mock_git:
            mock_git.return_value = MagicMock(returncode=0, stdout="abc1234")

            rpw.commit_and_push(dry_run=False)

            call_args = [c[0][0] for c in mock_git.call_args_list]
            # 应包含 add, commit, push, rev-parse 四次调用
            assert call_args[0] == ["add", rpw.NEW_WORKFLOW_PATH]
            assert call_args[1][0] == "commit"
            assert call_args[2] == ["push", "origin", rpw.BRANCH]
            assert call_args[3] == ["rev-parse", "--short", "HEAD"]

    def test_commit_returns_short_sha(self, isolated_repo):
        """实际模式：返回 commit short SHA"""
        with patch.object(rpw, "run_git") as mock_git:
            mock_git.return_value = MagicMock(returncode=0, stdout="abc1234\n")

            sha = rpw.commit_and_push(dry_run=False)

            assert sha == "abc1234"

    def test_commit_fails_on_commit_error(self, isolated_repo):
        """commit 失败时退出"""
        with patch.object(rpw, "run_git") as mock_git:
            mock_git.return_value = MagicMock(returncode=1, stderr="commit failed")

            with pytest.raises(SystemExit):
                rpw.commit_and_push(dry_run=False)


# ============================================================================
# check_prerequisites 测试
# ============================================================================


class TestCheckPrerequisites:
    """前置条件检查"""

    def test_passes_when_all_conditions_met(self, isolated_repo):
        """所有条件满足时通过"""
        with patch.object(rpw, "run_git") as mock_git:
            mock_git.side_effect = [
                MagicMock(stdout="phase2-visibility-convergence\n"),
                MagicMock(stdout=""),
            ]

            rpw.check_prerequisites()  # 不应抛出异常

    def test_fails_on_wrong_branch(self, isolated_repo):
        """分支不正确时退出"""
        with patch.object(rpw, "run_git") as mock_git:
            mock_git.return_value = MagicMock(stdout="master\n")

            with pytest.raises(SystemExit):
                rpw.check_prerequisites()

    def test_fails_when_old_workflow_missing(self, isolated_repo, tmp_path):
        """旧 workflow 文件不存在时退出"""
        # 删除旧文件
        (tmp_path / rpw.OLD_WORKFLOW_PATH).unlink()

        with patch.object(rpw, "run_git") as mock_git:
            mock_git.side_effect = [
                MagicMock(stdout="phase2-visibility-convergence\n"),
                MagicMock(stdout=""),
            ]

            with pytest.raises(SystemExit):
                rpw.check_prerequisites()


# ============================================================================
# analyze_result 测试
# ============================================================================


class TestAnalyzeResult:
    """运行结果分析"""

    def test_all_success_returns_true(self, capsys):
        """所有 Job 成功时返回 True"""
        jobs = [
            {"name": "静态扫描", "conclusion": "success", "steps": []},
            {"name": "P0 安全回归测试", "conclusion": "success", "steps": []},
            {"name": "补丁完整性", "conclusion": "success", "steps": []},
        ]

        result = rpw.analyze_result(jobs)
        assert result is True

    def test_p0_failure_set_up_job_returns_false(self, capsys):
        """P0 回归测试 Set up job 失败时返回 False"""
        jobs = [
            {"name": "静态扫描", "conclusion": "success", "steps": []},
            {
                "name": "P0 安全回归测试",
                "conclusion": "failure",
                "steps": [
                    {"name": "Set up job", "conclusion": "failure"},
                    {"name": "Run tests", "conclusion": "skipped"},
                ],
            },
        ]

        result = rpw.analyze_result(jobs)
        assert result is False
        output = capsys.readouterr().out
        assert "Set up job" in output

    def test_p0_failure_other_step_returns_false(self, capsys):
        """P0 回归测试非 Set up job 失败时返回 False"""
        jobs = [
            {
                "name": "P0 Security Regression Test",
                "conclusion": "failure",
                "steps": [
                    {"name": "Set up job", "conclusion": "success"},
                    {"name": "Run tests", "conclusion": "failure"},
                ],
            },
        ]

        result = rpw.analyze_result(jobs)
        assert result is False
        output = capsys.readouterr().out
        assert "代码问题" in output

    def test_p0_success_but_other_fails_returns_true(self, capsys):
        """P0 回归测试成功但其他 Job 失败时返回 True（核心目标已达成）"""
        jobs = [
            {"name": "静态扫描", "conclusion": "failure", "steps": []},
            {
                "name": "P0 安全回归测试",
                "conclusion": "success",
                "steps": [],
            },
        ]

        result = rpw.analyze_result(jobs)
        assert result is True

    def test_none_jobs_returns_false(self, capsys):
        """jobs 为 None 时返回 False"""
        result = rpw.analyze_result(None)
        assert result is False

    def test_empty_jobs_returns_false(self, capsys):
        """空 jobs 列表视为无法获取状态，返回 False"""
        result = rpw.analyze_result([])
        assert result is False
        output = capsys.readouterr().out
        assert "无法获取" in output

    def test_p0_regression_identified_by_english_name(self, capsys):
        """P0 回归测试 Job 通过英文名识别"""
        jobs = [
            {
                "name": "P0 Security Regression Test",
                "conclusion": "failure",
                "steps": [
                    {"name": "Set up job", "conclusion": "failure"},
                ],
            },
        ]

        result = rpw.analyze_result(jobs)
        assert result is False
        output = capsys.readouterr().out
        assert "Set up job" in output


# ============================================================================
# dry-run 集成测试
# ============================================================================


class TestDryRunIntegration:
    """dry-run 模式集成测试：确保不产生任何副作用"""

    def test_dry_run_creates_no_files(self, isolated_repo):
        """dry-run 模式：备份和创建步骤不产生任何文件"""
        rpw.backup_old_workflow(dry_run=True)
        rpw.create_new_workflow(dry_run=True)

        # 不应创建备份文件
        archive_dir = isolated_repo / rpw.ARCHIVE_DIR
        assert not archive_dir.exists()

        # 不应创建新 workflow 文件
        new_path = isolated_repo / rpw.NEW_WORKFLOW_PATH
        assert not new_path.exists()

    def test_dry_run_does_not_modify_old_file(self, isolated_repo):
        """dry-run 模式：旧文件内容不被修改"""
        old_path = isolated_repo / rpw.OLD_WORKFLOW_PATH
        original_content = old_path.read_text(encoding="utf-8")

        rpw.backup_old_workflow(dry_run=True)
        rpw.create_new_workflow(dry_run=True)

        assert old_path.read_text(encoding="utf-8") == original_content

    def test_full_dry_run_flow_no_side_effects(self, isolated_repo):
        """完整 dry-run 流程：不产生任何文件系统副作用"""
        with patch.object(rpw, "run_git") as mock_git:
            mock_git.side_effect = [
                MagicMock(stdout="phase2-visibility-convergence\n"),
                MagicMock(stdout=""),
            ]

            rpw.check_prerequisites()
            rpw.backup_old_workflow(dry_run=True)
            rpw.create_new_workflow(dry_run=True)
            rpw.remove_old_workflow(dry_run=True)
            rpw.commit_and_push(dry_run=True)

        # git rm / commit / push 不应被调用
        git_commands = [c[0][0] for c in mock_git.call_args_list]
        for cmd in git_commands:
            assert cmd[0] not in ("rm", "commit", "push", "add"), \
                f"dry-run 不应执行 git {cmd[0]}"

        # 无新文件创建
        assert not (isolated_repo / rpw.NEW_WORKFLOW_PATH).exists()
        assert not (isolated_repo / rpw.ARCHIVE_DIR).exists()


# ============================================================================
# 实际模式集成测试（mock git）
# ============================================================================


class TestActualFlowIntegration:
    """实际模式集成测试（mock git 命令）"""

    def test_backup_then_create_preserves_old_file(self, isolated_repo):
        """备份后创建新文件，旧文件仍存在"""
        old_path = isolated_repo / rpw.OLD_WORKFLOW_PATH

        rpw.backup_old_workflow(dry_run=False)
        rpw.create_new_workflow(dry_run=False)

        assert old_path.exists()
        assert (isolated_repo / rpw.NEW_WORKFLOW_PATH).exists()

    def test_new_file_content_matches_old_after_create(self, isolated_repo):
        """创建的新文件内容与旧文件完全一致"""
        rpw.create_new_workflow(dry_run=False)

        old_content = (isolated_repo / rpw.OLD_WORKFLOW_PATH).read_text(encoding="utf-8")
        new_content = (isolated_repo / rpw.NEW_WORKFLOW_PATH).read_text(encoding="utf-8")

        assert old_content == new_content


# ============================================================================
# 回滚决策树测试（覆盖 9 个补全的失败场景）
# ============================================================================


class TestRollbackDecisionTree:
    """回滚决策树自动化测试

    覆盖 runbook 第 4.4 节决策树中补全的 9 个失败场景：
    - 脚本中途崩溃：Step 2/3/4-commit/4-push
    - 验证阶段超时：Step 5/6/7/API 错误
    - 结果歧义：P0 通过但其他 Job 失败
    """

    # --- 脚本中途崩溃场景 ---

    def test_step2_create_new_file_fails(self, isolated_repo):
        """场景：Step 2 创建新文件失败（如磁盘满）

        决策树：旧文件未被删除，新文件未创建 → 清理不完整新文件
        """
        with patch("builtins.open", side_effect=IOError("No space left on device")):
            with pytest.raises(IOError):
                rpw.create_new_workflow(dry_run=False)

        # 旧文件应仍存在（未被删除）
        assert (isolated_repo / rpw.OLD_WORKFLOW_PATH).exists()
        # 新文件不应存在（创建失败）
        assert not (isolated_repo / rpw.NEW_WORKFLOW_PATH).exists()

    def test_step3_git_rm_fails(self, isolated_repo):
        """场景：Step 3 git rm 失败（如文件有未提交修改）

        决策树：新文件已创建但未提交，旧文件未被删除 → 清理新文件
        """
        with patch.object(rpw, "run_git") as mock_git:
            mock_git.side_effect = subprocess.CalledProcessError(
                1, "git rm", stderr="error: the file has changes staged in the index"
            )

            with pytest.raises(subprocess.CalledProcessError):
                rpw.remove_old_workflow(dry_run=False)

        # 旧文件应仍存在（git rm 失败）
        assert (isolated_repo / rpw.OLD_WORKFLOW_PATH).exists()

    def test_step4_commit_fails(self, isolated_repo):
        """场景：Step 4 commit 失败（如 pre-commit hook 拒绝）

        决策树：旧文件已 git rm（暂存区），新文件已创建但未提交
        恢复：git reset HEAD + git checkout + rm 新文件
        """
        with patch.object(rpw, "run_git") as mock_git:
            mock_git.side_effect = [
                MagicMock(returncode=0),  # git add
                MagicMock(returncode=1, stderr="pre-commit hook failed"),  # git commit
            ]

            with pytest.raises(SystemExit):
                rpw.commit_and_push(dry_run=False)

            # 验证调用了 add 和 commit
            calls = [c[0][0] for c in mock_git.call_args_list]
            assert calls[0] == ["add", rpw.NEW_WORKFLOW_PATH]
            assert calls[1][0] == "commit"

    def test_step4_push_fails(self, isolated_repo):
        """场景：Step 4 push 失败（网络/权限问题）

        决策树：commit 已创建但未推送到远程
        恢复：git reset --soft HEAD~1 + git reset HEAD + git checkout + rm 新文件
        """
        with patch.object(rpw, "run_git") as mock_git:
            mock_git.side_effect = [
                MagicMock(returncode=0),  # git add
                MagicMock(returncode=0, stdout="abc1234"),  # git commit
                MagicMock(returncode=1, stderr="Failed to connect"),  # git push
            ]

            with pytest.raises(SystemExit):
                rpw.commit_and_push(dry_run=False)

            # 验证 push 被调用
            calls = [c[0][0] for c in mock_git.call_args_list]
            assert calls[2] == ["push", "origin", rpw.BRANCH]
            # rev-parse 不应被调用（push 失败后退出）
            assert len(mock_git.call_args_list) == 3

    # --- 验证阶段超时场景 ---

    def test_step5_workflow_not_registered_timeout(self):
        """场景：Step 5 超时 — 新 workflow 未出现在 GitHub Actions

        决策树：push 已成功但 GitHub 未注册 → 等待后重试或恢复旧文件
        """
        with patch.object(rpw, "make_request") as mock_req, \
             patch.object(rpw.time, "sleep") as mock_sleep:
            mock_req.return_value = (200, {"workflows": []}, None)

            result = rpw.wait_for_new_workflow(token="fake_token", max_wait=0.5)

            assert result is None
            mock_req.assert_called()

    def test_step6_first_run_not_triggered_timeout(self):
        """场景：Step 6 超时 — 首次运行未触发

        决策树：新 workflow 已注册但未自动触发 → 手动 workflow_dispatch
        """
        with patch.object(rpw, "make_request") as mock_req, \
             patch.object(rpw.time, "sleep") as mock_sleep:
            mock_req.return_value = (200, {"workflow_runs": []}, None)

            result = rpw.wait_for_first_run(
                token="fake_token", commit_sha="abc1234", max_wait=0.5
            )

            assert result is None
            mock_req.assert_called()

    def test_step7_run_not_completed_timeout(self):
        """场景：Step 7 超时 — 运行未完成（轮询超时）

        决策树：运行仍在进行中 → 手动查看 UI，无需回滚
        """
        with patch.object(rpw, "make_request") as mock_req, \
             patch.object(rpw.time, "sleep") as mock_sleep:
            # 所有 Job 仍 in_progress
            mock_req.return_value = (
                200,
                {"jobs": [{"name": "P0 Security Regression Test", "status": "in_progress", "conclusion": None}]},
                None,
            )

            result = rpw.poll_run(token="fake_token", run_id=123, max_poll=2, interval=0)

            assert result is None
            assert mock_req.call_count == 2

    def test_api_error_403(self):
        """场景：API 错误 — 403/401/限流

        决策树：Token 过期或权限不足 → 检查 ~/.git-credentials
        """
        with patch.object(rpw, "make_request") as mock_req, \
             patch.object(rpw.time, "sleep") as mock_sleep:
            mock_req.return_value = (403, None, "HTTPError 403: Forbidden")

            result = rpw.wait_for_new_workflow(token="expired_token", max_wait=0.5)

            # 应返回 None（超时未找到），不崩溃
            assert result is None
            mock_req.assert_called()

    def test_api_error_network_exception(self):
        """场景：API 错误 — 网络异常

        决策树：网络中断 → 手动在 GitHub UI 查看
        """
        with patch.object(rpw, "make_request") as mock_req, \
             patch.object(rpw.time, "sleep") as mock_sleep:
            mock_req.return_value = (None, None, "Exception: connection refused")

            result = rpw.wait_for_first_run(
                token="fake_token", commit_sha="abc1234", max_wait=0.5
            )

            # 应返回 None（超时），不崩溃
            assert result is None

    # --- 结果歧义场景 ---

    def test_p0_passes_but_other_fails_decision_tree(self, capsys):
        """场景：P0 回归测试通过，但其他 Job 失败

        决策树：平台故障已解决（核心目标达成），无需回滚 workflow 重建
        """
        jobs = [
            {"name": "静态扫描", "conclusion": "failure", "steps": []},
            {
                "name": "P0 Security Regression Test",
                "conclusion": "success",
                "steps": [],
            },
            {"name": "补丁完整性", "conclusion": "success", "steps": []},
        ]

        result = rpw.analyze_result(jobs)

        # 决策树：P0 通过 → 无需回滚
        assert result is True
        output = capsys.readouterr().out
        assert "P0 回归测试 Job 已通过" in output

    # --- 完全无法恢复场景 ---

    def test_completely_unrecoverable_no_jobs(self, capsys):
        """场景：完全无法恢复 — 无法获取 jobs 状态

        决策树：执行场景 C（git revert）→ 场景 D（手工重建）
        """
        result = rpw.analyze_result(None)

        assert result is False
        output = capsys.readouterr().out
        assert "无法获取" in output

    def test_p0_fails_non_setup_job_decision_tree(self, capsys):
        """场景：P0 回归测试非 Set up job 步骤失败

        决策树：新 workflow 可能有配置问题 → 执行场景 B（恢复旧文件）
        """
        jobs = [
            {
                "name": "P0 Security Regression Test",
                "conclusion": "failure",
                "steps": [
                    {"name": "Set up job", "conclusion": "success"},
                    {"name": "Run tests", "conclusion": "failure"},
                ],
            },
        ]

        result = rpw.analyze_result(jobs)

        assert result is False
        output = capsys.readouterr().out
        # 决策树指出：非 Set up job 失败 → 可能是代码问题
        assert "代码问题" in output


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
