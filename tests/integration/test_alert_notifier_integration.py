"""AlertNotifier 集成测试

覆盖 agent.monitoring.alert_notifier 模块：
- 枚举与数据结构（NotificationChannel/NotificationResult/AlertNotification）
- NotificationSender 基类（_record_metric）
- EmailSender（format_message/send）
- DingTalkSender（_generate_sign/format_message/send）
- WebhookSender（format_message/send/重试）
- AlertNotifier（init/send/send_critical/send_recovery/get_history/get_stats）
- 全局单例与快捷函数
"""

import time
from unittest.mock import MagicMock, patch, call

import pytest

from agent.monitoring.alert_notifier import (
    NotificationChannel,
    NotificationResult,
    AlertNotification,
    NotificationSender,
    EmailSender,
    DingTalkSender,
    WebhookSender,
    AlertNotifier,
    get_alert_notifier,
    send_alert_notification,
)


# ============================================================================
# Fixtures
# ============================================================================

def make_notification(**kwargs):
    """构造测试用告警通知"""
    defaults = {
        "alert_name": "test-alert",
        "state": "firing",
        "severity": "critical",
        "message": "test message",
        "value": 0.95,
        "threshold": 0.8,
    }
    defaults.update(kwargs)
    return AlertNotification(**defaults)


@pytest.fixture
def email_config():
    return {
        "enabled": True,
        "smtp": {
            "host": "smtp.test.com",
            "port": 587,
            "username": "user@test.com",
            "password": "pass",
            "from_addr": "alert@test.com",
        },
        "recipients": ["admin@test.com", "ops@test.com"],
    }


@pytest.fixture
def dingtalk_config():
    return {
        "enabled": True,
        "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=test",
        "secret": "SECtest123",
        "use_markdown": True,
    }


@pytest.fixture
def webhook_config():
    return {
        "enabled": True,
        "url": "https://hook.test.com/alert",
        "method": "POST",
        "headers": {"Content-Type": "application/json"},
        "retry": {"max_attempts": 3, "backoff": "exponential"},
    }


@pytest.fixture
def reset_singleton():
    import agent.monitoring.alert_notifier as module
    old = module._alert_notifier
    module._alert_notifier = None
    yield
    module._alert_notifier = old


# ============================================================================
# 枚举测试
# ============================================================================

class TestNotificationChannel:
    def test_channel_values(self):
        assert NotificationChannel.EMAIL.value == "email"
        assert NotificationChannel.DINGTALK.value == "dingtalk"
        assert NotificationChannel.WEBHOOK.value == "webhook"
        assert NotificationChannel.WECHAT_WORK.value == "wechat_work"
        assert NotificationChannel.SLACK.value == "slack"
        assert NotificationChannel.SMS.value == "sms"

    def test_channel_count(self):
        assert len(NotificationChannel) == 6


# ============================================================================
# 数据结构测试
# ============================================================================

class TestNotificationResult:
    def test_defaults(self):
        result = NotificationResult(success=True, channel="email", message="ok")
        assert result.success is True
        assert result.response is None
        assert result.error is None
        assert result.duration_ms == 0

    def test_with_error(self):
        result = NotificationResult(
            success=False, channel="email", message="fail", error="timeout"
        )
        assert result.error == "timeout"


class TestAlertNotification:
    def test_defaults(self):
        n = AlertNotification(
            alert_name="a", state="firing", severity="critical",
            message="m", value=1.0, threshold=0.5,
        )
        assert n.duration_seconds == 0
        assert n.labels == {}
        assert n.annotations == {}
        assert n.timestamp > 0
        assert n.trace_id is None

    def test_custom_values(self):
        n = AlertNotification(
            alert_name="a", state="resolved", severity="warning",
            message="m", value=1.0, threshold=0.5,
            duration_seconds=30, labels={"k": "v"},
            annotations={"anno": "val"}, trace_id="trace-123",
        )
        assert n.state == "resolved"
        assert n.labels == {"k": "v"}
        assert n.trace_id == "trace-123"


