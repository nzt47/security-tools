#!/usr/bin/env python3
"""
告警评估器模块

基于 Prometheus 指标进行告警评估，支持：
1. 阈值告警（静态阈值）
2. 趋势告警（基于变化率）
3. 异常检测（基于统计）

告警触发流程：
1. 收集当前指标值
2. 评估告警规则条件
3. 状态机管理（pending -> firing -> resolved）
4. 触发通知和自愈动作
"""

import logging
import time
import threading
import hashlib
import uuid
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict
import json

# 结构化日志必需：get_trace_id() 提供当前请求/任务上下文追踪 ID
# set_trace_id() 用于跨线程传递 trace_id（ContextVar 不自动继承到子线程）
from agent.monitoring.tracing import get_trace_id, set_trace_id

try:
    from agent.monitoring.metrics import get_metrics_collector
    _METRICS_AVAILABLE = True
except ImportError:
    _METRICS_AVAILABLE = False
    logging.warning("[Alert] metrics 模块不可用")

try:
    from agent.error_handler import get_error_handler, YunshuError, ErrorCategory
    _ERROR_HANDLER_AVAILABLE = True
except ImportError:
    _ERROR_HANDLER_AVAILABLE = False
    logging.warning("[Alert] error_handler 模块不可用")

logger = logging.getLogger(__name__)


class AlertState(Enum):
    """告警状态"""
    INACTIVE = "inactive"      # 非活跃（未触发）
    PENDING = "pending"        # 等待确认（满足条件但未达到持续时间）
    FIRING = "firing"          # 触发中
    RESOLVED = "resolved"      # 已恢复


class AlertSeverity(Enum):
    """告警严重级别"""
    CRITICAL = "critical"      # 严重（需要立即处理）
    WARNING = "warning"        # 警告（需要关注）
    INFO = "info"             # 信息


@dataclass
class Alert:
    """告警实例"""
    name: str
    state: AlertState
    severity: AlertSeverity
    value: float              # 当前指标值
    threshold: float          # 触发阈值
    condition: str            # 告警条件表达式
    message: str              # 告警消息
    started_at: Optional[float] = None  # 触发开始时间
    pending_since: Optional[float] = None  # 进入 pending 状态时间
    resolved_at: Optional[float] = None  # 恢复时间
    fire_count: int = 0       # 触发次数（用于自愈）
    labels: Dict[str, str] = field(default_factory=dict)
    annotations: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "name": self.name,
            "state": self.state.value,
            "severity": self.severity.value,
            "value": self.value,
            "threshold": self.threshold,
            "condition": self.condition,
            "message": self.message,
            "started_at": self.started_at,
            "pending_since": self.pending_since,
            "resolved_at": self.resolved_at,
            "fire_count": self.fire_count,
            "labels": self.labels,
            "annotations": self.annotations,
            "duration_seconds": (time.time() - self.started_at) if self.started_at else 0
        }


@dataclass
class AlertRule:
    """告警规则定义"""
    name: str
    expr: str                           # PromQL 表达式
    duration: str = "5m"                 # 持续时间
    severity: str = "warning"           # 严重级别
    labels: Dict[str, str] = field(default_factory=dict)
    annotations: Dict[str, str] = field(default_factory=dict)
    # 评估参数
    threshold: Optional[float] = None   # 静态阈值
    comparison: str = "gt"              # 比较方式：gt, lt, eq, ne, gte, lte
    # 自愈参数
    auto_heal: bool = False
    heal_actions: List[str] = field(default_factory=list)
    heal_threshold: int = 3             # 触发自愈的告警次数


