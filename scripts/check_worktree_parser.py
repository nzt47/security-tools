"""worktree 级 CLI parser 注册一致性检测（TASK-04 防线，符号级 AST）

场景（Why）:
    并行会话会创建/切换/删除 worktree，导致工作区文件被覆盖回旧版，
    其中 CLI parser 注册缺失（convert-cards 事故：注册被冲掉时运行时
    才暴露 invalid choice）最难提前发现。pre-commit 只覆盖主工作区
    提交时刻；本脚本在任何 worktree 创建或切换（post-checkout hook）
    时遍历所有 worktree 检测 CLI 入口，输出 [WARN] 提示不一致，
    防止问题扩散到部署终验阶段。

用法:
    python scripts/check_worktree_parser.py [--checkout <new_head>]
    退出码: 0=全部一致；1=存在 WARN（可接入 hook/CI 阻断）

接线:
    .git/hooks/post-checkout 调用本脚本（仅警告不阻断 checkout）：
        python scripts/check_worktree_parser.py --checkout "$3"

    依赖: scripts/check_cli_parser.py（analyze_cli_file AST 双向校验）
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from scripts.check_cli_parser import analyze_cli_file  # noqa: E402

# 各 worktree 中需要检测的 CLI 入口（相对 worktree 根）
CLI_ENTRIES = [
    Path("agent/knowledge/__main__.py"),
]


def list_worktrees(repo_root: Path) -> list[Path]:
    """解析 `git worktree list --porcelain`，返回各 worktree 绝对路径。"""
    out = subprocess.run(
        ["git", "-C", str(repo_root), "worktree", "list", "--porcelain"],
        capture_output=True, text=True, encoding="utf-8",
    ).stdout
    paths: list[Path] = []
    for line in out.splitlines():
        if line.startswith("worktree "):
            paths.append(Path(line.split(maxsplit=1)[1]))
    return paths


def check_worktree(wt: Path) -> list[str]:
    """检测单个 worktree 的 CLI 入口，返回问题清单（空=一致）。"""
    problems: list[str] = []
    for rel in CLI_ENTRIES:
        entry = wt / rel
        if not entry.exists():
            continue  # 该 worktree 分支可能尚无此文件，跳过
        ok, issues = analyze_cli_file(entry)
        if not ok:
            problems.append(f"{rel}:")
            problems.extend(f"    - {i}" for i in issues)
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_worktree_parser",
        description="遍历所有 git worktree 检测 CLI parser 注册一致性",
    )
    parser.add_argument("--checkout", default=None,
                        help="post-checkout hook 传入的新 HEAD（仅日志）")
    args = parser.parse_args(argv)

    worktrees = list_worktrees(_REPO_ROOT)
    total_warn = 0
    if args.checkout:
        print(f"[worktree-parser] post-checkout → {args.checkout}")
    for wt in worktrees:
        problems = check_worktree(wt)
        if problems:
            total_warn += 1
            print(f"[WARN] worktree: {wt}")
            for p in problems:
                print(f"  {p}")
        else:
            print(f"[PASS] worktree: {wt}")
    if total_warn:
        print(f"[FAIL] {total_warn} 个 worktree 存在 parser 注册冲突")
        return 1
    print(f"[PASS] {len(worktrees)} 个 worktree 全部一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
