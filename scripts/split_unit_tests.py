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

用法（CI unit-tests job，默认扫描 tests/unit）:
    python scripts/split_unit_tests.py --shard 1 --shards 4

用法（observability-ci.yml full-project-tests，扫描全 tests/）:
    python scripts/split_unit_tests.py --shard 1 --shards 6 --root tests

输出: 当前 shard 应执行的测试文件路径（空格分隔，可直接传给 pytest）。

【简易】单文件零依赖，仅标准库；贪心分配 ~15 行实现。
【变易】--shards 可调，便于未来增减并行度；--root 可切换 tests/unit 与 tests 全集。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 【不易】排除清单必须与 ci.yml/observability-ci.yml 的 --ignore 保持一致：
# test_sandbox_multiprocess_boundary.py 含 CPU 密集型/子进程崩溃测试，
# CI 用 --ignore 隔离。但 --ignore 无法排除命令行显式传入的文件路径，
# 若分片脚本把该文件分配进某 shard，pytest 会绕过 --ignore 直接运行，
# 触发 worker 崩溃（node down）→ pytest 挂起 48min → runner 回收。
# 历史教训：4-shard 时代该文件在 Shard 2、6-shard 时代在 Shard 1，
# 均导致对应 shard 连续多轮 job 全部被回收，且曾误判为"大文件 OOM"。
#
# 【变易】OBSERVABILITY_CI_ONLY：仅 observability-ci.yml 的全项目模式
# （--root tests）需要排除；ci.yml 的 tests/unit 模式不扫描这些目录。
# 这些文件需要 CI 单元测试阶段无法提供的环境前提（后端 HTTP 服务、
# 未定义的 fixture、不兼容的依赖组合），属于环境依赖而非测试逻辑 bug。
# 详见 docs/troubleshooting/observability_ci_failure_report.md
EXCLUDED = {
    "tests/unit/test_sandbox_multiprocess_boundary.py",
}
# observability-ci 全项目模式专属排除（ci.yml 的 tests/unit 模式不触及）
OBSERVABILITY_CI_ONLY = {
    "tests/performance/test_chromadb_v05_api_compat.py",        # chromadb 0.4.x + numpy 2.0 不兼容
    "tests/performance/test_optimization_benchmark.py",         # fixture 'benchmark' 未定义
    "tests/e2e/test_online_chat.py",                            # 需后端服务（端口 5678 启动超时）
    "tests/e2e/test_online_tool_call.py",                       # 需后端服务（端口 5678 启动超时）
    "tests/integration/test_feedback_integration.py",           # fixture 'feedback_manager' 未定义
    "tests/integration/test_routes_skills_mgmt_integration.py",  # fixture 'skills_mgmt_client' 未定义
    "tests/integration/test_ab_testing_integration.py",         # fixture 'ab_test_manager' 未定义
}


def collect_test_files(root: Path, test_root: str = "tests/unit") -> list[str]:
    """收集指定子树下所有 test_*.py（排除 EXCLUDED），按路径排序。

    【不易】test_root="tests/unit" 用 glob（非递归），保持 ci.yml 行为完全一致。
    【变易】test_root="tests" 用 rglob（递归），覆盖 unit/integration/e2e/regression 全集；
            全项目模式额外排除 OBSERVABILITY_CI_ONLY（环境依赖文件，ci.yml 不触及）。
    """
    target_dir = root / test_root
    # 【不易】排除集合：全项目模式合并 OBSERVABILITY_CI_ONLY
    excluded = set(EXCLUDED)
    if test_root == "tests":
        excluded |= OBSERVABILITY_CI_ONLY
        # 全项目模式：递归扫描所有子目录
        files = sorted(p for p in target_dir.rglob("test_*.py"))
    else:
        # 默认模式：仅 tests/unit/test_*.py（非递归，与 ci.yml 一致）
        files = sorted(p for p in target_dir.glob("test_*.py"))
    # as_posix(): CI 在 Linux runner 上执行，路径必须用正斜杠分隔
    rel = [p.relative_to(root).as_posix() for p in files]
    return [f for f in rel if f not in excluded]


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
        description="按文件均分测试到多个 shard（贪心均衡，支持 tests/unit 与全 tests/）"
    )
    parser.add_argument("--shard", type=int, required=True,
                        help="当前分片序号（从 1 开始）")
    parser.add_argument("--shards", type=int, default=4,
                        help="总分片数（默认 4）")
    # 【变易】--root：默认 tests/unit 保持 ci.yml 向后兼容；
    # observability-ci.yml 传 tests 走全项目模式（rglob 递归）
    parser.add_argument("--root", type=str, default="tests/unit",
                        help="测试根目录（默认 tests/unit；传 tests 走全项目模式）")
    args = parser.parse_args()

    if args.shard < 1 or args.shard > args.shards:
        parser.error(f"--shard 必须在 [1, {args.shards}] 内，当前 {args.shard}")

    # 【简易】白名单校验，避免误传任意路径
    if args.root not in ("tests/unit", "tests"):
        parser.error(f"--root 仅支持 tests/unit 或 tests，当前 {args.root}")

    files = collect_test_files(ROOT, args.root)
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