# ============================================================================
# NotificationSender 基类测试
# ============================================================================

class TestNotificationSenderBase:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            NotificationSender({})

    def test_record_metric_handles_exception(self):
        class TestSender(NotificationSender):
            def send(self, notification):
                return NotificationResult(True, "test", "ok")

            def format_message(self, notification):
                return {}

        sender = TestSender({"enabled": True})
        with patch("agent.monitoring.prometheus.record_alert", create=True, side_effect=Exception("fail")):
            sender._record_metric(True, 10.0)

    def test_record_metric_success(self):
        class TestSender(NotificationSender):
            def send(self, notification):
                return NotificationResult(True, "test", "ok")

            def format_message(self, notification):
                return {}

        sender = TestSender({"enabled": True})
        with patch("agent.monitoring.prometheus.record_alert", create=True) as mock_record:
            sender._record_metric(True, 10.0)
            mock_record.assert_called_once()

    def test_record_metric_dedup(self):
        class TestSender(NotificationSender):
            def send(self, notification):
                return NotificationResult(True, "test", "ok")

            def format_message(self, notification):
                return {}

        sender = TestSender({"enabled": True})
        with patch("agent.monitoring.prometheus.record_alert", create=True) as mock_record:
            sender._record_metric(True, 10.0)
            sender._record_metric(True, 20.0)
            assert mock_record.call_count == 1


# ============================================================================
# EmailSender 测试
# ============================================================================

class TestEmailSenderInit:
    def test_init(self, email_config):
        sender = EmailSender(email_config)
        assert sender.smtp_host == "smtp.test.com"
        assert sender.smtp_port == 587
        assert sender.smtp_username == "user@test.com"
        assert sender.from_addr == "alert@test.com"
        assert len(sender.recipients) == 2

    def test_defaults(self):
        sender = EmailSender({})
        assert sender.smtp_host == "localhost"
        assert sender.smtp_port == 587
        assert sender.recipients == []

    def test_enabled_flag(self):
        sender = EmailSender({"enabled": False})
        assert sender.enabled is False


class TestEmailSenderFormatMessage:
    def test_returns_dict(self, email_config):
        sender = EmailSender(email_config)
        msg = sender.format_message(make_notification())
        assert "subject" in msg
        assert "html_body" in msg
        assert "text_body" in msg

    def test_subject_contains_severity(self, email_config):
        sender = EmailSender(email_config)
        msg = sender.format_message(make_notification(severity="critical"))
        assert "CRITICAL" in msg["subject"]

    def test_subject_contains_alert_name(self, email_config):
        sender = EmailSender(email_config)
        msg = sender.format_message(make_notification(alert_name="my-alert"))
        assert "my-alert" in msg["subject"]

    def test_subject_contains_state(self, email_config):
        sender = EmailSender(email_config)
        msg = sender.format_message(make_notification(state="resolved"))
        assert "恢复" in msg["subject"]

    def test_html_body_contains_content(self, email_config):
        sender = EmailSender(email_config)
        msg = sender.format_message(make_notification(message="custom detail"))
        assert "custom detail" in msg["html_body"]

    def test_text_body_contains_content(self, email_config):
        sender = EmailSender(email_config)
        msg = sender.format_message(make_notification(message="custom detail"))
        assert "custom detail" in msg["text_body"]

    def test_unknown_severity(self, email_config):
        sender = EmailSender(email_config)
        msg = sender.format_message(make_notification(severity="unknown"))
        assert "UNKNOWN" in msg["subject"]


