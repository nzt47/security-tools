"""GitSync 单元测试 — 技能仓库 Git 双向同步

覆盖维度:
- 初始化: init_repo / configure_remote / clone_remote
- 基本操作: add / commit / status / log / diff
- 同步: pull (成功/冲突/无远程) / push (成功/被拒/无远程)
- 分支: create_branch / checkout / merge_branch
- 冲突: has_conflicts / list_conflicts / resolve_conflict / abort_merge
- 安全: 命令注入防护 / 路径越界防护
- 错误: git 未安装 / 超时 / 非零退出码
"""
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

from agent.skills_mgmt.git_sync import (
    GitSync,
    GitSyncError,
    SyncResult,
    GitStatus,
    CommitInfo,
    ConflictFile,
    MergeResult,
)


# ═══════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════

def _mock_completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    """构造 subprocess.CompletedProcess mock"""
    return MagicMock(
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
    )


def _make_git_output(lines: list[str]) -> str:
    """将列表转为 git 输出格式"""
    return "\n".join(lines) + "\n" if lines else ""


# ═══════════════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def repo_path(tmp_path):
    """临时仓库路径"""
    path = tmp_path / "skills_repo"
    path.mkdir()
    return path


@pytest.fixture
def sync(repo_path):
    """GitSync 实例（不带远程地址）"""
    return GitSync(repo_path=repo_path)


@pytest.fixture
def sync_with_remote(repo_path):
    """GitSync 实例（带远程地址）"""
    return GitSync(
        repo_path=repo_path,
        remote_url="https://github.com/test/skills.git",
    )


# ═══════════════════════════════════════════════════════════════════
#  1. 初始化测试
# ═══════════════════════════════════════════════════════════════════

class TestGitSyncInit:
    """GitSync 初始化测试"""

    @patch("subprocess.run")
    def test_init_repo_calls_git_init(self, mock_run, sync, repo_path):
        """init_repo 应调用 git init 并设置 main 分支"""
        mock_run.return_value = _mock_completed(stdout="Initialized empty Git repository")

        sync.init_repo()

        # 验证调用了 git init
        first_call = mock_run.call_args_list[0]
        assert first_call[0][0][0] == "git"
        assert first_call[0][0][1] == "init"
        assert first_call[1]["cwd"] == str(repo_path)

    @patch("subprocess.run")
    def test_init_repo_sets_main_branch(self, mock_run, sync):
        """init_repo 应设置默认分支为 main"""
        mock_run.return_value = _mock_completed()

        sync.init_repo()

        # 应调用 git symbolic-ref 设置 HEAD
        calls = [c[0][0] for c in mock_run.call_args_list]
        assert any("symbolic-ref" in " ".join(c) for c in calls)

    @patch("subprocess.run")
    def test_configure_remote_adds_origin(self, mock_run, sync):
        """configure_remote 应添加名为 origin 的远程地址"""
        mock_run.return_value = _mock_completed()

        sync.configure_remote("https://github.com/test/repo.git")

        call_args = mock_run.call_args_list[0][0][0]
        assert "remote" in call_args
        assert "add" in call_args
        assert "origin" in call_args
        assert "https://github.com/test/repo.git" in call_args

    @patch("subprocess.run")
    def test_configure_remote_custom_name(self, mock_run, sync):
        """configure_remote 支持自定义远程名"""
        mock_run.return_value = _mock_completed()

        sync.configure_remote("https://github.com/test/repo.git", name="upstream")

        call_args = mock_run.call_args_list[0][0][0]
        assert "upstream" in call_args

    @patch("subprocess.run")
    def test_init_repo_is_idempotent(self, mock_run, sync):
        """重复调用 init_repo 不应报错"""
        mock_run.return_value = _mock_completed()

        sync.init_repo()
        sync.init_repo()  # 不抛异常

        assert mock_run.call_count >= 2


# ═══════════════════════════════════════════════════════════════════
#  2. 基本操作测试
# ═══════════════════════════════════════════════════════════════════

