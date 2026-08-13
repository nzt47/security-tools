#!/usr/bin/env python3
"""
告警系统集成模块

整合告警评估、通知和自愈功能，提供统一的告警管理接口。

主要功能：
1. 告警规则管理（从 YAML 配置加载）
2. 告警状态监控
3. 告警通知发送
4. 自愈动作执行
5. 告警历史和统计
"""

import json
import logging
import time
import threading
import os
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass

from agent.monitoring.tracing import get_trace_id

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False
    logging.warning("[Alert] yaml 模块不可用")

logger = logging.getLogger(__name__)

# SingletonManager 统一收口（保留 fallback 变量 _alert_manager 向后兼容）
try:
    from agent.utils.singleton_manager import (
        register_singleton, get_singleton, reset_singleton, is_initialized,
    )
    _SINGLETON_AVAILABLE = True
except ImportError:
    _SINGLETON_AVAILABLE = False
    register_singleton = get_singleton = reset_singleton = is_initialized = None


# 导入子模块
from agent.monitoring.alert_evaluator import (
    AlertEvaluator,
    AlertRule,
    Alert,
    AlertState,
    AlertSeverity,
    get_alert_evaluator,
    start_alert_evaluator
)

from agent.monitoring.alert_notifier import (
    AlertNotifier,
    AlertNotification,
    NotificationChannel,
    get_alert_notifier,
    send_alert_notification
)

from agent.monitoring.self_healer import (
    SelfHealer,
    HealAction,
    HealResult,
    HealStatus,
    get_self_healer,
    execute_heal_action
)


