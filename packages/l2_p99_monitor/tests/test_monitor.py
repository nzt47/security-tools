"""l2_p99_monitor 单元测试"""

from __future__ import annotations

import sys
from pathlib import Path

# 加入包路径（便于直接运行测试）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from l2_p99_monitor.parser import (
    AutoParser, BenchLogParser, ComparisonLogParser, MarkdownReportParser,
    ScenarioData, create_parser,
)
from l2_p99_monitor.monitor import (
    P99Monitor, ConsoleChannel, JsonlLogChannel, SlackChannel,
)


# ── 测试数据 ──

BENCH_LOG = """
【场景 C】高并发（5 个 assemble 并发，同步 IO）
  [concurrency=5]
    样本数: 5
    P50:    22.28ms
    P99:    390.10ms
    Max:    390.10ms
    总耗时: 575.34ms
"""

COMPARISON_LOG = """
── 全部场景概览 ──
  场景     P50(ms)          P99(ms)          Max(ms)
  --------------------------------------------------
  A        181.18           181.18           181.18
  B        21.55            21.55            21.55
  C        22.28            390.10           390.10
"""

MARKDOWN_REPORT = """\
| 场景 | 描述 | P50 (ms) | P99 (ms) | Max (ms) |
|------|------|----------|----------|----------|
| A | 冷启动 | 181.18 | 181.18 | 181.18 |
| C | 高并发 | 22.28 | 390.10 | 390.10 |
"""


# ── 解析器测试 ──

def test_bench_log_parser():
    """测试压测日志解析"""
    parser = BenchLogParser(scenario="C")
    result = parser.parse(BENCH_LOG)
    assert result is not None
    assert result.scenario == "C"
    assert result.samples == 5
    assert result.p50 == 22.28
    assert result.p99 == 390.10
    assert result.pmax == 390.10
    print("✓ test_bench_log_parser")


def test_comparison_log_parser():
    """测试对比日志解析"""
    parser = ComparisonLogParser(scenario="C")
    result = parser.parse(COMPARISON_LOG)
    assert result is not None
    assert result.scenario == "C"
    assert result.p99 == 390.10
    print("✓ test_comparison_log_parser")


def test_markdown_report_parser():
    """测试 Markdown 报告解析"""
    parser = MarkdownReportParser(scenario="C")
    result = parser.parse(MARKDOWN_REPORT)
    assert result is not None
    assert result.scenario == "C"
    assert result.p99 == 390.10
    assert result.description == "高并发"
    print("✓ test_markdown_report_parser")


def test_auto_parser():
    """测试自动检测解析"""
    parser = AutoParser(scenario="C")

    # 应该能识别 bench log
    result = parser.parse(BENCH_LOG)
    assert result is not None
    assert result.p99 == 390.10

    # 应该能识别 comparison log
    result = parser.parse(COMPARISON_LOG)
    assert result is not None
    assert result.p99 == 390.10

    # 应该能识别 markdown
    result = parser.parse(MARKDOWN_REPORT)
    assert result is not None
    assert result.p99 == 390.10

    print("✓ test_auto_parser")


def test_create_parser_factory():
    """测试解析器工厂"""
    for fmt in ["auto", "bench", "comparison", "markdown"]:
        parser = create_parser(scenario="C", fmt=fmt)
        assert parser is not None
    print("✓ test_create_parser_factory")


def test_parser_not_found():
    """测试场景不存在"""
    parser = BenchLogParser(scenario="Z")
    result = parser.parse(BENCH_LOG)
    assert result is None
    print("✓ test_parser_not_found")


# ── 监控器测试 ──

def test_monitor_ok():
    """测试正常场景（未超阈值）"""
    monitor = P99Monitor(
        parser=create_parser(scenario="C"),
        threshold=1000,
        channels=[],  # 不输出
    )
    data = ScenarioData(scenario="C", p99=500, p50=100, pmax=600)
    result = monitor.check(data)
    assert not result.alerted
    assert result.margin_pct > 0
    print("✓ test_monitor_ok")


def test_monitor_alert():
    """测试告警场景（超阈值）"""
    monitor = P99Monitor(
        parser=create_parser(scenario="C"),
        threshold=300,
        channels=[],
    )
    data = ScenarioData(scenario="C", p99=390, p50=100, pmax=400)
    result = monitor.check(data)
    assert result.alerted
    print("✓ test_monitor_alert")


def test_monitor_check_file(tmp_path=None):
    """测试文件检查"""
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False, encoding="utf-8") as f:
        f.write(BENCH_LOG)
        f.flush()
        f.close()

    monitor = P99Monitor(
        parser=create_parser(scenario="C"),
        threshold=1000,
        channels=[],
    )
    result = monitor.check_file(f.name)
    assert result is not None
    assert result.data.p99 == 390.10
    assert not result.alerted  # 390 < 1000

    import os
    os.unlink(f.name)
    print("✓ test_monitor_check_file")


def test_jsonl_log_channel(tmp_path=None):
    """测试 JSONL 告警日志"""
    import tempfile
    import json

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
        log_path = f.name
        f.close()

    monitor = P99Monitor(
        parser=create_parser(scenario="C"),
        threshold=300,  # 触发告警
        channels=[JsonlLogChannel(log_path)],
    )
    data = ScenarioData(scenario="C", p99=390, p50=100, pmax=400)
    monitor.check(data)

    # 验证日志写入
    with open(log_path, encoding="utf-8") as f:
        line = f.readline()
        entry = json.loads(line)
        assert entry["status"] == "ALERT"
        assert entry["p99"] == 390

    import os
    os.unlink(log_path)
    print("✓ test_jsonl_log_channel")


# ── 运行测试 ──

if __name__ == "__main__":
    test_bench_log_parser()
    test_comparison_log_parser()
    test_markdown_report_parser()
    test_auto_parser()
    test_create_parser_factory()
    test_parser_not_found()
    test_monitor_ok()
    test_monitor_alert()
    test_monitor_check_file()
    test_jsonl_log_channel()
    print("\n✅ 所有测试通过")
