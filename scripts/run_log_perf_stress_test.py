#!/usr/bin/env python3
"""日志性能压力测试 CI 守护脚本

功能：
1. 运行 perf_monitor.stress_test() 验证日志管道在多线程并发下无错误
2. 运行 perf_monitor.run_stress_comparison() 对比新旧模式，断言性能无回归
3. 阈值校验：吞吐量下限、延迟上限、错误率上限、加速比下限

CI 集成：
    python scripts/run_log_perf_stress_test.py \
        --threads 8 \
        --duration 3 \
        --min-throughput 5000 \
        --max-p99-us 500 \
        --max-error-rate 0.01 \
        --min-speedup 1.2

退出码：
    0 = 全部通过
    1 = 阈值未达标（性能回归）
    2 = 运行异常

机制说明：
- 边界显性化：阈值不达标时抛出带业务错误码的 SystemExit
- 幂等性：每次运行独立 logger，不污染全局状态
- 竞态防御：stress_test 内部已用线程独立计数器
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict


# 业务错误码
ERR_THRESHOLD = "LOG_PERF_THRESHOLD"
ERR_RUNTIME = "LOG_PERF_RUNTIME"


def _setup_path() -> None:
    """将项目根目录加入 sys.path，确保可导入 agent 模块"""
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="日志性能压力测试 CI 守护脚本"
    )
    parser.add_argument("--threads", type=int, default=8,
                        help="并发线程数（默认 8）")
    parser.add_argument("--duration", type=float, default=3.0,
                        help="每个模式测试持续时间（秒，默认 3.0）")
    parser.add_argument("--min-throughput", type=float, default=5000.0,
                        help="新模式吞吐量下限（ops/sec，默认 5000）")
    parser.add_argument("--max-p99-us", type=float, default=500.0,
                        help="新模式 p99 延迟上限（微秒，默认 500）")
    parser.add_argument("--max-error-rate", type=float, default=0.01,
                        help="错误率上限（默认 0.01 = 1%%）")
    parser.add_argument("--min-speedup", type=float, default=1.2,
                        help="新旧模式加速比下限（默认 1.2x）")
    parser.add_argument("--json-report", type=str, default=None,
                        help="JSON 报告输出路径（可选）")
    parser.add_argument("--quick", action="store_true",
                        help="快速模式（threads=2, duration=1s，用于 PR 快速验证）")
    return parser.parse_args()


def _run_stress_test(args: argparse.Namespace) -> Dict[str, Any]:
    """执行压力测试并返回结果"""
    _setup_path()

    try:
        from agent.utils.perf_monitor import stress_test, run_stress_comparison
    except ImportError as e:
        print(f"[{ERR_RUNTIME}] 无法导入 perf_monitor: {e}", file=sys.stderr)
        sys.exit(2)

    # ── 阶段 1：单独运行新模式 stress_test，验证功能正确性 ──
    print("=== 阶段 1：新模式功能正确性验证 ===")
    new_result = stress_test(
        num_threads=args.threads,
        duration_seconds=args.duration,
        use_log_dict=True,
        report_interval=None,
    )
    print(json.dumps({
        "mode": "new",
        "throughput_ops_per_sec": new_result["throughput_ops_per_sec"],
        "total_ops": new_result["total_ops"],
        "latency_us": new_result["latency_us"],
        "errors": new_result["errors"],
        "error_rate": new_result["error_rate"],
        "memory_growth_bytes": new_result["memory_growth_bytes"],
    }, indent=2, ensure_ascii=False))

    # ── 阶段 2：运行新旧模式对比 ──
    print("\n=== 阶段 2：新旧模式对比 ===")
    comparison_result = run_stress_comparison(
        num_threads=args.threads,
        duration_seconds=args.duration,
    )
    comp = comparison_result["comparison"]
    print(json.dumps(comp, indent=2, ensure_ascii=False))

    return {
        "new_result": new_result,
        "comparison": comparison_result,
    }


def _check_thresholds(result: Dict[str, Any], args: argparse.Namespace) -> list:
    """阈值校验，返回失败项列表"""
    failures = []
    new_res = result["new_result"]
    comp = result["comparison"]["comparison"]

    # 1. 吞吐量下限
    tps = new_res["throughput_ops_per_sec"]
    if tps < args.min_throughput:
        failures.append({
            "rule": "min_throughput",
            "actual": tps,
            "expected": args.min_throughput,
            "message": f"吞吐量 {tps:.1f} ops/sec 低于下限 {args.min_throughput}",
        })

    # 2. p99 延迟上限
    p99 = new_res["latency_us"]["p99"]
    if p99 > args.max_p99_us:
        failures.append({
            "rule": "max_p99_us",
            "actual": p99,
            "expected": args.max_p99_us,
            "message": f"p99 延迟 {p99:.1f}us 超过上限 {args.max_p99_us}us",
        })

    # 3. 错误率上限
    err_rate = new_res["error_rate"]
    if err_rate > args.max_error_rate:
        failures.append({
            "rule": "max_error_rate",
            "actual": err_rate,
            "expected": args.max_error_rate,
            "message": f"错误率 {err_rate:.6f} 超过上限 {args.max_error_rate}",
        })

    # 4. 加速比下限
    speedup = comp["throughput_speedup"]
    if speedup < args.min_speedup:
        failures.append({
            "rule": "min_speedup",
            "actual": speedup,
            "expected": args.min_speedup,
            "message": (
                f"加速比 {speedup:.3f}x 低于下限 {args.min_speedup}x，"
                f"新模式相比旧模式无显著提升（旧模式可能已足够快）"
            ),
        })

    return failures


def _save_report(result: Dict[str, Any], failures: list,
                 args: argparse.Namespace, path: str) -> None:
    """保存 JSON 报告"""
    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "config": {
            "threads": args.threads,
            "duration_seconds": args.duration,
            "thresholds": {
                "min_throughput": args.min_throughput,
                "max_p99_us": args.max_p99_us,
                "max_error_rate": args.max_error_rate,
                "min_speedup": args.min_speedup,
            },
        },
        "new_mode_summary": {
            "throughput_ops_per_sec": result["new_result"]["throughput_ops_per_sec"],
            "total_ops": result["new_result"]["total_ops"],
            "latency_us": result["new_result"]["latency_us"],
            "errors": result["new_result"]["errors"],
            "error_rate": result["new_result"]["error_rate"],
            "memory_growth_bytes": result["new_result"]["memory_growth_bytes"],
        },
        "comparison": result["comparison"]["comparison"],
        "threshold_check": {
            "passed": len(failures) == 0,
            "failures": failures,
        },
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\n报告已保存: {path}")


def main() -> None:
    args = _parse_args()

    # 快速模式覆盖
    if args.quick:
        args.threads = 2
        args.duration = 1.0
        args.min_throughput = 1000.0
        args.max_p99_us = 6000.0  # CI runner（共享资源）p99 普遍 5000+us，放宽到 6000us 避免环境噪声误报
        args.min_speedup = 1.0  # 快速模式放宽，避免环境噪声

    print("=" * 60)
    print("日志性能压力测试 CI 守护")
    print("=" * 60)
    print(f"线程数: {args.threads}")
    print(f"持续时间: {args.duration}s")
    print(f"阈值: 吞吐量>={args.min_throughput}, p99<={args.max_p99_us}us, "
          f"错误率<={args.max_error_rate}, 加速比>={args.min_speedup}x")
    print()

    # 执行测试
    result = _run_stress_test(args)

    # 阈值校验
    print("\n=== 阶段 3：阈值校验 ===")
    failures = _check_thresholds(result, args)

    if failures:
        print(f"\n❌ {len(failures)} 项阈值未达标:")
        for f in failures:
            print(f"  - [{f['rule']}] {f['message']}")
        if args.json_report:
            _save_report(result, failures, args, args.json_report)
        sys.exit(1)

    print("\n✅ 所有阈值达标，日志性能无回归")
    if args.json_report:
        _save_report(result, failures, args, args.json_report)
    sys.exit(0)


if __name__ == "__main__":
    main()
