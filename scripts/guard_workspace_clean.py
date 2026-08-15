#!/usr/bin/env python3
"""回归前工作区干净检查（P0 任务清单 T-11 / T-18）

检查 git status --porcelain 是否为空；非空时输出差异清单并按模式决定是否阻断。

用途：
- 本地：run_full_pytest.py 回归入口前置（默认提示模式，REGRESSION_REQUIRE_CLEAN=1 时严格阻断）
- CI：checkout 后验证工作区无残留（未跟踪生成物/未提交改动污染判定）

用法：
    python scripts/guard_workspace_clean.py [--repo-root <path>]
                                           [--strict]        # 非空即退出码 1（阻断）
                                           [--allow <glob>]  # 豁免路径（可多次传入），相对 repo-root

退出码: 0=干净（或仅被豁免项）, 1=脏且严格模式（或本地提示模式下的内部错误）
本地提示模式（默认非 strict）：输出差异清单并返回 0，便于并行会话常态脏工作区下仍可回归，
同时将清单打印出来供人工核对——严格模式由 CI 或 REGRESSION_REQUIRE_CLEAN=1 启用。
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import subprocess
import sys
from pathlib import Path


def git_porcelain(repo_root: Path) -> list[str]:
    """返回 git status --porcelain 输出行；非 git 仓库或出错时抛异常。"""
    r = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30,
    )
    if r.returncode != 0:
        raise RuntimeError(f"git status 失败: {r.stderr.strip()[:300]}")
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def is_allowed(path: str, allows: list[str]) -> bool:
    """路径是否命中任一豁免 glob（路径分隔符统一为 /，匹配 'path/**' 需显式给出）。"""
    norm = path.replace("\\", "/")
    return any(fnmatch.fnmatch(norm, a) for a in allows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=None,
                        help="仓库根目录（默认：脚本上级目录）")
    parser.add_argument("--strict", action="store_true",
                        help="严格模式：存在未豁免变更即退出码 1（CI 使用）")
    parser.add_argument("--allow", action="append", default=[],
                        help="豁免路径 glob（可多次传入，相对 repo-root），如 'pytest_chunks/**'")
    args = parser.parse_args()

    repo_root = Path(args.repo_root) if args.repo_root else Path(__file__).resolve().parent.parent
    if not (repo_root / ".git").exists() and not (repo_root / ".git").is_file():
        print(f"[guard] 非 git 仓库或 .git 缺失: {repo_root}", file=sys.stderr)
        return 1

    # 环境变量可强制严格模式（本地回归入口 REGRESSION_REQUIRE_CLEAN=1）
    strict = args.strict or os.environ.get("REGRESSION_REQUIRE_CLEAN") == "1"

    try:
        dirty = [ln for ln in git_porcelain(repo_root) if not is_allowed(ln[3:], args.allow)]
    except RuntimeError as e:
        print(f"[guard] 检查失败: {e}", file=sys.stderr)
        return 1

    if not dirty:
        print("[guard] 工作区干净，放行")
        return 0

    print(f"[guard] 检测到 {len(dirty)} 项未提交/未跟踪变更：")
    for ln in dirty:
        print(f"  {ln}")
    print("[guard] 提示: 回归判定可能被脏工作区污染。请先提交或隔离到独立 worktree，"
          "详见 docs/zh/P0_测试可信度修复_执行任务清单_20260813.md (T-9~T-11)。")
    if strict:
        print("[guard] 严格模式：阻断回归")
        return 1
    print("[guard] 提示模式：放行（设置 REGRESSION_REQUIRE_CLEAN=1 可强制阻断）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
