"""kwarg_scanner CLI 入口 — 命令行接口

用法:
    kwarg-scan --path src/ --min-risk HIGH
    kwarg-scan --path src/ --format json --output report.json
    kwarg-scan --path src/ --min-risk MEDIUM --enable-logging
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List

from .types import ScanConfig, RiskLevel
from .scanner import KwargScanner
from .reporter import format_text_report, format_json_report


def create_parser() -> argparse.ArgumentParser:
    """创建 CLI 参数解析器"""
    parser = argparse.ArgumentParser(
        prog="kwarg-scan",
        description=(
            "Python 关键字参数冲突风险静态扫描器 — "
            "检测 func(explicit_kwarg=x, **dict) 模式中 "
            "dict 含同名键的冲突风险"
        ),
        epilog=(
            "示例:\n"
            "  kwarg-scan --path src/ --min-risk HIGH\n"
            "  kwarg-scan --path src/ --format json --output report.json\n"
            "  kwarg-scan --path src/ --exclude venv,node_modules,build\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--path", "-p", default=".", required=False,
        help="扫描路径（文件或目录，默认: 当前目录）",
    )
    parser.add_argument(
        "--format", "-f", choices=["text", "json"], default="text",
        help="输出格式（默认: text）",
    )
    parser.add_argument(
        "--min-risk", "-m",
        choices=["LOW", "MEDIUM", "HIGH"], default="LOW",
        help="最低报告风险等级（默认: LOW=全部报告）",
    )
    parser.add_argument(
        "--output", "-o", default=None,
        help="输出文件路径（默认: 控制台）",
    )
    parser.add_argument(
        "--exclude", "-e", default=None,
        help="额外排除目录（逗号分隔，如: venv,node_modules）",
    )
    parser.add_argument(
        "--enable-logging", action="store_true",
        help="输出结构化 JSON 日志到 stderr",
    )
    parser.add_argument(
        "--version", "-v", action="version", version="kwarg-scanner 1.0.0",
    )
    return parser


def main(argv: List[str] = None) -> int:
    """CLI 主入口

    Args:
        argv: 命令行参数（None 使用 sys.argv）

    Returns:
        int — 退出码（0=通过，1=有 HIGH 风险）
    """
    parser = create_parser()
    args = parser.parse_args(argv)

    # 构建配置
    config = ScanConfig(
        min_risk=RiskLevel.from_str(args.min_risk),
        enable_logging=args.enable_logging,
    )

    # 额外排除目录
    if args.exclude:
        extra_excludes = set(args.exclude.split(","))
        config.exclude_dirs = config.exclude_dirs | extra_excludes

    # 扫描
    scanner = KwargScanner(config)
    findings = scanner.scan(args.path)

    # 生成报告
    if args.format == "json":
        report = format_json_report(findings)
    else:
        report = format_text_report(findings)

    # 输出
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(report, encoding="utf-8")
        print(f"报告已写入: {args.output}")
    else:
        print(report)

    # 退出码: 有 HIGH 风险则返回 1
    high_count = sum(1 for f in findings if f.risk_level == "HIGH")
    return 1 if high_count > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