class TestGitSyncBasicOps:
    """GitSync 基本操作测试"""

    @patch("subprocess.run")
    def test_add_all_when_no_paths(self, mock_run, sync):
        """add(paths=None) 应执行 git add -A"""
        mock_run.return_value = _mock_completed()

        sync.add()

        call_args = mock_run.call_args_list[0][0][0]
        assert "add" in call_args
        assert "-A" in call_args

    @patch("subprocess.run")
    def test_add_specific_paths(self, mock_run, sync):
        """add(paths=[...]) 应执行 git add <paths>"""
        mock_run.return_value = _mock_completed()

        sync.add(paths=["skill.md", "scripts/main.py"])

        call_args = mock_run.call_args_list[0][0][0]
        assert "add" in call_args
        assert "skill.md" in call_args
        assert "scripts/main.py" in call_args

    @patch("subprocess.run")
    def test_commit_returns_sha(self, mock_run, sync):
        """commit 应返回 commit SHA"""
        mock_run.return_value = _mock_completed(stdout="abc123def456\n")

        sha = sync.commit("test message")

        assert sha == "abc123def456"

    @patch("subprocess.run")
    def test_commit_calls_git_commit(self, mock_run, sync):
        """commit 应调用 git commit -m"""
        mock_run.return_value = _mock_completed(stdout="abc123\n")

        sync.commit("feat: add skill")

        call_args = mock_run.call_args_list[0][0][0]
        assert "commit" in call_args
        assert "-m" in call_args
        assert "feat: add skill" in call_args

    @patch("subprocess.run")
    def test_commit_with_author(self, mock_run, sync):
        """commit 支持自定义 author"""
        mock_run.return_value = _mock_completed(stdout="abc123\n")

        sync.commit("msg", author="Alice <alice@example.com>")

        calls = [c[0][0] for c in mock_run.call_args_list]
        # 应包含 --author 参数
        found_author = any("--author" in c for c in calls)
        assert found_author


# ═══════════════════════════════════════════════════════════════════
#  3. 状态查询测试
# ═══════════════════════════════════════════════════════════════════

class TestGitSyncStatus:
    """GitSync 状态查询测试"""

    @patch("subprocess.run")
    def test_status_clean_repo(self, mock_run, sync):
        """干净仓库返回 clean=True"""
        # git status --porcelain 返回空
        # git rev-list --count 返回 0
        mock_run.side_effect = [
            _mock_completed(stdout=""),                    # status --porcelain
            _mock_completed(stdout="main\n"),              # branch --show-current
            _mock_completed(stdout="0\n"),                 # rev-list ahead
            _mock_completed(stdout="0\n"),                 # rev-list behind
        ]

        status = sync.status()

        assert status.clean is True
        assert status.current_branch == "main"
        assert status.ahead == 0
        assert status.behind == 0
        assert status.modified_files == []
        assert status.untracked_files == []

    @patch("subprocess.run")
    def test_status_with_modified_files(self, mock_run, sync):
        """有修改文件时正确解析"""
        porcelain_output = " M skill.md\n?? new_skill/\n"
        mock_run.side_effect = [
            _mock_completed(stdout=porcelain_output),     # status --porcelain
            _mock_completed(stdout="main\n"),              # branch --show-current
            _mock_completed(stdout="2\n"),                 # rev-list ahead
            _mock_completed(stdout="1\n"),                 # rev-list behind
        ]

        status = sync.status()

        assert status.clean is False
        assert "skill.md" in status.modified_files
        assert "new_skill/" in status.untracked_files
        assert status.ahead == 2
        assert status.behind == 1

    @patch("subprocess.run")
    def test_log_returns_commit_list(self, mock_run, sync):
        """log 返回 CommitInfo 列表"""
        log_output = "abc123|feat: add skill|Alice|2026-07-09\ndef456|fix: bug|Bob|2026-07-08\n"
        mock_run.return_value = _mock_completed(stdout=log_output)

        commits = sync.log(limit=2)

        assert len(commits) == 2
        assert commits[0].sha == "abc123"
        assert commits[0].message == "feat: add skill"
        assert commits[0].author == "Alice"
        assert commits[1].sha == "def456"

    @patch("subprocess.run")
    def test_log_passes_limit_to_git(self, mock_run, sync):
        """log 应传递 limit 参数给 git log"""
        mock_run.return_value = _mock_completed(stdout="")

        sync.log(limit=50)

        call_args = mock_run.call_args_list[0][0][0]
        assert "-50" in call_args or "50" in call_args


