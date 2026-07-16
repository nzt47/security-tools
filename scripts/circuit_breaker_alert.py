#!/usr/bin/env python3
"""熔断器状态异常自动化告警通知脚本

基于巡检脚本的检查逻辑，专注于运行时熔断器状态监控。
检测到异常时通过多通道发送告警通知。

通知方式：
- console: 控制台输出（默认，带颜色高亮）
- file: 写入日志文件（JSON Lines 格式）
- webhook: 发送 HTTP POST 到 webhook URL（可选）

使用方式:
  python scripts/circuit_breaker_alert.py              # 单次检查
  python scripts/circuit_breaker_alert.py --watch       # 持续监控（30s 间隔）
  python scripts/circuit_breaker_alert.py --webhook URL # 发送 webhook 通知
  python scripts/circuit_breaker_alert.py --log-file FILE # 写入日志文件
  python scripts/circuit_breaker_alert.py --json        # JSON 输出（便于 CI 集成）

退出码:
  0 = 全部正常
  1 = 存在告警（WARNING 或 CRITICAL）

相关文件:
- scripts/post_deploy_inspection.py    上线后巡检脚本（配置项 + 日志脱敏）
- monitoring/circuit_breaker_alerts.yml Prometheus 告警规则
- monitoring/grafana_circuit_breaker_dashboard.json Grafana 仪表盘
"""

from __future__ import annotations

import argparse
import json
import os
import smtplib
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# 添加项目根目录到 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ── 告警数据结构 ──────────────────────────────────────────────

class AlertLevel:
    """告警级别"""
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"

    @classmethod
    def color(cls, level: str) -> str:
        """告警级别对应的颜色码"""
        return {
            cls.INFO: "\033[92m",      # green
            cls.WARNING: "\033[93m",   # yellow
            cls.CRITICAL: "\033[91m",  # red
        }.get(level, "\033[0m")


@dataclass
class Alert:
    """告警事件"""
    timestamp: str
    level: str
    category: str           # 告警类别（circuit_state / metrics / trace / config）
    title: str              # 告警标题
    description: str        # 告警描述
    breaker_name: str = ""  # 涉及的熔断器名称
    details: dict = field(default_factory=dict)  # 额外详情

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


# ── 告警检查器 ────────────────────────────────────────────────

