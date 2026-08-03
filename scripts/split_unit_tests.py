#!/usr/bin/env python3
"""将 tests/unit 下的测试文件按"测试数均衡"分配到多个 shard。

【背景】GitHub 公共 runner 会回收运行过长的 job（单元测试全量 8661 个
在 runner 上连续 6 次于 85% 进度被 shutdown signal 回收，pytest 本身
0 失败）。拆分为多个短 job 可显著降低被回收概率。

【不易】按测试数贪心均衡而非 round-robin 文件数均分：round-robin 按字母
序轮询只保证文件数均衡，会把超大文件扎堆（test_system_tools_core.py 407
测试 + test_error_handler.py 340 测试曾同在 Shard3），导致该 shard 运行
10+ 分钟远超其他 shard 的 4-7 分钟，2026-08-03 连续两轮 CI 的 3 个
Shard3 job 全部被 runner 回收。贪心分配保证每个 shard 测试数均衡
（约 2245），重文件均匀分散，各 shard 运行时间接近。

用法（CI unit-tests job）:
    python scripts/split_unit_tests.py --shard 1 --shards 4

输出: 当前 shard 应执行的测试文件路径（空格分隔，可直接传给 pytest）。

【简易】单文件零依赖，仅标准库；贪心分配 ~15 行实现。
【变易】--shards 可调，便于未来增减并行度。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 【不易】排除清单必须与 ci.yml 的 --ignore 保持一致：
# test_sandbox_multiprocess_boundary.py 含 CPU 密集型/子进程崩溃测试，
# CI 用 --ignore 隔离。但 --ignore 无法排除命令行显式传入的文件路径，
# 若分片脚本把该文件分配进某 shard，pytest 会绕过 --ignore 直接运行，
# 触发 worker 崩溃（node down）→ pytest 挂起 48min → runner 回收。
# 历史教训：4-shard 时代该文件在 Shard 2、6-shard 时代在 Shard 1，
# 均导致对应 shard 连续多轮 job 全部被回收，且曾误判为"大文件 OOM"。
EXCLUDED = {"tests/unit/test_sandbox_multiprocess_boundary.py"}


def collect_test_files(root: Path) -> list[str]:
    """收集 tests/unit 下所有 test_*.py（排除 EXCLUDED），按路径排序。"""
    unit_dir = root / "tests" / "unit"
    files = sorted(p for p in unit_dir.glob("test_*.py"))
    # as_posix(): CI 在 Linux runner 上执行，路径必须用正斜杠分隔
    rel = [p.relative_to(root).as_posix() for p in files]
    return [f for f in rel if f not in EXCLUDED]


def count_tests(root: Path, rel_path: str) -> int:
    """统计单个测试文件内的测试数（def test_* 行数，近似耗时权重）。"""
    n = 0
    for line in (root / rel_path).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("def test_") or stripped.startswith("async def test_"):
            n += 1
    return max(n, 1)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="按文件均分 tests/unit 测试到多个 shard"
    )
    parser.add_argument("--shard", type=int, required=True,
                        help="当前分片序号（从 1 开始）")
    parser.add_argument("--shards", type=int, default=4,
                        help="总分片数（默认 4）")
    args = parser.parse_args()

    if args.shard < 1 or args.shard > args.shards:
        parser.error(f"--shard 必须在 [1, {args.shards}] 内，当前 {args.shard}")

    files = collect_test_files(ROOT)
    # 贪心均衡: 按测试数降序, 每次放入当前测试总数最少的 shard
    # （重文件优先分配，避免大文件扎堆导致单 shard 运行时间过长）
    counts = {f: count_tests(ROOT, f) for f in files}
    buckets: list[list[str]] = [[] for _ in range(args.shards)]
    totals = [0] * args.shards
    for f in sorted(files, key=lambda x: -counts[x]):
        idx = totals.index(min(totals))
        buckets[idx].append(f)
        totals[idx] += counts[f]
    print(" ".join(buckets[args.shard - 1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