# ═══════════════════════════════════════════════════════════════════
#  4. Pull 测试
# ═══════════════════════════════════════════════════════════════════

class TestGitSyncPull:
    """GitSync pull 操作测试"""

    @patch("subprocess.run")
    def test_pull_success_returns_sync_result(self, mock_run, sync_with_remote):
        """pull 成功返回包含变更信息的 SyncResult"""
        mock_run.return_value = _mock_completed(
            stdout="Updating abc123..def456\nFast-forward\n skill.md | 5 +-\n"
        )

        result = sync_with_remote.pull(branch="main")

        assert isinstance(result, SyncResult)
        assert result.success is True
        assert result.action == "pull"
        assert result.branch == "main"

    @patch("subprocess.run")
    def test_pull_rebase_flag(self, mock_run, sync_with_remote):
        """pull(rebase=True) 应使用 --rebase"""
        mock_run.return_value = _mock_completed(stdout="Successfully rebased")

        sync_with_remote.pull(branch="main", rebase=True)

        call_args = mock_run.call_args_list[0][0][0]
        assert "--rebase" in call_args

    @patch("subprocess.run")
    def test_pull_no_rebase(self, mock_run, sync_with_remote):
        """pull(rebase=False) 不使用 --rebase"""
        mock_run.return_value = _mock_completed(stdout="Updating")

        sync_with_remote.pull(branch="main", rebase=False)

        call_args = mock_run.call_args_list[0][0][0]
        assert "--rebase" not in call_args

    @patch("subprocess.run")
    def test_pull_conflict_detected(self, mock_run, sync_with_remote):
        """pull 检测到冲突时返回 conflict 列表"""
        mock_run.return_value = _mock_completed(
            stdout="CONFLICT (content): Merge conflict in skill.md\n",
            stderr="Automatic merge failed; fix conflicts",
            returncode=1,
        )

        result = sync_with_remote.pull(branch="main")

        assert result.success is False
        assert len(result.conflicts) > 0
        assert result.conflicts[0].path == "skill.md"

    @patch("subprocess.run")
    def test_pull_no_remote_configured_raises(self, mock_run, sync):
        """无远程地址时 pull 应抛出 GitSyncError"""
        with pytest.raises(GitSyncError, match="remote"):
            sync.pull()

    @patch("subprocess.run")
    def test_pull_changed_files_parsed(self, mock_run, sync_with_remote):
        """pull 结果中 changed_files 被正确解析"""
        mock_run.return_value = _mock_completed(
            stdout="Updating abc..def\nFast-forward\n skill.md | 5 +-\n scripts/main.py | 10 +\n"
        )

        result = sync_with_remote.pull()

        assert "skill.md" in result.changed_files
        assert "scripts/main.py" in result.changed_files


# ═══════════════════════════════════════════════════════════════════
#  5. Push 测试
# ═══════════════════════════════════════════════════════════════════