class TestEmailSenderSend:
    @patch("agent.monitoring.alert_notifier.smtplib.SMTP")
    def test_send_success(self, mock_smtp, email_config):
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = mock_server
        sender = EmailSender(email_config)
        result = sender.send(make_notification())
        assert result.success is True
        assert result.channel == "email"
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once()
        mock_server.sendmail.assert_called_once()

    @patch("agent.monitoring.alert_notifier.smtplib.SMTP")
    def test_send_failure(self, mock_smtp, email_config):
        mock_smtp.side_effect = Exception("connection failed")
        sender = EmailSender(email_config)
        result = sender.send(make_notification())
        assert result.success is False
        assert "connection failed" in result.error

    def test_send_disabled(self, email_config):
        email_config["enabled"] = False
        sender = EmailSender(email_config)
        result = sender.send(make_notification())
        assert result.success is False
        assert "禁用" in result.message

    @patch("agent.monitoring.alert_notifier.smtplib.SMTP")
    def test_send_no_auth(self, mock_smtp):
        config = {
            "enabled": True,
            "smtp": {"host": "smtp.test.com", "port": 25},
            "recipients": ["a@test.com"],
        }
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = mock_server
        sender = EmailSender(config)
        result = sender.send(make_notification())
        assert result.success is True
        mock_server.login.assert_not_called()


# ============================================================================
# DingTalkSender 测试
# ============================================================================

class TestDingTalkInit:
    def test_init(self, dingtalk_config):
        sender = DingTalkSender(dingtalk_config)
        assert sender.webhook_url == "https://oapi.dingtalk.com/robot/send?access_token=test"
        assert sender.secret == "SECtest123"
        assert sender.use_markdown is True

    def test_defaults(self):
        sender = DingTalkSender({})
        assert sender.webhook_url == ""
        assert sender.secret == ""
        assert sender.use_markdown is True

    def test_disabled(self):
        sender = DingTalkSender({"enabled": False})
        assert sender.enabled is False


class TestDingTalkGenerateSign:
    def test_no_secret_returns_empty(self):
        sender = DingTalkSender({})
        assert sender._generate_sign() == ""

    def test_with_secret_returns_sign(self):
        sender = DingTalkSender({"secret": "testsecret"})
        sign = sender._generate_sign()
        assert "timestamp=" in sign
        assert "sign=" in sign

    def test_sign_format(self):
        sender = DingTalkSender({"secret": "testsecret"})
        sign = sender._generate_sign()
        assert sign.startswith("&timestamp=")
        assert "&sign=" in sign


class TestDingTalkFormatMessage:
    def test_markdown_format(self, dingtalk_config):
        sender = DingTalkSender(dingtalk_config)
        msg = sender.format_message(make_notification())
        assert msg["msgtype"] == "markdown"
        assert "markdown" in msg
        assert "title" in msg["markdown"]
        assert "text" in msg["markdown"]

    def test_text_format(self, dingtalk_config):
        dingtalk_config["use_markdown"] = False
        sender = DingTalkSender(dingtalk_config)
        msg = sender.format_message(make_notification())
        assert msg["msgtype"] == "text"
        assert "text" in msg
        assert "content" in msg["text"]

    def test_markdown_contains_alert_name(self, dingtalk_config):
        sender = DingTalkSender(dingtalk_config)
        msg = sender.format_message(make_notification(alert_name="my-alert"))
        assert "my-alert" in msg["markdown"]["text"]

    def test_markdown_contains_severity(self, dingtalk_config):
        sender = DingTalkSender(dingtalk_config)
        msg = sender.format_message(make_notification(severity="warning"))
        assert "WARNING" in msg["markdown"]["text"]


