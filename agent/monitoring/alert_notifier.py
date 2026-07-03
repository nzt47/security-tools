#!/usr/bin/env python3
"""
告警通知模块

支持多种通知渠道：
1. 邮件通知 (SMTP)
2. 钉钉群通知 (DingTalk Webhook)
3. Webhook 通知
4. 企业微信通知

告警通知流程：
1. 接收告警事件（firing/resolved）
2. 按路由规则选择通知渠道
3. 格式化通知消息
4. 发送通知（带重试机制）
"""

import logging
import time
import smtplib
import json
import uuid
import hashlib
import hmac
import base64
import urllib.parse
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from abc import ABC, abstractmethod
import threading
import requests

from agent.monitoring.tracing import get_trace_id

try:
    from agent.error_handler import get_error_handler, RetryPolicy
    _ERROR_HANDLER_AVAILABLE = True
except ImportError:
    _ERROR_HANDLER_AVAILABLE = False
    logging.warning("[Alert] error_handler 模块不可用")

logger = logging.getLogger(__name__)


class NotificationChannel(Enum):
    """通知渠道类型"""
    EMAIL = "email"
    DINGTALK = "dingtalk"
    WEBHOOK = "webhook"
    WECHAT_WORK = "wechat_work"
    SLACK = "slack"
    SMS = "sms"


@dataclass
class NotificationResult:
    """通知发送结果"""
    success: bool
    channel: str
    message: str
    response: Optional[str] = None
    error: Optional[str] = None
    duration_ms: float = 0


@dataclass
class AlertNotification:
    """告警通知"""
    alert_name: str
    state: str                          # firing, resolved, acknowledged
    severity: str                      # critical, warning, info
    message: str
    value: float
    threshold: float
    duration_seconds: float = 0
    labels: Dict[str, str] = field(default_factory=dict)
    annotations: Dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    trace_id: Optional[str] = None