class AlertManager:
    """告警系统管理器

    整合告警评估、通知和自愈功能。
    """

    def __init__(self, config_path: Optional[str] = None):
        """
        Args:
            config_path: 告警配置 YAML 文件路径
        """
        self.config_path = config_path
        self._config: Dict[str, Any] = {}

        # 初始化子模块
        self._evaluator: Optional[AlertEvaluator] = None
        self._notifier: Optional[AlertNotifier] = None
        self._healer: Optional[SelfHealer] = None

        # 运行状态
        self._running = False
        self._lock = threading.Lock()

        # 回调函数
        self._on_alert_callback: Optional[Callable] = None

        # 加载配置
        self._load_config()

        # 初始化组件
        self._init_components()

        # 结构化日志（使用 json.dumps 输出，trace_id 通过 get_trace_id 获取）
        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "alert_manager",
            "action": "init",
            "duration_ms": 0,
            "config_path": config_path,
            "rules_count": len(self._config.get("groups", []))
        }, ensure_ascii=False))

    def _load_config(self):
        """加载告警配置"""
        if not self.config_path:
            # 默认路径
            self.config_path = os.path.join(
                os.path.dirname(__file__),
                "alerts.yml"
            )

        if not os.path.exists(self.config_path):
            # 结构化日志：边界显性化 - 文件不存在时记录明确错误码 file_not_found
            logger.warning(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "alert_manager",
                "action": "config_load_failed",
                "duration_ms": 0,
                "config_path": self.config_path,
                "error": "file_not_found"
            }, ensure_ascii=False))
            # 使用默认配置
            self._config = self._get_default_config()
            return

        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self._config = yaml.safe_load(f) or {}

            # 结构化日志：配置加载成功
            logger.info(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "alert_manager",
                "action": "config_loaded",
                "duration_ms": 0,
                "config_path": self.config_path,
                "groups_count": len(self._config.get("groups", []))
            }, ensure_ascii=False))
        except Exception as e:
            logger.error(f"[AlertManager] 配置加载失败: {e}")
            self._config = self._get_default_config()

    def _get_default_config(self) -> Dict[str, Any]:
        """获取默认配置"""
        return {
            "groups": [],
            "alert_router": {
                "default_receiver": "default-notifications"
            },
            "notification": {
                "channels": []
            },
            "self_healing": {
                "enabled": True
            }
        }

    def _init_components(self):
        """初始化组件"""
        # 初始化评估器
        self._evaluator = AlertEvaluator(
            evaluation_interval=30.0,
            pending_duration=60.0
        )

        # 注册告警规则
        self._register_rules()

        # 设置评估器回调
        self._evaluator.set_on_state_change(self._on_alert_state_change)

        # 初始化通知器
        self._notifier = AlertNotifier(
            self._config.get("notification", {})
        )

        # 初始化自愈器
        self._healer = SelfHealer(self._config)

        # 设置自愈回调
        self._healer.set_on_heal_executed(self._on_heal_executed)

    def _register_rules(self):
        """注册告警规则"""
        groups = self._config.get("groups", [])

        for group in groups:
            group_name = group.get("name", "default")
            rules = group.get("rules", [])

            for rule_config in rules:
                try:
                    rule = AlertRule(
                        name=rule_config.get("alert", rule_config.get("name", "")),
                        expr=rule_config.get("expr", ""),
                        duration=rule_config.get("for", "5m"),
                        severity=rule_config.get("labels", {}).get("severity", "warning"),
                        labels=rule_config.get("labels", {}),
                        annotations=rule_config.get("annotations", {}),
                        threshold=self._parse_threshold(rule_config.get("expr", "")),
                        comparison=self._parse_comparison(rule_config.get("expr", ""))
                    )
                    self._evaluator.add_rule(rule)
                except Exception as e:
                    logger.error(f"[AlertManager] 注册规则失败: {e}")

    def _parse_threshold(self, expr: str) -> Optional[float]:
        """从表达式解析阈值"""
        import re
        # 匹配 > < >= <= == != 数字
        patterns = [
            r'>([\d.]+)',
            r'<([\d.]+)',
            r'>=([\d.]+)',
            r'<=([\d.]+)',
            r'==([\d.]+)',
            r'!=([\d.]+)'
        ]
        for pattern in patterns:
            match = re.search(pattern, expr)
            if match:
                return float(match.group(1))
        return None

    def _parse_comparison(self, expr: str) -> str:
        """从表达式解析比较方式"""
        if '>=' in expr:
            return "gte"
        elif '<=' in expr:
            return "lte"
        elif '==' in expr:
            return "eq"
        elif '!=' in expr:
            return "ne"
        elif '>' in expr:
            return "gt"
        elif '<' in expr:
            return "lt"
        return "gt"

    def _on_evaluator_state_change(
        self,
        alert: Alert,
        previous_state: AlertState,
        new_state: AlertState
    ):
        """评估器状态变化回调"""
        # 发送通知
        if new_state == AlertState.FIRING:
            self._send_alert_notification(alert)

            # 触发自愈检查
            self._check_heal_action(alert)

        elif new_state == AlertState.INACTIVE and previous_state == AlertState.FIRING:
            # 告警恢复
            self._send_recovery_notification(alert)

        # 调用外部回调
        if self._on_alert_callback:
            try:
                self._on_alert_callback(alert, previous_state, new_state)
            except Exception as e:
                logger.error(f"[AlertManager] 告警回调失败: {e}")

    def _on_alert_state_change(
        self,
        alert: Alert,
        previous_state: AlertState,
        new_state: AlertState
    ):
        """告警状态变化处理"""
        pass

    def _on_heal_executed(self, record):
        """自愈执行完成回调"""
        # 结构化日志：自愈动作执行完成（修复原代码 action 键重复问题）
        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "alert_manager",
            "action": "heal_executed",
            "duration_ms": 0,
            "heal_action": record.action,
            "status": record.status.value,
            "message": record.message
        }, ensure_ascii=False))

    def _send_alert_notification(self, alert: Alert):
        """发送告警通知"""
        try:
            notification = AlertNotification(
                alert_name=alert.name,
                state="firing",
                severity=alert.severity.value,
                message=alert.message,
                value=alert.value,
                threshold=alert.threshold,
                duration_seconds=time.time() - alert.started_at if alert.started_at else 0,
                labels=alert.labels,
                annotations=alert.annotations
            )

            # 根据严重级别选择通知渠道
            if alert.severity == AlertSeverity.CRITICAL:
                self._notifier.send_critical(notification)
            else:
                self._notifier.send(notification)

        except Exception as e:
            logger.error(f"[AlertManager] 发送告警通知失败: {e}")

    def _send_recovery_notification(self, alert: Alert):
        """发送恢复通知"""
        try:
            notification = AlertNotification(
                alert_name=alert.name,
                state="resolved",
                severity=alert.severity.value,
                message=f"告警已恢复，持续时间: {alert.resolved_at - alert.started_at:.1f}秒" if alert.started_at and alert.resolved_at else "告警已恢复",
                value=alert.value,
                threshold=alert.threshold,
                duration_seconds=alert.resolved_at - alert.started_at if alert.started_at and alert.resolved_at else 0,
                labels=alert.labels,
                annotations=alert.annotations
            )

            self._notifier.send_recovery(notification)

        except Exception as e:
            logger.error(f"[AlertManager] 发送恢复通知失败: {e}")

    def _check_heal_action(self, alert: Alert):
        """检查是否需要执行自愈动作"""
        # 从配置中查找告警对应的自愈动作
        groups = self._config.get("groups", [])
        for group in groups:
            for rule in group.get("rules", []):
                if rule.get("alert") == alert.name:
                    if rule.get("labels", {}).get("auto_heal"):
                        actions = rule.get("heal_actions", ["restart_service"])
                        for action in actions:
                            self._healer.execute_action(action, {"alert_name": alert.name})

    def set_on_alert(self, callback: Callable[[Alert, AlertState, AlertState], None]):
        """设置告警回调

        Args:
            callback: 回调函数 (alert, previous_state, new_state)
        """
        self._on_alert_callback = callback

    def start(self):
        """启动告警管理系统"""
        # [2026-08-13 并发审计] 检查-置位原子化（子组件 start 移出锁外）
        with self._lock:
            if self._running:
                return
            self._running = True

        # 启动评估器
        if self._evaluator:
            self._evaluator.start()

        # 启动自愈器
        if self._healer:
            self._healer.start()

        # 结构化日志：告警管理系统启动完成
        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "alert_manager",
            "action": "start",
            "duration_ms": 0
        }, ensure_ascii=False))

    def stop(self):
        """停止告警管理系统"""
        self._running = False

        # 停止评估器
        if self._evaluator:
            self._evaluator.stop()

        # 停止自愈器
        if self._healer:
            self._healer.stop()

        # 结构化日志：告警管理系统停止完成
        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "alert_manager",
            "action": "stop",
            "duration_ms": 0
        }, ensure_ascii=False))

    def get_alerts(
        self,
        state: Optional[str] = None,
        severity: Optional[str] = None
    ) -> List[Dict]:
        """获取告警列表

        Args:
            state: 按状态过滤 (firing, pending, inactive)
            severity: 按严重级别过滤 (critical, warning, info)

        Returns:
            告警列表
        """
        if not self._evaluator:
            return []

        alerts = self._evaluator.get_alerts()

        if state:
            alerts = [a for a in alerts if a["state"] == state]
        if severity:
            alerts = [a for a in alerts if a["severity"] == severity]

        return alerts

    def get_firing_alerts(self) -> List[Dict]:
        """获取当前触发的告警"""
        return self.get_alerts(state="firing")

    def get_stats(self) -> Dict:
        """获取告警统计"""
        if not self._evaluator:
            return {}

        return {
            "evaluator": self._evaluator.get_stats(),
            "notifier": self._notifier.get_stats() if self._notifier else {},
            "healer": self._healer.get_stats() if self._healer else {},
            "firing_alerts": len(self.get_firing_alerts()),
            "pending_alerts": len(self.get_alerts(state="pending"))
        }

    def execute_heal(
        self,
        action: str,
        context: Optional[Dict[str, Any]] = None
    ) -> HealResult:
        """手动执行自愈动作

        Args:
            action: 动作名称
            context: 上下文

        Returns:
            执行结果
        """
        if not self._healer:
            return HealResult(action, HealStatus.SKIPPED, "自愈器未初始化", 0)

        return self._healer.execute_action(action, context)

    def get_heal_records(
        self,
        limit: int = 50,
        action: Optional[str] = None
    ) -> List[Dict]:
        """获取自愈记录

        Args:
            limit: 返回条数
            action: 按动作过滤

        Returns:
            记录列表
        """
        if not self._healer:
            return []

        return self._healer.get_records(limit, action)

    def add_rule(
        self,
        name: str,
        expr: str,
        duration: str = "5m",
        severity: str = "warning",
        labels: Optional[Dict[str, str]] = None,
        annotations: Optional[Dict[str, str]] = None
    ) -> bool:
        """动态添加告警规则

        Args:
            name: 规则名称
            expr: PromQL 表达式
            duration: 持续时间
            severity: 严重级别
            labels: 标签
            annotations: 注解

        Returns:
            是否成功
        """
        if not self._evaluator:
            return False

        try:
            rule = AlertRule(
                name=name,
                expr=expr,
                duration=duration,
                severity=severity,
                labels=labels or {},
                annotations=annotations or {},
                threshold=self._parse_threshold(expr),
                comparison=self._parse_comparison(expr)
            )
            self._evaluator.add_rule(rule)
            return True
        except Exception as e:
            logger.error(f"[AlertManager] 添加规则失败: {e}")
            return False

    def remove_rule(self, rule_name: str) -> bool:
        """移除告警规则

        Args:
            rule_name: 规则名称

        Returns:
            是否成功
        """
        if not self._evaluator:
            return False

        self._evaluator.remove_rule(rule_name)
        return True


