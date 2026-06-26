#!/usr/bin/env python3
"""
生产环境失败案例自动收集与告警模块

功能：
1. 自动从日志系统中收集失败案例
2. 自动分类和归档到失败分析器
3. 配置告警规则，支持多渠道告警
4. 支持告警静默、阈值控制、分级告警
"""

import json
import logging
import time
import threading
from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass, field
from enum import Enum

from agent.cognitive.failure_analysis import (
    FailureAnalyzer, FailureRecord, FailureType, FailureSeverity,
    get_failure_analyzer
)

logger = logging.getLogger(__name__)


class AlertChannel(Enum):
    """告警渠道"""
    CONSOLE = "console"       # 控制台输出
    LOG = "log"               # 日志记录
    WEBHOOK = "webhook"       # Webhook 回调
    EMAIL = "email"           # 邮件告警


class AlertLevel(Enum):
    """告警级别"""
    INFO = "info"             # 信息
    WARNING = "warning"       # 警告
    CRITICAL = "critical"     # 严重
    FATAL = "fatal"           # 致命


@dataclass
class AlertRule:
    """告警规则"""
    rule_id: str
    name: str
    failure_type: Optional[FailureType] = None      # 失败类型（None表示所有类型）
    min_severity: FailureSeverity = FailureSeverity.MEDIUM  # 最低严重程度
    threshold: int = 1                              # 时间窗口内达到多少次触发
    time_window_seconds: int = 3600                 # 时间窗口（秒）
    channel: AlertChannel = AlertChannel.LOG        # 告警渠道
    enabled: bool = True
    cooldown_seconds: int = 300                     # 冷却时间（秒），防止告警风暴
    last_alert_time: float = 0.0
    description: str = ""


@dataclass
class AlertEvent:
    """告警事件"""
    alert_id: str
    rule_id: str
    failure_type: str
    severity: str
    count: int
    time_window_seconds: int
    triggered_at: float
    message: str
    details: Dict[str, Any] = field(default_factory=dict)


