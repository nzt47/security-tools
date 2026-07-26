"""P99 监控器：阈值检查 + 多渠道告警 [通用包]

告警渠道：
1. 控制台彩色输出
2. JSONL 告警日志文件
3. Slack webhook（可选）

可扩展：通过继承 AlertChannel 基类实现自定义告警渠道
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .parser import ScenarioData


# ── 颜色输出 ──

class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    RESET = "\033[0m"
    BOLD = "\033[1m"


def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if sys.platform == "win32":
        try:
            os.system("")
            return True
        except Exception:
            return False
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


_COLOR = _supports_color()


def _color(text: str, color: str) -> str:
    return f"{color}{text}{Colors.RESET}" if _COLOR else text


# ── 监控结果 ──

@dataclass
class MonitorResult:
    """监控检查结果"""
    data: ScenarioData           # 场景数据
    threshold: float             # 阈值（ms）
    alerted: bool                # 是否告警
    margin_pct: float            # 余量百分比（正=安全，负=超阈值）
    timestamp: str = ""          # 检查时间


# ── 告警渠道基类 ──

class AlertChannel(ABC):
    """告警渠道基类"""

    @abstractmethod
    def send(self, result: MonitorResult) -> None:
        """发送告警"""
        ...


class ConsoleChannel(AlertChannel):
    """控制台彩色输出"""

    def send(self, result: MonitorResult) -> None:
        data = result.data
        p99 = data.p99

        print()
        print(_color("=" * 60, Colors.BLUE))
        print(_color("  P99 监控报告", Colors.BOLD))
        print(_color("=" * 60, Colors.BLUE))
        print(f"  时间:     {result.timestamp}")
        print(f"  来源:     {data.source} ({data.file})")
        print(f"  场景:     {data.scenario} - {data.description}")
        if data.samples is not None:
            print(f"  样本数:   {data.samples}")
        print(f"  P50:      {data.p50:.2f}ms")
        print(f"  P99:      {p99:.2f}ms")
        print(f"  Max:      {data.pmax:.2f}ms")
        print(f"  阈值:     {result.threshold:.0f}ms")
        print(f"  状态:     ", end="")

        if result.alerted:
            ratio = p99 / result.threshold
            print(_color(f"❌ ALERT（超阈值 {ratio:.2f}x）", Colors.RED))
            print(_color(f"  建议:     检查 IO / 并发竞争 / 事件循环阻塞", Colors.YELLOW))
        else:
            print(_color(f"✅ OK（余量 {result.margin_pct:.1f}%）", Colors.GREEN))

        print(_color("=" * 60, Colors.BLUE))


class JsonlLogChannel(AlertChannel):
    """JSONL 告警日志文件（追加写入）"""

    def __init__(self, log_path: Path | str):
        self.log_path = Path(log_path)

    def send(self, result: MonitorResult) -> None:
        data = result.data
        entry = {
            "timestamp": result.timestamp,
            "status": "ALERT" if result.alerted else "OK",
            "scenario": data.scenario,
            "p50": data.p50,
            "p99": data.p99,
            "pmax": data.pmax,
            "threshold": result.threshold,
            "samples": data.samples,
            "source": data.source,
            "file": data.file,
        }
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"  [✓] 告警日志已追加: {self.log_path}")


class SlackChannel(AlertChannel):
    """Slack webhook 告警渠道

    安全：
    - webhook URL 通过环境变量传入，不硬编码
    - 强制 SSL 证书验证
    - 异常信息不泄露 webhook URL
    """

    def __init__(self, webhook_env: str = "SLACK_WEBHOOK_URL"):
        self.webhook_env = webhook_env

    def send(self, result: MonitorResult) -> None:
        # 只在告警时发送
        if not result.alerted:
            return

        webhook_url = os.environ.get(self.webhook_env)
        if not webhook_url:
            return

        data = result.data
        try:
            payload = json.dumps({
                "text": (
                    f"🚨 P99 告警\n"
                    f"  场景: {data.scenario} - {data.description}\n"
                    f"  P99: {data.p99:.2f}ms（阈值 {result.threshold:.0f}ms，超 {data.p99/result.threshold:.2f}x）\n"
                    f"  来源: {data.file}\n"
                    f"  时间: {result.timestamp}"
                )
            }).encode("utf-8")
            req = urllib.request.Request(
                webhook_url,
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            # 安全：强制 SSL 证书验证 + 短超时
            ssl_ctx = ssl.create_default_context()
            urllib.request.urlopen(req, timeout=5, context=ssl_ctx)
            print(f"  [✓] Slack 告警已发送")
        except urllib.error.HTTPError as e:
            # 安全：不打印完整异常（避免泄露 webhook URL）
            print(f"  [!] Slack 告警发送失败: HTTP {e.code}")
        except Exception:
            print(f"  [!] Slack 告警发送失败: 网络或配置错误")


# ── 监控器 ──

class P99Monitor:
    """P99 监控器：阈值检查 + 多渠道告警

    用法：
        from l2_p99_monitor import P99Monitor, create_parser, ConsoleChannel

        monitor = P99Monitor(
            parser=create_parser(scenario="C"),
            threshold=1000,
            channels=[ConsoleChannel()],
        )
        result = monitor.check_file("bench_ci.log")
        print(f"告警: {result.alerted}")
    """

    def __init__(
        self,
        parser,
        threshold: float = 1000.0,
        channels: list[AlertChannel] | None = None,
    ):
        self.parser = parser
        self.threshold = threshold
        self.channels = channels or [ConsoleChannel()]

    def check(self, data: ScenarioData) -> MonitorResult:
        """检查场景数据是否超阈值"""
        alerted = data.p99 > self.threshold
        if alerted:
            margin = (self.threshold - data.p99) / self.threshold * 100
        else:
            margin = (self.threshold - data.p99) / self.threshold * 100

        result = MonitorResult(
            data=data,
            threshold=self.threshold,
            alerted=alerted,
            margin_pct=margin,
            timestamp=datetime.now(tz=None).astimezone().isoformat(),
        )

        # 发送告警到所有渠道
        for channel in self.channels:
            channel.send(result)

        return result

    def check_file(self, file_path: Path | str) -> MonitorResult | None:
        """解析文件并检查"""
        data = self.parser.parse_file(Path(file_path))
        if not data:
            return None
        return self.check(data)