class TestDingTalkSend:
    @patch("agent.monitoring.alert_notifier.requests.post")
    def test_send_success(self, mock_post, dingtalk_config):
        mock_response = MagicMock()
        mock_response.json.return_value = {"errcode": 0}
        mock_post.return_value = mock_response
        sender = DingTalkSender(dingtalk_config)
        result = sender.send(make_notification())
        assert result.success is True
        assert result.channel == "dingtalk"

    @patch("agent.monitoring.alert_notifier.requests.post")
    def test_send_dingtalk_error(self, mock_post, dingtalk_config):
        mock_response = MagicMock()
        mock_response.json.return_value = {"errcode": 310000, "errmsg": "invalid token"}
        mock_post.return_value = mock_response
        sender = DingTalkSender(dingtalk_config)
        result = sender.send(make_notification())
        assert result.success is False
        assert "invalid token" in result.error

    @patch("agent.monitoring.alert_notifier.requests.post")
    def test_send_http_error(self, mock_post, dingtalk_config):
        mock_post.side_effect = Exception("network error")
        sender = DingTalkSender(dingtalk_config)
        result = sender.send(make_notification())
        assert result.success is False
        assert "network error" in result.error

    def test_send_disabled(self, dingtalk_config):
        dingtalk_config["enabled"] = False
        sender = DingTalkSender(dingtalk_config)
        result = sender.send(make_notification())
        assert result.success is False
        assert "禁用" in result.message

    def test_send_no_webhook_url(self):
        sender = DingTalkSender({"enabled": True})
        result = sender.send(make_notification())
        assert result.success is False
        assert "webhook_url" in result.message

    @patch("agent.monitoring.alert_notifier.requests.post")
    def test_send_with_sign(self, mock_post, dingtalk_config):
        mock_response = MagicMock()
        mock_response.json.return_value = {"errcode": 0}
        mock_post.return_value = mock_response
        sender = DingTalkSender(dingtalk_config)
        sender.send(make_notification())
        called_url = mock_post.call_args[0][0]
        assert "timestamp=" in called_url
        assert "sign=" in called_url

    @patch("agent.monitoring.alert_notifier.requests.post")
    def test_send_without_sign(self, mock_post):
        config = {
            "enabled": True,
            "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=test",
        }
        mock_response = MagicMock()
        mock_response.json.return_value = {"errcode": 0}
        mock_post.return_value = mock_response
        sender = DingTalkSender(config)
        sender.send(make_notification())
        called_url = mock_post.call_args[0][0]
        assert "timestamp=" not in called_url


# ============================================================================
# WebhookSender 测试
# ============================================================================

class TestWebhookInit:
    def test_init(self, webhook_config):
        sender = WebhookSender(webhook_config)
        assert sender.url == "https://hook.test.com/alert"
        assert sender.method == "POST"
        assert "Content-Type" in sender.headers

    def test_defaults(self):
        sender = WebhookSender({})
        assert sender.url == ""
        assert sender.method == "POST"
        assert sender.headers == {"Content-Type": "application/json"}

    def test_disabled(self):
        sender = WebhookSender({"enabled": False})
        assert sender.enabled is False


class TestWebhookFormatMessage:
    def test_returns_dict(self, webhook_config):
        sender = WebhookSender(webhook_config)
        msg = sender.format_message(make_notification())
        assert "alert_name" in msg
        assert "state" in msg
        assert "severity" in msg
        assert "value" in msg
        assert "threshold" in msg

    def test_includes_labels(self, webhook_config):
        sender = WebhookSender(webhook_config)
        msg = sender.format_message(make_notification(labels={"k": "v"}))
        assert msg["labels"] == {"k": "v"}

    def test_includes_trace_id(self, webhook_config):
        sender = WebhookSender(webhook_config)
        msg = sender.format_message(make_notification(trace_id="trace-123"))
        assert msg["trace_id"] == "trace-123"