class FailureCollector:
    """失败案例收集器
    
    自动从日志系统/错误处理器中收集失败案例，
    分类归档到失败分析器，并触发告警。
    """
    
    def __init__(self, analyzer: FailureAnalyzer = None):
        self.analyzer = analyzer or get_failure_analyzer()
        self._alert_rules: List[AlertRule] = []
        self._alert_handlers: Dict[AlertChannel, Callable] = {}
        self._failure_counts: Dict[str, List[float]] = {}  # 按失败类型记录时间戳
        self._lock = threading.Lock()
        self._initialized = False
        self._register_default_handlers()
    
    def initialize(self):
        """初始化收集器"""
        if self._initialized:
            return
        
        # 注册默认告警规则
        self._register_default_rules()
        
        self._initialized = True
        logger.info(json.dumps({
            "trace_id": "",
            "module_name": "failure_collector",
            "action": "initialize",
            "rule_count": len(self._alert_rules),
            "duration_ms": 0,
            "level": "INFO"
        }))
        logger.info("[FailureCollector] 失败案例收集器初始化完成")
    
    def _register_default_handlers(self):
        """注册默认告警处理器"""
        self._alert_handlers[AlertChannel.CONSOLE] = self._handle_console_alert
        self._alert_handlers[AlertChannel.LOG] = self._handle_log_alert
    
    def _register_default_rules(self):
        """注册默认告警规则"""
        default_rules = [
            AlertRule(
                rule_id="critical_api_fiction",
                name="严重API虚构告警",
                failure_type=FailureType.API_FICTION,
                min_severity=FailureSeverity.CRITICAL,
                threshold=1,
                time_window_seconds=3600,
                channel=AlertChannel.LOG,
                description="发生严重的API虚构问题时立即告警"
            ),
            AlertRule(
                rule_id="high_data_invention",
                name="高频数据虚构告警",
                failure_type=FailureType.DATA_INVENTION,
                min_severity=FailureSeverity.HIGH,
                threshold=3,
                time_window_seconds=3600,
                channel=AlertChannel.LOG,
                description="1小时内发生3次以上数据虚构时告警"
            ),
            AlertRule(
                rule_id="flow_skip_warning",
                name="流程跳步告警",
                failure_type=FailureType.FLOW_SKIP,
                min_severity=FailureSeverity.HIGH,
                threshold=2,
                time_window_seconds=1800,
                channel=AlertChannel.LOG,
                description="30分钟内发生2次以上流程跳步时告警"
            ),
            AlertRule(
                rule_id="any_critical",
                name="任意严重失败告警",
                failure_type=None,  # 所有类型
                min_severity=FailureSeverity.CRITICAL,
                threshold=1,
                time_window_seconds=600,
                channel=AlertChannel.LOG,
                description="10分钟内发生任意严重失败立即告警"
            ),
        ]
        
        for rule in default_rules:
            self.add_alert_rule(rule)
    
    def add_alert_rule(self, rule: AlertRule):
        """添加告警规则"""
        with self._lock:
            self._alert_rules.append(rule)
            logger.info(f"[FailureCollector] 添加告警规则: {rule.name} ({rule.rule_id})")
    
    def remove_alert_rule(self, rule_id: str) -> bool:
        """移除告警规则"""
        with self._lock:
            for i, rule in enumerate(self._alert_rules):
                if rule.rule_id == rule_id:
                    self._alert_rules.pop(i)
                    logger.info(f"[FailureCollector] 移除告警规则: {rule_id}")
                    return True
        return False
    
    def register_alert_handler(self, channel: AlertChannel, handler: Callable):
        """注册自定义告警处理器"""
        self._alert_handlers[channel] = handler
        logger.info(f"[FailureCollector] 注册告警处理器: {channel.value}")
    
    def collect_failure(self, trace_id: str, message: str, source: str = "",
                       severity: FailureSeverity = FailureSeverity.MEDIUM,
                       context: Dict[str, Any] = None,
                       evidence: List[str] = None) -> FailureRecord:
        """收集并处理失败案例
        
        Args:
            trace_id: 追踪ID
            message: 失败消息
            source: 来源模块
            severity: 严重程度
            context: 上下文信息
            evidence: 证据列表
        
        Returns:
            失败记录
        """
        # 自动分类失败类型
        failure_type = self.analyzer.classify_failure(message)
        
        # 生成优化建议
        suggested_fix = self.analyzer.generate_fix_suggestion(failure_type)
        
        # 创建失败记录
        record = FailureRecord(
            trace_id=trace_id,
            failure_type=failure_type,
            severity=severity,
            message=message,
            source=source,
            context=context or {},
            evidence=evidence or [],
            suggested_fix=suggested_fix
        )
        
        # 记录到失败分析器
        self.analyzer.record_failure(record)
        
        # 检查告警规则
        self._check_alert_rules(failure_type, severity, trace_id, record)
        
        return record
    
    def _check_alert_rules(self, failure_type: FailureType, severity: FailureSeverity,
                          trace_id: str, record: FailureRecord):
        """检查告警规则并触发告警"""
        now = time.time()
        
        with self._lock:
            # 更新失败计数
            type_key = failure_type.value
            if type_key not in self._failure_counts:
                self._failure_counts[type_key] = []
            self._failure_counts[type_key].append(now)
            
            # 也更新"所有类型"的计数
            all_key = "__all__"
            if all_key not in self._failure_counts:
                self._failure_counts[all_key] = []
            self._failure_counts[all_key].append(now)
            
            # 检查每条规则
            for rule in self._alert_rules:
                if not rule.enabled:
                    continue
                
                # 检查失败类型是否匹配
                if rule.failure_type is not None and rule.failure_type != failure_type:
                    continue
                
                # 检查严重程度是否达标
                severity_order = {
                    FailureSeverity.LOW: 1,
                    FailureSeverity.MEDIUM: 2,
                    FailureSeverity.HIGH: 3,
                    FailureSeverity.CRITICAL: 4,
                }
                if severity_order.get(severity, 0) < severity_order.get(rule.min_severity, 0):
                    continue
                
                # 计算时间窗口内的失败次数
                count_key = type_key if rule.failure_type else all_key
                counts = self._failure_counts.get(count_key, [])
                window_start = now - rule.time_window_seconds
                recent_count = sum(1 for t in counts if t >= window_start)
                
                # 检查是否达到阈值
                if recent_count >= rule.threshold:
                    # 检查冷却时间
                    if now - rule.last_alert_time < rule.cooldown_seconds:
                        continue
                    
                    rule.last_alert_time = now
                    self._trigger_alert(rule, failure_type, severity, recent_count, trace_id, record)
    
    def _trigger_alert(self, rule: AlertRule, failure_type: FailureType,
                      severity: FailureSeverity, count: int,
                      trace_id: str, record: FailureRecord):
        """触发告警"""
        alert = AlertEvent(
            alert_id=f"alert_{int(time.time())}_{rule.rule_id}",
            rule_id=rule.rule_id,
            failure_type=failure_type.value,
            severity=severity.value,
            count=count,
            time_window_seconds=rule.time_window_seconds,
            triggered_at=time.time(),
            message=f"[告警] {rule.name}: {count}次 {failure_type.value} 失败",
            details={
                "rule_name": rule.name,
                "threshold": rule.threshold,
                "trace_id": trace_id,
                "source": record.source,
                "suggested_fix": record.suggested_fix
            }
        )
        
        # 调用对应渠道的处理器
        handler = self._alert_handlers.get(rule.channel)
        if handler:
            try:
                handler(alert)
            except Exception as e:
                logger.error(f"[FailureCollector] 告警处理器执行失败: {e}")
        
        # 同时记录到日志
        self._handle_log_alert(alert)
        
        logger.warning(json.dumps({
            "trace_id": trace_id,
            "module_name": "failure_collector",
            "action": "alert_triggered",
            "rule_id": rule.rule_id,
            "failure_type": failure_type.value,
            "severity": severity.value,
            "count": count,
            "alert_id": alert.alert_id,
            "duration_ms": 0,
            "level": "WARNING"
        }))
    
    def _handle_console_alert(self, alert: AlertEvent):
        """控制台告警处理器"""
        print(f"\n{'='*60}")
        print(f"⚠️  告警: {alert.message}")
        print(f"{'='*60}")
        print(f"  告警ID: {alert.alert_id}")
        print(f"  失败类型: {alert.failure_type}")
        print(f"  严重程度: {alert.severity}")
        print(f"  发生次数: {alert.count}")
        print(f"  时间窗口: {alert.time_window_seconds}秒")
        if 'trace_id' in alert.details:
            print(f"  Trace ID: {alert.details['trace_id']}")
        if 'suggested_fix' in alert.details:
            print(f"  建议修复: {alert.details['suggested_fix'][:100]}...")
        print()
    
    def _handle_log_alert(self, alert: AlertEvent):
        """日志告警处理器"""
        alert_level = AlertLevel.WARNING
        if alert.severity in ['critical', 'fatal']:
            alert_level = AlertLevel.CRITICAL
        
        log_msg = (
            f"[告警-{alert_level.value.upper()}] {alert.message} | "
            f"count={alert.count} window={alert.time_window_seconds}s "
            f"trace_id={alert.details.get('trace_id', 'N/A')}"
        )
        
        if alert_level == AlertLevel.CRITICAL:
            logger.critical(log_msg)
        else:
            logger.warning(log_msg)
    
    def get_alert_rules(self) -> List[Dict[str, Any]]:
        """获取所有告警规则"""
        with self._lock:
            return [
                {
                    "rule_id": r.rule_id,
                    "name": r.name,
                    "failure_type": r.failure_type.value if r.failure_type else "all",
                    "min_severity": r.min_severity.value,
                    "threshold": r.threshold,
                    "time_window_seconds": r.time_window_seconds,
                    "channel": r.channel.value,
                    "enabled": r.enabled,
                    "cooldown_seconds": r.cooldown_seconds,
                    "description": r.description
                }
                for r in self._alert_rules
            ]
    
    def get_failure_statistics(self, hours: int = 24) -> Dict[str, Any]:
        """获取失败统计信息"""
        summary = self.analyzer.get_failure_summary(hours=hours)
        
        # 添加告警规则状态
        summary["alert_rules"] = self.get_alert_rules()
        
        return summary


# 全局收集器实例
_global_failure_collector = None

def get_failure_collector() -> FailureCollector:
    """获取全局失败案例收集器实例"""
    global _global_failure_collector
    if _global_failure_collector is None:
        _global_failure_collector = FailureCollector()
        _global_failure_collector.initialize()
    return _global_failure_collector


def collect_failure(trace_id: str, message: str, source: str = "",
                   severity: FailureSeverity = FailureSeverity.MEDIUM,
                   **kwargs) -> FailureRecord:
    """便捷函数：收集失败案例
    
    生产环境日志系统中可以直接调用此函数来收集失败。
    """
    collector = get_failure_collector()
    return collector.collect_failure(
        trace_id=trace_id,
        message=message,
        source=source,
        severity=severity,
        **kwargs
    )