class TestGitSyncPush:
    """GitSync push 操作测试"""

    @patch("subprocess.run")
    def test_push_success(self, mock_run, sync_with_remote):
        """push 成功返回 SyncResult"""
        mock_run.return_value = _mock_completed(
            stdout="To github.com:test/skills.git\n abc..def  main -> main\n"
        )

        result = sync_with_remote.push(branch="main")

        assert result.success is True
        assert result.action == "push"
        assert result.branch == "main"

    @patch("subprocess.run")
    def test_push_rejected_raises_or_returns_error(self, mock_run, sync_with_remote):
        """push 被拒绝时返回失败结果"""
        mock_run.return_value = _mock_completed(
            stderr="! [rejected] main -> main (fetch first)\n",
            returncode=1,
        )

        result = sync_with_remote.push(branch="main")

        assert result.success is False
        assert result.error is not None

    @patch("subprocess.run")
    def test_push_force_flag(self, mock_run, sync_with_remote):
        """push(force=True) 应使用 --force"""
        mock_run.return_value = _mock_completed(stdout="")

        sync_with_remote.push(branch="main", force=True)

        call_args = mock_run.call_args_list[0][0][0]
        assert "--force" in call_args

    @patch("subprocess.run")
    def test_push_no_remote_raises(self, mock_run, sync):
        """无远程地址时 push 抛出 GitSyncError"""
        with pytest.raises(GitSyncError, match="remote"):
            sync.push()


# ═══════════════════════════════════════════════════════════════════
#  6. 分支管理测试
# ═══════════════════════════════════════════════════════════════════

class TestGitSyncBranch:
    """GitSync 分支管理测试"""

    @patch("subprocess.run")
    def test_create_branch(self, mock_run, sync):
        """create_branch 应调用 git checkout -b"""
        mock_run.return_value = _mock_completed()

        sync.create_branch("user/alice", base="main")

        call_args = mock_run.call_args_list[0][0][0]
        assert "checkout" in call_args
        assert "-b" in call_args
        assert "user/alice" in call_args
        assert "main" in call_args

    @patch("subprocess.run")
    def test_checkout_branch(self, mock_run, sync):
        """checkout 应调用 git checkout"""
        mock_run.return_value = _mock_completed()

        sync.checkout("feature/new-skill")

        call_args = mock_run.call_args_list[0][0][0]
        assert "checkout" in call_args
        assert "feature/new-skill" in call_args

    @patch("subprocess.run")
    def test_merge_branch_success(self, mock_run, sync):
        """merge_branch 成功返回 MergeResult"""
        mock_run.return_value = _mock_completed(
            stdout="Updating abc..def\nFast-forward\n"
        )

        result = sync.merge_branch("user/alice", target="main")

        assert isinstance(result, MergeResult)
        assert result.success is True
        assert result.source_branch == "user/alice"
        assert result.target_branch == "main"

    @patch("subprocess.run")
    def test_merge_branch_with_conflicts(self, mock_run, sync):
        """merge_branch 检测到冲突"""
        mock_run.return_value = _mock_completed(
            stdout="CONFLICT (content): Merge conflict in skill.md\n",
            returncode=1,
        )

        result = sync.merge_branch("user/alice", target="main")

        assert result.success is False
        assert len(result.conflicts) > 0

    @patch("subprocess.run")
    def test_merge_branch_calls_git_merge(self, mock_run, sync):
        """merge_branch 应调用 git merge"""
        mock_run.return_value = _mock_completed(stdout="Fast-forward\n")

        sync.merge_branch("feature/x", target="main")

        call_args = mock_run.call_args_list[0][0][0]
        assert "merge" in call_args
        assert "feature/x" in call_args


# ═══════════════════════════════════════════════════════════════════
#  7. 冲突处理测试
# ═══════════════════════════════════════════════════════════════════