class NotificationSender(ABC):
    """通知发送器基类"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.enabled = config.get("enabled", True)
        self._lock = threading.Lock()
        # 配置化超时（支持热加载，每次初始化时读取最新值）
        try:
            from agent.monitoring.observability_config import get_alert_timeout
            self._timeout = get_alert_timeout()
        except Exception:
            self._timeout = 30

    @abstractmethod
    def send(self, notification: AlertNotification) -> NotificationResult:
        """发送通知"""
        pass

    @abstractmethod
    def format_message(self, notification: AlertNotification) -> Any:
        """格式化消息"""
        pass

    def _record_metric(self, success: bool, duration_ms: float):
        """记录指标（带防重复注册）"""
        if not hasattr(self, "_metrics_recorded"):
            self._metrics_recorded = False

        if not self._metrics_recorded:
            try:
                from agent.monitoring.prometheus import record_alert
                record_alert(self.__class__.__name__.lower())
                self._metrics_recorded = True
            except Exception:
                pass


class EmailSender(NotificationSender):
    """邮件发送器"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.smtp_host = config.get("smtp", {}).get("host", "localhost")
        self.smtp_port = config.get("smtp", {}).get("port", 587)
        self.smtp_username = config.get("smtp", {}).get("username", "")
        self.smtp_password = config.get("smtp", {}).get("password", "")
        self.from_addr = config.get("smtp", {}).get("from_addr", self.smtp_username)
        self.recipients = config.get("recipients", [])

    def format_message(self, notification: AlertNotification) -> Dict[str, Any]:
        """格式化邮件消息"""
        severity_emoji = {
            "critical": "🔴",
            "warning": "🟡",
            "info": "🔵"
        }
        state_text = {
            "firing": "触发",
            "resolved": "恢复",
            "acknowledged": "已确认"
        }

        emoji = severity_emoji.get(notification.severity, "⚪")
        state = state_text.get(notification.state, notification.state)

        subject = f"[{emoji} {notification.severity.upper()}] {state}: {notification.alert_name}"

        html_body = f"""
        <html>
        <body>
        <h2 style="color: {'red' if notification.severity == 'critical' else 'orange'};">
            {emoji} 告警通知 - {state}
        </h2>
        <table style="border-collapse: collapse; width: 100%; max-width: 600px;">
            <tr style="border-bottom: 1px solid #ddd;">
                <td style="padding: 8px; font-weight: bold;">告警名称</td>
                <td style="padding: 8px;">{notification.alert_name}</td>
            </tr>
            <tr style="border-bottom: 1px solid #ddd;">
                <td style="padding: 8px; font-weight: bold;">严重级别</td>
                <td style="padding: 8px;">{notification.severity.upper()}</td>
            </tr>
            <tr style="border-bottom: 1px solid #ddd;">
                <td style="padding: 8px; font-weight: bold;">当前值</td>
                <td style="padding: 8px;">{notification.value:.4f}</td>
            </tr>
            <tr style="border-bottom: 1px solid #ddd;">
                <td style="padding: 8px; font-weight: bold;">阈值</td>
                <td style="padding: 8px;">{notification.threshold:.4f}</td>
            </tr>
            <tr style="border-bottom: 1px solid #ddd;">
                <td style="padding: 8px; font-weight: bold;">持续时间</td>
                <td style="padding: 8px;">{notification.duration_seconds:.1f} 秒</td>
            </tr>
            <tr style="border-bottom: 1px solid #ddd;">
                <td style="padding: 8px; font-weight: bold;">发生时间</td>
                <td style="padding: 8px;">{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(notification.timestamp))}</td>
            </tr>
            <tr>
                <td colspan="2" style="padding: 8px;">
                    <strong>详情:</strong><br/>
                    {notification.message}
                </td>
            </tr>
        </table>
        </body>
        </html>
        """

        text_body = f"""
        告警通知 - {state}

        告警名称: {notification.alert_name}
        严重级别: {notification.severity.upper()}
        当前值: {notification.value:.4f}
        阈值: {notification.threshold:.4f}
        持续时间: {notification.duration_seconds:.1f} 秒
        发生时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(notification.timestamp))}

        详情:
        {notification.message}
        """

        return {"subject": subject, "html_body": html_body, "text_body": text_body}

    def send(self, notification: AlertNotification) -> NotificationResult:
        """发送邮件"""
        if not self.enabled:
            return NotificationResult(False, "email", "通知渠道已禁用")

        start_time = time.time()
        try:
            message = self.format_message(notification)

            # 构建邮件
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText

            msg = MIMEMultipart("alternative")
            msg["Subject"] = message["subject"]
            msg["From"] = self.from_addr
            msg["To"] = ", ".join(self.recipients)

            msg.attach(MIMEText(message["text_body"], "plain"))
            msg.attach(MIMEText(message["html_body"], "html"))

            # 发送邮件
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=self._timeout) as server:
                server.ehlo()
                server.starttls()
                if self.smtp_username and self.smtp_password:
                    server.login(self.smtp_username, self.smtp_password)
                server.sendmail(self.from_addr, self.recipients, msg.as_string())

            duration_ms = (time.time() - start_time) * 1000
            logger.info(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "alert_notifier",
                "action": "email_sent",
                "duration_ms": duration_ms,
                "alert_name": notification.alert_name,
                "recipients": len(self.recipients)
            }, ensure_ascii=False))
            self._record_metric(True, duration_ms)
            return NotificationResult(True, "email", "发送成功", duration_ms=duration_ms)

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "alert_notifier",
                "action": "email_failed",
                "duration_ms": duration_ms,
                "alert_name": notification.alert_name,
                "error": str(e)
            }, ensure_ascii=False))
            self._record_metric(False, duration_ms)
            return NotificationResult(False, "email", str(e), error=str(e), duration_ms=duration_ms)


