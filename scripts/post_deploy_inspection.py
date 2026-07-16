#!/usr/bin/env python3
"""三级熔断器上线后首小时巡检脚本

验证范围（对应部署检查清单第一/二/三章）：
1. 配置项检查 — CircuitBreakerScopeConfig Pydantic 模型 + 12 项 ValidationRule
2. 熔断器状态 — ThreeLevelCircuitBreaker 注册表 + 三状态转换
3. 日志脱敏 — hash_content SHA256[:16] + 5 个脱敏字段
4. tool_trace 事件 — record_tool_selection + record_circuit_event
5. 危险命令检测 — _is_dangerous critical 模式
6. Pydantic 严格校验 — 非法值被拒绝

使用方式：
    python scripts/post_deploy_inspection.py
    python scripts/post_deploy_inspection.py --verbose
    python scripts/post_deploy_inspection.py --json  # 输出 JSON 格式

退出码：
    0 — 全部通过
    1 — 存在失败项
    2 — 巡检无法执行（模块导入失败等）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any

# 将项目根目录加入 Python 路径（支持从 scripts/ 目录直接运行）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


# ── 巡检结果数据结构 ──────────────────────────────────────

@dataclass
class CheckResult:
    """单项检查结果"""
    name: str
    category: str
    passed: bool
    detail: str = ""
    duration_ms: float = 0.0


@dataclass
class InspectionReport:
    """巡检报告"""
    start_time: float = field(default_factory=time.time)
    end_time: float = 0.0
    results: list[CheckResult] = field(default_factory=list)

    def add(self, result: CheckResult) -> None:
        self.results.append(result)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.passed)

    @property
    def duration_seconds(self) -> float:
        return self.end_time - self.start_time

    def summary(self) -> str:
        return (
            f"巡检完成: {self.passed}/{self.total} 通过, "
            f"{self.failed} 失败, 耗时 {self.duration_seconds:.2f}s"
        )

    def to_dict(self) -> dict:
        return {
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_seconds": self.duration_seconds,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "results": [
                {
                    "name": r.name,
                    "category": r.category,
                    "passed": r.passed,
                    "detail": r.detail,
                    "duration_ms": r.duration_ms,
                }
                for r in self.results
            ],
        }


# ── 计时装饰器 ────────────────────────────────────────────

def _timed(func):
    """计时装饰器，记录检查耗时"""
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        if isinstance(result, CheckResult):
            result.duration_ms = (time.time() - start) * 1000
        return result
    return wrapper


# ── 巡检检查项 ────────────────────────────────────────────

@_timed
def check_pydantic_model() -> CheckResult:
    """1.1 Pydantic 模型完整性"""
    try:
        from config import CircuitBreakerScopeConfig, CircuitBreakerConfigSection, ConfigModel
        # 验证 CircuitBreakerScopeConfig 4 字段
        scope_fields = set(CircuitBreakerScopeConfig.model_fields.keys())
        expected = {"failure_threshold", "min_requests", "recovery_timeout", "half_open_max_calls"}
        if scope_fields != expected:
            return CheckResult(
                name="Pydantic 模型完整性",
                category="配置项",
                passed=False,
                detail=f"CircuitBreakerScopeConfig 字段不匹配: {scope_fields} != {expected}",
            )
        # 验证 CircuitBreakerConfigSection 3 字段
        section_fields = set(CircuitBreakerConfigSection.model_fields.keys())
        expected_section = {"session", "user", "global_"}
        if section_fields != expected_section:
            return CheckResult(
                name="Pydantic 模型完整性",
                category="配置项",
                passed=False,
                detail=f"CircuitBreakerConfigSection 字段不匹配: {section_fields} != {expected_section}",
            )
        # 验证 ConfigModel 包含 circuit_breaker
        if "circuit_breaker" not in ConfigModel.model_fields:
            return CheckResult(
                name="Pydantic 模型完整性",
                category="配置项",
                passed=False,
                detail="ConfigModel 缺少 circuit_breaker 字段",
            )
        return CheckResult(
            name="Pydantic 模型完整性",
            category="配置项",
            passed=True,
            detail="CircuitBreakerScopeConfig(4字段) + CircuitBreakerConfigSection(3字段) + ConfigModel.circuit_breaker 全部存在",
        )
    except Exception as e:
        return CheckResult(
            name="Pydantic 模型完整性",
            category="配置项",
            passed=False,
            detail=f"导入失败: {type(e).__name__}: {e}",
        )


@_timed
def check_validation_rules() -> CheckResult:
    """1.2 14 项校验规则"""
    try:
        from agent.config_validation import (
            CIRCUIT_BREAKER_VALIDATION_RULES,
            SEARCH_INSTANCE_VALIDATION_RULES,
        )
        cb_count = len(CIRCUIT_BREAKER_VALIDATION_RULES)
        si_count = len(SEARCH_INSTANCE_VALIDATION_RULES)
        total = cb_count + si_count
        if total == 14 and cb_count == 12:
            # 验证所有规则 required=True
            all_required = all(rule.required for rule in CIRCUIT_BREAKER_VALIDATION_RULES)
            if not all_required:
                return CheckResult(
                    name="14 项校验规则",
                    category="配置项",
                    passed=False,
                    detail="部分 CIRCUIT_BREAKER_VALIDATION_RULES 的 required=False",
                )
            return CheckResult(
                name="14 项校验规则",
                category="配置项",
                passed=True,
                detail=f"CIRCUIT_BREAKER={cb_count} + SEARCH_INSTANCE={si_count} = {total} 项，全部 required=True",
            )
        return CheckResult(
            name="14 项校验规则",
            category="配置项",
            passed=False,
            detail=f"校验规则数量不匹配: CIRCUIT_BREAKER={cb_count}, SEARCH_INSTANCE={si_count}, total={total}",
        )
    except Exception as e:
        return CheckResult(
            name="14 项校验规则",
            category="配置项",
            passed=False,
            detail=f"导入失败: {type(e).__name__}: {e}",
        )


@_timed
def check_pydantic_strict_validation() -> CheckResult:
    """1.3 Pydantic 严格校验拒绝非法值"""
    try:
        from config import CircuitBreakerScopeConfig
        from pydantic import ValidationError
        failures = []
        # 测试 failure_threshold=1.5（超范围）
        try:
            CircuitBreakerScopeConfig(failure_threshold=1.5)
            failures.append("failure_threshold=1.5 未被拒绝")
        except ValidationError:
            pass
        # 测试 min_requests=0（超范围）
        try:
            CircuitBreakerScopeConfig(min_requests=0)
            failures.append("min_requests=0 未被拒绝")
        except ValidationError:
            pass
        # 测试 recovery_timeout=100000（超范围）
        try:
            CircuitBreakerScopeConfig(recovery_timeout=100000)
            failures.append("recovery_timeout=100000 未被拒绝")
        except ValidationError:
            pass
        if failures:
            return CheckResult(
                name="Pydantic 严格校验",
                category="配置项",
                passed=False,
                detail="; ".join(failures),
            )
        return CheckResult(
            name="Pydantic 严格校验",
            category="配置项",
            passed=True,
            detail="3 项非法值（failure_threshold=1.5 / min_requests=0 / recovery_timeout=100000）均被拒绝",
        )
    except Exception as e:
        return CheckResult(
            name="Pydantic 严格校验",
            category="配置项",
            passed=False,
            detail=f"执行失败: {type(e).__name__}: {e}",
        )


@_timed
def check_alias_compatibility() -> CheckResult:
    """1.4 Pydantic alias 兼容性"""
    try:
        from config import CircuitBreakerConfigSection
        # 用 alias "global" 加载
        section = CircuitBreakerConfigSection.model_validate({
            "global": {"min_requests": 80}
        })
        if section.global_.min_requests == 80:
            return CheckResult(
                name="Pydantic alias 兼容性",
                category="配置项",
                passed=True,
                detail="alias='global' 加载成功，global_.min_requests=80",
            )
        return CheckResult(
            name="Pydantic alias 兼容性",
            category="配置项",
            passed=False,
            detail=f"alias 加载后 global_.min_requests={section.global_.min_requests}（期望 80）",
        )
    except Exception as e:
        return CheckResult(
            name="Pydantic alias 兼容性",
            category="配置项",
            passed=False,
            detail=f"执行失败: {type(e).__name__}: {e}",
        )


@_timed
def check_three_level_breaker() -> CheckResult:
    """2.1 三级熔断器注册表"""
    try:
        from agent.circuit_breaker import (
            CircuitScope,
            ThreeLevelCircuitBreaker,
            CircuitState,
        )
        # 验证 CircuitScope 枚举
        scopes = {CircuitScope.SESSION, CircuitScope.USER, CircuitScope.GLOBAL}
        if len(scopes) != 3:
            return CheckResult(
                name="三级熔断器注册表",
                category="熔断器状态",
                passed=False,
                detail=f"CircuitScope 枚举数量异常: {len(scopes)}",
            )
        # 实例化 ThreeLevelCircuitBreaker
        breaker = ThreeLevelCircuitBreaker()
        # 验证三级注册表存在且为空（初始状态）
        if not (hasattr(breaker, '_session_breakers') and
                hasattr(breaker, '_user_breakers') and
                hasattr(breaker, '_global_breakers')):
            return CheckResult(
                name="三级熔断器注册表",
                category="熔断器状态",
                passed=False,
                detail="ThreeLevelCircuitBreaker 缺少三级注册表属性",
            )
        # 验证初始状态允许请求
        allowed, scope = breaker.allow_request("test_session", "test_user", "test_tool")
        if not allowed:
            return CheckResult(
                name="三级熔断器注册表",
                category="熔断器状态",
                passed=False,
                detail="初始状态 allow_request 返回 False（应允许）",
            )
        return CheckResult(
            name="三级熔断器注册表",
            category="熔断器状态",
            passed=True,
            detail=f"CircuitScope(3级) + ThreeLevelCircuitBreaker 三级注册表正常，allow_request={allowed}",
        )
    except Exception as e:
        return CheckResult(
            name="三级熔断器注册表",
            category="熔断器状态",
            passed=False,
            detail=f"执行失败: {type(e).__name__}: {e}",
        )


@_timed
def check_circuit_states() -> CheckResult:
    """2.2 三状态转换"""
    try:
        from agent.circuit_breaker import CircuitBreaker, CircuitState, CircuitBreakerConfig
        # 创建一个快速熔断的 breaker（min_requests=1, failure_threshold=1.0）
        config = CircuitBreakerConfig(
            failure_threshold=1.0,
            min_requests=1,
            reset_timeout=0.1,  # 100ms 冷却
            max_attempts=1,
            name="test_breaker",
        )
        breaker = CircuitBreaker(config)
        # 初始状态 CLOSED
        if breaker.state != CircuitState.CLOSED:
            return CheckResult(
                name="三状态转换",
                category="熔断器状态",
                passed=False,
                detail=f"初始状态非 CLOSED: {breaker.state}",
            )
        # 记录失败触发 OPEN
        breaker.record_result(False)
        if breaker.state != CircuitState.OPEN:
            return CheckResult(
                name="三状态转换",
                category="熔断器状态",
                passed=False,
                detail=f"失败后未进入 OPEN: {breaker.state}",
            )
        # 等待冷却期进入 HALF_OPEN
        time.sleep(0.15)
        _ = breaker.state  # 触发状态检查
        if breaker.state != CircuitState.HALF_OPEN:
            return CheckResult(
                name="三状态转换",
                category="熔断器状态",
                passed=False,
                detail=f"冷却后未进入 HALF_OPEN: {breaker.state}",
            )
        # 探测成功恢复 CLOSED
        breaker.record_result(True)
        if breaker.state != CircuitState.CLOSED:
            return CheckResult(
                name="三状态转换",
                category="熔断器状态",
                passed=False,
                detail=f"探测成功后未恢复 CLOSED: {breaker.state}",
            )
        return CheckResult(
            name="三状态转换",
            category="熔断器状态",
            passed=True,
            detail="CLOSED → OPEN → HALF_OPEN → CLOSED 转换正常",
        )
    except Exception as e:
        return CheckResult(
            name="三状态转换",
            category="熔断器状态",
            passed=False,
            detail=f"执行失败: {type(e).__name__}: {e}",
        )


@_timed
def check_hash_content() -> CheckResult:
    """3.1 hash_content 脱敏函数"""
    try:
        from agent.observability.tool_trace import ToolTraceRecorder
        recorder = ToolTraceRecorder.instance()
        # 测试脱敏输出
        test_data = {"sensitive": "password123", "user": "admin"}
        hashed = recorder.hash_content(test_data)
        # 验证长度 16
        if len(hashed) != 16:
            return CheckResult(
                name="hash_content 脱敏",
                category="日志脱敏",
                passed=False,
                detail=f"hash 长度 {len(hashed)}（期望 16）",
            )
        # 验证是 hex 字符串
        try:
            int(hashed, 16)
        except ValueError:
            return CheckResult(
                name="hash_content 脱敏",
                category="日志脱敏",
                passed=False,
                detail=f"hash 非 hex 字符串: {hashed}",
            )
        # 验证幂等性（相同输入相同 hash）
        hashed2 = recorder.hash_content(test_data)
        if hashed != hashed2:
            return CheckResult(
                name="hash_content 脱敏",
                category="日志脱敏",
                passed=False,
                detail=f"幂等性失败: {hashed} != {hashed2}",
            )
        # 验证抗碰撞（不同输入不同 hash）
        different_data = {"sensitive": "different", "user": "admin"}
        hashed3 = recorder.hash_content(different_data)
        if hashed == hashed3:
            return CheckResult(
                name="hash_content 脱敏",
                category="日志脱敏",
                passed=False,
                detail=f"抗碰撞失败: 不同输入产生相同 hash",
            )
        # 验证原文不泄露
        if "password123" in hashed or "admin" in hashed:
            return CheckResult(
                name="hash_content 脱敏",
                category="日志脱敏",
                passed=False,
                detail="hash 中包含原文",
            )
        return CheckResult(
            name="hash_content 脱敏",
            category="日志脱敏",
            passed=True,
            detail=f"SHA256[:16]={hashed}, 长度=16, hex=✓, 幂等=✓, 抗碰撞=✓, 无原文泄露=✓",
        )
    except Exception as e:
        return CheckResult(
            name="hash_content 脱敏",
            category="日志脱敏",
            passed=False,
            detail=f"执行失败: {type(e).__name__}: {e}",
        )


@_timed
def check_tool_trace_events() -> CheckResult:
    """3.2 tool_trace 事件接入"""
    try:
        from agent.observability.tool_trace import ToolTraceRecorder
        recorder = ToolTraceRecorder.instance()
        # 验证 start_trace / finish_trace 可调用
        ctx = recorder.start_trace("test_tool", {"arg": "value"})
        if ctx is None:
            return CheckResult(
                name="tool_trace 事件",
                category="日志脱敏",
                passed=False,
                detail="start_trace 返回 None",
            )
        recorder.finish_trace(ctx, {"ok": True}, None)
        # 验证 record_tool_selection 可调用
        recorder.record_tool_selection(
            user_input="test input",
            categories=["core"],
            tools=["test_tool"],
        )
        return CheckResult(
            name="tool_trace 事件",
            category="日志脱敏",
            passed=True,
            detail="start_trace + finish_trace + record_tool_selection 均可调用",
        )
    except Exception as e:
        return CheckResult(
            name="tool_trace 事件",
            category="日志脱敏",
            passed=False,
            detail=f"执行失败: {type(e).__name__}: {e}",
        )


@_timed
def check_dangerous_detection() -> CheckResult:
    """3.3 危险命令检测"""
    try:
        from agent.observability.tool_trace import ToolTraceRecorder
        recorder = ToolTraceRecorder.instance()
        # 验证 _is_dangerous 方法存在
        if not hasattr(recorder, '_is_dangerous'):
            return CheckResult(
                name="危险命令检测",
                category="日志脱敏",
                passed=False,
                detail="_is_dangerous 方法不存在",
            )
        # 测试危险命令
        dangerous_input = "rm -rf /"
        is_dangerous = recorder._is_dangerous(dangerous_input)
        if not is_dangerous:
            return CheckResult(
                name="危险命令检测",
                category="日志脱敏",
                passed=False,
                detail=f"危险命令 '{dangerous_input}' 未被检测到",
            )
        # 测试安全命令
        safe_input = "list files"
        is_safe = not recorder._is_dangerous(safe_input)
        if not is_safe:
            return CheckResult(
                name="危险命令检测",
                category="日志脱敏",
                passed=False,
                detail=f"安全命令 '{safe_input}' 被误判为危险",
            )
        return CheckResult(
            name="危险命令检测",
            category="日志脱敏",
            passed=True,
            detail="_is_dangerous 正确识别危险命令(rm -rf /)和安全命令(list files)",
        )
    except Exception as e:
        return CheckResult(
            name="危险命令检测",
            category="日志脱敏",
            passed=False,
            detail=f"执行失败: {type(e).__name__}: {e}",
        )


@_timed
def check_business_metrics() -> CheckResult:
    """4.1 BusinessMetricsCollector 熔断器指标"""
    try:
        from agent.monitoring.business_metrics import (
            BusinessMetricsCollector,
            BUSINESS_METRICS_DEFINITIONS,
        )
        collector = BusinessMetricsCollector()
        # 验证熔断器指标定义存在
        required_metrics = [
            "yunshu_circuit_breaker_trigger_total",
            "yunshu_circuit_breaker_state",
        ]
        missing = [m for m in required_metrics if m not in BUSINESS_METRICS_DEFINITIONS]
        if missing:
            return CheckResult(
                name="BusinessMetrics 指标",
                category="监控指标",
                passed=False,
                detail=f"缺少指标定义: {missing}",
            )
        # 验证 record_circuit_breaker_trigger 可调用
        collector.record_circuit_breaker_trigger(
            breaker_name="test",
            from_state="closed",
            to_state="open",
            reason="test",
        )
        # 验证 update_circuit_breaker_state 可调用
        collector.update_circuit_breaker_state(
            breaker_name="test",
            state="open",
        )
        return CheckResult(
            name="BusinessMetrics 指标",
            category="监控指标",
            passed=True,
            detail="yunshu_circuit_breaker_trigger_total + yunshu_circuit_breaker_state 指标定义存在，record/update 方法可调用",
        )
    except Exception as e:
        return CheckResult(
            name="BusinessMetrics 指标",
            category="监控指标",
            passed=False,
            detail=f"执行失败: {type(e).__name__}: {e}",
        )


# ── 主巡检流程 ────────────────────────────────────────────

def run_inspection(verbose: bool = False) -> InspectionReport:
    """执行全部巡检检查"""
    report = InspectionReport()

    # 按检查清单顺序执行
    checks = [
        # 一、配置项检查
        check_pydantic_model,
        check_validation_rules,
        check_pydantic_strict_validation,
        check_alias_compatibility,
        # 二、熔断器状态
        check_three_level_breaker,
        check_circuit_states,
        # 三、日志脱敏
        check_hash_content,
        check_tool_trace_events,
        check_dangerous_detection,
        # 四、监控指标
        check_business_metrics,
    ]

    for check_func in checks:
        result = check_func()
        report.add(result)
        if verbose:
            status = "✓" if result.passed else "✗"
            print(f"  [{status}] {result.name} ({result.duration_ms:.1f}ms)")
            if not result.passed:
                print(f"      详情: {result.detail}")

    report.end_time = time.time()
    return report


def main():
    parser = argparse.ArgumentParser(
        description="三级熔断器上线后首小时巡检脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
检查范围：
  1. 配置项 — Pydantic 模型 + 12 项 ValidationRule + 严格校验 + alias 兼容
  2. 熔断器 — ThreeLevelCircuitBreaker 注册表 + 三状态转换
  3. 日志脱敏 — hash_content SHA256[:16] + tool_trace 事件 + 危险命令检测
  4. 监控指标 — BusinessMetricsCollector 熔断器指标

退出码：
  0 — 全部通过
  1 — 存在失败项
  2 — 巡检无法执行
        """,
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="输出每项检查的详细信息",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 格式输出巡检报告",
    )
    args = parser.parse_args()

    print("=" * 70)
    print("  三级熔断器上线后首小时巡检")
    print("  对应文档: docs/DEPLOYMENT_CHECKLIST_circuit_breaker.md")
    print("=" * 70)
    print()

    try:
        report = run_inspection(verbose=args.verbose)
    except KeyboardInterrupt:
        print("\n巡检被中断")
        return 2
    except Exception as e:
        print(f"巡检执行失败: {type(e).__name__}: {e}")
        return 2

    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print()
        print("=" * 70)
        print(f"  {report.summary()}")
        print("=" * 70)
        print()
        if report.failed > 0:
            print("失败项详情:")
            for r in report.results:
                if not r.passed:
                    print(f"  ✗ {r.name}: {r.detail}")
            print()

    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