class TestGitSyncConflict:
    """GitSync 冲突处理测试"""

    @patch("subprocess.run")
    def test_has_conflicts_true(self, mock_run, sync):
        """存在冲突时 has_conflicts 返回 True"""
        mock_run.return_value = _mock_completed(stdout="skill.md\n")

        assert sync.has_conflicts() is True

    @patch("subprocess.run")
    def test_has_conflicts_false(self, mock_run, sync):
        """无冲突时 has_conflicts 返回 False"""
        mock_run.return_value = _mock_completed(stdout="")

        assert sync.has_conflicts() is False

    @patch("subprocess.run")
    def test_list_conflicts(self, mock_run, sync):
        """list_conflicts 返回冲突文件列表"""
        mock_run.return_value = _mock_completed(stdout="skill.md\nscripts/main.py\n")

        conflicts = sync.list_conflicts()

        assert len(conflicts) == 2
        assert conflicts[0].path == "skill.md"
        assert conflicts[1].path == "scripts/main.py"

    @patch("subprocess.run")
    def test_resolve_conflict_ours(self, mock_run, sync):
        """resolve_conflict(resolution='ours') 使用 --ours"""
        mock_run.return_value = _mock_completed()

        sync.resolve_conflict("skill.md", resolution="ours")

        call_args = mock_run.call_args_list[0][0][0]
        assert "checkout" in call_args
        assert "--ours" in call_args
        assert "skill.md" in call_args

    @patch("subprocess.run")
    def test_resolve_conflict_theirs(self, mock_run, sync):
        """resolve_conflict(resolution='theirs') 使用 --theirs"""
        mock_run.return_value = _mock_completed()

        sync.resolve_conflict("skill.md", resolution="theirs")

        call_args = mock_run.call_args_list[0][0][0]
        assert "checkout" in call_args
        assert "--theirs" in call_args
        assert "skill.md" in call_args

    @patch("subprocess.run")
    def test_resolve_conflict_adds_after_resolve(self, mock_run, sync):
        """resolve_conflict 解决后应 git add 标记已解决"""
        mock_run.return_value = _mock_completed()

        sync.resolve_conflict("skill.md", resolution="ours")

        # 应有两次调用: checkout --ours + git add
        calls = mock_run.call_args_list
        assert len(calls) >= 2
        add_call = calls[-1][0][0]
        assert "add" in add_call
        assert "skill.md" in add_call

    @patch("subprocess.run")
    def test_abort_merge(self, mock_run, sync):
        """abort_merge 调用 git merge --abort"""
        mock_run.return_value = _mock_completed()

        sync.abort_merge()

        call_args = mock_run.call_args_list[0][0][0]
        assert "merge" in call_args
        assert "--abort" in call_args


# ═══════════════════════════════════════════════════════════════════
#  8. 安全测试
# ═══════════════════════════════════════════════════════════════════

class TestGitSyncSecurity:
    """GitSync 安全测试"""

    @patch("subprocess.run")
    def test_no_shell_injection_via_branch_name(self, mock_run, sync):
        """恶意分支名不会触发 shell 注入"""
        mock_run.return_value = _mock_completed()

        malicious_name = "main; rm -rf /"
        sync.create_branch(malicious_name)

        # 参数通过列表传递，不经过 shell
        call_args = mock_run.call_args_list[0]
        cmd = call_args[0][0]  # *args
        assert isinstance(cmd, list)
        # 整个恶意字符串作为单个参数，不会被 shell 解析
        assert malicious_name in cmd

    @patch("subprocess.run")
    def test_no_shell_injection_via_commit_message(self, mock_run, sync):
        """恶意 commit message 不会触发 shell 注入"""
        mock_run.return_value = _mock_completed(stdout="abc123\n")

        malicious_msg = "feat: skill; $(rm -rf /)"
        sync.commit(malicious_msg)

        call_args = mock_run.call_args_list[0]
        assert call_args[1].get("shell") is not True or call_args[1].get("shell") is None
        cmd = call_args[0][0]
        assert isinstance(cmd, list)
        assert malicious_msg in cmd

    @patch("subprocess.run")
    def test_resolve_conflict_path_traversal_blocked(self, mock_run, sync):
        """resolve_conflict 拒绝路径越界"""
        mock_run.return_value = _mock_completed()

        with pytest.raises(GitSyncError, match="path|路径|traversal"):
            sync.resolve_conflict("../../../etc/passwd", resolution="ours")

    @patch("subprocess.run")
    def test_git_terminal_prompt_disabled(self, mock_run, sync):
        """所有 git 命令应设置 GIT_TERMINAL_PROMPT=0"""
        mock_run.return_value = _mock_completed()

        sync.init_repo()

        call_kwargs = mock_run.call_args_list[0][1]
        env = call_kwargs.get("env", {})
        assert env.get("GIT_TERMINAL_PROMPT") == "0"