class DingTalkSender(NotificationSender):
    """钉钉群通知发送器"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.webhook_url = config.get("webhook_url", "")
        self.secret = config.get("secret", "")
        self.use_markdown = config.get("use_markdown", True)

    def _generate_sign(self) -> str:
        """生成签名"""
        if not self.secret:
            return ""

        timestamp = str(round(time.time() * 1000))
        secret_enc = self.secret.encode("utf-8")
        string_to_sign = f"{timestamp}\n{self.secret}"
        string_to_sign_enc = string_to_sign.encode("utf-8")
        hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        return f"&timestamp={timestamp}&sign={sign}"

    def format_message(self, notification: AlertNotification) -> Dict[str, Any]:
        """格式化钉钉消息"""
        severity_colors = {
            "critical": "red",
            "warning": "orange",
            "info": "blue"
        }
        color = severity_colors.get(notification.severity, "gray")

        if self.use_markdown:
            # Markdown 格式
            content = f"""### 🔔 告警通知 - {notification.state.upper()}

**告警名称**: {notification.alert_name}

**严重级别**: {notification.severity.upper()}

**当前值**: {notification.value:.4f}

**阈值**: {notification.threshold:.4f}

**持续时间**: {notification.duration_seconds:.1f} 秒

**详情**: {notification.message}

**时间**: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(notification.timestamp))}
"""
            return {
                "msgtype": "markdown",
                "markdown": {
                    "title": f"告警通知 - {notification.alert_name}",
                    "text": content
                }
            }
        else:
            # 文本格式
            return {
                "msgtype": "text",
                "text": {
                    "content": f"""【{notification.severity.upper()}】{notification.alert_name}