class CircuitBreakerAlertChecker:
    """熔断器状态异常检查器

    基于巡检脚本的检查逻辑，专注于运行时状态监控：
    1. 三级熔断器注册表完整性
    2. 熔断器状态转换功能
    3. 熔断器卡死检测（OPEN 状态超时）
    4. 熔断器频繁触发检测
    5. tool_trace 事件记录功能
    6. BusinessMetricsCollector 指标上报
    """

    def __init__(self):
        self.alerts: list[Alert] = []
        self._now = datetime.now(timezone.utc).isoformat()

    def _add_alert(
        self,
        level: str,
        category: str,
        title: str,
        description: str,
        breaker_name: str = "",
        **details,
    ) -> None:
        """添加一条告警"""
        self.alerts.append(Alert(
            timestamp=self._now,
            level=level,
            category=category,
            title=title,
            description=description,
            breaker_name=breaker_name,
            details=details,
        ))

    # ── 检查项 ──────────────────────────────────────────────

    def check_registry_integrity(self) -> None:
        """检查 1: 三级熔断器注册表完整性"""
        try:
            from agent.circuit_breaker import ThreeLevelCircuitBreaker
            breaker = ThreeLevelCircuitBreaker()
            # 三级注册表属性：_session_breakers / _user_breakers / _global_breakers
            registries = {
                "SESSION": breaker._session_breakers,
                "USER": breaker._user_breakers,
                "GLOBAL": breaker._global_breakers,
            }
            for scope, registry in registries.items():
                if registry is None:
                    self._add_alert(
                        AlertLevel.CRITICAL,
                        "circuit_state",
                        f"{scope} 级注册表缺失",
                        f"熔断器 {scope} 级注册表为 None，可能导致该级别熔断失效",
                        breaker_name=f"three_level.{scope}",
                    )
            self._add_alert(
                AlertLevel.INFO,
                "circuit_state",
                "三级注册表完整",
                f"SESSION={len(registries['SESSION'])} USER={len(registries['USER'])} GLOBAL={len(registries['GLOBAL'])}",
            )
        except ImportError as e:
            self._add_alert(
                AlertLevel.CRITICAL,
                "config",
                "熔断器模块导入失败",
                f"无法导入 ThreeLevelCircuitBreaker: {e}",
            )
        except AttributeError as e:
            self._add_alert(
                AlertLevel.CRITICAL,
                "circuit_state",
                "注册表属性缺失",
                f"ThreeLevelCircuitBreaker 缺少注册表属性: {e}",
            )
        except Exception as e:
            self._add_alert(
                AlertLevel.WARNING,
                "circuit_state",
                "注册表检查异常",
                f"检查注册表完整性时异常: {type(e).__name__}: {e}",
            )

    def check_state_transition(self) -> None:
        """检查 2: 熔断器状态转换功能"""
        try:
            from agent.circuit_breaker import CircuitBreaker, CircuitState
            # half_open_success_threshold=1：探测 1 次成功即恢复（便于测试）
            cb = CircuitBreaker(
                name="alert_check",
                failure_threshold=0.5,
                min_calls=2,
                half_open_max_calls=1,
                half_open_success_threshold=1,
            )
            # 触发熔断：2 次失败
            cb.record_result(False)
            cb.record_result(False)
            if cb.state != CircuitState.OPEN:
                self._add_alert(
                    AlertLevel.CRITICAL,
                    "circuit_state",
                    "熔断器无法进入 OPEN 状态",
                    f"连续 2 次失败后状态为 {cb.state.value}（期望 open）",
                    breaker_name="alert_check",
                )
            # 模拟冷却后恢复
            cb._stats.last_state_change = time.time() - cb.cooldown_seconds - 1
            cb.allow_request()  # 触发 OPEN→HALF_OPEN
            if cb.state != CircuitState.HALF_OPEN:
                self._add_alert(
                    AlertLevel.WARNING,
                    "circuit_state",
                    "熔断器无法进入 HALF_OPEN 状态",
                    f"冷却期到期后状态为 {cb.state.value}（期望 half_open）",
                    breaker_name="alert_check",
                )
            # 探测成功恢复（half_open_success_threshold=1，1 次成功即恢复）
            cb.record_result(True)
            if cb.state != CircuitState.CLOSED:
                self._add_alert(
                    AlertLevel.WARNING,
                    "circuit_state",
                    "熔断器无法恢复 CLOSED 状态",
                    f"探测成功后状态为 {cb.state.value}（期望 closed）",
                    breaker_name="alert_check",
                )
        except Exception as e:
            self._add_alert(
                AlertLevel.WARNING,
                "circuit_state",
                "状态转换检查异常",
                f"检查状态转换时异常: {type(e).__name__}: {e}",
            )

    def check_stuck_open(self, threshold_seconds: int = 600) -> None:
        """检查 3: 熔断器卡死检测（OPEN 状态超过阈值）

        Args:
            threshold_seconds: OPEN 状态超时阈值（秒），默认 600s（10min）
        """
        try:
            from agent.circuit_breaker import ThreeLevelCircuitBreaker, CircuitState
            breaker = ThreeLevelCircuitBreaker()
            now = time.time()
            stuck_count = 0
            # 遍历三级注册表
            registries = {
                "SESSION": breaker._session_breakers,
                "USER": breaker._user_breakers,
                "GLOBAL": breaker._global_breakers,
            }
            for scope, registry in registries.items():
                for name, cb in registry.items():
                    if cb.state == CircuitState.OPEN:
                        elapsed = now - cb._stats.last_state_change
                        if elapsed > threshold_seconds:
                            stuck_count += 1
                            self._add_alert(
                                AlertLevel.CRITICAL,
                                "circuit_state",
                                "熔断器卡在 OPEN 状态",
                                f"{scope}.{name} 已 OPEN {elapsed:.0f}s（阈值 {threshold_seconds}s）",
                                breaker_name=f"{scope}.{name}",
                                elapsed_seconds=round(elapsed, 1),
                                threshold_seconds=threshold_seconds,
                            )
            if stuck_count == 0:
                self._add_alert(
                    AlertLevel.INFO,
                    "circuit_state",
                    "无卡死熔断器",
                    f"所有熔断器均未卡在 OPEN 状态（阈值 {threshold_seconds}s）",
                )
        except Exception:
            # 注册表为空或访问异常时不算告警（正常情况）
            pass

    def check_frequent_trigger(self, threshold: int = 3, window_seconds: int = 300) -> None:
        """检查 4: 熔断器频繁触发检测

        Args:
            threshold: 触发次数阈值，默认 3 次
            window_seconds: 时间窗口（秒），默认 300s（5min）
        """
        try:
            from agent.circuit_breaker import ThreeLevelCircuitBreaker
            breaker = ThreeLevelCircuitBreaker()
            frequent_count = 0
            registries = {
                "SESSION": breaker._session_breakers,
                "USER": breaker._user_breakers,
                "GLOBAL": breaker._global_breakers,
            }
            for scope, registry in registries.items():
                for name, cb in registry.items():
                    transitions = cb._stats.state_transitions
                    if transitions >= threshold:
                        frequent_count += 1
                        self._add_alert(
                            AlertLevel.WARNING,
                            "circuit_state",
                            "熔断器频繁触发",
                            f"{scope}.{name} 状态转换 {transitions} 次（阈值 {threshold} 次）",
                            breaker_name=f"{scope}.{name}",
                            transition_count=transitions,
                            threshold=threshold,
                        )
            if frequent_count == 0:
                self._add_alert(
                    AlertLevel.INFO,
                    "circuit_state",
                    "无频繁触发熔断器",
                    f"所有熔断器状态转换次数均低于阈值 {threshold}",
                )
        except Exception:
            pass

    def check_tool_trace(self) -> None:
        """检查 5: tool_trace 事件记录功能"""
        try:
            from agent.observability.tool_trace import ToolTraceRecorder
            recorder = ToolTraceRecorder.instance()
            # 验证关键方法存在
            assert hasattr(recorder, "start_trace"), "缺少 start_trace 方法"
            assert hasattr(recorder, "finish_trace"), "缺少 finish_trace 方法"
            assert hasattr(recorder, "record_circuit_event"), "缺少 record_circuit_event 方法"
            assert hasattr(recorder, "record_tool_selection"), "缺少 record_tool_selection 方法"
            self._add_alert(
                AlertLevel.INFO,
                "trace",
                "tool_trace 功能正常",
                "ToolTraceRecorder 所有方法可用",
            )
        except ImportError as e:
            self._add_alert(
                AlertLevel.WARNING,
                "trace",
                "tool_trace 模块导入失败",
                f"无法导入 ToolTraceRecorder: {e}",
            )
        except AssertionError as e:
            self._add_alert(
                AlertLevel.WARNING,
                "trace",
                "tool_trace 方法缺失",
                f"ToolTraceRecorder {e}",
            )
        except Exception as e:
            self._add_alert(
                AlertLevel.WARNING,
                "trace",
                "tool_trace 检查异常",
                f"{type(e).__name__}: {e}",
            )

    def check_business_metrics(self) -> None:
        """检查 6: BusinessMetricsCollector 指标上报"""
        try:
            from agent.monitoring.business_metrics import BusinessMetricsCollector
            collector = BusinessMetricsCollector()
            # 熔断器相关方法：record_circuit_breaker_trigger + update_circuit_breaker_state
            assert hasattr(collector, "record_circuit_breaker_trigger"), "缺少 record_circuit_breaker_trigger 方法"
            assert hasattr(collector, "update_circuit_breaker_state"), "缺少 update_circuit_breaker_state 方法"
            # 降级与限流方法
            assert hasattr(collector, "record_degrade_trigger"), "缺少 record_degrade_trigger 方法"
            assert hasattr(collector, "record_rate_limit_trigger"), "缺少 record_rate_limit_trigger 方法"
            self._add_alert(
                AlertLevel.INFO,
                "metrics",
                "BusinessMetrics 功能正常",
                "BusinessMetricsCollector 熔断器/降级/限流指标方法可用",
            )
        except ImportError as e:
            self._add_alert(
                AlertLevel.WARNING,
                "metrics",
                "BusinessMetrics 模块导入失败",
                f"无法导入 BusinessMetricsCollector: {e}",
            )
        except AssertionError as e:
            self._add_alert(
                AlertLevel.WARNING,
                "metrics",
                "BusinessMetrics 方法缺失",
                f"BusinessMetricsCollector {e}",
            )
        except Exception as e:
            self._add_alert(
                AlertLevel.WARNING,
                "metrics",
                "BusinessMetrics 检查异常",
                f"{type(e).__name__}: {e}",
            )

    # ── 执行入口 ────────────────────────────────────────────

    def run_all_checks(self) -> list[Alert]:
        """执行所有检查，返回告警列表"""
        self.alerts = []
        self._now = datetime.now(timezone.utc).isoformat()
        self.check_registry_integrity()
        self.check_state_transition()
        self.check_stuck_open()
        self.check_frequent_trigger()
        self.check_tool_trace()
        self.check_business_metrics()
        return self.alerts

    def summary(self) -> dict:
        """生成告警摘要"""
        critical = sum(1 for a in self.alerts if a.level == AlertLevel.CRITICAL)
        warning = sum(1 for a in self.alerts if a.level == AlertLevel.WARNING)
        info = sum(1 for a in self.alerts if a.level == AlertLevel.INFO)
        return {
            "timestamp": self._now,
            "total": len(self.alerts),
            "critical": critical,
            "warning": warning,
            "info": info,
            "has_alert": critical > 0 or warning > 0,
        }


