"""Error Reporter 单元测试"""
import pytest
import json
from unittest.mock import patch, MagicMock, mock_open
from pathlib import Path

from agent.monitoring.error_reporter import (
    AlertLevel,
    ReporterType,
    ErrorReport,
    BaseReporter,
    ConsoleReporter,
    WebhookReporter,
    SlackReporter,
    EmailReporter,
    FileReporter,
    ErrorReporter,
    get_error_reporter,
    report_error,
)


class TestAlertLevel:
    """测试告警级别枚举"""

    def test_alert_level_values(self):
        """测试告警级别值"""
        assert AlertLevel.DEBUG.value == "debug"
        assert AlertLevel.INFO.value == "info"
        assert AlertLevel.WARNING.value == "warning"
        assert AlertLevel.ERROR.value == "error"
        assert AlertLevel.CRITICAL.value == "critical"


class TestErrorReport:
    """测试错误报告数据类"""

    def test_error_report_creation(self):
        """测试错误报告创建"""
        report = ErrorReport(
            error_type="ValueError",
            error_message="test error",
            traceback="traceback here",
            timestamp="2024-01-01T00:00:00",
            level="error",
            context={"user_id": "123"},
            trace_id="trace123",
            service="test-service",
            user_id="user1"
        )
        
        assert report.error_type == "ValueError"
        assert report.error_message == "test error"
        assert report.trace_id == "trace123"
        assert report.user_id == "user1"

    def test_error_report_to_dict(self):
        """测试转换为字典"""
        report = ErrorReport(
            error_type="TypeError",
            error_message="test",
            traceback="tb",
            timestamp="2024-01-01T00:00:00",
            level="error"
        )
        
        d = report.to_dict()
        assert d["error_type"] == "TypeError"
        assert d["error_message"] == "test"
        assert d["level"] == "error"

    def test_error_report_to_json(self):
        """测试转换为JSON"""
        report = ErrorReport(
            error_type="Error",
            error_message="msg",
            traceback="tb",
            timestamp="2024-01-01T00:00:00",
            level="info"
        )
        
        json_str = report.to_json()
        data = json.loads(json_str)
        assert data["error_type"] == "Error"


class TestBaseReporter:
    """测试上报器基类"""

    def test_base_reporter_init(self):
        """测试基类初始化"""
        reporter = BaseReporter({"enabled": True, "min_level": "warning"})
        assert reporter.enabled is True
        assert reporter.min_level == AlertLevel.WARNING

    def test_should_report(self):
        """测试是否应该上报"""
        reporter = BaseReporter({"enabled": True, "min_level": "warning"})
        
        assert reporter.should_report(AlertLevel.DEBUG) is False
        assert reporter.should_report(AlertLevel.INFO) is False
        assert reporter.should_report(AlertLevel.WARNING) is True
        assert reporter.should_report(AlertLevel.ERROR) is True
        assert reporter.should_report(AlertLevel.CRITICAL) is True

    def test_should_report_disabled(self):
        """测试禁用状态"""
        reporter = BaseReporter({"enabled": False, "min_level": "error"})
        assert reporter.should_report(AlertLevel.CRITICAL) is False


class TestConsoleReporter:
    """测试控制台上报器"""

    def test_console_reporter_send(self, capsys):
        """测试控制台上报"""
        reporter = ConsoleReporter({"enabled": True, "min_level": "debug"})
        report = ErrorReport(
            error_type="TestError",
            error_message="test message",
            traceback="traceback",
            timestamp="2024-01-01T00:00:00",
            level="error"
        )
        
        result = reporter.send(report)
        assert result is True
        
        captured = capsys.readouterr()
        assert "Error Report" in captured.out
        assert "TestError" in captured.out

    def test_console_reporter_min_level(self):
        """测试最小级别过滤"""
        reporter = ConsoleReporter({"enabled": True, "min_level": "error"})
        report = ErrorReport(
            error_type="Test",
            error_message="msg",
            traceback="",
            timestamp="2024-01-01T00:00:00",
            level="warning"
        )
        
        result = reporter.send(report)
        assert result is False


