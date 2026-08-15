#!/usr/bin/env python3
"""等待 index 干净后自动 cherry-pick（并行会话共享 index 阻塞防护，2026-08-15 经验固化）

背景（2026-08-15 实测）：多 worktree 并行会话共享 .git/index，cherry-pick 前若 index 与
HEAD 有整树差异（index_differs_from 检查），会报 "your local changes would be overwritten
by cherry-pick" 且**不列文件名**，无条件拒绝。此时盲目等待/重试会空转。

本脚本两步走：
1. 轮询等待 index 干净（正确判据：`git diff --cached --quiet HEAD`，rc=0 即干净）
2. index 干净后自动 cherry-pick，失败时清理 .git/sequencer 后按指数退避重试

关键经验（写死在文档字符串里，防止误用）：
- 判断 index 是否干净**必须**用 `git diff --cached --quiet`（rc=0 干净）或
  `git diff-index --cached --quiet HEAD`；
  **禁止** `git diff-index HEAD`（无 --cached 比较工作区 vs HEAD，工作区有并行会话
  未暂存改动时恒返回 1，会把"index 已干净"误判为"占用"导致轮询空转 20 分钟）。
- cherry-pick 报错**不列文件名** = index 与 HEAD 整树差异（index_differs_from 检查），
  与具体文件无关；列文件名版本才是真文件冲突。
- 失败 cherry-pick 会留 .git/sequencer 状态，重试前须 `git cherry-pick --quit` 清理。

用法：
    python scripts/dev/wait_index_retry.py --commits <sha1> [<sha2> ...] [--timeout 900]
        [--poll 10] [--max-retries 3] [--dry-run] [--repo <path>]

退出码：
    0 = index 干净且全部 commit 成功入库
    1 = 等待超时或 cherry-pick 重试耗尽
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from typing import List

# 注入仓库根到 sys.path（脚本在 scripts/dev/ 下运行，sys.path[0] 是脚本目录）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 统一重试策略（项目约束：重试机制必须复用 RetryPolicy，支持 fixed/linear/exponential）
from agent.error_handler import RetryPolicy


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True, text=True,
    )


def index_is_clean(repo: str) -> bool:
    """index 是否干净（与 HEAD 无暂存差异）。

    必须用 `git diff --cached --quiet HEAD`（rc=0 干净）；禁止 `git diff-index HEAD`
    （无 --cached 比较工作区 vs HEAD，并行会话未暂存改动会误判为占用）。
    """
    r = _git(repo, "diff", "--cached", "--quiet", "HEAD")
    return r.returncode == 0


def wait_index_clean(repo: str, timeout: float, poll: float) -> bool:
    """轮询等待 index 干净；超时返回 False。"""
    deadline = time.monotonic() + timeout
    while True:
        if index_is_clean(repo):
            return True
        elapsed = timeout - (deadline - time.monotonic())
        if time.monotonic() >= deadline:
            return False
        print(f"[wait_index] index 仍被占用（并行会话活跃），已等 {elapsed:.0f}s，"
              f"{poll}s 后重试…", flush=True)
        time.sleep(poll)


def _cleanup_sequencer(repo: str) -> None:
    """失败 cherry-pick 残留 .git/sequencer 状态，重试前清理。"""
    _git(repo, "cherry-pick", "--quit")


def _cherry_pick(repo: str, commit: str) -> None:
    r = _git(repo, "cherry-pick", commit)
    if r.returncode != 0:
        raise RuntimeError(
            f"cherry-pick {commit} 失败: {r.stdout.strip() or r.stderr.strip()}"
        )
    print(f"[cherry-pick] {commit} 入库成功", flush=True)


def cherry_pick_with_retry(repo: str, commits: List[str], max_retries: int) -> bool:
    """按序 cherry-pick 每个 commit，失败指数退避重试（含 sequencer 清理）。"""
    policy = RetryPolicy(
        max_retries=max_retries,
        initial_delay=2.0,
        max_delay=60.0,
        backoff_factor=2.0,
        strategy="exponential",
    )
    for commit in commits:
        attempt = 0
        while True:
            try:
                _cherry_pick(repo, commit)
                break
            except RuntimeError as e:
                _cleanup_sequencer(repo)
                if not policy.should_retry(e, attempt):
                    print(f"[FATAL] {e}；重试次数已耗尽（{max_retries}）", flush=True)
                    return False
                delay = policy.calculate_delay(attempt)
                print(f"[retry] {e}；{delay:.1f}s 后第 {attempt + 1} 次重试…", flush=True)
                time.sleep(delay)
                attempt += 1
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=".", help="git 仓库路径（默认当前目录）")
    ap.add_argument("--commits", nargs="+", required=True,
                    help="要 cherry-pick 的 commit（按序，可多个）")
    ap.add_argument("--timeout", type=float, default=900.0,
                    help="等待 index 干净的总超时秒数（默认 900）")
    ap.add_argument("--poll", type=float, default=10.0,
                    help="index 轮询间隔秒数（默认 10）")
    ap.add_argument("--max-retries", type=int, default=3,
                    help="单个 commit cherry-pick 失败重试次数（默认 3）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只等待 index 干净并退出，不执行 cherry-pick")
    args = ap.parse_args()

    # 1. 等待 index 干净
    if index_is_clean(args.repo):
        print("[wait_index] index 已干净，直接进行 cherry-pick", flush=True)
    elif wait_index_clean(args.repo, args.timeout, args.poll):
        print("[wait_index] index 已干净（等待完成）", flush=True)
    else:
        print(f"[FATAL] 等待 {args.timeout:.0f}s 后 index 仍被占用，放弃", flush=True)
        return 1

    if args.dry_run:
        print("[dry-run] index 检查通过，未执行 cherry-pick", flush=True)
        return 0

    # 2. 自动 cherry-pick + 重试
    if not cherry_pick_with_retry(args.repo, args.commits, args.max_retries):
        return 1

    print("[done] 全部 commit 入库成功", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
