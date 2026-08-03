#!/usr/bin/env python3
"""将 tests/unit 下的测试文件按 round-robin 均分到多个 shard。

【背景】GitHub 公共 runner 会回收运行超过 ~20min 的长 job（单元测试
全量 8661 个在 runner 上连续 6 次于 85% 进度被 shutdown signal 回收，
pytest 本身 0 失败）。拆分为多个短 job 可显著降低被回收概率。

用法（CI unit-tests job）:
    python scripts/split_unit_tests.py --shard 1 --shards 4

输出: 当前 shard 应执行的测试文件路径（空格分隔，可直接传给 pytest）。

【简易】单文件零依赖，仅标准库；round-robin 分配保证文件数均衡。
【变易】--shards 可调，便于未来增减并行度。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def collect_test_files(root: Path) -> list[str]:
    """收集 tests/unit 下所有 test_*.py，按路径排序。"""
    unit_dir = root / "tests" / "unit"
    files = sorted(p for p in unit_dir.glob("test_*.py"))
    # as_posix(): CI 在 Linux runner 上执行，路径必须用正斜杠分隔
    return [p.relative_to(root).as_posix() for p in files]


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
    # round-robin：第 i 个文件（按排序）进入第 (i % shards) 个 shard，
    # 比连续分块更均衡（避免某 shard 恰好集中大量重测试文件）
    my_files = files[args.shard - 1:: args.shards]
    print(" ".join(my_files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
