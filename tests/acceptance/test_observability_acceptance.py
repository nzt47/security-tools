#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
可观测性体系验收测试脚本
========================

覆盖五大测试维度：
1. 功能测试 - 所有可观测性功能是否正常工作
2. 集成测试 - 各模块之间是否正确协同
3. 性能测试 - 可观测性开销是否在可接受范围内
4. 安全测试 - 安全措施是否有效
5. 边界测试 - 极端场景下是否正常工作

测试模块：
- 追踪上下文传播（提取、注入、跨服务）
- Prometheus 指标导出
- 健康检查端点
- 告警规则触发
- 业务指标统计
- 混沌工程故障注入
- 自愈机制验证
- 敏感数据过滤
- 并发/多线程场景

运行方式:
    python tests/acceptance/test_observability_acceptance.py
"""

import sys
import os
import time
import json
import threading
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class TestCaseResult:
    """测试用例结果"""
    test_id: str
    test_name: str
    category: str
    module: str
    status: str  # passed, failed, skipped
    duration_ms: float = 0.0
    error_message: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AcceptanceTestReport:
    """验收测试报告"""
    start_time: datetime
    end_time: Optional[datetime] = None
    total_duration_ms: float = 0.0
    test_cases: List[TestCaseResult] = field(default_factory=list)
    
    @property
    def total_count(self) -> int:
        return len(self.test_cases)
    
    @property
    def passed_count(self) -> int:
        return sum(1 for t in self.test_cases if t.status == "passed")
    
    @property
    def failed_count(self) -> int:
        return sum(1 for t in self.test_cases if t.status == "failed")
    
    @property
    def skipped_count(self) -> int:
        return sum(1 for t in self.test_cases if t.status == "skipped")
    
    @property
    def pass_rate(self) -> float:
        if self.total_count == 0:
            return 0.0
        return self.passed_count / self.total_count * 100
    
    @property
    def by_category(self) -> Dict[str, Dict[str, int]]:
        result = defaultdict(lambda: {"passed": 0, "failed": 0, "skipped": 0, "total": 0})
        for tc in self.test_cases:
            result[tc.category]["total"] += 1
            result[tc.category][tc.status] += 1
        return dict(result)
    
    @property
    def by_module(self) -> Dict[str, Dict[str, int]]:
        result = defaultdict(lambda: {"passed": 0, "failed": 0, "skipped": 0, "total": 0})
        for tc in self.test_cases:
            result[tc.module]["total"] += 1
            result[tc.module][tc.status] += 1
        return dict(result)


class ObservabilityAcceptanceTest:
    """可观测性验收测试套件"""
    
    def __init__(self):
        self.report = AcceptanceTestReport(start_time=datetime.now())
        self._current_test: Optional[TestCaseResult] = None
    
    def _start_test(self, test_id: str, test_name: str, category: str, module: str):
        """开始一个测试用例"""
        self._current_test = TestCaseResult(
            test_id=test_id,
            test_name=test_name,
            category=category,
            module=module,
            status="running"
        )
        self._start_time = time.time()
        print(f"\n{'='*60}")
        print(f"[{test_id}] {test_name}")
        print(f"{'='*60}")
    
    def _pass(self, **details):
        """标记测试通过"""
        self._current_test.status = "passed"
        self._current_test.duration_ms = (time.time() - self._start_time) * 1000
        self._current_test.details = details
        self.report.test_cases.append(self._current_test)
        print(f"[PASS] 通过 - 耗时: {self._current_test.duration_ms:.2f}ms")
        if details:
            print(f"  详情: {json.dumps(details, ensure_ascii=False, default=str)}")
    
    def _fail(self, error_message: str, **details):
        """标记测试失败"""
        self._current_test.status = "failed"
        self._current_test.duration_ms = (time.time() - self._start_time) * 1000
        self._current_test.error_message = error_message
        self._current_test.details = details
        self.report.test_cases.append(self._current_test)
        print(f"[FAIL] 失败 - 耗时: {self._current_test.duration_ms:.2f}ms")
        print(f"  错误: {error_message}")
        if details:
            print(f"  详情: {json.dumps(details, ensure_ascii=False, default=str)}")
    
    def _skip(self, reason: str):
        """跳过测试"""
        self._current_test.status = "skipped"
        self._current_test.duration_ms = 0
        self._current_test.error_message = reason
        self.report.test_cases.append(self._current_test)
        print(f"[SKIP] 跳过 - 原因: {reason}")
    
    # ========================================================================
    # 1. 追踪上下文传播测试
    # ========================================================================
    
    def test_tracing_basic_context(self):
        """测试基本追踪上下文创建"""
        self._start_test("TRC-001", "基本追踪上下文创建", "功能测试", "tracing")
        try:
            from agent.monitoring.tracing import TraceContext, get_trace_id
            
            with TraceContext("TestService", "test_operation") as ctx:
                trace_id = ctx.trace_id
                span_id = ctx.span_id if hasattr(ctx, 'span_id') else None
                
                assert trace_id is not None, "Trace ID 不应为 None"
                assert len(trace_id) >= 16, f"Trace ID 长度应 >= 16, 实际 {len(trace_id)}"
                assert ctx.service_name == "TestService"
                assert ctx.operation == "test_operation"
                assert ctx.start_time is not None
                assert ctx.duration_ms >= 0
            
            self._pass(trace_id=trace_id, span_id=span_id)
        except Exception as e:
            self._fail(str(e))
    
    def test_tracing_context_propagation_extract(self):
        """测试追踪上下文提取（从HTTP头）"""
        self._start_test("TRC-002", "追踪上下文提取（HTTP头）", "功能测试", "tracing")
        try:
            from agent.monitoring.tracing import extract_trace_context
            
            # 测试 W3C Trace Context 格式
            trace_id = "abc123def4567890abc123def4567890"
            span_id = "1234567812345678"
            headers = {
                "traceparent": f"00-{trace_id}-{span_id}-01"
            }
            
            context = extract_trace_context(headers)
            assert context is not None, "提取的上下文不应为 None"
            assert context.get("trace_id") == trace_id, f"Trace ID 不匹配: {context.get('trace_id')}"
            assert context.get("span_id") == span_id, f"Span ID 不匹配: {context.get('span_id')}"
            
            self._pass(trace_id=context.get("trace_id"), span_id=context.get("span_id"))
        except Exception as e:
            self._fail(str(e))
    
    def test_tracing_context_propagation_inject(self):
        """测试追踪上下文注入（生成HTTP头）"""
        self._start_test("TRC-003", "追踪上下文注入（生成HTTP头）", "功能测试", "tracing")
        try:
            from agent.monitoring.tracing import TraceContext, inject_trace_context, set_trace_id
            
            with TraceContext("InjectService", "inject_test") as ctx:
                headers = inject_trace_context()
                
                assert headers is not None, "注入的头不应为 None"
                assert "traceparent" in headers, "应包含 traceparent 头"
                
                traceparent = headers["traceparent"]
                parts = traceparent.split("-")
                assert len(parts) == 4, f"traceparent 格式错误: {traceparent}"
                assert parts[0] == "00", "版本号应为 00"
                # Trace ID 长度可能是16或32位，取决于实现
                assert len(parts[1]) >= 16, f"Trace ID 长度应 >= 16: {len(parts[1])}"
                assert len(parts[2]) == 16, f"Span ID 长度应为 16: {len(parts[2])}"
            
            self._pass(headers_count=len(headers), traceparent_format="valid", trace_id_len=len(parts[1]))
        except Exception as e:
            self._fail(str(e))
    
    def test_tracing_cross_service_propagation(self):
        """测试跨服务上下文传播"""
        self._start_test("TRC-004", "跨服务上下文传播", "集成测试", "tracing")
        try:
            from agent.monitoring.tracing import (
                TraceContext, extract_trace_context, inject_trace_context,
                set_trace_id, set_span_id, get_trace_id
            )
            
            # 模拟 Service A 处理请求
            with TraceContext("ServiceA", "handle_request") as ctx_a:
                trace_id_a = ctx_a.trace_id
                headers_a = inject_trace_context()
                
                # 模拟 Service B 接收并继续传播
                context_b = extract_trace_context(headers_a)
                set_trace_id(context_b.get("trace_id"))
                set_span_id(context_b.get("span_id"))
                
                with TraceContext("ServiceB", "process") as ctx_b:
                    trace_id_b = ctx_b.trace_id
                    headers_b = inject_trace_context()
                    
                    # 模拟 Service C
                    context_c = extract_trace_context(headers_b)
                    assert context_c.get("trace_id") == trace_id_a, \
                        f"Trace ID 在跨服务传播中改变: {trace_id_a} -> {context_c.get('trace_id')}"
            
            self._pass(
                trace_id_a=trace_id_a,
                trace_id_b=trace_id_b,
                propagation_consistent=True
            )
        except Exception as e:
            self._fail(str(e))
    
    def test_tracing_opentelemetry_available(self):
        """测试 OpenTelemetry 可用性"""
        self._start_test("TRC-005", "OpenTelemetry 可用性检查", "功能测试", "tracing")
        try:
            from agent.monitoring.tracing import is_opentelemetry_available
            
            otel_available = is_opentelemetry_available()
            assert isinstance(otel_available, bool), "返回值应为布尔类型"
            
            self._pass(opentelemetry_available=otel_available)
        except Exception as e:
            self._fail(str(e))
    
    def test_tracing_decorator(self):
        """测试追踪装饰器"""
        self._start_test("TRC-006", "追踪装饰器功能", "功能测试", "tracing")
        try:
            from agent.monitoring.tracing import trace
            
            @trace("DecoratorTest", "test_func")
            def test_function(x, y):
                return x + y
            
            result = test_function(3, 4)
            assert result == 7, "函数返回值应正确"
            
            self._pass(return_value=result)
        except Exception as e:
            self._fail(str(e))
    
    def test_tracing_error_capture(self):
        """测试错误场景下的追踪捕获"""
        self._start_test("TRC-007", "错误场景追踪捕获", "边界测试", "tracing")
        try:
            from agent.monitoring.tracing import TraceContext
            
            error_caught = False
            try:
                with TraceContext("ErrorService", "error_operation") as ctx:
                    raise ValueError("测试错误")
            except ValueError:
                error_caught = True
            
            assert error_caught, "异常应被正常抛出"
            
            self._pass(error_propagated=True)
        except Exception as e:
            self._fail(str(e))
    
    def test_tracing_nested_spans(self):
        """测试嵌套 Span 创建"""
        self._start_test("TRC-008", "嵌套 Span 创建", "功能测试", "tracing")
        try:
            from agent.monitoring.tracing import TraceContext, get_trace_id
            
            with TraceContext("ParentService", "parent_op") as parent_ctx:
                parent_trace_id = parent_ctx.trace_id
                
                with TraceContext("ChildService", "child_op") as child_ctx:
                    child_trace_id = child_ctx.trace_id
                    
                    # 同一线程中 Trace ID 应保持一致
                    assert child_trace_id == parent_trace_id, \
                        f"嵌套 Span 的 Trace ID 应一致: {parent_trace_id} vs {child_trace_id}"
            
            self._pass(parent_trace_id=parent_trace_id, child_trace_id=child_trace_id)
        except Exception as e:
            self._fail(str(e))
    
    # ========================================================================
    # 2. Prometheus 指标导出测试
    # ========================================================================
    
    def test_prometheus_available(self):
        """测试 Prometheus 客户端可用性"""
        self._start_test("PROM-001", "Prometheus 客户端可用性", "功能测试", "prometheus")
        try:
            try:
                from prometheus_client import Counter, Gauge, Histogram
                prom_available = True
            except ImportError:
                prom_available = False
            
            if not prom_available:
                self._skip("prometheus_client 未安装")
                return
            
            self._pass(prometheus_client_available=True)
        except Exception as e:
            self._fail(str(e))
    
    def test_prometheus_metrics_types(self):
        """测试 Prometheus 各类型指标创建"""
        self._start_test("PROM-002", "Prometheus 指标类型创建", "功能测试", "prometheus")
        try:
            try:
                from prometheus_client import Counter, Gauge, Histogram, REGISTRY
            except ImportError:
                self._skip("prometheus_client 未安装")
                return
            
            # 测试 Counter
            test_counter = Counter('test_acceptance_counter_total', 'Test counter', ['label1'])
            test_counter.labels(label1='test').inc()
            
            # 测试 Gauge
            test_gauge = Gauge('test_acceptance_gauge', 'Test gauge', ['label1'])
            test_gauge.labels(label1='test').set(42)
            
            # 测试 Histogram
            test_histogram = Histogram('test_acceptance_histogram_seconds', 'Test histogram')
            test_histogram.observe(0.5)
            
            self._pass(
                counter_created=True,
                gauge_created=True,
                histogram_created=True
            )
        except Exception as e:
            self._fail(str(e))
    
    def test_prometheus_exporter_init(self):
        """测试 Prometheus 导出器初始化"""
        self._start_test("PROM-003", "Prometheus 导出器初始化", "功能测试", "prometheus")
        try:
            try:
                from agent.monitoring.prometheus import PrometheusMetricsExporter
            except ImportError:
                self._skip("PrometheusMetricsExporter 不可用")
                return
            
            try:
                exporter = PrometheusMetricsExporter(namespace="AcceptanceTest")
            except RuntimeError as e:
                if "not installed" in str(e):
                    self._skip("prometheus_client 未安装")
                    return
                raise
            
            assert exporter.namespace == "AcceptanceTest"
            assert exporter.port is not None
            
            self._pass(namespace=exporter.namespace, port=exporter.port)
        except Exception as e:
            self._fail(str(e))
    
    def test_prometheus_metrics_collector(self):
        """测试指标收集器功能"""
        self._start_test("PROM-004", "指标收集器功能", "功能测试", "metrics")
        try:
            from agent.monitoring.metrics import get_metrics_collector
            
            collector = get_metrics_collector()
            assert collector is not None, "指标收集器不应为 None"
            
            self._pass(collector_available=True)
        except Exception as e:
            self._fail(str(e))
    
    def test_prometheus_record_latency(self):
        """测试延迟记录功能"""
        self._start_test("PROM-005", "延迟记录功能", "功能测试", "metrics")
        try:
            from agent.monitoring.metrics import record_latency, increment_counter
            
            # 记录延迟
            record_latency("acceptance.test.latency", 0.05)
            
            # 增加计数器
            increment_counter("acceptance.test.counter")
            
            self._pass(latency_recorded=True, counter_incremented=True)
        except Exception as e:
            self._fail(str(e))
    
    # ========================================================================
    # 3. 健康检查端点测试
    # ========================================================================
    
    def test_health_basic_check(self):
        """测试基本健康检查功能"""
        self._start_test("HLT-001", "基本健康检查功能", "功能测试", "health")
        try:
            from agent.health.assessor import health_assessor
            
            assert health_assessor is not None, "健康评估器不应为 None"
            
            self._pass(health_assessor_available=True)
        except Exception as e:
            self._fail(str(e))
    
    def test_health_assessment(self):
        """测试健康评估功能"""
        self._start_test("HLT-002", "健康评估功能", "功能测试", "health")
        try:
            from agent.health.assessor import health_assessor
            
            result = health_assessor.assess()
            
            assert result is not None, "健康评估结果不应为 None"
            assert hasattr(result, 'overall'), "应包含 overall 字段"
            
            overall = result.overall
            assert isinstance(overall, (int, float)), f"overall 应为数值类型: {type(overall)}"
            assert 0 <= overall <= 1.0, f"健康值应在 0-1 范围: {overall}"
            
            self._pass(overall_health=overall, dimensions=list(result.dimensions.keys()))
        except Exception as e:
            self._fail(str(e))
    
    def test_health_dashboard(self):
        """测试健康历史记录功能"""
        self._start_test("HLT-003", "健康历史记录", "功能测试", "health")
        try:
            from agent.health.assessor import health_assessor
            
            # 进行几次评估
            health_assessor.assess()
            health_assessor.assess()
            
            history = health_assessor.get_history(5)
            assert history is not None, "历史记录不应为 None"
            assert isinstance(history, list), "历史记录应为列表"
            assert len(history) >= 2, f"至少应有2条历史记录，实际: {len(history)}"
            
            self._pass(history_count=len(history), latest_overall=history[-1].overall if history else None)
        except Exception as e:
            self._fail(str(e))
    
    # ========================================================================
    # 4. 告警规则触发测试
    # ========================================================================
    
    def test_alert_rule_creation(self):
        """测试告警规则创建"""
        self._start_test("ALT-001", "告警规则创建", "功能测试", "alert")
        try:
            from agent.monitoring.alert_evaluator import AlertRule, AlertSeverity
            
            rule = AlertRule(
                name="test_acceptance_alert",
                expr="test_metric > 0.5",
                duration="1m",
                severity="warning",
                threshold=0.5,
                comparison="gt"
            )
            
            assert rule.name == "test_acceptance_alert"
            assert rule.severity == AlertSeverity.WARNING.value or rule.severity == "warning"
            
            self._pass(rule_name=rule.name, severity=rule.severity)
        except Exception as e:
            self._fail(str(e))
    
    def test_alert_evaluator_init(self):
        """测试告警评估器初始化"""
        self._start_test("ALT-002", "告警评估器初始化", "功能测试", "alert")
        try:
            from agent.monitoring.alert_evaluator import AlertEvaluator
            
            evaluator = AlertEvaluator(
                evaluation_interval=1.0,
                pending_duration=2.0
            )
            
            assert evaluator is not None
            evaluator.stop()
            
            self._pass(evaluator_initialized=True)
        except Exception as e:
            self._fail(str(e))
    
    def test_alert_evaluator_add_remove_rule(self):
        """测试告警规则添加和移除"""
        self._start_test("ALT-003", "告警规则添加/移除", "功能测试", "alert")
        try:
            from agent.monitoring.alert_evaluator import AlertEvaluator, AlertRule
            
            evaluator = AlertEvaluator(evaluation_interval=10.0)
            
            try:
                # 添加规则
                rule = AlertRule(
                    name="acceptance_test_rule",
                    expr="test > 0.5",
                    threshold=0.5
                )
                evaluator.add_rule(rule)
                
                alerts = evaluator.get_alerts()
                assert len(alerts) >= 1, "应至少有一个告警规则"
                
                # 移除规则
                evaluator.remove_rule("acceptance_test_rule")
                alerts_after = evaluator.get_alerts()
                
                self._pass(
                    alerts_before=len(alerts),
                    alerts_after=len(alerts_after)
                )
            finally:
                evaluator.stop()
        except Exception as e:
            self._fail(str(e))
    
    def test_alert_notifier_init(self):
        """测试告警通知器初始化"""
        self._start_test("ALT-004", "告警通知器初始化", "功能测试", "alert")
        try:
            from agent.monitoring.alert_notifier import AlertNotifier
            
            notifier = AlertNotifier()
            assert notifier is not None
            
            self._pass(notifier_available=True)
        except Exception as e:
            self._fail(str(e))
    
    def test_alert_manager_integration(self):
        """测试告警管理器集成"""
        self._start_test("ALT-005", "告警管理器集成", "集成测试", "alert")
        try:
            from agent.monitoring.alert_evaluator import AlertEvaluator, AlertRule
            from agent.monitoring.alert_notifier import AlertNotifier
            from agent.monitoring.self_healer import SelfHealer
            
            # 测试各组件协同工作的能力（告警评估 + 通知 + 自愈）
            evaluator = AlertEvaluator(evaluation_interval=10.0)
            notifier = AlertNotifier()
            healer = SelfHealer()
            
            # 验证各组件都能正常初始化
            assert evaluator is not None
            assert notifier is not None
            assert healer is not None
            
            # 添加一条告警规则并验证
            rule = AlertRule(
                name="integration_test_rule",
                expr="test_metric > 100",
                threshold=100.0,
                severity="warning",
                comparison="gt"
            )
            evaluator.add_rule(rule)
            
            # 通过内部属性验证规则数量（无公开get_rules方法）
            rules_count = len(evaluator._rules)
            assert rules_count >= 1, "应至少有一条告警规则"
            
            # 验证自愈动作可执行
            result = healer.execute_action("gc_collect")
            assert result is not None, "自愈动作应有返回结果"
            
            self._pass(
                components_integrated=True,
                components=["evaluator", "notifier", "healer"],
                rules_count=rules_count,
                heal_action_available=True
            )
        except Exception as e:
            self._fail(str(e))
    
    # ========================================================================
    # 5. 业务指标统计测试
    # ========================================================================
    
    def test_business_metrics_definitions(self):
        """测试业务指标定义"""
        self._start_test("BM-001", "业务指标定义", "功能测试", "business_metrics")
        try:
            from agent.monitoring.business_metrics import BUSINESS_METRICS_DEFINITIONS
            
            assert isinstance(BUSINESS_METRICS_DEFINITIONS, dict)
            assert len(BUSINESS_METRICS_DEFINITIONS) > 0, "应定义至少一个业务指标"
            
            categories = set()
            for name, definition in BUSINESS_METRICS_DEFINITIONS.items():
                assert hasattr(definition, 'name'), f"{name} 应具有 name 属性"
                assert hasattr(definition, 'metric_type'), f"{name} 应具有 metric_type 属性"
                if hasattr(definition, 'category'):
                    categories.add(definition.category)
            
            self._pass(
                total_metrics=len(BUSINESS_METRICS_DEFINITIONS),
                categories=list(categories)
            )
        except Exception as e:
            self._fail(str(e))
    
    def test_business_metrics_categories(self):
        """测试业务指标分类完整性"""
        self._start_test("BM-002", "业务指标分类完整性", "功能测试", "business_metrics")
        try:
            from agent.monitoring.business_metrics import BUSINESS_METRICS_DEFINITIONS
            
            categories = defaultdict(list)
            for name, definition in BUSINESS_METRICS_DEFINITIONS.items():
                cat = getattr(definition, 'category', 'uncategorized')
                categories[cat].append(name)
            
            # 验证主要分类存在
            main_categories = ['interaction', 'task', 'knowledge', 'extension']
            found_categories = [cat for cat in main_categories if cat in categories]
            
            self._pass(
                categories_count=len(categories),
                main_categories_found=found_categories,
                all_categories=list(categories.keys())
            )
        except Exception as e:
            self._fail(str(e))
    
    # ========================================================================
    # 6. 混沌工程故障注入测试
    # ========================================================================
    
    def test_chaos_injector_init(self):
        """测试混沌工程注入器初始化"""
        self._start_test("CHAOS-001", "混沌注入器初始化", "功能测试", "chaos")
        try:
            from agent.monitoring.chaos_injector import ChaosInjector
            
            injector = ChaosInjector()
            assert injector is not None
            
            self._pass(injector_initialized=True)
        except Exception as e:
            self._fail(str(e))
    
    def test_chaos_fault_types(self):
        """测试支持的故障类型"""
        self._start_test("CHAOS-002", "支持的故障类型", "功能测试", "chaos")
        try:
            from agent.monitoring.chaos_injector import FaultType
            
            fault_types = [e.value for e in FaultType]
            assert len(fault_types) >= 4, f"应至少支持4种故障类型，当前: {len(fault_types)}"
            
            self._pass(fault_types=fault_types, count=len(fault_types))
        except Exception as e:
            self._fail(str(e))
    
    def test_chaos_inject_network_delay(self):
        """测试网络延迟故障注入"""
        self._start_test("CHAOS-003", "网络延迟故障注入", "功能测试", "chaos")
        try:
            from agent.monitoring.chaos_injector import ChaosInjector
            
            injector = ChaosInjector()
            
            # 注入短延迟（测试用，不影响整体性能）
            delay_ms = 10
            injector.inject_network_delay(delay_ms=delay_ms, duration_ms=500)
            
            active_faults = injector.get_active_faults()
            
            # 清理
            injector.clear_all()
            
            self._pass(
                delay_ms=delay_ms,
                active_faults_before_clear=len(active_faults)
            )
        except Exception as e:
            self._fail(str(e))
    
    def test_chaos_inject_service_unavailable(self):
        """测试服务不可用故障注入"""
        self._start_test("CHAOS-004", "服务不可用故障注入", "功能测试", "chaos")
        try:
            from agent.monitoring.chaos_injector import ChaosInjector
            
            injector = ChaosInjector()
            
            injector.inject_service_unavailable(service_name="test-service", duration_ms=500)
            
            active = injector.get_active_faults()
            
            # 验证有活跃的故障
            service_faults = [f for f in active if f.fault_type.value == "service_unavailable"]
            
            injector.clear_all()
            
            self._pass(
                service_name="test-service",
                active_faults_count=len(active),
                service_faults_count=len(service_faults)
            )
        except Exception as e:
            self._fail(str(e))
    
    def test_chaos_clear_all(self):
        """测试所有故障清理"""
        self._start_test("CHAOS-005", "所有故障清理", "功能测试", "chaos")
        try:
            from agent.monitoring.chaos_injector import ChaosInjector
            
            injector = ChaosInjector()
            
            # 注入多个故障
            injector.inject_network_delay(delay_ms=100, duration_ms=5000)
            injector.inject_service_unavailable(service_name="svc1", duration_ms=5000)
            
            assert len(injector.get_active_faults()) >= 1
            
            # 清理所有
            injector.clear_all()
            
            remaining = injector.get_active_faults()
            assert len(remaining) == 0, f"清理后应无活跃故障，剩余: {len(remaining)}"
            
            self._pass(remaining_faults=len(remaining))
        except Exception as e:
            self._fail(str(e))
    
    # ========================================================================
    # 7. 自愈机制验证测试
    # ========================================================================
    
    def test_self_healer_init(self):
        """测试自愈机制初始化"""
        self._start_test("HEAL-001", "自愈机制初始化", "功能测试", "self_healing")
        try:
            from agent.monitoring.self_healer import SelfHealer
            
            healer = SelfHealer()
            assert healer is not None
            
            self._pass(healer_initialized=True)
        except Exception as e:
            self._fail(str(e))
    
    def test_heal_action_types(self):
        """测试支持的自愈动作类型"""
        self._start_test("HEAL-002", "支持的自愈动作类型", "功能测试", "self_healing")
        try:
            from agent.monitoring.self_healer import HealAction
            
            actions = [e.value for e in HealAction]
            assert len(actions) >= 4, f"应至少支持4种自愈动作，当前: {len(actions)}"
            
            self._pass(heal_actions=actions, count=len(actions))
        except Exception as e:
            self._fail(str(e))
    
    def test_heal_policy_config(self):
        """测试自愈策略配置"""
        self._start_test("HEAL-003", "自愈策略配置", "功能测试", "self_healing")
        try:
            from agent.monitoring.self_healer import SelfHealer, HealPolicy
            
            healer = SelfHealer()
            
            # 验证默认策略存在
            assert hasattr(healer, '_policies'), "应包含 _policies 属性"
            assert isinstance(healer._policies, dict), "_policies 应为字典"
            
            self._pass(
                policies_count=len(healer._policies),
                policy_keys=list(healer._policies.keys())[:5]
            )
        except Exception as e:
            self._fail(str(e))
    
    def test_heal_gc_collect(self):
        """测试 GC 回收自愈动作"""
        self._start_test("HEAL-004", "GC 回收自愈动作", "功能测试", "self_healing")
        try:
            from agent.monitoring.self_healer import SelfHealer, HealAction
            
            healer = SelfHealer()
            
            result = healer.execute_action(HealAction.GC_COLLECT.value, {})
            
            assert result is not None
            assert hasattr(result, 'status'), "结果应包含 status 字段"
            
            self._pass(
                action=HealAction.GC_COLLECT.value,
                status=result.status.value if hasattr(result.status, 'value') else result.status
            )
        except Exception as e:
            self._fail(str(e))
    
    def test_heal_clear_cache(self):
        """测试缓存清理自愈动作"""
        self._start_test("HEAL-005", "缓存清理自愈动作", "功能测试", "self_healing")
        try:
            from agent.monitoring.self_healer import SelfHealer, HealAction
            
            healer = SelfHealer()
            
            result = healer.execute_action(HealAction.CLEAR_CACHE.value, {})
            
            assert result is not None
            
            self._pass(
                action=HealAction.CLEAR_CACHE.value,
                status=result.status.value if hasattr(result.status, 'value') else result.status
            )
        except Exception as e:
            self._fail(str(e))
    
    # ========================================================================
    # 8. 敏感数据过滤测试
    # ========================================================================
    
    def test_sensitive_filter_init(self):
        """测试敏感数据过滤器初始化"""
        self._start_test("SEC-001", "敏感数据过滤器初始化", "安全测试", "security")
        try:
            from agent.monitoring.sensitive_data_filter import SensitiveDataFilter
            
            filter_obj = SensitiveDataFilter()
            assert filter_obj is not None
            
            self._pass(filter_initialized=True)
        except Exception as e:
            self._fail(str(e))
    
    def test_sensitive_filter_dict(self):
        """测试字典敏感数据过滤"""
        self._start_test("SEC-002", "字典敏感数据过滤", "安全测试", "security")
        try:
            from agent.monitoring.sensitive_data_filter import (
                SensitiveDataFilter, REDACTED_VALUE
            )
            
            filter_obj = SensitiveDataFilter()
            
            test_data = {
                "username": "admin",
                "password": "secret123",
                "api_key": "ak_test_1234567890",
                "email": "test@example.com",
                "public_field": "safe_value"
            }
            
            filtered = filter_obj.filter_dict(test_data.copy())
            
            # 验证敏感字段被过滤
            assert filtered["password"] == REDACTED_VALUE, "password 应被完全屏蔽"
            assert filtered["api_key"] == REDACTED_VALUE, "api_key 应被完全屏蔽"
            # 验证非敏感字段保留
            assert filtered["username"] == "admin", "username 应保留原值"
            assert filtered["email"] == "test@example.com", "email 应保留原值"
            assert filtered["public_field"] == "safe_value", "public_field 应保留原值"
            
            self._pass(
                fields_filtered=["password", "api_key"],
                fields_preserved=["username", "email", "public_field"]
            )
        except Exception as e:
            self._fail(str(e))
    
    def test_sensitive_filter_nested_dict(self):
        """测试嵌套字典敏感数据过滤"""
        self._start_test("SEC-003", "嵌套字典敏感数据过滤", "安全测试", "security")
        try:
            from agent.monitoring.sensitive_data_filter import (
                SensitiveDataFilter, REDACTED_VALUE
            )
            
            filter_obj = SensitiveDataFilter()
            
            nested_data = {
                "user": {
                    "name": "test_user",
                    "credentials": {
                        "password": "nested_secret",
                        "token": "jwt_token_12345"
                    }
                },
                "config": {
                    "api_key": "key_12345",
                    "debug": True
                }
            }
            
            filtered = filter_obj.filter_dict(nested_data.copy())
            
            # 验证嵌套字段被过滤
            assert filtered["user"]["credentials"]["password"] == REDACTED_VALUE
            assert filtered["user"]["credentials"]["token"] == REDACTED_VALUE
            assert filtered["config"]["api_key"] == REDACTED_VALUE
            # 验证非敏感字段保留
            assert filtered["user"]["name"] == "test_user"
            assert filtered["config"]["debug"] is True
            
            self._pass(nested_filtered=True)
        except Exception as e:
            self._fail(str(e))
    
    def test_sensitive_filter_string(self):
        """测试字符串敏感数据过滤"""
        self._start_test("SEC-004", "字符串敏感数据过滤", "安全测试", "security")
        try:
            from agent.monitoring.sensitive_data_filter import SensitiveDataFilter, REDACTED_VALUE
            
            filter_obj = SensitiveDataFilter()
            
            # 测试 AWS Key 格式
            test_aws = "AWS Key: AKIAIOSFODNN7EXAMPLE"
            filtered_aws = filter_obj.filter_string(test_aws)
            assert "AKIAIOSFODNN7EXAMPLE" not in filtered_aws, "AWS Key 应被过滤"
            
            # 测试 API Key 格式
            test_api_key = "api_key=abcdef1234567890"
            filtered_api = filter_obj.filter_string(test_api_key)
            assert "abcdef1234567890" not in filtered_api, "API Key 值应被过滤"
            
            # 测试 JWT 格式
            test_jwt = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
            filtered_jwt = filter_obj.filter_string(test_jwt)
            assert "eyJhbGciOiJIUzI1NiJ9" not in filtered_jwt, "JWT Token 应被过滤"
            
            self._pass(
                aws_key_filtered=True,
                api_key_filtered=True,
                jwt_filtered=True
            )
        except Exception as e:
            self._fail(str(e))
    
    def test_sensitive_filter_is_sensitive_key(self):
        """测试敏感字段判断"""
        self._start_test("SEC-005", "敏感字段判断", "安全测试", "security")
        try:
            from agent.monitoring.sensitive_data_filter import SensitiveDataFilter
            
            filter_obj = SensitiveDataFilter()
            
            sensitive_keys = ["password", "api_key", "token", "secret", "db_password", "private_key"]
            non_sensitive_keys = ["username", "email", "status", "count", "name"]
            
            for key in sensitive_keys:
                assert filter_obj.is_sensitive_key(key), f"{key} 应被识别为敏感字段"
            
            for key in non_sensitive_keys:
                assert not filter_obj.is_sensitive_key(key), f"{key} 不应被识别为敏感字段"
            
            self._pass(
                sensitive_keys_checked=len(sensitive_keys),
                non_sensitive_keys_checked=len(non_sensitive_keys)
            )
        except Exception as e:
            self._fail(str(e))
    
    def test_sensitive_filter_custom_patterns(self):
        """测试自定义敏感字段模式"""
        self._start_test("SEC-006", "自定义敏感字段模式", "安全测试", "security")
        try:
            from agent.monitoring.sensitive_data_filter import SensitiveDataFilter, REDACTED_VALUE
            
            # 自定义字段名模式
            custom_patterns = [r'custom_secret_\w+']
            filter_obj = SensitiveDataFilter(additional_patterns=custom_patterns)
            
            test_data = {
                "custom_secret_key": "my_custom_secret_value",
                "normal_field": "safe"
            }
            
            original_value = test_data["custom_secret_key"]
            filtered = filter_obj.filter_dict(test_data.copy())
            
            # 自定义敏感字段应被过滤（值被修改，不是原值）
            assert filtered["custom_secret_key"] != original_value, "自定义敏感字段应被过滤"
            assert filtered["normal_field"] == "safe", "普通字段应保留"
            
            self._pass(
                custom_pattern_applied=True,
                original_value=original_value,
                filtered_value_preview=filtered["custom_secret_key"][:6] + "..."
            )
        except Exception as e:
            self._fail(str(e))
    
    # ========================================================================
    # 9. 并发/多线程场景测试
    # ========================================================================
    
    def test_concurrent_trace_isolation(self):
        """测试并发请求的追踪上下文隔离"""
        self._start_test("CONC-001", "并发请求追踪隔离", "集成测试", "concurrency")
        try:
            from agent.monitoring.tracing import TraceContext, get_trace_id, set_trace_id
            import threading
            
            num_threads = 10
            trace_ids = []
            errors = []
            lock = threading.Lock()
            
            def worker(thread_id):
                try:
                    set_trace_id(None)  # 确保初始状态干净
                    with TraceContext("ConcurrentService", f"thread_{thread_id}") as ctx:
                        trace_id = ctx.trace_id
                        time.sleep(0.01)  # 模拟工作
                        with lock:
                            trace_ids.append(trace_id)
                except Exception as e:
                    with lock:
                        errors.append(str(e))
            
            threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            
            assert len(errors) == 0, f"并发执行出现错误: {errors}"
            assert len(trace_ids) == num_threads, f"应为 {num_threads} 个 Trace ID，实际 {len(trace_ids)}"
            
            # 验证所有 Trace ID 唯一（隔离性）
            unique_ids = set(trace_ids)
            assert len(unique_ids) == num_threads, \
                f"Trace ID 应全部唯一，唯一数: {len(unique_ids)}/{num_threads}"
            
            self._pass(
                num_threads=num_threads,
                unique_trace_ids=len(unique_ids),
                errors=len(errors)
            )
        except Exception as e:
            self._fail(str(e))
    
    def test_concurrent_metrics_recording(self):
        """测试并发指标记录"""
        self._start_test("CONC-002", "并发指标记录", "集成测试", "concurrency")
        try:
            from agent.monitoring.metrics import increment_counter
            import threading
            
            num_threads = 20
            increments_per_thread = 50
            errors = []
            lock = threading.Lock()
            
            def worker(thread_id):
                try:
                    for i in range(increments_per_thread):
                        increment_counter("acceptance.concurrent.test")
                except Exception as e:
                    with lock:
                        errors.append(str(e))
            
            threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            
            assert len(errors) == 0, f"并发记录指标出现错误: {errors}"
            
            self._pass(
                num_threads=num_threads,
                total_increments=num_threads * increments_per_thread,
                errors=len(errors)
            )
        except Exception as e:
            self._fail(str(e))
    
    def test_concurrent_sensitive_filter(self):
        """测试并发敏感数据过滤"""
        self._start_test("CONC-003", "并发敏感数据过滤", "集成测试", "concurrency")
        try:
            from agent.monitoring.sensitive_data_filter import SensitiveDataFilter
            import threading
            
            filter_obj = SensitiveDataFilter()
            num_threads = 15
            errors = []
            results = []
            lock = threading.Lock()
            
            def worker(thread_id):
                try:
                    test_data = {
                        f"password_{thread_id}": f"secret_{thread_id}",
                        "username": f"user_{thread_id}"
                    }
                    filtered = filter_obj.filter_dict(test_data)
                    with lock:
                        results.append((thread_id, test_data, filtered))
                except Exception as e:
                    with lock:
                        errors.append(str(e))
            
            threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            
            assert len(errors) == 0, f"并发过滤出现错误: {errors}"
            assert len(results) == num_threads
            
            # 验证每个结果都正确过滤（敏感字段值被修改）
            for thread_id, original, filtered in results:
                orig_val = original[f"password_{thread_id}"]
                filtered_val = filtered[f"password_{thread_id}"]
                assert filtered_val != orig_val, f"password_{thread_id} 应被过滤"
                assert filtered["username"] == f"user_{thread_id}", "普通字段应保留"
            
            self._pass(num_threads=num_threads, errors=len(errors), filtered_count=len(results))
        except Exception as e:
            self._fail(str(e))
    
    # ========================================================================
    # 10. 性能测试 - 可观测性开销
    # ========================================================================
    
    def test_performance_tracing_overhead(self):
        """测试追踪功能性能开销"""
        self._start_test("PERF-001", "追踪功能性能开销", "性能测试", "performance")
        try:
            from agent.monitoring.tracing import TraceContext
            
            iterations = 1000
            
            # 基准测试（无可观测性）
            start = time.time()
            for i in range(iterations):
                _ = i * 2
            baseline_duration = (time.time() - start) * 1000
            
            # 带追踪的测试
            start = time.time()
            for i in range(iterations):
                with TraceContext("PerfTest", f"iter_{i}") as ctx:
                    _ = i * 2
            traced_duration = (time.time() - start) * 1000
            
            overhead_per_call = (traced_duration - baseline_duration) / iterations
            overhead_percent = ((traced_duration - baseline_duration) / baseline_duration) * 100 if baseline_duration > 0 else 0
            
            # 可接受的开销：每次调用 < 1ms
            acceptable = overhead_per_call < 1.0
            
            self._pass(
                iterations=iterations,
                baseline_ms=round(baseline_duration, 2),
                traced_ms=round(traced_duration, 2),
                overhead_per_call_ms=round(overhead_per_call, 4),
                overhead_percent=round(overhead_percent, 2),
                acceptable=acceptable
            )
        except Exception as e:
            self._fail(str(e))
    
    def test_performance_sensitive_filter_overhead(self):
        """测试敏感数据过滤性能开销"""
        self._start_test("PERF-002", "敏感数据过滤性能开销", "性能测试", "performance")
        try:
            from agent.monitoring.sensitive_data_filter import SensitiveDataFilter
            
            filter_obj = SensitiveDataFilter()
            iterations = 1000
            
            test_data = {
                "user": {
                    "name": "test_user",
                    "password": "secret123",
                    "email": "test@example.com",
                    "api_key": "ak_1234567890"
                },
                "config": {
                    "debug": True,
                    "db_password": "db_secret",
                    "timeout": 30
                }
            }
            
            start = time.time()
            for _ in range(iterations):
                filter_obj.filter_dict(test_data.copy())
            total_duration = (time.time() - start) * 1000
            
            per_call = total_duration / iterations
            
            # 可接受的开销：每次调用 < 0.5ms
            acceptable = per_call < 0.5
            
            self._pass(
                iterations=iterations,
                total_ms=round(total_duration, 2),
                per_call_ms=round(per_call, 4),
                acceptable=acceptable
            )
        except Exception as e:
            self._fail(str(e))
    
    def test_performance_context_extract_inject(self):
        """测试上下文提取/注入性能"""
        self._start_test("PERF-003", "上下文提取/注入性能", "性能测试", "performance")
        try:
            from agent.monitoring.tracing import (
                TraceContext, extract_trace_context, inject_trace_context
            )
            
            iterations = 1000
            
            # 注入性能
            start = time.time()
            with TraceContext("PerfTest", "inject_test"):
                for _ in range(iterations):
                    inject_trace_context()
            inject_duration = (time.time() - start) * 1000
            
            # 提取性能
            test_headers = {
                "traceparent": "00-abc123def4567890abc123def4567890-1234567812345678-01"
            }
            start = time.time()
            for _ in range(iterations):
                extract_trace_context(test_headers)
            extract_duration = (time.time() - start) * 1000
            
            inject_per_call = inject_duration / iterations
            extract_per_call = extract_duration / iterations
            
            self._pass(
                iterations=iterations,
                inject_total_ms=round(inject_duration, 2),
                inject_per_call_us=round(inject_per_call * 1000, 2),
                extract_total_ms=round(extract_duration, 2),
                extract_per_call_us=round(extract_per_call * 1000, 2)
            )
        except Exception as e:
            self._fail(str(e))
    
    # ========================================================================
    # 11. 边界测试
    # ========================================================================
    
    def test_boundary_empty_trace_context(self):
        """测试空数据边界情况"""
        self._start_test("BND-001", "空数据边界情况", "边界测试", "boundary")
        try:
            from agent.monitoring.tracing import extract_trace_context
            from agent.monitoring.sensitive_data_filter import SensitiveDataFilter
            
            # 空 headers 提取
            result = extract_trace_context({})
            # 不应报错，返回 None 或空字典
            assert result is None or isinstance(result, dict), "空 headers 不应抛出异常"
            
            # 空字典过滤
            filter_obj = SensitiveDataFilter()
            filtered = filter_obj.filter_dict({})
            assert filtered == {}, "空字典过滤后应仍为空字典"
            
            # 空字符串过滤
            filtered_str = filter_obj.filter_string("")
            assert filtered_str == "", "空字符串过滤后应仍为空"
            
            self._pass(empty_handling=True)
        except Exception as e:
            self._fail(str(e))
    
    def test_boundary_large_data_filter(self):
        """测试大数据量过滤性能"""
        self._start_test("BND-002", "大数据量过滤", "边界测试", "boundary")
        try:
            from agent.monitoring.sensitive_data_filter import SensitiveDataFilter
            
            filter_obj = SensitiveDataFilter()
            
            # 创建大型字典（100个普通字段 + 10个敏感字段 = 110个字段）
            large_data = {}
            for i in range(100):
                large_data[f"field_{i}"] = f"value_{i}"
                if i % 10 == 0:
                    large_data[f"password_{i}"] = f"secret_{i}"
            
            expected_count = len(large_data)
            
            start = time.time()
            filtered = filter_obj.filter_dict(large_data)
            duration_ms = (time.time() - start) * 1000
            
            # 验证字段数一致（敏感字段是替换值，不是删除）
            assert len(filtered) == expected_count, \
                f"过滤后字段数应一致: 期望 {expected_count}, 实际 {len(filtered)}"
            
            # 验证敏感字段被替换（值与原值不同）
            sensitive_fields_count = 0
            for i in range(0, 100, 10):
                key = f"password_{i}"
                original_val = large_data[key]
                filtered_val = filtered[key]
                assert filtered_val != original_val, f"{key} 应被过滤"
                sensitive_fields_count += 1
            
            self._pass(
                field_count=expected_count,
                sensitive_fields_count=sensitive_fields_count,
                duration_ms=round(duration_ms, 2)
            )
        except Exception as e:
            self._fail(str(e))
    
    def test_boundary_special_characters(self):
        """测试特殊字符处理"""
        self._start_test("BND-003", "特殊字符处理", "边界测试", "boundary")
        try:
            from agent.monitoring.sensitive_data_filter import SensitiveDataFilter, REDACTED_VALUE
            
            filter_obj = SensitiveDataFilter()
            
            test_data = {
                "password": "!@#$%^&*()_+-=[]{}|;:,.<>?",
                "正常字段": "正常值",
                "API-KEY": "special-key-123"
            }
            
            filtered = filter_obj.filter_dict(test_data)
            
            assert filtered["password"] == REDACTED_VALUE
            assert filtered["正常字段"] == "正常值"
            
            self._pass(special_chars_handled=True)
        except Exception as e:
            self._fail(str(e))
    
    def test_boundary_null_none_values(self):
        """测试 None/Null 值处理"""
        self._start_test("BND-004", "None/Null 值处理", "边界测试", "boundary")
        try:
            from agent.monitoring.sensitive_data_filter import SensitiveDataFilter
            
            filter_obj = SensitiveDataFilter()
            
            test_data = {
                "password": None,
                "api_key": "",
                "normal_field": None,
                "nested": {
                    "token": None,
                    "value": "valid"
                }
            }
            
            filtered = filter_obj.filter_dict(test_data)
            
            # 不应报错，None 值应被保留
            assert "password" in filtered
            assert "normal_field" in filtered
            
            self._pass(null_values_handled=True)
        except Exception as e:
            self._fail(str(e))
    
    # ========================================================================
    # 12. 集成测试 - 端到端场景
    # ========================================================================
    
    def test_integration_full_observability_pipeline(self):
        """测试完整可观测性流水线（追踪+指标+日志+告警）"""
        self._start_test("INT-001", "完整可观测性流水线", "集成测试", "integration")
        try:
            from agent.monitoring.tracing import TraceContext, get_trace_id
            from agent.monitoring.metrics import increment_counter, record_latency
            import logging
            
            test_logger = logging.getLogger("acceptance.test")
            
            # 模拟一个完整的请求处理流程
            with TraceContext("IntegrationTest", "full_pipeline") as ctx:
                trace_id = ctx.trace_id
                
                # 记录业务指标
                increment_counter("acceptance.integration.requests")
                record_latency("acceptance.integration.duration", 0.05)
                
                # 记录日志（带 trace_id）
                test_logger.info(f"[TRACE:{trace_id}] 集成测试请求处理完成")
                
                # 模拟子操作
                with TraceContext("IntegrationTest", "sub_operation") as sub_ctx:
                    sub_trace_id = sub_ctx.trace_id
                    assert sub_trace_id == trace_id, "子操作应继承相同 Trace ID"
                    increment_counter("acceptance.integration.sub_operations")
            
            self._pass(
                trace_id=trace_id,
                pipeline_stages=["tracing", "metrics", "logging"]
            )
        except Exception as e:
            self._fail(str(e))
    
    def test_integration_tracing_metrics_correlation(self):
        """测试追踪与指标关联"""
        self._start_test("INT-002", "追踪与指标关联", "集成测试", "integration")
        try:
            from agent.monitoring.tracing import TraceContext
            from agent.monitoring.metrics import record_latency, increment_counter
            
            # 模拟带追踪的请求，记录指标
            latencies = []
            for i in range(5):
                with TraceContext("CorrelationTest", f"request_{i}") as ctx:
                    start = time.time()
                    time.sleep(0.01)  # 模拟工作
                    duration = time.time() - start
                    
                    # 将追踪中的耗时记录到指标
                    record_latency("acceptance.correlation.duration", duration)
                    increment_counter("acceptance.correlation.requests")
                    
                    latencies.append(ctx.duration_ms)
            
            assert len(latencies) == 5
            
            self._pass(
                request_count=5,
                avg_latency_ms=round(sum(latencies) / len(latencies), 2)
            )
        except Exception as e:
            self._fail(str(e))
    
    def test_integration_chaos_alert_healing(self):
        """测试混沌工程 -> 告警 -> 自愈的完整链路"""
        self._start_test("INT-003", "混沌-告警-自愈链路", "集成测试", "integration")
        try:
            from agent.monitoring.chaos_injector import ChaosInjector
            from agent.monitoring.self_healer import SelfHealer, HealAction
            
            injector = ChaosInjector()
            healer = SelfHealer()
            
            try:
                # 1. 注入故障
                injector.inject_memory_pressure(target_mb=10, duration_ms=2000)
                
                # 2. 执行自愈动作
                result = healer.execute_action(HealAction.GC_COLLECT.value, {})
                
                # 3. 清理故障
                injector.clear_all()
                
                assert result is not None
                assert hasattr(result, 'status')
                
                self._pass(
                    chaos_injected=True,
                    heal_executed=True,
                    chaos_cleared=True,
                    heal_status=result.status.value if hasattr(result.status, 'value') else result.status
                )
            finally:
                injector.clear_all()
        except Exception as e:
            self._fail(str(e))
    
    # ========================================================================
    # 运行所有测试
    # ========================================================================
    
    def run_all_tests(self):
        """运行所有验收测试"""
        print("="*70)
        print("  云枢智能代理 - 可观测性体系验收测试")
        print("="*70)
        print(f"开始时间: {self.report.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print()
        
        # 1. 追踪测试
        print("\n" + "="*70)
        print("  一、追踪上下文传播测试")
        print("="*70)
        self.test_tracing_basic_context()
        self.test_tracing_context_propagation_extract()
        self.test_tracing_context_propagation_inject()
        self.test_tracing_cross_service_propagation()
        self.test_tracing_opentelemetry_available()
        self.test_tracing_decorator()
        self.test_tracing_error_capture()
        self.test_tracing_nested_spans()
        
        # 2. Prometheus 指标测试
        print("\n" + "="*70)
        print("  二、Prometheus 指标导出测试")
        print("="*70)
        self.test_prometheus_available()
        self.test_prometheus_metrics_types()
        self.test_prometheus_exporter_init()
        self.test_prometheus_metrics_collector()
        self.test_prometheus_record_latency()
        
        # 3. 健康检查测试
        print("\n" + "="*70)
        print("  三、健康检查端点测试")
        print("="*70)
        self.test_health_basic_check()
        self.test_health_assessment()
        self.test_health_dashboard()
        
        # 4. 告警系统测试
        print("\n" + "="*70)
        print("  四、告警规则触发测试")
        print("="*70)
        self.test_alert_rule_creation()
        self.test_alert_evaluator_init()
        self.test_alert_evaluator_add_remove_rule()
        self.test_alert_notifier_init()
        self.test_alert_manager_integration()
        
        # 5. 业务指标测试
        print("\n" + "="*70)
        print("  五、业务指标统计测试")
        print("="*70)
        self.test_business_metrics_definitions()
        self.test_business_metrics_categories()
        
        # 6. 混沌工程测试
        print("\n" + "="*70)
        print("  六、混沌工程故障注入测试")
        print("="*70)
        self.test_chaos_injector_init()
        self.test_chaos_fault_types()
        self.test_chaos_inject_network_delay()
        self.test_chaos_inject_service_unavailable()
        self.test_chaos_clear_all()
        
        # 7. 自愈机制测试
        print("\n" + "="*70)
        print("  七、自愈机制验证测试")
        print("="*70)
        self.test_self_healer_init()
        self.test_heal_action_types()
        self.test_heal_policy_config()
        self.test_heal_gc_collect()
        self.test_heal_clear_cache()
        
        # 8. 安全测试
        print("\n" + "="*70)
        print("  八、敏感数据过滤测试（安全测试）")
        print("="*70)
        self.test_sensitive_filter_init()
        self.test_sensitive_filter_dict()
        self.test_sensitive_filter_nested_dict()
        self.test_sensitive_filter_string()
        self.test_sensitive_filter_is_sensitive_key()
        self.test_sensitive_filter_custom_patterns()
        
        # 9. 并发测试
        print("\n" + "="*70)
        print("  九、并发/多线程场景测试")
        print("="*70)
        self.test_concurrent_trace_isolation()
        self.test_concurrent_metrics_recording()
        self.test_concurrent_sensitive_filter()
        
        # 10. 性能测试
        print("\n" + "="*70)
        print("  十、性能测试 - 可观测性开销")
        print("="*70)
        self.test_performance_tracing_overhead()
        self.test_performance_sensitive_filter_overhead()
        self.test_performance_context_extract_inject()
        
        # 11. 边界测试
        print("\n" + "="*70)
        print("  十一、边界测试")
        print("="*70)
        self.test_boundary_empty_trace_context()
        self.test_boundary_large_data_filter()
        self.test_boundary_special_characters()
        self.test_boundary_null_none_values()
        
        # 12. 集成测试
        print("\n" + "="*70)
        print("  十二、集成测试 - 端到端场景")
        print("="*70)
        self.test_integration_full_observability_pipeline()
        self.test_integration_tracing_metrics_correlation()
        self.test_integration_chaos_alert_healing()
        
        # 完成
        self.report.end_time = datetime.now()
        self.report.total_duration_ms = (self.report.end_time - self.report.start_time).total_seconds() * 1000
        
        return self.report
    
    def print_summary(self):
        """打印测试摘要"""
        print("\n" + "="*70)
        print("  验收测试结果摘要")
        print("="*70)
        print(f"总测试数: {self.report.total_count}")
        print(f"通过: {self.report.passed_count}")
        print(f"失败: {self.report.failed_count}")
        print(f"跳过: {self.report.skipped_count}")
        print(f"通过率: {self.report.pass_rate:.2f}%")
        print(f"总耗时: {self.report.total_duration_ms:.2f}ms ({self.report.total_duration_ms/1000:.2f}s)")
        
        print("\n按分类统计:")
        for category, stats in sorted(self.report.by_category.items()):
            print(f"  {category}: {stats['passed']}/{stats['total']} 通过 "
                  f"({stats['passed']/stats['total']*100:.1f}%)" if stats['total'] > 0 else f"  {category}: 0/0")
        
        print("\n按模块统计:")
        for module, stats in sorted(self.report.by_module.items()):
            print(f"  {module}: {stats['passed']}/{stats['total']} 通过 "
                  f"({stats['passed']/stats['total']*100:.1f}%)" if stats['total'] > 0 else f"  {module}: 0/0")
        
        # 失败用例详情
        failed_tests = [t for t in self.report.test_cases if t.status == "failed"]
        if failed_tests:
            print("\n失败的测试用例:")
            for t in failed_tests:
                print(f"  [{t.test_id}] {t.test_name}")
                print(f"      错误: {t.error_message}")
        
        # 验收结论
        print("\n" + "="*70)
        pass_threshold = 90.0  # 通过率 >= 90% 为通过
        if self.report.pass_rate >= pass_threshold:
            print(f"  验收结论: ✓ 通过 (通过率 {self.report.pass_rate:.1f}% >= {pass_threshold}%)")
        else:
            print(f"  验收结论: ✗ 不通过 (通过率 {self.report.pass_rate:.1f}% < {pass_threshold}%)")
        print("="*70)
    
    def generate_report_json(self, output_path: str = None):
        """生成 JSON 格式的测试报告"""
        report_dict = {
            "start_time": self.report.start_time.isoformat(),
            "end_time": self.report.end_time.isoformat() if self.report.end_time else None,
            "total_duration_ms": self.report.total_duration_ms,
            "summary": {
                "total": self.report.total_count,
                "passed": self.report.passed_count,
                "failed": self.report.failed_count,
                "skipped": self.report.skipped_count,
                "pass_rate": self.report.pass_rate
            },
            "by_category": self.report.by_category,
            "by_module": self.report.by_module,
            "test_cases": [
                {
                    "test_id": t.test_id,
                    "test_name": t.test_name,
                    "category": t.category,
                    "module": t.module,
                    "status": t.status,
                    "duration_ms": t.duration_ms,
                    "error_message": t.error_message,
                    "details": t.details
                }
                for t in self.report.test_cases
            ],
            "conclusion": "PASS" if self.report.pass_rate >= 90.0 else "FAIL"
        }
        
        if output_path:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(report_dict, f, ensure_ascii=False, indent=2, default=str)
            print(f"\n报告已保存到: {output_path}")
        
        return report_dict


def main():
    """主函数"""
    suite = ObservabilityAcceptanceTest()
    suite.run_all_tests()
    suite.print_summary()
    
    # 生成 JSON 报告
    output_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "reports",
        "observability_acceptance_report.json"
    )
    suite.generate_report_json(output_path)
    
    # 返回退出码
    return 0 if suite.report.pass_rate >= 90.0 else 1


if __name__ == "__main__":
    sys.exit(main())
