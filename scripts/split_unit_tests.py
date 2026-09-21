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

【变易·TASK-06 路线3 2026-09-21 —— 由「按条数均衡」改为「按实测耗时均衡」】
  背景（实测，非推断）：observability-ci.yml 全项目测试 6 分片里，Shard 2 的
  4502 条用例跑 277.21s，Shard 4 的 4574 条用例跑 1039.61s —— **条数几乎相同，
  耗时相差 3.75 倍**；2026-09-21 那一轮 Shard 4 更是 4813 条 / 2239.18s（37.3min），
  叠加工作流内建的「失败重试一次」即 75min，逼近 job 的 timeout-minutes: 90。
  根因：按条数（或条数+行数）均衡的工具函数对「条数少但单条极慢」的文件完全
  无感。Shard 4 的 131 个文件中，前 10 个文件占该 shard 实测总耗时的 54%，
  其中 test_skills_cleanup.py 只有 14 条用例却耗时 200.91s（14.35 s/条）。

  做法：引入**每文件实测耗时权重表** scripts/shard_time_weights.json
  （数据来源见该文件内的 measurement_context / sources 字段），
  贪心分配的目标函数由「条数」换成「秒数」。
    · 表内有实测值的文件：直接用实测秒数；
    · 表内没有的文件：按旧的条数启发式形状等比缩放，使该群体的总量等于
      「条数 x 实测标定单位耗时（default_seconds_per_test）」——
      即保留旧启发式的相对排序，只把量纲换成秒。
  回退：--by=count 走旧路径（默认 --by=time）。两种模式的文件集合完全相同，
  只有分片成员不同（并集不变，见 _t06_logs/union_*.txt 对拍）。
  确定性：权重是 (文件, 表) 的纯函数，排序键显式写成 (-权重, 路径)，
  不依赖随机数、时间或环境，同样输入必得同样分片。
