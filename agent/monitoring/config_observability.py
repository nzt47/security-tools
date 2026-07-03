"""配置变更可观测性模块

将 ObservabilityConfig 的 _change_log 升级为可观测事件流：
1. Loki 推送：异步推送配置变更事件到 Loki，失败时降级到本地日志
2. Prometheus 指标：暴露 config_changes_total（Counter）和 config_value（Gauge）
3. Alert 触发：高风险配置变更（如 pool_size > 50）触发告警通知

使用方式（由 observability_config.py 的 set() 方法在变更记录后调用）：
    from agent.monitoring.config_observability import on_config_changed
    on_config_changed(change_record)

设计要点：
- 三路并行处理，互不阻塞主流程
- 全部使用 lazy import 避免循环导入
- 任何一路失败都不影响配置变更本身和其它路
"""

import json
import logging
import threading
import time
from typing import Any, Dict, Optional

from agent.monitoring.tracing import get_trace_id

logger = logging.getLogger(__name__)


# ============================================================================
# Prometheus 指标定义（延迟初始化，避免循环导入）
# ============================================================================

_config_changes_counter = None
_config_value_gauge = None
_metrics_initialized = False


def _init_metrics():
    """延迟初始化 Prometheus 指标（仅初始化一次）"""
    global _config_changes_counter, _config_value_gauge, _metrics_initialized
    if _metrics_initialized:
        return
    _metrics_initialized = True
    try:
        from agent.monitoring.prometheus import _safe_counter, _safe_gauge
        _config_changes_counter = _safe_counter(
            'config_changes_total',
            '配置变更总数（按配置路径分维度）',
            ['config_path']
        )
        _config_value_gauge = _safe_gauge(
            'config_value',
            '配置项当前值（仅数值类型）',
            ['config_path']
        )
    except Exception as e:
        logger.debug(f"无法初始化配置变更 Prometheus 指标（非致命）: {e}")


# ============================================================================
# 高风险配置变更规则
# ============================================================================

# 高风险配置路径及其安全阈值
# 当配置变更后的值超出安全阈值时触发告警通知
HIGH_RISK_RULES: Dict[str, Dict[str, Any]] = {
    # 连接池/重试次数过大可能导致资源耗尽或雪崩
    "http.pool_size": {"max": 50, "description": "HTTP 连接池大小"},
    "http.max_retries": {"max": 10, "description": "HTTP 最大重试次数"},
    "retry.default_max_retries": {"max": 10, "description": "默认最大重试次数"},
    # 缓存/池过大可能导致内存溢出
    "cache.l1_max_size": {"max": 10000, "description": "L1 缓存最大条目数"},
    "tracing.span_pool_size": {"max": 5000, "description": "Span 对象池大小"},
    "tracing.context_max_size": {"max": 5000, "description": "追踪上下文缓存容量"},
    # 采样间隔过小可能导致 CPU 过载
    "resource_monitor.sample_interval_sec": {"min": 1, "description": "资源采样间隔（秒）"},
}


def _check_high_risk(key: str, new_value: Any) -> Optional[Dict[str, Any]]:
    """检查配置变更是否为高风险

    Args:
        key: 配置路径
        new_value: 变更后的值

    Returns:
        高风险详情 dict（含 key/value/threshold/description），None 表示非高风险
    """
    rule = HIGH_RISK_RULES.get(key)
    if not rule:
        return None
    try:
        v = float(new_value)
    except (TypeError, ValueError):
        return None

    if "max" in rule and v > rule["max"]:
        return {
            "key": key,
            "value": v,
            "threshold": rule["max"],
            "direction": "exceeds_max",
            "description": rule["description"],
        }
    if "min" in rule and v < rule["min"]:
        return {
            "key": key,
            "value": v,
            "threshold": rule["min"],
            "direction": "below_min",
            "description": rule["description"],
        }
    return None


# ============================================================================
# 核心入口：配置变更通知
# ============================================================================