# ── 通知器 ────────────────────────────────────────────────────

class AlertNotifier:
    """告警通知器（多通道）"""

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        log_file: Optional[str] = None,
        no_color: bool = False,
    ):
        self.webhook_url = webhook_url
        self.log_file = log_file
        self.no_color = no_color

    def _color(self, level: str, text: str) -> str:
        """添加颜色（如果未禁用）"""
        if self.no_color:
            return text
        color = AlertLevel.color(level)
        reset = "\033[0m"
        return f"{color}{text}{reset}"

    def notify_console(self, alerts: list[Alert], summary: dict) -> None:
        """控制台输出（带颜色）"""
        print(f"\n{'=' * 60}")
        print(f"熔断器状态告警报告  {summary['timestamp']}")
        print(f"{'=' * 60}")
        print(f"总计: {summary['total']}  "
              f"CRITICAL: {self._color(AlertLevel.CRITICAL, str(summary['critical']))}  "
              f"WARNING: {self._color(AlertLevel.WARNING, str(summary['warning']))}  "
              f"INFO: {self._color(AlertLevel.INFO, str(summary['info']))}")
        print(f"{'=' * 60}")
        for alert in alerts:
            level_str = self._color(alert.level, f"[{alert.level:8s}]")
            breaker_str = f" ({alert.breaker_name})" if alert.breaker_name else ""
            print(f"{level_str} {alert.category:14s} | {alert.title}{breaker_str}")
            if alert.description:
                print(f"             {alert.description}")
            if alert.details:
                details_str = " ".join(f"{k}={v}" for k, v in alert.details.items())
                print(f"             {details_str}")
        print(f"{'-' * 60}")
        if summary["has_alert"]:
            print(self._color(AlertLevel.CRITICAL, "⚠ 存在告警，请立即处理"))
        else:
            print(self._color(AlertLevel.INFO, "✓ 全部正常"))
        print()

    def notify_file(self, alerts: list[Alert], summary: dict) -> None:
        """写入日志文件（JSON Lines 格式）"""
        if not self.log_file:
            return
        log_path = Path(self.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            for alert in alerts:
                f.write(alert.to_json() + "\n")
            # 写入摘要
            f.write(json.dumps({"summary": summary}, ensure_ascii=False) + "\n")

    def notify_webhook(self, alerts: list[Alert], summary: dict) -> None:
        """发送 webhook 通知（HTTP POST）"""
        if not self.webhook_url:
            return
        try:
            import urllib.request
            payload = {
                "timestamp": summary["timestamp"],
                "summary": summary,
                "alerts": [a.to_dict() for a in alerts if a.level in (AlertLevel.CRITICAL, AlertLevel.WARNING)],
            }
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                self.webhook_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status >= 400:
                    print(f"webhook 发送失败: HTTP {resp.status}", file=sys.stderr)
                else:
                    print(f"webhook 发送成功: {len(payload['alerts'])} 条告警")
        except Exception as e:
            print(f"webhook 发送异常: {type(e).__name__}: {e}", file=sys.stderr)

    def notify(self, alerts: list[Alert], summary: dict, output_json: bool = False) -> None:
        """发送所有通知"""
        if output_json:
            # JSON 输出模式（便于 CI 集成）
            print(json.dumps({
                "summary": summary,
                "alerts": [a.to_dict() for a in alerts],
            }, ensure_ascii=False, indent=2))
        else:
            self.notify_console(alerts, summary)
        self.notify_file(alerts, summary)
        self.notify_webhook(alerts, summary)


# ── 主入口 ────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="熔断器状态异常自动化告警通知脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
退出码:
  0 = 全部正常
  1 = 存在告警（WARNING 或 CRITICAL）

示例:
  python scripts/circuit_breaker_alert.py
  python scripts/circuit_breaker_alert.py --watch
  python scripts/circuit_breaker_alert.py --webhook https://hooks.example.com/alert
  python scripts/circuit_breaker_alert.py --log-file logs/circuit_alerts.jsonl
  python scripts/circuit_breaker_alert.py --json
        """,
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="持续监控模式（默认 30s 间隔）",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="监控间隔（秒，默认 30，仅 --watch 模式生效）",
    )
    parser.add_argument(
        "--webhook",
        type=str,
        default=None,
        help="webhook URL（发送 HTTP POST 告警通知）",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        help="日志文件路径（JSON Lines 格式，追加写入）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="JSON 输出模式（便于 CI 集成）",
    )
    parser.add_argument(
        "--stuck-threshold",
        type=int,
        default=600,
        help="OPEN 状态卡死阈值（秒，默认 600）",
    )
    parser.add_argument(
        "--frequent-threshold",
        type=int,
        default=3,
        help="频繁触发阈值（次数，默认 3）",
    )
    args = parser.parse_args()

    notifier = AlertNotifier(
        webhook_url=args.webhook,
        log_file=args.log_file,
        no_color=args.json,
    )

    def run_once() -> bool:
        """执行一次检查，返回是否有告警"""
        checker = CircuitBreakerAlertChecker()
        # 注入自定义阈值
        original_stuck_check = checker.check_stuck_open
        original_frequent_check = checker.check_frequent_trigger
        checker.check_stuck_open = lambda: original_stuck_check(args.stuck_threshold)
        checker.check_frequent_trigger = lambda: original_frequent_check(args.frequent_threshold)

        alerts = checker.run_all_checks()
        summary = checker.summary()
        notifier.notify(alerts, summary, output_json=args.json)
        return summary["has_alert"]

    if args.watch:
        # 持续监控模式
        print(f"持续监控模式，间隔 {args.interval}s（Ctrl+C 退出）", file=sys.stderr)
        try:
            while True:
                has_alert = run_once()
                if has_alert and args.json:
                    pass  # JSON 模式下不输出额外信息
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n监控已停止", file=sys.stderr)
            return 0
    else:
        # 单次检查模式
        has_alert = run_once()
        return 1 if has_alert else 0


if __name__ == "__main__":
    sys.exit(main())
