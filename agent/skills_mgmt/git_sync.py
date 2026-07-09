"""GitSync — 技能仓库 Git 双向同步核心类

设计原则:
    - 不易: subprocess 列表传参防注入; GIT_TERMINAL_PROMPT=0 防挂起; 路径越界检查
    - 变易: _run_git 统一入口便于扩展; dataclass 数据模型便于序列化
    - 简易: 直接封装 git CLI, 不引入 GitPython

公开 API:
    GitSync, GitSyncError, SyncResult, GitStatus, CommitInfo, ConflictFile, MergeResult
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "GitSync",
    "GitSyncError",
    "SyncResult",
    "GitStatus",
    "CommitInfo",
    "ConflictFile",
    "MergeResult",
]

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════
#  异常
# ════════════════════════════════════════════════════════════

class GitSyncError(Exception):
    """GitSync 操作异常"""


# ════════════════════════════════════════════════════════════
#  数据模型
# ════════════════════════════════════════════════════════════

@dataclass
class CommitInfo:
    """提交信息"""
    sha: str
    message: str
    author: str
    timestamp: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sha": self.sha,
            "message": self.message,
            "author": self.author,
            "timestamp": self.timestamp,
        }


@dataclass
class ConflictFile:
    """冲突文件信息

    skill_id 从路径首段提取，用于关联技能。
    """
    path: str
    skill_id: str
    conflict_type: str
    resolution: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "skill_id": self.skill_id,
            "conflict_type": self.conflict_type,
            "resolution": self.resolution,
        }


@dataclass
class SyncResult:
    """同步操作结果（pull/push）"""
    success: bool
    action: str
    branch: str
    commits: List[CommitInfo]
    changed_files: List[str]
    conflicts: List[ConflictFile]
    error: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "action": self.action,
            "branch": self.branch,
            "commits": [c.to_dict() for c in self.commits],
            "changed_files": self.changed_files,
            "conflicts": [c.to_dict() for c in self.conflicts],
            "error": self.error,
        }


@dataclass
class GitStatus:
    """仓库状态"""
    current_branch: str
    clean: bool
    ahead: int
    behind: int
    modified_files: List[str]
    untracked_files: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "current_branch": self.current_branch,
            "clean": self.clean,
            "ahead": self.ahead,
            "behind": self.behind,
            "modified_files": self.modified_files,
            "untracked_files": self.untracked_files,
        }


@dataclass
class MergeResult:
    """分支合并结果"""
    success: bool
    source_branch: str
    target_branch: str
    merged_commits: int
    conflicts: List[ConflictFile]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "source_branch": self.source_branch,
            "target_branch": self.target_branch,
            "merged_commits": self.merged_commits,
            "conflicts": [c.to_dict() for c in self.conflicts],
        }


# ════════════════════════════════════════════════════════════
#  GitSync 核心类
# ════════════════════════════════════════════════════════════

class GitSync:
    """Git 双向同步核心类

    封装 git CLI 提供:
        - 初始化/配置远程仓库
        - add/commit/status/log 基本操作
        - pull/push 双向同步
        - 分支管理 (create/checkout/merge)
        - 冲突检测与解决

    线程安全: 使用 RLock 保护写操作。
    安全: subprocess 列表传参防注入; GIT_TERMINAL_PROMPT=0 防挂起; 路径越界检查。
    """

    def __init__(self, repo_path: Path, remote_url: Optional[str] = None):
        self._repo_path = Path(repo_path)
        self._remote_url = remote_url
        self._lock = threading.RLock()

    @property
    def repo_path(self) -> Path:
        return self._repo_path

    @property
    def remote_url(self) -> Optional[str]:
        return self._remote_url

    # ──────────────────────────────────────────────
    #  底层命令执行
    # ──────────────────────────────────────────────

    def _run_git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        """执行 git 命令

        安全不变量:
            - 列表传参 (禁 shell=True): 防止参数被 shell 解析为命令分隔符
            - GIT_TERMINAL_PROMPT=0: 防止 git 在无凭证时挂起等待输入

        手动检查 returncode 而非依赖 subprocess.run(check=True):
            便于 mock 测试时仍能触发错误转换逻辑。
        """
        cmd = ["git", *args]
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        try:
            result = subprocess.run(
                cmd,
                cwd=str(self._repo_path),
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as e:
            raise GitSyncError(f"git 未安装或不在 PATH 中: {e}") from e
        except subprocess.TimeoutExpired as e:
            raise GitSyncError(f"git 命令超时 (timeout={e.timeout}s): {e}") from e

        if check and result.returncode != 0:
            stderr = result.stderr or result.stdout or ""
            raise GitSyncError(
                f"git 命令失败 (exit={result.returncode}): {stderr}"
            )
        return result

    def _validate_path(self, file_path: str) -> None:
        """验证文件路径在仓库内，防止路径越界攻击"""
        target = (self._repo_path / file_path).resolve()
        try:
            target.relative_to(self._repo_path.resolve())
        except ValueError:
            raise GitSyncError(f"路径越界 (path traversal): {file_path}")

    # ──────────────────────────────────────────────
    #  初始化与配置
    # ──────────────────────────────────────────────

    def init_repo(self) -> None:
        """初始化 git 仓库，设置默认分支为 main"""
        with self._lock:
            self._run_git("init")
            self._run_git("symbolic-ref", "HEAD", "refs/heads/main")

    def configure_remote(self, remote_url: str, name: str = "origin") -> None:
        """配置远程仓库地址"""
        with self._lock:
            self._run_git("remote", "add", name, remote_url)
            self._remote_url = remote_url

    # ──────────────────────────────────────────────
    #  基本操作
    # ──────────────────────────────────────────────

    def add(self, paths: Optional[List[str]] = None) -> None:
        """暂存文件

        paths=None: 暂存所有变更 (git add -A)
        paths=[...]: 暂存指定文件
        """
        with self._lock:
            if paths is None:
                self._run_git("add", "-A")
            else:
                self._run_git("add", *paths)

    def commit(self, message: str, author: Optional[str] = None) -> str:
        """提交暂存区，返回 commit SHA

        author 格式: "Name <email@example.com>"
        """
        with self._lock:
            args = ["commit", "-m", message]
            if author:
                args.extend(["--author", author])
            result = self._run_git(*args)
            return result.stdout.strip()

    # ──────────────────────────────────────────────
    #  状态查询
    # ──────────────────────────────────────────────

    def status(self) -> GitStatus:
        """获取仓库状态"""
        porcelain = self._run_git("status", "--porcelain")
        branch = self._run_git("branch", "--show-current")
        ahead = self._run_git("rev-list", "--count", "@{u}..HEAD")
        behind = self._run_git("rev-list", "--count", "HEAD..@{u}")

        modified, untracked = self._parse_porcelain(porcelain.stdout)
        return GitStatus(
            current_branch=branch.stdout.strip(),
            clean=not modified and not untracked,
            ahead=int(ahead.stdout.strip() or "0"),
            behind=int(behind.stdout.strip() or "0"),
            modified_files=modified,
            untracked_files=untracked,
        )

    def log(self, *, limit: int = 20) -> List[CommitInfo]:
        """获取提交历史

        格式: <sha>|<message>|<author>|<timestamp>
        """
        result = self._run_git(
            "log", f"-{limit}", "--format=%H|%s|%an|%ad", "--date=short"
        )
        commits: List[CommitInfo] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("|", 3)
            if len(parts) == 4:
                commits.append(CommitInfo(
                    sha=parts[0],
                    message=parts[1],
                    author=parts[2],
                    timestamp=parts[3],
                ))
        return commits

    # ──────────────────────────────────────────────
    #  双向同步
    # ──────────────────────────────────────────────

    def pull(self, *, branch: str = "main", rebase: bool = True) -> SyncResult:
        """拉取远程分支

        rebase=True: 使用 git pull --rebase（避免无谓的 merge commit）
        冲突时不抛异常，返回包含 conflicts 列表的 SyncResult。
        """
        if not self._remote_url:
            raise GitSyncError("未配置远程仓库地址 (remote_url)")

        with self._lock:
            args = ["pull"]
            if rebase:
                args.append("--rebase")
            else:
                args.append("--no-rebase")
            args.extend(["origin", branch])
            result = self._run_git(*args, check=False)

            conflicts = self._parse_conflicts(result.stdout + "\n" + result.stderr)
            changed_files = self._parse_changed_files(result.stdout)

            return SyncResult(
                success=result.returncode == 0 and not conflicts,
                action="pull",
                branch=branch,
                commits=[],
                changed_files=changed_files,
                conflicts=conflicts,
                error=None if result.returncode == 0 else result.stderr,
            )

    def push(self, *, branch: str = "main", force: bool = False) -> SyncResult:
        """推送分支到远程

        被拒绝时不抛异常，返回 success=False 的 SyncResult。
        """
        if not self._remote_url:
            raise GitSyncError("未配置远程仓库地址 (remote_url)")

        with self._lock:
            args = ["push"]
            if force:
                args.append("--force")
            args.extend(["origin", branch])
            result = self._run_git(*args, check=False)

            return SyncResult(
                success=result.returncode == 0,
                action="push",
                branch=branch,
                commits=[],
                changed_files=[],
                conflicts=[],
                error=None if result.returncode == 0 else result.stderr,
            )

    # ──────────────────────────────────────────────
    #  分支管理
    # ──────────────────────────────────────────────

    def create_branch(self, branch_name: str, base: str = "main") -> None:
        """创建并切换到新分支"""
        with self._lock:
            self._run_git("checkout", "-b", branch_name, base)

    def checkout(self, branch_name: str) -> None:
        """切换分支"""
        with self._lock:
            self._run_git("checkout", branch_name)

    def merge_branch(self, source: str, target: str = "main") -> MergeResult:
        """合并分支

        注意: 调用方需先 checkout 到 target 分支。本方法只执行 git merge source。
        """
        with self._lock:
            result = self._run_git("merge", source, check=False)
            conflicts = self._parse_conflicts(result.stdout + "\n" + result.stderr)
            return MergeResult(
                success=result.returncode == 0 and not conflicts,
                source_branch=source,
                target_branch=target,
                merged_commits=0,
                conflicts=conflicts,
            )

    # ──────────────────────────────────────────────
    #  冲突处理
    # ──────────────────────────────────────────────

    def has_conflicts(self) -> bool:
        """是否存在未解决的冲突"""
        result = self._run_git("diff", "--name-only", "--diff-filter=U")
        return bool(result.stdout.strip())

    def list_conflicts(self) -> List[ConflictFile]:
        """列出所有冲突文件"""
        result = self._run_git("diff", "--name-only", "--diff-filter=U")
        conflicts: List[ConflictFile] = []
        for line in result.stdout.splitlines():
            path = line.strip()
            if not path:
                continue
            skill_id = path.split("/")[0] if "/" in path else path
            conflicts.append(ConflictFile(
                path=path,
                skill_id=skill_id,
                conflict_type="both_modified",
                resolution=None,
            ))
        return conflicts

    def resolve_conflict(self, file_path: str, resolution: str) -> None:
        """解决冲突文件

        resolution: "ours" 使用本地版本, "theirs" 使用远程版本
        解决后自动 git add 标记为已解决。
        """
        if resolution not in ("ours", "theirs"):
            raise GitSyncError(
                f"不支持的解决方案: {resolution}, 支持 ours/theirs"
            )
        self._validate_path(file_path)
        with self._lock:
            self._run_git("checkout", f"--{resolution}", file_path)
            self._run_git("add", file_path)

    def abort_merge(self) -> None:
        """中止合并"""
        with self._lock:
            self._run_git("merge", "--abort")

    # ──────────────────────────────────────────────
    #  输出解析（静态方法）
    # ──────────────────────────────────────────────

    @staticmethod
    def _parse_porcelain(output: str) -> Tuple[List[str], List[str]]:
        """解析 git status --porcelain 输出

        格式: XY filename
            X: 暂存区状态, Y: 工作区状态
            ??: 未跟踪文件
        Returns: (modified_files, untracked_files)
        """
        modified: List[str] = []
        untracked: List[str] = []
        for line in output.splitlines():
            if not line.strip():
                continue
            status_code = line[:2]
            file_path = line[3:].strip()
            if status_code == "??":
                untracked.append(file_path)
            else:
                modified.append(file_path)
        return modified, untracked

    @staticmethod
    def _parse_changed_files(output: str) -> List[str]:
        """解析 pull/push 输出中的变更文件列表

        格式: " skill.md | 5 +-"
        """
        files: List[str] = []
        for line in output.splitlines():
            line = line.strip()
            if "|" not in line:
                continue
            if line.startswith(("Updating", "Fast-forward", "CONFLICT")):
                continue
            parts = line.split("|", 1)
            if parts:
                files.append(parts[0].strip())
        return files

    @staticmethod
    def _parse_conflicts(output: str) -> List[ConflictFile]:
        """解析冲突文件列表

        格式: "CONFLICT (content): Merge conflict in skill.md"
        """
        conflicts: List[ConflictFile] = []
        for line in output.splitlines():
            if "CONFLICT" not in line:
                continue
            if "Merge conflict in" not in line:
                continue
            path = line.split("Merge conflict in")[-1].strip()
            skill_id = path.split("/")[0] if "/" in path else path
            conflicts.append(ConflictFile(
                path=path,
                skill_id=skill_id,
                conflict_type="both_modified",
                resolution=None,
            ))
        return conflicts