class TestWebhookReporter:
    """测试 Webhook 上报器"""

    def test_webhook_reporter_init(self):
        """测试初始化"""
        reporter = WebhookReporter({
            "enabled": True,
            "url": "https://example.com/webhook",
            "timeout": 5,
            "retry_times": 3
        })
        assert reporter.url == "https://example.com/webhook"
        assert reporter.timeout == 5

    @patch("urllib.request.urlopen")
    def test_webhook_reporter_send_success(self, mock_urlopen):
        """测试成功发送"""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_urlopen.return_value.__enter__.return_value = mock_response
        
        reporter = WebhookReporter({
            "enabled": True,
            "url": "https://example.com/webhook"
        })
        report = ErrorReport(
            error_type="TestError",
            error_message="test",
            traceback="tb",
            timestamp="2024-01-01T00:00:00",
            level="error"
        )
        
        result = reporter.send(report)
        assert result is True

    @patch("urllib.request.urlopen")
    def test_webhook_reporter_send_failure(self, mock_urlopen):
        """测试发送失败"""
        mock_urlopen.side_effect = Exception("Connection failed")
        
        reporter = WebhookReporter({
            "enabled": True,
            "url": "https://example.com/webhook"
        })
        report = ErrorReport(
            error_type="TestError",
            error_message="test",
            traceback="tb",
            timestamp="2024-01-01T00:00:00",
            level="error"
        )
        
        result = reporter.send(report)
        assert result is False

    def test_webhook_reporter_no_url(self):
        """测试未配置 URL"""
        reporter = WebhookReporter({"enabled": True})
        report = ErrorReport(
            error_type="Test",
            error_message="msg",
            traceback="",
            timestamp="2024-01-01T00:00:00",
            level="error"
        )
        
        result = reporter.send(report)
        assert result is False


class TestSlackReporter:
    """测试 Slack 上报器"""

    def test_slack_reporter_init(self):
        """测试初始化"""
        reporter = SlackReporter({
            "enabled": True,
            "webhook_url": "https://hooks.slack.com/test",
            "channel": "#errors",
            "username": "Test Bot"
        })
        assert reporter.webhook_url == "https://hooks.slack.com/test"
        assert reporter.channel == "#errors"

    @patch("urllib.request.urlopen")
    def test_slack_reporter_send_success(self, mock_urlopen):
        """测试成功发送"""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_urlopen.return_value.__enter__.return_value = mock_response
        
        reporter = SlackReporter({
            "enabled": True,
            "webhook_url": "https://hooks.slack.com/test"
        })
        report = ErrorReport(
            error_type="TestError",
            error_message="test",
            traceback="tb",
            timestamp="2024-01-01T00:00:00",
            level="error"
        )
        
        result = reporter.send(report)
        assert result is True

    def test_slack_reporter_no_url(self):
        """测试未配置 URL"""
        reporter = SlackReporter({"enabled": True})
        report = ErrorReport(
            error_type="Test",
            error_message="msg",
            traceback="",
            timestamp="2024-01-01T00:00:00",
            level="error"
        )
        
        result = reporter.send(report)
        assert result is False


class TestEmailReporter:
    """测试邮件上报器"""

    def test_email_reporter_init(self):
        """测试初始化"""
        reporter = EmailReporter({
            "enabled": True,
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "smtp_user": "user",
            "smtp_password": "pass",
            "to_addrs": ["admin@example.com"]
        })
        assert reporter.smtp_host == "smtp.example.com"
        assert reporter.to_addrs == ["admin@example.com"]

    @patch("smtplib.SMTP")
    def test_email_reporter_send_success(self, mock_smtp):
        """测试成功发送"""
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = mock_server
        
        reporter = EmailReporter({
            "enabled": True,
            "smtp_host": "smtp.example.com",
            "to_addrs": ["admin@example.com"]
        })
        report = ErrorReport(
            error_type="TestError",
            error_message="test",
            traceback="tb",
            timestamp="2024-01-01T00:00:00",
            level="error"
        )
        
        result = reporter.send(report)
        assert result is True

    def test_email_reporter_no_recipients(self):
        """测试未配置收件人"""
        reporter = EmailReporter({"enabled": True})
        report = ErrorReport(
            error_type="Test",
            error_message="msg",
            traceback="",
            timestamp="2024-01-01T00:00:00",
            level="error"
        )
        
        result = reporter.send(report)
        assert result is False


