import pytest
import os
from agent.error_reporting_config import get_config


class TestErrorReportingConfig:
    """错误上报配置测试"""

    def test_get_config_default(self):
        """测试获取默认配置"""
        config = get_config()
        
        assert config is not None
        assert isinstance(config, dict)
        
        # 检查控制台上报配置
        assert "console" in config
        assert config["console"]["enabled"] is True
        assert config["console"]["min_level"] == "warning"
        
        # 检查文件上报配置
        assert "file" in config
        assert config["file"]["enabled"] is True
        assert config["file"]["file_path"] == "./logs/digital_life_errors.log"
        assert config["file"]["min_level"] == "error"
        
        # 检查 webhook 上报配置
        assert "webhook" in config
        assert config["webhook"]["enabled"] is False
        assert config["webhook"]["url"] == ""
        assert config["webhook"]["timeout"] == 5
        
        # 检查 Slack 上报配置
        assert "slack" in config
        assert config["slack"]["enabled"] is False
        assert config["slack"]["channel"] == "#digital-life-alerts"
        assert config["slack"]["username"] == "Digital Life Bot"
        
        # 检查 email 上报配置
        assert "email" in config
        assert config["email"]["enabled"] is False

    def test_get_config_with_env_vars(self, monkeypatch):
        """测试通过环境变量覆盖配置"""
        monkeypatch.setenv("ERROR_REPORTING_CONSOLE_LEVEL", "debug")
        monkeypatch.setenv("ERROR_REPORTING_FILE_ENABLED", "false")
        monkeypatch.setenv("ERROR_REPORTING_FILE_PATH", "/custom/logs/errors.log")
        monkeypatch.setenv("ERROR_REPORTING_WEBHOOK_ENABLED", "true")
        monkeypatch.setenv("ERROR_REPORTING_WEBHOOK_URL", "https://example.com/webhook")
        monkeypatch.setenv("ERROR_REPORTING_WEBHOOK_TIMEOUT", "10")
        monkeypatch.setenv("ERROR_REPORTING_SLACK_ENABLED", "true")
        monkeypatch.setenv("ERROR_REPORTING_SLACK_CHANNEL", "#custom-channel")
        monkeypatch.setenv("ERROR_REPORTING_SLACK_USERNAME", "Custom Bot")
        
        config = get_config()
        
        assert config["console"]["min_level"] == "debug"
        assert config["file"]["enabled"] is False
        assert config["file"]["file_path"] == "/custom/logs/errors.log"
        assert config["webhook"]["enabled"] is True
        assert config["webhook"]["url"] == "https://example.com/webhook"
        assert config["webhook"]["timeout"] == 10
        assert config["slack"]["enabled"] is True
        assert config["slack"]["channel"] == "#custom-channel"
        assert config["slack"]["username"] == "Custom Bot"

    def test_get_config_env_var_case_insensitive(self, monkeypatch):
        """测试环境变量值不区分大小写"""
        monkeypatch.setenv("ERROR_REPORTING_FILE_ENABLED", "TRUE")
        monkeypatch.setenv("ERROR_REPORTING_WEBHOOK_ENABLED", "FALSE")
        
        config = get_config()
        
        assert config["file"]["enabled"] is True
        assert config["webhook"]["enabled"] is False

    def test_get_config_webhook_headers(self):
        """测试 webhook 请求头配置"""
        config = get_config()
        
        assert "headers" in config["webhook"]
        assert config["webhook"]["headers"]["Content-Type"] == "application/json"

    def test_get_config_timeout_default(self):
        """测试默认超时时间"""
        config = get_config()
        
        assert config["webhook"]["timeout"] == 5

    def test_get_config_slack_icon_emoji(self):
        """测试 Slack emoji 配置"""
        config = get_config()
        
        assert config["slack"]["icon_emoji"] == ":robot_face:"