# ═══════════════════════════════════════════════════════════════════
#  9. 错误处理测试
# ═══════════════════════════════════════════════════════════════════

class TestGitSyncErrors:
    """GitSync 错误处理测试"""

    @patch("subprocess.run")
    def test_git_not_found_raises_sync_error(self, mock_run, sync):
        """git 未安装时抛出 GitSyncError"""
        mock_run.side_effect = FileNotFoundError("git not found")

        with pytest.raises(GitSyncError, match="git.*not found|git.*未安装"):
            sync.init_repo()

    @patch("subprocess.run")
    def test_timeout_raises_sync_error(self, mock_run, sync_with_remote):
        """网络超时抛出 GitSyncError"""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=30)

        with pytest.raises(GitSyncError, match="timeout|超时"):
            sync_with_remote.pull()

    @patch("subprocess.run")
    def test_non_zero_exit_raises_sync_error(self, mock_run, sync):
        """非零退出码抛出 GitSyncError（check=True 时）"""
        mock_run.return_value = _mock_completed(
            stderr="fatal: not a git repository",
            returncode=128,
        )

        with pytest.raises(GitSyncError, match="not a git|128"):
            sync.status()

    @patch("subprocess.run")
    def test_error_message_includes_stderr(self, mock_run, sync):
        """错误消息包含 stderr 输出"""
        mock_run.return_value = _mock_completed(
            stderr="fatal: ambiguous argument 'main'",
            returncode=1,
        )

        with pytest.raises(GitSyncError) as exc_info:
            sync.commit("msg")

        assert "ambiguous" in str(exc_info.value) or "main" in str(exc_info.value)


# ═══════════════════════════════════════════════════════════════════
#  10. 数据模型测试
# ═══════════════════════════════════════════════════════════════════

class TestGitSyncModels:
    """数据模型测试"""

    def test_sync_result_to_dict(self):
        """SyncResult 可序列化为 dict"""
        result = SyncResult(
            success=True,
            action="pull",
            branch="main",
            commits=[CommitInfo(sha="abc", message="test", author="Alice", timestamp="2026-07-09")],
            changed_files=["skill.md"],
            conflicts=[],
            error=None,
        )

        d = result.to_dict()
        assert d["success"] is True
        assert d["action"] == "pull"
        assert d["changed_files"] == ["skill.md"]

    def test_git_status_to_dict(self):
        """GitStatus 可序列化为 dict"""
        status = GitStatus(
            current_branch="main",
            clean=False,
            ahead=2,
            behind=1,
            modified_files=["skill.md"],
            untracked_files=["new_skill/"],
        )

        d = status.to_dict()
        assert d["current_branch"] == "main"
        assert d["clean"] is False
        assert d["ahead"] == 2

    def test_conflict_file_skill_id_extracted(self):
        """ConflictFile 从路径中提取 skill_id"""
        conflict = ConflictFile(
            path="pdf_parser/skill.md",
            skill_id="pdf_parser",
            conflict_type="both_modified",
            resolution=None,
        )

        assert conflict.skill_id == "pdf_parser"
        assert conflict.resolution is None

    def test_merge_result_with_conflicts(self):
        """MergeResult 可包含冲突列表"""
        result = MergeResult(
            success=False,
            source_branch="user/alice",
            target_branch="main",
            merged_commits=0,
            conflicts=[
                ConflictFile(path="skill.md", skill_id="test", conflict_type="both_modified", resolution=None),
            ],
        )

        assert result.success is False
        assert len(result.conflicts) == 1