class TestFileReporter:
    """测试文件上报器"""

    def test_file_reporter_init(self, tmp_path):
        """测试初始化"""
        reporter = FileReporter({
            "enabled": True,
            "file_path": str(tmp_path / "errors.log"),
            "max_size_mb": 10,
            "backup_count": 5
        })
        assert reporter.file_path == tmp_path / "errors.log"

    def test_file_reporter_send(self, tmp_path):
        """测试写入文件"""
        log_file = tmp_path / "errors.log"
        reporter = FileReporter({
            "enabled": True,
            "file_path": str(log_file)
        })
        report = ErrorReport(
            error_type="TestError",
            error_message="test message",
            traceback="traceback",
            timestamp="2024-01-01T00:00:00",
            level="error"
        )
        
        result = reporter.send(report)
        assert result is True
        assert log_file.exists()
        
        content = log_file.read_text()
        assert "TestError" in content
        assert "test message" in content

    def test_file_reporter_rotation(self, tmp_path):
        """测试日志轮转"""
        log_file = tmp_path / "errors.log"
        reporter = FileReporter({
            "enabled": True,
            "file_path": str(log_file),
            "max_size_mb": 0.001,  # 1KB
            "backup_count": 2
        })
        
        # 写入大量数据触发轮转
        for i in range(100):
            report = ErrorReport(
                error_type=f"Error{i}",
                error_message="x" * 100,
                traceback="",
                timestamp="2024-01-01T00:00:00",
                level="error"
            )
            reporter.send(report)
        
        # 检查备份文件
        backup1 = tmp_path / "errors.1.log"
        backup2 = tmp_path / "errors.2.log"
        assert backup1.exists() or backup2.exists()


class TestErrorReporter:
    """测试错误上报管理器"""

    def test_error_reporter_init_default(self):
        """测试默认初始化"""
        reporter = ErrorReporter()
        assert len(reporter.reporters) > 0

    def test_report_error_sync(self):
        """测试同步上报错误"""
        reporter = ErrorReporter({
            "console": {"enabled": True},
            "webhook": {"enabled": False},
            "slack": {"enabled": False},
            "email": {"enabled": False},
            "file": {"enabled": False}
        })
        
        result = reporter.report_error(ValueError("test error"))
        assert result is True

    def test_report_error_async(self):
        """测试异步上报错误"""
        reporter = ErrorReporter({
            "console": {"enabled": True},
            "webhook": {"enabled": False},
            "slack": {"enabled": False},
            "email": {"enabled": False},
            "file": {"enabled": False}
        })
        
        result = reporter.report_error(ValueError("test error"), async_report=True)
        assert result is True
        
        # 停止工作线程
        reporter.stop()

    def test_report_message(self):
        """测试上报消息"""
        reporter = ErrorReporter({
            "console": {"enabled": True, "min_level": "info"},
            "webhook": {"enabled": False},
            "slack": {"enabled": False},
            "email": {"enabled": False},
            "file": {"enabled": False}
        })
        
        result = reporter.report_message("test message", level=AlertLevel.INFO)
        assert result is True

    def test_get_stats(self):
        """测试获取统计信息"""
        reporter = ErrorReporter({
            "console": {"enabled": True}
        })
        stats = reporter.get_stats()
        
        assert "queue_size" in stats
        assert "reporters" in stats
        assert "reporter_types" in stats


class TestErrorReporterSingleton:
    """测试单例模式"""

    def test_singleton(self):
        """测试单例获取"""
        reporter1 = get_error_reporter()
        reporter2 = get_error_reporter()
        
        assert reporter1 is reporter2


class TestReportErrorShortcut:
    """测试快捷函数"""

    def test_report_error_shortcut(self):
        """测试上报错误快捷函数"""
        result = report_error(ValueError("shortcut test"))
        assert isinstance(result, bool)