# 全局单例
_alert_manager: Optional[AlertManager] = None  # 保留作为 fallback
# [2026-08-13 并发审计] fallback 单例双检锁：防并发首调创建多个实例
_alert_manager_lock = threading.Lock()


def _create_alert_manager(config=None):
    """AlertManager 工厂（供 SingletonManager 使用）

    config 可能以两种形态传入，需区分：
    - SingletonManager dict 通道：{"config_path": <str>}，需解包
    - 直接传入的 config_path（str 或 None）：原样传给 AlertManager
    """
    if isinstance(config, dict) and "config_path" in config:
        config = config["config_path"]
    return AlertManager(config)


def _cleanup_alert_manager(manager):
    """清理钩子：停止告警管理系统（仅测试重置时调用）"""
    if manager is not None:
        manager.stop()


def get_alert_manager(config_path: Optional[str] = None) -> AlertManager:
    """获取全局告警管理器

    Args:
        config_path: 配置路径

    Returns:
        AlertManager 实例
    """
    if _SINGLETON_AVAILABLE:
        if config_path is not None and not is_initialized("alert_manager"):
            return get_singleton("alert_manager", {"config_path": config_path})
        return get_singleton("alert_manager")
    global _alert_manager
    if _alert_manager is None:
        with _alert_manager_lock:
            if _alert_manager is None:
                _alert_manager = _create_alert_manager(config_path)
    return _alert_manager


def reset_alert_manager():
    """重置全局告警管理器单例（仅用于测试）"""
    global _alert_manager
    if _SINGLETON_AVAILABLE:
        reset_singleton("alert_manager")
    _alert_manager = None


def start_alert_manager(config_path: Optional[str] = None) -> AlertManager:
    """启动全局告警管理器

    Args:
        config_path: 配置路径

    Returns:
        AlertManager 实例
    """
    manager = get_alert_manager(config_path)
    manager.start()
    return manager


# 注册单例工厂（置于文件末尾，确保类已定义）
if _SINGLETON_AVAILABLE:
    register_singleton("alert_manager", _create_alert_manager,
                       cleanup_fn=_cleanup_alert_manager)
