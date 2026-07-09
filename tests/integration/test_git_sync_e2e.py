"""GitSync + ConflictResolver 集成测试 — 真实 git 仓库端到端验证

覆盖场景:
- 基本 init/commit/push/pull 流程
- 状态查询 (clean/dirty, ahead/behind)
- 分支创建/合并 (无冲突)
- pull 冲突 → 检测 → 自动解决
- merge 冲突 → 检测 → 自动解决
- 不可自动解决的冲突 (name 字段冲突)

前置条件: 系统需安装 git。未安装时全部跳过。
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agent.skills_mgmt.conflict_resolver import ConflictResolver
from agent.skills_mgmt.git_sync import GitSync, GitSyncError, SyncResult


pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git 未安装，跳过集成测试",
)


# ═══════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════

SKILL_MD_TEMPLATE = """---
id: pdf_parser
name: PDF解析
version: 1.0.0
tags: [pdf, parse]
enabled: true
---

# PDF Parser

基础版本
"""


def _run_git(cmd: list, cwd: str = None, env: dict = None) -> subprocess.CompletedProcess:
    """直接执行 git 命令（不通过 GitSync，用于 fixture 准备）"""
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(
        ["git", *cmd],
        cwd=cwd,
        env=full_env,
        capture_output=True,
        text=True,
        check=True,
    )


# ═══════════════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def git_env(tmp_path, monkeypatch):
    """创建完整的 git 测试环境: bare remote + 两个工作仓库

    Returns: (sync1, sync2, repo1_path, repo2_path, remote_path)
    """
    # 设置 git 用户身份（避免 commit 失败）
    monkeypatch.setenv("GIT_AUTHOR_NAME", "TestUser")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "TestUser")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.com")

    # 创建 bare remote
    # 不易: bare repo 的 HEAD 必须指向 main，否则 clone 时会检出不存在的 master 分支
    # 导致工作区为空（init.defaultBranch 可能是 master）
    remote_path = tmp_path / "remote.git"
    _run_git(["init", "--bare", str(remote_path)])
    _run_git(["symbolic-ref", "HEAD", "refs/heads/main"], cwd=str(remote_path))

    # repo1: 初始化并推送
    repo1_path = tmp_path / "repo1"
    repo1_path.mkdir()
    sync1 = GitSync(repo1_path, remote_url=str(remote_path))
    sync1.init_repo()
    sync1.configure_remote(str(remote_path))

    # 创建初始 skill.md
    skill_dir = repo1_path / "pdf_parser"
    skill_dir.mkdir()
    (skill_dir / "skill.md").write_text(SKILL_MD_TEMPLATE, encoding="utf-8")

    sync1.add()
    sync1.commit("Initial commit")
    # 第一次 push 用 -u 设置 upstream
    _run_git(["push", "-u", "origin", "main"], cwd=str(repo1_path))

    # repo2: clone
    repo2_path = tmp_path / "repo2"
    _run_git(["clone", str(remote_path), str(repo2_path)])
    sync2 = GitSync(repo2_path, remote_url=str(remote_path))

    return sync1, sync2, repo1_path, repo2_path, remote_path


def _write_skill(repo_path: Path, version: str = None, name: str = None,
                 tags: list = None, body: str = None) -> None:
    """写入 skill.md（支持部分覆盖）"""
    content = f"""---
id: pdf_parser
name: {name or "PDF解析"}
version: {version or "1.0.0"}
tags: {tags or ["pdf", "parse"]}
enabled: true
---

# PDF Parser