当前值: {notification.value:.4f}
阈值: {notification.threshold:.4f}
状态: {notification.state}
详情: {notification.message}
时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(notification.timestamp))}"""
                }
            }

    def send(self, notification: AlertNotification) -> NotificationResult:
        """发送钉钉通知"""
        if not self.enabled:
            return NotificationResult(False, "dingtalk", "通知渠道已禁用")

        if not self.webhook_url:
            return NotificationResult(False, "dingtalk", "webhook_url 未配置")

        start_time = time.time()
        try:
            # 生成签名
            sign = self._generate_sign()
            url = f"{self.webhook_url}{sign}" if sign else self.webhook_url

            # 格式化消息
            message = self.format_message(notification)

            # 发送请求
            response = requests.post(
                url,
                json=message,
                headers={"Content-Type": "application/json"},
                timeout=self._timeout
            )
            response.raise_for_status()

            result = response.json()
            if result.get("errcode") == 0:
                duration_ms = (time.time() - start_time) * 1000
                logger.info(json.dumps({
                    "trace_id": get_trace_id(),
                    "module_name": "alert_notifier",
                    "action": "dingtalk_sent",
                    "duration_ms": duration_ms,
                    "alert_name": notification.alert_name
                }, ensure_ascii=False))
                self._record_metric(True, duration_ms)
                return NotificationResult(True, "dingtalk", "发送成功", duration_ms=duration_ms)
            else:
                raise Exception(f"钉钉返回错误: {result.get('errmsg')}")

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "alert_notifier",
                "action": "dingtalk_failed",
                "duration_ms": duration_ms,
                "alert_name": notification.alert_name,
                "error": str(e)
            }, ensure_ascii=False))
            self._record_metric(False, duration_ms)
            return NotificationResult(False, "dingtalk", str(e), error=str(e), duration_ms=duration_ms)


class WebhookSender(NotificationSender):
    """Webhook 通知发送器"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.url = config.get("url", "")
        self.method = config.get("method", "POST").upper()
        self.headers = config.get("headers", {"Content-Type": "application/json"})
        self.retry_config = config.get("retry", {})

    def format_message(self, notification: AlertNotification) -> Dict[str, Any]:
        """格式化 Webhook 消息"""
        return {
            "alert_name": notification.alert_name,
            "state": notification.state,
            "severity": notification.severity,
            "message": notification.message,
            "value": notification.value,
            "threshold": notification.threshold,
            "duration_seconds": notification.duration_seconds,
            "labels": notification.labels,
            "annotations": notification.annotations,
            "timestamp": notification.timestamp,
            "trace_id": notification.trace_id
        }

    def send(self, notification: AlertNotification) -> NotificationResult:
        """发送 Webhook 通知"""
        if not self.enabled:
            return NotificationResult(False, "webhook", "通知渠道已禁用")

        if not self.url:
            return NotificationResult(False, "webhook", "url 未配置")

        start_time = time.time()
        message = self.format_message(notification)

        # 重试配置
        max_attempts = self.retry_config.get("max_attempts", 3)
        backoff_type = self.retry_config.get("backoff", "exponential")
        base_delay = 1.0

        last_error = None
        for attempt in range(max_attempts):
            try:
                response = requests.request(
                    method=self.method,
                    url=self.url,
                    json=message,
                    headers=self.headers,
                    timeout=self._timeout
                )
                response.raise_for_status()

                duration_ms = (time.time() - start_time) * 1000
                logger.info(json.dumps({
                    "trace_id": get_trace_id(),
                    "module_name": "alert_notifier",
                    "action": "webhook_sent",
                    "duration_ms": duration_ms,
                    "alert_name": notification.alert_name,
                    "attempt": attempt + 1
                }, ensure_ascii=False))
                self._record_metric(True, duration_ms)
                return NotificationResult(
                    True, "webhook", "发送成功",
                    response=response.text[:200],
                    duration_ms=duration_ms
                )

            except Exception as e:
                last_error = e
                logger.warning(json.dumps({
                    "trace_id": get_trace_id(),
                    "module_name": "alert_notifier",
                    "action": "webhook_retry",
                    "duration_ms": 0,
                    "alert_name": notification.alert_name,
                    "attempt": attempt + 1,
                    "max_attempts": max_attempts,
                    "error": str(e)
                }, ensure_ascii=False))

                # 指数退避等待
                if attempt < max_attempts - 1:
                    delay = base_delay * (2 ** attempt)
                    if backoff_type == "linear":
                        delay = base_delay * (attempt + 1)
                    time.sleep(delay)

        duration_ms = (time.time() - start_time) * 1000
        logger.error(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "alert_notifier",
            "action": "webhook_failed",
            "duration_ms": duration_ms,
            "alert_name": notification.alert_name,
            "attempts": max_attempts,
            "error": str(last_error)
        }, ensure_ascii=False))
        self._record_metric(False, duration_ms)
        return NotificationResult(
            False, "webhook", str(last_error),
            error=str(last_error),
            duration_ms=duration_ms
        )