class TestWebhookSend:
    @patch("agent.monitoring.alert_notifier.requests.request")
    def test_send_success(self, mock_request, webhook_config):
        mock_response = MagicMock()
        mock_response.text = "ok"
        mock_request.return_value = mock_response
        sender = WebhookSender(webhook_config)
        result = sender.send(make_notification())
        assert result.success is True
        assert result.channel == "webhook"

    @patch("agent.monitoring.alert_notifier.requests.request")
    def test_send_http_error(self, mock_request, webhook_config):
        mock_request.side_effect = Exception("connection refused")
        sender = WebhookSender(webhook_config)
        result = sender.send(make_notification())
        assert result.success is False
        assert "connection refused" in result.error

    def test_send_disabled(self, webhook_config):
        webhook_config["enabled"] = False
        sender = WebhookSender(webhook_config)
        result = sender.send(make_notification())
        assert result.success is False
        assert "禁用" in result.message

    def test_send_no_url(self):
        sender = WebhookSender({"enabled": True})
        result = sender.send(make_notification())
        assert result.success is False
        assert "url" in result.message

    @patch("agent.monitoring.alert_notifier.time.sleep")
    @patch("agent.monitoring.alert_notifier.requests.request")
    def test_retry_on_failure(self, mock_request, mock_sleep, webhook_config):
        mock_request.side_effect = Exception("transient")
        sender = WebhookSender(webhook_config)
        result = sender.send(make_notification())
        assert result.success is False
        assert mock_request.call_count == 3
        assert mock_sleep.call_count == 2

    @patch("agent.monitoring.alert_notifier.time.sleep")
    @patch("agent.monitoring.alert_notifier.requests.request")
    def test_retry_succeeds_on_second(self, mock_request, mock_sleep, webhook_config):
        mock_response = MagicMock()
        mock_response.text = "ok"
        mock_request.side_effect = [Exception("first fail"), mock_response]
        sender = WebhookSender(webhook_config)
        result = sender.send(make_notification())
        assert result.success is True
        assert mock_request.call_count == 2

    @patch("agent.monitoring.alert_notifier.time.sleep")
    @patch("agent.monitoring.alert_notifier.requests.request")
    def test_linear_backoff(self, mock_request, mock_sleep):
        config = {
            "enabled": True,
            "url": "https://hook.test.com",
            "retry": {"max_attempts": 3, "backoff": "linear"},
        }
        mock_request.side_effect = Exception("fail")
        sender = WebhookSender(config)
        sender.send(make_notification())
        delays = [c.args[0] for c in mock_sleep.call_args_list]
        assert delays == [1.0, 2.0]

    @patch("agent.monitoring.alert_notifier.time.sleep")
    @patch("agent.monitoring.alert_notifier.requests.request")
    def test_exponential_backoff(self, mock_request, mock_sleep, webhook_config):
        mock_request.side_effect = Exception("fail")
        sender = WebhookSender(webhook_config)
        sender.send(make_notification())
        delays = [c.args[0] for c in mock_sleep.call_args_list]
        assert delays == [1.0, 2.0]

    @patch("agent.monitoring.alert_notifier.time.sleep")
    @patch("agent.monitoring.alert_notifier.requests.request")
    def test_no_retry_on_success(self, mock_request, mock_sleep, webhook_config):
        mock_response = MagicMock()
        mock_response.text = "ok"
        mock_request.return_value = mock_response
        sender = WebhookSender(webhook_config)
        sender.send(make_notification())
        assert mock_request.call_count == 1
        assert mock_sleep.call_count == 0

    @patch("agent.monitoring.alert_notifier.requests.request")
    def test_response_in_result(self, mock_request, webhook_config):
        mock_response = MagicMock()
        mock_response.text = "response body"
        mock_request.return_value = mock_response
        sender = WebhookSender(webhook_config)
        result = sender.send(make_notification())
        assert result.response == "response body"


# ============================================================================
# AlertNotifier 测试
# ============================================================================

