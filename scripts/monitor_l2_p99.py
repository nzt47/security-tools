#!/usr/bin/env python3
"""L2 场景 C P99 监控告警脚本 [TLM-L3]

用途：
- 解析 CI 压测日志（bench_ci.log 或 l2_switch_perf_comparison.log）
- 提取场景 C（高并发同步串行）的 P99 延迟
- 与阈值比较，超阈值时告警（控制台 + 日志 + 可选 Slack）

支持的输入格式：
1. bench_ci.log（压测原始日志）：解析【场景 C】块的 P99
2. l2_switch_perf_comparison.log（性能对比日志）：解析场景概览表中场景 C 的 P99
3. l2_switch_perf_comparison.md（Markdown 报告）：解析表格中场景 C 的 P99

告警渠道：
- 控制台彩色输出（绿=正常，红=告警）
- 告警日志文件（--alert-log，JSON 格式，便于后续分析）
- Slack webhook（环境变量 SLACK_WEBHOOK_URL，可选）

退出码：
- 0：P99 正常（未超阈值）
- 1：P99 告警（超阈值）
- 2：解析失败（日志格式不识别或场景 C 不存在）

运行示例：
    # 基本用法
    python scripts/monitor_l2_p99.py --input test_reports/bench_ci.log

    # 自定义阈值（默认 1000ms）
    python scripts/monitor_l2_p99.py --input test_reports/bench_ci.log --threshold 500

    # 告警日志 + Slack
    python scripts/monitor_l2_p99.py --input test_reports/bench_ci.log \\
        --alert-log test_reports/p99_alerts.jsonl

    # CI 集成（超阈值时 CI 失败）
    python scripts/monitor_l2_p99.py --input test_reports/bench_ci.log --threshold 2000
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path


# ── 颜色输出（Windows/Linux 兼容）──

class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    RESET = "\033[0m"
    BOLD = "\033[1m"


def _supports_color() -> bool:
    """检测终端是否支持颜色输出"""
    if os.environ.get("NO_COLOR"):
        return False
    if sys.platform == "win32":
        # Windows 10+ 支持 ANSI 颜色
        try:
            os.system("")  # 激活 ANSI 处理
            return True
        except Exception:
            return False
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


_COLOR = _supports_color()


def _color(text: str, color: str) -> str:
    return f"{color}{text}{Colors.RESET}" if _COLOR else text


# ── 日志解析 ──

def parse_bench_log(content: str) -> dict | None:
    """解析 bench_ci.log 格式：提取场景 C 的 P99

    格式：
        【场景 C】高并发（5 个 assemble 并发，同步 IO）
          [concurrency=5]
            样本数: 5
            P50:    22.28ms
            P99:    390.10ms
            Max:    390.10ms
    """
    # 匹配【场景 C】块，提取 P99
    pattern = re.compile(
        r"【场景 C】.*?\n.*?\n\s+样本数:\s*(\d+).*?\n\s+P50:\s*([\d.]+)ms.*?\n\s+P99:\s*([\d.]+)ms.*?\n\s+Max:\s*([\d.]+)ms",
        re.DOTALL,
    )
    match = pattern.search(content)
    if not match:
        return None

    return {
        "scenario": "C",
        "description": "高并发同步串行",
        "samples": int(match.group(1)),
        "p50": float(match.group(2)),
        "p99": float(match.group(3)),
        "pmax": float(match.group(4)),
    }


def parse_comparison_log(content: str) -> dict | None:
    """解析 l2_switch_perf_comparison.log 格式：提取场景概览表中场景 C 的 P99

    格式：
        ── 全部场景概览 ──
          场景     P50(ms)          P99(ms)          Max(ms)
          --------------------------------------------------
          A        181.18           181.18           181.18
          B        21.55            21.55            21.55
          C        22.28            390.10           390.10
    """
    # 匹配场景概览表中的场景 C 行
    pattern = re.compile(
        r"── 全部场景概览 ──.*?\n.*?P50.*?P99.*?Max.*?\n.*?-+.*?\n"
        r"(?:.*\n)*?"  # 跳过 A/B 行
        r"\s*C\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)",
        re.DOTALL,
    )
    match = pattern.search(content)
    if not match:
        return None

    return {
        "scenario": "C",
        "description": "高并发同步串行",
        "p50": float(match.group(1)),
        "p99": float(match.group(2)),
        "pmax": float(match.group(3)),
    }


def parse_markdown_report(content: str) -> dict | None:
    """解析 l2_switch_perf_comparison.md 格式：提取表格中场景 C 的 P99

    格式：
        | 场景 | 描述 | P50 (ms) | P99 (ms) | Max (ms) |
        |------|------|----------|----------|----------|
        | A | 冷启动（路径缓存空） | 181.18 | 181.18 | 181.18 |
        | C | 高并发同步串行 | 22.28 | 390.10 | 390.10 |
    """
    # 匹配 Markdown 表格中的场景 C 行
    pattern = re.compile(
        r"\|\s*C\s*\|.*?\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|"
    )
    match = pattern.search(content)
    if not match:
        return None

    return {
        "scenario": "C",
        "description": "高并发同步串行",
        "p50": float(match.group(1)),
        "p99": float(match.group(2)),
        "pmax": float(match.group(3)),
    }


def parse_log(file_path: Path) -> dict | None:
    """自动检测日志格式并解析场景 C 的 P99

    Returns: {"scenario": "C", "p50": ..., "p99": ..., "pmax": ..., "samples": ...} 或 None
    """
    content = file_path.read_text(encoding="utf-8", errors="replace")

    # 按优先级尝试不同解析器
    for parser in [parse_bench_log, parse_comparison_log, parse_markdown_report]:
        result = parser(content)
        if result:
            result["source"] = parser.__name__.replace("parse_", "").replace("_", " ")
            result["file"] = str(file_path)
            return result

    return None


# ── 告警逻辑 ──

def check_threshold(p99: float, threshold: float) -> bool:
    """检查 P99 是否超阈值

    Returns: True=告警（超阈值），False=正常
    """
    return p99 > threshold


def send_alert(
    data: dict,
    threshold: float,
    alerted: bool,
    alert_log: Path | None = None,
) -> None:
    """发送告警

    - 控制台彩色输出
    - 告警日志文件（JSONL 格式）
    - Slack webhook（环境变量 SLACK_WEBHOOK_URL）
    """
    timestamp = datetime.now(tz=None).astimezone().isoformat()
    p99 = data["p99"]
    status = "ALERT" if alerted else "OK"

    # 控制台输出
    print()
    print(_color("=" * 60, Colors.BLUE))
    print(_color(f"  L2 场景 C P99 监控报告", Colors.BOLD))
    print(_color("=" * 60, Colors.BLUE))
    print(f"  时间:     {timestamp}")
    print(f"  来源:     {data.get('source', 'unknown')} ({data.get('file', '?')})")
    print(f"  场景:     {data['scenario']} - {data['description']}")
    if "samples" in data:
        print(f"  样本数:   {data['samples']}")
    print(f"  P50:      {data['p50']:.2f}ms")
    print(f"  P99:      {p99:.2f}ms")
    print(f"  Max:      {data['pmax']:.2f}ms")
    print(f"  阈值:     {threshold:.0f}ms")
    print(f"  状态:     ", end="")

    if alerted:
        ratio = p99 / threshold
        print(_color(f"❌ ALERT（超阈值 {ratio:.2f}x）", Colors.RED))
        print(_color(f"  建议:     检查 CI 环境磁盘 IO / 并发竞争 / 事件循环阻塞", Colors.YELLOW))
    else:
        margin = (threshold - p99) / threshold * 100
        print(_color(f"✅ OK（余量 {margin:.1f}%）", Colors.GREEN))

    print(_color("=" * 60, Colors.BLUE))

    # 告警日志文件（JSONL 格式，便于后续分析）
    if alert_log:
        alert_entry = {
            "timestamp": timestamp,
            "status": status,
            "scenario": data["scenario"],
            "p50": data["p50"],
            "p99": p99,
            "pmax": data["pmax"],
            "threshold": threshold,
            "samples": data.get("samples"),
            "source": data.get("source"),
            "file": data.get("file"),
        }
        with alert_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(alert_entry, ensure_ascii=False) + "\n")
        print(f"\n  [✓] 告警日志已追加: {alert_log}")

    # Slack webhook（可选）
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if webhook_url and alerted:
        try:
            import urllib.request
            payload = json.dumps({
                "text": (
                    f"🚨 L2 P99 告警\n"
                    f"  场景: C（高并发同步串行）\n"
                    f"  P99: {p99:.2f}ms（阈值 {threshold:.0f}ms，超 {p99/threshold:.2f}x）\n"
                    f"  来源: {data.get('file', '?')}\n"
                    f"  时间: {timestamp}"
                )
            }).encode("utf-8")
            req = urllib.request.Request(
                webhook_url,
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=10)
            print(f"  [✓] Slack 告警已发送")
        except Exception as e:
            print(f"  [!] Slack 告警发送失败: {e}")


# ── 主入口 ──

def main() -> int:
    parser = argparse.ArgumentParser(
        description="L2 场景 C P99 监控告警",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--input", required=True, type=str,
        help="输入日志文件（bench_ci.log / comparison.log / .md）",
    )
    parser.add_argument(
        "--threshold", type=float, default=1000.0,
        help="P99 告警阈值（ms，默认 1000）",
    )
    parser.add_argument(
        "--alert-log", type=str, default="",
        help="告警日志文件路径（JSONL 格式，追加写入）",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = Path.cwd() / args.input

    if not input_path.exists():
        print(f"[错误] 输入文件不存在: {input_path}", file=sys.stderr)
        return 2

    # 解析日志
    data = parse_log(input_path)
    if not data:
        print(f"[错误] 无法解析日志或未找到场景 C 数据: {input_path}", file=sys.stderr)
        print(f"       支持的格式: bench_ci.log / comparison.log / .md", file=sys.stderr)
        return 2

    # 检查阈值
    alerted = check_threshold(data["p99"], args.threshold)

    # 发送告警
    alert_log = Path(args.alert_log) if args.alert_log else None
    send_alert(data, args.threshold, alerted, alert_log)

    # 退出码：0=正常，1=告警
    return 1 if alerted else 0


if __name__ == "__main__":
    sys.exit(main())
