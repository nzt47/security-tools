#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""本地模拟 GitHub Actions workflow_dispatch 触发场景

模拟 observability-ci.yml 中 chaos-tests job 的完整逻辑：
1. 解析 workflow_dispatch 输入参数（test_scope / verbose / max_fail）
2. 根据 event_name 和输入参数构建 pytest 命令
3. 执行测试并解析结果
4. 输出手动触发确认信息

用法:
    # 默认参数（chaos-only, verbose=true, max_fail=0）
    python scripts/simulate_workflow_dispatch.py

    # 指定参数
    python scripts/simulate_workflow_dispatch.py --scope chaos-and-p2 --verbose --max-fail 0

    # 模拟 schedule 触发（非 workflow_dispatch）
    python scripts/simulate_workflow_dispatch.py --event schedule

    # 模拟 pull_request 触发
    python scripts/simulate_workflow_dispatch.py --event pull_request
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:
    """解析命令行参数，模拟 workflow_dispatch 的输入参数"""
    parser = argparse.ArgumentParser(
        description="本地模拟 GitHub Actions workflow_dispatch 触发场景",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 模拟 workflow_dispatch + chaos-only
  python scripts/simulate_workflow_dispatch.py --event workflow_dispatch --scope chaos-only

  # 模拟 workflow_dispatch + chaos-and-p2
  python scripts/simulate_workflow_dispatch.py --event workflow_dispatch --scope chaos-and-p2

  # 模拟 schedule 触发
  python scripts/simulate_workflow_dispatch.py --event schedule
        """,
    )
    parser.add_argument(
        "--event",
        choices=["workflow_dispatch", "schedule", "pull_request"],
        default="workflow_dispatch",
        help="模拟的 GitHub Actions 事件类型（默认: workflow_dispatch）",
    )
    parser.add_argument(
        "--scope",
        choices=["chaos-only", "chaos-and-p2", "all-chaos-including-slow"],
        default="chaos-only",
        help="测试范围（仅 workflow_dispatch 时生效，默认: chaos-only）",
    )
    parser.add_argument(
        "--verbose",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="详细输出（默认: True）",
    )
    parser.add_argument(
        "--max-fail",
        default="0",
        help="最大失败数后停止（0=不限制，默认: 0）",
    )
    parser.add_argument(
        "--actor",
        default="local-tester",
        help="模拟的触发者（默认: local-tester）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印参数和命令，不实际执行测试",
    )
    return parser.parse_args()


def build_pytest_args(event_name: str, scope: str, verbose: bool, max_fail: str) -> list[str]:
    """根据触发方式和输入参数构建 pytest 参数列表

    对标 CI 中 "确定测试参数" step 的逻辑，但使用列表而非字符串，
    避免引号传递 bug。
    """
    # 根据事件类型确定参数
    if event_name == "workflow_dispatch":
        actual_scope = scope
        actual_verbose = verbose
        actual_max_fail = max_fail
    else:
        # schedule 和 pull_request 使用默认值
        actual_scope = "chaos-only"
        actual_verbose = True
        actual_max_fail = "0"

    # 构建参数列表（使用列表避免引号问题）
    args: list[str] = []

    if actual_scope == "chaos-only":
        args.append("tests/chaos/")
    elif actual_scope == "chaos-and-p2":
        args.extend(["tests/chaos/", "tests/unit/test_impact_analysis_cache.py", "-m", "chaos or p2"])
    elif actual_scope == "all-chaos-including-slow":
        args.extend(["tests/chaos/", "--runslow"])

    if actual_verbose:
        args.extend(["-v", "--tb=short"])
    else:
        args.extend(["-q", "--tb=line"])

    if actual_max_fail != "0":
        args.extend(["--maxfail", actual_max_fail])

    args.append("-p")
    args.append("no:cacheprovider")

    return args


def run_pytest(args: list[str]) -> tuple[int, str]:
    """执行 pytest 并返回退出码和输出

    Returns:
        (exit_code, stdout_output)
    """
    cmd = [sys.executable, "-m", "pytest"] + args
    print(f"\n[模拟] 执行命令: {' '.join(cmd)}\n")
    print("=" * 60)

    result = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )

    # 打印完整输出
    print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr)

    return result.returncode, result.stdout


def parse_test_results(output: str) -> dict[str, int]:
    """从 pytest 输出中解析通过/失败/跳过数量

    对标 CI 中 "解析测试结果" step 的 grep 逻辑。
    """
    results = {"passed": 0, "failed": 0, "skipped": 0}

    # 匹配 "36 passed" / "5 failed" / "81 skipped"
    passed_match = re.search(r"(\d+) passed", output)
    failed_match = re.search(r"(\d+) failed", output)
    skipped_match = re.search(r"(\d+) skipped", output)

    if passed_match:
        results["passed"] = int(passed_match.group(1))
    if failed_match:
        results["failed"] = int(failed_match.group(1))
    if skipped_match:
        results["skipped"] = int(skipped_match.group(1))

    return results


def print_confirmation(
    event_name: str,
    actor: str,
    scope: str,
    verbose: bool,
    max_fail: str,
    results: dict[str, int],
    exit_code: int,
    duration_ms: float,
) -> None:
    """输出手动触发确认信息

    对标 CI 中 "手动触发确认信息" step 的输出格式。
    """
    branch = "master"
    commit = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=str(PROJECT_ROOT),
        text=True,
    ).strip()

    print("\n" + "=" * 60)
    print(f"  {event_name} 触发确认")
    print("=" * 60)
    print(f"触发者: {actor}")
    print(f"分支: {branch}")
    print(f"提交: {commit}")
    print(f"测试范围: {scope}")
    print(f"详细输出: {verbose}")
    print(f"最大失败: {max_fail}")
    print("-" * 60)
    print(f"耗时: {duration_ms:.0f}ms")
    print("测试结果:")
    print(f"  通过: {results['passed']}")
    print(f"  失败: {results['failed']}")
    print(f"  跳过: {results['skipped']}")
    print(f"  退出码: {exit_code}")
    print("=" * 60)

    if event_name == "workflow_dispatch":
        print()
        if results["failed"] == 0:
            print("[OK] workflow_dispatch 手动触发已生效！")
            print("[OK] 测试范围选择功能正常！")
            print("[OK] 结果解析功能正常！")
            print("[OK] 参数传递链路完整：inputs -> 参数构建 -> pytest 执行 -> 结果解析")
        else:
            print("[WARN] workflow_dispatch 手动触发已生效，但测试存在失败！")
            print(f"[WARN] 失败数: {results['failed']}（不阻塞，continue-on-error: true）")


def main() -> int:
    """主入口：模拟 workflow_dispatch 触发流程"""
    args = parse_args()

    print("=" * 60)
    print("  GitHub Actions workflow_dispatch 本地模拟器")
    print("=" * 60)
    print(f"模拟事件: {args.event}")
    print(f"测试范围: {args.scope}")
    print(f"详细输出: {args.verbose}")
    print(f"最大失败: {args.max_fail}")
    print(f"触发者:   {args.actor}")
    print(f"项目根:   {PROJECT_ROOT}")
    print("=" * 60)

    # 构建参数
    pytest_args = build_pytest_args(args.event, args.scope, args.verbose, args.max_fail)
    print(f"\n构建的 pytest 参数: {pytest_args}")

    if args.dry_run:
        print("\n[dry-run] 仅打印参数，不执行测试")
        print(f"  完整命令: python -m pytest {' '.join(pytest_args)}")
        return 0

    # 执行测试
    start = time.time()
    exit_code, output = run_pytest(pytest_args)
    duration_ms = (time.time() - start) * 1000

    # 解析结果
    results = parse_test_results(output)

    # 输出确认信息
    print_confirmation(
        event_name=args.event,
        actor=args.actor,
        scope=args.scope if args.event == "workflow_dispatch" else "chaos-only",
        verbose=args.verbose if args.event == "workflow_dispatch" else True,
        max_fail=args.max_fail if args.event == "workflow_dispatch" else "0",
        results=results,
        exit_code=exit_code,
        duration_ms=duration_ms,
    )

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
