"""
日志级别切换展示脚本（可复用）

[用途] 快速验证 cicd_metrics_push.py 在不同 LOG_LEVEL 下的日志输出差异。
[不易] 不依赖真实 pushgateway，通过 mock 隔离网络；不修改任何生产代码。
[变易] 支持 4 级别对比（DEBUG/INFO/WARNING/ERROR），一目了然看差异。
[简易] 一键运行：python scripts/test_log_level_showcase.py
[可选] 单级别运行：python scripts/test_log_level_showcase.py --level DEBUG

测试矩阵：
  DEBUG   → 输出 5 条（推送开始 + 推送参数 + registry 指标数 + 推送耗时 + 推送失败）
  INFO    → 输出 3 条（推送开始 + 推送参数 + 推送失败），DEBUG 被过滤
  WARNING → 输出 1 条（推送失败），INFO/DEBUG 被过滤
  ERROR   → 输出 1 条（推送失败），INFO/DEBUG 被过滤
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from io import StringIO
from unittest.mock import patch

# 确保能导入 cicd_metrics_push（同目录）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cicd_metrics_push  # noqa: E402


def run_with_level(level_name: str, stage: str = "build") -> str:
    """用指定日志级别运行 push()，返回捕获的日志输出。

    [不易] 模拟 cicd_metrics_push.py main() 中的日志级别解析逻辑。
    [变易] 通过 StringIO + StreamHandler 捕获日志，不影响全局 logging 配置。
    """
    log_capture = StringIO()
    handler = logging.StreamHandler(log_capture)
    handler.setLevel(logging.DEBUG)  # handler 捕获所有，由 logger.setLevel 过滤
    handler.setFormatter(logging.Formatter("%(message)s"))

    logger = cicd_metrics_push.logger
    logger.handlers = [handler]

    # 复刻 main() 中的级别解析逻辑
    level_name_upper = level_name.upper()
    level = getattr(logging, level_name_upper, logging.INFO)
    if not isinstance(level, int):
        level = logging.INFO
    logger.setLevel(level)

    # mock pushadd_to_gateway 抛异常，触发推送失败日志（展示 ERROR 级别输出）
    with patch("prometheus_client.pushadd_to_gateway",
               side_effect=ConnectionError("pushgateway 模拟不可达")):
        cicd_metrics_push.push("ci-cd-%s" % stage)

    return log_capture.getvalue()


def count_lines(output: str) -> int:
    """统计非空日志行数。"""
    return len([line for line in output.strip().split("\n") if line.strip()])


def showcase_single(level_name: str) -> None:
    """展示单个日志级别的输出。"""
    print("=" * 70)
    print("LOG_LEVEL=%s" % level_name)
    print("=" * 70)
    output = run_with_level(level_name)
    line_count = count_lines(output)
    print(output.rstrip())
    print("-" * 70)
    print("日志行数: %d" % line_count)
    print()


def showcase_all() -> None:
    """展示所有日志级别的对比。"""
    print()
    print("╔" + "═" * 68 + "╗")
    print("║" + " 日志级别切换展示：cicd_metrics_push.py 4 级别对比".center(68) + "║")
    print("╚" + "═" * 68 + "╝")

    levels = ["DEBUG", "INFO", "WARNING", "ERROR"]
    results = {}

    for level in levels:
        output = run_with_level(level)
        results[level] = {
            "output": output,
            "count": count_lines(output),
            "has_debug": "registry 指标数" in output or "推送耗时" in output,
            "has_info": "推送开始" in output,
            "has_error": "推送失败" in output,
        }

    # 逐级展示
    for level in levels:
        showcase_single(level)

    # 汇总对比表
    print("╔" + "═" * 68 + "╗")
    print("║" + " 汇总对比表".center(68) + "║")
    print("╠" + "═" * 68 + "╣")
    header = "║ %-10s %-8s %-12s %-12s %-12s ║" % (
        "LOG_LEVEL", "行数", "DEBUG日志", "INFO日志", "ERROR日志")
    print(header)
    print("╠" + "═" * 68 + "╣")
    for level in levels:
        r = results[level]
        row = "║ %-10s %-8d %-12s %-12s %-12s ║" % (
            level,
            r["count"],
            "✅ 有" if r["has_debug"] else "❌ 无",
            "✅ 有" if r["has_info"] else "❌ 无",
            "✅ 有" if r["has_error"] else "❌ 无",
        )
        print(row)
    print("╚" + "═" * 68 + "╝")

    print()
    print("说明:")
    print("  DEBUG   → 输出全部 5 条日志（含 registry 指标数 + 推送耗时）")
    print("  INFO    → 输出 3 条日志（推送开始 + 推送参数 + 推送失败），DEBUG 被过滤")
    print("  WARNING → 输出 1 条日志（推送失败），INFO/DEBUG 被过滤")
    print("  ERROR   → 输出 1 条日志（推送失败），INFO/DEBUG 被过滤")
    print()
    print("使用方式:")
    print("  ci-cd.yml 全局 env:  LOG_LEVEL: INFO  (生产推荐)")
    print("  step 级临时覆盖:     LOG_LEVEL: DEBUG (排查问题)")
    print("  命令行临时指定:      $env:LOG_LEVEL='DEBUG'; python scripts/cicd_metrics_push.py")


def main():
    parser = argparse.ArgumentParser(description="日志级别切换展示")
    parser.add_argument("--level", default=None,
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="只展示指定级别（不传则展示全部 4 级别对比）")
    args = parser.parse_args()

    if args.level:
        showcase_single(args.level)
    else:
        showcase_all()

    return 0


if __name__ == "__main__":
    sys.exit(main())