"""
from __future__ import annotations

import argparse
import json
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
    # 【变易·R6】时序敏感单测移出并行分片：test_singleton_performance.py 的
    #   微秒级断言（min-of-3）对共享 runner 调度抢占敏感（2026-08-14 CI 实测
    #   3.12 平台 2605.90us vs 正常 <100us 误报）。移入 performance-tests job
    #   独立串行执行，与 ci.yml performance job 的补跑配置必须保持一致。
    #   排除后该文件不再进入任何 unit shard，避免并行 -n 2 下计时干扰。
    "tests/unit/test_singleton_performance.py",
}
# observability-ci 全项目模式专属排除（ci.yml 的 tests/unit 模式不触及）
# 【变易·2026-08-10】目录级排除（以 / 结尾）覆盖整目录：
#   pytest.ini 已 --ignore=tests/performance + --ignore=tests/stress，但 --ignore
#   无法拦截 split 脚本显式传入的文件路径。目录内文件被收集 import 即产生副作用
#   （test_knowledge_link_perf.py 模块级 logging.disable(CRITICAL) 全局禁用 INFO
#   日志，Shard 4 串行段 10 failed），或带性能阈值断言在并行段误报
#   （test_verification_perf / test_resource_leak，Shard 5/6）。性能/压力基准由
#   performance-tests job 专属运行（-m performance），不参与全项目分片。
OBSERVABILITY_CI_ONLY = {
    "tests/performance/",                                   # 性能基准专属 job 运行（pytest.ini --ignore=tests/performance）
    "tests/stress/",                                        # 压力测试（pytest.ini --ignore=tests/stress）
    "tests/performance/test_chromadb_v05_api_compat.py",    # chromadb 0.4.x + numpy 2.0 不兼容
    "tests/performance/test_optimization_benchmark.py",     # fixture 'benchmark' 未定义
    "tests/e2e/test_online_chat.py",                        # 需后端服务（端口 5678 启动超时）
    "tests/e2e/test_online_tool_call.py",                   # 需后端服务（端口 5678 启动超时）
    "tests/integration/test_feedback_integration.py",       # fixture 'feedback_manager' 未定义
    "tests/integration/test_routes_skills_mgmt_integration.py",  # fixture 'skills_mgmt_client' 未定义
    "tests/integration/test_ab_testing_integration.py",     # fixture 'ab_test_manager' 未定义
    "tests/test_network_config_integration.py",             # 脚本式测试，模块级代码在 collection 时执行，需 .env 文件
}

# 【不易】performance/stress 目录级排除（全项目模式）：计时敏感 + 模块顶层副作用风险，
# 混入并行分片会产生 flake（2026-08-10 Shard 6 实证：test_knowledge_link_perf.py 模块顶层
# logging.disable 污染同进程日志捕获测试）。由独立串行 job 覆盖（见
# docs/observability/scripts_serial_ci_segmentation_plan_20260810.md 阶段 B）。
SERIAL_DIRS = (
    "tests/performance/",
    "tests/stress/",
)

# 【不变·TASK-06】实测耗时权重表（数据文件而非内嵌常量）：
#   · 选文件而非内嵌常量的理由：表由 CI artifact（junit.xml）机械再生成
#     （_t06_logs/gen_weights.py），132 个条目内嵌进本文件会让「零依赖单文件」
#     变成 400+ 行常量，且每次重标定都会产生大块无意义 diff；
#   · 表缺失/损坏时**不报错**，退回旧启发式并告警（CI 不能因缺数据而崩）。
WEIGHTS_FILENAME = "shard_time_weights.json"
# 表缺失时的兜底单位耗时（秒/条）：取表中标定值，避免两处不一致
FALLBACK_SECONDS_PER_TEST = 0.129475


def load_time_weights(root: Path) -> tuple[dict[str, float], float]:
    """读取实测耗时权重表；失败时返回空表 + 兜底单位耗时（不抛异常）。"""
    path = root / "scripts" / WEIGHTS_FILENAME
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        table = {str(k): float(v) for k, v in doc.get("files", {}).items()}
        rate = float(doc.get("calibration", {}).get("default_seconds_per_test", FALLBACK_SECONDS_PER_TEST))
    except (OSError, ValueError, TypeError, AttributeError) as exc:  # pragma: no cover - 环境异常
        print(f"warning: 无法读取 {path.name}（{exc}），退回按条数均衡", file=sys.stderr)
        return {}, FALLBACK_SECONDS_PER_TEST
    if not table:  # pragma: no cover - 环境异常
        print(f"warning: {path.name} 为空表，退回按条数均衡", file=sys.stderr)
    return table, rate


def time_weights(
    files: list[str],
    counts: dict[str, int],
    raw_counts: dict[str, int],
    table: dict[str, float],
    rate: float,
) -> dict[str, float]:
    """把「条数权重」换算成「秒数权重」：实测优先，未实测按旧启发式形状定标。

    【不易】未实测文件的定标不是随手取常数，分两步：
      1. 该群体的**总秒数**锚定为 raw_counts 合计 x rate —— rate（秒/条）
         由 CI 实测标定（见权重表 calibration.default_seconds_per_test）；
      2. 群体**内部**按旧启发式 counts 的相对比例摊分。
    这样既把量纲换成秒，又完整保留旧启发式（条数 + 行数/4）的相对排序。
    直接写 counts[f] * rate 是错的：--root tests 下 counts 叠加了行数项，
    系数比 raw_counts 大 4~5 倍，会把未实测文件整体高估，反噬均衡。
    """
    fallback = [f for f in files if f not in table]
    raw_total = sum(raw_counts[f] for f in fallback)
    shape_total = sum(counts[f] for f in fallback)
    scale = (raw_total * rate / shape_total) if shape_total else rate
    return {
        f: (table[f] if f in table else counts[f] * scale)
        for f in files
    }


def greedy_assign(files: list[str], weights: dict[str, float], shards: int) -> list[list[str]]:
    """确定性贪心：重文件优先，每次放入当前总权重最小的 shard。

    【不易】排序键显式带路径作为第二键 —— 保证同权重时按路径定序，
    与旧实现（sorted(files) 后稳定排序）结果一致，不引入非确定性。
    """
    buckets: list[list[str]] = [[] for _ in range(shards)]
    totals = [0.0] * shards
    for f in sorted(files, key=lambda x: (-weights[x], x)):
        idx = totals.index(min(totals))
        buckets[idx].append(f)
        totals[idx] += weights[f]
    return buckets


def _is_excluded(rel_path: str, excluded: set[str]) -> bool:
    """文件是否被排除：精确文件匹配，或命中目录前缀（以 / 结尾）。"""
    if rel_path in excluded:
        return True
    return any(
        rel_path.startswith(prefix)
        for prefix in excluded
        if prefix.endswith("/")
    )


def collect_test_files(root: Path, test_root: str = "tests/unit") -> list[str]:
    """收集指定子树下所有 test_*.py（排除 EXCLUDED），按路径排序。

    【不易】test_root="tests/unit" 用 glob（非递归），保持 ci.yml 行为完全一致。
    【变易】test_root="tests" 用 rglob（递归），覆盖 unit/integration/e2e/regression 全集；
            全项目模式额外排除 OBSERVABILITY_CI_ONLY（环境依赖文件，ci.yml 不触及）。
    """
    target_dir = root / test_root
    # 【不易】排除集合：全项目模式合并 OBSERVABILITY_CI_ONLY
    excluded = set(EXCLUDED)
    if test_root in ("tests", "tests/integration"):
        # 【变易·B3 2026-08-30】integration 根目录同样递归收集：与 ci.yml
        # 集成测试分片（Issue #679）配套，支持子目录 test 文件且行为与
        # `pytest tests/integration/` 的递归收集一致。
        if test_root == "tests":
            excluded |= OBSERVABILITY_CI_ONLY
        files = sorted(p for p in target_dir.rglob("test_*.py"))
    else:
        # 默认模式：仅 tests/unit/test_*.py（非递归，与 ci.yml 一致）
        files = sorted(p for p in target_dir.glob("test_*.py"))
    # 【不易】全项目模式：performance/stress 移出并行分片（独立串行 job 覆盖），
    # 目录前缀匹配防止同进程污染与计时干扰型 flake。
    if test_root == "tests":
        files = [p for p in files if not any(d in p.as_posix() for d in SERIAL_DIRS)]
    # as_posix(): CI 在 Linux runner 上执行，路径必须用正斜杠分隔
    rel = [p.relative_to(root).as_posix() for p in files]
    return [f for f in rel if not _is_excluded(f, excluded)]


def count_tests(root: Path, rel_path: str, use_lines: bool = False) -> int:
    """统计单个测试文件内的测试数（def test_* 行数，近似耗时权重）。

    【变易】use_lines：全项目模式（--root tests）下叠加行数权重。
    背景：纯测试数贪心只保证「测试数均衡」不保证「耗时均衡」——大文件
    （comprehensive 类单测耗时长）会扎堆进同一 shard。2026-08-08 实测 Shard 1
    测试数 2089 与其他 shard 完全均衡，却跑 52min 被 runner 回收（其余 shard
    4-6min）；而 slow 目录文件（chaos/performance）实测 7 个也仅 4min，测试数
    反而严重高估了这些目录。因此对全项目模式按行数加权（每 4 行 ≈ 1 测试），
    让大文件均匀分散。tests/unit 模式保持纯测试数（ci.yml 4-shard 已稳定）。
    """
    n = 0
    lines = (root / rel_path).read_text(encoding="utf-8").splitlines()
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("def test_") or stripped.startswith("async def test_"):
            n += 1
    n = max(n, 1)
    if use_lines:
        n += len(lines) // 4
    return n


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
    # 【变易·TASK-06】--by：分片依据。time=按实测耗时（默认，路线3）；
    #   count=旧的按条数/行数启发式（回退开关，行为与 2026-09-21 之前逐字节一致）
    parser.add_argument("--by", type=str, choices=("time", "count"), default="time",
                        help="分片依据：time=实测耗时（默认），count=旧版按条数（回退用）")
    args = parser.parse_args()

    if args.shard < 1 or args.shard > args.shards:
        parser.error(f"--shard 必须在 [1, {args.shards}] 内，当前 {args.shard}")

    # 【简易】白名单校验，避免误传任意路径
    if args.root not in ("tests/unit", "tests", "tests/integration"):
        parser.error(f"--root 仅支持 tests/unit、tests 或 tests/integration，当前 {args.root}")

    files = collect_test_files(ROOT, args.root)
    # 【变易·B3】tests/integration 与全项目模式一致按行数加权：
    # 大 integration 文件（90KB+）测试数少但耗时长，纯测试数贪心会扎堆失衡。
    use_lines = args.root in ("tests", "tests/integration")
    counts = {f: count_tests(ROOT, f, use_lines=use_lines) for f in files}
    if args.by == "count":
        # 【不易·回退路径】旧行为：按测试数（+行数）贪心，重文件优先。
        weights: dict[str, float] = {f: float(counts[f]) for f in files}
    else:
        # 【变易·TASK-06 路线3】新行为：按实测秒数贪心，未实测者按旧启发式
        # 形状等比定标到同一量纲（详见 time_weights 的说明）。
        table, rate = load_time_weights(ROOT)
        raw_counts = ({f: count_tests(ROOT, f, use_lines=False) for f in files}
                      if use_lines else counts)
        weights = time_weights(files, counts, raw_counts, table, rate)
    print(" ".join(greedy_assign(files, weights, args.shards)[args.shard - 1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