class AlertNotifier:
    """告警通知管理器

    管理多种通知渠道，按路由规则发送通知。
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Args:
            config: 通知配置
        """
        self.config = config or {}
        self._senders: Dict[str, NotificationSender] = {}
        self._lock = threading.Lock()
        self._history: List[NotificationResult] = []
        self._max_history = 100

        # 初始化发送器
        self._init_senders()

        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "alert_notifier",
            "action": "init",
            "duration_ms": 0,
            "channels": list(self._senders.keys())
        }, ensure_ascii=False))

    def _init_senders(self):
        """初始化通知发送器"""
        channels = self.config.get("channels", [])

        for channel_config in channels:
            channel_type = channel_config.get("type", "")
            channel_name = channel_config.get("name", "")

            try:
                if channel_type == "email":
                    sender = EmailSender(channel_config)
                    self._senders[channel_name] = sender
                elif channel_type == "dingtalk":
                    sender = DingTalkSender(channel_config)
                    self._senders[channel_name] = sender
                elif channel_type == "webhook":
                    sender = WebhookSender(channel_config)
                    self._senders[channel_name] = sender
                else:
                    logger.warning(f"[Alert] 未知通知类型: {channel_type}")
            except Exception as e:
                logger.error(f"[Alert] 初始化通知渠道 {channel_name} 失败: {e}")

    def send(
        self,
        notification: AlertNotification,
        receivers: Optional[List[str]] = None
    ) -> List[NotificationResult]:
        """发送告警通知

        Args:
            notification: 告警通知
            receivers: 指定接收者列表，None 表示使用默认路由

        Returns:
            发送结果列表
        """
        results = []

        # 确定使用的接收者
        targets = receivers or [self.config.get("default_receiver", "default-notifications")]

        with self._lock:
            for receiver_name in targets:
                sender = self._senders.get(receiver_name)
                if not sender:
                    # 尝试使用通配符匹配
                    for name, s in self._senders.items():
                        if receiver_name in name or name in receiver_name:
                            sender = s
                            break

                if not sender:
                    logger.warning(f"[Alert] 未找到通知渠道: {receiver_name}")
                    results.append(NotificationResult(False, receiver_name, "渠道未找到"))
                    continue

                # 发送通知
                result = sender.send(notification)
                results.append(result)

                # 记录历史
                self._history.append(result)
                if len(self._history) > self._max_history:
                    self._history.pop(0)

        return results

    def send_critical(self, notification: AlertNotification) -> List[NotificationResult]:
        """发送关键告警通知（同时发送到所有 critical 渠道）"""
        results = []

        with self._lock:
            for name, sender in self._senders.items():
                if "critical" in name.lower():
                    result = sender.send(notification)
                    results.append(result)

        return results

    def send_recovery(self, notification: AlertNotification) -> List[NotificationResult]:
        """发送恢复通知"""
        notification.state = "resolved"

        # 发送到恢复通知渠道
        recovery_receivers = ["recovery-notifications", "default-notifications"]
        return self.send(notification, recovery_receivers)

    def get_history(self, limit: int = 50) -> List[Dict]:
        """获取通知历史

        Args:
            limit: 返回条数

        Returns:
            通知历史列表
        """
        with self._lock:
            history = list(self._history[-limit:])
            return [
                {
                    "success": r.success,
                    "channel": r.channel,
                    "message": r.message,
                    "error": r.error,
                    "duration_ms": r.duration_ms
                }
                for r in history
            ]

    def get_stats(self) -> Dict:
        """获取通知统计"""
        with self._lock:
            total = len(self._history)
            success = sum(1 for r in self._history if r.success)
            return {
                "total": total,
                "success": success,
                "failed": total - success,
                "success_rate": success / total if total > 0 else 0
            }


# 全局单例
_alert_notifier: Optional[AlertNotifier] = None


def get_alert_notifier(config: Optional[Dict[str, Any]] = None) -> AlertNotifier:
    """获取全局告警通知器

    Args:
        config: 通知配置，None 使用默认配置

    Returns:
        AlertNotifier 实例
    """
    global _alert_notifier
    if _alert_notifier is None:
        _alert_notifier = AlertNotifier(config)
    return _alert_notifier


def send_alert_notification(
    alert_name: str,
    state: str,
    severity: str,
    message: str,
    value: float,
    threshold: float,
    duration_seconds: float = 0,
    labels: Optional[Dict[str, str]] = None,
    annotations: Optional[Dict[str, str]] = None,
    trace_id: Optional[str] = None
) -> List[NotificationResult]:
    """快捷函数：发送告警通知

    Args:
        alert_name: 告警名称
        state: 状态 (firing/resolved/acknowledged)
        severity: 严重级别 (critical/warning/info)
        message: 告警消息
        value: 当前值
        threshold: 阈值
        duration_seconds: 持续时间
        labels: 标签
        annotations: 注解
        trace_id: 追踪ID

    Returns:
        发送结果列表
    """
    notification = AlertNotification(
        alert_name=alert_name,
        state=state,
        severity=severity,
        message=message,
        value=value,
        threshold=threshold,
        duration_seconds=duration_seconds,
        labels=labels or {},
        annotations=annotations or {},
        trace_id=trace_id
    )

    notifier = get_alert_notifier()
    return notifier.send(notification)