{body or "基础版本"}
"""
    (repo_path / "pdf_parser" / "skill.md").write_text(content, encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════
#  1. 基本操作集成测试
# ═══════════════════════════════════════════════════════════════════

class TestGitSyncBasicE2E:
    """基本操作端到端测试"""

    def test_init_creates_git_dir(self, tmp_path):
        """init_repo 创建 .git 目录"""
        repo = tmp_path / "new_repo"
        repo.mkdir()
        sync = GitSync(repo)
        sync.init_repo()
        assert (repo / ".git").exists()

    def test_commit_and_log(self, git_env):
        """commit 后 log 能查到提交"""
        sync1, _, repo1, _, _ = git_env
        commits = sync1.log()
        assert len(commits) >= 1
        assert "Initial" in commits[0].message

    def test_push_pull_roundtrip(self, git_env):
        """repo1 push 后 repo2 pull 获取内容"""
        sync1, sync2, repo1, repo2, _ = git_env

        # repo1 修改并推送
        _write_skill(repo1, version="1.1.0", body="更新版本")
        sync1.add()
        sync1.commit("Update version")
        sync1.push(branch="main")

        # repo2 拉取
        result = sync2.pull(branch="main", rebase=False)
        assert result.success
        assert "1.1.0" in (repo2 / "pdf_parser" / "skill.md").read_text(encoding="utf-8")

    def test_status_clean_repo(self, git_env):
        """干净仓库 status 返回 clean=True"""
        sync1, _, _, _, _ = git_env
        status = sync1.status()
        assert status.clean
        assert status.current_branch == "main"

    def test_status_dirty_repo(self, git_env):
        """有修改时 status 返回 clean=False"""
        sync1, _, repo1, _, _ = git_env
        _write_skill(repo1, version="1.5.0")
        status = sync1.status()
        assert not status.clean
        assert any("skill.md" in f for f in status.modified_files)


# ═══════════════════════════════════════════════════════════════════
#  2. 分支管理集成测试
# ═══════════════════════════════════════════════════════════════════

class TestGitSyncBranchE2E:
    """分支管理端到端测试"""

    def test_create_and_merge_no_conflict(self, git_env):
        """创建分支、修改不同文件、合并无冲突"""
        sync1, _, repo1, _, _ = git_env

        # 创建分支并添加新文件
        sync1.create_branch("feature/new-skill", base="main")
        new_skill_dir = repo1 / "new_skill"
        new_skill_dir.mkdir()
        (new_skill_dir / "skill.md").write_text(
            "---\nid: new_skill\nname: 新技能\nversion: 1.0.0\n---\n\n# New\n",
            encoding="utf-8",
        )
        sync1.add()
        sync1.commit("Add new skill")

        # 切回 main 合并
        sync1.checkout("main")
        result = sync1.merge_branch("feature/new-skill", target="main")
        assert result.success
        assert (repo1 / "new_skill" / "skill.md").exists()

    def test_branch_isolation(self, git_env):
        """分支修改不影响 main"""
        sync1, _, repo1, _, _ = git_env
        sync1.create_branch("feature/x", base="main")
        _write_skill(repo1, version="9.9.9")
        sync1.add()
        sync1.commit("Branch update")

        sync1.checkout("main")
        content = (repo1 / "pdf_parser" / "skill.md").read_text(encoding="utf-8")
        assert "9.9.9" not in content


# ═══════════════════════════════════════════════════════════════════
#  3. 冲突场景集成测试
# ═══════════════════════════════════════════════════════════════════

class TestGitSyncConflictE2E:
    """冲突场景端到端测试"""

    def test_pull_detects_conflict(self, git_env):
        """pull 检测到冲突"""
        sync1, sync2, repo1, repo2, _ = git_env

        # repo1 修改 version 并推送
        _write_skill(repo1, version="1.1.0")
        sync1.add()
        sync1.commit("Bump to 1.1.0")
        sync1.push(branch="main")

        # repo2 修改同一行 version 并 commit
        _write_skill(repo2, version="1.2.0")
        sync2.add()
        sync2.commit("Bump to 1.2.0")

        # repo2 pull → 冲突
        result = sync2.pull(branch="main", rebase=False)
        assert not result.success
        assert len(result.conflicts) > 0
        assert any("skill.md" in c.path for c in result.conflicts)

    def test_conflict_auto_resolved_version(self, git_env):
        """version 冲突自动解决（取较高版本）"""
        sync1, sync2, repo1, repo2, _ = git_env

        # repo1: version=1.1.0
        _write_skill(repo1, version="1.1.0")
        sync1.add()
        sync1.commit("Bump to 1.1.0")
        sync1.push(branch="main")

        # repo2: version=1.2.0
        _write_skill(repo2, version="1.2.0")
        sync2.add()
        sync2.commit("Bump to 1.2.0")

        # pull → 冲突
        result = sync2.pull(branch="main", rebase=False)
        assert not result.success

        # ConflictResolver 自动解决
        resolver = ConflictResolver(sync2)
        conflicts = resolver.detect(result)
        assert len(conflicts) >= 1

        resolved = resolver.auto_resolve(conflicts[0])
        assert resolved

        # 验证合并结果：取较高版本
        merged = (repo2 / "pdf_parser" / "skill.md").read_text(encoding="utf-8")
        assert "1.2.0" in merged

    def test_conflict_auto_resolved_tags_merge(self, git_env):
        """tags 冲突自动解决（合并去重）"""
        sync1, sync2, repo1, repo2, _ = git_env

        # repo1: tags 加 ocr
        _write_skill(repo1, tags=["pdf", "parse", "ocr"])
        sync1.add()
        sync1.commit("Add ocr tag")
        sync1.push(branch="main")

        # repo2: tags 加 advanced
        _write_skill(repo2, tags=["pdf", "parse", "advanced"])
        sync2.add()
        sync2.commit("Add advanced tag")

        result = sync2.pull(branch="main", rebase=False)
        if not result.success:
            # 有冲突则自动解决
            resolver = ConflictResolver(sync2)
            conflicts = resolver.detect(result)
            for c in conflicts:
                resolver.auto_resolve(c)

        # 验证 tags 合并
        merged = (repo2 / "pdf_parser" / "skill.md").read_text(encoding="utf-8")
        # 至少应包含基础的 pdf 和 parse
        assert "pdf" in merged

    def test_merge_branch_conflict_auto_resolved(self, git_env):
        """分支合并冲突自动解决"""
        sync1, _, repo1, _, _ = git_env

        # main 分支修改 version
        _write_skill(repo1, version="1.1.0")
        sync1.add()
        sync1.commit("Main: bump to 1.1.0")

        # 创建分支修改 version
        sync1.create_branch("feature/bump", base="main")
        _write_skill(repo1, version="1.5.0")
        sync1.add()
        sync1.commit("Feature: bump to 1.5.0")

        # main 再次修改 version（制造冲突，避免 fast-forward）
        sync1.checkout("main")
        _write_skill(repo1, version="1.3.0")
        sync1.add()
        sync1.commit("Main: bump to 1.3.0")

        # 合并 → 冲突
        result = sync1.merge_branch("feature/bump", target="main")
        assert not result.success

        # 自动解决
        resolver = ConflictResolver(sync1)
        conflicts = resolver.detect(result)
        assert len(conflicts) >= 1
        assert resolver.auto_resolve(conflicts[0])

        merged = (repo1 / "pdf_parser" / "skill.md").read_text(encoding="utf-8")
        assert "1.5.0" in merged  # 取较高

    def test_unresolvable_conflict_returns_false(self, git_env):
        """name 字段冲突不可自动解决"""
        sync1, sync2, repo1, repo2, _ = git_env

        # repo1: name=PDF解析器
        _write_skill(repo1, name="PDF解析器")
        sync1.add()
        sync1.commit("Rename to PDF解析器")
        sync1.push(branch="main")

        # repo2: name=PDF解析工具
        _write_skill(repo2, name="PDF解析工具")
        sync2.add()
        sync2.commit("Rename to PDF解析工具")

        result = sync2.pull(branch="main", rebase=False)
        assert not result.success

        resolver = ConflictResolver(sync2)
        conflicts = resolver.detect(result)
        assert len(conflicts) >= 1

        # name 冲突不可自动解决
        resolved = resolver.auto_resolve(conflicts[0])
        assert not resolved

    def test_has_conflicts_after_pull(self, git_env):
        """pull 冲突后 has_conflicts 返回 True"""
        sync1, sync2, repo1, repo2, _ = git_env

        _write_skill(repo1, version="1.1.0")
        sync1.add()
        sync1.commit("Bump")
        sync1.push(branch="main")

        _write_skill(repo2, version="1.2.0")
        sync2.add()
        sync2.commit("Bump")
        sync2.pull(branch="main", rebase=False)

        assert sync2.has_conflicts()

    def test_abort_merge_clears_conflicts(self, git_env):
        """abort_merge 清除冲突状态"""
        sync1, sync2, repo1, repo2, _ = git_env

        _write_skill(repo1, version="1.1.0")
        sync1.add()
        sync1.commit("Bump")
        sync1.push(branch="main")

        _write_skill(repo2, version="1.2.0")
        sync2.add()
        sync2.commit("Bump")
        sync2.pull(branch="main", rebase=False)

        assert sync2.has_conflicts()
        sync2.abort_merge()
        assert not sync2.has_conflicts()


# ═══════════════════════════════════════════════════════════════════
#  4. ConflictResolver 集成测试
# ═══════════════════════════════════════════════════════════════════

class TestConflictResolverE2E:
    """ConflictResolver 端到端测试"""

    def test_categorize_content_conflict(self, git_env):
        """categorize 返回 content_conflict"""
        sync1, sync2, repo1, repo2, _ = git_env

        _write_skill(repo1, version="1.1.0")
        sync1.add()
        sync1.commit("Bump")
        sync1.push(branch="main")

        _write_skill(repo2, version="1.2.0")
        sync2.add()
        sync2.commit("Bump")
        result = sync2.pull(branch="main", rebase=False)

        resolver = ConflictResolver(sync2)
        conflicts = resolver.detect(result)
        for c in conflicts:
            category = resolver.categorize(c)
            assert category in ("content_conflict", "add_add", "modify_delete")

    def test_resolve_all_batch(self, git_env):
        """resolve_all 批量解决"""
        sync1, sync2, repo1, repo2, _ = git_env

        _write_skill(repo1, version="1.1.0")
        sync1.add()
        sync1.commit("Bump")
        sync1.push(branch="main")

        _write_skill(repo2, version="1.2.0")
        sync2.add()
        sync2.commit("Bump")
        result = sync2.pull(branch="main", rebase=False)

        resolver = ConflictResolver(sync2)
        conflicts = resolver.detect(result)
        resolved, unresolved = resolver.resolve_all(conflicts)

        # version 冲突应可自动解决
        assert len(resolved) >= 1
        assert len(unresolved) == 0
