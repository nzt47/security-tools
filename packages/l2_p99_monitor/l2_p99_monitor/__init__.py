"""l2_p99_monitor: 通用 P99 监控告警包

功能：
- 解析多种格式的性能日志（bench log / comparison log / markdown）
- 检查 P99 是否超阈值
- 多渠道告警（控制台 / JSONL 日志 / Slack webhook）

快速开始：
    from l2_p99_monitor import P99Monitor, create_parser, ConsoleChannel, JsonlLogChannel

    monitor = P99Monitor(
        parser=create_parser(scenario="C"),
        threshold=1000,
        channels=[ConsoleChannel(), JsonlLogChannel("alerts.jsonl")],
    )
    result = monitor.check_file("bench_ci.log")
    if result and result.alerted:
        print("P99 超阈值！")
"""

from .parser import (
    LogParser, AutoParser, BenchLogParser, ComparisonLogParser, MarkdownReportParser,
    ScenarioData, create_parser,
)
from .monitor import (
    P99Monitor, MonitorResult, AlertChannel,
    ConsoleChannel, JsonlLogChannel, SlackChannel,
)

__version__ = "1.0.0"
__all__ = [
    # 解析器
    "LogParser", "AutoParser", "BenchLogParser", "ComparisonLogParser",
    "MarkdownReportParser", "ScenarioData", "create_parser",
    # 监控器
    "P99Monitor", "MonitorResult", "AlertChannel",
    "ConsoleChannel", "JsonlLogChannel", "SlackChannel",
]
