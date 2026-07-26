"""CLI 入口：命令行接口 [通用包]

用法：
    python -m l2_p99_monitor --input bench_ci.log --threshold 1000
    python -m l2_p99_monitor --input bench_ci.log --threshold 500 --alert-log alerts.jsonl
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .parser import create_parser
from .monitor import P99Monitor, ConsoleChannel, JsonlLogChannel, SlackChannel


def main() -> int:
    parser = argparse.ArgumentParser(
        description="P99 监控告警（通用版）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input", required=True, type=str,
        help="输入日志文件",
    )
    parser.add_argument(
        "--threshold", type=float, default=1000.0,
        help="P99 告警阈值（ms，默认 1000）",
    )
    parser.add_argument(
        "--scenario", type=str, default="C",
        help="场景标识（默认 C）",
    )
    parser.add_argument(
        "--format", type=str, default="auto",
        choices=["auto", "bench", "comparison", "markdown"],
        help="日志格式（默认 auto 自动检测）",
    )
    parser.add_argument(
        "--alert-log", type=str, default="",
        help="告警日志文件路径（JSONL 格式）",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = Path.cwd() / args.input

    if not input_path.exists():
        print(f"[错误] 输入文件不存在: {input_path}", file=sys.stderr)
        return 2

    # 构建告警渠道
    channels = [ConsoleChannel()]
    if args.alert_log:
        channels.append(JsonlLogChannel(args.alert_log))
    channels.append(SlackChannel())  # 自动检测 SLACK_WEBHOOK_URL

    # 创建监控器
    monitor = P99Monitor(
        parser=create_parser(scenario=args.scenario, fmt=args.format),
        threshold=args.threshold,
        channels=channels,
    )

    # 检查文件
    result = monitor.check_file(input_path)
    if not result:
        print(f"[错误] 无法解析日志或未找到场景 {args.scenario} 数据: {input_path}", file=sys.stderr)
        return 2

    # 退出码：0=正常，1=告警
    return 1 if result.alerted else 0


if __name__ == "__main__":
    sys.exit(main())
