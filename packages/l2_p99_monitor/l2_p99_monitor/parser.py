"""日志解析器：支持多种格式的 P99 数据提取 [通用包]

支持的格式：
1. bench log（压测原始日志）：【场景 X】块格式
2. comparison log（性能对比日志）：场景概览表格式
3. markdown report（Markdown 报告）：表格格式

可扩展：通过继承 LogParser 基类实现自定义解析器
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ScenarioData:
    """场景性能数据"""
    scenario: str               # 场景标识（如 "C"）
    description: str = ""       # 场景描述
    p50: float = 0.0            # P50 延迟（ms）
    p99: float = 0.0            # P99 延迟（ms）
    pmax: float = 0.0           # Max 延迟（ms）
    samples: int | None = None  # 样本数（可选）
    source: str = ""            # 数据来源标记
    file: str = ""              # 源文件路径


class LogParser(ABC):
    """日志解析器基类"""

    @abstractmethod
    def parse(self, content: str) -> ScenarioData | None:
        """解析日志内容，返回场景数据"""
        ...

    def parse_file(self, file_path: Path) -> ScenarioData | None:
        """解析日志文件"""
        content = file_path.read_text(encoding="utf-8", errors="replace")
        result = self.parse(content)
        if result:
            result.file = str(file_path)
            if not result.source:
                result.source = self.__class__.__name__.replace("Parser", "").lower()
        return result


class BenchLogParser(LogParser):
    """解析压测原始日志：【场景 X】块格式

    格式：
        【场景 C】高并发（5 个 assemble 并发，同步 IO）
          [concurrency=5]
            样本数: 5
            P50:    22.28ms
            P99:    390.10ms
            Max:    390.10ms
    """

    def __init__(self, scenario: str = "C"):
        self.scenario = scenario

    def parse(self, content: str) -> ScenarioData | None:
        pattern = re.compile(
            rf"【场景 {self.scenario}】(.*?)\n.*?\n\s+样本数:\s*(\d+).*?\n"
            rf"\s+P50:\s*([\d.]+)ms.*?\n"
            rf"\s+P99:\s*([\d.]+)ms.*?\n"
            rf"\s+Max:\s*([\d.]+)ms",
            re.DOTALL,
        )
        match = pattern.search(content)
        if not match:
            return None

        return ScenarioData(
            scenario=self.scenario,
            description=match.group(1).strip("（）"),
            samples=int(match.group(2)),
            p50=float(match.group(3)),
            p99=float(match.group(4)),
            pmax=float(match.group(5)),
            source="bench log",
        )


class ComparisonLogParser(LogParser):
    """解析性能对比日志：场景概览表格式

    格式：
        ── 全部场景概览 ──
          场景     P50(ms)          P99(ms)          Max(ms)
          --------------------------------------------------
          C        22.28            390.10           390.10
    """

    def __init__(self, scenario: str = "C"):
        self.scenario = scenario

    def parse(self, content: str) -> ScenarioData | None:
        pattern = re.compile(
            r"── 全部场景概览 ──.*?\n.*?P50.*?P99.*?Max.*?\n.*?-+.*?\n"
            r"(?:.*\n)*?"
            rf"\s*{self.scenario}\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)",
            re.DOTALL,
        )
        match = pattern.search(content)
        if not match:
            return None

        return ScenarioData(
            scenario=self.scenario,
            description="高并发同步串行",
            p50=float(match.group(1)),
            p99=float(match.group(2)),
            pmax=float(match.group(3)),
            source="comparison log",
        )


class MarkdownReportParser(LogParser):
    """解析 Markdown 报告：表格格式

    格式：
        | 场景 | 描述 | P50 (ms) | P99 (ms) | Max (ms) |
        |------|------|----------|----------|----------|
        | C | 高并发同步串行 | 22.28 | 390.10 | 390.10 |
    """

    def __init__(self, scenario: str = "C"):
        self.scenario = scenario

    def parse(self, content: str) -> ScenarioData | None:
        pattern = re.compile(
            rf"\|\s*{self.scenario}\s*\|\s*([^|]+?)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|"
        )
        match = pattern.search(content)
        if not match:
            return None

        return ScenarioData(
            scenario=self.scenario,
            description=match.group(1).strip(),
            p50=float(match.group(2)),
            p99=float(match.group(3)),
            pmax=float(match.group(4)),
            source="markdown report",
        )


class AutoParser(LogParser):
    """自动检测日志格式并解析（按优先级尝试多个解析器）"""

    def __init__(self, scenario: str = "C"):
        self.parsers = [
            BenchLogParser(scenario),
            ComparisonLogParser(scenario),
            MarkdownReportParser(scenario),
        ]

    def parse(self, content: str) -> ScenarioData | None:
        for parser in self.parsers:
            result = parser.parse(content)
            if result:
                result.source = parser.__class__.__name__.replace("Parser", "").lower()
                return result
        return None

    def parse_file(self, file_path: Path) -> ScenarioData | None:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        result = self.parse(content)
        if result:
            result.file = str(file_path)
        return result


def create_parser(scenario: str = "C", fmt: str = "auto") -> LogParser:
    """创建解析器工厂函数

    Args:
        scenario: 场景标识（默认 "C"）
        fmt: 格式（"auto" / "bench" / "comparison" / "markdown"）

    Returns: LogParser 实例
    """
    parsers = {
        "auto": AutoParser,
        "bench": BenchLogParser,
        "comparison": ComparisonLogParser,
        "markdown": MarkdownReportParser,
    }
    cls = parsers.get(fmt, AutoParser)
    return cls(scenario)