class TestAlertNotifierInit:
    def test_empty_config(self):
        notifier = AlertNotifier({})
        assert notifier._senders == {}

    def test_with_email_channel(self):
        config = {
            "channels": [
                {
                    "type": "email",
                    "name": "email-channel",
                    "smtp": {"host": "smtp.test.com"},
                    "recipients": ["a@test.com"],
                }
            ]
        }
        notifier = AlertNotifier(config)
        assert "email-channel" in notifier._senders
        assert isinstance(notifier._senders["email-channel"], EmailSender)

    def test_with_dingtalk_channel(self):
        config = {
            "channels": [
                {
                    "type": "dingtalk",
                    "name": "dingtalk-channel",
                    "webhook_url": "https://oapi.dingtalk.com/test",
                }
            ]
        }
        notifier = AlertNotifier(config)
        assert "dingtalk-channel" in notifier._senders

    def test_with_webhook_channel(self):
        config = {
            "channels": [
                {
                    "type": "webhook",
                    "name": "webhook-channel",
                    "url": "https://hook.test.com",
                }
            ]
        }
        notifier = AlertNotifier(config)
        assert "webhook-channel" in notifier._senders

    def test_unknown_channel_type(self):
        config = {
            "channels": [
                {"type": "unknown", "name": "unknown-channel"}
            ]
        }
        notifier = AlertNotifier(config)
        assert "unknown-channel" not in notifier._senders

    def test_multiple_channels(self):
        config = {
            "channels": [
                {"type": "email", "name": "email1", "recipients": []},
                {"type": "dingtalk", "name": "dingtalk1", "webhook_url": "url"},
                {"type": "webhook", "name": "webhook1", "url": "url"},
            ]
        }
        notifier = AlertNotifier(config)
        assert len(notifier._senders) == 3


class TestAlertNotifierSend:
    def test_send_to_specific_receiver(self):
        notifier = AlertNotifier({
            "channels": [
                {"type": "email", "name": "default", "recipients": []},
            ]
        })
        results = notifier.send(make_notification(), receivers=["default"])
        assert len(results) == 1

    def test_send_to_default_receiver(self):
        notifier = AlertNotifier({
            "default_receiver": "default",
            "channels": [
                {"type": "email", "name": "default", "recipients": []},
            ]
        })
        results = notifier.send(make_notification())
        assert len(results) == 1

    def test_send_to_nonexistent_receiver(self):
        notifier = AlertNotifier({})
        results = notifier.send(make_notification(), receivers=["nonexistent"])
        assert len(results) == 1
        assert results[0].success is False
        assert "未找到" in results[0].message

    def test_send_wildcard_match(self):
        notifier = AlertNotifier({
            "channels": [
                {"type": "email", "name": "default-notifications", "recipients": []},
            ]
        })
        results = notifier.send(make_notification(), receivers=["default"])
        assert len(results) == 1
        # 通配符匹配到 default-notifications 渠道，EmailSender.send 返回 channel type "email"
        assert results[0].channel == "email"

    def test_send_records_history(self):
        notifier = AlertNotifier({
            "channels": [
                {"type": "email", "name": "test", "recipients": []},
            ]
        })
        notifier.send(make_notification(), receivers=["test"])
        assert len(notifier._history) == 1

    def test_history_capped(self):
        notifier = AlertNotifier({
            "channels": [
                {"type": "email", "name": "test", "recipients": []},
            ]
        })
        notifier._max_history = 3
        for _ in range(5):
            notifier.send(make_notification(), receivers=["test"])
        assert len(notifier._history) == 3


class TestAlertNotifierSendCritical:
    def test_send_critical_to_critical_channels(self):
        notifier = AlertNotifier({
            "channels": [
                {"type": "email", "name": "critical-email", "recipients": []},
                {"type": "email", "name": "normal-email", "recipients": []},
            ]
        })
        results = notifier.send_critical(make_notification())
        assert len(results) == 1

    def test_send_critical_no_critical_channels(self):
        notifier = AlertNotifier({
            "channels": [
                {"type": "email", "name": "normal-email", "recipients": []},
            ]
        })
        results = notifier.send_critical(make_notification())
        assert len(results) == 0


