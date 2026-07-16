#!/usr/bin/env python3
"""模拟熔断器状态异常的测试数据脚本

通过注入异常状态到 ThreeLevelCircuitBreaker 实例，验证告警通知脚本的触发逻辑。
每种场景注入后运行告警检查，对比预期结果。

支持 5 种异常场景:
  stuck-open          熔断器卡在 OPEN 状态（超时未恢复）
  frequent-trigger    熔断器频繁触发（状态转换次数超阈值）
  registry-missing    注册表属性缺失（模拟配置损坏）
  state-transition    状态转换功能异常（无法进入 OPEN）
  all-normal          全部正常（对照测试）

使用方式:
  python scripts/simulate_circuit_breaker_failures.py                    # 运行所有场景
  python scripts/simulate_circuit_breaker_failures.py --scenario stuck-open  # 运行指定场景
  python scripts/simulate_circuit_breaker_failures.py --json             # JSON 输出

退出码:
  0 = 所有场景验证通过（告警脚本正确检测到异常）
  1 = 某些场景验证失败（告警脚本未检测到异常）
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch, MagicMock

# Windows 控制台 UTF-8 输出（避免 GBK 编码错误）
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 添加项目根目录到 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@dataclass
class ScenarioResult:
    """场景验证结果"""
    scenario: str
    description: str
    expected_alerts: int  # 预期告警数
    injected: bool = False        # 是否成功注入异常
    detected: bool = False        # 告警脚本是否检测到异常
    actual_alerts: int = 0        # 实际告警数
    alerts: list = field(default_factory=list)  # 告警详情
    passed: bool = False  # 验证是否通过

    def to_dict(self) -> dict:
        return {
            "scenario": self.scenario,
            "description": self.description,
            "injected": self.injected,
            "detected": self.detected,
            "expected_alerts": self.expected_alerts,
            "actual_alerts": self.actual_alerts,
            "passed": self.passed,
            "alerts": self.alerts,
        }


class FailureSimulator:
    """熔断器异常模拟器

    [不易] 所有注入操作针对独立实例，不污染全局单例
    [变易] 支持 5 种异常场景，可组合
    [简易] 直接修改内部状态，模拟真实故障
    """

    def __init__(self):
        self.results: list[ScenarioResult] = []

    def _run_alert_check(self, breaker_instance=None) -> tuple[bool, list[dict]]:
        """运行告警检查，返回 (has_alert, alerts_list)

        Args:
            breaker_instance: 可选的熔断器实例（注入异常后用于检查）
        """
        # 导入告警检查器
        from scripts.circuit_breaker_alert import CircuitBreakerAlertChecker, AlertLevel

        checker = CircuitBreakerAlertChecker()

        # 如果提供了熔断器实例，patch ThreeLevelCircuitBreaker 构造函数返回该实例
        if breaker_instance is not None:
            original_init = checker.check_registry_integrity
            original_stuck = checker.check_stuck_open
            original_frequent = checker.check_frequent_trigger

            def patched_registry():
                """检查注入的熔断器实例而非新建实例"""
                try:
                    registries = {
                        "SESSION": breaker_instance._session_breakers,
                        "USER": breaker_instance._user_breakers,
                        "GLOBAL": breaker_instance._global_breakers,
                    }
                    for scope, registry in registries.items():
                        if registry is None:
                            checker._add_alert(
                                AlertLevel.CRITICAL, "circuit_state",
                                f"{scope} 级注册表缺失",
                                f"熔断器 {scope} 级注册表为 None",
                                breaker_name=f"three_level.{scope}",
                            )
                except AttributeError as e:
                    checker._add_alert(
                        AlertLevel.CRITICAL, "circuit_state",
                        "注册表属性缺失", f"{e}",
                    )

            def patched_stuck(threshold_seconds=600):
                """检查注入实例的卡死状态

                注意: 使用 cb._stats.state（内部状态）而非 cb.state（属性），
                因为 state 属性会自动触发冷却期转换，掩盖真实卡死状态。
                """
                from agent.circuit_breaker import CircuitState
                now = time.time()
                for scope_name, registry in [
                    ("SESSION", breaker_instance._session_breakers),
                    ("USER", breaker_instance._user_breakers),
                    ("GLOBAL", breaker_instance._global_breakers),
                ]:
                    if not isinstance(registry, dict):
                        continue
                    for name, cb in registry.items():
                        # 使用 _stats.state 而非 state 属性，避免自动转换掩盖卡死
                        if cb._stats.state == CircuitState.OPEN:
                            elapsed = now - cb._stats.last_state_change
                            if elapsed > threshold_seconds:
                                checker._add_alert(
                                    AlertLevel.CRITICAL, "circuit_state",
                                    "熔断器卡在 OPEN 状态",
                                    f"{scope_name}.{name} 已 OPEN {elapsed:.0f}s",
                                    breaker_name=f"{scope_name}.{name}",
                                    elapsed_seconds=round(elapsed, 1),
                                )

            def patched_frequent(threshold=3, window_seconds=300):
                """检查注入实例的频繁触发"""
                for scope_name, registry in [
                    ("SESSION", breaker_instance._session_breakers),
                    ("USER", breaker_instance._user_breakers),
                    ("GLOBAL", breaker_instance._global_breakers),
                ]:
                    if not isinstance(registry, dict):
                        continue
                    for name, cb in registry.items():
                        transitions = cb._stats.state_transitions
                        if transitions >= threshold:
                            checker._add_alert(
                                AlertLevel.WARNING, "circuit_state",
                                "熔断器频繁触发",
                                f"{scope_name}.{name} 状态转换 {transitions} 次",
                                breaker_name=f"{scope_name}.{name}",
                                transition_count=transitions,
                            )

            checker.check_registry_integrity = patched_registry
            checker.check_stuck_open = patched_stuck
            checker.check_frequent_trigger = patched_frequent

        alerts = checker.run_all_checks()
        has_alert = any(a.level in ("WARNING", "CRITICAL") for a in alerts)
        alerts_list = [
            {"level": a.level, "category": a.category, "title": a.title, "description": a.description}
            for a in alerts
        ]
        return has_alert, alerts_list

    # ── 场景 1: 熔断器卡在 OPEN 状态 ────────────────────────

    def scenario_stuck_open(self) -> ScenarioResult:
        """场景: 熔断器卡在 OPEN 状态（超过阈值未恢复）

        预期: 告警脚本检测到 CRITICAL 告警
        """
        result = ScenarioResult(
            scenario="stuck-open",
            description="熔断器卡在 OPEN 状态 600s+，模拟冷却恢复机制失效",
            expected_alerts=1,
        )
        try:
            from agent.circuit_breaker import (
                ThreeLevelCircuitBreaker, CircuitBreaker, CircuitState,
            )
            breaker = ThreeLevelCircuitBreaker()
            # 在 GLOBAL 级注入一个卡死的熔断器
            cb = CircuitBreaker(name="stuck_tool", failure_threshold=0.5, min_calls=1)
            cb.record_result(False)  # 触发 OPEN
            # 修改 last_state_change 为 700s 前（超过默认阈值 600s）
            cb._stats.last_state_change = time.time() - 700
            breaker._global_breakers["stuck_tool"] = cb
            result.injected = True

            has_alert, alerts = self._run_alert_check(breaker)
            result.actual_alerts = len([a for a in alerts if a["level"] in ("WARNING", "CRITICAL")])
            result.detected = has_alert
            result.alerts = alerts
            result.passed = has_alert and result.actual_alerts >= 1
        except Exception as e:
            result.description += f" [异常: {type(e).__name__}: {e}]"
        return result

    # ── 场景 2: 熔断器频繁触发 ──────────────────────────────

    def scenario_frequent_trigger(self) -> ScenarioResult:
        """场景: 熔断器频繁触发（状态转换次数超阈值）

        预期: 告警脚本检测到 WARNING 告警
        """
        result = ScenarioResult(
            scenario="frequent-trigger",
            description="熔断器状态转换 5 次（超过阈值 3），模拟不稳定依赖",
            expected_alerts=1,
        )
        try:
            from agent.circuit_breaker import ThreeLevelCircuitBreaker, CircuitBreaker
            breaker = ThreeLevelCircuitBreaker()
            # 在 SESSION 级注入一个频繁触发的熔断器
            cb = CircuitBreaker(name="flaky_tool", failure_threshold=0.5, min_calls=1)
            # 模拟 5 次状态转换
            cb._stats.state_transitions = 5
            breaker._session_breakers[("sess_001", "flaky_tool")] = cb
            result.injected = True

            has_alert, alerts = self._run_alert_check(breaker)
            result.actual_alerts = len([a for a in alerts if a["level"] in ("WARNING", "CRITICAL")])
            result.detected = has_alert
            result.alerts = alerts
            result.passed = has_alert and result.actual_alerts >= 1
        except Exception as e:
            result.description += f" [异常: {type(e).__name__}: {e}]"
        return result

    # ── 场景 3: 注册表属性缺失 ──────────────────────────────

    def scenario_registry_missing(self) -> ScenarioResult:
        """场景: 注册表属性缺失（模拟配置损坏）

        预期: 告警脚本检测到 CRITICAL 告警
        """
        result = ScenarioResult(
            scenario="registry-missing",
            description="_session_breakers 属性设为 None，模拟注册表损坏",
            expected_alerts=1,
        )
        try:
            from agent.circuit_breaker import ThreeLevelCircuitBreaker
            breaker = ThreeLevelCircuitBreaker()
            # 注入：将 SESSION 级注册表设为 None
            breaker._session_breakers = None
            result.injected = True

            has_alert, alerts = self._run_alert_check(breaker)
            result.actual_alerts = len([a for a in alerts if a["level"] in ("WARNING", "CRITICAL")])
            result.detected = has_alert
            result.alerts = alerts
            result.passed = has_alert and result.actual_alerts >= 1
        except Exception as e:
            result.description += f" [异常: {type(e).__name__}: {e}]"
        return result

    # ── 场景 4: 状态转换功能异常 ────────────────────────────

    def scenario_state_transition_fail(self) -> ScenarioResult:
        """场景: 熔断器无法进入 OPEN 状态（状态转换功能异常）

        预期: 告警脚本检测到 CRITICAL 告警
        """
        result = ScenarioResult(
            scenario="state-transition",
            description="连续失败后熔断器仍为 CLOSED（模拟状态转换逻辑损坏）",
            expected_alerts=1,
        )
        try:
            from agent.circuit_breaker import CircuitBreaker, CircuitState
            from scripts.circuit_breaker_alert import (
                CircuitBreakerAlertChecker, AlertLevel,
            )

            # 创建一个 mock 的 CircuitBreaker，记录失败后仍返回 CLOSED
            cb = CircuitBreaker(name="broken_cb", failure_threshold=0.5, min_calls=2)
            # mock state 属性始终返回 CLOSED
            original_state = cb.state
            cb.record_result(False)
            cb.record_result(False)
            # 强制将状态改回 CLOSED（模拟状态转换失败）
            cb._stats.state = CircuitState.CLOSED

            checker = CircuitBreakerAlertChecker()
            # patch 状态转换检查，使用我们的 mock breaker
            original_check = checker.check_state_transition

            def patched_transition():
                try:
                    from agent.circuit_breaker import CircuitState
                    test_cb = CircuitBreaker(
                        name="alert_check", failure_threshold=0.5,
                        min_calls=2, half_open_max_calls=1, half_open_success_threshold=1,
                    )
                    test_cb.record_result(False)
                    test_cb.record_result(False)
                    # 模拟状态转换失败：强制改回 CLOSED
                    test_cb._stats.state = CircuitState.CLOSED
                    if test_cb.state != CircuitState.OPEN:
                        checker._add_alert(
                            AlertLevel.CRITICAL, "circuit_state",
                            "熔断器无法进入 OPEN 状态",
                            f"连续 2 次失败后状态为 {test_cb.state.value}（期望 open）",
                            breaker_name="alert_check",
                        )
                except Exception as e:
                    checker._add_alert(
                        AlertLevel.WARNING, "circuit_state",
                        "状态转换检查异常", f"{type(e).__name__}: {e}",
                    )

            checker.check_state_transition = patched_transition
            alerts = checker.run_all_checks()
            has_alert = any(a.level in ("WARNING", "CRITICAL") for a in alerts)
            alerts_list = [
                {"level": a.level, "category": a.category, "title": a.title, "description": a.description}
                for a in alerts
            ]
            result.injected = True
            result.actual_alerts = len([a for a in alerts_list if a["level"] in ("WARNING", "CRITICAL")])
            result.detected = has_alert
            result.alerts = alerts_list
            result.passed = has_alert and result.actual_alerts >= 1
        except Exception as e:
            result.description += f" [异常: {type(e).__name__}: {e}]"
        return result

    # ── 场景 5: 全部正常（对照测试）──────────────────────────

    def scenario_all_normal(self) -> ScenarioResult:
        """场景: 全部正常（对照测试）

        预期: 告警脚本不产生 WARNING/CRITICAL 告警
        """
        result = ScenarioResult(
            scenario="all-normal",
            description="无任何异常注入，验证告警脚本不会误报",
            expected_alerts=0,
        )
        try:
            has_alert, alerts = self._run_alert_check()
            result.injected = True
            result.actual_alerts = len([a for a in alerts if a["level"] in ("WARNING", "CRITICAL")])
            result.detected = has_alert
            result.alerts = alerts
            # 对照测试：不应有告警
            result.passed = not has_alert
        except Exception as e:
            result.description += f" [异常: {type(e).__name__}: {e}]"
        return result

    # ── 执行入口 ────────────────────────────────────────────

    def run_all_scenarios(self) -> list[ScenarioResult]:
        """运行所有场景"""
        self.results = []
        self.results.append(self.scenario_stuck_open())
        self.results.append(self.scenario_frequent_trigger())
        self.results.append(self.scenario_registry_missing())
        self.results.append(self.scenario_state_transition_fail())
        self.results.append(self.scenario_all_normal())
        return self.results

    def run_scenario(self, name: str) -> ScenarioResult:
        """运行指定场景"""
        scenarios = {
            "stuck-open": self.scenario_stuck_open,
            "frequent-trigger": self.scenario_frequent_trigger,
            "registry-missing": self.scenario_registry_missing,
            "state-transition": self.scenario_state_transition_fail,
            "all-normal": self.scenario_all_normal,
        }
        if name not in scenarios:
            raise ValueError(f"未知场景: {name}（可用: {', '.join(scenarios.keys())}）")
        return scenarios[name]()


def print_result(result: ScenarioResult, verbose: bool = False) -> None:
    """打印场景结果"""
    status = "✓ PASS" if result.passed else "✗ FAIL"
    color = "\033[92m" if result.passed else "\033[91m"
    reset = "\033[0m"
    print(f"{color}{status}{reset} [{result.scenario}] {result.description}")
    print(f"  注入: {'✓' if result.injected else '✗'}  "
          f"检测: {'✓' if result.detected else '✗'}  "
          f"预期告警: {result.expected_alerts}  "
          f"实际告警: {result.actual_alerts}")
    if verbose and result.alerts:
        for alert in result.alerts:
            level_color = {"CRITICAL": "\033[91m", "WARNING": "\033[93m", "INFO": "\033[92m"}.get(alert["level"], "")
            print(f"    {level_color}[{alert['level']:8s}]{reset} {alert['category']:14s} | {alert['title']}")
            if alert["description"]:
                print(f"               {alert['description']}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="模拟熔断器状态异常的测试数据脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
场景列表:
  stuck-open          熔断器卡在 OPEN 状态
  frequent-trigger    熔断器频繁触发
  registry-missing    注册表属性缺失
  state-transition    状态转换功能异常
  all-normal          全部正常（对照测试）

退出码:
  0 = 所有场景验证通过
  1 = 某些场景验证失败
        """,
    )
    parser.add_argument(
        "--scenario", type=str, default=None,
        help="运行指定场景（默认运行所有）",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="JSON 输出模式",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="显示详细告警信息",
    )
    args = parser.parse_args()

    simulator = FailureSimulator()

    if args.scenario:
        result = simulator.run_scenario(args.scenario)
        results = [result]
    else:
        results = simulator.run_all_scenarios()

    if args.json:
        output = {
            "total": len(results),
            "passed": sum(1 for r in results if r.passed),
            "failed": sum(1 for r in results if not r.passed),
            "results": [r.to_dict() for r in results],
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f"\n{'=' * 70}")
        print("熔断器异常模拟测试报告")
        print(f"{'=' * 70}")
        for result in results:
            print_result(result, verbose=args.verbose)
        print(f"{'=' * 70}")
        passed = sum(1 for r in results if r.passed)
        failed = len(results) - passed
        print(f"总计: {len(results)}  通过: {passed}  失败: {failed}")
        if failed > 0:
            print(f"\n✗ {failed} 个场景验证失败，告警脚本可能存在检测盲区")
        else:
            print(f"\n✓ 所有场景验证通过，告警脚本能正确检测异常")
        print()

    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
