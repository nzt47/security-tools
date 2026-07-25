"""CI L2 性能日志解析与可视化脚本 [TLM-L3]

用途：
- 解析 CI 日志中的 echo 方案标记 + pytest 输出 + 压测数据
- 生成可视化图表（同步 vs 异步对比、CI 护栏状态）
- 方便后续快速识别测试方案和性能趋势

支持两种输入格式：
1. CI 日志（test.yml 输出）：含 echo 标记 + pytest PASSED/FAILED
2. 压测日志（bench_l2_stress.py 输出）：含场景 C/E 的 P50/P99

运行：
    python scripts/parse_ci_l2_report.py --ci-log ci_output.log
    python scripts/parse_ci_l2_report.py --bench-log bench_async.log
    python scripts/parse_ci_l2_report.py --ci-log ci.log --bench-log bench.log --output report.png

输出：PNG 图表文件（默认 l2_perf_report.png）
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CIResult:
    """CI 日志解析结果"""
    scheme: str = ""  # 方案标识（从 echo 抓取）
    guardrails: str = ""  # 护栏阈值描述
    tests: list[dict] = field(default_factory=list)  # [{name, status, duration_ms}]
    total_passed: int = 0
    total_failed: int = 0
    total_duration: float = 0.0


@dataclass
class BenchResult:
    """压测日志解析结果"""
    scenarios: dict[str, dict] = field(default_factory=dict)  # {场景名: {P50, P99, Max}}


def parse_ci_log(file_path: str) -> CIResult:
    """解析 CI 日志：提取 echo 方案标记 + pytest 测试结果

    格式样本：
        === L2 性能回归测试 ===
        方案: 同步串行 read_fragment + 路径缓存（最优方案）
        护栏: 冷启动 P99<2s | 热启动 P99<1s | 并发 P99<2s | 缓存有效性
        ...
        test_l2_cold_start_p99_under_threshold PASSED [ 25%]
        ...
        4 passed in 7.91s
    """
    result = CIResult()
    content = Path(file_path).read_text(encoding="utf-8", errors="replace")

    # 提取方案标记
    scheme_match = re.search(r"方案:\s*(.+)", content)
    if scheme_match:
        result.scheme = scheme_match.group(1).strip()

    # 提取护栏描述
    guard_match = re.search(r"护栏:\s*(.+)", content)
    if guard_match:
        result.guardrails = guard_match.group(1).strip()

    # 提取测试结果（pytest 输出格式：test_name PASSED/FAILED [progress]）
    test_pattern = re.compile(
        r"(test_\w+)\s+(PASSED|FAILED|ERROR|SKIPPED)(?:\s+\[\s*\d+%\])?"
    )
    for match in test_pattern.finditer(content):
        name = match.group(1)
        status = match.group(2)
        result.tests.append({"name": name, "status": status})
        if status == "PASSED":
            result.total_passed += 1
        elif status in ("FAILED", "ERROR"):
            result.total_failed += 1

    # 提取总耗时（4 passed in 7.91s）
    duration_match = re.search(r"(\d+)\s+passed(?:.*?in\s+([\d.]+)s)?", content)
    if duration_match:
        result.total_duration = float(duration_match.group(2)) if duration_match.group(2) else 0.0

    return result


def parse_bench_log(file_path: str) -> BenchResult:
    """解析压测日志：提取各场景的 P50/P99/Max

    格式样本：
        【场景 C】高并发（20 个 assemble 并发，同步 IO）
            P50:    16.81ms
            P99:    99.75ms
            Max:    99.75ms
    """
    result = BenchResult()
    content = Path(file_path).read_text(encoding="utf-8", errors="replace")

    # 匹配所有【...】块作为分隔符，只有【场景 X】才采集指标
    # Why: 锁竞争统计【锁竞争统计】也含 P50/P99，需排除避免覆盖场景 C 数据
    block_pattern = re.compile(r"【([^】]+)】")
    scenario_pattern = re.compile(r"场景\s*([A-Z])")
    metric_pattern = re.compile(r"(P50|P99|Max):\s*([\d.]+)ms")

    lines = content.split("\n")
    current_scenario = None
    current_metrics: dict[str, float] = {}

    for line in lines:
        block_match = block_pattern.search(line)
        if block_match:
            # 保存上一个场景
            if current_scenario and current_metrics:
                result.scenarios[current_scenario] = current_metrics
            # 检查是否是场景块（非锁竞争统计等）
            scenario_match = scenario_pattern.search(block_match.group(1))
            if scenario_match:
                current_scenario = scenario_match.group(1)
                current_metrics = {}
            else:
                current_scenario = None  # 非场景块，不采集指标
            continue

        metric_match = metric_pattern.search(line)
        if metric_match and current_scenario:
            key = metric_match.group(1)
            value = float(metric_match.group(2))
            current_metrics[key] = value

    # 保存最后一个场景
    if current_scenario and current_metrics:
        result.scenarios[current_scenario] = current_metrics

    return result


def generate_chart(
    ci_result: Optional[CIResult],
    bench_result: Optional[BenchResult],
    output_path: str,
) -> None:
    """生成可视化图表

    布局：
    - 上半部分：压测数据对比（同步 vs 异步 P50/P99），若有压测数据
    - 下半部分：CI 护栏状态（4 个测试通过/失败），若有 CI 数据
    """
    import matplotlib
    matplotlib.use("Agg")  # 非交互式后端，适合 CI/服务器
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import numpy as np

    # 配置中文字体（Windows: SimHei/Microsoft YaHei；Linux: 文泉驿；降级 DejaVu Sans）
    plt.rcParams["font.sans-serif"] = [
        "Microsoft YaHei", "SimHei", "WenQuanYi Micro Hei", "DejaVu Sans"
    ]
    plt.rcParams["axes.unicode_minus"] = False  # 负号显示

    # 确定子图数量
    has_bench = bench_result and bench_result.scenarios
    has_ci = ci_result and ci_result.tests

    if not has_bench and not has_ci:
        print("[warn] 无可用的压测数据或 CI 数据，跳过图表生成")
        return

    fig_height = 4
    if has_bench and has_ci:
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8))
    elif has_bench:
        fig, ax1 = plt.subplots(1, 1, figsize=(10, 4))
        ax2 = None
    else:
        fig, ax2 = plt.subplots(1, 1, figsize=(10, 4))
        ax1 = None

    # ── 子图 1：压测数据对比（同步 vs 异步）──
    if has_bench and ax1:
        scenarios = bench_result.scenarios
        # 筛选有 P50/P99 的场景
        labels = []
        p50_values = []
        p99_values = []
        colors_p50 = []
        colors_p99 = []

        for name in sorted(scenarios.keys()):
            metrics = scenarios[name]
            if "P50" in metrics and "P99" in metrics:
                labels.append(f"场景 {name}")
                p50_values.append(metrics["P50"])
                p99_values.append(metrics["P99"])
                # 场景 E（异步）用红色，其他用蓝色
                color = "#e74c3c" if name == "E" else "#3498db"
                colors_p50.append(color)
                colors_p99.append(color)

        x = np.arange(len(labels))
        width = 0.35

        bars1 = ax1.bar(x - width/2, p50_values, width, label="P50", color=colors_p50, alpha=0.7)
        bars2 = ax1.bar(x + width/2, p99_values, width, label="P99", color=colors_p99, alpha=0.9)

        # 添加 1 秒阈值参考线
        ax1.axhline(y=1000, color="red", linestyle="--", linewidth=1, label="1s 阈值")

        ax1.set_xlabel("压测场景")
        ax1.set_ylabel("耗时 (ms)")
        ax1.set_title("L2 冷数据加载性能对比（同步 vs 异步）")
        ax1.set_xticks(x)
        ax1.set_xticklabels(labels)
        ax1.legend()
        ax1.grid(axis="y", alpha=0.3)

        # 在柱子上方标注数值
        for bar in bars1:
            height = bar.get_height()
            ax1.annotate(f"{height:.1f}",
                        xy=(bar.get_x() + bar.get_width() / 2, height),
                        xytext=(0, 3), textcoords="offset points",
                        ha="center", va="bottom", fontsize=8)
        for bar in bars2:
            height = bar.get_height()
            ax1.annotate(f"{height:.1f}",
                        xy=(bar.get_x() + bar.get_width() / 2, height),
                        xytext=(0, 3), textcoords="offset points",
                        ha="center", va="bottom", fontsize=8)

        # 如果有场景 C 和 E，添加对比说明
        if "C" in scenarios and "E" in scenarios:
            c_p50 = scenarios["C"].get("P50", 0)
            e_p50 = scenarios["E"].get("P50", 0)
            if c_p50 > 0:
                slowdown = e_p50 / c_p50
                ax1.text(0.02, 0.95, f"异步 IO P50 变慢 {slowdown:.1f} 倍",
                        transform=ax1.transAxes, fontsize=10,
                        verticalalignment="top",
                        bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.8))

    # ── 子图 2：CI 护栏状态 ──
    if has_ci and ax2:
        tests = ci_result.tests
        names = [t["name"].replace("test_l2_", "").replace("_p99_under_threshold", "")
                 .replace("_", "\n", 1) for t in tests]
        statuses = [t["status"] for t in tests]
        colors = ["#2ecc71" if s == "PASSED" else "#e74c3c" for s in statuses]

        bars = ax2.barh(range(len(names)), [1] * len(names), color=colors, alpha=0.7)

        ax2.set_yticks(range(len(names)))
        ax2.set_yticklabels(names, fontsize=9)
        ax2.set_xlabel("测试状态")
        ax2.set_title(f"CI 性能护栏状态（{ci_result.total_passed} 通过 / {ci_result.total_failed} 失败）")
        ax2.set_xlim(0, 1.5)

        # 在柱子上标注状态
        for i, (bar, status) in enumerate(zip(bars, statuses)):
            ax2.text(0.5, i, status, ha="center", va="center",
                    fontsize=10, fontweight="bold", color="white")

        # 显示方案标识
        if ci_result.scheme:
            ax2.text(0.98, 0.02, f"方案: {ci_result.scheme}",
                    transform=ax2.transAxes, fontsize=8,
                    verticalalignment="bottom", horizontalalignment="right",
                    bbox=dict(boxstyle="round", facecolor="lightyellow", alpha=0.8))

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"[✓] 图表已生成: {output_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="CI L2 性能日志解析与可视化")
    parser.add_argument("--ci-log", type=str, help="CI 日志文件路径（test.yml 输出）")
    parser.add_argument("--bench-log", type=str, help="压测日志文件路径（bench_l2_stress.py 输出）")
    parser.add_argument("--output", type=str, default="l2_perf_report.png",
                        help="输出图表路径（默认 l2_perf_report.png）")
    args = parser.parse_args()

    if not args.ci_log and not args.bench_log:
        print("[error] 至少指定 --ci-log 或 --bench-log")
        return 1

    ci_result = None
    bench_result = None

    # 解析 CI 日志
    if args.ci_log:
        if not Path(args.ci_log).exists():
            print(f"[error] CI 日志文件不存在: {args.ci_log}")
            return 1
        ci_result = parse_ci_log(args.ci_log)
        print(f"[CI 日志] 方案: {ci_result.scheme or '(未识别)'}")
        print(f"[CI 日志] 护栏: {ci_result.guardrails or '(未识别)'}")
        print(f"[CI 日志] 测试: {ci_result.total_passed} 通过 / {ci_result.total_failed} 失败")
        for t in ci_result.tests:
            mark = "✅" if t["status"] == "PASSED" else "❌"
            print(f"  {mark} {t['name']}: {t['status']}")

    # 解析压测日志
    if args.bench_log:
        if not Path(args.bench_log).exists():
            print(f"[error] 压测日志文件不存在: {args.bench_log}")
            return 1
        bench_result = parse_bench_log(args.bench_log)
        print(f"\n[压测日志] 场景数: {len(bench_result.scenarios)}")
        for name, metrics in sorted(bench_result.scenarios.items()):
            print(f"  场景 {name}: P50={metrics.get('P50', 0):.2f}ms "
                  f"P99={metrics.get('P99', 0):.2f}ms "
                  f"Max={metrics.get('Max', 0):.2f}ms")

    # 生成图表
    print(f"\n[生成图表] 输出: {args.output}")
    generate_chart(ci_result, bench_result, args.output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
