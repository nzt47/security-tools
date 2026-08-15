"""pre-commit local hook: 并行会话 index 隔离校验（2026-08-14 混入事故防线）

根因：并行会话（多 worktree）共享 .git/index，并发 git add/commit 会互相干扰：
  - 我方 git add 后 index 被并行会话立即覆盖/清空 → commit 报 "no changes added"
  - 我方已 staged 内容被并行会话的 git commit 一并提交 → 改动"混入"对方 commit

本钩子能力边界（诚实声明）：
  [可防] 我方 add 后 index 被清空 → 提交前校验 index 非空（工作区有改动时）
  [可防] 我方 index 混入高危运行时文件（data/reflection、data/sandbox、data/sessions、
         data/lifetrace、backup、.env 非 example）→ 阻断提示
  [不可防] 并行会话把我方已 staged 的内容带走（发生在对方 commit 上下文）→
           检测到多 worktree 时打印强警告，建议改用 detached worktree 隔离 index

退出码：违反即 exit 1 阻断提交；警告仅打印不阻断。
逃生通道：SKIP=check-index-isolation 跳过本钩子（pre-commit 标准机制）。
"""

import subprocess
import sys

# 高危运行时数据 / 敏感文件（正常提交不应含）
FORBIDDEN_PREFIXES = ("data/reflection/", "data/sandbox/", "backup/")
FORBIDDEN_SUBSTR = ("data/sessions/", "data/lifetrace/")
FORBIDDEN_EXACT = ("data/messages.jsonl",)


def _is_forbidden(path: str) -> bool:
    if any(path.startswith(p) for p in FORBIDDEN_PREFIXES):
        return True
    if any(s in path for s in FORBIDDEN_SUBSTR):
        return True
    if path in FORBIDDEN_EXACT:
        return True
    # .env 精确匹配或 .env.* （排除 .env.example 这类正常模板）
    if path == ".env" or (path.startswith(".env") and not path.endswith(".example")):
        return True
    return False


def _run(cmd: list) -> str:
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()


def main() -> int:
    problems: list[str] = []
    warnings: list[str] = []

    # 1. 多 worktree 检测（并行会话活跃信号）
    wts = [l for l in _run(["git", "worktree", "list", "--porcelain"]).split("\n\n") if l.strip()]
    if len(wts) > 1:
        warnings.append(
            f"检测到 {len(wts)} 个 worktree（并行会话活跃），共享 index 可能被并发覆盖/带走。"
            "建议改用 detached worktree 隔离 index：git worktree add --detach <tmp> HEAD，"
            "在 <tmp> 内 add+commit 后再回到主工作区"
        )

    # 2. index 空检测：工作区有改动但 index 为空 → 提交将为空提交（add 后 index 被清空）
    cached = _run(["git", "diff", "--cached", "--name-only"]).splitlines()
    worktree_changed = _run(["git", "diff", "--name-only"]).splitlines()
    if not cached and worktree_changed:
        problems.append(
            "index 为空但工作区有改动（并行会话可能清空了共享 index），本次提交将为空提交。"
            "请重新 git add 后立即 commit，或改用 detached worktree 隔离 index"
        )

    # 3. 高危运行时文件扫描（并行会话 git add . 可能混入）
    for f in cached:
        if _is_forbidden(f):
            problems.append(
                f"index 含运行时/敏感文件 {f!r}（疑似并行会话 git add . 混入）。"
                "若确需提交请用 `git commit -- <paths>` 精确限定，或 SKIP=check-index-isolation 逃生"
            )

    for w in warnings:
        print(f"[check-index-isolation] WARNING: {w}")
    for p in problems:
        print(f"[check-index-isolation] FAIL: {p}")
    if problems:
        return 1
    if warnings:
        print("[check-index-isolation] OK: index 完整（存在并行会话风险，请留意上方警告）")
    else:
        print("[check-index-isolation] OK: index 完整且无高危混入")
    return 0


if __name__ == "__main__":
    sys.exit(main())