class TestAlertNotifierSendRecovery:
    def test_send_recovery_sets_state(self):
        notifier = AlertNotifier({
            "channels": [
                {"type": "email", "name": "recovery-notifications", "recipients": []},
                {"type": "email", "name": "default-notifications", "recipients": []},
            ]
        })
        notification = make_notification(state="firing")
        results = notifier.send_recovery(notification)
        assert notification.state == "resolved"
        assert len(results) == 2


class TestAlertNotifierHistory:
    def test_get_history_empty(self):
        notifier = AlertNotifier({})
        assert notifier.get_history() == []

    def test_get_history_returns_dicts(self):
        notifier = AlertNotifier({
            "channels": [
                {"type": "email", "name": "test", "recipients": []},
            ]
        })
        notifier.send(make_notification(), receivers=["test"])
        history = notifier.get_history()
        assert len(history) == 1
        assert "success" in history[0]
        assert "channel" in history[0]

    def test_get_history_limit(self):
        notifier = AlertNotifier({
            "channels": [
                {"type": "email", "name": "test", "recipients": []},
            ]
        })
        for _ in range(5):
            notifier.send(make_notification(), receivers=["test"])
        history = notifier.get_history(limit=2)
        assert len(history) == 2


class TestAlertNotifierStats:
    def test_stats_empty(self):
        notifier = AlertNotifier({})
        stats = notifier.get_stats()
        assert stats["total"] == 0
        assert stats["success"] == 0
        assert stats["failed"] == 0
        assert stats["success_rate"] == 0

    def test_stats_with_history(self):
        notifier = AlertNotifier({
            "channels": [
                {"type": "email", "name": "test", "recipients": []},
            ]
        })
        with patch.object(EmailSender, "send", return_value=NotificationResult(True, "email", "ok")):
            notifier.send(make_notification(), receivers=["test"])
        stats = notifier.get_stats()
        assert stats["total"] == 1
        assert stats["success"] == 1

    def test_stats_mixed(self):
        notifier = AlertNotifier({
            "channels": [
                {"type": "email", "name": "test", "recipients": []},
            ]
        })
        # 一次成功一次失败，均被记录到 history
        success_result = NotificationResult(True, "email", "ok")
        fail_result = NotificationResult(False, "email", "fail", error="boom")
        with patch.object(EmailSender, "send", side_effect=[success_result, fail_result]):
            notifier.send(make_notification(), receivers=["test"])
            notifier.send(make_notification(), receivers=["test"])
        stats = notifier.get_stats()
        assert stats["total"] == 2
        assert stats["success"] == 1
        assert stats["failed"] == 1
        assert stats["success_rate"] == 0.5


# ============================================================================
# 全局单例测试
# ============================================================================

class TestGlobalSingleton:
    def test_get_alert_notifier(self, reset_singleton):
        notifier = get_alert_notifier({})
        assert isinstance(notifier, AlertNotifier)

    def test_singleton_returns_same(self, reset_singleton):
        n1 = get_alert_notifier({})
        n2 = get_alert_notifier()
        assert n1 is n2

    def test_reset(self, reset_singleton):
        n1 = get_alert_notifier({})
        import agent.monitoring.alert_notifier as module
        module._alert_notifier = None
        n2 = get_alert_notifier({})
        assert n1 is not n2


class TestSendAlertNotification:
    def test_send_alert_notification(self, reset_singleton):
        import agent.monitoring.alert_notifier as module
        module._alert_notifier = None

        results = send_alert_notification(
            alert_name="test",
            state="firing",
            severity="critical",
            message="msg",
            value=1.0,
            threshold=0.5,
        )
        assert isinstance(results, list)

    def test_send_alert_notification_with_labels(self, reset_singleton):
        import agent.monitoring.alert_notifier as module
        module._alert_notifier = None

        results = send_alert_notification(
            alert_name="test",
            state="firing",
            severity="warning",
            message="msg",
            value=1.0,
            threshold=0.5,
            labels={"env": "prod"},
            annotations={"runbook": "url"},
            trace_id="trace-123",
        )
        assert isinstance(results, list)
