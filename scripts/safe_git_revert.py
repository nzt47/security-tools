"""安全回滚工具 —— 守卫流程只允许 dry-run, 绝不执行破坏性 git 操作

Why(dry-run 默认):
- run_ci_guard 的 rollback_sim 步骤只需要"模拟回滚"的结果(受影响文件清单),
  用于 PR 合并前评估回滚影响面, 不执行任何真实 git 写操作。
- dry_run=False 也仅执行 `git revert --no-commit`(生成反向补丁到工作区,
  不提交、不强制), 且仅在调用方显式请求时生效。

用法:
    from safe_git_revert import safe_revert
    rev = safe_revert("abc1234", dry_run=True)   # 仅列影响文件
"""

import os
import subprocess
import sys

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _git(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _affected_files(commit: str) -> list[str]:
    """返回 commit 修改的文件清单(相对仓库根), 查询失败返回空列表"""
    r = _git(["diff-tree", "--no-commit-id", "--name-only", "-r", commit])
    if r.returncode != 0:
        return []
    return [line.strip() for line in r.stdout.splitlines() if line.strip()]


def safe_revert(target: str, dry_run: bool = True) -> dict:
    """模拟/执行回滚 target commit, 返回 {affected_files, exit_code}

    dry_run=True : 只列出 target 影响的文件与回滚建议命令, 不执行任何写操作
    dry_run=False: 执行 `git revert --no-commit`(反向补丁落地工作区, 不提交)
    """
    # 防御: target 必须能解析为 commit, 否则不进行任何操作
    verify = _git(["rev-parse", "--verify", f"{target}^{{commit}}"])
    if verify.returncode != 0:
        return {"affected_files": [], "exit_code": 1,
                "error": f"无法解析 commit: {target}"}

    files = _affected_files(verify.stdout.strip())

    if dry_run:
        # 日志走 stderr: 不污染 stdout(调用方 run_ci_guard --json 依赖纯净 stdout)
        print(f"[safe_git_revert][dry-run] 目标 commit: {target}", file=sys.stderr)
        print(f"[safe_git_revert][dry-run] 受影响文件({len(files)} 个):",
              file=sys.stderr)
        for f in files:
            print(f"  - {f}", file=sys.stderr)
        print(f"[safe_git_revert][dry-run] 建议: git revert --no-commit {target}",
              file=sys.stderr)
        return {"affected_files": files, "exit_code": 0}

    # 非 dry-run: 生成反向补丁到工作区, 不提交、不丢弃任何数据
    r = _git(["revert", "--no-commit", target])
    if r.returncode != 0:
        return {"affected_files": files, "exit_code": r.returncode,
                "error": r.stderr.strip() or "git revert 执行失败"}
    return {"affected_files": files, "exit_code": 0}


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    result = safe_revert(sys.argv[1], dry_run="--apply" not in sys.argv)
    print(result)
    sys.exit(result["exit_code"])