class AlertEvaluator:
    """告警评估器

    评估告警规则并管理告警生命周期。
    """

    def __init__(
        self,
        evaluation_interval: float = 30.0,
        pending_duration: float = 60.0
    ):
        """
        Args:
            evaluation_interval: 评估间隔（秒）
            pending_duration: 进入 firing 状态需要的持续时间（秒）
        """
        self.evaluation_interval = evaluation_interval
        self.pending_duration = pending_duration

        # 告警状态存储
        self._alerts: Dict[str, Alert] = {}
        self._rules: Dict[str, AlertRule] = {}
        self._lock = threading.RLock()

        # 指标收集器
        self._metrics_collector = get_metrics_collector() if _METRICS_AVAILABLE else None

        # 回调函数
        self._on_alert_state_change: Optional[Callable] = None
        self._on_heal_action: Optional[Callable] = None

        # 统计信息
        self._stats = {
            "total_evaluations": 0,
            "alerts_triggered": 0,
            "alerts_resolved": 0,
            "heal_actions_executed": 0
        }

        # 运行状态
        self._running = False
        self._evaluation_thread: Optional[threading.Thread] = None

        # 评估器专属 trace_id（后台线程不继承主线程 ContextVar，需手动设置）
        # 作用：让后台评估循环的所有日志可追溯到这个评估器实例
        self._evaluator_trace_id = f"alert-eval-{uuid.uuid4().hex[:16]}"

        # 结构化日志：init 节点（含配置参数，便于排查评估间隔与 pending 时长）
        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "alert_evaluator",
            "action": "init",
            "duration_ms": 0,
            "evaluation_interval": evaluation_interval,
            "pending_duration": pending_duration,
            "evaluator_trace_id": self._evaluator_trace_id
        }, ensure_ascii=False))

    def set_on_state_change(self, callback: Callable[[Alert, AlertState, AlertState], None]):
        """设置告警状态变化回调"""
        self._on_alert_state_change = callback

    def set_on_heal_action(self, callback: Callable[[Alert, str], bool]):
        """设置自愈动作回调

        Args:
            callback: 回调函数，返回是否执行成功
        """
        self._on_heal_action = callback

    def add_rule(self, rule: AlertRule):
        """添加告警规则

        Args:
            rule: 告警规则
        """
        with self._lock:
            self._rules[rule.name] = rule
            # 初始化告警实例
            if rule.name not in self._alerts:
                self._alerts[rule.name] = Alert(
                    name=rule.name,
                    state=AlertState.INACTIVE,
                    severity=AlertSeverity(rule.severity),
                    value=0.0,
                    threshold=rule.threshold or 0.0,
                    condition=rule.expr,
                    message="",
                    labels=rule.labels,
                    annotations=rule.annotations
                )
        # 结构化日志：规则新增（记录规则名与级别，便于审计规则变更）
        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "alert_evaluator",
            "action": "add_rule",
            "duration_ms": 0,
            "rule_name": rule.name,
            "severity": rule.severity
        }, ensure_ascii=False))

    def remove_rule(self, rule_name: str):
        """移除告警规则"""
        with self._lock:
            if rule_name in self._rules:
                del self._rules[rule_name]
                # 移除告警实例
                if rule_name in self._alerts:
                    del self._alerts[rule_name]
        # 结构化日志：规则移除（记录规则名，便于审计规则变更）
        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "alert_evaluator",
            "action": "remove_rule",
            "duration_ms": 0,
            "rule_name": rule_name
        }, ensure_ascii=False))

    def _parse_duration(self, duration_str: str) -> float:
        """解析 duration 字符串为秒数

        Args:
            duration_str: 如 "5m", "30s", "1h"

        Returns:
            秒数
        """
        duration_str = duration_str.strip().lower()
        if duration_str.endswith("s"):
            return float(duration_str[:-1])
        elif duration_str.endswith("m"):
            return float(duration_str[:-1]) * 60
        elif duration_str.endswith("h"):
            return float(duration_str[:-1]) * 3600
        elif duration_str.endswith("d"):
            return float(duration_str[:-1]) * 86400
        else:
            return float(duration_str)

    def _evaluate_condition(self, rule: AlertRule, current_value: float) -> bool:
        """评估条件是否满足

        Args:
            rule: 告警规则
            current_value: 当前指标值

        Returns:
            是否满足条件
        """
        threshold = rule.threshold
        if threshold is None:
            return current_value > 0

        comp = rule.comparison
        if comp == "gt":
            return current_value > threshold
        elif comp == "lt":
            return current_value < threshold
        elif comp == "gte":
            return current_value >= threshold
        elif comp == "lte":
            return current_value <= threshold
        elif comp == "eq":
            return current_value == threshold
        elif comp == "ne":
            return current_value != threshold
        return False

    def _get_metric_value(self, metric_name: str) -> Optional[float]:
        """获取指标值

        Args:
            metric_name: 指标名称

        Returns:
            指标值，如果不存在返回 None
        """
        if not self._metrics_collector:
            return None

        try:
            # 尝试从 histogram 获取 p99
            if "latency" in metric_name.lower() or "duration" in metric_name.lower():
                stats = self._metrics_collector.get_stats(metric_name)
                return stats.get("p99", 0)

            # 尝试获取 counter
            all_metrics = self._metrics_collector.get_all_metrics()
            counters = all_metrics.get("counters", {})
            if metric_name in counters:
                return float(counters[metric_name])

            # 尝试获取 histogram 的 count
            histograms = all_metrics.get("histograms", {})
            if metric_name in histograms:
                return float(histograms[metric_name].get("count", 0))
        except Exception as e:
            # 结构化日志：指标查询失败（含错误信息，便于排查指标源故障）
            logger.error(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "alert_evaluator",
                "action": "get_metric_value",
                "duration_ms": 0,
                "metric_name": metric_name,
                "error": str(e)
            }, ensure_ascii=False))
        return None

    def _evaluate_rule(self, rule: AlertRule) -> Optional[float]:
        """评估单个规则

        Args:
            rule: 告警规则

        Returns:
            当前指标值，不满足条件返回 None
        """
        # 从表达式中提取指标名称（简化版）
        metric_name = rule.expr.split("(")[1].split("[")[0].replace("yunshu_", "").replace("_", ".")

        # 映射指标名称
        metric_mappings = {
            "health.score": "latency.digital_life.health",
            "error.total": "count.errors.total",
            "interaction.total": "count.interactions.total",
            "interaction.duration": "latency.digital_life.chat",
            "memory.count": "memory.count",
        }

        mapped_name = metric_mappings.get(metric_name, f"latency.digital_life.{metric_name}")
        value = self._get_metric_value(mapped_name)

        if value is not None and self._evaluate_condition(rule, value):
            return value
        return None

    def evaluate(self) -> List[Alert]:
        """执行一次评估

        Returns:
            当前触发的告警列表
        """
        self._stats["total_evaluations"] += 1
        firing_alerts = []

        with self._lock:
            current_time = time.time()

            for rule_name, rule in self._rules.items():
                alert = self._alerts.get(rule_name)
                if not alert:
                    continue

                # 评估规则
                current_value = self._evaluate_rule(rule)

                previous_state = alert.state

                if current_value is not None:
                    # 条件满足
                    if alert.state == AlertState.INACTIVE:
                        # 进入 pending 状态
                        alert.state = AlertState.PENDING
                        alert.pending_since = current_time
                        alert.value = current_value
                        alert.message = rule.annotations.get("summary", f"告警 {rule_name} 已触发")

                    elif alert.state == AlertState.PENDING:
                        # 检查是否达到持续时间
                        if current_time - alert.pending_since >= self.pending_duration:
                            # 进入 firing 状态
                            alert.state = AlertState.FIRING
                            alert.started_at = alert.pending_since
                            alert.fire_count += 1
                            self._stats["alerts_triggered"] += 1
                            # 结构化日志：告警触发（含指标值与阈值，便于排查误报）
                            logger.warning(json.dumps({
                                "trace_id": get_trace_id(),
                                "module_name": "alert_evaluator",
                                "action": "alert_firing",
                                "duration_ms": 0,
                                "alert_name": rule_name,
                                "severity": alert.severity.value,
                                "value": current_value,
                                "threshold": rule.threshold
                            }, ensure_ascii=False))

                    elif alert.state == AlertState.FIRING:
                        # 更新值
                        alert.value = current_value

                else:
                    # 条件不满足
                    if alert.state in (AlertState.PENDING, AlertState.FIRING):
                        # 恢复
                        alert.state = AlertState.INACTIVE
                        alert.resolved_at = current_time
                        alert.pending_since = None
                        if previous_state == AlertState.FIRING:
                            self._stats["alerts_resolved"] += 1
                        # 结构化日志：告警恢复（含持续时长 ms，便于统计告警 MTTR）
                        # 规范统一：使用 duration_ms（原 duration_seconds 已废弃，统一为毫秒）
                        resolved_duration_ms = int((alert.resolved_at - alert.started_at) * 1000) if alert.started_at else 0
                        logger.info(json.dumps({
                            "trace_id": get_trace_id(),
                            "module_name": "alert_evaluator",
                            "action": "alert_resolved",
                            "duration_ms": resolved_duration_ms,
                            "alert_name": rule_name,
                            "previous_state": previous_state.value
                        }, ensure_ascii=False))

                # 触发状态变化回调
                if previous_state != alert.state and self._on_alert_state_change:
                    try:
                        self._on_alert_state_change(alert, previous_state, alert.state)
                    except Exception as e:
                        logger.error(f"[Alert] 状态变化回调失败: {e}")

                # 收集 firing 的告警
                if alert.state == AlertState.FIRING:
                    firing_alerts.append(alert)

                    # 执行自愈
                    if rule.auto_heal and alert.fire_count >= rule.heal_threshold:
                        if self._on_heal_action:
                            for action in rule.heal_actions:
                                try:
                                    success = self._on_heal_action(alert, action)
                                    if success:
                                        self._stats["heal_actions_executed"] += 1
                                except Exception as e:
                                    logger.error(f"[Alert] 自愈动作 {action} 执行失败: {e}")

        return firing_alerts

    def start(self):
        """启动告警评估"""
        if self._running:
            return

        self._running = True
        self._evaluation_thread = threading.Thread(
            target=self._evaluation_loop,
            name="alert-evaluator",
            daemon=True
        )
        self._evaluation_thread.start()
        # 结构化日志：评估器启动
        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "alert_evaluator",
            "action": "start",
            "duration_ms": 0,
            "evaluation_interval": self.evaluation_interval
        }, ensure_ascii=False))

    def stop(self):
        """停止告警评估"""
        self._running = False
        if self._evaluation_thread:
            self._evaluation_thread.join(timeout=5)
        # 结构化日志：评估器停止
        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "alert_evaluator",
            "action": "stop",
            "duration_ms": 0,
            "total_evaluations": self._stats["total_evaluations"],
            "alerts_triggered": self._stats["alerts_triggered"],
            "alerts_resolved": self._stats["alerts_resolved"]
        }, ensure_ascii=False))

    def _evaluation_loop(self):
        """评估循环（后台线程入口）

        注意：ContextVar 不自动继承到子线程，因此在线程入口设置评估器专属 trace_id，
        使后台评估产生的所有日志（含 evaluate() 内部日志）均可追溯到此评估器实例。
        外部直接调用 evaluate() 时仍使用调用方的 trace_id。
        """
        # 后台线程入口：设置评估器专属 trace_id（覆盖默认的 None）
        set_trace_id(self._evaluator_trace_id)
        while self._running:
            try:
                self.evaluate()
            except Exception as e:
                # 结构化日志：评估循环异常（含错误信息，便于排查评估故障）
                logger.error(json.dumps({
                    "trace_id": get_trace_id(),
                    "module_name": "alert_evaluator",
                    "action": "evaluation_error",
                    "duration_ms": 0,
                    "error": str(e)
                }, ensure_ascii=False))
            time.sleep(self.evaluation_interval)

    def get_alerts(self, state: Optional[AlertState] = None) -> List[Dict]:
        """获取告警列表

        Args:
            state: 可选，按状态过滤

        Returns:
            告警列表
        """
        with self._lock:
            alerts = list(self._alerts.values())
            if state:
                alerts = [a for a in alerts if a.state == state]
            return [a.to_dict() for a in alerts]

    def get_stats(self) -> Dict:
        """获取统计信息"""
        return dict(self._stats)

    def get_firing_alerts(self) -> List[Dict]:
        """获取当前触发的告警"""
        return self.get_alerts(AlertState.FIRING)

    def get_pending_alerts(self) -> List[Dict]:
        """获取 pending 状态的告警"""
        return self.get_alerts(AlertState.PENDING)


# 全局单例
_alert_evaluator: Optional[AlertEvaluator] = None


def get_alert_evaluator() -> AlertEvaluator:
    """获取全局告警评估器实例

    Returns:
        AlertEvaluator 实例
    """
    global _alert_evaluator
    if _alert_evaluator is None:
        _alert_evaluator = AlertEvaluator()
    return _alert_evaluator


def start_alert_evaluator(evaluation_interval: float = 30.0) -> AlertEvaluator:
    """启动全局告警评估器

    Args:
        evaluation_interval: 评估间隔

    Returns:
        AlertEvaluator 实例
    """
    evaluator = get_alert_evaluator()
    evaluator.start()
    return evaluator
