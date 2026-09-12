"""只读 git 访问层（TASK-S7-02 步骤 2）

【任务定位】
    定位器需要「最近改动」（任务书 §三 步骤 2：「最近改动（git 局部历史，**只读 git**，
    不写）」）。本模块是该需求的**唯一 git 出口**：任何修复流程里的 git 调用都必须
    经 ``run_git_readonly()``，而它对**子命令白名单**逐条把关。

【不易（为什么白名单，而不是黑名单）】
    黑名单（禁 push/merge/...）的问题在于「未登记 = 允许」——未来 git 新增子命令，
    或调用方写 ``git -c ... push`` 这类前缀参数时，黑名单会静默放行。任务书 §一 边界 ①
    是宪法级的（「绝不自动 push、绝不自动 merge」），因此本模块取**白名单**：
    只有明确列出的只读子命令能执行，其余一律 ``ForbiddenGitOperation``。
    唯一例外是 ``create_local_branch()``——它**不经过** ``run_git_readonly``，而是
    自带一条独立实现（``git branch <name> <start>``），使「写操作」在代码里**只有
    一处、可被静态检查指认**。这样「代码路径不存在 push/merge」的验收断言才有
    可判定的落点。

【不易（拒绝前缀参数）】
    ``git -c core.pager=... log`` 与 ``git --git-dir=... log`` 都会绕过「第一个参数
    即子命令」的朴素判定，故本模块拒绝任何以 ``-`` 开头的首个参数（不解析、不放行）。
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

#: 允许的**只读** git 子命令白名单（未登记 → 拒绝）
READONLY_SUBCOMMANDS: Tuple[str, ...] = (
    "log", "show", "diff", "status", "rev-parse", "rev-list", "ls-files",
    "ls-tree", "cat-file", "describe", "shortlog", "blame", "for-each-ref",
    "symbolic-ref", "merge-base", "name-rev", "count-objects", "check-ignore",
)

#: 恒被拒绝的写/网络/历史改写子命令（**明细**清单，用于拒绝原因可读化与自证断言）
FORBIDDEN_SUBCOMMANDS: Tuple[str, ...] = (
    "push", "fetch", "pull", "merge", "rebase", "reset", "checkout", "switch",
    "commit", "add", "rm", "mv", "restore", "clean", "stash", "cherry-pick",
    "revert", "tag", "clone", "remote", "submodule", "worktree", "branch",
    "config", "gc", "prune", "filter-branch", "update-ref", "symbolic-ref-write",
    "am", "apply", "archive", "daemon", "http-fetch", "send-pack", "receive-pack",
)

#: 默认命令超时（秒）——git 只读命令不应挂起流程
DEFAULT_GIT_TIMEOUT = 20.0

#: 提交分隔符（NUL：git 输出里不可能出现，比 ``%x01`` 更稳）
_RECORD_SEP = "\x00"


class GitReadOnlyError(RuntimeError):
    """git 只读访问层异常基类"""


class ForbiddenGitOperation(GitReadOnlyError):
    """尝试执行非只读 git 操作（**宪法级拒绝**：绝不 push/合并/改写历史）"""

    def __init__(self, subcommand: str, reason: str = "") -> None:
        self.subcommand = str(subcommand or "")
        self.reason = reason or "不在只读白名单内"
        super().__init__(
            f"拒绝执行 git {self.subcommand}：{self.reason}"
            f"（只读白名单：{', '.join(READONLY_SUBCOMMANDS)}）")


class GitUnavailableError(GitReadOnlyError):
    """git 不可用 / 命令执行失败"""


def assert_readonly_subcommand(subcommand: str) -> str:
    """校验子命令是否在只读白名单内；不在则抛 ``ForbiddenGitOperation``

    Returns:
        归一化后的子命令（小写去空白）。
    """
    sub = str(subcommand or "").strip().lower()
    if not sub:
        raise ForbiddenGitOperation(sub, "子命令为空")
    if sub.startswith("-"):
        # ``git -c k=v log`` / ``git --git-dir=... log`` 一类前缀参数：
        # 不解析、不放行（见模块 docstring「拒绝前缀参数」）
        raise ForbiddenGitOperation(sub, "不接受以 '-' 开头的参数（前缀参数不可判定）")
    if sub in FORBIDDEN_SUBCOMMANDS:
        raise ForbiddenGitOperation(sub, "该子命令会写仓库/改历史/访问网络")
    if sub not in READONLY_SUBCOMMANDS:
        raise ForbiddenGitOperation(sub, "未登记在只读白名单中（fail-closed）")
    return sub


def is_readonly_invocation(argv: Sequence[str]) -> bool:
    """整条 git argv 是否只读（供静态自证与调用点前置校验）"""
    if not argv:
        return False
    try:
        assert_readonly_subcommand(str(argv[0]))
    except ForbiddenGitOperation:
        return False
    joined = " ".join(str(a) for a in argv)
    # 白名单子命令里也不允许出现强制写标志（如 `log --output=`）
    for token in ("--output=", "--exec=", "--upload-pack=", "--receive-pack="):
        if token in joined:
            return False
    return True


def run_git_readonly(args: Sequence[str], *, cwd: str, timeout: float = DEFAULT_GIT_TIMEOUT,
                     env: Optional[Mapping[str, str]] = None) -> str:
    """执行一条**只读** git 命令并返回 stdout（UTF-8）

    Args:
        args: 子命令及其参数（``args[0]`` 必须是白名单子命令）。
        cwd: 仓库根。
        timeout: 超时秒。
        env: 覆盖环境（缺省继承；本函数**不向子进程传递凭据**）。

    Raises:
        ForbiddenGitOperation: 子命令非只读。
        GitUnavailableError: git 不可用 / 超时 / 非零退出。
    """
    if not args:
        raise ForbiddenGitOperation("", "命令为空")
    assert_readonly_subcommand(str(args[0]))
    if not is_readonly_invocation(args):
        raise ForbiddenGitOperation(str(args[0]), "整条命令含非只读标志")

    full_env = dict(os.environ)
    if env:
        full_env.update({str(k): str(v) for k, v in env.items()})
    # 只读命令不应触发交互式凭据提示（也是「不访问网络」的旁证）
    full_env["GIT_TERMINAL_PROMPT"] = "0"
    full_env["GIT_OPTIONAL_LOCKS"] = "0"
    # 路径**必须**是可见字面量：git 默认 core.quotepath=true 会把非 ASCII 路径
    # 转义成八进制（`"docs/\346\226\207..."`），既不可读也无法与仓库相对路径比较。
    # 用 `-c` 传入是 git 的官方全局选项写法（非子命令，不绕过白名单判定）。
    try:
        proc = subprocess.run(
            ["git", "-c", "core.quotepath=false", *[str(a) for a in args]],
            cwd=str(cwd), capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=float(timeout),
            env=full_env)
    except FileNotFoundError as exc:  # git 未安装
        raise GitUnavailableError(f"git 不可用：{exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitUnavailableError(f"git {' '.join(args)} 超时（{timeout}s）") from exc
    except OSError as exc:
        raise GitUnavailableError(f"git 执行失败：{exc}") from exc
    if proc.returncode != 0:
        raise GitUnavailableError(
            f"git {' '.join(args)} 退出码 {proc.returncode}："
            f"{(proc.stderr or '').strip()[:400]}")
    return proc.stdout or ""


# ════════════════════════════════════════════════════════════
#  只读查询
# ════════════════════════════════════════════════════════════


def repo_head(repo_root: str) -> str:
    """当前 HEAD SHA（失败返回空串，不抛——定位器可降级）"""
    try:
        return run_git_readonly(["rev-parse", "HEAD"], cwd=repo_root).strip()
    except GitReadOnlyError:
        return ""


def is_git_repo(repo_root: str) -> bool:
    """是否 git 工作区（``rev-parse --is-inside-work-tree``）"""
    try:
        out = run_git_readonly(["rev-parse", "--is-inside-work-tree"], cwd=repo_root)
    except GitReadOnlyError:
        return False
    return out.strip().lower() == "true"


def git_dir(repo_root: str) -> str:
    """解析真实 git 目录（worktree 下 ``.git`` 是文件，指向真正的 gitdir）

    审计/事件路径必须落在**真实 git 目录**内，否则 worktree 场景下会被误判为
    「仓库外产物」。故不能假设 ``<repo>/.git`` 一定是目录。
    """
    direct = os.path.join(repo_root, ".git")
    if os.path.isdir(direct):
        return direct
    if os.path.isfile(direct):
        try:
            with open(direct, "r", encoding="utf-8") as fh:
                text = fh.read().strip()
        except OSError:
            return direct
        match = re.match(r"^gitdir:\s*(.+)$", text)
        if match:
            target = match.group(1).strip()
            if not os.path.isabs(target):
                target = os.path.normpath(os.path.join(repo_root, target))
            return target
    return direct


def head_changed_files(repo_root: str, *, limit: int = 200) -> List[str]:
    """HEAD 提交改动的文件清单（仓库相对 POSIX 路径）

    **只读**：``git show --name-only``。用于「最近改动」按 mtime 归属到提交。
    """
    try:
        out = run_git_readonly(
            ["show", "--name-only", "--no-renames", "--format=", "HEAD"],
            cwd=repo_root)
    except GitReadOnlyError:
        return []
    files: List[str] = []
    for line in out.splitlines():
        rel = line.strip().replace("\\", "/")
        if rel and rel not in files:
            files.append(rel)
        if len(files) >= int(limit):
            break
    return files


def recent_commits(repo_root: str, *, limit: int = 10, paths: Sequence[str] = (),
                   ) -> List[Dict[str, Any]]:
    """近期提交（**只读** ``git log``）

    Args:
        repo_root: 仓库根。
        limit: 回看提交数（策略值，非法已在 policy 层回退）。
        paths: 限定路径（``--`` 后的路径过滤器）。

    Returns:
        ``[{sha, short, subject, date, author, files[]}]``；git 不可用时返回 ``[]``
        （**降级不抛**：定位器缺这条证据仍可工作，但会在 ``evidence_gaps`` 披露）。
    """
    if int(limit) <= 0:
        return []
    fmt = "%H%x00%h%x00%s%x00%cI%x00%an%x00"
    args = ["log", f"-n{int(limit)}", f"--format={fmt}", "--name-only", "--no-renames"]
    if paths:
        args += ["--", *[str(p) for p in paths]]
    try:
        out = run_git_readonly(args, cwd=repo_root)
    except GitReadOnlyError:
        return []
    # ``--format`` 以 NUL 结尾 ⇒ 输出形如：
    #   <H>\0<h>\0<s>\0<date>\0<an>\0 <文件行...>\n <H>\0...
    # 故按下标 5k/5k+1/5k+2/5k+3/5k+4 取字段，5k+5 起为紧随其后的文件清单。
    chunks = out.split(_RECORD_SEP)
    commits: List[Dict[str, Any]] = []
    idx = 0
    while idx + 4 < len(chunks):
        sha, short, subject, date, author = chunks[idx:idx + 5]
        idx += 5
        following = chunks[idx] if idx < len(chunks) else ""
        if following == "":  # 已到末尾（格式串尾随的最后一个 NUL）
            idx += 1
            last = True
        else:
            last = False
        files: List[str] = []
        for line in following.splitlines():
            rel = line.strip().replace("\\", "/")
            if rel:
                files.append(rel)
        commits.append({
            "sha": sha.strip(),
            "short": short.strip(),
            "subject": subject.strip(),
            "date": date.strip(),
            "author": author.strip(),
            "files": files,
        })
        if last:
            break
    return commits


def is_tracked(repo_root: str, path: str) -> bool:
    """路径是否被 git 跟踪（``git ls-files --error-unmatch``）"""
    try:
        run_git_readonly(["ls-files", "--error-unmatch", "--", str(path)], cwd=repo_root)
    except GitReadOnlyError:
        return False
    return True


def is_ignored(repo_root: str, path: str) -> bool:
    """路径是否被 .gitignore 忽略（``git check-ignore -v``）

    【为什么不用 ``--quiet``】``--quiet`` 下「未忽略」与「出错」都表现为退出码 1，
    无法区分。改用 `-v`（输出匹配到的规则）后，「未忽略 = 退出码 1 但无输出」，
    语义明确。
    """
    try:
        out = run_git_readonly(["check-ignore", "-v", "--", str(path)], cwd=repo_root)
    except GitUnavailableError:
        return False  # 未命中规则时 git 退出码为 1（非"忽略"）
    except GitReadOnlyError:
        return False
    return bool(out.strip())


# ════════════════════════════════════════════════════════════
#  产物分支（**唯一写操作**；不含 push/合并）
# ════════════════════════════════════════════════════════════


@dataclass
class BranchResult:
    """本地分支创建结果（产物止于本地）"""

    name: str = ""
    created: bool = False
    start_point: str = ""
    error: str = ""
    existed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "created": bool(self.created),
            "start_point": self.start_point,
            "error": self.error,
            "existed": bool(self.existed),
            # 恒为 False：本任务不做 push / 合并（字段存在即自证）
            "pushed": False,
            "merged": False,
        }


def branch_exists(repo_root: str, name: str) -> bool:
    """本地分支是否已存在（``rev-parse --verify refs/heads/<name>``）"""
    try:
        run_git_readonly(["rev-parse", "--verify", "--quiet", f"refs/heads/{name}"],
                         cwd=repo_root)
    except GitReadOnlyError:
        return False
    return True


def create_local_branch(repo_root: str, name: str, *, start_point: str = "HEAD",
                        timeout: float = DEFAULT_GIT_TIMEOUT) -> BranchResult:
    """创建**本地**分支（``git branch <name> <start>``）——本任务的唯一写操作

    【为什么单独实现而不复用 ``run_git_readonly``】
        ``branch`` 在白名单外（它是写操作）。把它塞进白名单会让「只读白名单」这个
        概念失效；单独一条实现则让「写操作只有一处」成为可静态指认的事实，
        也让「代码路径中不存在 push/merge」的验收断言有落点。

    【为什么用 ``git branch`` 而不是 ``git checkout -b``】
        本流程可能在主工作区执行。``checkout`` 会切换工作区分支（污染交付），
        ``branch`` 只创建引用，不影响任何工作区文件。

    Args:
        repo_root: 仓库根。
        name: 分支名（须为合法 ref 名，且以 ``repair/`` 前缀为准）。
        start_point: 起点（默认 HEAD）。
        timeout: 超时秒。

    Returns:
        ``BranchResult``（已存在时不覆盖：``existed=True`` 且 ``error`` 说明）。
    """
    if not name or not re.match(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$", name):
        return BranchResult(name=name, error=f"非法分支名: {name!r}")
    if branch_exists(repo_root, name):
        return BranchResult(name=name, existed=True, start_point=start_point,
                            error=f"分支已存在，拒绝覆盖: {name}")
    full_env = dict(os.environ)
    full_env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        proc = subprocess.run(
            ["git", "branch", str(name), str(start_point)], cwd=str(repo_root),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=float(timeout), env=full_env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return BranchResult(name=name, error=f"分支创建失败：{exc}", start_point=start_point)
    if proc.returncode != 0:
        return BranchResult(name=name, start_point=start_point,
                            error=f"分支创建失败：{(proc.stderr or '').strip()[:300]}")
    return BranchResult(name=name, created=True, start_point=start_point)


__all__ = [
    "READONLY_SUBCOMMANDS", "FORBIDDEN_SUBCOMMANDS", "DEFAULT_GIT_TIMEOUT",
    "GitReadOnlyError", "ForbiddenGitOperation", "GitUnavailableError",
    "assert_readonly_subcommand", "is_readonly_invocation", "run_git_readonly",
    "repo_head", "is_git_repo", "git_dir", "head_changed_files", "recent_commits",
    "is_tracked", "is_ignored", "BranchResult", "branch_exists", "create_local_branch",
]