def on_config_changed(change_record: Dict[str, Any]) -> None:
    """配置变更通知入口（由 ObservabilityConfig.set() 调用）

    三路并行处理，互不阻塞：
    1. Prometheus 指标更新（同步，< 0.1ms）
    2. Loki 异步推送（daemon 线程）
    3. 高风险变更告警（daemon 线程，仅高风险时触发）

    Args:
        change_record: 变更记录 {timestamp, key, old_value, new_value, duration_ms}
    """
    # 1. Prometheus 指标更新（同步，极快）
    try:
        _init_metrics()
        if _config_changes_counter and _config_value_gauge:
            _config_changes_counter.labels(config_path=change_record["key"]).inc()
            try:
                _config_value_gauge.labels(config_path=change_record["key"]).set(
                    float(change_record["new_value"])
                )
            except (TypeError, ValueError):
                pass  # 非数值类型不更新 Gauge
    except Exception as e:
        logger.debug(f"Prometheus 指标更新失败（非致命）: {e}")

    # 2. Loki 异步推送
    threading.Thread(
        target=_push_to_loki,
        args=(change_record,),
        daemon=True,
        name="config-change-loki"
    ).start()

    # 3. 高风险变更告警（异步，仅高风险时触发）
    high_risk = _check_high_risk(change_record["key"], change_record["new_value"])
    if high_risk:
        threading.Thread(
            target=_trigger_alert,
            args=(change_record, high_risk),
            daemon=True,
            name="config-change-alert"
        ).start()


# ============================================================================
# Loki 推送
# ============================================================================

def _push_to_loki(change_record: Dict[str, Any]) -> None:
    """异步推送配置变更事件到 Loki

    推送格式：JSON {config_path, old_value, new_value, operator, trace_id, duration_ms}
    失败时降级到本地日志（LokiClient 内部已处理降级逻辑）
    """
    try:
        from agent.monitoring.loki import LokiClient
        client = LokiClient()

        message = json.dumps({
            "config_path": change_record["key"],
            "old_value": change_record["old_value"],
            "new_value": change_record["new_value"],
            "operator": "system",
            "trace_id": get_trace_id(),
            "duration_ms": change_record.get("duration_ms", 0),
        }, ensure_ascii=False)

        client.push_log(
            labels={"app": "agent-config", "event": "change"},
            message=message,
            timestamp=change_record.get("timestamp", time.time())
        )
    except Exception as e:
        # Loki 推送失败不影响主流程，LokiClient 内部已降级到本地文件
        logger.debug(f"Loki 推送配置变更失败（已降级）: {e}")


# ============================================================================
# Alert 告警触发
# ============================================================================

def _trigger_alert(change_record: Dict[str, Any], high_risk: Dict[str, Any]) -> None:
    """异步触发高风险配置变更告警

    通过 alert_notifier.send_alert_notification() 发送告警到配置的通知渠道。
    """
    try:
        from agent.monitoring.alert_notifier import send_alert_notification

        direction_text = "超过上限" if high_risk["direction"] == "exceeds_max" else "低于下限"
        send_alert_notification(
            alert_name=f"HighRiskConfigChange:{high_risk['key']}",
            state="firing",
            severity="warning",
            message=(
                f"高风险配置变更: {high_risk['description']} "
                f"从 {change_record['old_value']} 改为 {change_record['new_value']} "
                f"（安全阈值{direction_text}: {high_risk['threshold']}）"
            ),
            value=float(high_risk["value"]),
            threshold=float(high_risk["threshold"]),
            labels={
                "config_path": high_risk["key"],
                "event": "high_risk_config_change",
                "direction": high_risk["direction"],
            },
            annotations={
                "old_value": str(change_record["old_value"]),
                "new_value": str(change_record["new_value"]),
            },
            trace_id=get_trace_id()
        )
        logger.warning(
            f"高风险配置变更告警已触发: {high_risk['key']}={change_record['new_value']} "
            f"(阈值{direction_text}: {high_risk['threshold']})"
        )
    except Exception as e:
        logger.error(f"触发配置变更告警失败: {e}")


# ============================================================================
# 查询接口（供 Prometheus /metrics 端点使用）
# ============================================================================

def get_config_changes_counter():
    """获取 config_changes_total Counter（供外部指标注册使用）"""
    _init_metrics()
    return _config_changes_counter


def get_config_value_gauge():
    """获取 config_value Gauge（供外部指标注册使用）"""
    _init_metrics()
    return _config_value_gauge
