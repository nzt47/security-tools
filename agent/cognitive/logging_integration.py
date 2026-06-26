#!/usr/bin/env python3
"""
生产环境日志系统集成配置

功能：
1. 将失败案例收集器自动集成到日志系统
2. 日志格式配置（JSON结构化日志）
3. 告警渠道配置（Webhook、邮件等）
4. 日志轮转和归档策略
"""

import json
import logging
import logging.handlers
import sys
from typing import Dict, Any, Optional

from agent.cognitive.failure_collector import (
    FailureCollector, AlertRule, AlertChannel,
    FailureSeverity, FailureType, get_failure_collector
)


class JsonFormatter(logging.Formatter):
    """JSON 格式日志格式化器
    
    输出结构化 JSON 日志，包含 trace_id、module_name、action、duration_ms 等字段
    """
    
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": getattr(record, "trace_id", ""),
            "module_name": getattr(record, "module_name", ""),
            "action": getattr(record, "action", ""),
            "duration_ms": getattr(record, "duration_ms", 0),
        }
        
        # 附加额外字段
        if hasattr(record, "extra_fields"):
            log_entry.update(record.extra_fields)
        
        # 异常信息
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        
        return json.dumps(log_entry, ensure_ascii=False)


class FailureLogFilter(logging.Filter):
    """失败日志过滤器
    
    自动从日志中识别失败案例并收集到失败分析器。
    """
    
    _collecting = False  # 防递归标志
    
    def __init__(self, collector: FailureCollector = None):
        super().__init__()
        self.collector = collector or get_failure_collector()
    
    def filter(self, record: logging.LogRecord) -> bool:
        # 防止递归：收集器本身的日志不处理
        if FailureLogFilter._collecting:
            return True
        
        # 只处理错误级别及以上的日志
        if record.levelno < logging.ERROR:
            return True
        
        # 跳过失败收集器和分析器自身的日志，避免递归
        if record.name and ("failure_collector" in record.name or "failure_analysis" in record.name):
            return True
        
        # 检查是否包含失败相关的关键词
        message = record.getMessage()
        failure_keywords = [
            "失败", "error", "exception", "失败案例",
            "幻觉", "虚构", "跳过", "不匹配", "invalid"
        ]
        
        is_failure = any(
            keyword.lower() in message.lower()
            for keyword in failure_keywords
        )
        
        if is_failure:
            FailureLogFilter._collecting = True
            try:
                # 获取 trace_id
                trace_id = getattr(record, "trace_id", "") or "unknown"
                
                # 推断严重程度
                if record.levelno >= logging.CRITICAL:
                    severity = FailureSeverity.CRITICAL
                elif record.levelno >= logging.ERROR:
                    severity = FailureSeverity.HIGH
                else:
                    severity = FailureSeverity.MEDIUM
                
                # 收集失败案例
                try:
                    self.collector.collect_failure(
                        trace_id=trace_id,
                        message=message,
                        source=record.name,
                        severity=severity,
                        context={
                            "logger": record.name,
                            "level": record.levelname,
                            "line": record.lineno,
                            "file": record.filename,
                        }
                    )
                except Exception as e:
                    # 收集失败不影响主流程
                    pass
            finally:
                FailureLogFilter._collecting = False
        
        return True


def setup_production_logging(config: Optional[Dict[str, Any]] = None):
    """配置生产环境日志系统
    
    Args:
        config: 日志配置字典
            - level: 日志级别
            - json_format: 是否使用JSON格式
            - log_file: 日志文件路径
            - max_bytes: 单个日志文件最大字节数
            - backup_count: 保留的备份文件数
            - enable_failure_collection: 是否启用失败案例收集
            - enable_console: 是否输出到控制台
    """
    config = config or {}
    
    level = config.get("level", "INFO")
    use_json = config.get("json_format", True)
    log_file = config.get("log_file", "logs/production.log")
    max_bytes = config.get("max_bytes", 10 * 1024 * 1024)  # 10MB
    backup_count = config.get("backup_count", 5)
    enable_failure_collection = config.get("enable_failure_collection", True)
    enable_console = config.get("enable_console", True)
    
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper()))
    
    # 清除已有处理器
    root_logger.handlers.clear()
    
    # 日志格式
    if use_json:
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )
    
    # 控制台输出
    if enable_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)
    
    # 文件输出（带轮转）
    if log_file:
        import os
        log_dir = os.path.dirname(log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)
        
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    
    # 失败案例收集过滤器
    if enable_failure_collection:
        failure_filter = FailureLogFilter()
        for handler in root_logger.handlers:
            handler.addFilter(failure_filter)
        
        # 初始化失败收集器
        collector = get_failure_collector()
        logging.info(json.dumps({
            "trace_id": "",
            "module_name": "logging_config",
            "action": "setup_production_logging",
            "failure_collection": "enabled",
            "duration_ms": 0,
            "level": "INFO"
        }))
    
    logging.info("生产环境日志系统配置完成")
    return root_logger


def setup_webhook_alert(webhook_url: str,
                       rule_name: str = "Webhook告警",
                       failure_type: Optional[FailureType] = None,
                       min_severity: FailureSeverity = FailureSeverity.HIGH,
                       threshold: int = 1,
                       time_window_seconds: int = 3600):
    """配置 Webhook 告警渠道
    
    Args:
        webhook_url: Webhook URL
        rule_name: 规则名称
        failure_type: 监控的失败类型（None表示所有）
        min_severity: 最低严重程度
        threshold: 阈值
        time_window_seconds: 时间窗口
    """
    import requests
    
    collector = get_failure_collector()
    
    def webhook_handler(alert):
        """Webhook 告警处理器"""
        payload = {
            "alert_id": alert.alert_id,
            "rule_id": alert.rule_id,
            "failure_type": alert.failure_type,
            "severity": alert.severity,
            "count": alert.count,
            "message": alert.message,
            "triggered_at": alert.triggered_at,
            "details": alert.details,
        }
        
        try:
            response = requests.post(
                webhook_url,
                json=payload,
                timeout=10
            )
            response.raise_for_status()
        except Exception as e:
            logging.error(f"Webhook告警发送失败: {e}")
    
    # 注册处理器
    collector.register_alert_handler(AlertChannel.WEBHOOK, webhook_handler)
    
    # 添加告警规则
    rule = AlertRule(
        rule_id=f"webhook_{int(time.time())}",
        name=rule_name,
        failure_type=failure_type,
        min_severity=min_severity,
        threshold=threshold,
        time_window_seconds=time_window_seconds,
        channel=AlertChannel.WEBHOOK,
        description=f"Webhook告警: {webhook_url}"
    )
    collector.add_alert_rule(rule)
    
    logging.info(f"Webhook告警配置完成: {webhook_url}")


import time

# 便捷函数
def configure_production_environment():
    """一键配置生产环境
    
    包含：
    - 结构化日志
    - 失败案例自动收集
    - 默认告警规则
    - 日志轮转
    """
    # 配置日志
    setup_production_logging({
        "level": "INFO",
        "json_format": True,
        "log_file": "logs/production.log",
        "max_bytes": 50 * 1024 * 1024,  # 50MB
        "backup_count": 10,
        "enable_failure_collection": True,
        "enable_console": True,
    })
    
    # 初始化失败收集器
    collector = get_failure_collector()
    
    logging.info("生产环境配置完成")
    logging.info(f"已加载 {len(collector.get_alert_rules())} 条告警规则")
    
    return collector